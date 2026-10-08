# core/devices/voice.py - voice through devices (tmp/device-manager-plan.md)
#
# The halves of an Alexa-like satellite:
#   hear()  a device heard its wake word and sends what was said. hear()
#           answers as soon as the words are known. The turn runs on its own
#           thread, and the answer is spoken on the SAME device.
#   say()   speak on a device, whole: her `say` action, and the reply lane
#           of cadence.run_turn when a reply could not be spoken as it was
#           made. say_all() is the same on every device that can speak at
#           once (the composite device `all`): rendered once, played on each.
#   halt()  core's half of a stop: her sentences still to be sent to a
#           device are dropped. The driver's `stop` action calls it, then
#           tells the device to cut what is playing.
#   Speech  her reply spoken on a device sentence by sentence, as the turn
#           makes it: the device hears the first sentence while she is still
#           writing the third. The turn feeds it, a worker plays in order.
#   cue()   what a device's light should show: thinking, tool, idle, error.
#           Only the device whose turn it is gets them. A device holds one
#           stream open to receive them (GET /api/devices/{id}/events).
#   typed() a device with a keyboard sends what was typed (a pocket
#           terminal). The turn runs the same way; her reply goes to the
#           device's SCREEN, not its speaker: Reply keeps the text as she
#           writes it and rings the device's stream with a `text` doorbell
#           ({msg, rev, have, done}); the device pulls what it lacks from
#           reply_text(). A doorbell can be dropped, the text never is.
# One wake gets one answer: hear() asks wake.py whether the main app or
# another satellite already holds the room, and drops a repeat of its words.
#
# Both privacy gates apply: a private chat never sends audio to a cloud
# speech engine, and never sends its reply to a cloud voice engine.
#
# A device may state the ONE format it plays: {"type": "audio/wav", "rate":
# 16000, "channels": 1}. wav is always 16 bit PCM. fit() is the only place
# her voice is converted. A device that states nothing gets the audio as the
# voice engine made it.
import asyncio
import base64
import contextvars
import hmac
import io
import logging
import os
import queue
import tempfile
import threading
import time
import uuid

from core.devices import wake

logger = logging.getLogger(__name__)

MAX_AUDIO = 20 * 1024 * 1024
MAX_WAITING = 3            # questions one device may have waiting or running
MAX_LISTENERS = 4          # light streams one device may hold open
RATES = (8000, 48000)      # the sample rates a device may ask for, lowest and highest
QUIET = 0.01               # below this a sample is silence, for the trim
HEAD, TAIL = 0.08, 0.20    # seconds of silence left before and after her words
CUE_FRESH = 300            # seconds a cue is still worth showing to a late stream
CUES = ('thinking', 'tool', 'idle', 'error', 'standdown', 'text')   # standdown: another listener took this wake; text: a doorbell
SPEECH_WAIT = 600          # seconds a turn waits for the last sentence to finish playing
SAY_ALL_WAIT = 180         # seconds say_all waits for the slowest device
TYPED_MAX = 2000           # chars a device may type at once
TEXT_MAX = 12000           # chars of one reply kept for a screen
TEXT_KEEP = 4              # replies kept per device, for a pull that comes late
DOORBELL_GAP = 0.25        # seconds between doorbells while she writes
PULL_MAX = 2000            # chars one pull may ask for

_speaking_for = contextvars.ContextVar('device_voice_chat_settings', default=None)
_lock = threading.Lock()
_waiting = {}              # device id -> questions waiting or running
_showing = {}              # device id -> the cue its light should show right now
_listeners = {}            # device id -> [(loop, queue)] of open light streams
_live = {}                 # device id -> [Speech] with sentences still to play
_replies = {}              # device id -> [Reply] to typed questions, newest last
wake.told = lambda device_id: cue(device_id, 'standdown')   # the main app took a satellite's claim
_spoke = threading.local() # did the reply lane manage to speak, on this thread


def _system():
    from core.api_fastapi import get_system
    return get_system()


def _engine():
    from core.devices import engine
    return engine


# --- the clock -------------------------------------------------------------------

def local_tz():
    """This machine's time zone as the POSIX string a small board's C library
    takes, like EST5EDT,M3.2.0,M11.1.0: the footer of the system zone file.
    Without one, the plain offset, which knows no summer time."""
    try:
        with open('/etc/localtime', 'rb') as f:
            data = f.read()
        if data[:4] == b'TZif' and data[4:5] >= b'2':
            foot = data.rstrip(b'\n').rsplit(b'\n', 1)[-1]
            if 0 < len(foot) < 64 and b'\x00' not in foot:
                return foot.decode('ascii', 'replace')
    except (OSError, ValueError):
        pass
    name = ''.join(c for c in (time.tzname[0] or 'UTC') if c.isalpha())[:5] or 'UTC'
    hours = time.timezone / 3600                      # POSIX counts west as positive
    return f"{name}{int(hours) if hours == int(hours) else hours:g}"


def clock():
    """{'now': seconds since 1970, 'tz': POSIX zone}, for a device that keeps time."""
    return {'now': int(time.time()), 'tz': local_tz()}


# --- used by drivers -----------------------------------------------------------

def render(text):
    """Her voice as (bytes, content_type), or (None, the reason). Gated by the
    chat that produced the text when say() set one, else the effective chat.
    fit() makes it the format a device plays."""
    tts = getattr(_system(), 'tts', None)
    if tts is None or not hasattr(tts, 'render'):
        return None, "Speech is not available right now."
    return tts.render(text, chat_settings=_speaking_for.get())


def wanted(plays):
    """The format a device stated, checked: {'type', 'rate', 'channels'}, or
    None when it stated none that can be made."""
    if not isinstance(plays, dict) or str(plays.get('type') or '').lower() != 'audio/wav':
        return None
    try:
        rate, channels = int(plays.get('rate') or 0), int(plays.get('channels') or 1)
    except (TypeError, ValueError):
        return None
    if not RATES[0] <= rate <= RATES[1] or channels not in (1, 2):
        return None
    return {'type': 'audio/wav', 'rate': rate, 'channels': channels}


def _resample(sound, was, to):
    """One channel at another sample rate. Going down, what the new rate
    cannot carry is filtered out first, or it would come back as noise."""
    import numpy as np
    if was == to or not len(sound):
        return sound
    if to < was:
        edge = 0.46 * to / was                   # a little under half the new rate
        reach = 16 * int(np.ceil(was / to))
        n = np.arange(-reach, reach + 1)
        taps = 2 * edge * np.sinc(2 * edge * n) * np.hamming(len(n))
        sound = np.convolve(sound, taps / taps.sum(), mode='same')
    count = max(1, int(round(len(sound) * to / was)))
    return np.interp(np.arange(count) * (was / to), np.arange(len(sound)), sound)


def _trim(sound, rate):
    """Her words with a breath of silence each side, no more. A sentence
    rendered on its own comes with ~0.3 s of silence before and ~0.4 s after
    (Kokoro, measured 2026-10-03); joined one after another on a device that
    was a hole at every sentence, twice what a whole reply has between them."""
    import numpy as np
    loud = np.flatnonzero(np.abs(sound).max(axis=1) > QUIET)
    if not len(loud):
        return sound
    start = max(0, int(loud[0]) - int(HEAD * rate))
    end = min(len(sound), int(loud[-1]) + 1 + int(TAIL * rate))
    return sound[start:end]


def fit(audio, kind, plays):
    """Audio in the format a device plays: (bytes, content_type), or (None,
    the reason), with the silence at its ends trimmed to a breath. Audio that
    already fits is handed on untouched."""
    import numpy as np
    import soundfile as sf
    want = wanted(plays)
    if want is None:
        return None, "This device stated a sound format I cannot make."
    try:
        with sf.SoundFile(io.BytesIO(audio)) as f:
            fits = (f.format == 'WAV' and f.subtype == 'PCM_16'
                    and f.samplerate == want['rate'] and f.channels == want['channels'])
            if fits:
                return audio, want['type']
            rate = f.samplerate
            sound = _trim(f.read(dtype='float32', always_2d=True), rate)
        one = sound.mean(axis=1)
        one = np.clip(_resample(one, rate, want['rate']), -1.0, 1.0)
        out = io.BytesIO()
        sf.write(out, np.column_stack([one] * want['channels']), want['rate'],
                 format='WAV', subtype='PCM_16')
        return out.getvalue(), want['type']
    except Exception as e:
        logger.error(f"[DEVICES] could not convert {kind} speech: {e}", exc_info=True)
        return None, f"Her voice could not be converted for this device ({type(e).__name__})."


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

def _part_with(row, *names):
    """The part of this device that has one of these capabilities, or None."""
    e = _engine()
    for part in row.get('parts', []):
        caps = e.capabilities(part, e._registry().get_driver(part.get('driver')))
        if any(n in caps for n in names):
            return part
    return None


def _voice_part(row):
    """The part of this device that has a microphone, or None."""
    return _part_with(row, 'mic')


def _talk_part(row):
    """The part that talks to her: a microphone or a keyboard. It holds the
    key the device sends and the chat its words land in."""
    return _part_with(row, 'mic', 'keyboard')


def key_ok(device_id, presented):
    """True when `presented` is the key stored for this device's voice - or
    the one provision() minted for this name, which becomes the stored one
    now (engine.promote): a board proves its new keys by calling in."""
    if not presented:
        return False
    return _key_stored(device_id, presented) or \
        (_engine().promote(device_id, presented) and _key_stored(device_id, presented))


def _key_stored(device_id, presented):
    e = _engine()
    row = e.rows().get(str(device_id or '').strip().lower())
    part = _talk_part(row) if row and row.get('enabled', True) else None
    if not part:
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
        elif state != 'text':            # a doorbell says nothing about the light
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


def _follow(device_id, event, speech=None, reply=None):
    """The light follows the tools of this device's own turn, and each
    sentence of her voice goes to the device as it is made; for a typed
    question each piece of her text goes to its Reply instead."""
    kind = event.get('type')
    if kind == 'tool_start':
        cue(device_id, 'tool', tool_name=str(event.get('name') or '')[:60])
    elif kind == 'tool_end':
        cue(device_id, 'thinking')
    elif kind == 'tts_chunk' and speech is not None:
        speech.feed(event)
    elif kind == 'content' and reply is not None:
        reply.add(event.get('text') or '')


# --- her words, as they are written -------------------------------------------------

class Reply:
    """Her reply to one typed question, as a screen follows it: the text so
    far, a revision that goes up when her final text differs from what
    streamed (a tool round joined, thinking stripped), and a doorbell on the
    device's stream at most every DOORBELL_GAP seconds saying how much there
    is. The device pulls the words with reply_text(); a doorbell lost on a
    full stream is made good by the next one."""

    def __init__(self, device_id):
        self.device_id = device_id
        self.msg = uuid.uuid4().hex[:12]
        self.rev = 0
        self.text = ''                   # what a screen may show: the stream with its thinking taken out
        self.done = False
        self._raw = ''
        self._rang = 0.0
        with _lock:
            held = _replies.setdefault(device_id, [])
            held.append(self)
            del held[:-TEXT_KEEP]

    def add(self, text):
        """A piece of her stream. A model that thinks inside its text
        (<think>...</think>) streams that too; only what she is saying
        reaches the screen (Krem saw the tags on the glass, 2026-10-06)."""
        if not text or self.done:
            return
        from core import think
        self._raw = (self._raw + text)[:TEXT_MAX * 4]
        shown = think.strip(self._raw)[:TEXT_MAX]
        if shown == self.text:
            return
        if not shown.startswith(self.text):      # the visible text changed under the device: a new revision
            self.rev += 1
        self.text = shown
        self.ring()

    def ring(self, force=False):
        now = time.monotonic()
        if not force and now - self._rang < DOORBELL_GAP:
            return
        self._rang = now
        cue(self.device_id, 'text', msg=self.msg, rev=self.rev, have=len(self.text), done=self.done)

    def finish(self, final):
        """Her text as the turn kept it. More of the same is just more to
        pull; a different text (tool rounds joined, a prefill) is a new
        revision, pulled from the start."""
        final = str(final or '')[:TEXT_MAX]
        if final and final != self.text:
            if not final.startswith(self.text):
                self.rev += 1
            self.text = final
        self.done = True
        self.ring(force=True)

    def slice(self, start, limit):
        start = max(0, min(int(start), len(self.text)))
        limit = max(1, min(int(limit), PULL_MAX))
        return {'msg': self.msg, 'rev': self.rev, 'from': start, 'text': self.text[start:start + limit],
                'have': len(self.text), 'done': self.done}


def reply_text(device_id, msg='', start=0, limit=PULL_MAX):
    """A piece of her reply for a device's screen: the message named, or the
    latest when none is (a device that just connected). A message that is no
    longer kept answers with the latest, whose id the device will not match."""
    with _lock:
        held = list(_replies.get(str(device_id or '').strip().lower(), ()))
    if not held:
        return {'msg': '', 'rev': 0, 'from': 0, 'text': '', 'have': 0, 'done': True}
    rec = next((r for r in held if r.msg == msg), None) if msg else held[-1]
    return (rec or held[-1]).slice(start if rec else 0, limit)


# --- her voice, as it is made ----------------------------------------------------

class Speech:
    """Her reply spoken on one device sentence by sentence. The turn's TTS
    pump renders each sentence and feed() takes it; one worker plays them in
    order, each play returning when the device has finished it, so a
    sentence that is ready early waits its turn. feed() never blocks the
    turn. wait() at the end does, until the last sentence has played.
    The device's own stop (the Pi's button) ends the rest of the reply."""

    def __init__(self, device_id):
        self.device_id = device_id
        self.door = _engine().speaker(device_id)     # None: this device cannot take sound as it is made
        self.queue = queue.Queue()
        self.spoken = 0                              # sentences the device has played
        self.stopped = False                         # the device stopped it, or a play failed
        self.problem = None
        self._thread = None

    @property
    def ready(self):
        return self.door is not None

    def feed(self, event):
        if not self.ready or self.stopped:
            return
        try:
            audio = base64.b64decode(event.get('audio_b64') or '')
        except (TypeError, ValueError):
            return
        if not audio:
            return
        self.queue.put((audio, str(event.get('content_type') or 'audio/ogg')))
        if self._thread is None:
            with _lock:
                _live.setdefault(self.device_id, []).append(self)
            self._thread = threading.Thread(target=self._play_all, daemon=True,
                                            name=f'device-speech-{self.device_id}')
            self._thread.start()

    def finish(self):
        """No more sentences are coming."""
        self.queue.put(None)

    def wait(self, timeout=SPEECH_WAIT):
        """Until the device has played everything it was given. True when it did."""
        if self._thread is None:
            return True
        self._thread.join(timeout)
        if self._thread.is_alive():
            logger.warning(f"[DEVICES] {self.device_id}: still speaking after {timeout}s, not waited for")
            return False
        return True

    def _forget(self):
        with _lock:
            held = _live.get(self.device_id, [])
            if self in held:
                held.remove(self)
            if not held:
                _live.pop(self.device_id, None)

    def _play_all(self):
        mod, device, config, secrets = self.door
        while True:
            item = self.queue.get()
            if item is None:
                return self._forget()
            if self.stopped:
                continue                             # drain what is left, play nothing
            audio, kind = item
            t0 = time.monotonic()
            try:
                said = mod.play(audio, kind, device, config, secrets)
            except Exception as e:
                self.problem, self.stopped = str(e), True
                logger.warning(f"[DEVICES] {self.device_id}: a sentence could not be played: {e}")
                continue
            self.spoken += 1
            took = time.monotonic() - t0
            sound = said.get('seconds') if isinstance(said, dict) else None
            logger.info(f"[DEVICES] {self.device_id}: sentence {self.spoken}"
                        + (f", {sound}s of sound" if sound is not None else '') + f", taken in {took:.2f}s")
            if isinstance(said, dict) and said.get('stopped'):
                self.stopped = True
                logger.info(f"[DEVICES] {self.device_id}: stopped at the device after {self.spoken} sentence(s)")


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


def halt(device_id):
    """Her sentences still to be sent to this device are dropped. True when
    a reply was being spoken there. What the device is playing this moment
    is the driver's to cut."""
    with _lock:
        live = list(_live.get(device_id, ()))
    for speech in live:
        speech.stopped = True
    return bool(live)


def say_all(text):
    """Say it on every device that can speak, at once: her voice rendered
    once (gated like render), fitted and played on each device on its own
    thread. A device whose button stops it stops the others. Returns (one
    line per device, ok). say('all', ...) comes here through the engine."""
    e = _engine()
    text = str(text or '').strip()
    if not text:
        return "say: the value is what to say. Example: Dinner is ready", True
    doors = e.speakers()
    if not doors:
        return "No device can speak right now.", False
    audio, kind = render(text)
    if audio is None:
        return f"Nothing was said: {kind}", False
    said, first = {}, []

    def one(door):
        mod, device, config, secrets = door
        try:
            out = mod.play(audio, kind, device, config, secrets)
        except Exception as ex:
            said[device['id']] = f"not said: {ex}"
            return
        if not (isinstance(out, dict) and out.get('stopped')):
            said[device['id']] = 'said'
            return
        with _lock:
            lead = not first
            first.append(device['id'])
        if lead:                                     # the one that was stopped first stops the rest
            said[device['id']] = 'stopped there, so stopped everywhere'
            stop_all(but=device['id'])
        else:
            said[device['id']] = 'stopped'
    threads = [threading.Thread(target=one, args=(d,), daemon=True, name=f"say-all-{d[1]['id']}") for d in doors]
    for t in threads:
        t.start()
    end = time.monotonic() + SAY_ALL_WAIT
    for t in threads:
        t.join(max(0.0, end - time.monotonic()))
    lines = [f"{d[1]['id']}: {said.get(d[1]['id'], 'still playing')}" for d in doors]
    ok = any(v in ('said', 'stopped', 'stopped there, so stopped everywhere') for v in said.values())
    short = text if len(text) <= 80 else text[:77] + '...'
    return f'Said on {len(doors)} device(s): "{short}"\n' + '\n'.join(lines), ok


def stop_all(but=None):
    """Stop her voice on every device whose speaker has a `stop` action,
    each on its own thread. Returns (one line per device, ok)."""
    e = _engine()
    todo = []
    for device_id, row in e.rows().items():
        if device_id == but or not row.get('enabled', True):
            continue
        cap = next((c for c in e.describe(row) if c['capability'] == 'speaker'), None)
        if cap and not cap['error'] and 'stop' in cap['actions']:
            todo.append(device_id)
    if not todo:
        return "No device has a stop.", False
    said = {}

    def one(device_id):
        told, ok = e.run(device_id, 'speaker', 'stop', owner=True)
        said[device_id] = e.text_of(told) if ok else f"not stopped: {e.text_of(told)}"
    threads = [threading.Thread(target=one, args=(d,), daemon=True, name=f'stop-all-{d}') for d in todo]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    return '\n'.join(f"{d}: {said.get(d, 'no answer yet')}" for d in todo), any(not v.startswith('not stopped') for v in said.values())


def origin_line(row):
    """The line she reads above anything a device heard: which device, where
    it is, and that her answer is said out loud there."""
    def clean(text):
        return ' '.join(str(text or '').replace('[', '(').replace(']', ')').split())
    label, where = clean(row.get('label')), clean(row.get('location'))
    name = f'"{row["id"]}"' + (f" ({label})" if label and label != row['id'] else '')
    return (f"[Voice from device {name}." + (f" Location: {where}." if where else '')
            + " Your reply is spoken aloud there.]")


def typed_line(row):
    """The same line for words typed on a device's keyboard: her reply is
    read on its small screen, not heard."""
    spoken = origin_line(row)
    return spoken.replace('[Voice from device', '[Typed on device', 1) \
                 .replace('Your reply is spoken aloud there.', 'Your reply is shown on its small screen.', 1)


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


def _turn(row, chat, heard, reply=None):
    """One question from start to finish, on its own thread. Never raises.
    Her reply is spoken on the device as it is made (Speech); a device whose
    driver cannot take sound that way hears it whole at the end, through
    the reply lane. With a Reply (a typed question) nothing is spoken: her
    text goes to the device's screen as she writes it. The light ends on
    idle, or on error when the person got no answer."""
    device_id, failed, speech, box = row['id'], False, None, {}
    lane = f"device:{device_id}"
    try:
        from core import cadence
        from core.chat import inbox
        cue(device_id, 'thinking')
        text = f"{typed_line(row) if reply else origin_line(row)}\n{heard}"

        def _body():
            # Runs when it is the chat's turn (core/chat/inbox.py, 'now' lane:
            # a person is waiting - it never drops, Krem 2026-10-06; before,
            # a chat busy for BUSY_WAIT dropped the question). Speech is made
            # HERE so its door is fresh after a wait, and _spoke - a
            # threading.local set by voice.say inside run_turn - is read on
            # this same thread before returning.
            _spoke.ok = None
            sp = Speech(device_id) if reply is None else None
            box['speech'] = sp
            try:
                return cadence.run_turn(chat, text, speak=lane if sp else None, source=lane,
                                        on_event=lambda ev: _follow(device_id, ev, sp, reply),
                                        stream_speech=bool(sp and sp.ready))
            finally:
                box['spoke_ok'] = getattr(_spoke, 'ok', None)

        said = inbox.turn(chat, _body, source=lane, lane='now')
        speech = box.get('speech')
        if reply is not None:
            reply.finish(said)
            failed = not said
            logger.info(f"[DEVICES] {device_id}: answered in chat '{chat}' on its screen, "
                        f"{len(said or '')} chars, rev {reply.rev}")
            return
        speech.finish()
        speech.wait()
        if said and (box.get('spoke_ok') is False or (speech.problem and not speech.spoken)):
            failed = True                # she answered, and the device could not say it
        logger.info(f"[DEVICES] {device_id}: answered in chat '{chat}', {len(said or '')} chars"
                    + (f', {speech.spoken} sentence(s) as made' if speech.spoken else '')
                    + (', stopped at the device' if speech.stopped and speech.spoken else '')
                    + (', NOT spoken' if failed else ''))
    except Exception as e:
        failed = True
        speech = speech or box.get('speech')     # the body made it before the turn broke off
        if speech is not None:
            speech.stopped = True        # a reply that broke off is not read out to the end
        if reply is not None:
            reply.finish(reply.text)     # what came through stays; the device learns it is over
        logger.error(f"[DEVICES] {device_id}: the turn failed: {e}", exc_info=True)
    finally:
        speech = speech or box.get('speech')
        if speech is not None:
            speech.finish()              # the worker goes home whatever happened
        _free_slot(device_id)
        wake.release(device_id)
        cue(device_id, 'error' if failed else 'idle')


def woke(device_id):
    """A device's wake word fired and it asks for the room before it has
    anything to send (POST /api/devices/{id}/wake). {'ok', 'yours'} and, when
    not, 'taken_by'. A device that is refused records nothing and shows its
    resting light. Never raises."""
    try:
        e = _engine()
        why = e.refusal()
        if why:
            return {'ok': False, 'error': why}
        row = e.rows().get(str(device_id or '').strip().lower())
        if not row or not row.get('enabled', True) or not _voice_part(row):
            return {'ok': False, 'error': f"There is no device named '{device_id}' with a microphone, or it is turned off."}
        yours, taken_by = wake.claim(row['id'])
        if not yours:
            logger.info(f"[DEVICES] {row['id']} woke; the room is {taken_by}'s")
            return {'ok': True, 'yours': False, 'taken_by': taken_by}
        logger.info(f"[DEVICES] {row['id']} woke and holds the room")
        return {'ok': True, 'yours': True}
    except Exception as e:
        logger.error(f"[DEVICES] woke({device_id}) failed: {e}", exc_info=True)
        return {'ok': False, 'error': f"Something went wrong ({type(e).__name__})."}


def typed(device_id, text):
    """A device with a keyboard sends what was typed. {'ok', 'accepted',
    'chat', 'msg'} or {'ok': False, 'error'}. accepted = a turn has started;
    her reply reaches the device's screen through `text` doorbells and
    reply_text(). Never raises."""
    try:
        e = _engine()
        why = e.refusal()
        if why:
            return {'ok': False, 'error': why}
        row = e.rows().get(str(device_id or '').strip().lower())
        if not row or not row.get('enabled', True):
            return {'ok': False, 'error': f"There is no device named '{device_id}', or it is turned off."}
        part = _part_with(row, 'keyboard')
        if not part:
            return {'ok': False, 'error': f"'{row['id']}' has no keyboard."}
        text = str(text or '').strip()
        if not text:
            return {'ok': False, 'error': 'Nothing was typed.'}
        if len(text) > TYPED_MAX:
            return {'ok': False, 'error': f'That is more than {TYPED_MAX} characters.'}
        sm = _system().llm_chat.session_manager
        chat = str(part['config'].get('chat') or '').strip() or sm.get_active_chat_name()
        if sm.get_settings_for(chat) is None:
            return {'ok': False, 'error': f"The chat '{chat}' set for '{row['id']}' does not exist."}
        logger.info(f"[DEVICES] {row['id']} typed {len(text)} chars for chat '{chat}'")
        if not _take_slot(row['id']):
            return {'ok': False, 'busy': True, 'chat': chat,
                    'error': f"'{row['id']}' already has {MAX_WAITING} questions waiting."}
        reply = Reply(row['id'])
        try:
            _spawn(_turn, row, chat, text, reply)
        except Exception:
            _free_slot(row['id'])
            raise
        return {'ok': True, 'accepted': True, 'chat': chat, 'msg': reply.msg}
    except Exception as e:
        logger.error(f"[DEVICES] typed({device_id}) failed: {e}", exc_info=True)
        return {'ok': False, 'error': f"Something went wrong ({type(e).__name__})."}


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

        # One wake, one answer: the main app or another satellite may have
        # heard the same words (core/devices/wake.py). With nobody ahead,
        # this device holds the room from here; behind someone, its words
        # are compared with theirs and a repeat is dropped unanswered.
        others = wake.shadowed(row['id'])
        mine = not others
        if mine and not wake.holds(row['id']):      # a device that claimed at its wake holds it already
            wake.claim(row['id'])
        try:
            heard, problem = transcribe(audio, suffix)
            if problem:
                return {'ok': False, 'error': problem, 'chat': chat}
            if not heard:
                logger.info(f"[DEVICES] {row['id']}: audio arrived, no speech in it")
                return {'ok': True, 'heard': '', 'accepted': False, 'chat': chat}
            if mine:
                wake.heard(row['id'], heard)
            else:
                twin = next((o for o in others if wake.same(wake.words_of(o), heard)), None)
                if twin:
                    logger.info(f"[DEVICES] {row['id']} heard what {twin['who']} heard "
                                f"({len(heard)} chars), not answered twice")
                    return {'ok': True, 'heard': '', 'accepted': False, 'chat': chat, 'taken_by': twin['who']}
            logger.info(f"[DEVICES] {row['id']} heard {len(heard)} chars for chat '{chat}'")

            if not _take_slot(row['id']):
                return {'ok': False, 'busy': True, 'heard': heard, 'chat': chat,
                        'error': f"'{row['id']}' already has {MAX_WAITING} questions waiting."}
            try:
                _spawn(_turn, row, chat, heard)
            except Exception:
                _free_slot(row['id'])
                raise
            mine = False                     # the turn releases the room when it ends
            return {'ok': True, 'heard': heard, 'accepted': True, 'chat': chat}
        finally:
            if mine:
                wake.release(row['id'])
    except Exception as e:
        logger.error(f"[DEVICES] hear({device_id}) failed: {e}", exc_info=True)
        return {'ok': False, 'error': f"Something went wrong ({type(e).__name__})."}
