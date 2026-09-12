"""In-memory cognition snapshots for the Debug tab (situation / intention / gates)."""

from __future__ import annotations

import threading
import time
from collections import deque


class CognitionDebugService:
    """Ring buffers of recent social judgment for operator preview."""

    def __init__(self, *, situation_limit: int = 12, intention_limit: int = 20, gate_limit: int = 20):
        self._situation_limit = max(1, int(situation_limit))
        self._intention_limit = max(1, int(intention_limit))
        self._gate_limit = max(1, int(gate_limit))
        self._situations: dict[str, dict] = {}
        self._intentions: deque[dict] = deque(maxlen=self._intention_limit)
        self._gates: deque[dict] = deque(maxlen=self._gate_limit)
        self._lock = threading.Lock()

    def record_situation(
        self,
        *,
        account_name: str,
        channel_id: str,
        channel_name: str = '',
        guild_id: str = '',
        situation: dict,
        organic_multiplier: float = 1.0,
    ) -> None:
        key = f'{account_name}:{channel_id}'
        row = {
            'key': key,
            'account': account_name,
            'channel_id': channel_id,
            'channel_name': channel_name or '',
            'guild_id': guild_id or '',
            'built_at': float(situation.get('built_at') or time.time()),
            'vibe': str(situation.get('vibe') or ''),
            'heat': float(situation.get('heat') or 0.0),
            'silence_seconds': float(situation.get('silence_seconds') or 0.0),
            'message_count': int(situation.get('message_count') or 0),
            'unique_authors': int(situation.get('unique_authors') or 0),
            'summary': str(situation.get('summary') or ''),
            'recent_topics': list(situation.get('recent_topics') or [])[:5],
            'organic_multiplier': float(organic_multiplier),
        }
        with self._lock:
            self._situations[key] = row
            # Cap distinct channels kept
            if len(self._situations) > self._situation_limit:
                oldest = sorted(self._situations.values(), key=lambda r: r.get('built_at') or 0.0)
                for drop in oldest[: max(0, len(self._situations) - self._situation_limit)]:
                    self._situations.pop(str(drop.get('key') or ''), None)

    def record_intention(
        self,
        *,
        account_name: str = '',
        channel_id: str = '',
        channel_name: str = '',
        message_id: str = '',
        username: str = '',
        kind: str,
        score: float = 0.0,
        reason: str = '',
        organic_multiplier: float = 1.0,
        reaction_multiplier: float = 1.0,
        situation_vibe: str = '',
        relationship: dict | None = None,
    ) -> None:
        entry = {
            'at': time.time(),
            'account': account_name,
            'channel_id': channel_id,
            'channel_name': channel_name,
            'message_id': message_id,
            'username': username,
            'kind': kind,
            'score': float(score),
            'reason': reason,
            'organic_multiplier': float(organic_multiplier),
            'reaction_multiplier': float(reaction_multiplier),
            'situation_vibe': situation_vibe or '',
            'relationship': dict(relationship or {}),
        }
        with self._lock:
            self._intentions.append(entry)

    def record_gate(
        self,
        *,
        gate: str,
        account_name: str = '',
        channel_id: str = '',
        channel_name: str = '',
        detail: dict | None = None,
    ) -> None:
        entry = {
            'at': time.time(),
            'gate': gate,
            'account': account_name,
            'channel_id': channel_id,
            'channel_name': channel_name,
            'detail': dict(detail or {}),
        }
        with self._lock:
            self._gates.append(entry)

    def snapshot(self) -> dict:
        with self._lock:
            situations = sorted(
                (dict(v) for v in self._situations.values()),
                key=lambda r: r.get('built_at') or 0.0,
                reverse=True,
            )
            intentions = [dict(item) for item in list(self._intentions)][::-1]
            gates = [dict(item) for item in list(self._gates)][::-1]
        return {
            'situations': situations,
            'intentions': intentions,
            'gates': gates,
            'updated_at': time.time(),
        }
