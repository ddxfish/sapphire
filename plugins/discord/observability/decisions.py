"""Recent decisions (the Debug tab): why she answered or stayed quiet, ids only.

Replaces the LLM debug ring (gone 2026-09-22, Krem: a privacy mess). Nothing
here is content — no message text, no prompt, no reply — only the shape of
the decision: which message, in which channel, what stage refused it or how
many chunks went out. What she saw and said is in the task's chat, in the open.
"""

from __future__ import annotations

import threading
import time
from collections import deque


class DecisionLog:
    def __init__(self, limit: int = 20):
        self._rows: deque[dict] = deque(maxlen=max(1, int(limit)))
        self._lock = threading.Lock()

    def note(self, kind: str, *, account: str = '', channel_id: str = '', channel_name: str = '',
             message_id: str = '', stage: str = '', reason: str = '', chunks: int = 0) -> dict:
        row = {
            'at': time.time(), 'kind': str(kind), 'account': str(account or ''),
            'channel_id': str(channel_id or ''), 'channel_name': str(channel_name or ''),
            'message_id': str(message_id or ''), 'stage': str(stage or ''), 'reason': str(reason or ''),
            'chunks': int(chunks or 0),
        }
        with self._lock:
            self._rows.append(row)
        return row

    def list(self, limit: int | None = None) -> list[dict]:
        with self._lock:
            rows = list(self._rows)
        rows.reverse()
        return rows[: max(1, int(limit))] if limit else rows

    def clear(self) -> int:
        with self._lock:
            n = len(self._rows)
            self._rows.clear()
        return n
