# llm_providers/gemini.py
"""
Google Gemini via its OpenAI-compatible endpoint.

Rebuilt 2026-10-27 on live probes (3.8-flash, 3.5-flash-lite, 2.5-flash), not docs:

- Thinking takes ONE knob: `reasoning_effort` OR `google.thinking_config`. Both
  is a 400, even a config holding only include_thoughts. We always send the
  config. In the JSON body it sits at {"extra_body": {"google": ...}}; the SDK's
  extra_body kwarg flattens one level, so it nests twice here. (One level put
  `google` at the top: 'Unknown name "google"', the user report.)
- 3.x+ takes thinking_level; 2.5 refuses levels and takes thinking_budget. A
  level a model lacks (minimal on 3.8) is a 400: learned once, stepped up, retried.
- Off: 2.5 Flash honors reasoning_effort "none". 3.x and 2.5 Pro can't stop
  thinking; "off" there means the least they allow, thoughts hidden.
- include_thoughts returns thinking inline as <thought>…</thought> at the head
  of content, the closer sharing a chunk with the answer. Split into thinking
  events here, so tags never reach history.
- Tool calls carry extra_content.google.thought_signature (first parallel call
  only); 3.x 400s a request whose current-turn calls lack it. We replay what came
  and give calls from another model Google's skip value.
- usage.completion_tokens leaves thinking out; total doesn't. The gap is thinking.
"""

import logging
import re
from typing import Dict, Any, List, Optional

from core import think as _think
from .openai_compat import OpenAICompatProvider

logger = logging.getLogger(__name__)

LEVELS = ('minimal', 'low', 'medium', 'high')
# Google's own reasoning_effort → thinking_budget mapping for 2.5
BUDGETS = {'minimal': 1024, 'low': 1024, 'medium': 8192, 'high': 24576}
SKIP_SIGNATURE = 'skip_thought_signature_validator'
_UNSUPPORTED = ('stop', 'frequency_penalty', 'presence_penalty', 'repeat_penalty',
                'top_k', 'reasoning_effort')
_VERSION_RE = re.compile(r'gemini-(\d+)(?:\.(\d+))?')
_LEVEL_REFUSED_RE = re.compile(r'thinking level (\w+) is not supported', re.IGNORECASE)
OPEN_T, CLOSE_T = '<thought>', '</thought>'

# model → thinking levels it refused (process lifetime; providers rebuild per turn)
_REFUSED_LEVELS: Dict[str, set] = {}


def _version(model) -> Optional[tuple]:
    m = _VERSION_RE.search((model or '').lower())
    return (int(m.group(1)), int(m.group(2) or 0)) if m else None


def _effort(value) -> str:
    e = str(value or 'medium').lower()
    if e in LEVELS:
        return e
    return 'high' if e in ('xhigh', 'max') else 'medium'


def _thinking_config(kwargs) -> dict:
    google = ((kwargs.get('extra_body') or {}).get('extra_body') or {}).get('google') or {}
    return google.get('thinking_config') or {}


def _count_thinking(usage) -> None:
    """Fold the hidden thinking gap into completion (OpenAI's convention:
    reasoning is part of completion) and name it."""
    if not usage:
        return
    gap = ((usage.get('total_tokens') or 0) - (usage.get('prompt_tokens') or 0)
           - (usage.get('cache_read_tokens') or 0) - (usage.get('completion_tokens') or 0))
    if gap > 0:
        usage['completion_tokens'] = (usage.get('completion_tokens') or 0) + gap
        usage['reasoning_tokens'] = max(usage.get('reasoning_tokens') or 0, gap)


class _Thoughts:
    """Splits streamed content into ('thinking' | 'content', text) pieces.
    Only a reply that hasn't said anything yet can open a thought; after
    that a <thought> tag is a word."""

    def __init__(self):
        self.buf, self.inside, self.said, self.thought = '', False, '', ''

    def _emit(self, out, kind, text):
        if not text:
            return
        out.append((kind, text))
        if kind == 'thinking':
            self.thought += text
        else:
            self.said += text

    def feed(self, text) -> List[tuple]:
        self.buf += text or ''
        out = []
        while self.buf:
            if self.inside:
                at = self.buf.find(CLOSE_T)
                if at < 0:                                   # hold a partial closer back
                    keep = next((k for k in range(len(CLOSE_T) - 1, 0, -1)
                                 if self.buf.endswith(CLOSE_T[:k])), 0)
                    cut = len(self.buf) - keep
                    self._emit(out, 'thinking', self.buf[:cut])
                    self.buf = self.buf[cut:]
                    break
                self._emit(out, 'thinking', self.buf[:at])
                self.buf, self.inside = self.buf[at + len(CLOSE_T):], False
                continue
            lead = self.buf.lstrip()
            if not self.said.strip():
                if lead.startswith(OPEN_T):
                    self.buf, self.inside = lead[len(OPEN_T):], True
                    continue
                if OPEN_T.startswith(lead):                  # blank or a partial opener: wait
                    break
            self._emit(out, 'content', self.buf)
            self.buf = ''
        return out

    def flush(self) -> List[tuple]:
        out = []
        self._emit(out, 'thinking' if self.inside else 'content', self.buf)
        self.buf = ''
        return out


class GeminiProvider(OpenAICompatProvider):
    """Gemini via OpenAI-compatible API with thinking + thought signatures."""

    OWNS_THINKING_SWITCH = True
    REPLAYS_TOOL_EXTRA = True

    @property
    def supports_images(self) -> bool:
        return True

    def __init__(self, llm_config: Dict[str, Any], request_timeout: float = 240.0):
        super().__init__(llm_config, request_timeout)
        self._thinking_enabled = llm_config.get('thinking_enabled') is not False
        self._reasoning_effort = llm_config.get('reasoning_effort', 'medium')
        self._disable = bool(llm_config.get('disable_thinking'))

    # ── request ──────────────────────────────────────────────────────────

    def _level(self, want: str) -> Optional[str]:
        refused = _REFUSED_LEVELS.get(self.model, ())
        return next((lvl for lvl in LEVELS[LEVELS.index(want):] if lvl not in refused), None)

    def _thinking(self, disable: bool):
        """(google block or None, reasoning_effort or None) — never both."""
        v = _version(self.model)
        if not v or v < (2, 5):
            return None, None
        if disable:
            if v < (3, 0) and 'pro' not in self.model.lower():
                return None, 'none'                          # 2.5 Flash really stops
            want, thoughts = 'minimal', False
        elif self._thinking_enabled:
            want, thoughts = _effort(self._reasoning_effort), True
        else:
            return None, None                                # model default, thoughts unseen
        if v < (3, 0):
            cfg = {'thinking_budget': BUDGETS[want]}
        else:
            level = self._level(want)
            cfg = {'thinking_level': level} if level else {}
        if thoughts:
            cfg['include_thoughts'] = True
        return ({'thinking_config': cfg} if cfg else None), None

    def _transform_params_for_model(self, params: Dict[str, Any]) -> Dict[str, Any]:
        params = dict(params or {})
        disable = bool(params.pop('disable_thinking', False)) or self._disable
        # Unsupported knobs out before the base lift (else top_k would ride
        # extra_body into an endpoint that doesn't take it).
        params = {k: v for k, v in params.items() if k not in _UNSUPPORTED}
        result = super()._transform_params_for_model(params) or {}
        google, effort = self._thinking(disable)
        if google:
            result.setdefault('extra_body', {})['extra_body'] = {'google': google}
        if effort:
            result['reasoning_effort'] = effort
        return result

    def _create(self, request_kwargs: Dict[str, Any]):
        """A thinking level this model lacks: remember it, step up, retry."""
        while True:
            try:
                return super()._create(request_kwargs)
            except Exception as e:
                m = _LEVEL_REFUSED_RE.search(f"{e} {getattr(e, 'body', '') or ''}")
                cfg = _thinking_config(request_kwargs)
                refused = m.group(1).lower() if m else None
                if not refused or cfg.get('thinking_level') != refused:
                    raise
                _REFUSED_LEVELS.setdefault(self.model, set()).add(refused)
                nxt = self._level(refused)
                logger.warning(f"[GEMINI] {self.model} has no thinking level '{refused}' "
                               f"— using '{nxt or 'model default'}' until restart")
                if nxt:
                    cfg['thinking_level'] = nxt
                else:
                    cfg.pop('thinking_level', None)

    def _sanitize_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        clean = super()._sanitize_messages(messages)
        v = _version(self.model)
        if v and v >= (3, 0):
            for msg in clean:
                calls = msg.get('tool_calls')
                if calls and not ((calls[0].get('extra_content') or {}).get('google') or {}).get('thought_signature'):
                    # From another model (or before signatures were kept)
                    calls[0]['extra_content'] = {'google': {'thought_signature': SKIP_SIGNATURE}}
        return clean

    # ── response ─────────────────────────────────────────────────────────

    def chat_completion_stream(self, messages, tools=None, generation_params=None):
        split = _Thoughts()
        for event in super().chat_completion_stream(messages, tools=tools,
                                                    generation_params=generation_params):
            kind = event.get('type')
            if kind == 'content':
                for k, text in split.feed(event.get('text', '')):
                    yield {'type': k, 'text': text}
                continue
            if kind == 'done':
                for k, text in split.flush():
                    yield {'type': k, 'text': text}
                resp = event.get('response')
                if resp is not None:
                    resp.content = split.said or None
                    _count_thinking(resp.usage)
                if split.thought:
                    event['thinking'] = split.thought
            yield event

    def _parse_response(self, response):
        resp = super()._parse_response(response)
        split = _Thoughts()
        split.feed(resp.content or '')
        split.flush()
        if split.thought:
            resp.thinking = split.thought
            if split.said.strip():
                resp.content = _think.wrap(split.thought, split.said)
            else:
                resp.content, resp.content_is_reasoning = split.thought, True
        _count_thinking(resp.usage)
        return resp
