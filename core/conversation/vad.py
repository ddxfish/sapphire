"""Per-frame speech/silence gate for conversation mode (v3 Rollout 2b).

Wraps silero VAD (512-sample 16k chunks) into a simple `is_speech(chunk) -> bool`.
`score_fn` is injectable so the gate is unit-testable without loading the model.

Hysteresis (2026-07-15): entering speech requires `threshold`; STAYING in speech
only requires `exit_threshold` (default: threshold − 0.15). Brief mid-word score
dips no longer flicker the gate to silence, which is what makes a raised
threshold (phone calls, noisy rooms) usable without chopping real speech.
"""
import logging

logger = logging.getLogger(__name__)


class SpeechGate:
    def __init__(self, sample_rate=16000, threshold=None, score_fn=None,
                 exit_threshold=None):
        self.sample_rate = sample_rate
        if threshold is None:
            try:
                import config as _cfg
                threshold = float(getattr(_cfg, "STT_VAD_SPEECH_THRESHOLD", 0.5))
            except Exception:
                threshold = 0.5
        self.threshold = threshold
        if exit_threshold is None:
            exit_threshold = max(0.15, threshold - 0.15)
        self.exit_threshold = min(float(exit_threshold), threshold)
        self._in_speech = False
        self._vad = None
        if score_fn is not None:
            self._score = score_fn
        else:
            from core.stt.silero_vad import SileroVAD
            self._vad = SileroVAD(sample_rate=16000)
            self._score = self._vad.score_chunk

    def is_speech(self, chunk_int16):
        """chunk_int16: 512-sample int16 frame @ 16k. Returns True if speech."""
        try:
            score = float(self._score(chunk_int16))
        except Exception as e:
            logger.debug(f"[CONV] VAD score failed: {e}")
            self._in_speech = False
            return False
        gate = self.exit_threshold if self._in_speech else self.threshold
        self._in_speech = score >= gate
        return self._in_speech

    def reset(self):
        self._in_speech = False
        if self._vad is not None:
            self._vad.reset()
