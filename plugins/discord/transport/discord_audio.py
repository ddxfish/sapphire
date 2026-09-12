"""Audio format helpers for Discord voice (48 kHz stereo PCM)."""

from __future__ import annotations

import io
import logging
import os
import struct
import tempfile
import wave

logger = logging.getLogger(__name__)

DISCORD_SAMPLE_RATE = 48000
DISCORD_CHANNELS = 2
DISCORD_SAMPLE_WIDTH = 2
WHISPER_SAMPLE_RATE = 16000
PREROLL_SECONDS = 0.5
SPEECH_RMS_THRESHOLD = 55
SPEECH_CONTINUE_RATIO = 0.35
SPEECH_WEAK_FLOOR = 28
# Discard utterances when this fraction of 20ms frames sit below SPEECH_WEAK_FLOOR.
MOSTLY_SILENT_FRAME_RATIO = 0.70
# Normalized peak below this on sub-1.2s clips is treated as decode glitch, not speech.
LOW_ENERGY_DISCARD_PEAK = 0.06
# Peak int16 sample above this is treated as decrypt garbage (not speech).
SATURATED_PCM_PEAK = 30000
GAP_FILL_MAX_FRAMES = 4
# Opus voice frames are 20 ms at 48 kHz (960 samples/channel).
OPUS_FRAME_SAMPLES = 960


def _read_wav_pcm(wav_bytes: bytes) -> tuple[bytes, int, int]:
    buffer = io.BytesIO(wav_bytes)
    with wave.open(buffer, 'rb') as wav_file:
        channels = wav_file.getnchannels()
        sample_rate = wav_file.getframerate()
        frames = wav_file.readframes(wav_file.getnframes())
    return frames, sample_rate, channels


def wav_bytes_to_whisper_pcm_mono(wav_bytes: bytes) -> bytes:
    """Decode WAV bytes to 16 kHz mono int16 PCM for conversation turns."""
    if not wav_bytes:
        return b''
    pcm, sample_rate, channels = _read_wav_pcm(wav_bytes)
    if channels == 2:
        pcm = _stereo_to_mono_int16(pcm)
    if sample_rate != WHISPER_SAMPLE_RATE:
        pcm = _resample_int16(pcm, sample_rate, WHISPER_SAMPLE_RATE)
    return pcm


def concat_wav_bytes(left: bytes, right: bytes) -> bytes:
    """Concatenate two mono/stereo WAV blobs with matching format."""
    if not left:
        return right
    if not right:
        return left
    left_pcm, left_rate, left_channels = _read_wav_pcm(left)
    right_pcm, right_rate, right_channels = _read_wav_pcm(right)
    if left_rate != right_rate or left_channels != right_channels:
        return right
    merged = left_pcm + right_pcm
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wav_file:
        wav_file.setnchannels(left_channels)
        wav_file.setsampwidth(DISCORD_SAMPLE_WIDTH)
        wav_file.setframerate(left_rate)
        wav_file.writeframes(merged)
    return buffer.getvalue()


def _stereo_to_mono_int16(pcm_stereo: bytes) -> bytes:
    count = len(pcm_stereo) // DISCORD_SAMPLE_WIDTH
    if count < 2:
        return b''
    samples = struct.unpack(f'<{count}h', pcm_stereo)
    mono = []
    for index in range(0, len(samples), DISCORD_CHANNELS):
        left = samples[index]
        right = samples[index + 1] if index + 1 < len(samples) else left
        mono.append(int((left + right) / 2))
    return struct.pack(f'<{len(mono)}h', *mono)


def _resample_int16(mono: bytes, from_rate: int, to_rate: int) -> bytes:
    """Resample mono int16 PCM with proper anti-aliasing.

    Uses scipy.signal.resample_poly when available (FIR anti-alias filter +
    polyphase decomposition) so spectral energy above the Nyquist of the
    target rate does not fold back as aliasing.  Falls back to the historical
    linear-interpolation path when scipy is missing.
    """
    if not mono:
        return b''
    if from_rate == to_rate:
        return mono
    try:
        import numpy as np
        from scipy.signal import resample_poly
    except ImportError:
        return _resample_int16_linear(mono, from_rate, to_rate)
    samples = np.frombuffer(mono, dtype='<i2').astype(np.float64)
    # resample_poly needs a coprime up/down ratio, which is the common case
    # for 48000 -> 16000 (3/1).  Reduce the fraction to avoid needlessly
    # large internal buffers.
    from math import gcd
    g = gcd(from_rate, to_rate)
    up, down = to_rate // g, from_rate // g
    resampled = resample_poly(samples, up, down)
    resampled = np.clip(resampled, -32768.0, 32767.0).astype('<i2')
    return resampled.tobytes()


def _resample_int16_linear(mono: bytes, from_rate: int, to_rate: int) -> bytes:
    if not mono:
        return b''
    if from_rate == to_rate:
        return mono
    samples = list(struct.unpack(f'<{len(mono) // DISCORD_SAMPLE_WIDTH}h', mono))
    out_count = max(1, int(len(samples) * to_rate / from_rate))
    if len(samples) == 1:
        return struct.pack(f'<{out_count}h', *([samples[0]] * out_count))
    resampled = []
    for index in range(out_count):
        position = index * (len(samples) - 1) / max(out_count - 1, 1)
        left = int(position)
        right = min(left + 1, len(samples) - 1)
        weight = position - left
        value = int(samples[left] * (1.0 - weight) + samples[right] * weight)
        resampled.append(max(-32768, min(32767, value)))
    return struct.pack(f'<{len(resampled)}h', *resampled)


def pcm_stereo_peak(pcm_stereo: bytes) -> int:
    """Peak absolute int16 sample across stereo PCM."""
    count = len(pcm_stereo) // DISCORD_SAMPLE_WIDTH
    if count < 1:
        return 0
    samples = struct.unpack(f'<{count}h', pcm_stereo)
    return max((abs(sample) for sample in samples), default=0)


def repair_short_pcm_gaps(
    pcm_stereo: bytes,
    *,
    rms_threshold: float = 45.0,
    max_gap_frames: int = GAP_FILL_MAX_FRAMES,
) -> bytes:
    """Interpolate brief decode-silence gaps between voiced Opus frames."""
    if not pcm_stereo:
        return pcm_stereo
    frame_bytes = OPUS_FRAME_SAMPLES * DISCORD_SAMPLE_WIDTH * DISCORD_CHANNELS
    if len(pcm_stereo) < frame_bytes * 3:
        return pcm_stereo
    try:
        import numpy as np
    except ImportError:
        return pcm_stereo

    samples = np.frombuffer(pcm_stereo, dtype='<i2')
    frame_samples = OPUS_FRAME_SAMPLES * DISCORD_CHANNELS
    usable = (len(samples) // frame_samples) * frame_samples
    if usable < frame_samples * 3:
        return pcm_stereo
    frames = samples[:usable].reshape(-1, OPUS_FRAME_SAMPLES, DISCORD_CHANNELS).astype(np.float64)
    n_frames = len(frames)

    def frame_rms(index: int) -> float:
        chunk = frames[index]
        return float(np.sqrt(np.mean(chunk ** 2)))

    silent = [frame_rms(index) < rms_threshold for index in range(n_frames)]
    index = 0
    while index < n_frames:
        if not silent[index]:
            index += 1
            continue
        gap_start = index
        while index < n_frames and silent[index]:
            index += 1
        gap_len = index - gap_start
        if 0 < gap_len <= max_gap_frames and gap_start > 0 and index < n_frames:
            left = frames[gap_start - 1]
            right = frames[index]
            for offset in range(gap_len):
                alpha = (offset + 1) / (gap_len + 1)
                frames[gap_start + offset] = left * (1.0 - alpha) + right * alpha
    return np.clip(frames, -32768, 32767).astype('<i2').reshape(-1).tobytes()


def pcm_stereo_rms(pcm_stereo: bytes) -> float:
    """RMS level for 48 kHz stereo int16 PCM (0 = silence)."""
    count = len(pcm_stereo) // DISCORD_SAMPLE_WIDTH
    if count < 1:
        return 0.0
    samples = struct.unpack(f'<{count}h', pcm_stereo)
    if not samples:
        return 0.0
    return float((sum(sample * sample for sample in samples) / len(samples)) ** 0.5)


def pcm_stereo_mostly_silent(
    pcm_stereo: bytes,
    *,
    rms_floor: float = SPEECH_WEAK_FLOOR,
    silent_ratio: float = MOSTLY_SILENT_FRAME_RATIO,
) -> bool:
    """True when most 20ms frames are below the speech weak floor."""
    if not pcm_stereo:
        return True
    frame_bytes = OPUS_FRAME_SAMPLES * DISCORD_SAMPLE_WIDTH * DISCORD_CHANNELS
    if len(pcm_stereo) < frame_bytes:
        return pcm_stereo_rms(pcm_stereo) < rms_floor
    silent_frames = 0
    total_frames = 0
    for offset in range(0, len(pcm_stereo) - frame_bytes + 1, frame_bytes):
        if pcm_stereo_rms(pcm_stereo[offset:offset + frame_bytes]) < rms_floor:
            silent_frames += 1
        total_frames += 1
    if total_frames < 1:
        return True
    return (silent_frames / total_frames) >= silent_ratio


def pcm_stereo_normalized_peak(pcm_stereo: bytes) -> float:
    peak = pcm_stereo_peak(pcm_stereo)
    return peak / 32768.0 if peak else 0.0


def pcm_stereo_has_repetitive_glitch(
    pcm_stereo: bytes,
    *,
    rms_floor: float = SPEECH_WEAK_FLOOR,
    repeat_ratio: float = 0.60,
) -> bool:
    """True when voiced frames share nearly identical energy (decode stutter)."""
    if not pcm_stereo:
        return False
    from collections import Counter

    frame_bytes = OPUS_FRAME_SAMPLES * DISCORD_SAMPLE_WIDTH * DISCORD_CHANNELS
    voiced = []
    for offset in range(0, len(pcm_stereo) - frame_bytes + 1, frame_bytes):
        rms = pcm_stereo_rms(pcm_stereo[offset:offset + frame_bytes])
        if rms >= rms_floor:
            voiced.append(round(rms, 0))
    if len(voiced) < 3:
        return False
    _value, count = Counter(voiced).most_common(1)[0]
    return count >= max(3, int(len(voiced) * repeat_ratio))


def pcm_stereo_to_wav_bytes(pcm_stereo: bytes, *, sample_rate: int = DISCORD_SAMPLE_RATE) -> bytes:
    """Wrap 48 kHz stereo 16-bit PCM in a WAV container."""
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wav_file:
        wav_file.setnchannels(DISCORD_CHANNELS)
        wav_file.setsampwidth(DISCORD_SAMPLE_WIDTH)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_stereo)
    return buffer.getvalue()


def pcm_stereo_to_whisper_wav_bytes(pcm_stereo: bytes, *, sample_rate: int = DISCORD_SAMPLE_RATE) -> bytes:
    """Convert Discord PCM to mono WAV for Whisper at native capture rate.

    faster-whisper resamples to 16 kHz internally. Keeping 48 kHz here avoids
    an extra lossy downsample and ensures debug WAVs play at the correct speed
    in players that assume Discord voice is 48 kHz.
    """
    if not pcm_stereo:
        return b''
    mono = _stereo_to_mono_int16(pcm_stereo)
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(DISCORD_SAMPLE_WIDTH)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(mono)
    return buffer.getvalue()


def prepare_discord_wav_for_stt(audio_path: str, *, lead_in_fraction: float = 0.35) -> str:
    """Normalize Discord utterance audio and boost a quiet lead-in before STT.

    Combined VC clips often have a softer opening (e.g. \"testing testing\") and a
    louder tail (\"1, 2, 3\"). Global peak normalization buries the lead-in;
    this balances levels so Whisper can hear the full phrase.
    """
    import numpy as np
    import soundfile as sf

    audio, sample_rate = sf.read(audio_path)
    if len(audio.shape) > 1:
        audio = audio.mean(axis=1)
    audio = np.asarray(audio, dtype=np.float64)

    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    if peak > 0.001:
        audio = audio / peak * 0.95

    rms = float(np.sqrt(np.mean(audio ** 2))) if len(audio) else 0.0
    target_rms = 0.12
    if 0.0005 < rms < target_rms:
        gain = min(6.0, target_rms / rms)
        audio = np.clip(audio * gain, -1.0, 1.0)
        rms = float(np.sqrt(np.mean(audio ** 2))) if len(audio) else 0.0
    if 0.001 < rms < 0.08:
        gain = min(4.0, 0.08 / rms)
        audio = np.clip(audio * gain, -1.0, 1.0)

    split = max(1, int(len(audio) * lead_in_fraction))
    if split < len(audio):
        lead = audio[:split]
        tail = audio[split:]
        lead_rms = float(np.sqrt(np.mean(lead ** 2))) if len(lead) else 0.0
        tail_rms = float(np.sqrt(np.mean(tail ** 2))) if len(tail) else 0.0
        if tail_rms > 0.01 and lead_rms < tail_rms * 0.45:
            gain = min(3.5, tail_rms / max(lead_rms, 1e-6))
            audio[:split] = np.clip(lead * gain, -1.0, 1.0)
            logger.debug(
                'Discord voice lead-in boosted %.1fx (lead_rms=%.4f tail_rms=%.4f)',
                gain,
                lead_rms,
                tail_rms,
            )

    fd, path = tempfile.mkstemp(suffix='.discord_stt.wav')
    os.close(fd)
    sf.write(path, audio, sample_rate)
    return path


def write_playback_file(audio_bytes: bytes, *, suffix: str = '.audio') -> str:
    """Write TTS bytes to a temp file for FFmpegPCMAudio playback."""
    if not audio_bytes:
        raise ValueError('audio_bytes is empty')
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    with open(path, 'wb') as handle:
        handle.write(audio_bytes)
    return path
