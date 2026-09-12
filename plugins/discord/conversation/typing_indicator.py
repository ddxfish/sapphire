"""Human-like typing indicator timing for Discord replies."""

from __future__ import annotations

import random

WPM_BASE_MIN = 60
WPM_BASE_MAX = 120
WPM_JITTER = 0.2
MIN_TYPING_SECS = 0.5
MAX_TYPING_SECS = 12.0
READ_DELAY_MIN = 0.3
READ_DELAY_MAX = 1.2
HUMAN_PAUSE_MIN = 0.5
HUMAN_PAUSE_MAX = 3.0
INTER_CHUNK_MIN = 0.8
INTER_CHUNK_MAX = 2.5
CHARS_PER_WORD = 5
SHORT_REPLY_CHARS = 50
LONG_REPLY_CHARS = 200


def _chars_per_second(wpm: int) -> float:
    return (wpm * CHARS_PER_WORD) / 60.0


def _looks_like_code(text: str) -> bool:
    if not text:
        return False
    if '```' in text or text.count('`') >= 2:
        return True
    special = sum(1 for c in text if c in '{}[]();=<>/#\\|&$@')
    return len(text) >= 20 and (special / len(text)) > 0.08


def contextual_wpm(text: str = '') -> int:
    """Pick a human typing speed for this chunk (roughly 60–120 WPM)."""
    length = len(text or '')
    if _looks_like_code(text):
        return random.randint(35, 55)
    if length < SHORT_REPLY_CHARS:
        return random.randint(90, 120)
    if length > LONG_REPLY_CHARS:
        return random.randint(55, 85)
    return random.randint(WPM_BASE_MIN, WPM_BASE_MAX)


def typing_duration_seconds(text_length: int, *, text: str = '', wpm: int | None = None) -> float:
    if wpm is None:
        wpm = contextual_wpm(text) if text else random.randint(WPM_BASE_MIN, WPM_BASE_MAX)
    cps = _chars_per_second(wpm)
    base = text_length / cps if cps > 0 else 0
    jitter = 1.0 + random.uniform(-WPM_JITTER, WPM_JITTER)
    return max(MIN_TYPING_SECS, min(MAX_TYPING_SECS, base * jitter))


def human_pause_seconds() -> float:
    return random.uniform(HUMAN_PAUSE_MIN, HUMAN_PAUSE_MAX)


def read_delay_seconds(trigger_length: int = 0) -> float:
    base = random.uniform(READ_DELAY_MIN, READ_DELAY_MAX)
    return base + min(0.5, trigger_length / 2000.0)


def inter_chunk_pause_seconds() -> float:
    return random.uniform(INTER_CHUNK_MIN, INTER_CHUNK_MAX)
