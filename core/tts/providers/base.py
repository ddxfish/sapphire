"""Base class for all TTS providers."""
from abc import ABC, abstractmethod
from typing import Iterator, Optional


class BaseTTSProvider(ABC):
    """Base interface for text-to-speech providers.

    Providers handle audio generation only. Text processing, playback,
    hooks, and threading are handled by TTSClient.
    """

    # Subclasses override to declare their output format
    audio_content_type: str = 'audio/ogg'

    # Speed range — subclasses override for provider-specific limits
    SPEED_MIN: float = 0.5
    SPEED_MAX: float = 2.5

    # True if this provider has a real streaming implementation (not the
    # default wrap-generate fallback). Used by TTSClient to decide whether
    # to expect chunked semantics or just one-shot bytes.
    supports_streaming: bool = False
    # True when the provider applies pitch at synthesis time (pass pitch= as a
    # kwarg). When False, callers that want pitch fall back to the legacy
    # client-side decode→resample→re-encode — never both (double-shift).
    supports_pitch: bool = False

    @abstractmethod
    def generate(self, text: str, voice: str, speed: float, **kwargs) -> Optional[bytes]:
        """Generate audio bytes from text.

        Args:
            text: Cleaned text ready for synthesis.
            voice: Voice identifier (provider-specific).
            speed: Playback speed multiplier.

        Returns:
            Audio bytes in the provider's native format, or None on failure.
        """
        ...

    def generate_stream(self, text: str, voice: str, speed: float, **kwargs) -> Iterator[bytes]:
        """Yield audio chunks as they're produced. Each yielded chunk is a
        self-contained, independently-decodable audio blob (e.g., one OGG
        file). Order matters — chunks must play in yield order.

        Default implementation calls generate() once and yields the whole
        blob — backwards-compat for providers without a true streaming API.
        Providers with real streaming APIs override + set
        supports_streaming=True.

        Returns:
            Iterator yielding audio bytes. Empty iterator on failure.
        """
        audio = self.generate(text, voice, speed, **kwargs)
        if audio:
            yield audio

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this provider is ready to generate audio."""
        ...
