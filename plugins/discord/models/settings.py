"""Typed settings models and layered settings resolution."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class BotInteractionSettings:
    enabled: bool = True
    allowlist_ids: list = field(default_factory=list)


@dataclass
class SafetySettings:
    allow_direct_messages: bool = False   # M19: a stranger's DM is an outside line — opt in per install
    dm_daily_budget: int = 30             # DM messages per person per day she will answer (0 = unlimited)
    tools_stay_in_server: bool = True     # H3: inside a server event her tools reach only that server
    rate_limit_seconds: int = 30


@dataclass
class MediaSettings:
    # Images posted in chat ride the reply payload (the task's model sees them).
    images_in_enabled: bool = False
    max_images: int = 4
    gif_enabled: bool = False
    gif_api_key: str = ''
    gif_provider: str = 'klipy'
    gif_content_filter: str = 'medium'
    gif_auto_chance: float = 0.0
    gif_cooldown_seconds: int = 300


@dataclass
class VoiceSettings:
    # Voice is a Realtime daemon task (Discord: Voice channel): the account,
    # the channels and keep_chat_history live on the task. These are the
    # channel-independent conversation knobs.
    turn_cues_enabled: bool = True
    min_silence_seconds: float = 1.5
    addressing_mode: str = 'bot_name'  # always | bot_name
    addressing_aliases: list = field(default_factory=list)
    # Addressing (mic test 2026-09-13): in bot_name mode, one human alone with
    # her needs no name; the person she just answered may keep talking nameless
    # for follow_up_seconds after her reply ends. Everyone else says her name.
    solo_no_name: bool = True
    follow_up_seconds: float = 20.0
    # Continuous VAD-speech needed over her before she stops (Discord only —
    # its audio arrives through the client's own gate, clicks and all).
    barge_hold_ms: int = 250
    conversation_prompt_template: str = ''


@dataclass
class RetentionSettings:
    enabled: bool = False
    message_days: int = 90
    trace_days: int = 14
    transcript_days: int = 30
    profile_buffer_days: int = 7


@dataclass
class ConversationSettings:
    reply_mode: str = 'default'
    human_response_chance: float = 15.0
    bot_response_chance: float = 15.0
    name_match_enabled: bool = False
    name_match_case_sensitive: bool = False
    batching_seconds: int = 8
    strip_think_tags: bool = True
    typing_indicator_enabled: bool = True
    human_pause_enabled: bool = True
    read_delay_enabled: bool = True
    # account:channel_id entries — fully ignore inbound (and skip proactive) for these.
    ignored_channels: list = field(default_factory=list)


@dataclass
class ReactionSettings:
    enabled: bool = True
    silent_enabled: bool = True
    reaction_chance: float = 10.0
    reaction_cooldown_seconds: int = 30


@dataclass
class DebugSettings:
    # Debug ring holds full prompts (other people's messages) in memory — opt in.
    llm_debug_enabled: bool = False


@dataclass
class EffectiveSettings:
    safety: SafetySettings = field(default_factory=SafetySettings)
    media: MediaSettings = field(default_factory=MediaSettings)
    voice: VoiceSettings = field(default_factory=VoiceSettings)
    retention: RetentionSettings = field(default_factory=RetentionSettings)
    debug: DebugSettings = field(default_factory=DebugSettings)
    bot: BotInteractionSettings = field(default_factory=BotInteractionSettings)
    reaction: ReactionSettings = field(default_factory=ReactionSettings)
    dm: ConversationSettings = field(default_factory=ConversationSettings)
    guild: ConversationSettings = field(default_factory=ConversationSettings)
    channel: ConversationSettings = field(default_factory=ConversationSettings)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SettingsOverlay:
    safety: dict[str, Any] = field(default_factory=dict)
    media: dict[str, Any] = field(default_factory=dict)
    voice: dict[str, Any] = field(default_factory=dict)
    retention: dict[str, Any] = field(default_factory=dict)
    debug: dict[str, Any] = field(default_factory=dict)
    bot: dict[str, Any] = field(default_factory=dict)
    reaction: dict[str, Any] = field(default_factory=dict)
    dm: dict[str, Any] = field(default_factory=dict)
    guild: dict[str, Any] = field(default_factory=dict)
    channel: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> 'SettingsOverlay':
        payload = payload or {}
        keys = cls.__dataclass_fields__.keys()
        return cls(**{key: dict(payload.get(key) or {}) for key in keys})

    def to_dict(self) -> dict[str, Any]:
        return {key: dict(getattr(self, key)) for key in self.__dataclass_fields__.keys() if getattr(self, key)}


@dataclass
class SettingsStore:
    """The effective settings: core's plugin settings, read live on every
    resolve (a Settings save reaches the reply path with no daemon reload),
    under an in-memory global overlay (tests, standalone tooling). Per-guild /
    channel / DM overlays are gone (S6, 2026-09-22): daemon tasks + filters do
    per-server behavior now."""
    global_overlay: SettingsOverlay = field(default_factory=SettingsOverlay)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> 'SettingsStore':
        payload = payload or {}
        return cls(global_overlay=SettingsOverlay.from_dict(payload.get('global')))

    def to_dict(self) -> dict[str, Any]:
        return {'global': self.global_overlay.to_dict()}

    def resolve(self) -> EffectiveSettings:
        effective = EffectiveSettings()
        for overlay in (self.global_overlay, core_global_overlay()):
            if overlay:
                _apply_overlay(effective, overlay)
        return effective


def overlay_from_flat(flat: dict | None) -> SettingsOverlay:
    """Map core's flat dotted-key settings dict (section.field) to an overlay."""
    nested: dict = {}
    for key, value in (flat or {}).items():
        section, dot, field_name = key.partition('.')
        if dot and field_name:
            nested.setdefault(section, {})[field_name] = value
    return SettingsOverlay.from_dict(nested)


def core_global_overlay() -> SettingsOverlay:
    """Global settings layer, read live from core (manifest defaults + user/webui/plugins/discord.json).

    Live per-resolve so a Settings save reaches the reply path immediately —
    no daemon reload needed. Falls back to an empty overlay outside Sapphire
    (unit tests, standalone tooling).
    """
    try:
        from core.plugin_loader import plugin_loader
        if not plugin_loader.get_plugin_info('discord'):
            # Plugin system not booted (unit tests, standalone tooling) — the
            # import alone succeeds anywhere the repo root is on sys.path, so
            # gate on actual registration, not importability.
            return SettingsOverlay()
        flat = plugin_loader.get_plugin_settings('discord') or {}
    except Exception:
        return SettingsOverlay()
    return overlay_from_flat(flat)


def _coerce(current, value):
    """Coerce an overlay value to the dataclass field's type (M29): a saved
    '0.5' or 'false' used to land as a str and every `> 0` / `if flag` read
    misjudged it. Unparseable → the existing value stands."""
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


def _apply_overlay(effective: EffectiveSettings, overlay: SettingsOverlay) -> None:
    for section, values in overlay.to_dict().items():
        target = getattr(effective, section)
        for key, value in values.items():
            if hasattr(target, key):
                setattr(target, key, _coerce(getattr(target, key), value))
