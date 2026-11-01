"""Base class for all STT providers."""
from abc import ABC, abstractmethod
from typing import Optional
import logging

from core.stt.hallucination import is_whisper_hallucination

logger = logging.getLogger(__name__)


class STTFailed(Exception):
    """The engine could not run, which is not the same as nobody speaking. The
    message is fit to show a person."""


def stt_failed(why) -> STTFailed:
    """One row for a failing engine, however many lanes hit it, and the error to
    raise. Every lane then says its own sentence (spoken, 503, ok=False...)."""
    try:
        from core import notify
        notify.notify("Speech-to-text failed", 'error', str(why), source='stt',
                      go={'view': 'settings', 'tab': 'stt'}, key='stt-down')
    except Exception:
        pass
    logger.error(f"[STT] {why}")
    return STTFailed(str(why))


class BaseSTTProvider(ABC):
    """Base interface for speech-to-text providers.

    Concrete providers implement `_transcribe_impl`. The public
    `transcribe_file` method on this base class wraps the impl and
    applies the Whisper hallucination filter — so wakeword, browser
    STT, and any future voice-mode consumer get filtered output for
    free without each one having to remember to call the filter.
    """

    def transcribe_file(self, audio_path: str) -> Optional[str]:
        """Transcribe an audio file and return the text (filtered).

        Returns None if the impl returned None/empty OR if the result
        was a known Whisper hallucination (case-insensitive match
        against silence/noise canned phrases). Callers should treat
        None as "no usable speech". An engine that could not run raises
        STTFailed instead: that is never silence.
        """
        text = self._transcribe_impl(audio_path)
        if is_whisper_hallucination(text):
            if text:
                logger.warning(
                    f"[STT] Filtered Whisper hallucination: {text!r}"
                )
            return None
        return text

    def transcribe_segments(self, audio_path: str, **params) -> list:
        """Segment-level transcription for consumers that run their own quality
        gates on per-segment confidence (the Discord voice lane: DAVE-degraded
        audio needs no_speech_prob / avg_logprob per segment). Returns objects
        with at least .text, .no_speech_prob and .avg_logprob. NOT hallucination-
        filtered — the caller decides. Providers with real segments override;
        this default wraps the plain transcription into one pseudo-segment so
        every provider answers the same shape."""
        from types import SimpleNamespace
        text = str(self._transcribe_impl(audio_path) or '').strip()
        return [SimpleNamespace(text=text, no_speech_prob=0.0, avg_logprob=0.0)] if text else []

    @abstractmethod
    def _transcribe_impl(self, audio_path: str) -> Optional[str]:
        """Provider-specific transcription. Return None / '' on no
        usable speech, raise stt_failed(why) when the engine failed. Do not apply the hallucination filter here —
        the base class handles it for every consumer."""
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this provider is ready to transcribe."""
        ...
