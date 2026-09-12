"""Reply trigger evaluation: mentions, name match, reply mode, organic chance."""

from __future__ import annotations

import random

from plugins.discord.conversation.ignored_channels import is_channel_ignored
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
    if is_channel_ignored(observation.account_name, observation.channel_id, settings):
        allowed = False
        reason = 'channel_ignored'
    elif reply_mode == 'disabled':
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
        'organic_reply': False,
    }


def evaluate_organic_chance(
    observation,
    settings,
    *,
    rng=None,
    chance_multiplier: float = 1.0,
) -> dict:
    """Roll chance for an unaddressed channel message in default reply mode.

    Mentions, name match, DMs, and non-default reply modes are handled by the
    caller — this only decides the organic roll.
    """
    roll = rng if rng is not None else random.random
    channel_settings = getattr(settings, 'channel', None) if settings else None
    author_is_bot = bool(getattr(observation, 'author_is_bot', False))
    reason_key = 'bot_response_chance' if author_is_bot else 'human_response_chance'
    raw = getattr(channel_settings, reason_key, 15.0) if channel_settings else 15.0
    try:
        base_chance = max(0.0, min(100.0, float(raw)))
    except (TypeError, ValueError):
        base_chance = 15.0
    try:
        mult = max(0.0, float(chance_multiplier))
    except (TypeError, ValueError):
        mult = 1.0
    chance = max(0.0, min(100.0, base_chance * mult))

    if chance <= 0 or roll() >= (chance / 100.0):
        return {
            'allowed': False,
            'reason': reason_key,
            'organic_reply': False,
            'chance': chance,
            'base_chance': base_chance,
            'chance_multiplier': mult,
        }
    return {
        'allowed': True,
        'reason': 'organic_chance',
        'organic_reply': True,
        'chance': chance,
        'base_chance': base_chance,
        'chance_multiplier': mult,
    }
