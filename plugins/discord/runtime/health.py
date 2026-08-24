"""Runtime health state models."""

from __future__ import annotations

from dataclasses import dataclass, field
from time import time


@dataclass
class RuntimeHealth:
    state: str = 'created'
    started_at: float | None = None
    updated_at: float = field(default_factory=time)
    detail: str = ''

    def mark(self, state: str, detail: str = '') -> None:
        now = time()
        if state == 'ready' and self.started_at is None:
            self.started_at = now
        self.state = state
        self.detail = detail
        self.updated_at = now

    def as_dict(self) -> dict:
        return {
            'state': self.state,
            'detail': self.detail,
            'started_at': self.started_at,
            'updated_at': self.updated_at,
        }
