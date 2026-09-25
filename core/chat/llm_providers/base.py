# llm_providers/base.py
"""
Base provider interface for LLM abstraction.

All providers must implement these methods to ensure consistent behavior
across OpenAI-compatible APIs, Claude, and others.
"""

import hashlib
import json
import logging
import time
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Any, List, Optional, Generator, Callable, TypeVar

logger = logging.getLogger(__name__)

# Retry configuration
RETRY_MAX_ATTEMPTS = 3
RETRY_BASE_DELAY = 1.0  # seconds
RETRY_MAX_DELAY = 10.0  # seconds
RETRY_STATUS_CODES = {429, 529}  # Rate limit codes (429 standard, 529 Anthropic overload)

T = TypeVar('T')


def server_answered(exc: Exception) -> bool:
    """True if an API-client exception means the HOST RESPONDED with an HTTP status
    (4xx/5xx) — i.e. the server is REACHABLE even if the probed endpoint (e.g.
    /models) is broken. False for connection/DNS/timeout errors (genuinely
    unreachable). Health checks use this so a flaky side-endpoint (e.g. Fireworks'
    /models returning 500) doesn't falsely mark a working provider dead — the
    actual completion is the real source of truth. 2026-06-21."""
    try:
        import openai
        return isinstance(exc, openai.APIStatusError)
    except Exception:
        return False


# HTTP statuses that mean "this key/account is refused". A provider answering
# one of these on its probe is DEAD for fallback purposes: a refused key never
# fixes itself mid-turn, and the old any-status-is-alive verdict selected such
# a provider every turn in Auto mode while everything behind it in the order
# never ran (scout F2, 2026-09-20).
AUTH_DEAD_STATUSES = {401, 402, 403}


def http_status(exc: Exception):
    """The HTTP status an API-client exception carries, or None."""
    code = getattr(exc, 'status_code', None)
    return code if isinstance(code, int) else None


def retry_on_rate_limit(func: Callable[..., T], *args, **kwargs) -> T:
    """
    Execute a function with exponential backoff retry on rate limit errors.
    
    Handles:
    - HTTP 429 Too Many Requests
    - HTTP 529 Overloaded (Anthropic-specific)
    
    Args:
        func: The function to execute
        *args, **kwargs: Arguments to pass to the function
    
    Returns:
        The function's return value
    
    Raises:
        The original exception after max retries exhausted
    """
    last_exception = None
    
    for attempt in range(RETRY_MAX_ATTEMPTS):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            # Check if this is a rate limit error
            status_code = _extract_status_code(e)
            
            if status_code not in RETRY_STATUS_CODES:
                # Not a rate limit error, re-raise immediately
                raise
            
            last_exception = e
            
            if attempt == RETRY_MAX_ATTEMPTS - 1:
                # Last attempt, give up
                logger.warning(f"[RETRY] Rate limit: max retries ({RETRY_MAX_ATTEMPTS}) exhausted")
                raise
            
            # Calculate delay with exponential backoff + jitter
            delay = min(
                RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 1),
                RETRY_MAX_DELAY
            )
            
            logger.info(f"[RETRY] Rate limited (HTTP {status_code}), attempt {attempt + 1}/{RETRY_MAX_ATTEMPTS}, "
                       f"waiting {delay:.1f}s before retry")
            time.sleep(delay)
    
    # Should not reach here, but just in case
    if last_exception:
        raise last_exception


def _extract_status_code(exception: Exception) -> Optional[int]:
    """Extract HTTP status code from various exception types."""
    # OpenAI/Anthropic SDK exceptions
    if hasattr(exception, 'status_code'):
        return exception.status_code
    
    # httpx/requests style
    if hasattr(exception, 'response') and hasattr(exception.response, 'status_code'):
        return exception.response.status_code
    
    # Check exception message for status codes
    error_str = str(exception).lower()
    if '429' in error_str or 'rate limit' in error_str:
        return 429
    if '529' in error_str or 'overloaded' in error_str:
        return 529
    
    return None


# ── Request identity: placeholders + session affinity (2026-09-25) ───────────
# A provider's `extra_headers` (and `extra_body`) config may carry two
# placeholders, filled per request:
#   {session}  a stable id for THIS conversation — gateways key replica routing
#              and prompt-cache affinity on it (OpenCode Go rejects a request
#              without one; Fireworks reads it from the `user` body field).
#   {version}  Sapphire's version, for a self-identifying User-Agent.
# The conversation name is stamped on the provider by the ONE resolver
# (resolve.py) and hashed with the API key as salt: the wire never sees a
# chat name, and two installs never collide on 'default'. No name stamped
# (Test button, one-shot lanes) → a stable per-install id, so a gateway that
# REQUIRES the header still gets one.

def _read_version() -> str:
    try:
        return (Path(__file__).parent.parent.parent.parent / 'VERSION').read_text(encoding='utf-8').strip() or '?'
    except Exception:
        return '?'


SAPPHIRE_VERSION = _read_version()


def fill_placeholders(obj, session: str, version: str = SAPPHIRE_VERSION):
    """Substitute {session} / {version} inside string values, recursively.
    Non-strings and unknown {tokens} pass through untouched."""
    if isinstance(obj, str):
        return obj.replace('{session}', session).replace('{version}', version)
    if isinstance(obj, dict):
        return {k: fill_placeholders(v, session, version) for k, v in obj.items()}
    if isinstance(obj, list):
        return [fill_placeholders(v, session, version) for v in obj]
    return obj


@dataclass
class ToolCall:
    """Normalized tool call representation."""
    id: str
    name: str
    arguments: str  # JSON string
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to OpenAI-style dict format (used internally)."""
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": self.arguments
            }
        }


@dataclass
class LLMResponse:
    """
    Normalized LLM response that works across all providers.
    
    This is what chat.py sees - regardless of whether the underlying
    provider is OpenAI, Claude, or something else.
    """
    content: Optional[str] = None
    tool_calls: List[ToolCall] = field(default_factory=list)
    finish_reason: Optional[str] = None
    usage: Optional[Dict[str, int]] = None  # {prompt_tokens, completion_tokens, total_tokens}
    # Raw reasoning content (DeepSeek-reasoner, Fireworks reasoning models, etc.)
    # exposed separately so the assistant-with-tool_calls message dict can carry
    # `thinking` for the DeepSeek-official round-trip sanitizer to find. Without
    # this, the in-memory messages list omits `thinking`, the sanitizer never
    # emits `reasoning_content`, and the next API call after tool execution
    # fails with 400 "Missing reasoning_content field". 2026-05-14.
    thinking: Optional[str] = None
    # True when `content` is the reasoning stream substituted for an empty
    # visible answer (openai_compat's DashScope-style fallback). The web chat
    # renders it; outbound lanes (ExecutionContext) treat it as no answer.
    content_is_reasoning: bool = False
    
    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0
    
    def get_tool_calls_as_dicts(self) -> List[Dict[str, Any]]:
        """Get tool calls in OpenAI dict format for history/messages."""
        return [tc.to_dict() for tc in self.tool_calls]


class BaseProvider(ABC):
    """
    Abstract base class for LLM providers.
    
    Implementations handle the specifics of each API while exposing
    a consistent interface to the rest of the application.
    """
    
    def __init__(self, llm_config: Dict[str, Any], request_timeout: float = 240.0):
        """
        Initialize provider with config.
        
        Args:
            llm_config: Dict containing base_url, api_key, model, timeout, enabled
            request_timeout: Overall request timeout
        """
        self.config = llm_config
        self.base_url = llm_config.get('base_url', '')
        self.api_key = llm_config.get('api_key', '')
        self.model = llm_config.get('model', '')
        self.health_check_timeout = llm_config.get('timeout', 3.0)
        self.request_timeout = request_timeout
        self._client = None
        # Which conversation this (per-turn) instance serves — stamped by the
        # resolver; feeds {session}. None = no conversation (fallback id).
        self.conversation = None
    
    @property
    def provider_name(self) -> str:
        """Return provider identifier string."""
        return self.config.get('provider', 'unknown')
    
    @property
    def supports_images(self) -> bool:
        """Whether this provider supports image inputs.

        The per-provider config override `supports_images` (the vision 👁
        checkbox on custom providers) wins when set — True/False forces it;
        None/unset falls through to the subclass default (False here).
        Subclasses for known-vision APIs (claude, gemini, responses) override
        with unconditional True; openai_compat layers name/host heuristics.
        """
        override = self.config.get('supports_images')
        if override is not None:
            return bool(override)
        return False

    # ── Request identity (see fill_placeholders) ─────────────────────────────

    def session_id(self) -> str:
        """Stable per-conversation id for session-affinity headers/fields:
        sha256(api_key | conversation) — never the raw chat name."""
        raw = f"{self.api_key or ''}|{self.conversation or ''}"
        return 'sapphire-' + hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]

    def fill(self, obj):
        """Resolve {session} / {version} in a config value (dict/list/str)."""
        return fill_placeholders(obj, self.session_id(), SAPPHIRE_VERSION)

    def request_headers(self) -> Dict[str, str]:
        """Per-request headers from the provider's `extra_headers` config
        (JSON string from the UI or a dict from a preset), placeholders
        filled. {} when unset or unparseable (logged)."""
        raw = self.config.get('extra_headers')
        if not raw:
            return {}
        try:
            hdrs = raw if isinstance(raw, dict) else json.loads(raw)
        except (ValueError, TypeError) as e:
            logger.warning(f"[{self.provider_name}] extra_headers config not valid JSON, ignoring: {e}")
            return {}
        if not isinstance(hdrs, dict):
            logger.warning(f"[{self.provider_name}] extra_headers config is not a JSON object; ignoring")
            return {}
        return {str(k): str(v) for k, v in self.fill(hdrs).items()}

    def _hdr_kwargs(self) -> Dict[str, Any]:
        """`{'extra_headers': ...}` to splat into an SDK call, or {} so a
        provider with no headers configured sends byte-identical requests."""
        hdrs = self.request_headers()
        return {'extra_headers': hdrs} if hdrs else {}
    
    @abstractmethod
    def health_check(self) -> bool:
        """
        Check if the provider endpoint is reachable.

        Returns:
            True if healthy, False otherwise
        """
        pass

    def test_connection(self) -> dict:
        """
        Test provider connectivity with detailed results.
        Override for provider-specific validation (e.g., actual API call).

        Returns:
            dict with 'ok' (bool) and optionally 'response' (str) or 'error' (str)
        """
        try:
            if self.health_check():
                return {"ok": True}
            return {"ok": False, "error": "Health check failed"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @abstractmethod
    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        generation_params: Optional[Dict[str, Any]] = None
    ) -> LLMResponse:
        """
        Send a chat completion request (non-streaming).
        
        Args:
            messages: List of message dicts with role/content
            tools: Optional list of tool definitions (OpenAI format)
            generation_params: Optional dict with max_tokens, temperature, etc.
        
        Returns:
            LLMResponse with content and/or tool_calls
        """
        pass
    
    @abstractmethod
    def chat_completion_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        generation_params: Optional[Dict[str, Any]] = None
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Send a streaming chat completion request.
        
        Args:
            messages: List of message dicts with role/content
            tools: Optional list of tool definitions (OpenAI format)
            generation_params: Optional dict with max_tokens, temperature, etc.
        
        Yields:
            Dicts with either:
                {"type": "content", "text": "..."} for text chunks
                {"type": "tool_call", "index": N, "id": "...", "name": "...", "arguments": "..."} for tool calls
                {"type": "done", "response": LLMResponse} for final response
        """
        pass
    
    def format_tool_result(
        self,
        tool_call_id: str,
        function_name: str,
        result: str
    ) -> Dict[str, Any]:
        """
        Format a tool result message for this provider.
        
        Default implementation returns OpenAI format.
        Claude provider overrides this.
        
        Args:
            tool_call_id: The tool call ID to respond to
            function_name: Name of the function that was called
            result: The result string
        
        Returns:
            Message dict to append to conversation
        """
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": function_name,
            "content": result
        }
    
    def convert_messages_for_api(
        self,
        messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Convert messages to provider-specific format if needed.
        
        Default implementation passes through unchanged (OpenAI format).
        Claude provider overrides this.
        
        Args:
            messages: Messages in OpenAI format
        
        Returns:
            Messages in provider-specific format
        """
        return messages
    
    def convert_tools_for_api(
        self,
        tools: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Convert tool definitions to provider-specific format if needed.
        
        Default implementation strips internal fields (like 'network') that
        aren't part of the API spec. Claude provider overrides this.
        
        Args:
            tools: Tool definitions in OpenAI format
        
        Returns:
            Tools in provider-specific format
        """
        # Strip internal fields that APIs don't accept
        internal_fields = {'network', 'is_local', 'loop_warn_after', 'loop_warn_message', 'hidden'}
        cleaned = []
        for tool in tools:
            clean_tool = {k: v for k, v in tool.items() if k not in internal_fields}
            cleaned.append(clean_tool)
        return cleaned