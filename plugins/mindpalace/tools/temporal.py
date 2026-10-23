# plugins/mindpalace/tools/temporal.py
# The temporal floor (2026-07-16) — save-time date/time resolution, no LLM.
#
# Two-tier design (tmp/librarian.md): this module is the ALWAYS-ON tier. It
# stamps `meta.event_dates` (ISO strings at text-supported precision:
# YYYY | YYYY-MM-DD | YYYY-MM-DDTHH:MM — lexicographic order stays
# chronological across all three) with `meta.event_date_src = 'regex'`.
# The librarian's temporal pass is the refiner: it may overwrite regex
# stamps (src becomes 'librarian', terminal) and owns everything ambiguous.
#
# POLICY (Krem's ruling, 2026-07-16): stamp only what the rules are SURE of.
# Everything stamped here is visible in Self → Upcoming, and a confidently
# wrong date on screen is worse than a blank. Deliberately NOT resolved
# (left as refers_to_time candidates for the pass): bare ordinals
# ("the 12th"), "next <weekday>" (humans disagree), seasons, bare years,
# bare month names, "around/sometime" anything.

import calendar
import re
from datetime import date, datetime, timedelta, timezone

MAX_DATES = 4

_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
_MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
_MONTHS['sept'] = 9
_MONTH_PAT = '|'.join(sorted(_MONTHS, key=len, reverse=True))

_WEEKDAYS = {d.lower(): i for i, d in enumerate(calendar.day_name)}
_WD_PAT = '|'.join(_WEEKDAYS)

_WORD_N = {'a': 1, 'an': 1, 'one': 1, 'two': 2, 'three': 3, 'four': 4,
           'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10}
_N_PAT = r'\d{1,3}|' + '|'.join(_WORD_N)

# Fixed-date holidays plus computed Thanksgiving (4th Thursday of November).
_HOLIDAYS = {'christmas eve': (12, 24), 'christmas': (12, 25),
             "new year's eve": (12, 31), "new year's day": (1, 1),
             'halloween': (10, 31), "valentine's day": (2, 14),
             'valentines day': (2, 14)}
_HOL_PAT = '|'.join(re.escape(h) for h in sorted(_HOLIDAYS, key=len, reverse=True))

RX_ISO = re.compile(r'\b((?:19|20)\d{2})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?\b')
RX_MONTH_DAY = re.compile(
    r'\b(' + _MONTH_PAT + r')\.?\s+(\d{1,2})(?:st|nd|rd|th)?'
    r'(?:\s*[-–/]\s*\d{1,2})?'                 # ranges: "Feb 11-12 [2026]"
    r'(?:,?\s+((?:19|20)\d{2}))?\b',
    re.IGNORECASE)
RX_DAY_MONTH = re.compile(
    r'\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(' + _MONTH_PAT + r')\b'
    r'(?:,?\s+((?:19|20)\d{2}))?', re.IGNORECASE)
RX_RELDAY = re.compile(r'\b(today|tonight|tomorrow|yesterday)\b', re.IGNORECASE)
RX_AGO = re.compile(r'\b(' + _N_PAT + r')\s+(day|week|month|year)s?\s+ago\b',
                    re.IGNORECASE)
# "in N days/weeks" only — short horizons are plans. "in 15 months" is
# almost always narrative span in diary prose ("5000 hours in 15 months",
# live ghost 2026-07-16); long horizons stay librarian candidates.
RX_IN = re.compile(r'\bin\s+(' + _N_PAT + r')\s+(day|week)s?\b',
                   re.IGNORECASE)
RX_WEEKDAY = re.compile(r'\b(?:(this|last)\s+)?(' + _WD_PAT + r')\b', re.IGNORECASE)
RX_NEXT_WD = re.compile(r'\bnext\s+(?:' + _WD_PAT + r')\b', re.IGNORECASE)
RX_END_OF = re.compile(r'\bend\s+of\s+(?:the\s+)?(month|year)\b', re.IGNORECASE)
RX_HOLIDAY = re.compile(r'\b(' + _HOL_PAT + r')\b', re.IGNORECASE)
RX_THANKSGIVING = re.compile(r'\bthanksgiving\b', re.IGNORECASE)
RX_CLOCK = re.compile(
    r'\b(?:at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b'   # at 3pm / at 7:30am
    r'|at\s+(\d{1,2}):(\d{2})\b'                       # at 15:00
    r'|(\d{1,2}):(\d{2})\s*(am|pm)?\b)',               # 15:00 / 7:30pm
    re.IGNORECASE)


def _parse_anchor(anchor):
    """ISO string or datetime → naive LOCAL datetime. Dates in content are
    civil dates in the speaker's day, and `created` is stored UTC — keeping
    the UTC wall-clock shifts every evening save forward a day for anyone
    west of Greenwich ('tomorrow' at 9:30pm EDT stamped the day AFTER
    tomorrow — jank hunt, 2026-07-16). tz-aware anchors convert to the
    system's local zone before the date math."""
    if isinstance(anchor, datetime):
        dt = anchor
    else:
        try:
            dt = datetime.fromisoformat(str(anchor).strip())
        except (TypeError, ValueError):
            return datetime.now()
    if dt.tzinfo is not None:
        dt = dt.astimezone()
    return dt.replace(tzinfo=None)


def _valid(y, m, d):
    return 1900 <= y <= 2100 and 1 <= m <= 12 and 1 <= d <= calendar.monthrange(y, m)[1]


def _nearest_occurrence(anchor, month, day):
    """Month+day with no year → the temporally NEAREST occurrence, past or
    future; a tie goes future. Diary reality (live ghosts, 2026-07-16): a
    "Feb 14th" written in mid-February is about THIS February — resolving
    always-forward resurrects past memories as next-year Upcoming ghosts.
    Feb 29 slides to the nearest valid year."""
    fut = past = None
    for y in range(anchor.year, anchor.year + 5):
        if _valid(y, month, day) and datetime(y, month, day).date() >= anchor.date():
            fut = datetime(y, month, day)
            break
    for y in range(anchor.year, anchor.year - 5, -1):
        if _valid(y, month, day) and datetime(y, month, day).date() <= anchor.date():
            past = datetime(y, month, day)
            break
    if fut and past:
        return fut if (fut.date() - anchor.date()) <= (anchor.date() - past.date()) else past
    return fut or past


def _shift_months(anchor, n):
    y, m = anchor.year, anchor.month + n
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return anchor.replace(year=y, month=m,
                          day=min(anchor.day, calendar.monthrange(y, m)[1]))


def _num(tok):
    tok = tok.lower()
    return int(tok) if tok.isdigit() else _WORD_N.get(tok, 0)


def resolve(content, anchor):
    """Extract SURE dates from content, anchored to the moment it was saved.
    Returns a deduped, text-ordered list of ISO strings (max MAX_DATES) at
    the precision the text supports. Never raises."""
    try:
        return _resolve(content, _parse_anchor(anchor))
    except Exception:
        return []


def _resolve(text, anchor):
    found = []          # (position, datetime, has_time)

    def add(pos, dt, has_time=False):
        if dt and 1900 <= dt.year <= 2100:
            found.append((pos, dt, has_time))

    covered = []        # spans already claimed by a stronger rule

    def claim(m):
        covered.append(m.span())

    def free(m):
        s, e = m.span()
        return not any(cs < e and s < ce for cs, ce in covered)

    for m in RX_ISO.finditer(text):
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
        if _valid(y, mo, d):
            claim(m)
            if m[4] is not None and int(m[4]) < 24 and int(m[5]) < 60:
                add(m.start(), datetime(y, mo, d, int(m[4]), int(m[5])), True)
            else:
                add(m.start(), datetime(y, mo, d))

    for rx, mi, di, yi in ((RX_MONTH_DAY, 1, 2, 3), (RX_DAY_MONTH, 2, 1, 3)):
        for m in rx.finditer(text):
            if not free(m):
                continue
            mo, d = _MONTHS.get(m[mi].lower().rstrip('.')), int(m[di])
            if not mo:
                continue
            # Month-verb collision guard (scouts, 2026-07-19/20): "out of
            # 20, 5 may fail" → May 5; "we march 12 miles" → Mar 12; and
            # the ordinal is NOT evidence ("the 3rd may be rainy"). For
            # month words that double as common verbs, a lowercase token
            # resolves ONLY with an explicit year — anything less stays a
            # refers_to_time candidate for the librarian.
            if m[mi].rstrip('.') in ('may', 'march') and not m[yi]:
                continue
            if m[yi]:
                y = int(m[yi])
                if _valid(y, mo, d):
                    claim(m)
                    add(m.start(), datetime(y, mo, d))
            else:
                # Explicit "next/last year" modifier right after the date is a
                # SURE year ("July 10th next year" — live find, 2026-07-16).
                mod = re.match(r"[\s,]*(?:of\s+)?(next|last)\s+year\b",
                               text[m.end():m.end() + 16], re.IGNORECASE)
                if mod:
                    y = anchor.year + (1 if mod[1].lower() == 'next' else -1)
                    if _valid(y, mo, d):
                        claim(m)
                        add(m.start(), datetime(y, mo, d))
                    continue
                dt = _nearest_occurrence(anchor, mo, d)
                if dt:
                    claim(m)
                    add(m.start(), dt)

    for m in RX_RELDAY.finditer(text):
        word = m[1].lower()
        delta = {'today': 0, 'tonight': 0, 'tomorrow': 1, 'yesterday': -1}[word]
        add(m.start(), (anchor + timedelta(days=delta)).replace(
            hour=0, minute=0, second=0, microsecond=0))

    for rx, sign in ((RX_AGO, -1), (RX_IN, 1)):
        for m in rx.finditer(text):
            n, unit = _num(m[1]), m[2].lower()
            if not n:
                continue
            if unit == 'day':
                dt = anchor + timedelta(days=sign * n)
            elif unit == 'week':
                dt = anchor + timedelta(weeks=sign * n)
            elif unit == 'month':
                dt = _shift_months(anchor, sign * n)
            else:
                dt = _shift_months(anchor, sign * n * 12)
            add(m.start(), dt.replace(hour=0, minute=0, second=0, microsecond=0))

    # Weekdays: bare/'this' → next occurrence (or today); 'last' → previous.
    # 'next <weekday>' is deliberately NOT resolved — genuinely ambiguous.
    next_wd_spans = [m.span() for m in RX_NEXT_WD.finditer(text)]
    for m in RX_WEEKDAY.finditer(text):
        s, e = m.span()
        if any(cs <= s and e <= ce for cs, ce in next_wd_spans):
            continue
        target = _WEEKDAYS[m[2].lower()]
        if (m[1] or '').lower() == 'last':
            days = -(((anchor.weekday() - target) % 7) or 7)
        else:
            days = (target - anchor.weekday()) % 7
        add(m.start(), (anchor + timedelta(days=days)).replace(
            hour=0, minute=0, second=0, microsecond=0))

    for m in RX_END_OF.finditer(text):
        if m[1].lower() == 'month':
            add(m.start(), anchor.replace(
                day=calendar.monthrange(anchor.year, anchor.month)[1],
                hour=0, minute=0, second=0, microsecond=0))
        else:
            add(m.start(), datetime(anchor.year, 12, 31))

    for m in RX_HOLIDAY.finditer(text):
        mo, d = _HOLIDAYS[m[1].lower()]
        dt = _nearest_occurrence(anchor, mo, d)
        if dt:
            add(m.start(), dt)
    for m in RX_THANKSGIVING.finditer(text):
        for y in (anchor.year, anchor.year + 1):
            first = datetime(y, 11, 1)
            dt = first + timedelta(days=(3 - first.weekday()) % 7 + 21)
            if dt.date() >= anchor.date():
                add(m.start(), dt)
                break

    # Clock times: attach to the single resolved date if exactly one exists
    # and it carries no time yet; a time with NO date at all means the anchor
    # day at that time (Krem's ruling: "see you at 15:00" = today 15:00).
    clock = None
    for m in RX_CLOCK.finditer(text):
        if not free(m):
            continue        # part of an ISO datetime already claimed
        if m[1] is not None:
            h, mi_, ap = int(m[1]), int(m[2] or 0), m[3].lower()
        elif m[4] is not None:
            h, mi_, ap = int(m[4]), int(m[5]), None
        else:
            h, mi_, ap = int(m[6]), int(m[7]), (m[8] or '').lower() or None
        if ap == 'pm' and h < 12:
            h += 12
        elif ap == 'am' and h == 12:
            h = 0
        if 0 <= h < 24 and 0 <= mi_ < 60:
            clock = (h, mi_)
            break
    if clock:
        dated = [(i, f) for i, f in enumerate(found) if not f[2]]
        if len(dated) == 1:
            i, (pos, dt, _t) = dated[0]
            found[i] = (pos, dt.replace(hour=clock[0], minute=clock[1]), True)
        elif not found:
            found.append((0, anchor.replace(hour=clock[0], minute=clock[1],
                                            second=0, microsecond=0), True))

    found.sort(key=lambda f: f[0])
    out, seen = [], set()
    for _pos, dt, has_time in found:
        s = dt.strftime('%Y-%m-%dT%H:%M') if has_time else dt.strftime('%Y-%m-%d')
        if s not in seen:
            seen.add(s)
            out.append(s)
        if len(out) >= MAX_DATES:
            break
    return out


# ── Recurring dates (2026-07-19, Krem's B) ──────────────────────────────────
# Annual dates in ISO 8601 recurring form '--MM-DD', stamped as
# meta.recurring_dates. Regex-OWNED: the librarian never writes this key, so
# redate may always refresh it — no verdict interplay. Triggers: fixed-date
# holiday names (christmas IS every year), and birthday/anniversary/annual
# keywords within a small window of an explicit month+day. "The state fair
# this year" stays one-shot — no keyword, no recurrence. Floating holidays
# (thanksgiving) stay one-shot: '--MM-DD' can't express "4th Thursday".
# One-shot resolution is untouched — a chunk may carry both forms; the
# dashboard dedups at read time (same computed day = one entry).

RX_RECUR_KEY = re.compile(
    r"\b(birthday|b-?day|anniversary|born|every\s+year|each\s+year|"
    r"annual(?:ly)?|yearly)\b", re.IGNORECASE)
_RECUR_WINDOW = 40      # chars between keyword and date phrase


def recurring(content):
    """Annual '--MM-DD' entries the rules are SURE of (max MAX_DATES).
    Never raises."""
    try:
        return _recurring(content)
    except Exception:
        return []


def _recurring(text):
    out, seen = [], set()

    def add(mo, d):
        # Year 2000 (leap) as validity template so Feb 29 birthdays store;
        # occurrences() slides them to real leap years at read time.
        if 1 <= mo <= 12 and 1 <= d <= calendar.monthrange(2000, mo)[1]:
            s = f'--{mo:02d}-{d:02d}'
            if s not in seen:
                seen.add(s)
                out.append(s)

    for m in RX_HOLIDAY.finditer(text):
        add(*_HOLIDAYS[m[1].lower()])

    key_spans = [m.span() for m in RX_RECUR_KEY.finditer(text)]
    if key_spans:
        def near(m):
            s, e = m.span()
            return any(ks - _RECUR_WINDOW < e and s < ke + _RECUR_WINDOW
                       for ks, ke in key_spans)

        for m in RX_ISO.finditer(text):
            if near(m):
                add(int(m[2]), int(m[3]))
        for rx, mi, di in ((RX_MONTH_DAY, 1, 2), (RX_DAY_MONTH, 2, 1)):
            for m in rx.finditer(text):
                mo = _MONTHS.get(m[mi].lower().rstrip('.'))
                # Same month-verb guard as _resolve (scouts 2026-07-19/20):
                # lowercase may/march never mint a recurrence — "her
                # birthday, the 3rd may be rainy" must not create --05-03.
                if m[mi].rstrip('.') in ('may', 'march'):
                    continue
                if mo and near(m):
                    add(mo, int(m[di]))
    return out[:MAX_DATES]


def occurrences(mmdd, anchor):
    """'--MM-DD' → (prev, nxt) date pair around `anchor` (a date): prev =
    latest occurrence <= anchor, nxt = earliest >= anchor (equal on the day
    itself). Feb 29 slides to the nearest leap year. None if unparseable."""
    m = re.fullmatch(r'--(\d{2})-(\d{2})', mmdd or '')
    if not m:
        return None
    mo, d = int(m[1]), int(m[2])
    prev = nxt = None
    for y in range(anchor.year, anchor.year + 8):
        if _valid(y, mo, d) and date(y, mo, d) >= anchor:
            nxt = date(y, mo, d)
            break
    for y in range(anchor.year, anchor.year - 8, -1):
        if _valid(y, mo, d) and date(y, mo, d) <= anchor:
            prev = date(y, mo, d)
            break
    return prev, nxt
