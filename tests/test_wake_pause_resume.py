"""The wake word held for an audio test, and given back (hunt 2026-11-16).

Two ways it was not given back as it was taken:
- a pause inside a pause (each warning beep of Easy setup's microphone test goes
  through play_test_tone, which holds the wake word itself) read True, and its
  resume restarted the stream the outer pause had stopped: a second input
  stream on the microphone the test was about to open;
- a wake turn that ended while the wake word was held left the microphone
  closed and its thread gone, and the resume gave up quietly: deaf, shown ON.
"""
import threading
import types

import core.api_fastapi  # noqa: F401  (first: the routes import the app, and the app imports them)
from core.routes import system as routes
from core.wakeword.audio_recorder import AudioRecorder


class Stream:
    def __init__(self):
        self.active, self.calls = True, []

    def stop(self):                 # as sounddevice: stopping a stopped stream raises nothing
        self.calls.append('stop')
        self.active = False

    def start(self):
        self.calls.append('start')
        self.active = True


def recorder(stream):
    rec = object.__new__(AudioRecorder)      # no device probe: only the stream and its lock
    rec._stream_lock, rec.stream = threading.Lock(), stream
    return rec


class Detector:
    """start_listening as the real one: no stream, no start."""
    def __init__(self, rec, running=True):
        self.rec, self.running, self.starts = rec, running, 0

    def stop_listening(self):
        self.running = False

    def start_listening(self):
        self.starts += 1
        if self.rec.get_stream() is not None:
            self.running = True


def test_a_pause_inside_a_pause_does_not_restart_the_stream(monkeypatch):
    monkeypatch.setattr(routes.time, 'sleep', lambda s: None)
    stream = Stream()
    rec = recorder(stream)
    system = types.SimpleNamespace(wake_word_recorder=rec, wake_detector=Detector(rec))
    outer = routes._quiesce_wakeword(system)
    assert outer == (True, True) and not stream.active
    inner = routes._quiesce_wakeword(system)             # a beep's own hold, inside the test's
    assert inner == (False, False)
    routes._resume_wakeword(system, *inner)
    assert not stream.active, "the inner resume restarted the stream the test still holds"
    routes._resume_wakeword(system, *outer)
    assert stream.active and system.wake_detector.running


def test_pause_says_true_only_when_it_stopped_a_running_stream():
    stream = Stream()
    rec = recorder(stream)
    assert rec.pause_recording() is True and rec.pause_recording() is False
    assert rec.resume_recording() is True and stream.active
    assert recorder(None).pause_recording() is False


def test_a_wake_turn_that_ended_while_held_gets_its_microphone_back(monkeypatch):
    monkeypatch.setattr(routes.time, 'sleep', lambda s: None)
    rec = recorder(None)                                 # the turn closed the stream and never reopened it
    opened = []

    def settled():
        opened.append(1)
        rec.stream = Stream()
        return True
    rec.start_recording_settled = settled
    det = Detector(rec, running=False)
    routes._resume_wakeword(types.SimpleNamespace(wake_word_recorder=rec, wake_detector=det), True, False)
    assert opened == [1] and det.running and det.starts == 2


def test_a_turn_still_running_reopens_its_own_microphone(monkeypatch):
    """start_listening re-armed the surviving thread (running is True): the resume opens nothing."""
    monkeypatch.setattr(routes.time, 'sleep', lambda s: None)
    rec = recorder(None)
    rec.start_recording_settled = lambda: (_ for _ in ()).throw(AssertionError("opened under a live turn"))
    det = Detector(rec, running=False)
    det.start_listening = lambda: setattr(det, 'running', True)
    routes._resume_wakeword(types.SimpleNamespace(wake_word_recorder=rec, wake_detector=det), True, False)
    assert det.running
