"""TTS voice validation with plugin-extensible provider registry.

Voice validation is now driven by a registry of `(provider_key, matcher,
default)` entries. Core registers kokoro + elevenlabs at import time;
plugin providers (qwen3-tts, f5-tts, etc.) register their own shapes
when the plugin loads. `validate_voice` catches cross-provider voice IDs
and substitutes the correct default — works for any registered provider,
not just the two hardcoded ones. 2026-04-21 refactor (Wolf-Claude finding).
"""
import config


class _VoiceRegistry:
    """Plugin-extensible map of provider_key → voice shape + default.

    Entries: {provider_key: {'matcher': callable(voice)->bool, 'default': str|callable}}

    Plugins call `voice_registry.register(...)` on load and `unregister(...)`
    on unload. Validation logic uses the registry to answer:
      - Does this voice belong to this provider?
      - Does it belong to a DIFFERENT registered provider (→ cross-contamination)?
      - Neither (→ unknown shape, passthrough for legacy compat)
    """

    def __init__(self):
        self._entries = {}

    def register(self, provider_key, matcher, default):
        """Register a provider's voice shape.

        matcher: callable(voice:str) -> bool
        default: str or callable() -> str — default voice for this provider.
        """
        self._entries[provider_key] = {'matcher': matcher, 'default': default}

    def unregister(self, provider_key):
        self._entries.pop(provider_key, None)

    def is_registered(self, provider_key):
        return provider_key in self._entries

    def matches(self, voice, provider_key):
        entry = self._entries.get(provider_key)
        if not entry:
            return False
        try:
            return bool(entry['matcher'](voice))
        except Exception:
            return False

    def default_for(self, provider_key):
        entry = self._entries.get(provider_key)
        if not entry:
            return ''
        d = entry['default']
        try:
            return d() if callable(d) else d
        except Exception:
            return ''

    def provider_for_voice(self, voice):
        """Reverse lookup — which registered provider claims this voice shape?
        Returns provider_key or None."""
        for key in self._entries:
            if self.matches(voice, key):
                return key
        return None


voice_registry = _VoiceRegistry()


# ─── Core provider matchers ──────────────────────────────────────────────────

def is_elevenlabs_voice(voice: str) -> bool:
    """Check if a voice string looks like an ElevenLabs voice ID (20+ alphanumeric).
    Kept as a module-level function because plugins import it directly."""
    return bool(voice) and len(voice) >= 20 and voice.isalnum()


def _is_kokoro_voice(voice: str) -> bool:
    """Kokoro voices use a 2-letter-lowercase locale prefix + underscore + name,
    e.g. af_heart, am_adam, bf_emma, jf_sakura. Permissive on locale so new
    Kokoro languages land without a code change."""
    if not voice or '_' not in voice:
        return False
    prefix, _, rest = voice.partition('_')
    return len(prefix) == 2 and prefix.isalpha() and prefix.islower() and bool(rest)


# Core registrations. Plugins add more via voice_registry.register() on load.
voice_registry.register(
    'elevenlabs',
    is_elevenlabs_voice,
    lambda: getattr(config, 'TTS_ELEVENLABS_VOICE_ID', '') or '21m00Tcm4TlvDq8ikWAM',
)
voice_registry.register(
    'kokoro',
    _is_kokoro_voice,
    'af_heart',
)


# ─── Public API (signatures unchanged) ──────────────────────────────────────

def default_voice(provider: str = None) -> str:
    """Return the default voice for a TTS provider."""
    if provider is None:
        provider = getattr(config, 'TTS_PROVIDER', 'none')
    return voice_registry.default_for(provider)


def validate_voice(voice: str, provider: str = None) -> str:
    """Detect voice/provider mismatch and substitute the correct default.

    Substitutes ONLY when the voice clearly belongs to a DIFFERENT registered
    provider (cross-contamination). Unknown shapes passthrough — preserves the
    pre-registry behavior of not punishing legacy or custom voices.

    Unknown providers (not registered — e.g. plugin hasn't loaded yet) also
    passthrough, so plugin voices aren't blocked during boot.
    """
    if not voice:
        return voice
    if provider is None:
        provider = getattr(config, 'TTS_PROVIDER', 'none')
    if not voice_registry.is_registered(provider):
        return voice
    if voice_registry.matches(voice, provider):
        return voice
    # Voice doesn't match requested provider — is it claimed by another?
    for other_key in list(voice_registry._entries):
        if other_key != provider and voice_registry.matches(voice, other_key):
            return voice_registry.default_for(provider)
    # Unknown shape — passthrough (legacy compat).
    return voice
