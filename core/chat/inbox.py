# core/chat/inbox.py - one queue per chat for everything that wants her attention
#
# Six doors can start a turn on a chat (typed, satellite voice, MCP, agents,
# MIDI, cadence); the engine allows one live turn per chat. Before this file
# the losers each had their own retry-then-drop loop. Now a door `put`s an
# Item: if the chat is free it runs at once, if not it waits and runs when her
# current message's WORDS end (all tool rounds, llm_done) - not its voice: a
# streaming-TTS tail is preempted by the next turn, which cuts over as it
# starts (Krem, 2026-10-08). Record: tmp/chat-inbox-plan.md.
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
from concurrent.futures import Future, TimeoutError as _FutureTimeout   # builtin TimeoutError on 3.11+, its own class before
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

COALESCE_S = 3.0              # foldable items that land this close ride one turn
DEPTH_MAX = 20                # per chat; past it put() refuses with a reason
SWEEP_S = 2.0                 # the drainer looks again this often with no signal
DRAINER_IDLE_EXIT_S = 10.0    # an empty inbox lets its thread go
BACKOFF_MIN, BACKOFF_MAX = 0.1, 2.0   # after ChatBusy: a typed turn won the race


_HOLD = 'hold'                # _refusal: not refused, not runnable yet - the item waits (a sealed chat's machine item)


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
    # WHY it was dropped, typed, set before on_drop runs: 'removed' (× / drop()),
    # 'stale' (ttl), 'gone' (chat deleted), 'sealed' (a person's turn on a
    # vault-locked chat), 'privacy' (the ratchet or a door's gate said no),
    # 'unreachable' (run-time), 'restart'. A door's fallback must read it: the
    # twilio door wrote a PRIVACY-dropped report into the chat by hand - the
    # inbox said "this must not run public" and the door did it anyway
    # (privacy scout, 2026-10-07).
    drop_kind: str = 'dropped'
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
    # A door's OWN rule, asked again the moment the item is about to run: '' to
    # go, or why not (the item is dropped with that reason). An MCP ask checked
    # "is this chat private?" at put time only - a chat that turned private
    # while the ask waited ran with the private history and its reply left over
    # the network (privacy scout, 2026-10-07). Sealed/missing chats are the
    # inbox's own check; this is for what only the door knows.
    gate: Optional[Callable[[], str]] = None
    # Privacy rolls downhill (Krem): anything queued while its chat was PRIVATE
    # never runs public. Set by put(); checked the moment the item is about to
    # run. The other direction (public → private) needs nothing: the turn reads
    # the chat's privacy when it runs and goes local. THREE states: True (known
    # private - arms the drop), False (known public), None (the setting could
    # not be read at put - a sqlite hiccup; it must not arm the drop, or a typed
    # turn is thrown away with a false "queued while private"; second scout
    # wave, 2026-10-08).
    private_at_put: Optional[bool] = False

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
    if item.lane not in ('now', 'later'):
        raise InboxRefused(f"lane must be 'now' or 'later', not {item.lane!r}")
    why = _refusal(chat, item.lane)
    if why and why is not _HOLD:
        raise InboxRefused(why)
    if item.run is None and not (item.text or '').strip():
        raise InboxRefused('An item needs text or a run().')
    if item.ctx is None:
        item.ctx = contextvars.copy_context()
    item.private_at_put = _chat_private(chat)
    c = _chat(chat)
    with c.lock:
        # The cap is for machines (the `later` lane): a person's typed turn and
        # a satellite's spoken one are NEVER refused (Krem's ruling) - twenty
        # agent reports in line used to bounce the user's own message with a
        # 409 and lose its images (day-ruiner scout, 2026-10-07).
        if item.lane == 'later' and len(c.later_q) >= DEPTH_MAX:
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


def tell(chat, text, source, header_line='', coalesce=False, **kw):
    """A machine says something and does not wait. Returns the ticket.
    coalesce defaults False (Krem, 2026-10-06): a machine's returns never fold
    with each other - each gets her own reply. Only a person's typed turns fold."""
    return put(chat, Item(text=text, source=source, lane='later', header=header_line,
                          coalesce=coalesce, **kw)).ticket


def ask(chat, text, source, header_line='', timeout=None, **kw):
    """A machine asks and waits for her reply (an MCP ask). Runs alone, in the
    put-time context. Raises InboxRefused from inside this chat's own live turn
    (she would wait on herself)."""
    _refuse_self_wait(chat)
    item = put(chat, Item(text=text, source=source, lane='later', header=header_line,
                          coalesce=False, **kw))
    return _await(chat, item, timeout)


def turn(chat, fn, source='door', lane='now', timeout=None):
    """A door runs its OWN turn when it is this chat's turn: fn() is called on
    the drainer thread under the caller's context and must begin_stream(
    exclusive=True) itself (ChatBusy from inside it means a typed turn won the
    race; the inbox tries again). Blocks the caller until fn returns, and
    returns what fn returned. The wake word and the conversation driver."""
    _refuse_self_wait(chat)
    item = put(chat, Item(run=fn, source=source, lane=lane))
    return _await(chat, item, timeout)


def _await(chat, item, timeout):
    """Wait for the item's reply. A caller that gives up takes its item back
    out of the line: an MCP ask that timed out used to run anyway later - a
    ghost turn she answered into the void, twice if the client retried
    (chaos + race scouts, 2026-10-07). Already running: nothing to take back."""
    try:
        return item.reply.result(timeout)
    except (TimeoutError, _FutureTimeout):
        drop(chat, item.ticket, 'the asker stopped waiting')
        raise


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
    _dropped(gone, why, 'removed')
    _changed(c)
    return True


def drop_chat(chat, why='chat gone', kind='gone'):
    """The chat was deleted: everything waiting on it is dropped."""
    c = _chats.get(str(chat or '').strip())
    if c is None:
        return 0
    with c.lock:
        items = list(c.now_q) + list(c.later_q)
        c.now_q.clear()
        c.later_q.clear()
    for it in items:
        _dropped(it, why, kind)
    if items:
        _changed(c)
    return len(items)


def shutdown(why='Sapphire is restarting'):
    """Every chat's waiting items are dropped WITH their on_drop, so each door
    leaves its own record (twilio writes its transcript by hand, a web ticket
    tells its tab, an MCP waiter hears the exception now instead of timing
    out). Before this the process just died on them - nothing fired, nothing
    logged (day-ruiner scout, 2026-10-07). Returns how many were dropped."""
    with _lock:
        chats = list(_chats.values())
    n = 0
    for c in chats:
        n += drop_chat(c.name, why, 'restart')
    if n:
        logger.info(f"[INBOX] shutdown: {n} waiting item(s) dropped with notice")
    return n


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


def _refusal(chat, lane='now'):
    """Why `chat` cannot take an item right now, or ''. A missing chat refuses
    every lane. A SEALED chat (vault locked) refuses a person's `now` turn -
    they are there, the text goes back in their box - but HOLDS a machine's
    `later` item: an agent's report into a chat that sealed while it ran has
    nowhere else to live (rows are metadata-only; the content store refuses a
    hidden chat), so it waits in memory for the unlock instead of vanishing
    with a log line that said it was kept (chaos scout, 2026-10-07). Krem's
    ruling: machine doors wait, never drop. The sealed-while-active corner is
    closed by run_turn's own reachability check before it publishes anything."""
    system = _system()
    if system is None:
        return ''                                  # before boot: the drainer holds items until cadence is up
    try:
        sm = system.llm_chat.session_manager
        if sm.is_chat_hidden(chat) is True:        # only an explicit True hides
            return f"The chat '{chat}' is sealed." if lane == 'now' else _HOLD
        if sm.read_chat_settings(chat) is None:
            # None is also what a locked database or a sealed-while-active
            # corner reads as; only a chat that is GONE is refused here. A
            # chat that exists but can't be read right now holds its items -
            # run_turn's own reachability check is the judge at run time
            # (day-ruiner scout, 2026-10-07: a backup's lock dropped everything).
            exists = getattr(sm, 'chat_exists', None)
            if exists is None or not exists(chat):
                return f"There is no chat named '{chat}'."
    except Exception as e:
        logger.debug(f"[INBOX] refusal check for '{chat}' failed (holding): {e}")
    return ''


def _chat_private(chat):
    """The chat's privacy right now: True / False / None (could not be read -
    sealed, gone, or a database hiccup). Callers that GATE treat None as
    private (fail closed); the put-time stamp keeps it as "unknown"."""
    system = _system()
    if system is None:
        return False                               # before boot nothing runs anyway
    try:
        s = system.llm_chat.session_manager.read_chat_settings(chat)
    except Exception:
        return None
    if s is None:
        return None
    return bool(s.get('private_chat'))


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
        # a sealed chat's name never rides the bus (every tab hears it)
        system = _system()
        if system is not None:
            try:
                if system.llm_chat.session_manager.is_chat_hidden(c.name) is True:
                    return
            except Exception:
                return
        publish('inbox_changed', {'chat': c.name, 'depth': n}, ephemeral=True)
    except Exception as e:
        logger.debug(f"[INBOX] inbox_changed not published: {e}")


def _dropped(it, why, kind='dropped'):
    it.drop_kind = kind
    if not it.reply.done():
        it.reply.set_exception(RuntimeError(f"dropped: {why}"))
    if it.on_drop:
        try:
            it.on_drop(why)
        except Exception as e:
            logger.warning(f"[INBOX] on_drop for {it.source} failed: {e}")
    logger.info(f"[INBOX] '{it.source}' item {it.ticket} dropped ({kind})")   # the `why` names the chat: the waiter hears it, the log does not


def _idle_hint(c):
    """Would begin_stream(exclusive) take the chat now? Its own rule, asked of
    the stream registry: a stream past llm_done (an audio tail) is not live.
    The session counters (chat_idle_event) stay up until the engine's finally,
    after the tail - asking them held the line through her whole reply aloud."""
    system = _system()
    if system is None:
        return True                                # nothing to ask: let begin_stream judge
    try:
        return bool(system.llm_chat.chat_free(c.name))
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
        _dropped(it, f'stale after {it.ttl:.0f}s', 'stale')
    if stale:
        _changed(c)


def _drain(c):
    """The one runner for a chat. Lives while anything waits, exits after
    DRAINER_IDLE_EXIT_S of nothing; put() starts it again. Whatever ends this
    thread - the idle exit or something escaping _run (a BaseException from a
    door's run()) - leaves alive=False behind, so put() can start a new one: a
    dead drainer with alive=True stranded the chat's whole line (race scout)."""
    try:
        _drain_loop(c)
    except BaseException as e:            # noqa: BLE001 - the thread is ending either way
        logger.error(f"[INBOX] a drainer died: {type(e).__name__}: {e}")
        raise
    finally:
        restart = False
        with c.lock:
            # only while this thread is still the chat's drainer: after the
            # idle exit, put() may already have started the next one
            if c.thread is threading.current_thread():
                c.alive = False
                restart = bool(c.now_q or c.later_q)
        if restart:
            _restart(c)


def _restart(c):
    """Items are waiting and their drainer just died: start another."""
    with c.lock:
        if c.alive:
            return
        c.alive = True
        c.thread = threading.Thread(target=_drain, args=(c,), daemon=True, name=f"inbox-{c.name[:24]}")
        c.thread.start()
    c.wake.set()


HINT_MAX_S = 30.0             # the idle hint may hold the line this long; then begin_stream judges


def _drain_loop(c):
    backoff = BACKOFF_MIN
    hinted_since = None
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
            # her message is still going - a hint, not the gate. A hint that
            # never clears (a leaked counter) must not hold the line forever:
            # past HINT_MAX_S we try anyway and let begin_stream judge.
            hinted_since = hinted_since or time.monotonic()
            if time.monotonic() - hinted_since < HINT_MAX_S:
                continue
            logger.warning(f"[INBOX] idle hint stuck for {HINT_MAX_S:.0f}s - trying anyway")
        hinted_since = None
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
        items = picked if isinstance(picked, list) else [picked]
        why = _refusal(c.name, 'later' if all(it.lane == 'later' for it in items) else 'now')
        if why is _HOLD:
            _push_back(c, picked)                   # sealed: a machine's item waits for the unlock
            time.sleep(min(BACKOFF_MAX, 2.0))
            continue
        if why:
            for it in items:
                _dropped(it, why, 'sealed' if 'sealed' in why else 'gone')
            _changed(c)
            continue
        picked = _gated(c, picked)                  # each door's own run-time rule
        if picked is None:
            _changed(c)
            continue
        _changed(c)                                 # the chip counts what WAITS - the picked item runs now
        try:
            _run(c, picked)
            backoff = BACKOFF_MIN
        except _Busy:
            _push_back(c, picked)                   # a typed turn won the race
            time.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)
            c.wake.set()
        _changed(c)
        with c.lock:
            # the lane frees at llm_done, before end_stream's kick (that comes
            # when the tail ends): whatever waits is picked now, not next sweep
            if c.now_q or c.later_q:
                c.wake.set()


class _Busy(Exception):
    pass


def _gated(c, picked):
    """Drop every picked item whose own gate says no, now. Returns what is
    left (None when nothing is). A folded run drops as a whole if any member
    is refused - they would have ridden one turn."""
    items = picked if isinstance(picked, list) else [picked]
    public_now = None
    for it in items:
        why = ''
        if it.private_at_put is True:
            if public_now is None:
                public_now = _chat_private(c.name) is False      # unreadable now = not known public: hold the drop
            if public_now:
                why = (f"queued while '{c.name}' was private; the chat is public now, so it did not run "
                       f"- say it again if you mean it to")
        if not why and it.gate is not None:
            try:
                why = it.gate() or ''
            except Exception as e:
                why = f"gate check failed: {e}"
        if why:
            for x in items:
                _dropped(x, why, 'privacy')
            return None
    return picked


def _is_unreadable(e):
    try:
        from core import cadence
        return isinstance(e, cadence.Unreadable)
    except Exception:
        return False


def _is_unreachable(e):
    try:
        from core import cadence
        return isinstance(e, cadence.Unreachable)
    except Exception:
        return False


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
        if _is_unreadable(e):
            raise _Busy()            # the database can't be read right now: hold the item, try again
        # Only the chat being gone/sealed drops the item (a typed check:
        # cadence.Unreachable, or this inbox's own refusal). Anything else is
        # a FAILED turn - its waiter hears the error, the log says so.
        unreachable = isinstance(e, InboxRefused) or _is_unreachable(e)
        for it in items:
            if unreachable:
                _dropped(it, str(e), 'unreachable')
            elif not it.reply.done():
                it.reply.set_exception(e)
        if not unreachable:
            logger.warning(f"[INBOX] turn for {first.source} failed: {type(e).__name__}")
        return
    for it in items:
        if not it.reply.done():
            it.reply.set_result(result)
    logger.info(f"[INBOX] ran {len(items)} item(s) from {', '.join(sorted({it.source for it in items}))}")


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
    # keep_prompt: a text item's row is the record (a report, an MCP message,
    # a call transcript) - it survives an empty or stopped answer
    return cadence.run_turn(chat, text, images=first.images, speak=speak,
                            source=first.source, on_event=first.on_event,
                            stream_speech=first.stream_speech, keep_prompt=True)
