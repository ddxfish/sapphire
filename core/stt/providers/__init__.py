"""STT provider registry and factory."""
import logging

from core.stt.providers.base import BaseSTTProvider
from core.provider_registry import BaseProviderRegistry

logger = logging.getLogger(__name__)


class STTProviderRegistry(BaseProviderRegistry):
    """STT provider registry — core + plugin providers."""

    def __init__(self):
        super().__init__('stt', 'STT_PROVIDER')
        # Defer core registration to avoid circular imports
        # (stt_null imports from providers.base which triggers this __init__)
        self._core_registered = False

    def _ensure_core(self):
        """Lazy-register core providers on first access."""
        if self._core_registered:
            return
        self._core_registered = True
        from core.stt.providers.faster_whisper import FasterWhisperProvider
        from core.stt.providers.fireworks_whisper import FireworksWhisperProvider
        from core.stt.stt_null import NullWhisperClient
        self.register_core('faster_whisper', FasterWhisperProvider, 'Faster Whisper (Local)',
                          is_local=True)
        self.register_core('fireworks_whisper', FireworksWhisperProvider, 'Fireworks Whisper (Cloud)',
                          requires_api_key=True, api_key_env='STT_FIREWORKS_API_KEY')
        self.register_core('none', NullWhisperClient, 'None (disabled)', is_local=True)

    def get_all(self):
        self._ensure_core()
        return super().get_all()

    def get_keys(self):
        self._ensure_core()
        return super().get_keys()

    def create(self, key, **kwargs):
        """Create STT provider — no constructor args needed."""
        self._ensure_core()
        entry = self._core.get(key) or self._plugins.get(key)
        if not entry:
            if key and key != 'none':
                logger.warning(f"[stt] Unknown provider '{key}', falling back to null")
            entry = self._core.get('none')
            if not entry:
                return None
        try:
            return entry['class']()
        except Exception as e:
            logger.error(f"[stt] Failed to create '{key}': {e}")
            from core.stt.stt_null import NullWhisperClient
            return NullWhisperClient()


stt_registry = STTProviderRegistry()


# Backward compat
def get_stt_provider(provider_name: str) -> BaseSTTProvider:
    """Create an STT provider instance by name. Legacy wrapper."""
    return stt_registry.create(provider_name or 'none')
