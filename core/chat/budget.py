# core/chat/budget.py - the one context budgeter (tmp/context-budget-plan.md)
#
# fit() runs right before EVERY provider call of the two lanes that send a
# conversation (chat_streaming's turn engine, every tool round and the forced
# final; ExecCtx's tool loop). It sees what is really sent: the final message
# list, the tool schemas, the reply's max_tokens, the provider. Before this the
# trim ran once, on history alone, before the provider was even chosen, and
# nothing kept room for the reply: LM Studio, holding 56,320 tokens, was sent
# 56,296 and the model got 24 to answer with (2026-10-27).
#
# What it does, in order:
#   1. The window: the smaller of Context Limit and what the server says it
#      holds (provider.context_window(): LM Studio, llama.cpp, Ollama).
#   2. Tool expiry: old results of tools that say they expire are stubbed to
#      one line on the wire (`expires` on a tool, TOOL_EXPIRES in its file,
#      `tool_expires` in its plugin, else the global TOOL_EXPIRES_AFTER).
#      Decided by what came AFTER a row, never by how full the window is, so a
#      row stubbed once stays stubbed and the cached prefix holds still.
#   3. The count: system, tools, history, tool_call arguments, pictures at
#      their real size for the provider's family, times a ratio learned from
#      the provider's own usage reports.
#   4. Room for the reply, then whole oldest turns out of view behind a
#      watermark (cut to ~65%, held until full again: the prefix moves rarely),
#      with one line telling her.
#   5. Still over with no history left: the live turn's own superseded page
#      views go; still over: ContextFull, honest words, nothing sent.
# The stored chat and the transcript are never touched: only the wire copy.
import base64
import io
import json
import logging
import math
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import config
from core.chat import history as _history


def count_tokens(text) -> int:
    return _history.count_tokens(text)      # through the module: one tokenizer, one seam to stub

logger = logging.getLogger(__name__)

REPLY_FLOOR = 1024          # tokens the reply always keeps, whatever the window
CUT_TO = 0.65               # a cut takes history down to this share of its room
MSG_OVERHEAD = 4            # per message: role and framing
STUB_LINE = 200             # chars of an expired result's first line kept

_lock = threading.Lock()
_watermark: Dict[str, int] = {}      # chat → whole turns out of view at its front
_ratio: Dict[tuple, float] = {}      # (provider_key, model) → real tokens / our count
_dims: Dict[tuple, tuple] = {}       # an image's (len, head, tail) → (w, h)
_last: Dict[str, dict] = {}          # chat → what its last fit sent (the meter)


class ContextFull(Exception):
    """This turn alone is more than the model can hold: nothing was sent."""


@dataclass
class Fit:
    messages: List[Dict[str, Any]]
    max_tokens: Optional[int]
    window: Optional[int] = None     # what the request had to fit in (None = no limit)
    limit: int = 0                   # Context Limit, the setting
    server: Optional[int] = None     # what the server says it holds
    estimate: int = 0                # tokens, calibrated
    raw: int = 0                     # tokens as counted, before the ratio (what learn() divides by)
    reserve: int = 0                 # held back for the reply
    out_of_view: int = 0             # whole turns cut at the front
    stubbed: int = 0                 # tool results sent as one line
    key: tuple = field(default=())   # (provider_key, model) for learn()


# --- counting -----------------------------------------------------------------

def _family(provider) -> str:
    name = str(getattr(provider, 'provider_name', '') or '').lower()
    base = str(getattr(provider, 'base_url', '') or '').lower()
    if 'claude' in name or 'anthropic' in name:
        return 'claude'
    if 'gemini' in name or 'generativelanguage' in base:
        return 'gemini'
    if 'responses' in name or 'api.openai.com' in base:
        return 'openai'
    return 'patches'      # Qwen-VL, Llama, Gemma and kin: one token per 28 px square


def _size(data: str):
    """(w, h) of a base64 image, read from its header once."""
    k = (len(data), data[:64], data[-64:])
    hit = _dims.get(k)
    if hit:
        return hit
    try:
        from PIL import Image
        with Image.open(io.BytesIO(base64.b64decode(data))) as im:
            wh = im.size
    except Exception:
        wh = (1024, 1024)
    if len(_dims) > 512:
        _dims.clear()
    _dims[k] = wh
    return wh


def image_tokens(w: int, h: int, family: str) -> int:
    """What a picture costs the model it is sent to, by the provider's rule."""
    w, h = max(1, int(w)), max(1, int(h))
    if family == 'claude':                       # long edge 1568 and ~1.15 MP, then w*h/750
        s = min(1.0, 1568 / max(w, h), math.sqrt(1_150_000 / (w * h)))
        return int(w * s * h * s / 750) + 1
    if family == 'openai':                       # fit 2048, short side 768, 512 px tiles
        s = min(1.0, 2048 / max(w, h))
        w, h = w * s, h * s
        s = min(1.0, 768 / min(w, h))
        return 85 + 170 * math.ceil(w * s / 512) * math.ceil(h * s / 512)
    if family == 'gemini':
        return 258 * math.ceil(w / 768) * math.ceil(h / 768)
    return min(16384, math.ceil(w / 28) * math.ceil(h / 28) + 2)


def _content_tokens(content, family) -> int:
    if not content:
        return 0
    if isinstance(content, str):
        return count_tokens(content)
    total = 0
    for b in content if isinstance(content, list) else [content]:
        if isinstance(b, str):
            total += count_tokens(b)
        elif isinstance(b, dict):
            if b.get('type') == 'image':
                total += image_tokens(*_size(str(b.get('data') or '')), family)
            else:
                total += count_tokens(str(b.get('text') or ''))
    return total


def _msg_tokens(m, family) -> int:
    n = MSG_OVERHEAD + _content_tokens(m.get('content'), family)
    if m.get('tool_calls'):
        n += count_tokens(json.dumps(m['tool_calls'], ensure_ascii=False))
    return n


def tools_tokens(tools) -> int:
    return sum(count_tokens(json.dumps({'type': t.get('type'), 'function': t.get('function')}, ensure_ascii=False))
               for t in (tools or []) if isinstance(t, dict))


# --- the window ---------------------------------------------------------------

def _limit() -> int:
    try:
        return max(0, int(getattr(config, 'CONTEXT_LIMIT', 0) or 0))
    except (TypeError, ValueError):
        return 0


def server_window(provider) -> Optional[int]:
    try:
        n = provider.context_window() if hasattr(provider, 'context_window') else None
        return n if isinstance(n, int) and not isinstance(n, bool) and n > 0 else None
    except Exception as e:
        logger.debug(f"[CONTEXT] window probe failed: {e}")
        return None


# --- tool expiry --------------------------------------------------------------

def _global_after() -> int:
    try:
        return max(0, int(getattr(config, 'TOOL_EXPIRES_AFTER', 0) or 0))
    except (TypeError, ValueError):
        return 0


def _stub(m, spec) -> dict:
    name = m.get('name') or 'tool'
    text = m.get('content')
    if isinstance(text, list):
        text = ' '.join(str(b.get('text') or '') for b in text if isinstance(b, dict))
    text = str(text or '')
    from core.ui_markers import UI_MARKER_RE
    first = next((ln.strip() for ln in UI_MARKER_RE.sub('', text).splitlines() if ln.strip()), '')[:STUB_LINE]
    hint = (spec or {}).get('hint') or f"Run {name} again if you need it."
    return dict(m, content=f"[{name} result left out of your view to save room: {first} ({len(text)} chars). {hint}]",
                _stub=True)


def _expire(msgs, live_from, expiry, pressure=False):
    """Stub expired tool results. A row before live_from expires when a later
    row of its group exists (superseded) or N of the person's turns came after
    it; pressure=True also lets the live turn's superseded rows go. Returns
    (messages, number stubbed). Images a stubbed result brought go with it."""
    if not expiry:
        return msgs, 0
    glob = _global_after()
    specs, turn_at, turns = {}, {}, 0
    for i, m in enumerate(msgs):
        if m.get('role') == 'user' and not m.get('_inject'):
            turns += 1
        elif m.get('role') == 'tool' and not m.get('_stub'):
            spec = expiry(m.get('name') or '')
            if spec is None and glob:
                spec = {'after': glob, 'group': m.get('name') or '', 'hint': ''}
            if spec and spec.get('after') != 'never':
                specs[i], turn_at[i] = spec, turns
    if not specs:
        return msgs, 0
    last = {}
    for i, spec in specs.items():
        if spec['after'] == 'superseded':
            last[spec['group']] = i
    gone = set()
    for i, spec in specs.items():
        if i >= live_from and not (pressure and spec['after'] == 'superseded'):
            continue
        if spec['after'] == 'superseded':
            if last[spec['group']] > i:
                gone.add(i)
        elif turns - turn_at[i] >= spec['after']:
            gone.add(i)
    if not gone:
        return msgs, 0
    from core.chat.history import _IMG_MARKER_RE
    out, dead_ids, batch, batch_gone = [], set(), [], []
    for i, m in enumerate(msgs):
        if m.get('role') == 'tool':
            batch.append(i)
            if i in gone:
                batch_gone.append(i)
                dead_ids.update(_IMG_MARKER_RE.findall(str(m.get('content') or '')))
                m = _stub(m, specs[i])
            out.append(m)
            continue
        if m.get('_inject') and m.get('role') == 'user' and isinstance(m.get('content'), list) and batch:
            all_gone = len(batch_gone) == len(batch)
            kept = [b for b in m['content'] if not (isinstance(b, dict) and b.get('type') == 'image'
                    and (b.get('id') in dead_ids or (all_gone and not b.get('id'))))]
            if not any(isinstance(b, dict) and b.get('type') == 'image' for b in kept):
                batch, batch_gone = [], []
                continue                               # a picture row with no pictures left
            m = dict(m, content=kept)
        batch, batch_gone = [], []
        out.append(m)
    return out, len(gone)


# --- turns out of view --------------------------------------------------------

def _spans(msgs, lo, hi):
    """Whole turns in msgs[lo:hi]: each starts at the person's row (rows the app
    adds - ghost, RAG, pictures - are not turns), so a tool call never leaves
    without its result."""
    starts = [i for i in range(lo, hi) if msgs[i].get('role') == 'user' and not msgs[i].get('_inject')]
    if not starts:
        return []
    starts[0] = lo
    return [(s, starts[k + 1] if k + 1 < len(starts) else hi) for k, s in enumerate(starts)]


def _note(m, turns) -> dict:
    line = (f"[Earlier in this chat: the first {turns} turn{'s' if turns != 1 else ''} are out of your view, "
            f"to fit what the model can hold. The chat itself keeps them.]")
    c = m.get('content')
    if isinstance(c, list):
        return dict(m, content=[{'type': 'text', 'text': line}] + c)
    return dict(m, content=f"{line}\n\n{c or ''}")


# --- the fit --------------------------------------------------------------------

def fit(messages, tools=None, provider=None, provider_key='', model='', max_tokens=None,
        chat=None, live=None, expiry: Optional[Callable[[str], Optional[dict]]] = None,
        limit: Optional[int] = None) -> Fit:
    """What to send. `live`: this turn's first row (the person's message, or the
    task's) - it and everything after it are this turn and stay whole unless
    nothing else is left. `chat`: whose watermark. `expiry`: tool name → its
    parsed `expires` (FunctionManager.parse_expires) or None. Raises
    ContextFull when the live turn alone can't fit. `limit`: a task's own
    Context Limit in place of the setting (0/None = the setting)."""
    msgs = list(messages)
    if not (isinstance(max_tokens, int) and not isinstance(max_tokens, bool) and max_tokens > 0):
        max_tokens = None                               # unset, or not a number at all
    family = _family(provider)
    key = (provider_key or '', model or '')
    sys_end = 0
    while sys_end < len(msgs) and msgs[sys_end].get('role') == 'system':
        sys_end += 1
    live_from = next((i for i, m in enumerate(msgs) if m is live), len(msgs))
    while live_from > sys_end and msgs[live_from - 1].get('_inject'):
        live_from -= 1                                  # the ghost and RAG rows ride with this turn

    msgs, stubbed = _expire(msgs, live_from, expiry)

    limit, server = (int(limit) if limit else _limit()), server_window(provider)
    window = min([n for n in (limit, server) if n] or [0]) or None
    ratio = _ratio.get(key) or (1.2 if family == 'claude' else 1.05)
    schema = tools_tokens(tools)

    def raw_of(ms):
        return schema + sum(_msg_tokens(m, family) for m in ms)

    raw = raw_of(msgs)
    if not window:
        _remember(chat, int(raw * ratio), server, 0, stubbed)
        return Fit(msgs, max_tokens, None, limit, server, int(raw * ratio), raw, 0, 0, stubbed, key)

    asked = int(max_tokens or 8192)
    reserve = min(asked, max(REPLY_FLOOR, window // 4))
    margin = int(window * 0.02) + 256
    room = window - reserve - margin                    # what the prompt may use

    out_of_view = 0
    if raw * ratio > room:
        spans = _spans(msgs, sys_end, live_from)
        held = _watermark.get(chat, 0) if chat else 0
        held = min(held, len(spans))
        cost = [sum(_msg_tokens(m, family) for m in msgs[s:e]) for s, e in spans]
        fixed = raw - sum(cost)                         # system, tools, the live turn
        target = (fixed + CUT_TO * max(0, room / ratio - fixed)) * ratio
        n, now = held, raw - sum(cost[:held])
        if now * ratio > room:                          # past the watermark too: cut down to the target
            while n < len(spans) and now * ratio > target:
                now -= cost[n]
                n += 1
        if n:
            cut = spans[n - 1][1] if n <= len(spans) else live_from
            kept = msgs[:sys_end] + msgs[cut:]
            if cut < live_from:
                kept[sys_end] = _note(kept[sys_end], n)
            live_from -= cut - sys_end
            msgs, raw, out_of_view = kept, raw_of(kept), n
        if chat:
            with _lock:
                if _watermark.get(chat) != n:
                    logger.info(f"[CONTEXT] '{chat}': {n} oldest turn(s) out of view "
                                f"(window {window}, ~{int(raw * ratio)} tokens now)")
                _watermark[chat] = n
    elif chat and _watermark.get(chat):
        with _lock:
            _watermark.pop(chat, None)                  # it all fits again (a bigger window, a cleared chat)

    if raw * ratio > room:                              # no history left to cut: this turn's own old page views
        msgs, more = _expire(msgs, live_from, expiry, pressure=True)
        stubbed += more
        raw = raw_of(msgs)
    est = int(raw * ratio)
    if est + margin + REPLY_FLOOR > window:
        raise ContextFull(_full_words(est, window, schema * ratio, limit, server))

    send = max_tokens
    if server and window == server:                     # the server's own window: ask only for what's left
        send = max(REPLY_FLOOR, min(asked, window - est - margin))
    _remember(chat, est, server, out_of_view, stubbed)
    return Fit(msgs, send, window, limit, server, est, raw, reserve, out_of_view, stubbed, key)


def _remember(chat, est, server, out_of_view, stubbed):
    if chat:
        with _lock:
            _last[chat] = {'used': est, 'server': server, 'out_of_view': out_of_view, 'stubbed': stubbed}


def meter(chat, fallback_used: int) -> dict:
    """The context bar: {used, limit, percent, notch, server, out_of_view,
    stubbed, measured}. The bar spans Context Limit (Krem's vote: our setting
    is the 100%); `notch` marks where the server's own window ends when that
    is smaller; `used` is what this chat's last fit really sent (measured), or
    the stored chat counted (fallback_used) before any turn this run."""
    last = _last.get(chat) if chat else None
    used = last['used'] if last else int(fallback_used or 0)
    server = last.get('server') if last else None
    limit = _limit() or server or 0
    pct = min(100, int(used * 100 / limit)) if limit else 0
    notch = round(server * 100 / limit, 1) if server and limit and server < limit else None
    return {'used': used, 'limit': limit, 'percent': pct, 'notch': notch, 'server': server,
            'out_of_view': last['out_of_view'] if last else 0, 'stubbed': last['stubbed'] if last else 0,
            'measured': bool(last)}


def _full_words(est, window, schema, limit, server) -> str:
    where = (f"the model holds {server:,} tokens" if server and window == server
             else f"Context Limit is {limit:,} tokens")
    if schema > window * 0.5:
        return (f"Her tools alone take about {int(schema):,} tokens and {where}, so there's no room left "
                f"to talk. Pick a smaller toolset for this chat, or give the model a bigger context.")
    return (f"This turn alone is about {est:,} tokens and {where}, so nothing was sent. "
            f"Give the model a bigger context, or ask for less in one go.")


def learn(f: Optional[Fit], tokens: Optional[dict]):
    """The provider's own count of what was sent tunes our ratio for that
    model: real / counted, smoothed. Skips estimates and empty reports."""
    if not f or not f.raw or not isinstance(tokens, dict) or tokens.get('estimated'):
        return
    real = sum(int(tokens.get(k) or 0) for k in ('prompt', 'cache_read_tokens', 'cache_write_tokens'))
    if real <= 0:
        return
    r = max(0.6, min(2.5, real / f.raw))
    with _lock:
        old = _ratio.get(f.key)
        _ratio[f.key] = r if old is None else 0.7 * old + 0.3 * r


def learn_usage(f: Optional[Fit], usage: Optional[dict]):
    """learn() from a provider's raw usage dict (LLMResponse.usage)."""
    if isinstance(usage, dict):
        learn(f, {'prompt': usage.get('prompt_tokens'), 'cache_read_tokens': usage.get('cache_read_tokens'),
                  'cache_write_tokens': usage.get('cache_write_tokens')})


def tighten(provider_key='', model=''):
    """The server said the request was too long though we counted it in: count
    this model 25% heavier from now on (learn() walks it back if it overshoots)."""
    key = (provider_key or '', model or '')
    with _lock:
        _ratio[key] = min(2.5, (_ratio.get(key) or 1.05) * 1.25)
    logger.warning(f"[CONTEXT] {key[0]} {key[1]}: refused as too long - counting it 25% heavier")


def forget(chat=None):
    """Drop a chat's watermark (all of them with none): its next fit recomputes."""
    with _lock:
        if chat is None:
            _watermark.clear()
            _last.clear()
        else:
            _watermark.pop(chat, None)
            _last.pop(chat, None)
