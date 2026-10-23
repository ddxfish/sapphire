"""TTS provider registry and factory."""
import logging

from .base import BaseTTSProvider
from core.provider_registry import BaseProviderRegistry

logger = logging.getLogger(__name__)


class TTSProviderRegistry(BaseProviderRegistry):
    """TTS provider registry — core + plugin providers."""

    def __init__(self):
        super().__init__('tts', 'TTS_PROVIDER')
        # Core providers
        from .null import NullTTSProvider
        # Guarded: a broken/missing provider module must degrade to
        # "provider unavailable", never kill boot (day-ruiner CRIT,
        # 2026-09-01: kokoro's `from core import net` made this registry
        # a boot chokepoint). Null stays unguarded — it IS the fallback.
        try:
            from .kokoro import KokoroTTSProvider
            self.register_core('kokoro', KokoroTTSProvider, 'Kokoro (Local)', is_local=True)
        except Exception as e:
            logger.error(f"[tts] core provider 'kokoro' unavailable — continuing without it: {e}")
        # Sapphire Router — managed-mode cloud TTS. sapphire_router.py existed
        # and carried a live provider but was never registered here, so
        # TTS_PROVIDER='sapphire_router' silently fell to null (mirror of the
        # STT H6 fix). Guarded like the others.
        try:
            from .sapphire_router import SapphireRouterTTSProvider
            self.register_core('sapphire_router', SapphireRouterTTSProvider,
                               'Sapphire Router (Managed)', is_local=False)
        except Exception as e:
            logger.error(f"[tts] core provider 'sapphire_router' unavailable: {e}")
        self.register_core('none', NullTTSProvider, 'None (disabled)', is_local=True)

    def create(self, key, **kwargs):
        """Create TTS provider — no constructor args needed."""
        entry = self._core.get(key) or self._plugins.get(key)
        if not entry:
            if key and key != 'none':
                logger.warning(f"[tts] Unknown provider '{key}', falling back to null")
            entry = self._core.get('none')
            if not entry:
                return None
        try:
            return entry['class']()
        except Exception as e:
            logger.error(f"[tts] Failed to create '{key}': {e}")
            from .null import NullTTSProvider
            return NullTTSProvider()


tts_registry = TTSProviderRegistry()


# Backward compat
def get_tts_provider(provider_name: str) -> BaseTTSProvider:
    """Create a TTS provider instance by name. Legacy wrapper."""
    return tts_registry.create(provider_name or 'none')
