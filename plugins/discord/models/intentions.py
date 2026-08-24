from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BaseIntention:
    intention_type: str
    account_name: str
    channel_id: str
    message_id: str
    reason: str
    confidence: float = 1.0
    urgency: float = 0.5
    cost: float = 0.1
    metadata: dict = field(default_factory=dict)


@dataclass
class ReplyMessageIntention(BaseIntention):
    prompt: str = ''


@dataclass
class SummarizeChannelIntention(BaseIntention):
    prompt: str = ''


@dataclass
class RecordUserFactIntention(BaseIntention):
    fact: str = ''


@dataclass
class AddReactionIntention(BaseIntention):
    emoji: str = ''


@dataclass
class GreetChannelIntention(BaseIntention):
    prompt: str = ''


@dataclass
class BirthdayWishIntention(BaseIntention):
    prompt: str = ''
    user_id: str = ''


@dataclass
class OutreachIntention(BaseIntention):
    prompt: str = ''


@dataclass
class GoodnightIntention(BaseIntention):
    prompt: str = ''


@dataclass
class SendGifIntention(BaseIntention):
    query: str = ''


@dataclass
class SendMemeIntention(BaseIntention):
    meme_url: str = ''
    theme: str = ''


@dataclass
class UpdatePresenceIntention(BaseIntention):
    status: str = 'online'
    activity: str = ''


@dataclass
class JoinVoiceIntention(BaseIntention):
    guild_id: str = ''
    mode: str = ''


@dataclass
class LeaveVoiceIntention(BaseIntention):
    session_id: str = ''


@dataclass
class SpeakVoiceIntention(BaseIntention):
    text: str = ''


@dataclass
class SummarizeVoiceSessionIntention(BaseIntention):
    session_id: str = ''
    prompt: str = ''
