import io
import os
import struct
import wave

import numpy as np

from plugins.discord.transport.discord_audio import (
    DISCORD_SAMPLE_RATE,
    WHISPER_SAMPLE_RATE,
    _resample_int16,
    concat_wav_bytes,
    pcm_stereo_to_whisper_wav_bytes,
    prepare_discord_wav_for_stt,
)


def test_concat_wav_bytes_joins_audio():
    first = pcm_stereo_to_whisper_wav_bytes(b'\x00\x01' * 800)
    second = pcm_stereo_to_whisper_wav_bytes(b'\x02\x03' * 400)
    merged = concat_wav_bytes(first, second)
    assert len(merged) > len(first)
    assert merged[:4] == b'RIFF'


def _sine_pcm(freq_hz: float, duration_s: float = 1.0, sample_rate: int = DISCORD_SAMPLE_RATE, amplitude: float = 0.5) -> bytes:
    n = int(duration_s * sample_rate)
    t = np.linspace(0, duration_s, n, endpoint=False, dtype=np.float64)
    wave = (amplitude * 32767 * np.sin(2 * np.pi * freq_hz * t)).astype('<i2')
    return wave.tobytes()


def test_resample_48k_to_16k_preserves_length_and_low_freq():
    mono = _sine_pcm(1000)
    out = _resample_int16(mono, DISCORD_SAMPLE_RATE, WHISPER_SAMPLE_RATE)
    out_samples = len(out) // 2
    assert 15900 <= out_samples <= 16100, f'expected ~16000, got {out_samples}'
    arr = np.frombuffer(out, dtype='<i2').astype(np.float64)
    fft = np.abs(np.fft.rfft(arr))
    freqs = np.fft.rfftfreq(len(arr), 1 / WHISPER_SAMPLE_RATE)
    peak = freqs[int(np.argmax(fft))]
    assert 950 <= peak <= 1050, f'1 kHz sine shifted to {peak} Hz'


def test_repair_short_pcm_gaps_fills_brief_silence():
    from plugins.discord.transport.discord_audio import OPUS_FRAME_SAMPLES, repair_short_pcm_gaps

    frame_bytes = OPUS_FRAME_SAMPLES * 2 * 2
    loud = struct.pack('<h', 5000) * 2 * OPUS_FRAME_SAMPLES
    silent = b'\x00\x00' * 2 * OPUS_FRAME_SAMPLES
    pcm = loud + silent + loud
    repaired = repair_short_pcm_gaps(pcm, max_gap_frames=2)
    gap_start = frame_bytes
    gap = repaired[gap_start:gap_start + frame_bytes]
    gap_rms = float(np.sqrt(np.mean(np.frombuffer(gap, dtype='<i2').astype(np.float64) ** 2)))
    assert gap_rms > 100.0


def test_pcm_stereo_rms_detects_silence_and_speech():
    from plugins.discord.transport.discord_audio import pcm_stereo_rms

    silent = b'\x00\x00' * 200
    loud = struct.pack('<200h', *([5000] * 200))
    assert pcm_stereo_rms(silent) < 50
    assert pcm_stereo_rms(loud) > 1000


def test_resample_48k_to_16k_attenuates_above_nyquist():
    mono = _sine_pcm(12000, amplitude=0.5)
    out = _resample_int16(mono, DISCORD_SAMPLE_RATE, WHISPER_SAMPLE_RATE)
    arr = np.frombuffer(out, dtype='<i2').astype(np.float64) / 32768.0
    rms_16k = float(np.sqrt(np.mean(arr ** 2)))
    assert rms_16k < 0.02, f'12 kHz tone not attenuated, rms={rms_16k}'


def test_prepare_discord_wav_boosts_quiet_lead_in():
    pytest = __import__('pytest')
    sf = pytest.importorskip('soundfile')
    sample_rate = 16000
    lead = np.full(int(sample_rate * 0.8), 0.02, dtype=np.float64)
    tail = np.full(int(sample_rate * 1.2), 0.35, dtype=np.float64)
    audio = np.concatenate([lead, tail])
    fd, source_path = __import__('tempfile').mkstemp(suffix='.wav')
    os.close(fd)
    prepared_path = None
    try:
        sf.write(source_path, audio, sample_rate)
        prepared_path = prepare_discord_wav_for_stt(source_path)
        prepared, _ = sf.read(prepared_path)
        split = max(1, int(len(prepared) * 0.45))
        lead_rms = float(np.sqrt(np.mean(prepared[:split] ** 2)))
        tail_rms = float(np.sqrt(np.mean(prepared[split:] ** 2)))
        assert lead_rms >= tail_rms * 0.35
    finally:
        for path in (source_path, prepared_path):
            if path and os.path.exists(path):
                os.unlink(path)
