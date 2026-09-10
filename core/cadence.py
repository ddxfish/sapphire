"""core/cadence.py — the cadence organ (Game Room F3, 2026-09-09).

Her UNPROMPTED turns: on a random clock between a min and max gap (watching
you play Doom, a movie), or on game events (a wave cleared), each one a real
turn on the chat it's armed for — the same door the wake word and the phone
use (begin_stream by name + chat_stream, VOICE_TURN events so an open room
streams it live, Stop works, other tabs mirror). Turn-based games never arm.

One organ, one ticker thread, per-chat records. Rules:
  - a record is ARMED by a room (or a plugin) with a TTL and kept alive by
    its keepalive; the tab dying = the record expiring = silence
  - never two turns on one chat: her own live stream on that chat = skip,
    and the gap STRETCHES toward max after repeated skips
  - frames come from the perception inbox at fire time (latest wins) and
    reach the model for that turn only (`images_ephemeral`)
  - `speak`: 'browser' rides the VOICE_TURN_END payload (the room's tab
    plays it), 'speakers' speaks the blob on the server after the turn

This is also the seat for heartbeats / Discord proactive / her pinging Krem
later — nothing in here knows what a game is.
"""
import logging
import random
import re
import threading
import time
import uuid

from core.event_bus import publish, Events
from core import perception

logger = logging.getLogger(__name__)

TICK_S = 1.0
DEFAULT_TTL = 90.0
MODES = ('turn', 'event', 'timer')
# A thinking model that never leaves its <think> block: past this many chars
# of unfinished thinking the turn is cancelled and the pair dropped — an
# unattended turn has no one to wait for it (Krem's 8K-token think loop
# that then read the inside of the tag aloud, 2026-09-09).
THINK_BUDGET_CHARS = 6000
_THINK_OPEN = re.compile(r'<(?:seed:)?think>')
_THINK_BLOCK = re.compile(r'<(?:seed:)?think>.*?</(?:seed:think|seed:cot_budget_reflect|think)>\s*', re.DOTALL)
_THINK_TAIL = re.compile(r'<(?:seed:)?think>.*$', re.DOTALL)
_THINK_CLOSE_ORPHAN = re.compile(r'^.*?</(?:seed:think|seed:cot_budget_reflect|think)>\s*', re.DOTALL)

_lock = threading.RLock()
_records = {}                   # chat -> dict
_system = None
_thread = None
_stop = threading.Event()


def _now():
    return time.time()


def _gap(rec, stretch=False):
    lo, hi = float(rec['min_s']), float(rec['max_s'])
    if hi < lo:
        hi = lo
    g = random.uniform(lo, hi)
    if stretch and rec['skips']:
        g = min(hi, g * (1 + 0.5 * rec['skips']))
    return g


def visible_text(text):
    """What she actually said: think blocks gone (closed, unclosed, or an
    orphaned close from a stream that opened before we looked)."""
    t = str(text or '')
    t = _THINK_BLOCK.sub('', t)
    t = _THINK_CLOSE_ORPHAN.sub('', t)
    t = _THINK_TAIL.sub('', t)
    return t.strip()


def _unfinished_thinking(buf):
    """Chars of thinking still open at the end of `buf` (0 = none open)."""
    m = None
    for m in _THINK_OPEN.finditer(buf):
        pass
    if m is None:
        return 0
    tail = buf[m.end():]
    return 0 if re.search(r'</(?:seed:think|seed:cot_budget_reflect|think)>', tail) else len(tail)


def _drop_last_turn(chat, reason):
    """A no-answer turn leaves no trace: the cue row + her think-only row go.
    Only when the chat is the live one (by-name row surgery is later);
    otherwise the pair stays and the reason is logged."""
    try:
        sm = _system.llm_chat.session_manager
        if sm.get_active_chat_name() == chat:
            ok = sm.remove_last_messages(2)
            logger.info(f"[CADENCE] '{chat}': {reason} — pair dropped ({'ok' if ok else 'nothing to drop'})")
            return bool(ok)
        logger.info(f"[CADENCE] '{chat}': {reason} — pair left in place (chat not live)")
    except Exception as e:
        logger.warning(f"[CADENCE] '{chat}': drop after {reason} failed: {e}")
    return False


def default_prompt(percept):
    """The cue when the arming party gave none."""
    lines = []
    if percept and percept.get('text'):
        lines.append(percept['text'])
    lines.append("(Your turn — nobody spoke. You're watching. Say what you'd say out loud right now, briefly; a word or two is fine.)")
    return '\n'.join(lines)


def arm(chat, *, mode='timer', min_s=60, max_s=180, paused=False, send_frames=False,
        frames_per_tick=6, every=1, ttl=DEFAULT_TTL, owner=None, prompt=None, speak=None, title=None):
    """(Re)arm her unprompted turns on `chat`. Idempotent: an armed record
    keeps its clock (and its poke count) unless the range changed; the TTL
    always extends. Mode 'turn' disarms. `every` = event mode fires on every
    Nth poke (a wave game at every 3rd wave). Returns status()."""
    if not chat:
        return None
    mode = mode if mode in MODES else 'timer'
    if mode == 'turn':
        disarm(chat)
        return status(chat)
    with _lock:
        rec = _records.get(chat)
        fresh = rec is None
        if fresh:
            rec = _records[chat] = {
                'chat': chat, 'last_at': None, 'skips': 0, 'pending': None,
                'running': False, 'next_at': None, 'fired': 0, 'pokes': 0,
            }
        range_changed = (not fresh) and (rec.get('min_s') != min_s or rec.get('max_s') != max_s or rec.get('mode') != mode)
        rec.update({
            'mode': mode, 'min_s': max(1.0, float(min_s)), 'max_s': max(1.0, float(max_s)),
            'paused': bool(paused), 'send_frames': bool(send_frames),
            'frames_per_tick': int(frames_per_tick or 1), 'every': max(1, int(every or 1)),
            'ttl_until': _now() + float(ttl),
            'owner': owner, 'prompt': prompt, 'speak': speak, 'title': title,
        })
        if mode == 'timer' and (fresh or range_changed or rec['next_at'] is None):
            rec['next_at'] = _now() + _gap(rec)
        if mode == 'event':
            rec['next_at'] = None
    logger.info(f"[CADENCE] armed '{chat}': {mode} {min_s}-{max_s}s"
                f"{' paused' if paused else ''}{' frames' if send_frames else ''} ({owner})")
    return status(chat)


def disarm(chat, owner=None):
    with _lock:
        rec = _records.get(chat)
        if not rec:
            return False
        if owner and rec.get('owner') and rec['owner'] != owner:
            return False
        del _records[chat]
    perception.clear(chat)
    logger.info(f"[CADENCE] disarmed '{chat}'")
    return True


def pause(chat, paused=True):
    with _lock:
        rec = _records.get(chat)
        if not rec:
            return None
        rec['paused'] = bool(paused)
        if not paused and rec['mode'] == 'timer' and rec['next_at'] is not None and rec['next_at'] < _now():
            rec['next_at'] = _now() + _gap(rec)     # resume = a fresh gap, not an instant turn
    return status(chat)


def poke(chat, note=None, force=False):
    """An event: 'a moment happened'. Event mode fires once the min gap has
    passed since her last turn — on every Nth poke when armed with `every`
    (the count runs from the last poke that became her turn); `force` marks
    a terminal moment (the castle fell) that always comes through. Timer
    mode pulls the next turn forward to that same floor."""
    with _lock:
        rec = _records.get(chat)
        if not rec:
            return None
        if rec['mode'] == 'event' and not force and (rec['pokes'] + 1) % rec['every']:
            rec['pokes'] += 1                 # counted, not yet her turn
            return _status_locked(rec)
        rec['pokes'] = 0
        rec['pending'] = str(note or '')[:400] or True
        if rec['mode'] == 'timer':
            floor = (rec['last_at'] or 0) + rec['min_s']
            rec['next_at'] = min(rec['next_at'] or floor, max(floor, _now()))
    return status(chat)


def status(chat):
    with _lock:
        rec = _records.get(chat)
        if not rec:
            return {'armed': False, 'chat': chat}
        return _status_locked(rec)


def _status_locked(rec):
    chat, nxt = rec['chat'], rec['next_at']
    return {
            'armed': True, 'chat': chat, 'mode': rec['mode'], 'paused': rec['paused'],
            'min_s': rec['min_s'], 'max_s': rec['max_s'], 'send_frames': rec['send_frames'],
            'frames_per_tick': rec['frames_per_tick'], 'every': rec['every'], 'pokes': rec['pokes'],
            'next_in': (max(0.0, nxt - _now()) if (nxt and not rec['paused']) else None),
            'last_at': rec['last_at'], 'skips': rec['skips'], 'running': rec['running'],
            'pending': bool(rec['pending']), 'fired': rec['fired'],
            'ttl_in': max(0.0, rec['ttl_until'] - _now()),
        }


def armed():
    with _lock:
        return sorted(_records)


# ── the turn ────────────────────────────────────────────────────────────────

def run_turn(chat, text, images=None, speak=None, source='cadence'):
    """One unprompted turn on `chat` through THE engine, live to the wire.
    Raises ChatBusy when the chat has a turn in flight. Returns her text."""
    system = _system
    if system is None:
        raise RuntimeError('cadence organ not started')
    llm = system.llm_chat
    sm = llm.session_manager
    stream, sid, chat_name = llm.begin_stream(chat, exclusive=True)
    stream.suppress_tts = True            # the pump stays inert; the lane below speaks
    stream.images_ephemeral = True        # frames reach the model this turn only
    try:
        active = sm.get_active_chat_name()
    except Exception:
        active = None
    foreign = bool(chat and chat != active)
    mid = uuid.uuid4().hex
    publish(Events.VOICE_TURN_START, {"message_id": mid, "user_text": text,
                                      "chat": chat, "foreign": foreign, "source": source})
    parts, cancelled, errored, overthought = [], False, None, False
    thinking_chars = 0             # provider-side thinking events (Claude-style)
    try:
        for ev in stream.chat_stream(text, images=images or None):
            if not isinstance(ev, dict):
                continue
            et = ev.get('type')
            if et == 'content':
                parts.append(ev.get('text') or '')
                publish(Events.VOICE_TURN_CHUNK, {"message_id": mid, "text": ev.get('text', ''),
                                                  "chat": chat, "foreign": foreign})
            elif et == 'thinking':
                thinking_chars += len(ev.get('text') or '')
            elif et == 'final':
                cancelled = bool(ev.get('cancelled'))
                if ev.get('text'):
                    parts = [ev['text']]
            elif et == 'error':
                errored = ev.get('text') or 'stream error'
            # the thinking budget: in-content <think> that never closes, or
            # provider thinking with no content yet, past the budget = cancel
            if not overthought and not cancelled:
                buf = ''.join(parts)
                if _unfinished_thinking(buf) > THINK_BUDGET_CHARS or \
                        (thinking_chars > THINK_BUDGET_CHARS and not visible_text(buf)):
                    overthought = True
                    stream.cancel_flag = True
                    logger.warning(f"[CADENCE] '{chat}': thinking past {THINK_BUDGET_CHARS} chars with no answer — cancelled")
    finally:
        llm.end_stream(sid, chat_name)
    final = visible_text(''.join(parts)) if not cancelled or overthought else ''
    if overthought or (not final and not errored):
        # no answer (a think loop, an empty reply): nothing to show, speak, or keep
        final = ''
        _drop_last_turn(chat, 'thinking never finished' if overthought else 'empty answer')
    publish(Events.VOICE_TURN_END, {"message_id": mid, "chat": chat, "foreign": foreign,
                                    "text": final,
                                    "speak": (speak if (final and not errored) else None),
                                    "source": source, "dropped": bool(overthought or not final)})
    if errored:
        raise RuntimeError(errored)
    if speak == 'speakers' and final:
        try:
            system.tts.speak(final)
        except Exception as e:
            logger.warning(f"[CADENCE] speakers lane failed: {e}")
    return final


def fire_once(chat, text, images=None, speak=None, source='cadence'):
    """A single unprompted turn now (a session-end summary). Raises ChatBusy
    if the chat is mid-turn."""
    return run_turn(chat, text, images=images, speak=speak, source=source)


def _fire(rec):
    chat = rec['chat']
    from core.chat.chat import ChatBusy
    try:
        sm = _system.llm_chat.session_manager
        if sm.is_streaming(chat):
            raise ChatBusy(chat)
        percept = perception.take(chat)
        prompt = rec.get('prompt')
        if callable(prompt):
            text = prompt(percept, rec)
        else:
            text = default_prompt(percept)
        images = (percept or {}).get('frames') if rec['send_frames'] else None
        if images:
            images = images[-max(1, rec['frames_per_tick']):]
        run_turn(chat, text, images=images, speak=rec.get('speak'))
        with _lock:
            if _records.get(chat) is rec:
                rec['last_at'] = _now()
                rec['skips'] = 0
                rec['pending'] = None
                rec['fired'] += 1
                rec['next_at'] = (_now() + _gap(rec)) if rec['mode'] == 'timer' else None
    except ChatBusy:
        with _lock:
            if _records.get(chat) is rec:
                rec['skips'] += 1
                rec['next_at'] = _now() + _gap(rec, stretch=True)
        logger.info(f"[CADENCE] '{chat}' busy — skipped ({rec['skips']}), gap stretched")
    except Exception as e:
        logger.warning(f"[CADENCE] turn on '{chat}' failed: {e}")
        with _lock:
            if _records.get(chat) is rec:
                rec['next_at'] = _now() + _gap(rec)
                rec['pending'] = None
    finally:
        with _lock:
            rec['running'] = False


def _due(rec, now):
    if rec['paused'] or rec['running']:
        return False
    since = now - (rec['last_at'] or 0)
    if rec['pending'] and since >= rec['min_s']:
        return True
    return rec['mode'] == 'timer' and rec['next_at'] is not None and now >= rec['next_at']


def _tick():
    now = _now()
    fire = []
    with _lock:
        for chat, rec in list(_records.items()):
            if now > rec['ttl_until']:
                del _records[chat]
                logger.info(f"[CADENCE] '{chat}' expired (no keepalive) — disarmed")
                continue
            if _due(rec, now):
                rec['running'] = True
                fire.append(rec)
    for rec in fire:
        threading.Thread(target=_fire, args=(rec,), daemon=True,
                         name=f"cadence-{rec['chat'][:24]}").start()


def _loop():
    while not _stop.wait(TICK_S):
        try:
            _tick()
        except Exception as e:
            logger.warning(f"[CADENCE] tick failed: {e}")


def start(system):
    global _system, _thread
    _system = system
    _stop.clear()
    if _thread and _thread.is_alive():
        return
    _thread = threading.Thread(target=_loop, daemon=True, name='cadence-organ')
    _thread.start()
    logger.info("[CADENCE] organ started")


def stop():
    _stop.set()
    with _lock:
        _records.clear()
