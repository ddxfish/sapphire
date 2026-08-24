"""Discord voice channel chat naming for llm_chat persistence."""

from __future__ import annotations

import logging

from plugins.discord.sapphire.voice_prompt import merge_voice_context

logger = logging.getLogger(__name__)

CHAT_PREFIX = 'discord'
KOKORO_VOICE_PREFIXES = ('af_', 'am_', 'bf_', 'bm_')
DEFAULT_DISCORD_TTS_VOICE = 'af_heart'


def is_kokoro_streaming_voice(voice: str) -> bool:
    """True when voice id is compatible with Kokoro /tts/stream."""
    value = str(voice or '').strip()
    if not value or ':' in value:
        return False
    return value.startswith(KOKORO_VOICE_PREFIXES)


def default_discord_tts_voice(system) -> str:
    tts = getattr(system, 'tts', None) if system else None
    global_voice = str(getattr(tts, 'voice_name', None) or '').strip()
    if is_kokoro_streaming_voice(global_voice):
        return global_voice
    return DEFAULT_DISCORD_TTS_VOICE


def ensure_discord_voice_chat_settings(
    system,
    chat_name: str,
    *,
    bot_names: list[str] | None = None,
    conversation_prompt_template: str = '',
) -> None:
    """Ensure discord VC chats use Kokoro TTS voice + voice-mode prompt context."""
    llm = getattr(system, 'llm_chat', None)
    sm = getattr(llm, 'session_manager', None) if llm else None
    if sm is None:
        return
    try:
        settings = sm.read_chat_settings(chat_name) or {}
    except Exception:
        settings = {}
    updates: dict = {}
    voice = str(settings.get('tts_voice') or '').strip()
    if not is_kokoro_streaming_voice(voice):
        updates['tts_voice'] = default_discord_tts_voice(system)
    merged_ctx = merge_voice_context(
        settings.get('custom_context', ''),
        bot_names=bot_names,
        prompt_template=conversation_prompt_template,
    )
    if merged_ctx != str(settings.get('custom_context') or '').strip():
        updates['custom_context'] = merged_ctx
    if not updates:
        return
    setter = getattr(sm, 'set_named_chat_settings', None)
    if not callable(setter):
        return
    if setter(chat_name, updates):
        logger.info('Discord voice chat %s settings updated: %s', chat_name, sorted(updates))


def sanitize_chat_name(chat_name: str) -> str:
    """Match core SessionManager.create_chat name sanitization."""
    safe = ''.join(c for c in str(chat_name or '') if c.isalnum() or c in (' ', '-', '_')).strip()
    return safe.replace(' ', '_').lower()


def voice_chat_name(guild_id: str, channel_id: str) -> str:
    """Canonical llm_chat name for a voice channel (survives core sanitization)."""
    return sanitize_chat_name(f'{CHAT_PREFIX}_{guild_id}_{channel_id}')


def legacy_voice_chat_name(guild_id: str, channel_id: str) -> str:
    """Older colon form — core stored as discord{guild}{channel} with no separators."""
    return sanitize_chat_name(f'{CHAT_PREFIX}:{guild_id}:{channel_id}')


def is_voice_chat_name(chat_name: str) -> bool:
    raw = str(chat_name or '').strip().lower()
    if not raw.startswith(CHAT_PREFIX):
        return False
    if raw.startswith(f'{CHAT_PREFIX}_'):
        return True
    tail = raw[len(CHAT_PREFIX):]
    return bool(tail) and tail.isdigit()


def parse_voice_chat_name(chat_name: str) -> tuple[str, str] | None:
    """Parse guild_id and channel_id from a stored voice chat name."""
    raw = str(chat_name or '').strip().lower()
    if raw.startswith(f'{CHAT_PREFIX}_'):
        parts = raw.split('_', 2)
        if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
            return parts[1], parts[2]
    if raw.startswith(f'{CHAT_PREFIX}:'):
        parts = raw.split(':', 2)
        if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
            return parts[1], parts[2]
    tail = raw[len(CHAT_PREFIX):] if raw.startswith(CHAT_PREFIX) else ''
    if not tail.isdigit():
        return None
    return _parse_legacy_concatenated(tail)


def _parse_legacy_concatenated(tail: str) -> tuple[str, str] | None:
    candidates: list[tuple[str, str]] = []
    for guild_len in range(17, 21):
        if guild_len >= len(tail):
            continue
        guild_id = tail[:guild_len]
        channel_id = tail[guild_len:]
        if len(channel_id) < 17 or len(channel_id) > 20:
            continue
        if guild_id.isdigit() and channel_id.isdigit():
            candidates.append((guild_id, channel_id))
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    return min(candidates, key=lambda pair: abs(len(pair[0]) - len(pair[1])))


def resolve_voice_chat_name(system, guild_id: str, channel_id: str) -> str:
    """Return the stored chat name for this VC, preferring an existing record."""
    canonical = voice_chat_name(guild_id, channel_id)
    legacy = legacy_voice_chat_name(guild_id, channel_id)
    llm = getattr(system, 'llm_chat', None)
    sm = getattr(llm, 'session_manager', None) if llm else None
    if sm is not None:
        for candidate in (canonical, legacy):
            try:
                if sm.read_chat_settings(candidate) is not None:
                    return candidate
            except Exception:
                continue
    return canonical


def ensure_voice_chat(
    system,
    guild_id: str,
    channel_id: str,
    *,
    label: str = '',
    bot_names: list[str] | None = None,
    conversation_prompt_template: str = '',
) -> str:
    """Create the per-VC chat in llm_chat if missing. Returns stored chat_name."""
    chat_name = resolve_voice_chat_name(system, guild_id, channel_id)
    llm = getattr(system, 'llm_chat', None)
    if not llm or not hasattr(llm, 'create_chat'):
        return chat_name
    sm = getattr(llm, 'session_manager', None)
    if sm is not None:
        try:
            if sm.read_chat_settings(chat_name) is not None:
                ensure_discord_voice_chat_settings(
                    system,
                    chat_name,
                    bot_names=bot_names,
                    conversation_prompt_template=conversation_prompt_template,
                )
                return chat_name
        except Exception:
            pass
    try:
        created = llm.create_chat(chat_name)
        if created:
            logger.info('Created voice chat %s', chat_name)
    except Exception as exc:
        logger.debug('Voice chat create skipped for %s: %s', chat_name, exc)
    ensure_discord_voice_chat_settings(
        system,
        chat_name,
        bot_names=bot_names,
        conversation_prompt_template=conversation_prompt_template,
    )
    return chat_name
