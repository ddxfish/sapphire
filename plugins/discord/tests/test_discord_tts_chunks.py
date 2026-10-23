import struct

from plugins.discord.transport.discord_audio import DISCORD_SAMPLE_RATE
from plugins.discord.transport.discord_tts_chunks import pcm_to_discord_stereo


def test_pcm_to_discord_stereo_resamples_to_48k():
    np = __import__('pytest').importorskip('numpy')

    samples = (np.sin(np.linspace(0, 2 * np.pi, 1600)) * 10000).astype('<i2')
    mono = samples.tobytes()
    stereo = pcm_to_discord_stereo(mono, sample_rate=16000)
    expected_samples = int(1600 * DISCORD_SAMPLE_RATE / 16000)
    assert len(stereo) == expected_samples * 2 * 2  # stereo s16


def test_pcm_to_discord_stereo_empty():
    assert pcm_to_discord_stereo(b'', sample_rate=DISCORD_SAMPLE_RATE) == b''


def test_mono_to_stereo_channel_count():
    mono = struct.pack('<3h', 1, 2, 3)
    stereo = pcm_to_discord_stereo(mono, sample_rate=DISCORD_SAMPLE_RATE)
    assert len(stereo) == len(mono) * 2
