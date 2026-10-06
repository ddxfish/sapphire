# core/stt/listen.py - one utterance from this machine's microphone, on request
#
# The wake word hears her name and then records. This is the other way in: a
# caller asks for the microphone for a few seconds (a persona at the MCP door,
# waiting for a spoken answer). The recorder, the VAD and the transcriber are
# the wake path's own. The microphone has one reader at a time, so the wake
# word's stream is closed for the length of the listen and brought back right
# after, the way conversation mode does it.
import logging
import os
import threading
import time

import config

logger = logging.getLogger(__name__)

LEAST, MOST = 3, 60            # seconds one listen may hold the microphone
HER_TURN = 20                  # seconds to wait for her own voice to end first
_ears = threading.Lock()       # one listen at a time


def _refusal(system):
    """Why the microphone may not be opened now, in words, or ''."""
    from core.stt.utils import can_transcribe
    from core.stt.stt_null import NullAudioRecorder
    ok, reason = can_transcribe(getattr(system, 'whisper_client', None))
    if not ok:
        return reason
    if isinstance(getattr(system, 'whisper_recorder', None), (NullAudioRecorder, type(None))):
        return "This Sapphire has no microphone recorder of its own."
    if getattr(system, 'conversation_mode_enabled', False):
        return "She is in a live conversation and the microphone is hers."
    # The same gate as the wake path, on the same chat: a private chat with a
    # cloud transcriber means this room's audio would leave the machine.
    try:
        from core.voice_privacy import stt_gate_reason
        return stt_gate_reason(system.llm_chat.session_manager.get_chat_settings())
    except Exception:
        return ''


def _give_back(system, detector):
    """Her wake word back on the microphone, and checked: a listen must never
    leave her deaf. True when it is listening again."""
    system._restore_wakeword()                           # never raises
    for wait in (0.3, 0.6):
        if getattr(detector, 'running', False):
            return True
        time.sleep(wait)                                 # the device was slow to let go of the recorder's stream
        system._restore_wakeword()
    if getattr(detector, 'running', False):
        return True
    logger.error("[LISTEN] the wake word did NOT come back on the microphone after a listen")
    return False


def _words(system, path):
    """What the recording says. Not hallucination-filtered: the recorder's VAD
    already heard a voice, and a real "okay" or "thank you" must not vanish.
    The caller is told when the words are ones a transcriber also invents."""
    client = system.whisper_client
    read = getattr(client, '_transcribe_impl', None) or client.transcribe_file
    text = str(read(path) or '').strip()
    if not text:
        return ''
    try:
        from core.hooks import hook_runner, HookEvent
        if hook_runner.has_handlers("post_stt"):         # every STT lane shows plugins its words
            event = HookEvent(input=text, config=config, metadata={"system": system})
            hook_runner.fire("post_stt", event)
            text = str(event.input or '').strip()
    except Exception as e:
        logger.debug(f"post_stt hook fire failed: {e}")
    return text


def once(system, seconds=20, cue=None):
    """Record until the speaker stops, or `seconds` pass, and return what was
    said. `cue()` runs once the microphone is ours, just before recording: the
    sound that says "speak now". Returns (text, ok); silence is an answer
    (ok), a microphone that could not be had is not."""
    try:
        seconds = max(LEAST, min(MOST, int(seconds)))
    except (TypeError, ValueError):
        seconds = 20
    refused = _refusal(system)
    if refused:
        return refused, False
    if not _ears.acquire(blocking=False):
        return "The microphone is already taken by another listen.", False
    detector = getattr(system, 'wake_detector', None)
    thread = getattr(detector, 'listen_thread', None)
    held = bool(getattr(detector, 'running', False))     # the wake word is listening: it gets the mic back
    hers = "She is in a voice turn of her own and the microphone is hers. Try again in a moment."
    path, recorder, started, restore, deaf = None, system.whisper_recorder, time.monotonic(), False, ''
    try:
        # A wake turn in flight owns the recorder: taking it would cut the
        # user off mid-sentence. Its sign is the wake stream torn down while
        # the wake word is on, or its thread alive while the wake word is off.
        # Nothing has been touched yet, so there is nothing to put back.
        if held and system.wake_word_recorder.get_stream() is None:
            return hers, False
        if not held and thread is not None and thread.is_alive():
            return hers, False
        if held:
            detector.stop_listening()
            thread = getattr(detector, 'listen_thread', None)    # read after the stop: the one that matters now
            if thread is not None and thread.is_alive():
                # She woke in the instant between the check and the stop. The
                # thread is alive, so this only re-arms its flag: it reopens
                # its own stream when its turn ends. Opening it here would put
                # two streams on one microphone.
                detector.start_listening()
                return hers, False
            restore = True
            system.wake_word_recorder.stop_recording()   # no reader is left on it: safe to close
        tts = getattr(system, 'tts', None)
        if tts is not None and hasattr(tts, 'wait'):
            tts.wait(timeout=HER_TURN)
        if cue is not None:
            try:
                cue()
            except Exception as e:
                logger.warning(f"[LISTEN] the cue did not play: {e}")
        path = recorder.record_audio(max_seconds=seconds, no_speech_timeout=seconds)
    finally:
        # Her ears come back before anything is transcribed, and it is said out loud when they do not.
        if restore and not _give_back(system, detector):
            deaf = " WARNING: her wake word did not come back on the microphone. Tell the user."
        _ears.release()
    if not path or not os.path.exists(path):
        why = getattr(recorder, 'last_failure_reason', '') or ''
        if why == 'no_speech_captured':
            logger.info(f"[LISTEN] nobody spoke in {seconds}s")
            return f"Heard nothing: nobody spoke in {seconds} seconds.{deaf}", True
        logger.warning(f"[LISTEN] no recording ({why or 'unknown'})")
        return ("The microphone could not be opened." if why == 'mic_busy' else "The recording could not be saved.") + deaf, False
    try:
        text = _words(system, path)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    logger.info(f"[LISTEN] heard {len(text)} chars in {time.monotonic() - started:.1f}s")
    if not text:
        return f"Heard a voice, and no words came out of it.{deaf}", True
    from core.stt.hallucination import is_whisper_hallucination
    if is_whisper_hallucination(text):
        return f'Heard: "{text}" (a phrase transcribers also invent from noise, so weigh it){deaf}', True
    return f'Heard: "{text}"{deaf}', True
