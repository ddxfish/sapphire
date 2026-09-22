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
class AddReactionIntention(BaseIntention):
    emoji: str = ''


@dataclass
class JoinVoiceIntention(BaseIntention):
    guild_id: str = ''
    mode: str = ''


@dataclass
class LeaveVoiceIntention(BaseIntention):
    session_id: str = ''
