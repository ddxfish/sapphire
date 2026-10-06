# tests/test_stt_listen.py - core/stt/listen.py: one utterance from this
# machine's microphone on request. The recorder, the transcriber and the wake
# word are faked; no microphone is opened. What is proven: the wake word's
# stream is closed around the recording and always brought back, a wake turn
# in flight is never cut off, and nothing heard is silently dropped.
import os
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import config
from core.stt import listen


class Recorder:
    def __init__(self, world):
        self.world, self.last_failure_reason, self.fail = world, '', None

    def record_audio(self, max_seconds=None, no_speech_timeout=None):
        self.world.order.append(f'recorded max={max_seconds} first_word={no_speech_timeout}')
        if self.fail == 'boom':
            raise RuntimeError('portaudio')
        if self.fail:
            self.last_failure_reason = self.fail
            return None
        path = str(self.world.tmp / 'utterance.wav')
        open(path, 'wb').close()
        return path


class Detector:
    """The wake word. `wakes_mid_stop` leaves its thread alive after a stop,
    the way a wake turn that began in that instant would."""
    def __init__(self, world):
        self.world, self.running, self.listen_thread, self.wakes_mid_stop = world, True, None, False

    def stop_listening(self):
        self.world.order.append('wake stopped')
        self.running = False
        if self.wakes_mid_stop:
            self.listen_thread = self.world.alive

    def start_listening(self):
        self.world.order.append('wake re-armed')
        self.running = True


@pytest.fixture
def world(monkeypatch, tmp_path):
    w = SimpleNamespace(order=[], tmp=tmp_path, said='turn the lights off', comes_back=True)
    w.alive = SimpleNamespace(is_alive=lambda: True)
    w.recorder, w.detector = Recorder(w), Detector(w)
    w.wake_mic = SimpleNamespace(stream=object(), get_stream=lambda: w.wake_mic.stream,
                                 stop_recording=lambda: w.order.append('wake mic closed'))

    def transcribe(path):
        assert os.path.exists(path)
        w.order.append('transcribed')
        return w.said

    def restore():
        w.order.append('wake restored')
        w.detector.running = w.comes_back

    w.system = SimpleNamespace(
        whisper_client=SimpleNamespace(_transcribe_impl=transcribe, is_available=lambda: True),
        whisper_recorder=w.recorder, wake_detector=w.detector, wake_word_recorder=w.wake_mic,
        conversation_mode_enabled=False, _restore_wakeword=restore,
        tts=SimpleNamespace(wait=lambda timeout=0: w.order.append('her voice ended')),
        llm_chat=SimpleNamespace(session_manager=SimpleNamespace(get_chat_settings=lambda: {})))
    monkeypatch.setattr(config, 'STT_PROVIDER', 'faster_whisper', raising=False)
    monkeypatch.setattr(listen.time, 'sleep', lambda s: None)
    return w


def cue_of(world):
    return lambda: world.order.append('cue')


# --- the handoff ---------------------------------------------------------------

def test_the_wake_word_gives_up_the_mic_and_gets_it_back_before_transcribing(world):
    assert listen.once(world.system, 20, cue=cue_of(world)) == ('Heard: "turn the lights off"', True)
    assert world.order == ['wake stopped', 'wake mic closed', 'her voice ended', 'cue',
                           'recorded max=20 first_word=20', 'wake restored', 'transcribed']
    assert not (world.tmp / 'utterance.wav').exists()            # the recording is not kept


@pytest.mark.parametrize('asked, got', [(999, 60), (1, 3), ('soon', 20), (None, 20), (45, 45)])
def test_the_seconds_are_kept_in_bounds(world, asked, got):
    listen.once(world.system, asked)
    assert f'recorded max={got} first_word={got}' in world.order


def test_a_wake_turn_in_flight_is_never_touched(world):
    world.wake_mic.stream = None                 # torn down for her own recording
    text, ok = listen.once(world.system, cue=cue_of(world))
    assert not ok and 'voice turn of her own' in text
    assert world.order == [] and world.detector.running is True


def test_a_turn_finishing_while_the_wake_word_is_off_is_never_touched(world):
    world.detector.running, world.detector.listen_thread = False, world.alive
    text, ok = listen.once(world.system)
    assert not ok and 'voice turn of her own' in text and world.order == []


def test_a_wake_in_the_instant_of_the_stop_is_only_rearmed(world):
    world.detector.wakes_mid_stop = True
    text, ok = listen.once(world.system, cue=cue_of(world))
    assert not ok and 'voice turn of her own' in text
    # re-armed and nothing else: its stream is its own thread's to reopen
    assert world.order == ['wake stopped', 'wake re-armed'] and world.detector.running is True


def test_with_the_wake_word_off_it_just_records_and_starts_nothing(world):
    world.detector.running = False
    assert listen.once(world.system)[1] is True
    assert 'wake stopped' not in world.order and 'wake restored' not in world.order
    assert world.detector.running is False       # a wake word the user turned off stays off


def test_the_wake_word_comes_back_when_the_recorder_blows_up(world):
    world.recorder.fail = 'boom'
    with pytest.raises(RuntimeError):
        listen.once(world.system)
    assert world.order[-1] == 'wake restored' and world.detector.running is True
    world.recorder.fail = None
    assert listen.once(world.system)[1] is True  # and the next listen is not locked out


def test_a_wake_word_that_does_not_come_back_is_retried_and_then_said_out_loud(world):
    world.comes_back = False
    text, ok = listen.once(world.system)
    assert world.order.count('wake restored') == 3
    assert ok and text.startswith('Heard: "turn the lights off"') and 'WARNING: her wake word did not come back' in text


def test_a_cue_that_fails_does_not_cost_the_listen(world):
    def cue():
        raise RuntimeError('no speakers')
    assert listen.once(world.system, cue=cue) == ('Heard: "turn the lights off"', True)


def test_one_listen_at_a_time(world):
    assert listen._ears.acquire(blocking=False)
    try:
        text, ok = listen.once(world.system)
    finally:
        listen._ears.release()
    assert not ok and 'already taken' in text and world.order == []


# --- what comes back -----------------------------------------------------------

def test_silence_is_an_answer_and_a_dead_mic_is_a_failure(world):
    world.recorder.fail = 'no_speech_captured'
    assert listen.once(world.system, 8) == ('Heard nothing: nobody spoke in 8 seconds.', True)
    world.recorder.fail = 'mic_busy'
    assert listen.once(world.system) == ('The microphone could not be opened.', False)
    assert world.order.count('wake restored') == 2 and 'transcribed' not in world.order


@pytest.mark.parametrize('said', ['Okay.', 'thank you', 'Bye!'])
def test_a_real_okay_is_kept_and_flagged_not_dropped(world, said):
    world.said = said
    text, ok = listen.once(world.system)
    assert ok and text.startswith(f'Heard: "{said}"') and 'invent from noise' in text


def test_a_voice_with_no_words_says_so(world):
    world.said = '   '
    assert listen.once(world.system) == ('Heard a voice, and no words came out of it.', True)


def test_plugins_see_the_words_like_on_every_stt_lane(world, monkeypatch):
    from core import hooks
    monkeypatch.setattr(hooks.hook_runner, 'has_handlers', lambda name: name == 'post_stt')

    def fire(name, event):
        event.input = event.input.replace('lights', 'lamps')
    monkeypatch.setattr(hooks.hook_runner, 'fire', fire)
    assert listen.once(world.system) == ('Heard: "turn the lamps off"', True)


# --- when the microphone may not be opened --------------------------------------

def test_refusals_touch_nothing(world, monkeypatch):
    from core.stt.stt_null import NullAudioRecorder
    import core.voice_privacy as vp

    world.system.conversation_mode_enabled = True
    assert 'live conversation' in listen.once(world.system)[0]
    world.system.conversation_mode_enabled = False

    monkeypatch.setattr(vp, 'stt_gate_reason', lambda settings: 'Private chat: audio would leave this machine.')
    assert listen.once(world.system) == ('Private chat: audio would leave this machine.', False)
    monkeypatch.setattr(vp, 'stt_gate_reason', lambda settings: '')

    world.system.whisper_recorder = NullAudioRecorder()
    assert 'no microphone recorder' in listen.once(world.system)[0]

    monkeypatch.setattr(config, 'STT_PROVIDER', 'none', raising=False)
    assert listen.once(world.system) == ('Speech-to-text is disabled', False)
    assert world.order == []
