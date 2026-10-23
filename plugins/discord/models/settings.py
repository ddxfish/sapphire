"""Typed settings (S7): manifest defaults ← core's saved values (read live) ← overrides.

A Settings save reaches the reply path on the next resolve() — no daemon
reload. Overrides exist for tests and standalone tooling. Per-guild / channel /
DM overlays are gone (S6): daemon tasks + filters do per-server behavior.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class BotInteractionSettings:
    allow_all: bool = False              # ON: answer any bot; OFF: only the allowlist (empty = none)
    allowlist_ids: list = field(default_factory=list)


@dataclass
class SafetySettings:
    allow_direct_messages: bool = False   # M19: a stranger's DM is an outside line — opt in per install
    dm_daily_budget: int = 30             # DM messages per person per day she will answer (0 = unlimited)
    tools_stay_in_server: bool = True     # H3: inside a server event her tools reach only that server
    rate_limit_seconds: int = 30


@dataclass
class MediaSettings:
    images_in_enabled: bool = False       # images in chat ride the reply payload (the task's model sees them)
    max_images: int = 4
    gif_enabled: bool = False
    gif_api_key: str = ''
    gif_provider: str = 'klipy'
    gif_content_filter: str = 'medium'


@dataclass
class VoiceSettings:
    # Voice is a Realtime rule (Discord: Voice channel): the account, the
    # channel filter, auto-join and keep_chat_history live on the rule. These
    # are the channel-independent conversation knobs.
    turn_cues_enabled: bool = True
    min_silence_seconds: float = 1.5
    addressing_mode: str = 'bot_name'  # always | bot_name
    addressing_aliases: list = field(default_factory=list)
    solo_no_name: bool = True          # one human alone with her needs no name
    follow_up_seconds: float = 20.0    # the person she just answered may keep talking nameless
    barge_hold_ms: int = 250           # continuous speech over her before she stops
    conversation_prompt_template: str = ''


@dataclass
class ConversationSettings:
    reply_mode: str = 'default'
    human_response_chance: float = 15.0
    bot_response_chance: float = 15.0
    name_match_enabled: bool = False
    name_match_case_sensitive: bool = False
    batching_seconds: int = 8
    context_messages: int = 20          # channel messages fetched live from Discord when she replies
    natural_delay: bool = True          # read, type, pause like a person; OFF = post instantly
    ignored_channels: list = field(default_factory=list)   # account:channel_id entries


@dataclass
class ReactionSettings:
    enabled: bool = True
    silent_enabled: bool = True
    reaction_chance: float = 10.0
    reaction_cooldown_seconds: int = 30


@dataclass
class EffectiveSettings:
    safety: SafetySettings = field(default_factory=SafetySettings)
    media: MediaSettings = field(default_factory=MediaSettings)
    voice: VoiceSettings = field(default_factory=VoiceSettings)
    bot: BotInteractionSettings = field(default_factory=BotInteractionSettings)
    reaction: ReactionSettings = field(default_factory=ReactionSettings)
    channel: ConversationSettings = field(default_factory=ConversationSettings)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SettingsStore:
    """resolve() = defaults, then core's saved plugin settings, then this
    store's overrides. Overrides may be flat ({'channel.reply_mode': 'all'})
    or nested ({'channel': {'reply_mode': 'all'}})."""

    def __init__(self, overrides: dict | None = None):
        self.overrides = nested(overrides)

    def resolve(self) -> EffectiveSettings:
        effective = EffectiveSettings()
        apply_settings(effective, core_settings())
        apply_settings(effective, self.overrides)
        return effective


def nested(values: dict | None) -> dict:
    """{'a.b': 1, 'c': {'d': 2}} → {'a': {'b': 1}, 'c': {'d': 2}}."""
    out: dict = {}
    for key, value in (values or {}).items():
        if isinstance(value, dict):
            out.setdefault(key, {}).update(value)
            continue
        section, dot, name = str(key).partition('.')
        if dot and name:
            out.setdefault(section, {})[name] = value
    return out


def core_settings() -> dict:
    """Core's saved plugin settings (manifest defaults + user/webui/plugins/discord.json),
    nested. Empty outside a booted Sapphire (unit tests, standalone tooling) —
    gated on registration, not importability."""
    try:
        from core.plugin_loader import plugin_loader
        if not plugin_loader.get_plugin_info('discord'):
            return {}
        return nested(plugin_loader.get_plugin_settings('discord') or {})
    except Exception:
        return {}


def apply_settings(effective: EffectiveSettings, values: dict) -> None:
    """Retired sections and unknown keys are ignored, never fatal (stale keys
    in a saved settings file are the §9 class)."""
    for section, fields_ in (values or {}).items():
        target = getattr(effective, str(section), None)
        if target is None or not isinstance(fields_, dict):
            continue
        for key, value in fields_.items():
            if hasattr(target, str(key)):
                setattr(target, str(key), _coerce(getattr(target, str(key)), value))


def _coerce(current, value):
    """Coerce a saved value to the field's type (M29): a saved '0.5' or 'false'
    used to land as a str and every `> 0` / `if flag` read misjudged it.
    Unparseable → the existing value stands."""
    if value is None:
        return current
    if isinstance(current, bool):
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value).strip().lower()
        if text in ('1', 'true', 'yes', 'on'):
            return True
        if text in ('0', 'false', 'no', 'off', ''):
            return False
        return current
    if isinstance(current, int):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return current
    if isinstance(current, float):
        try:
            return float(value)
        except (TypeError, ValueError):
            return current
    if isinstance(current, list):
        if isinstance(value, (list, tuple)):
            return list(value)
        text = str(value).strip()
        if text.startswith('['):
            try:
                import json as _json
                parsed = _json.loads(text)
                return list(parsed) if isinstance(parsed, list) else current
            except ValueError:
                return current
        return [part.strip() for part in text.split(',') if part.strip()] if text else []
    if isinstance(current, str):
        return str(value)
    return value
