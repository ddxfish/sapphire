# core/devices/audio_codecs.py - encode fitted audio to the codec a satellite
# says it decodes (/health `plays.codec`).
#
# One registry, so supporting another codec is one entry here and nothing in the
# delivery path. Today: `pcm` (plain 16-bit WAV, what every board has always
# got and the default) and `ima_adpcm` (IMA ADPCM WAV, ~4:1, for a board on a
# congested link where 16-bit PCM's 256 kbps does not fit). `opus` would slot in
# the same way - a function that returns (ogg_bytes, 'audio/ogg').
#
# An encoder takes the fitted PCM as a float32 array shaped (frames, channels),
# the sample rate and channel count, and returns (body_bytes, content_type).
import io


def _wav(subtype):
    """A WAV encoder for the given libsndfile subtype (PCM_16, IMA_ADPCM, ...)."""
    def enc(samples, rate, channels):
        import soundfile as sf
        out = io.BytesIO()
        sf.write(out, samples, rate, format='WAV', subtype=subtype)
        return out.getvalue(), 'audio/wav'
    return enc


# def _opus(samples, rate, channels):           # the shape a future codec takes
#     return ogg_bytes, 'audio/ogg'

_CODECS = {
    'pcm': _wav('PCM_16'),
    'ima_adpcm': _wav('IMA_ADPCM'),
    # 'opus': _opus,
}


def codecs():
    """Every codec name the core can encode to."""
    return set(_CODECS)


def normalize(codec):
    """A stated codec as we keep it: a known name lowercased, else 'pcm'. Every
    board plays plain PCM, so an unknown codec falls back to it safely."""
    c = str(codec or 'pcm').lower()
    return c if c in _CODECS else 'pcm'


def encode(samples, rate, channels, codec):
    """(body_bytes, content_type) for the PCM `samples` in the stated codec."""
    return _CODECS[normalize(codec)](samples, rate, channels)


# ── the stream door (/face/packet): bare samples, no container ──────────────────
# A packet's voice is a second of her at a time with its frames beside it, so it goes
# bare: `pcm` = int16 little-endian, `ima_adpcm` = the WAV's IMA blocks as they are
# (mono: int16 first sample, u8 step index, u8 0, then nibbles, low first), the last
# block cut to the samples it holds rather than padded with silence. Every block starts
# afresh, so a packet decodes on its own. Returns (bytes, block_align); 0 for pcm.

def _riff(data, want):
    """The body of chunk `want` in a RIFF WAV."""
    import struct
    i = 12
    while i + 8 <= len(data):
        tag, n = data[i:i + 4], struct.unpack('<I', data[i + 4:i + 8])[0]
        if tag == want:
            return data[i + 8:i + 8 + n]
        i += 8 + n + (n & 1)
    raise ValueError(f"no {want!r} chunk")


def encode_stream(samples, rate, codec):
    """(bytes, block_align) for mono float32 `samples` at `rate` in `codec`."""
    import struct
    import numpy as np
    c = normalize(codec)
    if c == 'pcm':
        return (np.clip(samples, -1.0, 1.0) * 32767).astype('<i2').tobytes(), 0
    if c != 'ima_adpcm':
        raise ValueError(f"{c} has no stream form")
    n = len(samples)
    if not n:
        return b'', 0
    wav, _ = _CODECS[c](np.asarray(samples, dtype=np.float32).reshape(-1, 1), rate, 1)
    block = struct.unpack('<H', _riff(wav, b'fmt ')[12:14])[0]
    per = (block - 4) * 2 + 1                     # samples a mono block holds: the header's one + two a byte
    blocks = -(-n // per)
    last = n - (blocks - 1) * per
    body = _riff(wav, b'data')[:(blocks - 1) * block + 4 + (last - 1 + 1) // 2]
    return body, block

