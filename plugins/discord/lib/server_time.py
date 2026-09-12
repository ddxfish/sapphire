"""Sapphire host local time for schedule evaluation."""

from __future__ import annotations

from datetime import datetime


def now_local() -> datetime:
    """Current time in the Sapphire server's local timezone."""
    return datetime.now()
