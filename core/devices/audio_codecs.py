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
