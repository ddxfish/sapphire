"""core/stt/utterance.py — the audio of one utterance, shown to plugins BEFORE
it is transcribed (voice tone, 2026-11-07).

Every STT lane calls heard() with the audio it is about to hand to Whisper.
heard() normalizes it once to 16 kHz mono float32 in [-1, 1] and fires the
`stt_audio` hook. Handlers must return fast and do their analysis on their
own thread: that is what lets it run alongside transcription instead of
after it. The returned id rides the same lane's `post_stt` event
(metadata['utterance']) so a plugin can pair its reading with the final
words.

Costs nothing when no plugin listens: no decode, no resample, no id.
Never raises: a bad clip must not cost the user their turn.
"""
import io
import logging
import uuid

import numpy as np

logger = logging.getLogger(__name__)

RATE = 16000


def _decode(audio, rate):
    """(float32 mono at its own rate, rate) from a path, encoded file bytes,
    or raw int16 PCM bytes (rate given)."""
    if isinstance(audio, (bytes, bytearray, memoryview)) and rate:
        return np.frombuffer(bytes(audio), dtype='<i2').astype(np.float32) / 32768.0, int(rate)
    import soundfile as sf
    src = io.BytesIO(bytes(audio)) if isinstance(audio, (bytes, bytearray, memoryview)) else str(audio)
    data, sr = sf.read(src, dtype='float32', always_2d=True)
    return data.mean(axis=1), int(sr)


def to_16k(audio, rate=None):
    a, sr = _decode(audio, rate)
    if sr != RATE:
        from math import gcd
        from scipy.signal import resample_poly
        g = gcd(sr, RATE)
        a = resample_poly(a, RATE // g, sr // g).astype(np.float32)
    return np.ascontiguousarray(a, dtype=np.float32)


def heard(audio, *, rate=None, chat=None, lane=''):
    """Fire `stt_audio` for this utterance. Returns its id, or None when no
    plugin listens or the audio could not be read.

    audio: a file path, encoded file bytes (wav/ogg/...), or raw int16 mono
           PCM bytes with `rate`.
    chat:  the chat this turn will land in, when the lane knows it. Stamped
           on the event so privacy is resolved for THAT chat; None = the
           runner resolves the active chat.
    lane:  wake | browser | conversation | call | device — plugins choose
           which voices they read (a call carries someone else's).
    """
    try:
        from core.hooks import hook_runner, HookEvent
        if not hook_runner.has_handlers("stt_audio"):
            return None
        pcm = to_16k(audio, rate)
        if not pcm.size:
            return None
        uid = uuid.uuid4().hex[:12]
        event = HookEvent(metadata={"audio": pcm, "sample_rate": RATE,
                                    "utterance": uid, "lane": lane})
        if chat:
            event.chat_name = chat
        hook_runner.fire("stt_audio", event)
        return uid
    except Exception as e:
        logger.warning(f"[STT] stt_audio skipped ({lane}): {e}")
        return None
