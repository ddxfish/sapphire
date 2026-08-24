"""Track recent bot message edits for prompt continuity."""

from __future__ import annotations

from collections import deque
import threading
import time


class EditHistoryService:
    def __init__(self, *, max_per_channel: int = 8):
        self.max_per_channel = max(1, int(max_per_channel))
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, str], deque] = {}

    def record(
        self,
        account_name: str,
        channel_id: str,
        *,
        message_id: str,
        before: str,
        after: str,
        kind: str = 'edit',
    ) -> None:
        before = (before or '').strip()
        after = (after or '').strip()
        if not before or not after or before == after:
            return
        key = (str(account_name), str(channel_id))
        entry = {
            'message_id': str(message_id),
            'before': before[:200],
            'after': after[:200],
            'kind': kind,
            'created_at': time.time(),
        }
        with self._lock:
            bucket = self._entries.setdefault(key, deque(maxlen=self.max_per_channel))
            bucket.append(entry)

    def recent_entries(self, account_name: str, channel_id: str, *, limit: int = 3) -> list[dict]:
        key = (str(account_name), str(channel_id))
        with self._lock:
            items = list(self._entries.get(key, ()))
        items.sort(key=lambda item: item.get('created_at', 0), reverse=True)
        return items[: max(1, int(limit))]

    def build_prompt_hint(self, account_name: str, channel_id: str, *, limit: int = 2) -> str:
        entries = self.recent_entries(account_name, channel_id, limit=limit)
        if not entries:
            return ''
        lines = []
        for entry in entries:
            lines.append(f'- corrected "{entry["before"]}" → "{entry["after"]}"')
        return '[Recent self-edits — internal]\n' + '\n'.join(lines)
