"""The one voice model: a live voice-channel session (in memory, S6 2026-09-22)."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class VoiceSession:
    session_id: str
    account_name: str
    guild_id: str
    channel_id: str
    started_at: float = 0.0
    health: str = 'connected'

    def to_dict(self) -> dict:
        return asdict(self)
