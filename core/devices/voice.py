# core/devices/voice.py - voice through devices (tmp/device-manager-plan.md)
#
# The halves of an Alexa-like satellite:
#   hear()  a device heard its wake word and sends what was said. hear()
#           answers as soon as the words are known. The turn runs on its own
#           thread, and the answer is spoken on the SAME device.
#   say()   speak on a device. The reply lane of cadence.run_turn uses it.
#   cue()   what a device's light should show: thinking, tool, idle, error.
#           Only the device whose turn it is gets them. A device holds one
#           stream open to receive them (GET /api/devices/{id}/events).
#
# Both privacy gates apply: a private chat never sends audio to a cloud
# speech engine, and never sends its reply to a cloud voice engine.
import asyncio
import contextvars
import hmac
import logging
import os
import tempfile
import threading
import time

logger = logging.getLogger(__name__)

MAX_AUDIO = 20 * 1024 * 1024
MAX_WAITING = 3            # questions one device may have waiting or running
BUSY_WAIT = 20             # seconds a question waits for a chat that is mid-turn
MAX_LISTENERS = 4          # light streams one device may hold open
CUE_FRESH = 300            # seconds a cue is still worth showing to a late stream
CUES = ('thinking', 'tool', 'idle', 'error')

_speaking_for = contextvars.ContextVar('device_voice_chat_settings', default=None)
_lock = threading.Lock()
_waiting = {}              # device id -> questions waiting or running
_showing = {}              # device id -> the cue its light should show right now
_listeners = {}            # device id -> [(loop, queue)] of open light streams
_spoke = threading.local() # did the reply lane manage to speak, on this thread


def _system():
    from core.api_fastapi import get_system
    return get_system()


def _engine():
    from core.devices import engine
    return engine


# --- used by drivers -----------------------------------------------------------

def render(text):
    """Her voice as (bytes, content_type), or (None, the reason). Gated by the
    chat that produced the text when say() set one, else the effective chat."""
    tts = getattr(_system(), 'tts', None)
    if tts is None or not hasattr(tts, 'render'):
        return None, "Speech is not available right now."
    return tts.render(text, chat_settings=_speaking_for.get())


def stt_refusal(chat_settings=None):
    """Why audio may not be transcribed for this chat, or ''."""
    from core.voice_privacy import stt_gate_reason
    if chat_settings is None:
        chat_settings = _system().llm_chat.session_manager.get_chat_settings()
    return stt_gate_reason(chat_settings) or ''


def transcribe(audio, suffix='.wav'):
    """(what was said, '') or ('', the problem). Silence is ('', '')."""
    stt = getattr(_system(), 'whisper_client', None)
    if stt is None:
        return '', "Speech recognition is not available right now."
    fd, path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(audio)
        text = stt.transcribe_file(path)
    except Exception as e:
        logger.error(f"[DEVICES] transcribe failed: {e}", exc_info=True)
        return '', f"Speech recognition failed ({type(e).__name__})."
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    return (text or '').strip(), ''


# --- which device, which chat --------------------------------------------------

def _voice_part(row):
    """The part of this device that has a microphone, or None."""
    reg = _engine()._registry()
    for part in row.get('parts', []):
        spec = reg.get_driver(part.get('driver'))
        if spec and 'mic' in spec['capabilities']:
            return part
    return None


def key_ok(device_id, presented):
    """True when `presented` is the key stored for this device's voice."""
    e = _engine()
    row = e.rows().get(str(device_id or '').strip().lower())
    part = _voice_part(row) if row and row.get('enabled', True) else None
    if not part or not presented:
        return False
    try:
        stored = e._part_secrets(row['id'], part['driver']).get('voice_key')
    except Exception:
        return False
    return bool(stored) and hmac.compare_digest(str(presented).encode(), stored.encode())


# --- the light ----------------------------------------------------------------

def _offer(queue, item):
    """On the stream's own loop. A full queue loses its oldest cue."""
    while True:
        try:
            return queue.put_nowait(item)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass


def _send(loop, queue, item):
    try:
        loop.call_soon_threadsafe(_offer, queue, item)
    except RuntimeError:                 # that loop has closed
        pass


def cue(device_id, state, **more):
    """Tell ONE device what its light should show. Never waits, never raises."""
    if state not in CUES:
        return
    payload = dict(more, state=state, src='device', ts=time.time())
    with _lock:
        if state in ('thinking', 'tool'):
            _showing[device_id] = payload
        else:
            _showing.pop(device_id, None)
        streams = list(_listeners.get(device_id, ()))
    for loop, queue in streams:
        _send(loop, queue, payload)


def listen(device_id, loop, queue):
    """A device opened its light stream. Returns the cue it should show right
    now, or None - a stream that reconnects mid-turn is not left behind."""
    with _lock:
        held = _listeners.setdefault(device_id, [])
        while len(held) >= MAX_LISTENERS:
            _send(*held.pop(0), None)    # None ends the oldest stream
        held.append((loop, queue))
        now = _showing.get(device_id)
    return now if now and time.time() - now['ts'] < CUE_FRESH else None


def unlisten(device_id, loop, queue):
    with _lock:
        held = _listeners.get(device_id, [])
        if (loop, queue) in held:
            held.remove((loop, queue))
        if not held:
            _listeners.pop(device_id, None)


def _follow(device_id, event):
    """The light follows the tools of this device's own turn."""
    kind = event.get('type')
    if kind == 'tool_start':
        cue(device_id, 'tool', tool_name=str(event.get('name') or '')[:60])
    elif kind == 'tool_end':
        cue(device_id, 'thinking')


# --- the halves ----------------------------------------------------------------

def say(device_id, text, chat_settings=None):
    """Speak on a device. Returns (what happened, ok)."""
    token = _speaking_for.set(chat_settings)
    try:
        said, ok = _engine().run(device_id, 'speaker', 'say', text, owner=True)
    finally:
        _speaking_for.reset(token)
    _spoke.ok = bool(ok)
    return _engine().text_of(said), ok


def origin_line(row):
    """The line she reads above anything a device heard: which device, where
    it is, and that her answer is said out loud there."""
    def clean(text):
        return ' '.join(str(text or '').replace('[', '(').replace(']', ')').split())
    label, where = clean(row.get('label')), clean(row.get('location'))
    name = f'"{row["id"]}"' + (f" ({label})" if label and label != row['id'] else '')
    return (f"[Voice from device {name}." + (f" Location: {where}." if where else '')
            + " Your reply is spoken aloud there.]")


def _spawn(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True, name='device-turn').start()


def _take_slot(device_id):
    with _lock:
        if _waiting.get(device_id, 0) >= MAX_WAITING:
            return False
        _waiting[device_id] = _waiting.get(device_id, 0) + 1
        return True


def _free_slot(device_id):
    with _lock:
        left = _waiting.get(device_id, 0) - 1
        if left > 0:
            _waiting[device_id] = left
        else:
            _waiting.pop(device_id, None)


def _turn(row, chat, heard):
    """One question from start to finish, on its own thread. Never raises.
    The light ends on idle, or on error when the person got no answer."""
    device_id, failed = row['id'], False
    lane = f"device:{device_id}"
    try:
        from core import cadence
        from core.chat.chat import ChatBusy
        cue(device_id, 'thinking')
        text = f"{origin_line(row)}\n{heard}"
        give_up = time.monotonic() + BUSY_WAIT
        _spoke.ok = None
        while True:
            try:
                reply = cadence.run_turn(chat, text, speak=lane, source=lane,
                                         on_event=lambda ev: _follow(device_id, ev))
                break
            except ChatBusy:
                if time.monotonic() >= give_up:
                    logger.warning(f"[DEVICES] {device_id}: chat '{chat}' stayed mid-turn for "
                                   f"{BUSY_WAIT}s, the question was dropped")
                    failed = True
                    return
                time.sleep(1)
        if reply and _spoke.ok is False:
            failed = True                # she answered, and the device could not say it
        logger.info(f"[DEVICES] {device_id}: answered in chat '{chat}', {len(reply or '')} chars"
                    + (', NOT spoken' if failed else ''))
    except Exception as e:
        failed = True
        logger.error(f"[DEVICES] {device_id}: the turn failed: {e}", exc_info=True)
    finally:
        _free_slot(device_id)
        cue(device_id, 'error' if failed else 'idle')


def hear(device_id, audio, suffix='.wav'):
    """A device heard something. Answers as soon as the words are known:
    {'ok', 'heard', 'accepted', 'chat'} or {'ok': False, 'error'}.
    accepted = a turn has started, and its answer will be spoken on the
    device. Never raises."""
    try:
        e = _engine()
        why = e.refusal()
        if why:
            return {'ok': False, 'error': why}
        row = e.rows().get(str(device_id or '').strip().lower())
        if not row or not row.get('enabled', True):
            return {'ok': False, 'error': f"There is no device named '{device_id}', or it is turned off."}
        part = _voice_part(row)
        if not part:
            return {'ok': False, 'error': f"'{row['id']}' has no microphone."}
        if not audio:
            return {'ok': False, 'error': 'No audio arrived.'}
        if len(audio) > MAX_AUDIO:
            return {'ok': False, 'error': 'The audio is too large.'}

        sm = _system().llm_chat.session_manager
        chat = str(part['config'].get('chat') or '').strip() or sm.get_active_chat_name()
        settings = sm.get_settings_for(chat)
        if settings is None:
            return {'ok': False, 'error': f"The chat '{chat}' set for '{row['id']}' does not exist."}
        gate = stt_refusal(settings)
        if gate:
            return {'ok': False, 'error': gate, 'chat': chat}

        heard, problem = transcribe(audio, suffix)
        if problem:
            return {'ok': False, 'error': problem, 'chat': chat}
        if not heard:
            logger.info(f"[DEVICES] {row['id']}: audio arrived, no speech in it")
            return {'ok': True, 'heard': '', 'accepted': False, 'chat': chat}
        logger.info(f"[DEVICES] {row['id']} heard {len(heard)} chars for chat '{chat}'")

        if not _take_slot(row['id']):
            return {'ok': False, 'busy': True, 'heard': heard, 'chat': chat,
                    'error': f"'{row['id']}' already has {MAX_WAITING} questions waiting."}
        try:
            _spawn(_turn, row, chat, heard)
        except Exception:
            _free_slot(row['id'])
            raise
        return {'ok': True, 'heard': heard, 'accepted': True, 'chat': chat}
    except Exception as e:
        logger.error(f"[DEVICES] hear({device_id}) failed: {e}", exc_info=True)
        return {'ok': False, 'error': f"Something went wrong ({type(e).__name__})."}
