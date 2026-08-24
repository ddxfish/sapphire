"""Profile, relationship, and affect models for Phase 03."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class RelationshipState:
    fondness: float = 0.5
    trust: float = 0.5
    patience: float = 0.5
    respect: float = 0.5
    interest: float = 0.5
    familiarity: float = 0.0
    message_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict | None) -> 'RelationshipState':
        payload = payload or {}
        return cls(
            fondness=float(payload.get('fondness', 0.5)),
            trust=float(payload.get('trust', 0.5)),
            patience=float(payload.get('patience', 0.5)),
            respect=float(payload.get('respect', 0.5)),
            interest=float(payload.get('interest', 0.5)),
            familiarity=float(payload.get('familiarity', 0.0)),
            message_count=int(payload.get('message_count', 0)),
        )


@dataclass
class AgentAffect:
    energy: float = 0.7
    sociability: float = 0.6
    irritability: float = 0.2
    playfulness: float = 0.5
    stress: float = 0.2

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict | None) -> 'AgentAffect':
        payload = payload or {}
        return cls(
            energy=float(payload.get('energy', 0.7)),
            sociability=float(payload.get('sociability', 0.6)),
            irritability=float(payload.get('irritability', 0.2)),
            playfulness=float(payload.get('playfulness', 0.5)),
            stress=float(payload.get('stress', 0.2)),
        )


@dataclass
class ProfileFact:
    content: str
    confidence: float = 1.0
    source: str = 'explicit'
    created_at: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class UserProfile:
    account_name: str
    user_id: str
    summary: str = ''
    relationship: RelationshipState = field(default_factory=RelationshipState)
    facts: list[ProfileFact] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'account_name': self.account_name,
            'user_id': self.user_id,
            'summary': self.summary,
            'relationship': self.relationship.to_dict(),
            'facts': [fact.to_dict() for fact in self.facts],
        }
