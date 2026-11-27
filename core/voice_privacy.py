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


# ── voice belongs to the chat it was started in (Krem, 2026-11-26) ──
# Speech takes seconds: the wake word, the sentence, the transcription. The
# chat it was meant for used to be read only when the turn started, so if the
# vault idle-locked (or the person was moved) in between, words spoken to a
# PRIVATE chat ran in the ordinary chat the pointer had been moved to, on that
# chat's model, and were stored in plaintext. Now voice remembers where it
# started, and if that is not where it would land any more it is dropped:
# never sent, never stored, and the person is told.

VOICE_MOVED_MSG = "The chat changed while you were speaking, so that was not sent."
VOICE_SESSION_OVER_MSG = ("Conversation mode stopped: the private chat it was started in is no longer open. "
                          "Turn it on again to talk here.")


def voice_lands(system):
    """(chat, private) for where the person's voice would land right now: the
    landing chat (a room on the screen, else the open chat). (None, True) when
    it cannot be told."""
    try:
        from core.chat.history import landing_chat
        sm = system.llm_chat.session_manager
        name = landing_chat(sm)
        if not name:
            return None, True
        s = sm.get_settings_for(name)
        return name, (True if s is None else bool(s.get('private_chat')))
    except Exception:
        return None, True


def voice_moved(system, started) -> bool:
    """Is the chat this voice was started in (`started`, from voice_lands) no
    longer where it would land? Cannot tell, then or now = yes."""
    try:
        was = started[0] if started else None
        now = voice_lands(system)[0]
        return not was or not now or was != now
    except Exception:
        return True
