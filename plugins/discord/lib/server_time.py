"""Sapphire host local time for schedule evaluation."""

from __future__ import annotations

from datetime import datetime


def now_local() -> datetime:
    """Current time in the Sapphire server's local timezone (OS clock).

    Elapsed/epoch math rides this. Hour COMPARISONS ride user_hour() below."""
    return datetime.now()


def user_hour(now: datetime | None = None) -> int:
    """Hour-of-day on Sapphire's configured clock (config.USER_TIMEZONE) — the
    clock the continuity cron matches on. The cron fired on one clock while
    evaluate() compared the OS hour: on a box where they differ, zero greetings
    a day and no error (hunt 2.13.0, row 74). A naive `now` is read as OS-local
    and re-expressed in the user's zone."""
    base = now or datetime.now()
    try:
        import config
        from zoneinfo import ZoneInfo
        tz_name = getattr(config, 'USER_TIMEZONE', '') or ''
        if tz_name:
            return base.astimezone(ZoneInfo(tz_name)).hour
    except Exception:
        pass
    return base.hour
