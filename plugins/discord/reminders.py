"""Reminders — "remind me in 2h to…" (2026-09-25, back in the host; no LLM anywhere).

The `discord_remind` tool stores a row bound to the person asking and the
channel they asked in. Every runtime tick the due rows are posted as a plain
"<@user> Reminder: text" and dropped; a failed post is logged, never retried.
Rows live in memory and are mirrored into the plugin's state JSON (the
greetings latch's file) on every change, so a restart keeps what was pending.
Off by default (`reminders.enabled`); off = the tool refuses and nothing posts.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime, timedelta

from plugins.discord.greetings import now_user, parse_hhmm

logger = logging.getLogger(__name__)

STATE_KEY = 'reminders_pending'
MAX_PENDING = 20          # per person per bot
MAX_DAYS = 30
MAX_TEXT = 300
_UNITS = {'s': 1, 'sec': 1, 'secs': 1, 'second': 1, 'seconds': 1,
          'm': 60, 'min': 60, 'mins': 60, 'minute': 60, 'minutes': 60,
          'h': 3600, 'hr': 3600, 'hrs': 3600, 'hour': 3600, 'hours': 3600,
          'd': 86400, 'day': 86400, 'days': 86400,
          'w': 604800, 'week': 604800, 'weeks': 604800}
_PART = re.compile(r'(\d+(?:\.\d+)?)\s*([a-z]+)')


def parse_delay(text) -> float | None:
    """'2h', '30m', '1d 2h', '90 minutes', 'in 2 hours 30 min' → seconds; None if unreadable."""
    raw = re.sub(r'^\s*in\s+', '', str(text or '').strip().lower())
    if not raw:
        return None
    total, consumed = 0.0, ''
    for num, unit in _PART.findall(raw):
        if unit not in _UNITS:
            return None
        total += float(num) * _UNITS[unit]
        consumed += num + unit
    if not consumed or re.sub(r'[\s,]|and', '', raw) != consumed:
        return None
    return total if total > 0 else None


def parse_at(text, now: datetime) -> datetime | None:
    """'18:30' → today at 18:30, or tomorrow if that already passed."""
    hm = parse_hhmm(text)
    if not hm:
        return None
    at = now.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
    return at if at > now else at + timedelta(days=1)


def due_in_seconds(*, delay: str = '', at: str = '', now: datetime | None = None) -> float | None:
    if str(delay or '').strip():
        return parse_delay(delay)
    if str(at or '').strip():
        now = now or now_user()
        when = parse_at(at, now)
        return (when - now).total_seconds() if when else None
    return None


class Reminders:
    """Pending rows in memory, mirrored to `state` (get/save) when given."""

    def __init__(self, state=None):
        self.state = state
        self._lock = threading.RLock()
        self._rows: list[dict] = []
        for r in (state.get(STATE_KEY) if state is not None else None) or []:
            try:
                self._rows.append({'id': int(r['id']), 'account': str(r['account']), 'channel_id': str(r['channel_id']),
                                   'user_id': str(r['user_id']), 'text': str(r['text']), 'due_ts': float(r['due_ts'])})
            except (KeyError, TypeError, ValueError):
                continue
        self._next_id = max([r['id'] for r in self._rows], default=0) + 1

    def _mirror(self) -> None:
        if self.state is not None:
            try:
                self.state.save(STATE_KEY, list(self._rows))
            except Exception as exc:
                logger.warning('[DISCORD] reminders not mirrored to plugin state: %s', exc)

    def add(self, account: str, channel_id: str, user_id: str, text: str, due_ts: float) -> dict:
        with self._lock:
            row = {'id': self._next_id, 'account': str(account), 'channel_id': str(channel_id),
                   'user_id': str(user_id), 'text': str(text), 'due_ts': float(due_ts)}
            self._next_id += 1
            self._rows.append(row)
            self._mirror()
            return dict(row)

    def pending(self, account: str, user_id: str | None = None) -> list[dict]:
        with self._lock:
            rows = [r for r in self._rows if r['account'] == str(account)
                    and (user_id is None or r['user_id'] == str(user_id))]
        return sorted((dict(r) for r in rows), key=lambda r: (r['due_ts'], r['id']))

    def cancel(self, account: str, user_id: str, *, reminder_id: int | None = None, match: str = '') -> int:
        """Drop a person's OWN pending reminders: one by id, or every one whose text contains `match`."""
        needle = str(match or '').lower()
        if reminder_id is None and not needle:
            return 0
        with self._lock:
            keep = [r for r in self._rows if not (
                r['account'] == str(account) and r['user_id'] == str(user_id)
                and ((reminder_id is not None and r['id'] == int(reminder_id)) or (reminder_id is None and needle in r['text'].lower())))]
            removed = len(self._rows) - len(keep)
            if removed:
                self._rows = keep
                self._mirror()
        return removed

    def take_due(self, account: str, now_ts: float | None = None) -> list[dict]:
        """Pop every due row for `account` — taken BEFORE posting, so a crash mid-send cannot double post."""
        now_ts = time.time() if now_ts is None else now_ts
        with self._lock:
            due = [r for r in self._rows if r['account'] == str(account) and r['due_ts'] <= now_ts]
            if due:
                self._rows = [r for r in self._rows if r not in due]
                self._mirror()
        return sorted((dict(r) for r in due), key=lambda r: (r['due_ts'], r['id']))

    async def deliver_async(self, account: str, transport, now_ts: float | None = None) -> list[dict]:
        """Post every due reminder for `account` through the transport (on its loop)."""
        rows = self.take_due(account, now_ts)
        for r in rows:
            text = f"<@{r['user_id']}> Reminder: {r['text']}"
            try:
                result = await transport.send_message_async(r['channel_id'], text, account_name=account) or {}
            except Exception as exc:
                result = {'status': 'error', 'error': str(exc)}
            if result.get('status') == 'error':
                logger.warning('[DISCORD] reminder #%s for %s not posted: %s', r['id'], r['user_id'], result.get('error'))
            else:
                logger.info('[DISCORD] reminder #%s posted in %s', r['id'], r['channel_id'])
        return rows
