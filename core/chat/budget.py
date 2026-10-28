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
import time
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
_watermark: Dict[str, tuple] = {}    # chat → (whole turns out of view at its front, the window they were cut for)
_ratio: Dict[tuple, float] = {}      # (provider_key, model) → real tokens / our count
_boost: Dict[tuple, float] = {}      # (provider_key, model) → one heavier fit, after a too-long refusal
_dims: Dict[tuple, tuple] = {}       # an image's (len, head, tail) → (w, h)
_last: Dict[str, dict] = {}          # chat → what its last fit sent (the meter)
_sent: Dict[str, dict] = {}          # chat → its last send: at, key, stubs (the warm-cache hold)
_sender: Dict[tuple, str] = {}       # (provider_key, model) → the chat that last sent to it


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
            elif b.get('type') == 'tool_result':      # Claude's shape for a result this turn: its text is in content
                total += _content_tokens(b.get('content'), family)
            else:
                total += count_tokens(str(b.get('text') or ''))
    return total


def _msg_tokens(m, family) -> int:
    n = MSG_OVERHEAD + _content_tokens(m.get('content'), family)
    if m.get('tool_calls'):
        n += count_tokens(json.dumps(m['tool_calls'], ensure_ascii=False))
    if m.get('thinking_raw'):                          # Claude's signed thinking rides back inside a tool cycle
        n += count_tokens(json.dumps(m['thinking_raw'], ensure_ascii=False, default=str))
    return n


def _results(m):
    """The tool_result blocks of a Claude-shaped result row (role user), or []."""
    c = m.get('content')
    if m.get('role') != 'user' or not isinstance(c, list) or not c:
        return []
    blocks = [b for b in c if isinstance(b, dict)]
    return blocks if blocks and all(b.get('type') == 'tool_result' for b in blocks) else []


def _is_result(m) -> bool:
    return m.get('role') == 'tool' or bool(_results(m))


def _is_person(m) -> bool:
    """A row the person (or a task's prompt) wrote: not one the app added
    (ghost, RAG, pictures: `_inject`), not a tool result in Claude's shape."""
    return m.get('role') == 'user' and not m.get('_inject') and not _results(m)


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


def _text_of(content) -> str:
    if isinstance(content, list):
        return ' '.join(_text_of(b.get('content')) if b.get('type') == 'tool_result' else str(b.get('text') or '')
                        for b in content if isinstance(b, dict))
    return str(content or '')


def _stub(m, spec, name) -> dict:
    text = _text_of(m.get('content'))
    from core.ui_markers import UI_MARKER_RE
    first = next((ln.strip() for ln in UI_MARKER_RE.sub('', text).splitlines() if ln.strip()), '')[:STUB_LINE]
    hint = (spec or {}).get('hint') or f"Run {name} again if you need it."
    line = f"[{name} result left out of your view to save room: {first} ({len(text)} chars). {hint}]"
    if m.get('role') == 'tool':
        return dict(m, content=line, _stub=True)
    return dict(m, content=[dict(b, content=line) for b in _results(m)], _stub=True)


def _rid(m):
    """A result row's identity across turns: its call id."""
    if m.get('role') == 'tool':
        return m.get('tool_call_id')
    r = _results(m)
    return r[0].get('tool_use_id') if r else None


def _expire(msgs, live_from, expiry, pressure=False, only=None):
    """Stub expired tool results. A row before live_from expires when a later
    row of its group exists (superseded) or N of the person's turns came after
    it; pressure=True also lets the live turn's superseded rows go. Returns
    (messages, number stubbed). Images a stubbed result brought go with it.
    expiry(name): the tool's parsed `expires`, None = declared nothing (the
    global TOOL_EXPIRES_AFTER applies), False = no such tool loaded (never
    expires: a memory tool whose plugin is off is still a memory tool).
    only: a set of row ids - while the prompt cache is warm, only these (the
    ones already stubbed) are; the rest wait (None = all that expired). The
    third value returned is the ids stubbed."""
    if not expiry:
        return msgs, 0, set()
    glob = _global_after()
    specs, turn_at, names, turns, called = {}, {}, {}, 0, {}
    for i, m in enumerate(msgs):
        for tc in m.get('tool_calls') or []:
            if isinstance(tc, dict):
                called[tc.get('id')] = (tc.get('function') or {}).get('name')
        if _is_person(m):
            turns += 1
        elif _is_result(m) and not m.get('_stub'):
            name = m.get('name') if m.get('role') == 'tool' else called.get(_results(m)[0].get('tool_use_id'))
            spec = expiry(name or '')
            if spec is None and glob:
                spec = {'after': glob, 'group': name or '', 'hint': ''}
            if spec and spec.get('after') != 'never':
                specs[i], turn_at[i], names[i] = spec, turns, name or 'tool'
    if not specs:
        return msgs, 0, set()
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
    if only is not None:
        gone = {i for i in gone if _rid(msgs[i]) in only}
    if not gone:
        return msgs, 0, set()
    from core.chat.history import _IMG_MARKER_RE
    out, dead_ids, batch, batch_gone = [], set(), [], []
    for i, m in enumerate(msgs):
        if _is_result(m):
            batch.append(i)
            if i in gone:
                batch_gone.append(i)
                dead_ids.update(_IMG_MARKER_RE.findall(_text_of(m.get('content'))))
                m = _stub(m, specs[i], names[i])
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
    return out, len(gone), {_rid(msgs[i]) for i in gone}


# --- turns out of view --------------------------------------------------------

def _spans(msgs, lo, hi):
    """Whole turns in msgs[lo:hi]: each starts at the person's row (rows the app
    adds - ghost, RAG, pictures - are not turns), so a tool call never leaves
    without its result."""
    starts = [i for i in range(lo, hi) if _is_person(msgs[i])]
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

def _live_index(msgs, live, sys_end) -> int:
    """Where this turn starts in msgs: its own row, found by identity (rows
    before it can be dropped or replaced), then back over the ghost and RAG
    rows that ride with it."""
    i = next((k for k, m in enumerate(msgs) if m is live), len(msgs))
    while i > sys_end and msgs[i - 1].get('_inject') and msgs[i - 1].get('role') == 'user' and not _results(msgs[i - 1]):
        i -= 1
    return i


def fit(messages, tools=None, provider=None, provider_key='', model='', max_tokens=None,
        chat=None, live=None, expiry: Optional[Callable[[str], Optional[dict]]] = None,
        limit: Optional[int] = None, label: Optional[str] = None) -> Fit:
    """What to send. `live`: this turn's first row (the person's message, or the
    task's) - it and everything after it are this turn and stay whole unless
    nothing else is left. `chat`: whose watermark. `expiry`: see _expire.
    Raises ContextFull when the live turn alone can't fit. `limit`: a task's
    own Context Limit in place of the setting (0/None = the setting). `label`:
    how the log names the chat (a private chat's name never reaches the log)."""
    msgs = list(messages)
    if not (isinstance(max_tokens, int) and not isinstance(max_tokens, bool) and max_tokens > 0):
        max_tokens = None                               # unset, or not a number at all
    family = _family(provider)
    key = (provider_key or '', model or '')
    sys_end = 0
    while sys_end < len(msgs) and msgs[sys_end].get('role') == 'system':
        sys_end += 1

    base = msgs
    warm = _warm(chat, key, provider, family)
    prev = (_sent.get(chat) or {}).get('stubs', set()) if chat else set()
    msgs, stubbed, ids = _expire(base, _live_index(base, live, sys_end), expiry, only=prev if warm else None)
    live_from = _live_index(msgs, live, sys_end)

    limit, server = (int(limit) if limit else _limit()), server_window(provider)
    window = min([n for n in (limit, server) if n] or [0]) or None
    ratio = _ratio.get(key) or (1.2 if family == 'claude' else 1.05)
    with _lock:
        ratio *= _boost.pop(key, 1.0)                    # tighten() counts for the retry, not forever
    schema = tools_tokens(tools)
    tk = [_msg_tokens(m, family) for m in msgs]          # each row counted once per fit
    raw = schema + sum(tk)
    if not window:
        _remember(chat, int(raw * ratio), server, 0, stubbed)
        _record(chat, key, ids)
        return Fit(msgs, max_tokens, None, limit, server, int(raw * ratio), raw, 0, 0, stubbed, key)

    asked = int(max_tokens or 8192)
    floor = min(REPLY_FLOOR, asked)                     # what the reply always keeps
    reserve = min(asked, max(floor, window // 4))
    margin = int(window * 0.02) + 256
    room = window - reserve - margin                    # what the prompt may use

    if warm and raw * ratio > room:                     # warm, but full: the stubs that waited go first
        msgs, stubbed, ids = _expire(base, _live_index(base, live, sys_end), expiry)
        live_from = _live_index(msgs, live, sys_end)
        tk = [_msg_tokens(m, family) for m in msgs]
        raw = schema + sum(tk)

    spans = _spans(msgs, sys_end, live_from)
    cost = [sum(tk[s:e]) for s, e in spans]
    held, held_window = _watermark.get(chat, (0, None)) if chat else (0, None)
    if held_window != window or held >= len(spans):
        held = 0                                        # another window (a model switch), or turns deleted under it: start over
    if held and raw * ratio <= CUT_TO * room and not warm:
        held = 0                                        # the whole chat fits with room to spare again (and the cache is cold)
    n, now = held, raw - sum(cost[:held])
    if now * ratio > room:                              # over even past the watermark: cut down to ~65%
        fixed = raw - sum(cost)                         # system, tools, the live turn
        target = (fixed + CUT_TO * max(0, room / ratio - fixed)) * ratio
        while n < len(spans) and now * ratio > target:
            now -= cost[n]
            n += 1
    if chat:
        with _lock:
            if (_watermark.get(chat) or (0,))[0] != n:
                logger.info(f"[CONTEXT] '{label or chat}': {n} oldest turn(s) out of view "
                            f"(window {window}, ~{int(now * ratio)} tokens now)")
            if n:
                _watermark[chat] = (n, window)
            else:
                _watermark.pop(chat, None)
    out_of_view = 0
    if n:
        cut = spans[n - 1][1]
        msgs = msgs[:sys_end] + msgs[cut:]
        live_from = _live_index(msgs, live, sys_end)
        first = next((k for k in range(sys_end, len(msgs)) if _is_person(msgs[k])), None)
        if first is not None:                           # her note rides the first row she still sees, the live one at worst
            msgs[first] = _note(msgs[first], n)
            if first >= live_from and msgs[first] is not live:
                live = msgs[first]
        raw, out_of_view = now, n

    if raw * ratio > room:                              # no history left to cut: this turn's own old page views
        msgs, more, more_ids = _expire(msgs, live_from, expiry, pressure=True)
        stubbed += more
        ids |= more_ids
        raw = schema + sum(_msg_tokens(m, family) for m in msgs)
    est = int(raw * ratio)
    if est + margin + floor > window:
        raise ContextFull(_full_words(est, window, schema * ratio, limit, server))

    send = max_tokens
    if max_tokens or (server and window == server):    # never ask for more reply than the window has left
        send = max(floor, min(asked, window - est - margin))
    _remember(chat, est, server, out_of_view, stubbed)
    _record(chat, key, ids)
    return Fit(msgs, send, window, limit, server, est, raw, reserve, out_of_view, stubbed, key)


# --- the prompt cache ------------------------------------------------------------
# Sapphire's catch (2026-10-27): every stub or cut changes the prompt from that
# row on, so a warm cache is paid for again (Claude writes it anew; LM Studio
# reprocesses from there). While a chat's cache is warm, the stubs it has
# earned since wait - until it is cold, or until room runs out.

def _ttl(provider, family) -> float:
    """Seconds a provider keeps this chat's prompt cached after a call."""
    if family == 'claude':
        conf = getattr(provider, 'config', None)
        return 3600.0 if isinstance(conf, dict) and conf.get('cache_ttl') == '1h' else 300.0
    if server_window(provider):                         # a local server: one prompt's cache, kept while loaded
        return 3600.0
    return 300.0


def _warm(chat, key, provider, family) -> bool:
    """This chat's last send is still cached at that provider: the same model,
    inside its cache time, and (a local server keeps only the last prompt) no
    other chat has sent to it since."""
    last = _sent.get(chat) if chat else None
    if not last or last.get('key') != key:
        return False
    if server_window(provider) and _sender.get(key) != chat:
        return False
    return time.time() - last.get('at', 0) < _ttl(provider, family)


def warm(chat) -> bool:
    """For a plugin or the meter: was this chat's last send recent enough to
    still be cached? (Without the provider: its own cache time is assumed 5 min.)"""
    last = _sent.get(chat) if chat else None
    return bool(last) and time.time() - last.get('at', 0) < 300


def _record(chat, key, ids):
    if chat:
        with _lock:
            _sent[chat] = {'at': time.time(), 'key': key, 'stubs': set(ids)}
            _sender[key] = chat


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
    if real < 0.5 * f.raw:
        # No tokenizer is half another's: this is a server counting only the
        # tokens it evaluated, its reused KV prefix left out (Ollama's
        # prompt_eval_count). Learning it would sink the ratio and let fit()
        # overfill the very servers it guards (hunt 19).
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


def tighten(provider_key='', model='', provider=None):
    """The server said the request was too long though we counted it in: ask
    the server its window again and count the NEXT fit (the retry) 25%
    heavier. Only that one: it used to stay, and it only came down on a
    send that worked, so after a refusal or two every turn was refused here
    before reaching the model, until a restart (hunt 20). The retry's own
    usage report teaches the real ratio (learn)."""
    if provider is not None and hasattr(provider, 'forget_window'):
        try:
            provider.forget_window()
        except Exception:
            pass
    key = (provider_key or '', model or '')
    with _lock:
        _boost[key] = 1.25
    logger.warning(f"[CONTEXT] {key[0]} {key[1]}: refused as too long - counting the retry 25% heavier")


def forget(chat=None):
    """Drop a chat's watermark (all of them with none): its next fit recomputes."""
    with _lock:
        if chat is None:
            _watermark.clear()
            _last.clear()
            _sent.clear()
        else:
            _watermark.pop(chat, None)
            _last.pop(chat, None)
            _sent.pop(chat, None)
