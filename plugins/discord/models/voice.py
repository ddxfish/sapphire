"""Voice session models and modes."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum


class VoiceMode(str, Enum):
    LISTEN_ONLY = 'listen_only'
    TRANSCRIBE_ONLY = 'transcribe_only'
    SUMMARIZE_ONLY = 'summarize_only'
    CONVERSATIONAL = 'conversational'


@dataclass
class VoiceSession:
    session_id: str
    account_name: str
    guild_id: str
    channel_id: str
    mode: VoiceMode = VoiceMode.LISTEN_ONLY
    participants: list[str] = field(default_factory=list)
    started_at: float = 0.0
    ended_at: float = 0.0
    health: str = 'connecting'
    state: str = 'active'

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload['mode'] = self.mode.value if isinstance(self.mode, VoiceMode) else str(self.mode)
        return payload

    @classmethod
    def from_row(cls, row: dict) -> 'VoiceSession':
        mode = row.get('mode') or VoiceMode.LISTEN_ONLY.value
        try:
            mode = VoiceMode(mode)
        except ValueError:
            mode = VoiceMode.LISTEN_ONLY
        import json
        participants = row.get('participants_json') or row.get('participants') or '[]'
        if isinstance(participants, str):
            participants = json.loads(participants or '[]')
        return cls(
            session_id=str(row.get('session_id') or row.get('id') or ''),
            account_name=row.get('account_name') or '',
            guild_id=row.get('guild_id') or '',
            channel_id=row.get('channel_id') or '',
            mode=mode,
            participants=list(participants),
            started_at=float(row.get('started_at') or 0),
            ended_at=float(row.get('ended_at') or 0),
            health=row.get('health') or 'connecting',
            state=row.get('state') or 'active',
        )


@dataclass
class TranscriptSegment:
    session_id: str
    account_name: str
    channel_id: str
    text: str
    speaker_id: str = ''
    speaker_name: str = ''
    confidence: float = 0.5
    started_at: float = 0.0
    ended_at: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class VoiceSummary:
    session_id: str
    account_name: str
    channel_id: str
    summary: str
    created_at: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)
