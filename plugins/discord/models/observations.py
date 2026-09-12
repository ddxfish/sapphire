from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BaseObservation:
    observation_id: str
    account_name: str
    guild_id: str
    guild_name: str
    channel_id: str
    channel_name: str
    author_id: str
    username: str
    display_name: str
    created_at: float
    is_dm: bool


@dataclass
class TextMessageObservation(BaseObservation):
    message_id: str
    content: str
    clean_content: str
    mentioned: bool
    author_is_bot: bool = False
    mention_user_ids: list[str] = field(default_factory=list)
    name_matched: bool = False
    follow_up_hints: list[str] = field(default_factory=list)
    attachments: list[dict] = field(default_factory=list)
    slash_command: str = ''
    reply_to_message_id: str = ''


@dataclass
class TypingObservation(BaseObservation):
    pass


@dataclass
class SlashCommandObservation(BaseObservation):
    command_name: str = ''
    content: str = ''


@dataclass
class ReactionContextObservation(BaseObservation):
    message_id: str = ''
    content: str = ''


@dataclass
class VoiceTranscriptObservation(BaseObservation):
    session_id: str = ''
    text: str = ''
    confidence: float = 0.5
    transcript_segment_id: int = 0
