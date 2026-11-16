"""Easy setup: what this computer should run, picked from what it has
(tmp/easy-mode-plan.md, 2026-11-15).

The setup wizard's Easy step shows these rows and Finish saves the settings
behind them through the ordinary batch route. Nothing here changes anything.

The rule (Krem 2026-11-15):
  Voice  Kokoro when torch can use an NVIDIA card (core/gpu_status.py says
         'ok'), else Piper, the voice built for CPUs.
  Room   The card must also have room for them NOW (her LLM is loaded by the
         time this page opens): the voice first, then the ears, each on the
         processor when it does not fit (KOKORO_MIB, WHISPER_MIB).
  Ears   Faster Whisper, on the card when there is one, else on the CPU. A
         card torch cannot use YET keeps the device as it is: writing 'cpu'
         would read as a choice and silence the "fix the card" reminder
         (core/gpu_status.py _voice_tab).
  Wake   on, when this computer has a microphone (the page keeps it off
         while her ears are off).
  Brain  whatever Auto would use now; the Sapphire Launcher's LLM is looked
         for first (core/launcher_llm.py).

A second run (setup was finished before) keeps what is set: a row whose
setting differs from the pick carries `current`, the page shows that and offers
the pick beside it, and Finish changes nothing the person did not click.

The launcher installs the right torch before Sapphire ever starts, so this
only reads what is there: it never installs or swaps torch.

say() and listen() are the page's optional tests of this computer's speakers
and microphone: her voice (a tone until she has one), and what she heard.
"""
import logging
import queue
import threading
import time

logger = logging.getLogger(__name__)

PIPER = 'piper'


def _row(rid, tab, title, text, why, settings=None, note='', was=''):
    return {'id': rid, 'tab': tab, 'title': title, 'text': text, 'why': why,
            'settings': settings or {}, 'note': note, 'was': was, 'current': ''}


def _gpu():
    """(usable, card name, state). Usable = torch opened the card."""
    try:
        from core import gpu_status
        st = gpu_status.status()
    except Exception as e:
        logger.debug(f"[easy] GPU not read: {e}")
        return False, '', 'unknown'
    card = (st.get('gpu') or {}).get('name') or ''
    return st.get('state') == 'ok' and bool(card), card, st.get('state') or 'unknown'


# What each takes on the card once it has spoken or heard (measured 2026-11-15
# on an RTX 5090: Kokoro 1814 MiB after its longest piece, Whisper base.en int8
# 700 MiB), with some air.
KOKORO_MIB, WHISPER_MIB = 2000, 800


def _free(card):
    """Free memory on the card now, in MiB, None when it cannot be read."""
    try:
        from core import gpu_status
        return gpu_status.free_mib(card)
    except Exception as e:
        logger.debug(f"[easy] free graphics memory not read: {e}")
        return None


def _room(usable, card):
    """(her voice fits, her ears fit, free MiB or None) on a usable card. One
    already on the card has its memory: it fits. Unreadable = both fit."""
    if not usable:
        return False, False, None
    free = _free(card)
    if free is None:
        return True, True, None
    from core.settings_manager import settings

    def there(provider, value, device):
        return settings.get(provider) == value and (settings.get(device) or 'cuda') == 'cuda'
    left = free
    voice = there('TTS_PROVIDER', 'kokoro', 'KOKORO_DEVICE')
    if not voice and left >= KOKORO_MIB:
        voice, left = True, left - KOKORO_MIB
    ears = there('STT_PROVIDER', 'faster_whisper', 'FASTER_WHISPER_DEVICE') or left >= WHISPER_MIB
    return voice, ears, free


def _piper():
    """The Piper plugin as the loader knows it: None when it is not here,
    else {'ready': the provider is registered, 'note': what picking it does}."""
    try:
        from core.plugin_loader import plugin_loader
        from core.tts.providers import tts_registry
        info = plugin_loader.get_plugin_info(PIPER)
        if not info:
            return None
        if tts_registry.has_key(PIPER):
            return {'ready': True, 'note': ''}
        missing = plugin_loader._check_dependencies(info.get('manifest') or {})
        return {'ready': False,
                'note': ('Installs piper-tts, then turns on the Piper plugin.' if missing
                         else 'Turns on the Piper plugin.')}
    except Exception as e:
        logger.debug(f"[easy] Piper not read: {e}")
        return None


def _label(key, value):
    from core.provider_switch import _label
    return _label(key, value)


def _was(key, pick):
    """The provider set now, by name, when it is one and not the pick."""
    from core.settings_manager import settings
    cur = settings.get(key, 'none')
    return _label(key, cur) if cur and cur not in ('none', pick) else ''


def _gb(mib):
    return f"{mib / 1024:.1f} GB"


def _voice(usable, card, state, full=None):
    """`full`: the free MiB of a usable card with no room for her voice."""
    if usable and full is None:
        return _row('voice', 'tts', 'Voice', f"Kokoro on your {card}",
                    "Her best local voice. Your graphics card makes it fast.",
                    {'TTS_PROVIDER': 'kokoro', 'KOKORO_DEVICE': 'cuda'}, was=_was('TTS_PROVIDER', 'kokoro'))
    built = "so she gets the voice built for processors."
    why = (f"Your {card} has only {_gb(full)} free right now, {built}" if usable
           else f"{card} is here, but Sapphire cannot use it yet, {built}" if card
           else f"No NVIDIA graphics card here, {built}" if state == 'no_nvidia'
           else f"The graphics card could not be checked, {built}")
    from core.settings_manager import settings
    # a container keeps no pip install past its next image: Kokoro is in the image, Piper is not
    piper = None if settings.is_docker() else _piper()
    if piper:
        return _row('voice', 'tts', 'Voice', "Piper on the processor", why,
                    {'TTS_PROVIDER': PIPER}, note=piper['note'], was=_was('TTS_PROVIDER', PIPER))
    return _row('voice', 'tts', 'Voice', "Kokoro on the processor",
                why.replace(built, 'so Kokoro runs on the processor.'),
                {'TTS_PROVIDER': 'kokoro', 'KOKORO_DEVICE': 'cpu'}, was=_was('TTS_PROVIDER', 'kokoro'))


def _ears(usable, card, full=None):
    """`full`: the free MiB of a usable card with no room for her ears."""
    on_card = usable and full is None
    pick = {'STT_PROVIDER': 'faster_whisper'}
    if usable or not card:              # a card that only waits for its fix keeps the device setting
        pick['FASTER_WHISPER_DEVICE'] = 'cuda' if on_card else 'cpu'
    return _row('ears', 'stt', 'Ears',
                f"Whisper on your {card}" if on_card else "Whisper on the processor",
                "She hears you here, on this computer. Nothing you say leaves it.",
                pick, was=_was('STT_PROVIDER', 'faster_whisper'),
                note=f"Your {card} has only {_gb(full)} free right now." if usable and not on_card else '')


# The sound server's own doors, not devices: "default", PipeWire, and Windows' mapper.
_GENERIC = {'', 'default', 'sysdefault', 'pipewire', 'pulse'}
_GENERIC_STARTS = ('microsoft sound mapper', 'primary sound')


def _generic(name):
    name = str(name or '').strip().lower()
    return name in _GENERIC or name.startswith(_GENERIC_STARTS)


def _has_microphone():
    """A real input: the sound server's own doors are there with nothing plugged in."""
    try:
        from core.audio.device_manager import get_device_manager
        return any(not _generic(d.name) for d in get_device_manager().get_input_devices())
    except Exception as e:
        logger.debug(f"[easy] microphones not read: {e}")
        return False


def _has_speakers():
    try:
        from core.audio.device_manager import get_device_manager
        return bool(get_device_manager().get_output_devices())
    except Exception as e:
        logger.debug(f"[easy] speakers not read: {e}")
        return False


def _no_sound():
    """Why Sapphire cannot reach this computer's sound at all, '' when she can."""
    try:
        from core.audio import backend
        err = backend.error()
    except Exception as e:
        err = str(e)
    return f"Sapphire cannot reach this computer's sound system ({err})." if err else ''


def _phrase(model):
    try:
        from core.wakeword import library
        e = library.get(model, 'onnx')
        if e and e.get('phrase'):
            return str(e['phrase'])
    except Exception:
        pass
    return str(model or '').replace('_', ' ')


def _wake(docker):
    from core.settings_manager import settings
    if docker:
        return _row('wake', 'wakeword', 'Wake word', "Off", "A container has no microphone of its own.",
                    {'WAKE_WORD_ENABLED': False})
    if _no_sound() or not _has_microphone():
        return _row('wake', 'wakeword', 'Wake word', "Off", _no_sound() or "No microphone was found on this computer.",
                    {'WAKE_WORD_ENABLED': False})
    model = settings.get('WAKEWORD_MODEL', 'hey_sapphire') or 'hey_sapphire'
    # off after a finished setup, or on an install set up by hand, is a choice: the row says it changes
    by_hand = bool(settings.get('SETUP_COMPLETE', False)) or any(
        (settings.get(k, 'none') or 'none') != 'none' for k in ('TTS_PROVIDER', 'STT_PROVIDER'))
    off_by_choice = by_hand and not settings.get('WAKE_WORD_ENABLED', False)
    return _row('wake', 'wakeword', 'Wake word', f'On, say "{_phrase(model).title()}"',
                "Say it and talk, hands free. It listens on this computer only.",
                # the model as it is set: nothing to save, and the page can tell when the person picks another
                {'WAKE_WORD_ENABLED': True, 'WAKEWORD_MODEL': model}, was='Off' if off_by_choice else '')


_DEVICE_KEY = {'kokoro': 'KOKORO_DEVICE', 'faster_whisper': 'FASTER_WHISPER_DEVICE'}


def _now(rid, card):
    """What a row is set to today, in the words its pick would use."""
    from core.settings_manager import settings
    if rid == 'wake':
        if not settings.get('WAKE_WORD_ENABLED', False):
            return 'Off'
        return f'On, say "{_phrase(settings.get("WAKEWORD_MODEL", "hey_sapphire") or "hey_sapphire").title()}"'
    key = 'TTS_PROVIDER' if rid == 'voice' else 'STT_PROVIDER'
    cur = settings.get(key, 'none') or 'none'
    if cur == 'none':
        return 'Off'
    device = settings.get(_DEVICE_KEY[cur]) if cur in _DEVICE_KEY else None
    where = f" on your {card}" if device == 'cuda' and card else " on the processor" if device == 'cpu' else ''
    return f"{_label(key, cur)}{where}"


def _labels():
    """Provider names for a row the person changed by hand."""
    out = {'tts': {}, 'stt': {}}
    try:
        from core.tts.providers import tts_registry
        from core.stt.providers import stt_registry
        from core.routes.settings import _dormant_providers
        for name, reg in (('tts', tts_registry), ('stt', stt_registry)):
            rows = reg.get_all()
            rows = rows + _dormant_providers(name, {p['key'] for p in rows})
            out[name] = {p['key']: p.get('display_name') or p['key'] for p in rows}
    except Exception as e:
        logger.debug(f"[easy] provider names not read: {e}")
    return out


def plan():
    """The rows the Easy step shows, each with the settings Finish would save.
    Blocks for the GPU probe when it has not run yet: call it off the loop."""
    from core.settings_manager import settings
    rows = []
    usable, card, state = _gpu()
    free = None
    if not settings.is_managed():       # managed: the router speaks and hears
        voice_fits, ears_fit, free = _room(usable, card)
        rows = [_voice(usable, card, state, None if voice_fits else free),
                _ears(usable, card, None if ears_fit else free), _wake(settings.is_docker())]
    rerun = bool(settings.get('SETUP_COMPLETE', False))
    if rerun:                           # what is set stands; the pick is only offered
        for r in rows:
            if any(settings.get(k) != v for k, v in r['settings'].items()):
                r['current'] = _now(r['id'], card)
    sound = _sound()
    return {'rows': rows, 'gpu': {'state': state, 'name': card, 'usable': usable, 'free_mib': free}, 'labels': _labels(),
            'rerun': rerun, 'sound': sound, 'mic': sound and _has_microphone(), 'speakers': sound and _has_speakers(),
            # a computer that should have sound and does not: the page says why its tests are gone
            'sound_why': '' if sound or settings.is_managed() or settings.is_docker() else _no_sound()}


# ── the optional tests: this computer's speakers and microphone ────────────

LINE = "Hello. If you can hear this, my voice works."     # not her name: a wake word must not hear itself
HEARD = 0.02        # a peak that is a voice, not hiss (the browser mic test's line)
LISTEN_S = 6.0      # the longest take
QUIET_S = 1.0       # this much quiet after speech ends the take
LEVEL_S = 0.08      # one level event per this long
WORDS_S = 120       # the wait for the closing event (a first transcription on a CPU is slow)
SPEAK_S = 20        # the wait for her voice to reach the speakers (a first sentence loads the model)
_LISTENING = threading.Lock()     # one test at a time, the speaker's or the microphone's: each pauses the wake word


def _sound():
    """True when this computer has speakers and a microphone of its own to test."""
    from core.settings_manager import settings
    if settings.is_managed() or settings.is_docker():
        return False
    try:
        from core.audio import backend
        return backend.error() is None
    except Exception as e:
        logger.debug(f"[easy] audio backend not read: {e}")
        return False


def _device(name, what):
    """A device by name, or 'the default speakers' when the name is only the sound server's."""
    return f"the default {what}" if _generic(name) else str(name).strip()


def _busy(key):
    from core import provider_switch
    return bool(provider_switch.busy(key))


def _not_ready(key, what, why=''):
    """Why her voice or her ears cannot be tested yet, as a sentence."""
    from core.settings_manager import settings
    if _busy(key):
        return f"Her {what} still being set up. Try again when that is done."
    if (settings.get(key, 'none') or 'none') == 'none':
        return f"Her {what} not set up yet."
    return f"Her {what} not ready{': ' + why if why else ''}."


def _voice_block(system):
    """'' when she can speak on this computer now, else why not."""
    from core.tts.providers.null import NullTTSProvider
    from core.tts.tts_client import local_playback_on
    from core.settings_manager import settings
    prov = getattr(getattr(system, 'tts', None), 'provider', None)
    if (settings.get('TTS_PROVIDER', 'none') or 'none') == 'none' or prov is None or isinstance(prov, NullTTSProvider):
        return _not_ready('TTS_PROVIDER', 'voice is')
    try:
        if not prov.is_available():
            return _not_ready('TTS_PROVIDER', 'voice is', getattr(prov, '_health_why', None) or '')
    except Exception as e:
        return _not_ready('TTS_PROVIDER', 'voice is', str(e))
    if not local_playback_on():
        return "Speech is set not to play on this computer (Settings › TTS › Play speech here)."
    from core.voice_privacy import tts_gate_reason
    gate = tts_gate_reason(None)
    if gate:                                   # a private chat and a cloud voice: the line stays here
        return f"{gate}."
    return ''


def _spoke(system):
    """True when LINE reached the speakers. speak() only starts a thread and says
    True (a voice whose model never downloaded read as "She spoke"): the proof is
    playback starting, which the player publishes."""
    from core.event_bus import get_event_bus, Events
    started = threading.Event()

    def playing(event):
        started.set()
    bus = get_event_bus()
    bus.on((Events.TTS_PLAYING,), playing)
    try:
        if system.tts.speak(LINE) is False:
            return False
        if started.wait(SPEAK_S):
            return True
        system.tts.stop()                      # no sound in time: it must not start after the tone
        return False
    finally:
        bus.off((Events.TTS_PLAYING,), playing)


def say(system):
    """The speaker test: {ok, kind, device, why, busy, error}. `kind` is 'voice'
    (she said LINE on this computer's speakers) or 'tone' (she cannot yet, `why`
    says so, and a test tone played instead). `busy`: her voice is being set up now."""
    if not _LISTENING.acquire(blocking=False):
        return {'ok': False, 'kind': 'tone', 'why': '', 'device': '', 'busy': _busy('TTS_PROVIDER'),
                'error': "Another test is running. Try again when it is done."}
    try:
        why = _voice_block(system)
        if not why:
            try:
                if _spoke(system):
                    return {'ok': True, 'kind': 'voice', 'why': '', 'busy': _busy('TTS_PROVIDER'),
                            'device': _device(getattr(system.tts, 'output_device_name', ''), 'speakers')}
                why = "Her voice made no sound (it may still be downloading)."
            except Exception as e:
                logger.warning(f"[easy] voice test failed: {e!r}")
                why = f"Her voice failed: {e}."
        from core.audio import get_device_manager
        from core.routes.system import play_test_tone
        try:
            index, _rate, name = get_device_manager().find_output_device()
        except Exception as e:
            logger.debug(f"[easy] output device not read: {e}")
            index, name = None, ''
        r = play_test_tone(system, index)
        return {'ok': bool(r.get('success')), 'kind': 'tone', 'why': why, 'device': _device(name, 'speakers'),
                'busy': _busy('TTS_PROVIDER'),
                'error': '' if r.get('success') else f"No sound could be played on {_device(name, 'speakers')}."}
    finally:
        _LISTENING.release()


def listen(system):
    """The microphone test, as events for the page's stream: {'warn'} while
    she says she will listen, {'count': 3..1} with each beep, {'mic'} when it
    listens, then
    {'level'} while it listens, {'thinking'} while the words are made, and one
    closing event with `done`: {'error'} | {'heard': False, 'why'?} | {'heard':
    True, 'text', 'ears', 'busy'}. `why` is there when the microphone sent pure
    silence (muted, or blocked by the system). `ears` is why there are no words
    when her ears are not ready (the microphone still passed); `busy`: they are
    being set up now.

    The take runs on its own thread and always ends by itself, so a page that
    goes away mid-take never leaves the wake word paused."""
    q = queue.Queue()
    threading.Thread(target=_listen, args=(system, q.put), name='easy-listen', daemon=True).start()
    while True:
        try:
            ev = q.get(timeout=WORDS_S)
        except queue.Empty:
            yield {'error': "That took too long. Try again.", 'done': True}
            return
        yield ev
        if ev.get('done'):
            return


def _listen(system, emit):
    if not _LISTENING.acquire(blocking=False):
        emit({'error': "She is already listening for a test.", 'done': True})
        return
    last = {'error': "The microphone test stopped early.", 'done': True}
    try:
        try:
            take = _take(system, emit)
        except Exception as e:                 # the microphone's own trouble
            logger.warning(f"[easy] microphone test failed: {e!r}")
            from core.audio import classify_audio_error
            last = {'error': classify_audio_error(e), 'done': True}
            return
        try:
            last = {**_words_of(system, emit, *take), 'done': True}
        except Exception as e:                 # the take was fine: whatever broke after it is not the microphone
            logger.warning(f"[easy] the take could not be turned into words: {e!r}")
            last = {'heard': True, 'text': '', 'busy': False, 'done': True,
                    'ears': f"Her ears failed, this is not your microphone: {e}"}
    finally:
        _LISTENING.release()
        emit(last)


# ── the warning before the microphone opens (Krem 2026-11-15) ──────────────
#
# The page may be open on another machine, and the test hands back what the
# room said. So the Sapphire computer says so first: one sentence in her voice,
# then a beep a second for three seconds, then it listens. When the microphone
# is closed again: a ping and "Done". With no voice yet the beeps stand alone.
# Best effort: none of it may stop the test.

WARN_LINE = "I am going to listen to your microphone in 3 seconds."
DONE_LINE = "Done."


def _aloud(system, text):
    """Her voice says `text` on this computer, and this returns when it has
    been said: True when it really played (the player publishes that)."""
    if _voice_block(system):
        return False
    from core.event_bus import get_event_bus, Events
    started = threading.Event()

    def playing(event):
        started.set()
    bus = get_event_bus()
    bus.on((Events.TTS_PLAYING,), playing)
    try:
        system.tts.speak_sync(text)
        return started.is_set()
    except Exception as e:
        logger.debug(f"[easy] not spoken: {e}")
        return False
    finally:
        bus.off((Events.TTS_PLAYING,), playing)


def _warn(system, emit):
    """The sentence, then three beeps a second apart; the page counts along."""
    from core.routes.system import play_test_tone
    emit({'warn': True})
    logger.info("[easy] the setup page's microphone test is about to listen on this computer")
    _aloud(system, WARN_LINE)
    try:
        from core.audio import get_device_manager
        out = get_device_manager().find_output_device()[0]
    except Exception as e:
        logger.debug(f"[easy] output device not read: {e}")
        out = None
    for left in (3, 2, 1):
        emit({'count': left})
        at = time.monotonic()
        try:
            play_test_tone(system, out, duration=0.15, frequency=880)
        except Exception as e:
            logger.debug(f"[easy] warning beep not played: {e}")
        time.sleep(max(0.0, 1.0 - (time.monotonic() - at)))    # a beep a second, however long the beep took to play


def _all_clear(system):
    """The microphone is closed again: a ping and "Done" (a falling tone when she has no voice)."""
    from core.routes.system import play_test_tone
    try:
        from core.audio import get_device_manager
        out = get_device_manager().find_output_device()[0]
    except Exception as e:
        logger.debug(f"[easy] output device not read: {e}")
        out = None
    try:
        play_test_tone(system, out, duration=0.15, frequency=880)
        if not _aloud(system, DONE_LINE):
            play_test_tone(system, out, duration=0.3, frequency=660)
    except Exception as e:
        logger.debug(f"[easy] no all-clear after the microphone test: {e}")


def _take(system, emit):
    """Listen until speech then quiet (or LISTEN_S): (samples, rate, loudest peak, the microphone's name)."""
    import numpy as np
    from core.routes.system import _quiesce_wakeword, _resume_wakeword
    chunks = []
    stopped = _quiesce_wakeword(system)        # her wake word holds this microphone
    try:
        _warn(system, emit)
        try:
            mic, rate, loudest = _listen_once(system, emit, chunks)
        finally:                               # the microphone is closed, however it went: say so
            emit({'clear': True})
            _all_clear(system)
    finally:
        _resume_wakeword(system, *stopped)
    return np.concatenate(chunks), rate, loudest, mic


def _listen_once(system, emit, chunks):
    """Open the microphone, fill `chunks`, close it: (its name, sample rate, loudest peak)."""
    import numpy as np
    from core.audio import get_device_manager, convert_to_mono
    from core.audio.backend import sd
    loudest, last_loud = 0.0, None
    dc = get_device_manager().find_input_device(target_rate=None, preferred_blocksize=1024)
    mic = _device(dc.device_name, 'microphone')
    with sd.InputStream(device=dc.device_index, samplerate=dc.sample_rate, channels=dc.channels,
                        dtype=np.int16, blocksize=dc.blocksize) as stream:
        emit({'mic': mic})
        start = told = time.monotonic()
        peak = 0.0
        while True:
            data, _ = stream.read(dc.blocksize)
            mono = convert_to_mono(data) if dc.needs_stereo_downmix else data.flatten()
            chunks.append(mono.astype(np.int16))
            peak = max(peak, float(np.max(np.abs(mono.astype(np.int32)))) / 32768 if len(mono) else 0.0)
            now = time.monotonic()
            if peak >= HEARD:
                last_loud = now
            loudest = max(loudest, peak)
            if now - told >= LEVEL_S:
                emit({'level': round(peak, 3)})
                told, peak = now, 0.0
            if now - start >= LISTEN_S or (last_loud and now - last_loud >= QUIET_S):
                break
    return mic, dc.sample_rate, loudest


def _words_of(system, emit, audio, rate, loudest, mic):
    """The closing event for a take: silence, or the words (or why there are none)."""
    import sys
    import numpy as np
    from core.audio import resample_audio
    if loudest == 0.0:
        # Not one sample moved. A live microphone always hisses a little: this
        # one is muted, or the system is keeping it from Sapphire (Windows'
        # privacy switch opens the device and hands back zeros).
        fix = (' In Windows: Settings › Privacy & security › Microphone, turn on '
               '"Let desktop apps access your microphone".' if sys.platform == 'win32'
               else ' Unmute it in your sound settings.')
        return {'heard': False, 'why': f"I opened {mic}, but it sent pure silence: it is muted or blocked.{fix}"}
    if loudest < HEARD:
        return {'heard': False}
    from core.stt.utils import can_transcribe
    client = getattr(system, 'whisper_client', None)
    ok, why = can_transcribe(client)
    if not ok:
        return {'heard': True, 'text': '', 'ears': _not_ready('STT_PROVIDER', 'ears are', why),
                'busy': _busy('STT_PROVIDER')}
    from core.voice_privacy import stt_gate_reason
    gate = stt_gate_reason(system.llm_chat.session_manager.get_chat_settings())
    if gate:                                   # a private chat and cloud ears: the take stays here
        return {'heard': True, 'text': '', 'ears': gate, 'busy': False}
    emit({'thinking': True})
    if rate != 16000:
        audio = resample_audio(audio, rate, 16000).astype(np.int16)
    from core.stt.providers.base import STTFailed
    try:
        return {'heard': True, 'text': _words(client, audio) or '', 'ears': '', 'busy': _busy('STT_PROVIDER')}
    except STTFailed as e:
        return {'heard': True, 'text': '', 'ears': f"Her ears failed, this is not your microphone: {e}", 'busy': False}


def _words(client, audio):
    """16 kHz mono int16 -> the words, through the same door the mic button uses."""
    import os
    import tempfile
    import wave
    fd, path = tempfile.mkstemp(suffix='.wav')
    try:
        with os.fdopen(fd, 'wb') as f, wave.open(f, 'wb') as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(audio.tobytes())
        return client.transcribe_file(path)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# ── each row's own test, and its light (Krem 2026-11-15) ───────────────────

HELLO = "Say hello in one short, friendly sentence."
WAKE_S = 8          # how long the wake word test waits to hear its phrase


def hello():
    """The Brain row's test: whichever LLM Auto would use answers one line.
    {ok, text, name, why}."""
    from core import think
    from core.chat.llm_providers import get_generation_params
    from core.chat.llm_providers.resolve import providers_config, resolve
    try:
        sel = resolve('auto', conversation='setup')
    except Exception as e:
        return {'ok': False, 'text': '', 'name': '', 'why': str(e) or "No LLM answered."}
    name = sel.display_name
    try:
        provider, model = sel.provider, sel.effective_model
        if not model and getattr(provider, 'ensure_model', None) and provider.ensure_model():
            model = provider.model
        gen = get_generation_params(sel.key, model, providers_config())
        if model:
            gen['model'] = model
        r = provider.chat_completion([{'role': 'user', 'content': HELLO}], generation_params=gen)
        text = think.strip(getattr(r, 'content', '') or '').strip()      # her answer, not her scratch work
    except Exception as e:
        logger.warning(f"[easy] the LLM test failed: {e!r}")
        return {'ok': False, 'text': '', 'name': name, 'why': f"{name} did not answer: {str(e)[:160]}"}
    return {'ok': bool(text), 'text': text[:300], 'name': f"{name}{': ' + model if model else ''}",
            'why': '' if text else f"{name} answered with nothing."}


def _failed(row_key):
    """Why the last switch behind a row failed, '' when it did not (its notification row)."""
    try:
        from core import notify
        for r in notify.rows():
            if r.get('key') == row_key:
                return (r.get('body') or r.get('title') or 'It failed.') if r.get('state') == 'failed' else ''
    except Exception as e:
        logger.debug(f"[easy] switch rows not read: {e}")
    return ''


def status(system):
    """The light on each row: what is LIVE behind it now, whatever is staged.
    {voice|ears|wake: {state, why}}; state: 'ok' (works), 'loading' (being set
    up, or not ready yet), 'failed' (its last switch failed and nothing is
    set), 'off'."""
    from core.settings_manager import settings

    def light(key, row_key, off, check):
        if _busy(key):
            return {'state': 'loading', 'why': "Being set up now: its progress shows under Downloads."}
        if off:
            why = _failed(row_key)
            return {'state': 'failed' if why else 'off', 'why': why}
        try:
            return check()
        except Exception as e:
            return {'state': 'loading', 'why': str(e)}

    def voice():
        why = _voice_block(system)
        return {'state': 'loading' if why else 'ok', 'why': why}

    def ears():
        from core.stt.utils import can_transcribe
        ok, why = can_transcribe(getattr(system, 'whisper_client', None))
        return {'state': 'ok' if ok else 'loading', 'why': '' if ok else _not_ready('STT_PROVIDER', 'ears are', why)}

    def wake():
        from core.wakeword.wakeword_null import NullWakeWordDetector
        det, rec = getattr(system, 'wake_detector', None), getattr(system, 'wake_word_recorder', None)
        listening = (det is not None and not isinstance(det, NullWakeWordDetector)
                     and (_LISTENING.locked() or (getattr(det, 'running', False) and getattr(rec, 'stream', None) is not None)))
        return {'state': 'ok', 'why': ''} if listening else {
            'state': 'failed', 'why': "The wake word is on but not listening: the microphone did not open."}

    return {
        'voice': light('TTS_PROVIDER', 'switch-tts', (settings.get('TTS_PROVIDER', 'none') or 'none') == 'none', voice),
        'ears': light('STT_PROVIDER', 'switch-stt', (settings.get('STT_PROVIDER', 'none') or 'none') == 'none', ears),
        'wake': light('WAKE_WORD_ENABLED', 'switch-wakeword', not settings.get('WAKE_WORD_ENABLED', False), wake),
    }


def hear_wake(system):
    """The Wake row's test: wait for the wake phrase, and prove only that it was
    heard. Nothing is recorded or sent anywhere (the detector's `probe`).
    {ok, heard, why, busy}: ok False = the wake word is not listening."""
    from core.settings_manager import settings
    from core.wakeword.wakeword_null import NullWakeWordDetector
    det = getattr(system, 'wake_detector', None)
    busy = _busy('WAKE_WORD_ENABLED')
    if busy or not settings.get('WAKE_WORD_ENABLED', False) or det is None or isinstance(det, NullWakeWordDetector):
        return {'ok': False, 'heard': False, 'busy': busy,
                'why': "The wake word is still being set up. Try again when that is done." if busy
                       else "The wake word is not on yet."}
    if not _LISTENING.acquire(blocking=False):
        return {'ok': False, 'heard': False, 'busy': False, 'why': "Another test is running. Try again when it is done."}
    probe = threading.Event()
    try:
        det.probe = probe
        heard = probe.wait(WAKE_S)
    finally:
        det.probe = None
        _LISTENING.release()
    return {'ok': True, 'heard': heard, 'busy': False, 'why': ''}


def _launcher_says():
    """The Brain row from what the launcher itself reports when nothing answers
    on its port (core/launcher_llm.py cli_status): a model downloading and how
    far, installed but stopped, a download that failed. None = it could not be
    asked, or has nothing to add to "No LLM yet"."""
    from core import launcher_llm
    try:
        told = launcher_llm.cli_status()
    except Exception as e:
        logger.debug(f"[easy] launcher not asked: {e}")
        told = None
    if not told:
        return None
    phase = told.get('phase')
    where = "the Sapphire Launcher, on the LLM tab"
    if phase == 'downloading':
        job = told.get('job') if isinstance(told.get('job'), dict) else {}
        pct = job.get('percent')
        far = f": {round(pct)}%" if isinstance(pct, (int, float)) and not isinstance(pct, bool) else '...'
        return {'state': 'loading', 'text': f"Downloading {job.get('label') or 'her LLM'}{far}",
                'hint': "The launcher is fetching it. This row turns green when it runs. You can finish now."}
    if phase in ('loading', 'running'):     # up, and not answering core yet
        return {'state': 'loading', 'text': "The launcher's LLM is starting...",
                'hint': "This row turns green when it answers. You can finish now."}
    if phase == 'stopped':
        return {'state': 'stopped', 'text': "Her LLM is installed, but not running.",
                'hint': f"Start it in {where}. She cannot reply until it runs."}
    last = told.get('last_job') if isinstance(told.get('last_job'), dict) else {}
    if last.get('state') == 'failed':
        why = str(last.get('error') or '').strip().rstrip('.')
        return {'state': 'none', 'text': f"The download of {last.get('label') or 'her LLM'} failed.",
                'hint': f"{why + '. ' if why else ''}Try again in {where}, or add your own under Change."}
    if last.get('state') in ('cancelled', 'interrupted'):
        return {'state': 'none', 'text': f"The download of {last.get('label') or 'her LLM'} did not finish.",
                'hint': f"Start it again in {where}, or add your own under Change."}
    return None


def brain(light=False):
    """The Brain row: {state, text, hint}. state: 'running' (the launcher's
    LLM), 'ok' (another provider answers), 'loading' (starting, or still
    downloading), 'stopped', 'none'.

    `light` is the page's poll while nothing answers: the launcher only (a
    file stat, a loopback probe, and its own --llm-status when the port is
    silent), and the whole look only once it runs."""
    from core import launcher_llm
    from core.settings_manager import settings
    try:
        launcher_llm.adopt()
        ls = launcher_llm.status()
    except Exception as e:
        logger.debug(f"[easy] launcher not read: {e}")
        ls = {'provider': None, 'state': None}
    sel = None
    if not light or ls.get('state') == 'running':
        try:
            from core.chat.llm_providers.resolve import resolve
            sel = resolve('auto', conversation='setup')
        except ConnectionError:
            sel = None
        except Exception as e:
            logger.warning(f"[easy] Auto could not be asked: {e}")
    if sel is None and ls.get('state') == 'running':
        # its server answers, Auto's quick health look just missed it (a busy
        # computer mid-install): never "not running" over a running LLM
        return {'state': 'loading', 'text': "The launcher's LLM is starting...",
                'hint': "This row turns green when it answers. You can finish now."}
    if sel is not None:
        if sel.key == launcher_llm.KEY:
            n = ls.get('n_ctx')
            size = f" ({round(n / 1024)}K context)" if n else ''
            return {'state': 'running', 'hint': '',
                    'text': f"Sapphire Launcher: {ls.get('model') or sel.effective_model or 'its model'}, running{size}"}
        model = sel.effective_model
        return {'state': 'ok', 'hint': '', 'text': f"{sel.display_name}{': ' + model if model else ''}"}
    if ls.get('state') == 'loading':
        return {'state': 'loading', 'text': "The launcher's LLM is loading...",
                'hint': "This row turns green when it is ready. You can finish now."}
    if settings.is_managed() or settings.is_docker():
        return {'state': 'none', 'text': "No LLM answers yet.",
                'hint': "She cannot reply until one does. Add one under Change."}
    told = _launcher_says()
    if told:
        return told
    if ls.get('provider'):
        return {'state': 'stopped', 'text': "The launcher's LLM is not running.",
                'hint': "Start it in the Sapphire Launcher, on the LLM tab. She cannot reply until it runs."}
    return {'state': 'none', 'text': "No LLM yet.",
            'hint': "Install one in the Sapphire Launcher, on the LLM tab, or add your own under Change. "
                    "She cannot reply until then."}
