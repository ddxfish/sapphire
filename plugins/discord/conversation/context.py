"""The reply's context: the channel transcript, and the per-channel reply cooldown (S7)."""

from __future__ import annotations

import time

DEFAULT_LINE_MAX_CHARS = 1000


def format_message_line(row: dict, *, line_max_chars: int = DEFAULT_LINE_MAX_CHARS) -> str:
    author = (str(row.get('author_name') or row.get('author') or row.get('display_name') or row.get('username')
                  or 'Unknown').strip()
              or 'Unknown')
    text = str(row.get('content') or row.get('clean_content') or '').replace('\n', ' ').strip()
    line = f'{author}: {text}' if text else f'{author}:'
    if len(line) > line_max_chars:
        line = line[: line_max_chars - 1].rstrip() + '…'
    return line


def format_recent_history(rows: list[dict], *, exclude_message_id: str = '',
                          line_max_chars: int = DEFAULT_LINE_MAX_CHARS) -> list[str]:
    return [format_message_line(row, line_max_chars=line_max_chars) for row in rows or []
            if not (exclude_message_id and str(row.get('message_id') or '') == exclude_message_id)]


def build_context(rows: list[dict], trigger) -> dict:
    """`rows` = the channel's recent messages fetched live from Discord (oldest
    first). One identity for the whole turn: the reply gates key off the newest
    ADDRESSED message, so the transcript exclusion does too (hunt 2.13.0, row 15)."""
    return {
        'recent_history': format_recent_history(rows, exclude_message_id=trigger.message_id),
        'channel_id': trigger.channel_id,
        'channel_name': trigger.channel_name,
        'guild_name': trigger.guild_name,
        'guild_id': trigger.guild_id,
        'author_id': trigger.author_id,
        'attachments': trigger.attachments,
    }


class ReplyCooldown:
    """safety.rate_limit_seconds per (account, channel). Evaluated AFTER the
    respond decision — stamping the clock on ambient chatter she never answers
    starved real mentions in busy channels (scout, 2026-08-05)."""

    def __init__(self):
        self._last_reply_at: dict[tuple[str, str], float] = {}

    def evaluate(self, observation, settings=None) -> dict:
        if getattr(observation, 'author_id', '') == '':
            return {'allowed': False, 'reason': 'missing_author'}
        key = (observation.account_name, observation.channel_id)
        cooldown = getattr(getattr(settings, 'safety', None), 'rate_limit_seconds', 0) if settings else 0
        now = time.time()
        if cooldown and now - self._last_reply_at.get(key, 0) < cooldown:
            return {'allowed': False, 'reason': 'cooldown'}
        self._last_reply_at[key] = now
        return {'allowed': True, 'reason': 'allowed'}
