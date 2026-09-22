"""Tiny clock helpers shared by the timed modules (birthdays, presence)."""
from __future__ import annotations

import re
from datetime import datetime

WINDOW_SECONDS = 300


def now_user() -> datetime:
    """Now on Sapphire's configured clock (config.USER_TIMEZONE), else OS-local; naive."""
    try:
        import config
        from zoneinfo import ZoneInfo
        tz_name = getattr(config, 'USER_TIMEZONE', '') or ''
        if tz_name:
            return datetime.now(ZoneInfo(tz_name)).replace(tzinfo=None)
    except Exception:
        pass
    return datetime.now()


def parse_hhmm(text) -> tuple[int, int] | None:
    m = re.fullmatch(r'\s*(\d{1,2}):(\d{2})\s*', str(text or ''))
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    return (hour, minute) if 0 <= hour <= 23 and 0 <= minute <= 59 else None


def due(text, now: datetime, window: int = WINDOW_SECONDS) -> bool:
    """True inside [time, time + window) — a late boot never fires a stale moment."""
    hm = parse_hhmm(text)
    if not hm:
        return False
    at = now.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
    delta = (now - at).total_seconds()
    return 0 <= delta < window


def in_window(now: datetime, start, end) -> bool:
    """True when now's HH:MM lies in [start, end); a window may cross midnight."""
    a, b = parse_hhmm(start), parse_hhmm(end)
    if not a or not b or a == b:
        return False
    cur = now.hour * 60 + now.minute
    s, e = a[0] * 60 + a[1], b[0] * 60 + b[1]
    return (s <= cur < e) if s < e else (cur >= s or cur < e)


class MemoryState:
    """Dict-backed stand-in for core's PluginState (tests, no loader)."""

    def __init__(self):
        self.d: dict = {}

    def get(self, key, default=None):
        return self.d.get(key, default)

    def save(self, key, value):
        self.d[key] = value
