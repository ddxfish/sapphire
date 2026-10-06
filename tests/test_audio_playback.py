# tests/test_audio_playback.py - core/audio/playback.py: a sound out of this
# machine's speakers. The device and the stream are faked; nothing is heard.
import io
import threading

import numpy as np
import pytest
import soundfile as sf

from core.audio import playback


class Refused(Exception):
    pass


class FakeStream:
    def __init__(self, world, samplerate):
        self.world, self.rate = world, samplerate
        if samplerate in world.refuse_open:
            raise Refused(f'rate {samplerate}')
        world.opened.append(samplerate)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def write(self, chunk):
        assert chunk.ndim == 2 and chunk.shape[1] == 1 and chunk.dtype == np.float32
        if self.world.break_after is not None and self.world.frames >= self.world.break_after:
            raise Refused('device went away')
        self.world.frames += len(chunk)
        if self.world.on_write:
            self.world.on_write()


@pytest.fixture
def world(monkeypatch):
    import core.audio
    import core.audio.backend as backend
    w = type('W', (), {})()
    w.opened, w.frames, w.refuse_open, w.break_after, w.on_write = [], 0, set(), None, None
    w.device = (3, 48000, 'Speakers')
    fake_sd = type('SD', (), {'PortAudioError': Refused,
                              'OutputStream': staticmethod(lambda samplerate, **k: FakeStream(w, samplerate))})
    monkeypatch.setattr(backend, 'sd', fake_sd)
    monkeypatch.setattr(core.audio, 'get_device_manager',
                        lambda: type('DM', (), {'find_output_device': staticmethod(lambda: w.device)})())
    return w


def wav(seconds=0.5, rate=16000, channels=1):
    data = np.zeros((int(seconds * rate), channels), dtype=np.float32)
    buf = io.BytesIO()
    sf.write(buf, data, rate, format='WAV')
    return buf.getvalue()


def test_bytes_play_to_the_end_at_their_own_rate(world):
    assert playback.play(wav(0.5, 16000)) == (True, '')
    assert world.opened == [16000] and world.frames == 8000


def test_a_path_plays_and_the_chime_that_ships_is_readable(world):
    from core.mcp_persona import DING
    assert playback.play(DING) == (True, '')
    assert world.frames > 0


def test_stereo_is_played_as_one_channel(world):
    assert playback.play(wav(0.2, 16000, channels=2)) == (True, '')
    assert world.frames == 3200


def test_what_cannot_be_read_and_what_is_empty_are_named(world):
    ok, why = playback.play(b'not a sound at all')
    assert not ok and 'could not be read' in why
    ok, why = playback.play(wav(0))
    assert not ok and 'empty' in why
    assert world.opened == []


def test_a_machine_with_no_output_says_so(world):
    world.device = (None, None, None)
    assert playback.play(wav()) == (False, 'This machine has no sound output.')


def test_a_device_that_refuses_the_sounds_rate_gets_its_own(world):
    world.refuse_open = {16000}
    assert playback.play(wav(0.5, 16000)) == (True, '')
    assert world.opened == [48000] and world.frames == 24000


def test_a_device_that_refuses_both_rates_is_a_failure_in_words(world):
    world.refuse_open = {16000, 48000}
    ok, why = playback.play(wav())
    assert not ok and "'Speakers' refused" in why


def test_a_sound_that_breaks_midway_is_never_played_twice(world):
    world.break_after = 1600
    ok, why = playback.play(wav(0.5, 16000))
    assert not ok and world.opened == [16000]


def test_stop_ends_the_sound_at_its_next_chunk(world):
    world.on_write = playback.stop
    assert playback.play(wav(1.0, 16000)) == (True, 'Stopped.')
    assert world.frames == 1600                    # one 100 ms chunk, then the stop landed
    world.on_write = None
    assert playback.play(wav(0.2, 16000)) == (True, '')    # an old stop does not eat the next sound


def test_a_second_sound_waits_its_turn_and_gives_up_in_words(world):
    inside, leave = threading.Event(), threading.Event()
    world.on_write = lambda: (inside.set(), leave.wait(5))
    first = threading.Thread(target=playback.play, args=(wav(0.1, 16000),))
    first.start()
    assert inside.wait(5)
    ok, why = playback.play(wav(), wait=0.05)
    assert not ok and 'stayed busy' in why
    leave.set()
    first.join(5)
    world.on_write = None
    assert playback.play(wav(0.1, 16000)) == (True, '')
