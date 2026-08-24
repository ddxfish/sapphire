"""Typed settings models and layered settings resolution."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class PresenceSettings:
    status: str = 'online'
    activity: str = ''
    quiet_status: str = 'idle'
    sleep_activity: str = 'custom: sleeping'
    cycling_enabled: bool = False
    cycle_interval_seconds: int = 300
    llm_status_chance: float = 0.0
    activity_presets: list = field(default_factory=list)
    activities_custom: list = field(default_factory=list)


@dataclass
class BotInteractionSettings:
    enabled: bool = True
    reply_mode: str = 'allowlist'  # never | allowlist | mentions_only | all
    allowlist_ids: list = field(default_factory=list)
    session_human_window_seconds: int = 300
    session_silence_seconds: int = 150
    session_safety_max_exchanges: int = 20
    proactive_enabled: bool = False
    proactive_cooldown_hours: int = 12


@dataclass
class SafetySettings:
    allow_direct_messages: bool = True
    quiet_hours_enabled: bool = False
    quiet_hours_start: int = 0
    quiet_hours_end: int = 0
    rate_limit_seconds: int = 30
    proactive_cooldown_hours: int = 6


@dataclass
class ProactiveSettings:
    greeting_enabled: bool = False
    greeting_utc_hour: int = 9  # server local hour (legacy field name)
    greeting_targets: list = field(default_factory=list)
    greeting_message: str = ''
    greeting_fallback: str = 'Good morning!'
    greeting_use_llm: bool = True
    greeting_model_provider: str = ''
    greeting_model_name: str = ''
    greeting_max_tokens: int = 180
    birthday_wish_fallback: str = 'Happy birthday! 🎂'
    birthday_use_llm: bool = True
    birthday_wish_spread_end_hour: int = 20
    outreach_enabled: bool = False
    outreach_stale_minutes: int = 120
    outreach_cooldown_hours: int = 6
    greeting_outreach_lead_hours: int = 2
    sleep_schedule_enabled: bool = False
    sleep_utc_hour: int = 22  # server local hour (legacy field name)
    goodnight_message: str = ''
    goodnight_fallback: str = 'Goodnight everyone!'
    goodnight_use_llm: bool = True
    goodnight_model_provider: str = ''
    goodnight_model_name: str = ''
    goodnight_max_tokens: int = 180
    sleep_buffered_reply_max: int = 3
    forced_wake_mention_threshold: int = 2
    forced_wake_minutes: int = 30


@dataclass
class ProfileSettings:
    enabled: bool = True
    distillation_enabled: bool = False
    birthday_capture_enabled: bool = True
    birthday_followups_enabled: bool = True
    birthday_bulk_enabled: bool = True
    birthday_bulk_threshold: int = 3


@dataclass
class MediaSettings:
    enabled: bool = False
    gif_enabled: bool = False
    gif_api_key: str = ''
    gif_provider: str = 'klipy'
    gif_content_filter: str = 'medium'
    gif_auto_chance: float = 0.0
    gif_cooldown_seconds: int = 300
    meme_enabled: bool = False
    image_understanding_enabled: bool = False
    vision_provider: str = 'openai_compat'
    vision_base_url: str = ''
    vision_model: str = ''
    vision_api_key: str = ''
    vision_timeout_seconds: int = 30
    vision_gif_mode: str = 'first_frame'
    vision_debug_enabled: bool = False


@dataclass
class VoiceSettings:
    enabled: bool = False
    transcription_enabled: bool = False
    speaking_enabled: bool = False
    emergency_disabled: bool = False
    mode: str = 'listen_only'
    join_targets: list = field(default_factory=list)
    min_silence_seconds: float = 1.5
    speak_cooldown_seconds: float = 2.0
    rolling_summary_seconds: int = 0
    streaming_playback_enabled: bool = True
    conversation_core_enabled: bool = True
    addressing_mode: str = 'bot_name'  # always | bot_name
    addressing_aliases: list = field(default_factory=list)
    conversation_prompt_template: str = ''
    max_conversation_sessions: int = 2


@dataclass
class RetentionSettings:
    enabled: bool = True
    message_days: int = 90
    trace_days: int = 14
    transcript_days: int = 30
    profile_buffer_days: int = 7


@dataclass
class ConversationSettings:
    reply_mode: str = 'default'
    name_match_enabled: bool = False
    name_match_case_sensitive: bool = False
    batching_seconds: int = 8
    strip_think_tags: bool = True
    typing_indicator_enabled: bool = True
    human_pause_enabled: bool = True
    read_delay_enabled: bool = True


@dataclass
class ReactionSettings:
    enabled: bool = True
    silent_enabled: bool = True
    reaction_chance: float = 50.0
    reaction_cooldown_seconds: int = 30
    react_on_reply_path: bool = True
    read_only_enabled: bool = True


@dataclass
class DeliverySettings:
    message_edits_enabled: bool = True
    auto_typo_enabled: bool = False
    auto_typo_chance: float = 12.0
    auto_typo_delay_min: float = 2.0
    auto_typo_delay_max: float = 6.0
    quote_reply_enabled: bool = True
    post_send_edit_enabled: bool = True


@dataclass
class CognitiveSettings:
    enabled: bool = True
    mode: str = 'integrated'
    task_follow_up_enabled: bool = True
    commitment_followups_enabled: bool = True
    reminder_followups_enabled: bool = True
    affect_modulation_enabled: bool = True
    llm_primary: str = 'auto'
    llm_model: str = ''


@dataclass
class EffectiveSettings:
    presence: PresenceSettings = field(default_factory=PresenceSettings)
    safety: SafetySettings = field(default_factory=SafetySettings)
    profile: ProfileSettings = field(default_factory=ProfileSettings)
    media: MediaSettings = field(default_factory=MediaSettings)
    voice: VoiceSettings = field(default_factory=VoiceSettings)
    retention: RetentionSettings = field(default_factory=RetentionSettings)
    proactive: ProactiveSettings = field(default_factory=ProactiveSettings)
    cognitive: CognitiveSettings = field(default_factory=CognitiveSettings)
    bot: BotInteractionSettings = field(default_factory=BotInteractionSettings)
    reaction: ReactionSettings = field(default_factory=ReactionSettings)
    delivery: DeliverySettings = field(default_factory=DeliverySettings)
    dm: ConversationSettings = field(default_factory=ConversationSettings)
    guild: ConversationSettings = field(default_factory=ConversationSettings)
    channel: ConversationSettings = field(default_factory=ConversationSettings)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SettingsOverlay:
    presence: dict[str, Any] = field(default_factory=dict)
    safety: dict[str, Any] = field(default_factory=dict)
    profile: dict[str, Any] = field(default_factory=dict)
    media: dict[str, Any] = field(default_factory=dict)
    voice: dict[str, Any] = field(default_factory=dict)
    retention: dict[str, Any] = field(default_factory=dict)
    proactive: dict[str, Any] = field(default_factory=dict)
    cognitive: dict[str, Any] = field(default_factory=dict)
    bot: dict[str, Any] = field(default_factory=dict)
    reaction: dict[str, Any] = field(default_factory=dict)
    delivery: dict[str, Any] = field(default_factory=dict)
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
    global_overlay: SettingsOverlay = field(default_factory=SettingsOverlay)
    guild_overrides: dict[str, SettingsOverlay] = field(default_factory=dict)
    channel_overrides: dict[str, SettingsOverlay] = field(default_factory=dict)
    dm_overrides: dict[str, SettingsOverlay] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> 'SettingsStore':
        payload = payload or {}
        return cls(
            global_overlay=SettingsOverlay.from_dict(payload.get('global')),
            guild_overrides={k: SettingsOverlay.from_dict(v) for k, v in (payload.get('guilds') or {}).items()},
            channel_overrides={k: SettingsOverlay.from_dict(v) for k, v in (payload.get('channels') or {}).items()},
            dm_overrides={k: SettingsOverlay.from_dict(v) for k, v in (payload.get('dms') or {}).items()},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            'global': self.global_overlay.to_dict(),
            'guilds': {k: v.to_dict() for k, v in self.guild_overrides.items()},
            'channels': {k: v.to_dict() for k, v in self.channel_overrides.items()},
            'dms': {k: v.to_dict() for k, v in self.dm_overrides.items()},
        }

    def merge_store(self, other: 'SettingsStore') -> 'SettingsStore':
        merged = SettingsStore.from_dict(self.to_dict())
        _merge_overlay(merged.global_overlay, other.global_overlay)
        for key, overlay in other.guild_overrides.items():
            merged.guild_overrides.setdefault(key, SettingsOverlay())
            _merge_overlay(merged.guild_overrides[key], overlay)
        for key, overlay in other.channel_overrides.items():
            merged.channel_overrides.setdefault(key, SettingsOverlay())
            _merge_overlay(merged.channel_overrides[key], overlay)
        for key, overlay in other.dm_overrides.items():
            merged.dm_overrides.setdefault(key, SettingsOverlay())
            _merge_overlay(merged.dm_overrides[key], overlay)
        return merged

    def resolve(self, guild_id: str | None = None, channel_id: str | None = None, dm_id: str | None = None) -> EffectiveSettings:
        effective = EffectiveSettings()
        overlays = [
            self.global_overlay,
            self.guild_overrides.get(guild_id or ''),
            self.channel_overrides.get(channel_id or ''),
            self.dm_overrides.get(dm_id or ''),
        ]
        for overlay in overlays:
            if overlay:
                _apply_overlay(effective, overlay)
        return effective


def _merge_overlay(target: SettingsOverlay, source: SettingsOverlay) -> None:
    for key in target.__dataclass_fields__.keys():
        values = dict(getattr(target, key))
        values.update(getattr(source, key))
        setattr(target, key, values)


def _apply_overlay(effective: EffectiveSettings, overlay: SettingsOverlay) -> None:
    for section, values in overlay.to_dict().items():
        target = getattr(effective, section)
        for key, value in values.items():
            if hasattr(target, key):
                setattr(target, key, value)
