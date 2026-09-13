"""H17 (hunt 2026-09-12): the batch "speak" lane decodes TTS blobs in-process
and feeds the PCM queue — no ffmpeg subprocess, no temp file. Plus the M26
log-privacy tripwire: what people say in a voice channel never hits INFO."""

import asyncio
import io
import re
import sys
import tempfile
import types
import wave
from pathlib import Path

import pytest

from plugins.discord.transport.discord_audio import DISCORD_SAMPLE_RATE
from plugins.discord.transport.discord_execution import DiscordExecution
from plugins.discord.transport.discord_streaming_playback import DISCORD_FRAME_BYTES
from plugins.discord.transport.discord_tts_chunks import decode_tts_audio

PLUGIN = Path(__file__).resolve().parents[1]

np = pytest.importorskip('numpy')
sf = pytest.importorskip('soundfile')


def _tone(sample_rate: int, seconds: float):
    return (np.sin(np.linspace(0, 440 * 2 * np.pi * seconds, int(sample_rate * seconds))) * 0.5).astype('float32')


def _wav_bytes(sample_rate: int = 24000, seconds: float = 0.5) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes((_tone(sample_rate, seconds) * 32767).astype('<i2').tobytes())
    return buffer.getvalue()


def _expected_pcm_bytes(seconds: float) -> int:
    return int(DISCORD_SAMPLE_RATE * seconds) * 2 * 2  # stereo s16 at 48 kHz


# ── decoder ──────────────────────────────────────────────────────────────

def test_decode_tts_audio_wav_resamples_to_discord_stereo():
    pcm = decode_tts_audio(_wav_bytes(24000, 0.5), label='wav')
    assert abs(len(pcm) - _expected_pcm_bytes(0.5)) <= 4 * 4  # resampler edge rounding


def test_decode_tts_audio_reads_ogg_opus_like_core_pitch_shift_emits():
    # core tts_client re-encodes pitch-shifted audio as OGG/OPUS — the one
    # container the old wave-only helpers could never read.
    buffer = io.BytesIO()
    sf.write(buffer, _tone(24000, 0.5), 24000, format='OGG', subtype='OPUS')
    pcm = decode_tts_audio(buffer.getvalue(), label='ogg')
    assert len(pcm) >= _expected_pcm_bytes(0.4)
    assert len(pcm) % 4 == 0


def test_decode_tts_audio_garbage_and_empty_return_empty():
    assert decode_tts_audio(b'') == b''
    assert decode_tts_audio(b'not audio at all' * 20, label='wav') == b''


# ── batch lane ───────────────────────────────────────────────────────────

class FakeVoiceClient:
    def __init__(self):
        self.played = []

    def is_playing(self):
        return False

    def play(self, source, after=None):
        self.played.append((source, after))


def _execution(monkeypatch, voice_client):
    execution = DiscordExecution(transport=None)

    async def _resolve(account_name, channel_id):
        return 'alpha', voice_client

    async def _dave_ready(_voice_client):
        return {'is_dave': False, 'dave_ready': True}

    import plugins.discord.voice.dave_session as dave_session

    monkeypatch.setattr(execution, '_voice_client_for_channel', _resolve)
    monkeypatch.setattr(dave_session, 'wait_for_dave_ready', _dave_ready)
    # Under pytest `discord` resolves to this plugin package; QueuedPCMSource
    # only needs a base class to subclass.
    monkeypatch.setitem(sys.modules, 'discord', types.SimpleNamespace(AudioSource=type('AudioSource', (), {})))

    def _no_temp_files(*args, **kwargs):
        raise AssertionError('batch playback must never write a temp file')

    monkeypatch.setattr(tempfile, 'mkstemp', _no_temp_files)
    monkeypatch.setattr(tempfile, 'NamedTemporaryFile', _no_temp_files)
    return execution


def test_play_voice_audio_feeds_pcm_queue_without_a_file(monkeypatch):
    voice_client = FakeVoiceClient()
    execution = _execution(monkeypatch, voice_client)

    result = asyncio.run(execution.play_voice_audio('alpha', '123', _wav_bytes(24000, 0.5), audio_format='wav'))

    assert result['status'] == 'playing'
    assert abs(result['pcm_bytes'] - _expected_pcm_bytes(0.5)) <= 16
    assert len(voice_client.played) == 1
    source, _after = voice_client.played[0]
    frames = 0
    while True:
        frame = source.read()
        if not frame:
            break
        assert len(frame) == DISCORD_FRAME_BYTES
        frames += 1
    assert 24 <= frames <= 26  # 0.5 s of 20 ms frames


def test_play_voice_audio_reports_undecodable_blob_instead_of_playing_silence(monkeypatch):
    voice_client = FakeVoiceClient()
    execution = _execution(monkeypatch, voice_client)

    result = asyncio.run(execution.play_voice_audio('alpha', '123', b'\x00garbage' * 50, audio_format='mp3'))

    assert result['status'] == 'error'
    assert 'mp3' in result['error']
    assert voice_client.played == []


def test_play_voice_audio_replaces_a_live_streaming_session(monkeypatch):
    from plugins.discord.transport.discord_streaming_playback import StreamingVoicePlayback

    voice_client = FakeVoiceClient()
    execution = _execution(monkeypatch, voice_client)
    old = StreamingVoicePlayback()
    old.start()
    execution._streaming_playback[('alpha', '123')] = old

    asyncio.run(execution.play_voice_audio('alpha', '123', _wav_bytes(24000, 0.2)))

    assert ('alpha', '123') not in execution._streaming_playback
    assert old._stopped is True


# ── tripwires ────────────────────────────────────────────────────────────

def test_no_ffmpeg_or_temp_file_playback_in_plugin_code():
    execution_src = (PLUGIN / 'transport' / 'discord_execution.py').read_text(encoding='utf-8')
    audio_src = (PLUGIN / 'transport' / 'discord_audio.py').read_text(encoding='utf-8')
    assert 'FFmpegPCMAudio(' not in execution_src
    assert '_playback_paths' not in execution_src
    assert 'write_playback_file' not in execution_src
    assert 'write_playback_file' not in audio_src
    for path in PLUGIN.rglob('*.py'):
        if 'tests' in path.parts:
            continue
        assert 'FFmpegPCMAudio(' not in path.read_text(encoding='utf-8'), path


_TEXT_AT_INFO = re.compile(
    r"logger\.info\((?:[^()]|\([^()]*\))*?\b(?:text|raw|user_text|reply|spoken)\[:",
    re.S,
)


def test_voice_text_never_logs_at_info():
    # M26: transcripts and her replies are DEBUG; INFO carries counts/status.
    files = list((PLUGIN / 'voice').glob('*.py')) + [PLUGIN / 'sapphire' / 'speech_bridge.py']
    offenders = [str(path) for path in files if _TEXT_AT_INFO.search(path.read_text(encoding='utf-8'))]
    assert offenders == []


def test_wait_streaming_playback_gives_up_when_nobody_pulls_frames(monkeypatch):
    from plugins.discord.transport.discord_streaming_playback import StreamingVoicePlayback

    voice_client = FakeVoiceClient()                  # is_playing() is always False
    execution = _execution(monkeypatch, voice_client)
    playback = StreamingVoicePlayback()
    playback.start()
    playback.feed(b'\x01' * 100)
    playback.finish()
    execution._streaming_playback[('alpha', '123')] = playback

    result = asyncio.run(execution.wait_streaming_playback('alpha', '123', timeout=30.0))

    assert result['status'] == 'not_playing'


def test_wait_streaming_playback_reports_drained_when_the_player_pulls(monkeypatch):
    from plugins.discord.transport.discord_streaming_playback import StreamingVoicePlayback

    voice_client = FakeVoiceClient()
    execution = _execution(monkeypatch, voice_client)
    playback = StreamingVoicePlayback()
    playback.start()
    playback.feed(b'\x01' * 100)
    playback.finish()
    while playback.read_frame():                     # a player pulling the queue
        pass
    execution._streaming_playback[('alpha', '123')] = playback

    result = asyncio.run(execution.wait_streaming_playback('alpha', '123', timeout=5.0))

    assert result['status'] == 'drained'
