import base64
import io
import struct
import wave

from plugins.discord.transport.discord_audio import DISCORD_SAMPLE_RATE
from plugins.discord.transport.discord_streaming_playback import (
    DISCORD_FRAME_BYTES,
    StreamingVoicePlayback,
)
from plugins.discord.transport.discord_tts_chunks import decode_tts_chunk, pcm_to_discord_stereo


def _wav_chunk(sample_rate: int, samples) -> dict:
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(samples.astype('<i2').tobytes())
    return {
        'audio_b64': base64.b64encode(buffer.getvalue()).decode('ascii'),
        'content_type': 'audio/wav',
    }


def test_pcm_to_discord_stereo_doubles_channels():
    mono = struct.pack('<2h', 1000, -1000)
    stereo = pcm_to_discord_stereo(mono, sample_rate=DISCORD_SAMPLE_RATE)
    assert len(stereo) == len(mono) * 2


def test_decode_tts_chunk_returns_stereo_pcm():
    np = __import__('pytest').importorskip('numpy')
    __import__('pytest').importorskip('soundfile')
    tone = (np.sin(np.linspace(0, 4 * np.pi, 4800)) * 16000).astype('<i2')
    chunk = _wav_chunk(DISCORD_SAMPLE_RATE, tone)
    pcm = decode_tts_chunk(chunk)
    assert len(pcm) > 0
    assert len(pcm) >= DISCORD_FRAME_BYTES // 2


def test_decode_tts_chunk_invalid_base64_returns_empty():
    assert decode_tts_chunk({'audio_b64': '%%%'}) == b''


def test_decode_tts_chunk_missing_audio_returns_empty():
    assert decode_tts_chunk({}) == b''


def test_streaming_playback_read_returns_frame_size():
    playback = StreamingVoicePlayback()
    playback.feed(b'\x01\x02' * (DISCORD_FRAME_BYTES // 2))
    frame = playback.read_frame()
    assert len(frame) == DISCORD_FRAME_BYTES


def test_streaming_playback_stop_clears_pending():
    playback = StreamingVoicePlayback()
    playback.feed(b'\xff' * DISCORD_FRAME_BYTES * 3)
    playback.stop()
    assert playback.read_frame() == b''
    assert playback.pending_bytes() == 0


def test_streaming_playback_wait_drains_after_finish():
    playback = StreamingVoicePlayback()
    playback.feed(b'\x11' * DISCORD_FRAME_BYTES)
    playback.finish()

    def _drain():
        while True:
            frame = playback.read_frame()
            if not frame:
                break

    _drain()
    playback.wait(timeout=1.0)
