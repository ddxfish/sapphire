"""Media artifact models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class MediaArtifact:
    message_id: str
    channel_id: str
    account_name: str
    media_kind: str
    source_url: str = ''
    filename: str = ''
    content_type: str = ''
    raw_metadata: dict = field(default_factory=dict)
    interpretation: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MemeClassification:
    is_meme: bool = False
    sentiment: str = 'neutral'
    role: str = 'generic'
