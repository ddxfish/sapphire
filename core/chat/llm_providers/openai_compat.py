# llm_providers/openai_compat.py
"""
OpenAI-compatible provider.

Handles:
- LM Studio (local)
- llama.cpp server (local)
- Fireworks.ai (cloud)
- OpenRouter (cloud)
- Any OpenAI-compatible API

This is the default provider and your 99% use case.
"""

import hashlib
import json
import logging
import threading
from typing import Dict, Any, List, Optional, Generator

from openai import OpenAI

from .base import BaseProvider, LLMResponse, ToolCall, retry_on_rate_limit, server_answered

logger = logging.getLogger(__name__)

# ── Shared HTTP transport pool ───────────────────────────────────────────────
# Provider objects are rebuilt every turn (cheap), but the TRANSPORT persists
# here so the TCP+TLS connection to each endpoint stays warm across turns.
# Without this, every voice/phone turn paid a fresh DNS+TCP+TLS handshake:
# each new OpenAI() got its own httpx pool, and httpx's default
# keepalive_expiry (5s) is shorter than any conversational gap anyway.
# API keys ride per-request headers, so providers sharing an endpoint can
# safely share a pool; per-request timeouts still come from each provider's
# own OpenAI(timeout=...) (explicit timeout wins over the client's). 2026-07-15.
_HTTP_POOL = {}
_HTTP_POOL_LOCK = threading.Lock()
_KEEPALIVE_SECONDS = 120.0


def _shared_http_client(base_url):
    import httpx
    key = (base_url or "").rstrip("/")
    with _HTTP_POOL_LOCK:
        cli = _HTTP_POOL.get(key)
        if cli is None or cli.is_closed:
            cli = httpx.Client(
                limits=httpx.Limits(max_connections=100,
                                    max_keepalive_connections=20,
                                    keepalive_expiry=_KEEPALIVE_SECONDS),
                timeout=httpx.Timeout(600.0, connect=10.0),
            )
            _HTTP_POOL[key] = cli
        return cli


# Params a strict endpoint may refuse; each is optional to the request. A
# 400 naming one is learned ONCE per provider instance (Krem's F1a,
# 2026-08-23): stripped, the request retried, never sent again until
# restart — one warning + toast names it and where to clear it.
# stream_options rides the same law (it used to re-pay the double request
# on EVERY streaming call to an endpoint that refused it).
OPTIONAL_PARAMS = ('presence_penalty', 'frequency_penalty', 'repeat_penalty',
                   'top_k', 'stream_options')
# Not in the OpenAI SDK's signature — passing them as kwargs is a TypeError,
# so they ride extra_body (LM Studio / llama.cpp honor them there).
EXTRA_BODY_PARAMS = ('repeat_penalty', 'top_k')
PENALTY_PARAMS = ('presence_penalty', 'frequency_penalty', 'repeat_penalty', 'top_k')


def _notify(message: str, severity: str = 'warning') -> None:
    """Surface a provider notice as a UI toast (best effort, never raises)."""
    try:
        from core.event_bus import publish, Events
        publish(Events.PLUGIN_NOTICE, {'plugin': 'llm', 'message': message,
                                       'severity': severity})
    except Exception:
        pass


class OpenAICompatProvider(BaseProvider):
    """
    Provider for OpenAI-compatible APIs.
    
    Works with any server implementing the OpenAI chat completions API:
    - POST /v1/chat/completions
    - GET /v1/models (for health check)
    """
    
    def __init__(self, llm_config: Dict[str, Any], request_timeout: float = 240.0):
        super().__init__(llm_config, request_timeout)
        
        self._client = OpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
            timeout=self.request_timeout,
            http_client=_shared_http_client(self.base_url),
        )

        # Fireworks prompt caching: stable session ID for replica affinity
        if 'fireworks.ai' in (self.base_url or '').lower():
            key_hash = hashlib.sha256((self.api_key or '').encode()).hexdigest()[:12]
            self._fireworks_session_id = f"sapphire-{key_hash}"
        else:
            self._fireworks_session_id = None

        # Optional params this endpoint has refused (learn-once, see OPTIONAL_PARAMS)
        self._rejected_params: set = set()

        logger.info(f"OpenAI-compat provider initialized: {self.base_url}")
    
    @property
    def provider_name(self) -> str:
        return self.config.get('provider', 'openai')
    
    @property
    def supports_images(self) -> bool:
        """Whether this provider instance supports vision/image inputs."""
        return self._supports_multimodal()
    
    def _supports_multimodal(self) -> bool:
        """
        Whether this provider instance supports multimodal (image) inputs.

        Priority:
        1. Explicit per-provider override `supports_images` in the config (wins).
        2. OpenAI official API → always.
        3. Self-hosted endpoints (localhost OR private LAN IPs OR .local) and
           known model marketplaces → detect by VLM model-name token.
        4. Unknown public endpoint → conservative (off).
        """
        import re as _re
        base_url = (self.base_url or '').lower()
        model = (self.model or '').lower()

        # 1. Explicit override — lets any vision-capable endpoint be declared
        #    regardless of host/name heuristics. Set supports_images on the provider.
        override = self.config.get('supports_images')
        logger.debug(f"[VISION-DEBUG] supports_images: base_url={base_url!r} model={model!r} config_flag={override!r}")
        if override is not None:
            logger.debug(f"[VISION-DEBUG]   -> config override decides: {bool(override)}")
            return bool(override)

        vision_indicators = ['llava', 'vision', 'vl', 'pixtral', 'bakllava', 'cogvlm',
                             'minicpm-v', 'internvl', 'gemma-3', 'qwen2-vl', 'qwen2.5-vl',
                             'qwen3-vl', 'gpt-4o', 'gpt-4-vision']

        # 2. OpenAI official
        if 'api.openai.com' in base_url:
            return True

        # 3. Self-hosted (user-run servers) + known marketplaces → detect by model name.
        #    Private-LAN IPs are self-hosted too — a Qwen-VL on the box at 192.168.x.x
        #    is just as vision-capable as one on localhost.
        is_self_hosted = (
            any(h in base_url for h in
                ['localhost', '127.0.0.1', '0.0.0.0', '.local', 'host.docker.internal'])
            or bool(_re.search(r'//(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)', base_url))
        )
        if is_self_hosted or 'fireworks.ai' in base_url or 'openrouter.ai' in base_url:
            supported = any(ind in model for ind in vision_indicators)
            logger.debug(f"[VISION-DEBUG]   -> heuristic (self_hosted={is_self_hosted}) name-match = {supported}")
            return supported

        # 4. Unknown public endpoint — conservative.
        logger.debug(f"[VISION-DEBUG]   -> unknown endpoint, defaulting OFF")
        return False
    
    def _is_deepseek_official(self) -> bool:
        """
        Check if this is DeepSeek's official reasoner endpoint.

        DeepSeek's official API for `deepseek-reasoner` requires that
        `reasoning_content` be round-tripped on every assistant message that
        contains `tool_calls`. Without it, request 2+ in a tool cycle returns
        400 "Missing reasoning_content field in the assistant message at
        message index N". Other DeepSeek deployments (Fireworks, OpenRouter,
        Featherless) don't enforce this — they accept the standard OpenAI
        message shape. The strip-on-non-tool-turn rule still applies: we
        ONLY add reasoning_content for assistant messages that have tool_calls.

        Gating on the model name (`reasoner`) keeps `deepseek-chat` requests
        clean — that model has no reasoning content and the field would be
        ignored or rejected.
        """
        base_url = (self.base_url or '').lower()
        model = (self.model or '').lower()
        return 'api.deepseek.com' in base_url and 'reasoner' in model

    def _is_fireworks_reasoning_model(self) -> bool:
        """
        Check if this is a Fireworks reasoning model that needs reasoning_effort param.

        These models output thinking in reasoning_content field when reasoning_effort is set.
        """
        base_url = (self.base_url or '').lower()
        model = (self.model or '').lower()
        
        if 'fireworks.ai' not in base_url:
            return False
        
        # Models known to support reasoning_effort parameter
        reasoning_indicators = [
            'deepseek',      # DeepSeek V3
            'glm',           # GLM 4.7
            'kimi',          # Kimi K2
            'thinking',      # Any model with "thinking" in name
            'qwq',           # QwQ
        ]
        
        return any(ind in model for ind in reasoning_indicators)
    
    @property
    def client(self) -> OpenAI:
        """Access the underlying OpenAI client if needed."""
        return self._client
    
    def health_check(self) -> bool:
        """Reachability probe via models.list(). ANY HTTP status response (4xx/5xx)
        counts as reachable — a broken /models (e.g. Fireworks 500 'Error listing
        deployed models', or xAI/Grok not exposing /models) does NOT mean completions
        are down, and the older code wrongly hard-failed chat on it. Only a genuine
        connection/DNS/timeout error marks the provider unhealthy."""
        try:
            self._client.models.list(timeout=self.health_check_timeout)
            return True
        except Exception as e:
            # Host answered with an HTTP status (incl. 500/401/429) → it's reachable.
            # Let the actual completion be the judge of whether it truly works.
            if server_answered(e):
                logger.debug(f"Health check: {self.base_url} /models errored ({e}) but server is reachable")
                return True

            logger.debug(f"Health check failed for {self.base_url}: {e}")

            # Genuinely unreachable (connection/timeout) — auto-correct a missing
            # /v1 suffix (common with llama.cpp, Ollama, etc.) and retry once.
            base = self.base_url.rstrip('/')
            if not base.endswith('/v1'):
                corrected = base + '/v1'
                try:
                    test_client = OpenAI(
                        base_url=corrected,
                        api_key=self.api_key,
                        timeout=self.request_timeout,
                        http_client=_shared_http_client(corrected),
                    )
                    test_client.models.list(timeout=self.health_check_timeout)
                    logger.info(f"Auto-corrected base_url: {self.base_url} -> {corrected}")
                    self.base_url = corrected
                    self._client = test_client
                    return True
                except Exception:
                    pass

            return False

    def list_models(self) -> Optional[list]:
        """Discover available models via /v1/models endpoint."""
        try:
            response = self._client.models.list(timeout=self.health_check_timeout)
            models = []
            for m in response.data:
                models.append({
                    'id': m.id,
                    'name': getattr(m, 'name', m.id) or m.id,
                })
            models.sort(key=lambda x: x['name'].lower())
            return models
        except Exception as e:
            logger.debug(f"Model discovery failed for {self.base_url}: {e}")
            return None

    def _transform_params_for_model(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Transform generation params for model compatibility.
        
        GPT-5+ and o1/o3 reasoning models:
        - Use max_completion_tokens instead of max_tokens
        - Don't support temperature, top_p, presence_penalty, frequency_penalty
        
        This handles conversions transparently so callers don't need to care.
        """
        if not params:
            return params

        # Blank = absent = never sent (None would go out as JSON null).
        result = {k: v for k, v in params.items() if v is not None and v != ''}

        # Strip internal Sapphire params that aren't part of the OpenAI API
        result.pop('disable_thinking', None)

        model_lower = (self.model or '').lower()

        # Detect reasoning models (GPT-5+, o1, o3)
        is_reasoning_model = (
            model_lower.startswith('gpt-5') or
            model_lower.startswith('o1') or
            model_lower.startswith('o3')
        )

        # Detect models that don't support penalty params (Grok, etc.)
        is_grok = (
            model_lower.startswith('grok-') or
            self.config.get('strip_penalties', False)
        )

        if is_reasoning_model:
            # max_tokens → max_completion_tokens
            if 'max_tokens' in result:
                result['max_completion_tokens'] = result.pop('max_tokens')

            # Remove unsupported sampling params (reasoning models don't use these)
            removed = []
            for unsupported in ['temperature', 'top_p', *PENALTY_PARAMS]:
                if unsupported in result:
                    result.pop(unsupported)
                    removed.append(unsupported)

            if removed:
                logger.debug(f"Filtered unsupported params for {self.model}: {removed}")

        elif is_grok:
            removed = []
            for unsupported in [*PENALTY_PARAMS, 'stop']:
                if unsupported in result:
                    result.pop(unsupported)
                    removed.append(unsupported)

            if removed:
                logger.debug(f"Filtered unsupported params for {self.model}: {removed}")

        # Non-schema knobs ride extra_body — as kwargs they'd TypeError in the SDK.
        for k in EXTRA_BODY_PARAMS:
            if k in result:
                result.setdefault('extra_body', {})[k] = result.pop(k)

        return result

    # ── Learn-once param stripping ─────────────────────────────────────────

    def _strip_rejected(self, kwargs: Dict[str, Any]) -> None:
        for p in self._rejected_params:
            kwargs.pop(p, None)
            eb = kwargs.get('extra_body')
            if isinstance(eb, dict):
                eb.pop(p, None)
                if not eb:
                    kwargs.pop('extra_body', None)

    def _params_named(self, err: Exception, kwargs: Dict[str, Any]) -> List[str]:
        """Which OPTIONAL params of THIS request the error names."""
        text = f"{err} {getattr(err, 'body', '') or ''}".lower()
        eb = kwargs.get('extra_body') or {}
        sent = [p for p in OPTIONAL_PARAMS if p in kwargs or p in eb]
        named = [p for p in sent if p in text]
        if not named and 'stream_options' in sent and any(
                h in text for h in ('unrecognized', 'unknown parameter', 'unknown field')):
            # endpoints that refuse stream_options without naming it (llama.cpp lineage)
            named = ['stream_options']
        return named

    def _create(self, request_kwargs: Dict[str, Any]):
        """chat.completions.create with learn-once stripping of refused
        optional params: strip what this endpoint already refused, try,
        and on a refusal naming an optional param remember it, warn once
        (log + toast), retry once without it."""
        self._strip_rejected(request_kwargs)
        try:
            return retry_on_rate_limit(self._client.chat.completions.create, **request_kwargs)
        except Exception as e:
            bad = self._params_named(e, request_kwargs)
            if not bad:
                raise
            self._rejected_params.update(bad)
            self._strip_rejected(request_kwargs)
            where = self.config.get('display_name') or self.provider_name
            msg = (f"{where} ({self.model}) rejected {', '.join(bad)} — sent without it from "
                   f"now on (until restart). Clear it under Settings › LLM › {where} to stop this.")
            logger.warning(f"[OPENAI-COMPAT] {msg} | {e}")
            _notify(msg)
            return retry_on_rate_limit(self._client.chat.completions.create, **request_kwargs)
    
    def _sanitize_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Sanitize messages for OpenAI-compatible APIs.
        
        Handles cross-provider compatibility:
        - Strips Claude-specific fields (thinking_raw, thinking, metadata)
        - Converts content lists to strings (Claude uses content blocks)
        - Normalizes tool results from Claude format to OpenAI format
        - Ensures proper message structure for tool calls
        """
        clean = []
        
        for msg in messages:
            role = msg.get('role', '')
            content = msg.get('content')
            
            # Handle Claude-format tool results: {"role": "user", "content": [{"type": "tool_result", ...}]}
            # Convert to OpenAI format: {"role": "tool", "tool_call_id": ..., "content": ...}
            if role == 'user' and isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get('type') == 'tool_result':
                        tool_use_id = block.get('tool_use_id', '')
                        # Convert Claude tool ID format if needed
                        if tool_use_id.startswith('toolu_'):
                            tool_use_id = 'call_' + tool_use_id[6:]
                        clean.append({
                            'role': 'tool',
                            'tool_call_id': tool_use_id,
                            'name': block.get('name', 'unknown'),
                            'content': block.get('content', '')
                        })
                # If we processed tool_result blocks, skip the original message
                if any(isinstance(b, dict) and b.get('type') == 'tool_result' for b in content):
                    continue
            
            # Normalize content - handle multimodal content lists
            if isinstance(content, list):
                # Check if content has images
                has_images = any(
                    isinstance(b, dict) and b.get('type') == 'image' 
                    for b in content
                )
                
                if has_images:
                    # Convert to OpenAI multimodal format — always send images,
                    # let the provider reject if model doesn't support vision
                    openai_content = []
                    for block in content:
                        if isinstance(block, dict):
                            if block.get('type') == 'text':
                                openai_content.append({"type": "text", "text": block.get('text', '')})
                            elif block.get('type') == 'image':
                                # Internal: {"type": "image", "data": "...", "media_type": "..."}
                                # OpenAI:   {"type": "image_url", "image_url": {"url": "data:...;base64,..."}}
                                media_type = block.get('media_type', 'image/jpeg')
                                data = block.get('data', '')
                                openai_content.append({
                                    "type": "image_url",
                                    "image_url": {"url": f"data:{media_type};base64,{data}"}
                                })
                            elif block.get('type') in ('thinking', 'tool_use'):
                                # Skip thinking and tool_use blocks
                                continue
                        elif isinstance(block, str):
                            openai_content.append({"type": "text", "text": block})
                    content = openai_content if openai_content else ""
                else:
                    # No images - flatten to string
                    text_parts = []
                    for block in content:
                        if isinstance(block, dict):
                            if block.get('type') == 'text':
                                text_parts.append(block.get('text', ''))
                            elif block.get('type') == 'thinking':
                                # Skip thinking blocks - they shouldn't be sent to other providers
                                continue
                            elif block.get('type') == 'tool_use':
                                # Tool use blocks are handled via tool_calls field
                                continue
                            elif block.get('type') == 'image':
                                # Provider doesn't support images - add placeholder
                                text_parts.append('[image]')
                        elif isinstance(block, str):
                            text_parts.append(block)
                    content = ' '.join(text_parts).strip()
            
            # Build clean message with only allowed fields
            clean_msg = {'role': role}
            
            # Handle content - preserve list for multimodal, stringify otherwise
            if content is not None:
                if isinstance(content, list):
                    clean_msg['content'] = content  # Keep list for multimodal
                else:
                    clean_msg['content'] = str(content) if content else ''
            elif 'tool_calls' in msg:
                # OpenAI requires content field, use empty string if tool_calls present
                clean_msg['content'] = ''
            else:
                clean_msg['content'] = ''
            
            # Handle tool_calls (assistant messages)
            if msg.get('tool_calls'):
                # Normalize tool_calls format for OpenAI-compat APIs
                normalized_calls = []
                for tc in msg['tool_calls']:
                    if isinstance(tc, dict):
                        func = tc.get('function', {})

                        # Get arguments - MUST be a valid JSON-encoded string per OpenAI spec.
                        # Strict providers (Ollama Cloud, etc.) reject dicts or malformed strings.
                        args = func.get('arguments', '{}')
                        if args is None or args == '':
                            args = '{}'
                        elif isinstance(args, dict):
                            args = json.dumps(args)
                        elif isinstance(args, str):
                            try:
                                json.loads(args)
                            except (json.JSONDecodeError, ValueError):
                                logger.warning(f"[OPENAI-COMPAT] Tool call arguments not valid JSON, defaulting to empty ({len(args)} chars)")
                                args = '{}'
                        else:
                            args = '{}'

                        # Get tool ID - convert Claude format if needed
                        tool_id = tc.get('id', '')
                        if tool_id.startswith('toolu_'):
                            # Convert Claude ID to OpenAI-compatible format
                            tool_id = 'call_' + tool_id[6:]
                        
                        normalized_tc = {
                            'id': tool_id,
                            'type': 'function',
                            'function': {
                                'name': func.get('name', ''),
                                'arguments': args
                            }
                        }
                        normalized_calls.append(normalized_tc)
                if normalized_calls:
                    clean_msg['tool_calls'] = normalized_calls
            
            # Handle tool results (tool messages)
            if role == 'tool':
                tool_call_id = msg.get('tool_call_id', '')
                # Convert Claude tool ID format if needed
                if tool_call_id.startswith('toolu_'):
                    tool_call_id = 'call_' + tool_call_id[6:]
                clean_msg['tool_call_id'] = tool_call_id
                clean_msg['name'] = msg.get('name', 'unknown')
            
            # Include name field for function calls if present
            if msg.get('name') and role != 'tool':
                clean_msg['name'] = msg['name']

            # DeepSeek-reasoner official API requires reasoning_content
            # round-trip on tool-calling assistant turns. Without this,
            # the next request after a tool result hits 400 "Missing
            # reasoning_content field in the assistant message at message
            # index N". Only fires for the official endpoint + reasoner
            # model — other providers ignore the field. 2026-05-11.
            if (self._is_deepseek_official()
                    and msg.get('tool_calls')
                    and msg.get('thinking')):
                clean_msg['reasoning_content'] = msg['thinking']

            clean.append(clean_msg)

        # (The old Qwen `/no_think` token trick was removed 2026-07-02 — it never
        #  reliably worked. Thinking-disable now goes through _inject_thinking_control
        #  via extra_body, which is the mechanism Qwen/vLLM actually honor.)
        return clean
    
    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        generation_params: Optional[Dict[str, Any]] = None
    ) -> LLMResponse:
        """Send non-streaming chat completion request."""

        params = self._transform_params_for_model(generation_params or {})

        # Some OpenAI-compat providers (Zhipu GLM, others) enforce:
        #   "Requests with max_tokens > 4096 must have stream=true"
        # at the endpoint. The non-streaming path here would silently 400 for
        # any caller (voice path, continuity tasks, agents) whose gen_params
        # exceed that threshold. Rather than cap max_tokens or force callers
        # to track provider quirks, when we'd cross the gate we consume the
        # streaming endpoint internally and return the same LLMResponse the
        # non-streaming path would have produced. Caller sees no behavior
        # change. Bug surfaced on voice + glm51 (max_tokens=8192) 2026-04-20.
        mt = params.get('max_tokens') or 0
        if mt and mt > 4096:
            logger.info(
                f"[OPENAI-COMPAT] max_tokens={mt} > 4096 — using streaming "
                f"internally to satisfy provider stream-required contract; "
                f"caller still receives a single LLMResponse."
            )
            final = None
            for event in self.chat_completion_stream(messages, tools=tools,
                                                     generation_params=generation_params):
                if event.get("type") == "done":
                    final = event.get("response")
            if final is None:
                raise ValueError(
                    "Internal stream-accumulate produced no 'done' event — "
                    "provider may have closed the stream without finishing."
                )
            return final

        # Sanitize messages - only keep fields the OpenAI API understands
        clean_messages = self._sanitize_messages(messages)
        
        logger.debug(f"[OPENAI-COMPAT] Non-streaming: {len(clean_messages)} messages to {self.model}")
        
        request_kwargs = {
            "model": self.model,
            "messages": clean_messages,
            **params
        }

        # Fireworks: session affinity for prompt caching
        if self._fireworks_session_id:
            request_kwargs["user"] = self._fireworks_session_id

        # Add reasoning_effort for Fireworks reasoning models
        if self._is_fireworks_reasoning_model():
            request_kwargs["reasoning_effort"] = params.get("reasoning_effort", "medium")

        if tools:
            request_kwargs["tools"] = self.convert_tools_for_api(tools)
            # Note: tool_choice omitted intentionally. Per OpenAI spec, "auto" is the
            # default when tools are present, so setting it is redundant. Omitting it
            # reduces the rejection surface with strict OpenAI-compat providers.
            # (Matches the Anthropic provider, which also never sets tool_choice.)

        # Thinking control (universal, family-gated) + raw extra_body passthrough.
        self._inject_thinking_control(
            request_kwargs, want_disable=(generation_params or {}).get('disable_thinking', False))

        response = self._create(request_kwargs)

        return self._parse_response(response)

    def _inject_thinking_control(self, kwargs: dict, want_disable: bool = False) -> None:
        """Merge thinking-control + a raw extra_body passthrough into the request.

        Universal, best-effort, per-provider "disable thinking" (replaces the old
        Qwen-only /no_think trick). Family-GATED: a model's own off-switch is sent
        ONLY to that family, so a strict endpoint never sees a param it 400s on.
        Unknown models are a logged no-op — the user's raw `extra_body` config is the
        escape hatch for those. OFF path (no flag, no extra_body config) leaves kwargs
        untouched → byte-identical to a no-op.

        "Disable" comes from any of: the per-provider `disable_thinking` config (the
        Settings checkbox), the per-request `disable_thinking` gen param (continue/
        prefill), or the legacy `disable_thinking_qwen` flag (migrated transparently).
        """
        cfg = self.config
        disable = (want_disable or cfg.get('disable_thinking', False)
                   or cfg.get('disable_thinking_qwen', False))
        raw = cfg.get('extra_body')
        if not (disable or raw):
            return                                    # OFF path — leave kwargs untouched

        extra = dict(kwargs.get('extra_body') or {})

        if disable:
            model = (self.model or '').lower()
            base = (self.base_url or '').lower()
            if self._is_fireworks_reasoning_model():
                # Fireworks disables reasoning via the top-level `reasoning_effort`
                # param (none/false), NOT extra_body — its schema is strict and 400s
                # on unknown fields like `thinking` / `chat_template_kwargs`. This
                # overrides the "medium" default set in build_request_kwargs.
                kwargs['reasoning_effort'] = 'none'
                fam = 'fireworks'
            elif model.startswith('glm') or any(h in base for h in ('z.ai', 'bigmodel', 'zhipu')):
                extra['thinking'] = {'type': 'disabled'}          # GLM / Z.AI convention
                fam = 'glm'
            elif 'qwen' in model:
                ctk = dict(extra.get('chat_template_kwargs') or {})
                ctk['enable_thinking'] = False                    # Qwen 3 (vLLM / LM Studio)
                extra['chat_template_kwargs'] = ctk
                fam = 'qwen'
            else:
                fam = None                                        # unknown → don't guess (avoid 400)
            if fam:
                logger.info(f"[OPENAI-COMPAT] disable_thinking ({fam}) → {self.model}")
            else:
                logger.info(f"[OPENAI-COMPAT] disable_thinking requested but no known switch "
                            f"for {self.model}; set extra_body manually if it's a reasoning model")

        if raw:
            try:
                rb = raw if isinstance(raw, dict) else json.loads(raw)
                if isinstance(rb, dict):
                    extra.update(rb)                              # user's raw extra_body wins
                else:
                    logger.warning("[OPENAI-COMPAT] extra_body config is not a JSON object; ignoring")
            except (ValueError, TypeError) as e:
                logger.warning(f"[OPENAI-COMPAT] extra_body config not valid JSON, ignoring: {e}")

        if extra:
            kwargs['extra_body'] = extra

    def chat_completion_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        generation_params: Optional[Dict[str, Any]] = None
    ) -> Generator[Dict[str, Any], None, None]:
        """Send streaming chat completion request."""
        
        params = self._transform_params_for_model(generation_params or {})
        
        # Sanitize messages - only keep fields the OpenAI API understands
        clean_messages = self._sanitize_messages(messages)
        
        # DEBUG: Log message structure (reduced verbosity)
        logger.info(f"[OPENAI-COMPAT] Sending {len(clean_messages)} messages to {self.base_url} model={self.model}")
        logger.debug(f"[OPENAI-COMPAT] Multimodal supported: {self._supports_multimodal()}")
        for i, msg in enumerate(clean_messages):
            role = msg.get('role')
            content = msg.get('content')
            content_type = type(content).__name__
            has_tc = 'tool_calls' in msg
            
            if isinstance(content, str):
                preview = content[:60] + '...' if len(content) > 60 else content
            elif isinstance(content, list):
                preview = f"[list with {len(content)} items]"
            else:
                preview = str(content)[:60]
            
            logger.debug(f"[OPENAI-COMPAT]   [{i}] role={role}, content_type={content_type}, has_tool_calls={has_tc}, preview={preview}")
        
        request_kwargs = {
            "model": self.model,
            "messages": clean_messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            **params
        }

        # Fireworks: session affinity for prompt caching
        if self._fireworks_session_id:
            request_kwargs["user"] = self._fireworks_session_id

        # Add reasoning_effort for Fireworks reasoning models to enable thinking output
        if self._is_fireworks_reasoning_model():
            request_kwargs["reasoning_effort"] = params.get("reasoning_effort", "medium")
            logger.info(f"[REASONING] Enabled reasoning_effort={request_kwargs['reasoning_effort']} for {self.model}")

        if tools:
            request_kwargs["tools"] = self.convert_tools_for_api(tools)
            # Note: tool_choice omitted intentionally. Per OpenAI spec, "auto" is the
            # default when tools are present, so setting it is redundant. Omitting it
            # reduces the rejection surface with strict OpenAI-compat providers.
            # (Matches the Anthropic provider, which also never sets tool_choice.)

        # Thinking control (universal, family-gated) + raw extra_body — streaming path too.
        self._inject_thinking_control(
            request_kwargs, want_disable=(generation_params or {}).get('disable_thinking', False))

        logger.info(f"[OPENAI-COMPAT] Request params: model={request_kwargs.get('model')}, tools={len(request_kwargs.get('tools', []))}")

        # Refused optional params (stream_options on llama.cpp-lineage servers,
        # a penalty a strict cloud endpoint won't take) are learned once and
        # retried without — see _create. Anything else fails loud.
        try:
            stream = self._create(request_kwargs)
        except Exception as e:
            logger.error(f"[OPENAI-COMPAT] REQUEST FAILED: {e}")
            logger.error(f"[OPENAI-COMPAT] Message count: {len(clean_messages)}, has tool_calls: {any('tool_calls' in m for m in clean_messages)}")
            raise
        
        # Track accumulated state for final response
        full_content = ""
        full_thinking = ""
        tool_calls_acc = []  # List of dicts being built
        finish_reason = None
        usage = None

        for chunk in stream:
            # Final chunk with usage data (stream_options=include_usage)
            if hasattr(chunk, 'usage') and chunk.usage:
                usage = {
                    "prompt_tokens": chunk.usage.prompt_tokens or 0,
                    "completion_tokens": chunk.usage.completion_tokens or 0,
                    "total_tokens": chunk.usage.total_tokens or 0
                }
                # Some providers include cache stats. OpenAI/Fireworks report
                # prompt_tokens INCLUSIVE of cached; normalize to the Anthropic
                # convention (prompt_tokens = NON-cached input, cached counted
                # separately) so the shared cache% formula — cache_read /
                # (prompt + cache_read) — isn't double-counted (which otherwise
                # caps the displayed hit rate at ~50%). 2026-05-30.
                cache_read = getattr(chunk.usage, 'prompt_tokens_details', None)
                if cache_read and hasattr(cache_read, 'cached_tokens'):
                    cached = cache_read.cached_tokens or 0
                    if cached > 0:
                        usage["cache_read_tokens"] = cached
                        usage["prompt_tokens"] = max(0, usage["prompt_tokens"] - cached)
                        logger.info(f"[CACHE] Stream usage: {cached} cached of {usage['prompt_tokens'] + cached} prompt tokens")

            if not chunk.choices:
                continue
            
            choice = chunk.choices[0]
            delta = choice.delta
            
            if choice.finish_reason:
                finish_reason = choice.finish_reason
            
            # Reasoning content (Fireworks GLM, DeepSeek, etc.)
            # These models return thinking in reasoning_content field instead of <think> tags
            reasoning = getattr(delta, 'reasoning_content', None)
            if reasoning:
                full_thinking += reasoning
                yield {"type": "thinking", "text": reasoning}
            
            # Debug: log all delta attributes on first content chunk to diagnose missing reasoning
            if delta.content and not full_content:
                delta_attrs = [a for a in dir(delta) if not a.startswith('_')]
                logger.debug(f"[REASONING] Delta attributes: {delta_attrs}")
                # Check for alternative reasoning field names
                for attr in ['reasoning', 'reasoning_content', 'thought', 'thinking']:
                    val = getattr(delta, attr, None)
                    if val:
                        logger.info(f"[REASONING] Found {attr} ({len(val)} chars)")
            
            # Content chunk
            if delta.content:
                full_content += delta.content
                yield {"type": "content", "text": delta.content}
            
            # Tool call chunks
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index if tc_delta.index is not None else 0

                    # Expand list if needed
                    while len(tool_calls_acc) <= idx:
                        tool_calls_acc.append({
                            "id": "",
                            "name": "",
                            "arguments": ""
                        })
                    
                    if tc_delta.id:
                        tool_calls_acc[idx]["id"] = tc_delta.id
                    if tc_delta.function and tc_delta.function.name:
                        tool_calls_acc[idx]["name"] = tc_delta.function.name
                    if tc_delta.function and tc_delta.function.arguments:
                        tool_calls_acc[idx]["arguments"] += tc_delta.function.arguments
                    
                    yield {
                        "type": "tool_call",
                        "index": idx,
                        "id": tool_calls_acc[idx]["id"],
                        "name": tool_calls_acc[idx]["name"],
                        "arguments": tool_calls_acc[idx]["arguments"]
                    }
        
        # Build final response
        final_tool_calls = [
            ToolCall(id=tc["id"], name=tc["name"], arguments=tc["arguments"])
            for tc in tool_calls_acc
            if tc["id"] and tc["name"]
        ]
        
        # If no visible content but we have thinking, use thinking as content
        # This handles providers that put the full response in reasoning_content
        # (e.g., DashScope Qwen3 thinking mode)
        if not full_content and full_thinking:
            logger.info(f"[REASONING] No content but have thinking ({len(full_thinking)} chars) — using as content")
            full_content = full_thinking

        final_response = LLMResponse(
            content=full_content if full_content else None,
            tool_calls=final_tool_calls,
            finish_reason=finish_reason,
            usage=usage
        )

        done_event = {"type": "done", "response": final_response}
        if full_thinking:
            done_event["thinking"] = full_thinking
        yield done_event
    
    def _parse_response(self, response) -> LLMResponse:
        """Parse OpenAI response into normalized LLMResponse."""
        
        if not response.choices:
            raise ValueError("LLM returned empty response (no choices)")
        choice = response.choices[0]
        message = choice.message
        
        # Check for reasoning_content (Fireworks reasoning models)
        # Prepend as <think> tags to match streaming behavior
        reasoning = getattr(message, 'reasoning_content', None)
        if reasoning:
            logger.info(f"[REASONING] Non-stream response has reasoning_content ({len(reasoning)} chars)")
            content = message.content or ""
            if content:
                message_content = f"<think>{reasoning}</think>\n\n{content}"
            else:
                # No visible content — use reasoning as the response
                # (some providers put everything in reasoning_content)
                message_content = reasoning
        else:
            message_content = message.content

        # Parse tool calls if present
        tool_calls = []
        if message.tool_calls:
            for tc in message.tool_calls:
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=tc.function.arguments
                ))
        
        # Parse usage if present
        usage = None
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens
            }
            # Normalize cached tokens to the Anthropic convention (see streaming
            # path above) — prompt_tokens = non-cached, cache_read separate.
            cache_read = getattr(response.usage, 'prompt_tokens_details', None)
            if cache_read and hasattr(cache_read, 'cached_tokens'):
                cached = cache_read.cached_tokens or 0
                if cached > 0:
                    usage["cache_read_tokens"] = cached
                    usage["prompt_tokens"] = max(0, usage["prompt_tokens"] - cached)

        return LLMResponse(
            content=message_content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason,
            usage=usage,
            thinking=reasoning,  # raw reasoning_content for DeepSeek round-trip
        )