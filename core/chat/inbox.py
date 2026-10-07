# core/chat/inbox.py - one queue per chat for everything that wants her attention
#
# Six doors can start a turn on a chat (typed, satellite voice, MCP, agents,
# MIDI, cadence); the engine allows one live turn per chat. Before this file
# the losers each had their own retry-then-drop loop. Now a door `put`s an
# Item: if the chat is free it runs at once, if not it waits and runs when her
# current message truly ends (all tool rounds). Record: tmp/chat-inbox-plan.md.
#
# Two lanes answer one question - who is waiting? `now` is a person (typed,
# spoke to a satellite): FIFO, keeps its voice lane. `later` is a machine
# (agent reports and questions, MCP ask/tell, a MIDI take), each alone.
# Folding (one turn for several items) is same-kind only and, as of Krem's
# 2026-10-06 ruling, used by one kind: a person's typed turns that stood in
# line together - continuations of one thought. An agent's return, a daemon's
# message, a take: one event from one author, her own reply to each.
#
# An item either carries `text` (run through cadence.run_turn) or `run`, a
# door's own turn (LLMChat.chat, the conversation driver) that the inbox only
# orders. Either way the drainer is the ONE caller: `put` never runs a turn
# itself, so a tell returns at once and a tool inside her turn can queue
# without deadlock. The idle Event is a hint; begin_stream is the judge - on
# ChatBusy the item goes back to the head and the drainer backs off.
import contextvars
import logging
import threading
import time
import uuid
from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

COALESCE_S = 3.0              # foldable items that land this close ride one turn
DEPTH_MAX = 20                # per chat; past it put() refuses with a reason
SWEEP_S = 2.0                 # the drainer looks again this often with no signal
DRAINER_IDLE_EXIT_S = 10.0    # an empty inbox lets its thread go
BACKOFF_MIN, BACKOFF_MAX = 0.1, 2.0   # after ChatBusy: a typed turn won the race
UNREACHABLE = ("isn't reachable", "sealed", "not found", "unreachable", "does not exist")


class InboxRefused(Exception):
    """put() said no, with a reason fit to show."""


@dataclass
class Item:
    text: str = ''                                  # what she is told (cadence.run_turn), or
    run: Optional[Callable[[], Any]] = None         # a door's own turn: fn() -> her text; begin_stream(exclusive=True) inside
    source: str = 'inbox'                           # 'web' | 'device:<id>' | 'agent:<kind>' | 'mcp:<where>' | 'midi' ...
    lane: str = 'later'                             # 'now' = a person is waiting · 'later' = a machine
    header: str = ''                                # the line above text; '' when the source wrote its own
    coalesce: bool = False                          # the caller's promise: text only, no context needs, may fold
    ttl: Optional[float] = None                     # seconds before it is stale (nothing uses it in v1)
    speak: Any = None                               # run_turn's voice lane ('speakers' | 'device:<id>')
    images: Any = None
    on_event: Optional[Callable] = None
    stream_speech: bool = False
    on_start: Optional[Callable[[], None]] = None   # the item is about to run (a device shows 'thinking')
    on_drop: Optional[Callable[[str], None]] = None  # the item was dropped, with why
    reply: Future = field(default_factory=Future)   # her text, or the exception, when it ran
    ticket: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    ctx: Optional[contextvars.Context] = None       # the put-time context the turn runs under
    put_at: float = field(default_factory=time.monotonic)
    # Same-kind folding (Krem 2026-10-06: "only combine types of returns together").
    # fold_key names the kind - 'web' for a person's typed turns, 'agent' for
    # agent reports and questions, 'midi' for takes; '' = never folds. Items
    # of one kind that stand together at the head of their lane ride ONE turn.
    fold_key: str = ''
    payload: Any = None                             # a door's own data for a folded run (the web route's started Future)
    run_folded: Optional[Callable[[list], Any]] = None   # run(), given the OTHER items folded in with this one

    def __post_init__(self):
        if not self.fold_key and self.coalesce and self.run is None:
            self.fold_key = (self.source or '').split(':', 1)[0]

    @property
    def foldable(self):
        """A machine item that may ride a folded turn (text only, no voice lane
        of its own, no context needs). People's typed turns fold through
        run_folded instead."""
        return (self.lane == 'later' and self.coalesce and self.run is None
                and self.on_event is None and not self.images and not self.stream_speech
                and self.speak in (None, 'speakers') and bool(self.fold_key))

    def age(self):
        return time.monotonic() - self.put_at


class _Chat:
    def __init__(self, name):
        self.name = name
        self.lock = threading.Lock()
        self.now_q = deque()
        self.later_q = deque()
        self.wake = threading.Event()
        self.alive = False
        self.thread = None
        self.idle_since = None


_chats = {}
_lock = threading.Lock()


def header(who, kind='', what=''):
    """The one line above an item's text: who wrote it, what it is, and that
    the user did not type it. One shape for every door."""
    who = ' '.join(str(who or 'something').split())[:80]
    kind = f" ({' '.join(str(kind).split())[:40]})" if kind else ''
    what = f" — {' '.join(str(what).split())[:120]}" if what else ''
    return f"[{who}{kind}{what}; not typed by the user]"


# --- the public door ----------------------------------------------------------

def put(chat, item):
    """Queue `item` on `chat`. Returns the item (its ticket set). Raises
    InboxRefused when the chat cannot take it. Never runs a turn itself."""
    chat = str(chat or '').strip()
    if not chat:
        raise InboxRefused('The inbox needs a chat name.')
    if not isinstance(item, Item):
        raise TypeError('put() takes an Item')
    why = _refusal(chat)
    if why:
        raise InboxRefused(why)
    if item.lane not in ('now', 'later'):
        raise InboxRefused(f"lane must be 'now' or 'later', not {item.lane!r}")
    if item.run is None and not (item.text or '').strip():
        raise InboxRefused('An item needs text or a run().')
    if item.ctx is None:
        item.ctx = contextvars.copy_context()
    c = _chat(chat)
    with c.lock:
        if len(c.now_q) + len(c.later_q) >= DEPTH_MAX:
            raise InboxRefused(f"Her inbox for '{chat}' is full ({DEPTH_MAX} waiting).")
        (c.now_q if item.lane == 'now' else c.later_q).append(item)
        c.idle_since = None
        if not c.alive:
            c.alive = True
            c.thread = threading.Thread(target=_drain, args=(c,), daemon=True,
                                        name=f"inbox-{chat[:24]}")
            c.thread.start()
    c.wake.set()
    _changed(c)
    return item


def tell(chat, text, source, header_line='', coalesce=True, **kw):
    """A machine says something and does not wait. Returns the ticket."""
    return put(chat, Item(text=text, source=source, lane='later', header=header_line,
                          coalesce=coalesce, **kw)).ticket


def ask(chat, text, source, header_line='', timeout=None, **kw):
    """A machine asks and waits for her reply (an MCP ask). Runs alone, in the
    put-time context. Raises InboxRefused from inside this chat's own live turn
    (she would wait on herself)."""
    _refuse_self_wait(chat)
    item = put(chat, Item(text=text, source=source, lane='later', header=header_line,
                          coalesce=False, **kw))
    return item.reply.result(timeout)


def turn(chat, fn, source='door', lane='now', timeout=None):
    """A door runs its OWN turn when it is this chat's turn: fn() is called on
    the drainer thread under the caller's context and must begin_stream(
    exclusive=True) itself (ChatBusy from inside it means a typed turn won the
    race; the inbox tries again). Blocks the caller until fn returns, and
    returns what fn returned. The wake word and the conversation driver."""
    _refuse_self_wait(chat)
    item = put(chat, Item(run=fn, source=source, lane=lane))
    return item.reply.result(timeout)


def drop(chat, ticket, why='dropped'):
    """Remove one queued item. True if it was there."""
    c = _chats.get(str(chat or '').strip())
    if c is None:
        return False
    gone = None
    with c.lock:
        for q in (c.now_q, c.later_q):
            for it in list(q):
                if it.ticket == ticket:
                    q.remove(it)
                    gone = it
                    break
            if gone:
                break
    if gone is None:
        return False
    _dropped(gone, why)
    _changed(c)
    return True


def drop_chat(chat, why='chat gone'):
    """The chat was deleted or sealed: everything waiting on it is dropped."""
    c = _chats.get(str(chat or '').strip())
    if c is None:
        return 0
    with c.lock:
        items = list(c.now_q) + list(c.later_q)
        c.now_q.clear()
        c.later_q.clear()
    for it in items:
        _dropped(it, why)
    if items:
        _changed(c)
    return len(items)


def rename_chat(old, new):
    """A chat was renamed: its queue follows."""
    old, new = str(old or '').strip(), str(new or '').strip()
    with _lock:
        c = _chats.pop(old, None)
        if c is None or not new:
            return
        c.name = new
        _chats[new] = c


def peek(chat):
    """What waits on a chat: [{ticket, source, lane, age}] - ids and sources
    only, never the text (the chip, the UI, a hidden chat's privacy)."""
    c = _chats.get(str(chat or '').strip())
    if c is None:
        return []
    with c.lock:
        items = list(c.now_q) + list(c.later_q)
    return [{'ticket': it.ticket, 'source': it.source, 'lane': it.lane, 'age': round(it.age(), 1)}
            for it in items]


def position(chat, ticket):
    """1-based place in line for a waiting item (people first, then machines),
    0 when it is not waiting."""
    c = _chats.get(str(chat or '').strip())
    if c is None:
        return 0
    with c.lock:
        items = list(c.now_q) + list(c.later_q)
    for i, it in enumerate(items):
        if it.ticket == ticket:
            return i + 1
    return 0


def depth(chat):
    c = _chats.get(str(chat or '').strip())
    if c is None:
        return 0
    with c.lock:
        return len(c.now_q) + len(c.later_q)


def kick(chat):
    """The chat may be free: look again now. Called when a stream ends."""
    c = _chats.get(str(chat or '').strip())
    if c is not None:
        c.wake.set()


def pending_counts(chat):
    """(questions, reports) waiting - for the trailing line of a drained turn."""
    c = _chats.get(str(chat or '').strip())
    if c is None:
        return 0, 0
    with c.lock:
        items = list(c.now_q) + list(c.later_q)
    q = sum(1 for it in items if 'asks' in (it.header or ''))
    return q, len(items) - q


# --- inside -------------------------------------------------------------------

def _chat(name):
    with _lock:
        c = _chats.get(name)
        if c is None:
            c = _chats[name] = _Chat(name)
        return c


def _system():
    try:
        from core.api_fastapi import get_system
        return get_system()
    except Exception:
        return None


def _refusal(chat):
    """Why `chat` cannot take an item right now, or ''. Missing and sealed
    chats refuse here; the sealed-while-active corner is closed by run_turn's
    own reachability check before it publishes anything (so nothing leaks as
    a turn), and _run drops the item on that refusal."""
    system = _system()
    if system is None:
        return ''                                  # before boot: the drainer holds items until cadence is up
    try:
        sm = system.llm_chat.session_manager
        if sm.is_chat_hidden(chat) is True:        # only an explicit True hides
            return f"The chat '{chat}' is sealed."
        if sm.read_chat_settings(chat) is None:
            return f"There is no chat named '{chat}'."
    except Exception as e:
        logger.debug(f"[INBOX] refusal check for '{chat}' failed: {e}")
    return ''


def _refuse_self_wait(chat):
    """A tool inside her live turn on `chat` must not wait on `chat`: she would
    wait on herself until the timeout. Today that call failed in 20 s; this
    says so at once."""
    try:
        from core.chat.function_manager import tool_context
        inside = (tool_context.get() or {}).get('chat')
    except Exception:
        inside = None
    if inside and str(inside) == str(chat or '').strip():
        raise InboxRefused(f"This is her own turn on '{chat}': answer in your message instead of waiting on it.")


def _changed(c):
    try:
        from core.event_bus import publish
        with c.lock:
            n = len(c.now_q) + len(c.later_q)
        publish('inbox_changed', {'chat': c.name, 'depth': n}, ephemeral=True)
    except Exception as e:
        logger.debug(f"[INBOX] inbox_changed not published: {e}")


def _dropped(it, why):
    if not it.reply.done():
        it.reply.set_exception(RuntimeError(f"dropped: {why}"))
    if it.on_drop:
        try:
            it.on_drop(why)
        except Exception as e:
            logger.warning(f"[INBOX] on_drop for {it.source} failed: {e}")
    logger.info(f"[INBOX] '{it.source}' item {it.ticket} dropped: {why}")


def _idle_hint(c):
    system = _system()
    if system is None:
        return True                                # no counter to ask: let begin_stream judge
    try:
        return system.llm_chat.session_manager.chat_idle_event(c.name).is_set()
    except Exception:
        return True


def _cadence_ready():
    try:
        from core import cadence
        return cadence._system is not None
    except Exception:
        return False


def _pick(c):
    """Under c.lock: the next thing to run - an Item, a list of Items of one
    kind, or None. A person first; then a machine that waits, alone; then
    whatever folds. Folding is same-kind only and contiguous at the head of
    the lane: three typed turns ride one turn; a satellite question between
    them keeps its place."""
    if c.now_q:
        head = c.now_q.popleft()
        if head.fold_key and head.run_folded is not None:
            batch = [head]
            while c.now_q and c.now_q[0].fold_key == head.fold_key and c.now_q[0].run_folded is not None:
                batch.append(c.now_q.popleft())
            return batch if len(batch) > 1 else head
        return head
    if not c.later_q:
        return None
    head = c.later_q[0]
    if not head.foldable:
        return c.later_q.popleft()
    batch = [c.later_q.popleft()]
    while c.later_q and c.later_q[0].foldable and c.later_q[0].fold_key == head.fold_key:
        batch.append(c.later_q.popleft())
    return batch


def _more_foldable(c, batch):
    """Under c.lock: take same-kind foldable items that arrived during the window."""
    key = batch[0].fold_key
    while c.later_q and c.later_q[0].foldable and c.later_q[0].fold_key == key:
        batch.append(c.later_q.popleft())


def _push_back(c, picked):
    with c.lock:
        items = picked if isinstance(picked, list) else [picked]
        q = c.now_q if items[0].lane == 'now' else c.later_q
        for it in reversed(items):
            q.appendleft(it)


def _expire(c):
    with c.lock:
        stale = []
        for q in (c.now_q, c.later_q):
            for it in list(q):
                if it.ttl is not None and it.age() > it.ttl:
                    q.remove(it)
                    stale.append(it)
    for it in stale:
        _dropped(it, f'stale after {it.ttl:.0f}s')
    if stale:
        _changed(c)


def _drain(c):
    """The one runner for a chat. Lives while anything waits, exits after
    DRAINER_IDLE_EXIT_S of nothing; put() starts it again."""
    backoff = BACKOFF_MIN
    logger.debug(f"[INBOX] drainer up for '{c.name}'")
    while True:
        c.wake.wait(timeout=SWEEP_S)
        c.wake.clear()
        _expire(c)
        with c.lock:
            empty = not c.now_q and not c.later_q
            if empty:
                if c.idle_since is None:
                    c.idle_since = time.monotonic()
                elif time.monotonic() - c.idle_since > DRAINER_IDLE_EXIT_S:
                    c.alive = False
                    logger.debug(f"[INBOX] drainer down for '{c.name}' (idle)")
                    return
                continue
            c.idle_since = None
        if not _idle_hint(c):
            continue                                # her message is still going (a hint, not the gate)
        with c.lock:
            picked = _pick(c)
        if picked is None:
            continue
        needs_organ = (picked[0] if isinstance(picked, list) else picked).run is None
        if needs_organ and not _cadence_ready():
            _push_back(c, picked)                   # a text item before the organ is up: hold it
            continue
        if isinstance(picked, list) and picked[0].lane == 'later':
            if picked[0].age() < COALESCE_S:
                time.sleep(max(0.0, COALESCE_S - picked[0].age()))
            with c.lock:
                _more_foldable(c, picked)
                if c.now_q:                         # a person arrived during the window: they go first
                    for it in reversed(picked):
                        c.later_q.appendleft(it)
                    picked = _pick(c)
        why = _refusal(c.name)
        if why:
            for it in (picked if isinstance(picked, list) else [picked]):
                _dropped(it, why)
            _changed(c)
            continue
        try:
            _run(c, picked)
            backoff = BACKOFF_MIN
        except _Busy:
            _push_back(c, picked)                   # a typed turn won the race
            time.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)
            c.wake.set()
        _changed(c)


class _Busy(Exception):
    pass


def _run(c, picked):
    from core.chat.chat import ChatBusy
    items = picked if isinstance(picked, list) else [picked]
    first = items[0]
    for it in items:
        if it.on_start:
            try:
                it.on_start()
            except Exception as e:
                logger.warning(f"[INBOX] on_start for {it.source} failed: {e}")
    try:
        if first.run_folded is not None and len(items) > 1:
            result = first.ctx.run(first.run_folded, items[1:])
        elif first.run is not None:
            result = first.ctx.run(first.run)
        else:
            text = _compose(c, items)
            # a folded turn speaks on the speakers only when every member asked to
            speak = first.speak if len(items) == 1 else (
                'speakers' if all(it.speak == 'speakers' for it in items) else None)
            result = first.ctx.run(_run_turn, c.name, text, first, speak)
    except ChatBusy:
        raise _Busy()
    except Exception as e:
        unreachable = any(s in str(e).lower() for s in UNREACHABLE)
        for it in items:
            if unreachable:
                _dropped(it, str(e))
            elif not it.reply.done():
                it.reply.set_exception(e)
        if not unreachable:
            logger.warning(f"[INBOX] '{c.name}': turn for {first.source} failed: {e}")
        return
    for it in items:
        if not it.reply.done():
            it.reply.set_result(result)
    logger.info(f"[INBOX] '{c.name}': ran {len(items)} item(s) from {', '.join(sorted({it.source for it in items}))}")


def _compose(c, items):
    parts = []
    for it in items:
        parts.append((it.header + '\n' if it.header else '') + it.text)
    text = '\n\n'.join(parts)
    if items[0].lane == 'later':                   # a machine turn says what else waits; a person's words stay theirs
        q, r = pending_counts(c.name)
        waiting = q + r
        if waiting:
            text += f"\n\n[{waiting} more waiting in the inbox]"
    return text


def _run_turn(chat, text, first, speak):
    from core import cadence
    return cadence.run_turn(chat, text, images=first.images, speak=speak,
                            source=first.source, on_event=first.on_event,
                            stream_speech=first.stream_speech)
