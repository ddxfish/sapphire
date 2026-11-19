# core/plugins/calendar/repeat.py - repeating events, stored as an RFC 5545 RRULE
"""
The subset this calendar writes and reads:

    FREQ=DAILY|WEEKLY|MONTHLY|YEARLY   INTERVAL=n   COUNT=n   UNTIL=YYYYMMDD
    BYDAY=MO,WE,...  (weekly only)

Anything else in a rule is refused by parse(), so a rule is never expanded
wrongly in silence. A linked calendar can carry any rule ("the second Tuesday
of the month"): expand() hands those to dateutil's full RFC expander when it
is installed (it comes with Sapphire's own requirements), and raises
ValueError when it is not, which the caller shows as a one-time event.

Month ends follow the RFC (and Google, and Apple): "monthly on the 31st" has
no occurrence in a 30-day month, and "yearly on Feb 29" only falls in leap
years. Days are never moved to a neighbour.

Own expander for the subset on purpose: no dependency for what this page
writes, and the rule set is this small.
"""
from datetime import date, datetime, time as dtime, timedelta

FREQS = ('DAILY', 'WEEKLY', 'MONTHLY', 'YEARLY')
DAYS = ('MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU')          # date.weekday() order
DAY_NAMES = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
MAX_COUNT = 1000
_MAX_PERIODS = 60000        # loop guard: 160 years of daily


def build(freq, interval=1, count=None, until=None, byday=None):
    """Fields -> RRULE string. Raises ValueError on a bad field."""
    freq = str(freq or '').upper()
    if freq not in FREQS:
        raise ValueError(f"repeat must be one of daily, weekly, monthly, yearly (got {freq!r})")
    try:
        interval = 1 if interval in (None, '') else int(interval)
    except (TypeError, ValueError):
        raise ValueError("every must be a whole number")
    if not 1 <= interval <= 999:
        raise ValueError("every must be between 1 and 999")
    parts = [f"FREQ={freq}"]
    if interval != 1:
        parts.append(f"INTERVAL={interval}")
    if byday and freq == 'WEEKLY':
        days = sorted({int(d) for d in byday})
        if any(d < 0 or d > 6 for d in days):
            raise ValueError("bad weekday")
        parts.append("BYDAY=" + ",".join(DAYS[d] for d in days))
    if count not in (None, '', 0):
        try:
            count = int(count)
        except (TypeError, ValueError):
            raise ValueError("times must be a whole number")
        if not 1 <= count <= MAX_COUNT:
            raise ValueError(f"times must be between 1 and {MAX_COUNT}")
        parts.append(f"COUNT={count}")
    elif until:
        if isinstance(until, datetime):
            until = until.date()
        parts.append("UNTIL=" + until.strftime('%Y%m%d'))
    return ";".join(parts)


def parse(rrule):
    """RRULE string -> {'freq','interval','count','until','byday'}; None for ''.
    Raises ValueError on anything outside the subset."""
    text = str(rrule or '').strip()
    if text.upper().startswith('RRULE:'):
        text = text[6:]
    if not text:
        return None
    out = {'freq': None, 'interval': 1, 'count': None, 'until': None, 'byday': None}
    for part in text.split(';'):
        if not part.strip():
            continue
        key, _, val = part.partition('=')
        key, val = key.strip().upper(), val.strip().upper()
        if key == 'FREQ':
            if val not in FREQS:
                raise ValueError(f"unsupported repeat frequency {val!r}")
            out['freq'] = val
        elif key == 'INTERVAL':
            out['interval'] = max(1, int(val))
        elif key == 'COUNT':
            out['count'] = max(1, int(val))
        elif key == 'UNTIL':
            out['until'] = datetime.strptime(val[:8], '%Y%m%d').date()
        elif key == 'BYDAY':
            days = []
            for d in val.split(','):
                if d not in DAYS:
                    raise ValueError(f"unsupported BYDAY value {d!r}")
                days.append(DAYS.index(d))
            out['byday'] = sorted(set(days))
        elif key == 'WKST':
            if val != 'MO':
                raise ValueError("only weeks that start on Monday are supported in a repeat rule")
        else:
            raise ValueError(f"unsupported repeat rule part {key!r}")
    if not out['freq']:
        raise ValueError("repeat rule has no FREQ")
    if out['byday'] and out['freq'] != 'WEEKLY':
        raise ValueError("BYDAY is only supported for weekly repeats")
    return out


def describe(rrule):
    """Plain words for a rule: 'every 2 weeks on Mon, Wed until 2027-01-01'."""
    try:
        r = parse(rrule)
    except ValueError:
        return 'repeats'
    if not r:
        return ''
    unit = {'DAILY': 'day', 'WEEKLY': 'week', 'MONTHLY': 'month', 'YEARLY': 'year'}[r['freq']]
    n = r['interval']
    if n == 1:
        text = {'DAILY': 'daily', 'WEEKLY': 'weekly', 'MONTHLY': 'monthly', 'YEARLY': 'yearly'}[r['freq']]
    else:
        text = f"every {n} {unit}s"
    if r['byday']:
        text += " on " + ", ".join(DAY_NAMES[d] for d in r['byday'])
    if r['count']:
        text += f", {r['count']} times"
    elif r['until']:
        text += f" until {r['until'].isoformat()}"
    return text


def _days_in_month(year, month):
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return (nxt - timedelta(days=1)).day


def _period(r, first, p):
    """Period p (0 = the one holding `first`) as (its first day, its candidate
    dates in order); None past the end of the calendar."""
    n = r['interval'] * p
    if r['freq'] == 'DAILY':
        d = first + timedelta(days=n)
        return d, [d]
    if r['freq'] == 'WEEKLY':
        monday = first - timedelta(days=first.weekday()) + timedelta(weeks=n)
        days = r['byday'] or [first.weekday()]
        return monday, [monday + timedelta(days=d) for d in days]
    if r['freq'] == 'MONTHLY':
        m = first.month - 1 + n
        year, month = first.year + m // 12, m % 12 + 1
    else:                                                      # YEARLY
        year, month = first.year + n, first.month
    if year > 9998:
        return None
    fits = first.day <= _days_in_month(year, month)
    return date(year, month, 1), ([date(year, month, first.day)] if fits else [])


def _first_period(r, first, range_start):
    """A period index safely at or before the one that can first reach range_start."""
    gap = (range_start - first).days
    if gap <= 0:
        return 0
    n = r['interval']
    if r['freq'] == 'DAILY':
        return gap // n
    if r['freq'] == 'WEEKLY':
        return max(0, gap // (7 * n) - 1)
    months = (range_start.year - first.year) * 12 + (range_start.month - first.month)
    if r['freq'] == 'MONTHLY':
        return max(0, months // n - 1)
    return max(0, (range_start.year - first.year) // n - 1)


def expand(start, rrule, range_start, range_end, exdates=()):
    """Occurrence starts of a repeating event that fall on a day in
    [range_start, range_end] (dates, inclusive).

    start: date (all-day) or naive datetime (timed); occurrences keep its type
    and clock time. exdates: 'YYYY-MM-DD' days with no occurrence. COUNT counts
    occurrences before exceptions are taken out, as the RFC says.
    """
    try:
        r = parse(rrule)
    except ValueError:
        return _expand_any(start, rrule, range_start, range_end, exdates)
    timed = isinstance(start, datetime)
    first = start.date() if timed else start
    skip = set(exdates or ())
    out = []
    if not r:
        if range_start <= first <= range_end and first.isoformat() not in skip:
            out.append(start)
        return out
    last = range_end if not r['until'] else min(range_end, r['until'])
    if last < first or last < range_start:
        return out
    # With a COUNT every occurrence has to be counted from the first one.
    p = 0 if r['count'] else _first_period(r, first, range_start)
    made = 0
    for _ in range(_MAX_PERIODS):
        try:
            period = _period(r, first, p)
        except OverflowError:
            break
        if period is None or period[0] > last:
            break
        p += 1
        for d in period[1]:
            if d < first:
                continue
            if r['until'] and d > r['until']:
                return out
            made += 1
            if r['count'] and made > r['count']:
                return out
            if d > range_end:
                return out
            if d >= range_start and d.isoformat() not in skip:
                out.append(datetime.combine(d, start.time()) if timed else d)
    return out


def _expand_any(start, rrule, range_start, range_end, exdates=()):
    """expand() for a rule outside the subset, through dateutil. ValueError
    when dateutil is missing or cannot read the rule either."""
    try:
        from dateutil.rrule import rrulestr
    except ImportError:
        raise ValueError("this repeat rule needs the dateutil package")
    text = str(rrule or '').strip()
    if text.upper().startswith('RRULE:'):
        text = text[6:]
    timed = isinstance(start, datetime)
    first = start if timed else datetime.combine(start, dtime())
    # UNTIL may carry a Z (UTC); dateutil then refuses a start with no zone.
    # A day is all the precision this calendar keeps, so the date part is used.
    parts = []
    for part in text.split(';'):
        key, _, val = part.partition('=')
        if key.strip().upper() == 'UNTIL':
            part = f"UNTIL={val.strip()[:8]}T235959"
        parts.append(part)
    try:
        rule = rrulestr(';'.join(parts), dtstart=first)
        hits = rule.between(datetime.combine(range_start, dtime()),
                            datetime.combine(range_end, dtime(23, 59, 59)), inc=True)
    except Exception as e:
        raise ValueError(f"repeat rule not understood: {e}")
    skip = set(exdates or ())
    out = []
    for h in hits[:_MAX_PERIODS]:
        if h.date().isoformat() not in skip:
            out.append(h if timed else h.date())
    return out
