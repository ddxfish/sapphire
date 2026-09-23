from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BaseIntention:
    intention_type: str
    account_name: str
    channel_id: str
    message_id: str
    reason: str
    metadata: dict = field(default_factory=dict)


@dataclass
class AddReactionIntention(BaseIntention):
    emoji: str = ''


@dataclass
class JoinVoiceIntention(BaseIntention):
    guild_id: str = ''


@dataclass
class LeaveVoiceIntention(BaseIntention):
    pass
