"""Cheap temporal signal detection and local date parsing for commitments."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

try:
    from dateparser.search import search_dates
except ImportError:  # pragma: no cover - exercised when dependency missing
    search_dates = None

BIRTHDAY_MARKERS = ('birthday', 'b-day', 'bday')
FUTURE_INTENT_MARKERS = (
    "i'll",
    'i will',
    "we'll",
    'we will',
    "i'm going",
    'im going',
    'i am going',
    'going to',
    'gonna',
    'planning to',
    'plan to',
    'about to',
)
TEMPORAL_MARKERS = (
    'today',
    'tomorrow',
    'tonight',
    'next week',
    'next month',
    'next year',
    'this weekend',
    'end of the week',
    'end of week',
    'monday',
    'tuesday',
    'wednesday',
    'thursday',
    'friday',
    'saturday',
    'sunday',
    'in a few days',
    'in a couple days',
    'in a couple of days',
    'in several days',
    'in some days',
)
_IN_DAYS_RE = re.compile(r'\bin\s+(?:a|one|two|three|four|five|six|seven|\d+)\s+days?\b', re.IGNORECASE)
_IN_MINUTES_RE = re.compile(
    r'\b(?:in\s+)?(?:a|an|one|two|three|four|five|six|\d+)\s*minutes?\b',
    re.IGNORECASE,
)
_IN_HOURS_RE = re.compile(
    r'\b(?:in\s+)?(?:a|an|one|two|three|four|five|six|\d+)\s*hours?\b',
    re.IGNORECASE,
)
_IN_DAYS_REL_RE = re.compile(
    r'\b(?:in\s+)?(?:a|an|one|two|three|four|five|six|seven|\d+)\s*days?\b',
    re.IGNORECASE,
)
_MENTION_RE = re.compile(r'<@!?\d+>')
_WORD_NUMBERS = {
    'a': 1,
    'an': 1,
    'one': 1,
    'two': 2,
    'three': 3,
    'four': 4,
    'five': 5,
    'six': 6,
    'seven': 7,
    'eight': 8,
    'nine': 9,
    'ten': 10,
}
_REMIND_RE = re.compile(
    r"(?:can you |could you |will you |please )?remind(?:\s+me)?"
    r"|don'?t let me forget"
    r"|set a reminder"
    r"|\breminder\b",
    re.IGNORECASE,
)
_REMIND_TO_RE = re.compile(
    r"\bto\s+(?P<body>.+?)(?:\s+in\s+(?:\d+|a|an|one|two|three|four|five|six)\s+(?:minutes?|hours?|days?)\b|$)",
    re.IGNORECASE,
)
_BIRTHDAY_CAPTURE_RE = re.compile(
    r"(?:\bmy\b|\bi'm\b|\bi am\b|\bit'?s my\b).{0,48}\bbirthday\b"
    r"|\bbirthday\b.{0,48}\b(?:my|mine)\b"
    r"|(?:today|tomorrow)(?:'s)?\s+(?:is\s+)?my\s+birthday",
    re.IGNORECASE,
)
_THIRD_PARTY_BIRTHDAY_RE = re.compile(
    r"\b(?:your|his|her|their|its|sapphire'?s?)\s+birthday\b",
    re.IGNORECASE,
)
_COMMITMENT_BODY_RE = re.compile(
    r"(?:i(?:'ll| will)|we(?:'ll| will)|i(?:'m| am)\s+going\s+to|going\s+to|gonna|planning\s+to|plan\s+to|about\s+to)\s+(?P<body>.{8,200})",
    re.IGNORECASE,
)
_MAX_COMMITMENT_HORIZON_DAYS = 90
_MAX_REMINDER_HORIZON_DAYS = 30


_GLUED_UNIT_RE = re.compile(
    r'(\d)(minutes?|mins?|hours?|hrs?|days?)\b',
    re.IGNORECASE,
)


def _normalize_temporal_text(text: str) -> str:
    """Expand glued times like ``5minutes`` → ``5 minutes``."""
    if not text:
        return ''
    normalized = _MENTION_RE.sub(' ', text)
    normalized = _GLUED_UNIT_RE.sub(r'\1 \2', normalized)
    normalized = re.sub(r'\bin(\d+)', r'in \1', normalized, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', normalized).strip()


def _parse_relative_delta(text: str, now: datetime) -> tuple[datetime, str] | None:
    """Regex fallback for ``in 5 minutes`` / ``5 minutes`` when dateparser is unavailable."""
    patterns = (
        (_IN_MINUTES_RE, 'minute', 'minutes', lambda n: timedelta(minutes=n)),
        (_IN_HOURS_RE, 'hour', 'hours', lambda n: timedelta(hours=n)),
        (_IN_DAYS_REL_RE, 'day', 'days', lambda n: timedelta(days=n)),
    )
    for pattern, singular, plural, delta_fn in patterns:
        match = pattern.search(text)
        if not match:
            continue
        raw = match.group(0)
        number_match = re.search(
            r'(?:in\s+)?(?P<n>\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten)',
            raw,
            flags=re.IGNORECASE,
        )
        if not number_match:
            continue
        token = number_match.group('n').lower()
        amount = int(token) if token.isdigit() else _WORD_NUMBERS.get(token, 0)
        if amount <= 0:
            continue
        when_dt = (now + delta_fn(amount)).replace(microsecond=0)
        unit = singular if amount == 1 else plural
        return when_dt, f'in {amount} {unit}'
    return None


def looks_like_reminder_request(text: str) -> bool:
    return bool(text and text.strip() and _REMIND_RE.search(text))


def passes_reminder_gate(text: str) -> bool:
    """Fast pre-filter for explicit reminder requests."""
    if not text or not text.strip():
        return False
    text = _normalize_temporal_text(text)
    if not _REMIND_RE.search(text):
        return False
    lower = text.lower()
    if _IN_MINUTES_RE.search(lower) or _IN_HOURS_RE.search(lower):
        return True
    if _has_temporal_signal(lower):
        return True
    return bool(_IN_DAYS_RE.search(lower))


def passes_birthday_capture_gate(text: str) -> bool:
    """First-person birthday statements only — not ambient 'birthday' + 'today' chat."""
    if not text or not text.strip():
        return False
    text = _normalize_temporal_text(text)
    if not _BIRTHDAY_CAPTURE_RE.search(text):
        return False
    lower = text.lower()
    if _THIRD_PARTY_BIRTHDAY_RE.search(lower) and not re.search(
        r"\bmy\b.*\bbirthday\b|\bbirthday\b.*\b(?:my|mine)\b",
        lower,
    ):
        return False
    return True


def passes_commitment_gate(text: str) -> bool:
    """Fast pre-filter before any date parsing."""
    if not text or not text.strip():
        return False
    if passes_reminder_gate(text):
        return False
    lower = text.lower()
    if not _has_temporal_signal(lower):
        return False
    return _has_future_intent(lower)


def extract_birthday_date(text: str, now: datetime) -> tuple[int, int, str] | None:
    """Return (month, day, when_label) for a first-person birthday mention, or None."""
    if not passes_birthday_capture_gate(text):
        return None
    when_dt = _resolve_birthday_date(text, now)
    if when_dt is None:
        return None
    return when_dt.month, when_dt.day, _when_label(when_dt, now)


def extract_birthday_run_at(text: str, now: datetime) -> tuple[datetime, str] | None:
    """Return (run_at morning, when_label) for a birthday mention, or None."""
    parsed = extract_birthday_date(text, now)
    if not parsed:
        return None
    month, day, label = parsed
    try:
        when_dt = datetime(now.year, month, day)
    except ValueError:
        return None
    run_at = _morning_of(when_dt, hour=9)
    if run_at <= now:
        run_at = _morning_of(when_dt + timedelta(days=1), hour=9)
    return run_at, label


def _resolve_birthday_date(text: str, now: datetime) -> datetime | None:
    parsed = _search_future_dates(text, now)
    if parsed:
        when_dt, _label = parsed[0]
        return when_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    quick = _quick_relative_day(text, now)
    if quick is not None:
        return quick.replace(hour=0, minute=0, second=0, microsecond=0)
    weekday = _quick_weekday(text, now)
    if weekday is not None:
        return weekday.replace(hour=0, minute=0, second=0, microsecond=0)
    return None


def extract_reminder_run_at(text: str, now: datetime) -> tuple[datetime, str, str] | None:
    """Return (run_at, reminder_text, when_label) for a reminder request, or None."""
    text = _normalize_temporal_text(text)
    if not _REMIND_RE.search(text):
        return None
    parsed = _search_future_dates(text, now)
    if parsed:
        when_dt, label = parsed[0]
    else:
        relative = _parse_relative_delta(text, now)
        if not relative:
            return None
        when_dt, label = relative
    if when_dt <= now:
        return None
    horizon = now + timedelta(days=_MAX_REMINDER_HORIZON_DAYS)
    if when_dt > horizon:
        return None
    body = _extract_reminder_body(text, label)
    if len(body) < 3:
        return None
    return when_dt.replace(microsecond=0), body[:300], str(label)


def extract_commitment_run_at(text: str, now: datetime) -> tuple[datetime, str] | None:
    """Return (run_at, commitment_body) for a future commitment, or None."""
    lower = text.lower()
    if not _has_temporal_signal(lower) or not _has_future_intent(lower):
        return None
    parsed = _search_future_dates(text, now)
    if not parsed:
        relative = _parse_relative_delta(text, now)
        if relative:
            when_dt, _label = relative
        elif 'next week' in lower:
            when_dt = now + timedelta(days=7)
            _label = 'next week'
        else:
            return None
    else:
        when_dt, _label = parsed[0]
    if when_dt <= now:
        return None
    horizon = now + timedelta(days=_MAX_COMMITMENT_HORIZON_DAYS)
    if when_dt > horizon:
        return None
    body = _extract_commitment_body(text) or _text_after_phrase(text, _label) or text.strip()
    body = body.strip().rstrip('.!?')
    if len(body) < 8:
        return None
    run_at = when_dt.replace(minute=0, second=0, microsecond=0)
    if run_at <= now:
        run_at = when_dt + timedelta(hours=1)
    return run_at, body[:300]


def _has_temporal_signal(lower: str) -> bool:
    if any(marker in lower for marker in TEMPORAL_MARKERS):
        return True
    if _IN_DAYS_RE.search(lower):
        return True
    if _IN_MINUTES_RE.search(lower):
        return True
    return bool(_IN_HOURS_RE.search(lower))


def _has_future_intent(lower: str) -> bool:
    return any(marker in lower for marker in FUTURE_INTENT_MARKERS)


def _search_future_dates(text: str, now: datetime) -> list[tuple[datetime, str]]:
    if search_dates is None:
        return []
    settings = {
        'RELATIVE_BASE': now,
        'PREFER_DATES_FROM': 'future',
        'RETURN_AS_TIMEZONE_AWARE': False,
        'STRICT_PARSING': False,
    }
    try:
        found = search_dates(text, languages=['en'], settings=settings) or []
    except Exception:
        return []
    results: list[tuple[datetime, str]] = []
    for label, dt in found:
        if not isinstance(dt, datetime):
            continue
        naive = dt.replace(tzinfo=None) if dt.tzinfo else dt
        results.append((naive, str(label)))
    return results


def _quick_relative_day(text: str, now: datetime) -> datetime | None:
    lower = text.lower()
    if 'tomorrow' in lower:
        return now + timedelta(days=1)
    if re.search(r"\btoday\b", lower):
        return now
    return None


def _quick_weekday(text: str, now: datetime) -> datetime | None:
    lower = text.lower()
    for index, name in enumerate(
        ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'),
    ):
        if re.search(rf'\b{name}\b', lower):
            days_ahead = (index - now.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            return now + timedelta(days=days_ahead)
    return None


def _morning_of(dt: datetime, *, hour: int) -> datetime:
    return dt.replace(hour=hour % 24, minute=0, second=0, microsecond=0)


def _when_label(when_dt: datetime, now: datetime) -> str:
    delta = (when_dt.date() - now.date()).days
    if delta <= 0:
        return 'today'
    if delta == 1:
        return 'tomorrow'
    return when_dt.strftime('%A, %B %d')


def _extract_reminder_body(text: str, time_label: str) -> str:
    match = _REMIND_TO_RE.search(text)
    if match:
        body = (match.group('body') or '').strip().rstrip('.!?')
        if body:
            return body
    after = _text_after_phrase(text, time_label).strip()
    after = re.sub(r'^to\s+', '', after, flags=re.IGNORECASE).strip().rstrip('.!?')
    if after:
        return after
    return ''


def _extract_commitment_body(text: str) -> str:
    match = _COMMITMENT_BODY_RE.search(text)
    if not match:
        return ''
    return (match.group('body') or '').strip()


def _text_after_phrase(text: str, phrase: str) -> str:
    lower = text.lower()
    needle = phrase.lower()
    idx = lower.find(needle)
    if idx < 0:
        return ''
    after = text[idx + len(phrase):].strip(' ,:-')
    after = re.sub(r'^(?:i(?:\'ll| will)|we(?:\'ll| will)|i(?:\'m| am)\s+going\s+to|going\s+to|gonna)\s+', '', after, flags=re.IGNORECASE)
    return after.strip()
