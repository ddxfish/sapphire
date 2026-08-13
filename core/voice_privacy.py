# core/voice_privacy.py — private-chat gates for the voice path (vault v1.1,
# ruling D 2026-08-09; plan tmp/vault-stt-gate-plan.md).
"""A private chat refuses cloud LLMs — but SPOKEN input rides STT before the
chat ever sees text, and responses ride TTS after. With a cloud provider on
either leg, voice leaks where typing wouldn't. These gates consult the
provider registries' existing `is_local` metadata (declared in code for core
providers, in the manifest for plugin ones).

Failure posture, deliberately asymmetric:
- Chat-settings resolution failing → gate OPEN (a systemic settings error
  would break far more than voice; don't mute the whole app for it).
- Provider metadata absent or unreadable in a PRIVATE chat → gate CLOSED
  (an unlabeled provider is the attack surface — treated as cloud).

No enable/disable toggle by design: allowing cloud voice in a private chat
is the exact contradiction the gate exists to stop.
"""
import logging

logger = logging.getLogger(__name__)


def _gate(chat_settings, get_registry, kind, what_leaks) -> str:
    try:
        if not (chat_settings or {}).get('private_chat'):
            return ''
    except Exception:
        return ''   # unreadable settings — systemic, fail open
    try:
        registry = get_registry()
        key = registry.get_active_key()
        if not key or key == 'none':
            return ''   # provider disabled — nothing to leak
        registry.get_all()   # triggers lazy core registration
        entry = registry.get_entry(key) or {}
        if entry.get('is_local', False):
            return ''
        return (f"Private chat — {kind} provider '{key}' is not local, so "
                f"{what_leaks} would leave this machine. Switch to a local "
                f"{kind} provider in Settings, or use text.")
    except Exception as e:
        logger.warning(f"[VOICE-PRIVACY] {kind} gate could not verify provider: {e}")
        return (f"Private chat — could not verify the {kind} provider is "
                f"local; voice is disabled (fail-closed).")


def stt_gate_reason(chat_settings) -> str:
    """Non-empty refusal string when this chat must not transcribe through
    the current STT provider. Empty string = proceed."""
    def _reg():
        from core.stt.providers import stt_registry
        return stt_registry
    return _gate(chat_settings, _reg, 'STT', 'your voice audio')


def tts_gate_reason(chat_settings=None) -> str:
    """Non-empty refusal string when this chat must not speak through the
    current TTS provider. chat_settings=None resolves the EFFECTIVE chat
    (stream-brain aware) — lets the TTS client self-gate with no caller
    knowledge. Empty string = proceed."""
    if chat_settings is None:
        try:
            from core.api_fastapi import get_system
            chat_settings = get_system().llm_chat.session_manager.get_chat_settings()
        except Exception:
            return ''   # no system yet (boot/tests) — don't mute
    def _reg():
        from core.tts.providers import tts_registry
        return tts_registry
    return _gate(chat_settings, _reg, 'TTS', "the response text")
