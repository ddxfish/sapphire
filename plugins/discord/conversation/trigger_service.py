"""Reply trigger evaluation: mentions, name match, reply mode."""

from __future__ import annotations

from plugins.discord.conversation.name_match import bot_names_for_account, message_matches_bot_name


def evaluate_reply_trigger(
    observation,
    settings,
    *,
    transport=None,
    account_repository=None,
) -> dict:
    channel_settings = getattr(settings, 'channel', None) if settings else None
    name_match_enabled = bool(getattr(channel_settings, 'name_match_enabled', False))
    case_sensitive = bool(getattr(channel_settings, 'name_match_case_sensitive', False))
    reply_mode = str(getattr(channel_settings, 'reply_mode', 'default') or 'default')

    bot_names = bot_names_for_account(
        observation.account_name,
        transport=transport,
        account_repository=account_repository,
    ) if name_match_enabled else set()
    name_matched = name_match_enabled and message_matches_bot_name(
        observation.clean_content,
        bot_names,
        case_sensitive=case_sensitive,
    )
    mentioned = bool(getattr(observation, 'mentioned', False))
    respond_trigger = mentioned or name_matched

    allowed = True
    reason = 'allowed'
    if reply_mode == 'disabled':
        allowed = False
        reason = 'reply_disabled'
    elif reply_mode == 'mentions_only' and not respond_trigger:
        allowed = False
        reason = 'mentions_only'

    return {
        'allowed': allowed,
        'reason': reason,
        'mentioned': mentioned,
        'name_matched': name_matched,
        'respond_trigger': respond_trigger,
        'reply_mode': reply_mode,
    }
