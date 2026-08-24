"""System-prompt context for Discord voice-channel conversations."""

from __future__ import annotations

VOICE_CONTEXT_MARKER = '[discord-voice-mode]'

DEFAULT_VOICE_CONVERSATION_PROMPT_TEMPLATE = """[Discord voice conversation]
You are speaking in a live Discord voice channel. The user hears you through text-to-speech.
- Keep every reply to ONE or TWO short spoken sentences unless they clearly ask for detail.
- No paragraphs, lists, essays, or multi-part monologues.
- Your name is {primary}{alias_line}. Speech-to-text garbles names constantly — Remy, Remi, Romy, and similar variants all mean YOU.
- Never correct the user's pronunciation, never riff on spelling mistakes, and never bring up past turns where your name was misheard.
- Do not meta-comment about voice, TTS, STT, or transcription unless they are actively troubleshooting audio.
- Multiple people may be in this voice channel. User lines are prefixed with their Discord display name (e.g. "Alice: ..."). Respond to whoever addressed you; use their name when it helps disambiguate.
- Be direct and conversational — say the thing, then stop."""


def default_conversation_prompt_template() -> str:
    """Editable default body (without the context marker line)."""
    return DEFAULT_VOICE_CONVERSATION_PROMPT_TEMPLATE


def _bot_name_fields(bot_names: list[str] | None) -> tuple[str, str]:
    names = [str(n).strip() for n in (bot_names or []) if str(n or '').strip()]
    primary = names[0] if names else 'the assistant'
    aliases = ', '.join(names[1:3]) if len(names) > 1 else ''
    alias_line = f' (also known as {aliases})' if aliases else ''
    return primary, alias_line


def render_voice_conversation_prompt_template(
    template: str | None,
    *,
    bot_names: list[str] | None = None,
) -> str:
    """Fill ``{primary}`` and ``{alias_line}`` placeholders in a voice prompt template."""
    body = str(template or '').strip() or DEFAULT_VOICE_CONVERSATION_PROMPT_TEMPLATE
    primary, alias_line = _bot_name_fields(bot_names)
    try:
        return body.format(primary=primary, alias_line=alias_line)
    except KeyError:
        return body.format(primary=primary, alias_line='')


def build_voice_conversation_context(
    *,
    bot_names: list[str] | None = None,
    prompt_template: str | None = None,
) -> str:
    """Instructions for live VC: brevity, TTS, and STT name fuzziness."""
    rendered = render_voice_conversation_prompt_template(prompt_template, bot_names=bot_names)
    return f'{VOICE_CONTEXT_MARKER}\n{rendered}'


def strip_voice_context(existing: str) -> str:
    """Remove a prior Discord voice prompt block from chat custom_context."""
    prior = str(existing or '').strip()
    if VOICE_CONTEXT_MARKER not in prior:
        return prior
    idx = prior.index(VOICE_CONTEXT_MARKER)
    return prior[:idx].rstrip()


def merge_voice_context(
    existing: str,
    *,
    bot_names: list[str] | None = None,
    prompt_template: str | None = None,
) -> str:
    head = strip_voice_context(existing)
    block = build_voice_conversation_context(
        bot_names=bot_names,
        prompt_template=prompt_template,
    )
    if not head:
        return block
    return f'{head}\n\n{block}'


def is_voice_conversation_chat(chat_name: str | None) -> bool:
    from plugins.discord.sapphire.voice_chat import is_voice_chat_name
    return is_voice_chat_name(str(chat_name or ''))


def resolve_voice_conversation_context(chat_name: str) -> str:
    """Build the voice prompt block using plugin settings for the VC channel when possible."""
    from plugins.discord.sapphire.voice_chat import parse_voice_chat_name
    from plugins.discord.voice.voice_addressing import resolve_bot_names

    parsed = parse_voice_chat_name(chat_name)
    prompt_template = ''
    bot_names: list[str] = []
    if parsed:
        guild_id, channel_id = parsed
        try:
            from plugins.discord.daemon import get_runtime
            runtime = get_runtime()
            if runtime and runtime.settings_store:
                settings = runtime.settings_store.resolve(
                    guild_id=guild_id,
                    channel_id=channel_id,
                )
                prompt_template = str(
                    getattr(settings.voice, 'conversation_prompt_template', '') or ''
                )
                bot_names = resolve_bot_names(
                    settings=settings,
                    transport=getattr(runtime, 'transport', None),
                )
        except Exception:
            pass
    return build_voice_conversation_context(
        bot_names=bot_names or None,
        prompt_template=prompt_template or None,
    )


def format_voice_turn_text(text: str, *, speaker_name: str = '') -> str:
    """Prefix transcript with Discord display name for multi-speaker VC context."""
    raw = str(text or '').strip()
    speaker = str(speaker_name or '').strip()
    if not raw:
        return raw
    if not speaker:
        return raw
    prefix = f'{speaker}:'
    if raw.lower().startswith(prefix.lower()):
        return raw
    return f'{prefix} {raw}'
