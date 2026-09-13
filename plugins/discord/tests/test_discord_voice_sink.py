import struct

import pytest

from plugins.discord.transport.discord_voice_sink import _extract_pcm_and_user


class FakeVoiceData:
    def __init__(self, pcm, source=None):
        self.pcm = pcm
        self.source = source


class FakeUser:
    def __init__(self, user_id, name='alice'):
        self.id = user_id
        self.name = name
        self.display_name = name


def test_pcm_stereo_mostly_silent_detects_sparse_blip():
    from plugins.discord.transport.discord_audio import (
        DISCORD_CHANNELS,
        DISCORD_SAMPLE_WIDTH,
        OPUS_FRAME_SAMPLES,
        pcm_stereo_mostly_silent,
    )

    frame_bytes = OPUS_FRAME_SAMPLES * DISCORD_SAMPLE_WIDTH * DISCORD_CHANNELS
    silent = b'\x00\x00' * (OPUS_FRAME_SAMPLES * DISCORD_CHANNELS)
    sample_count = OPUS_FRAME_SAMPLES * DISCORD_CHANNELS
    voiced = struct.pack(f'<{sample_count}h', *([2000] * sample_count))
    pcm = silent * 16 + voiced + voiced + silent * 8
    assert len(pcm) == frame_bytes * 26
    assert pcm_stereo_mostly_silent(pcm) is True


def test_pcm_stereo_mostly_silent_accepts_real_speech():
    from plugins.discord.transport.discord_audio import (
        DISCORD_CHANNELS,
        OPUS_FRAME_SAMPLES,
        pcm_stereo_mostly_silent,
    )

    sample_count = OPUS_FRAME_SAMPLES * DISCORD_CHANNELS
    voiced = struct.pack(f'<{sample_count}h', *([2500] * sample_count))
    pcm = voiced * 20
    assert pcm_stereo_mostly_silent(pcm) is False


def test_extract_pcm_from_voice_data():
    user = FakeUser(42)
    pcm, speaker, ssrc = _extract_pcm_and_user(FakeVoiceData(b'\x01\x02', user), None)
    assert pcm == b'\x01\x02'
    assert speaker is user
    assert ssrc is None


def test_extract_pcm_from_raw_bytes():
    pcm, speaker, ssrc = _extract_pcm_and_user(b'\x03\x04', FakeUser(1))
    assert pcm == b'\x03\x04'
    assert speaker.id == 1
    assert ssrc is None


@pytest.mark.skipif(
    __import__('importlib').util.find_spec('discord.sinks') is None,
    reason='py-cord not installed',
)
def test_utterance_sink_instantiation():
    from plugins.discord.transport.discord_voice_sink import UtteranceVoiceSink

    sink = UtteranceVoiceSink(on_utterance=lambda *args: None, loop=None)
    assert sink.is_opus() is False
    assert sink.__sink_listeners__ == []
    assert list(sink.walk_children()) == []


@pytest.mark.skipif(
    __import__('importlib').util.find_spec('discord.sinks') is None,
    reason='py-cord not installed',
)
def test_utterance_sink_ignores_silence_until_speech():
    from plugins.discord.transport.discord_audio import SPEECH_RMS_THRESHOLD, pcm_stereo_rms
    from plugins.discord.transport.discord_voice_sink import UtteranceVoiceSink

    captured = []
    sink = UtteranceVoiceSink(on_utterance=lambda *a: captured.append(a), loop=None)
    user = FakeUser(99, 'Zeebie')
    silent = b'\x00\x00' * 400
    speech = struct.pack('<400h', *([4000] * 400))

    sink.write(silent, user)
    assert not sink._buffers[99]
    sink.write(speech, user)
    assert len(sink._buffers[99]) > 0
    assert sink._in_speech[99] is True
    assert pcm_stereo_rms(speech) >= SPEECH_RMS_THRESHOLD


@pytest.mark.skipif(
    __import__('importlib').util.find_spec('discord.sinks') is None,
    reason='py-cord not installed',
)
def test_prepend_history_does_not_duplicate_current_frame():
    from plugins.discord.transport.discord_voice_sink import UtteranceVoiceSink

    sink = UtteranceVoiceSink(on_utterance=lambda *a: None, loop=None)
    user_id = 7
    frame = b'\x01\x02' * 100
    sink._preroll[user_id].extend(frame)
    buffer = bytearray()
    sink._prepend_history(user_id, buffer, current_pcm=frame)
    assert buffer == b''


@pytest.mark.skipif(
    __import__('importlib').util.find_spec('discord.sinks') is None,
    reason='py-cord not installed',
)
def test_finalize_discards_mostly_silent_clip():
    from plugins.discord.transport.discord_audio import (
        DISCORD_CHANNELS,
        DISCORD_SAMPLE_RATE,
        DISCORD_SAMPLE_WIDTH,
        OPUS_FRAME_SAMPLES,
    )
    from plugins.discord.transport.discord_voice_sink import UtteranceVoiceSink

    captured = []
    sink = UtteranceVoiceSink(on_utterance=lambda *a: captured.append(a), loop=None)
    user_id = 12
    silent = b'\x00\x00' * (OPUS_FRAME_SAMPLES * DISCORD_CHANNELS)
    sample_count = OPUS_FRAME_SAMPLES * DISCORD_CHANNELS
    voiced = struct.pack(f'<{sample_count}h', *([2000] * sample_count))
    pcm = silent * 16 + voiced + voiced + silent * 8
    duration_s = len(pcm) / (DISCORD_SAMPLE_RATE * DISCORD_SAMPLE_WIDTH * DISCORD_CHANNELS)
    sink._emit_finalized_utterance(user_id, 'bonjaman', pcm, duration_s)
    assert not captured
    assert user_id not in sink._buffers


@pytest.mark.skipif(
    __import__('importlib').util.find_spec('discord.sinks') is None,
    reason='py-cord not installed',
)
def test_finalize_clears_preroll_so_next_utterance_does_not_replay():
    from plugins.discord.transport.discord_voice_sink import UtteranceVoiceSink

    sink = UtteranceVoiceSink(on_utterance=lambda *a: None, loop=None)
    user_id = 8
    sink._preroll[user_id].extend(b'\xaa\xbb' * 200)
    sink._reset_capture_history(user_id)
    assert not sink._preroll[user_id]


@pytest.mark.skipif(
    __import__('importlib').util.find_spec('discord.sinks') is None,
    reason='py-cord not installed',
)
def test_finalize_rearms_when_voice_is_recent():
    # H19 (hunt 2026-09-12): the early return left no timer, so a weak-signal
    # mic grew the buffer forever. Now it re-arms.
    import time
    from plugins.discord.transport.discord_voice_sink import UtteranceVoiceSink

    class FakeHandle:
        def cancel(self):
            pass

    class FakeLoop:
        def __init__(self):
            self.calls = []

        def call_later(self, delay, cb):
            self.calls.append(delay)
            return FakeHandle()

    loop = FakeLoop()
    sink = UtteranceVoiceSink(on_utterance=lambda *a: None, loop=loop)
    sink._buffers[7] = bytearray(b'\x01\x00' * 4800)
    sink._last_voice[7] = time.monotonic()

    sink._finalize_user(7)

    assert 7 in sink._buffers
    assert loop.calls == [sink.silence_seconds]


@pytest.mark.skipif(
    __import__('importlib').util.find_spec('discord.sinks') is None,
    reason='py-cord not installed',
)
def test_utterance_cap_forces_finalize():
    import time
    from plugins.discord.transport.discord_voice_sink import UtteranceVoiceSink, _MAX_UTTERANCE_BYTES

    sink = UtteranceVoiceSink(on_utterance=lambda *a: None, loop=None)
    sink._buffers[7] = bytearray(_MAX_UTTERANCE_BYTES)
    sink._last_voice[7] = time.monotonic()

    sink._enforce_utterance_cap(7)

    assert 7 not in sink._buffers


@pytest.mark.skipif(
    __import__('importlib').util.find_spec('discord.sinks') is None,
    reason='py-cord not installed',
)
def test_on_pcm_frame_sees_every_accepted_frame_including_silence():
    # The engine's barge hold resets on gaps — it must see the silences.
    from plugins.discord.transport.discord_voice_sink import UtteranceVoiceSink

    frames = []
    sink = UtteranceVoiceSink(
        on_utterance=lambda *a: None,
        loop=None,
        on_pcm_frame=lambda uid, pcm, rms, is_speech: frames.append((uid, rms, is_speech)),
    )
    user = FakeUser(7, 'Krem')
    sink.write(b'\x00\x00' * 400, user)
    sink.write(struct.pack('<400h', *([4000] * 400)), user)
    sink.write(b'\x00\x00' * 400, user)
    assert [f[2] for f in frames] == [False, True, False]
    assert all(f[0] == 7 for f in frames)
