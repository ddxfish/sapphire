"""Typed settings models and layered settings resolution."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class BotInteractionSettings:
    enabled: bool = True
    reply_mode: str = 'allowlist'  # never | allowlist | mentions_only | all
    allowlist_ids: list = field(default_factory=list)
    session_human_window_seconds: int = 300
    session_silence_seconds: int = 150
    session_safety_max_exchanges: int = 20


@dataclass
class SafetySettings:
    allow_direct_messages: bool = False   # M19: a stranger's DM is an outside line — opt in per install
    dm_daily_budget: int = 30             # DM messages per person per day she will answer (0 = unlimited)
    tools_stay_in_server: bool = True     # H3: inside a server event her tools reach only that server
    rate_limit_seconds: int = 30


@dataclass
class ProfileSettings:
    enabled: bool = True
    # Opt-in ambient chat → fact distill (plugin-local; not Sapphire core Mind).
    ambient_distill_enabled: bool = False
    ambient_distill_interval_hours: float = 1.0
    ambient_distill_min_messages: int = 8
    ambient_distill_max_facts: int = 3
    distill_model_provider: str = ''
    distill_model_name: str = ''
    # Soft social modulation from relationship scores.
    relationship_policy_enabled: bool = True
    relationship_policy_strength: str = 'normal'  # subtle | normal | bold


@dataclass
class MediaSettings:
    enabled: bool = False
    gif_enabled: bool = False
    gif_api_key: str = ''
    gif_provider: str = 'klipy'
    gif_content_filter: str = 'medium'
    gif_auto_chance: float = 0.0
    gif_cooldown_seconds: int = 300
    image_understanding_enabled: bool = False
    # House vision: which Sapphire-registered LLM captions images.
    # 'auto' = first registered provider that supports images.
    vision_llm_provider: str = 'auto'
    vision_llm_model: str = ''
    # Legacy sidecar endpoint (hidden from UI since 1.13.0; kept as fallback
    # for pre-registry configs). 'auto' = detect from base URL.
    vision_provider: str = 'auto'
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
    mode: str = 'listen_only'
    join_targets: list = field(default_factory=list)
    min_silence_seconds: float = 1.5
    speak_cooldown_seconds: float = 2.0
    rolling_summary_seconds: int = 0
    streaming_playback_enabled: bool = True
    conversation_core_enabled: bool = True
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
    max_conversation_sessions: int = 2
    turn_cues_enabled: bool = True
    keep_chat_history: bool = False  # off = VC chats reap VOICE_CHAT_TTL_MINUTES after the last session
    llm_provider: str = ''  # stamped onto the voice chat as llm_primary ('' = leave alone)
    llm_model: str = ''


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
    sentiment_backend: str = 'vader'  # vader | twitter_roberta
    reaction_chance: float = 10.0
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
    llm_primary: str = 'auto'
    llm_model: str = ''
    # Human world-model roadmap features
    situation_enabled: bool = True
    situation_in_prompt: bool = True
    intention_competition_enabled: bool = False
    # Debug ring holds full prompts (other people's messages) in memory — opt in.
    llm_debug_enabled: bool = False
    # Side lanes (greeting, goodnight, distill, vision) in 'auto' mode pick
    # only providers marked local (M6). Explicit picks always win.
    side_lanes_local_only: bool = True


@dataclass
class EffectiveSettings:
    safety: SafetySettings = field(default_factory=SafetySettings)
    profile: ProfileSettings = field(default_factory=ProfileSettings)
    media: MediaSettings = field(default_factory=MediaSettings)
    voice: VoiceSettings = field(default_factory=VoiceSettings)
    retention: RetentionSettings = field(default_factory=RetentionSettings)
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
    safety: dict[str, Any] = field(default_factory=dict)
    profile: dict[str, Any] = field(default_factory=dict)
    media: dict[str, Any] = field(default_factory=dict)
    voice: dict[str, Any] = field(default_factory=dict)
    retention: dict[str, Any] = field(default_factory=dict)
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

    def replace_from(self, other: 'SettingsStore') -> 'SettingsStore':
        """Swap this store's overlays for another's IN PLACE. Services capture
        the store object at build time; rebinding runtime.settings_store left
        them on the boot-time overlays until restart (hunt 2.13.0, row 44)."""
        self.global_overlay = other.global_overlay
        self.guild_overrides = other.guild_overrides
        self.channel_overrides = other.channel_overrides
        self.dm_overrides = other.dm_overrides
        return self

    def resolve(self, guild_id: str | None = None, channel_id: str | None = None, dm_id: str | None = None) -> EffectiveSettings:
        effective = EffectiveSettings()
        overlays = [
            self.global_overlay,
            core_global_overlay(),
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
