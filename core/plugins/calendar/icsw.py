# core/plugins/calendar/icsw.py - writing calendar files (the CalDAV side)
"""
ics.py reads a calendar file into rows. This writes one, and changes one that
is already there.

A change is made IN the file a server handed us, never by building the event
again from our own few fields: an event carries things this calendar does not
model (alarms, guests and their answers, another app's own properties), and a
rebuilt event would silently drop them. So a file is loaded into a small tree
of components and lines, the lines we mean to change are replaced, and the
rest goes back as it came.

Times. A local wall time goes out with its zone's name (TZID) and a VTIMEZONE
block that says what that name means; with no zone name known it goes out as
a UTC instant. A repeating event must not be written in UTC: its occurrences
would drift an hour when the clocks change.
"""
import calendar as _cal
import re
from datetime import date, datetime, timedelta, timezone

from . import ics

PRODID = '-//Sapphire//Calendar//EN'
_CONTROL = re.compile(r'[\x00-\x08\x0b-\x1f\x7f\x85\u2028\u2029]')      # also the line ends some readers split on
_DAY_CODES = ('MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU')
# A copy of a series' event that becomes one changed occurrence leaves these behind.
_SERIES_ONLY = ('RRULE', 'EXDATE', 'RDATE', 'EXRULE', 'RECURRENCE-ID')


def _name(line):
    """The property name of a content line."""
    return re.split(r'[;:]', line, maxsplit=1)[0].strip().upper()


class Comp:
    """One BEGIN..END block: its lines (unfolded, as written) and the blocks inside it, in order."""

    def __init__(self, name, items=None):
        self.name = name
        self.items = list(items or [])

    def comps(self, name=None):
        return [c for c in self.items if isinstance(c, Comp) and (name is None or c.name == name)]

    def lines(self, prop):
        return [l for l in self.items if isinstance(l, str) and _name(l) == prop]

    def get(self, prop):
        """(params, value) of the first line of that name, or None."""
        hit = self.lines(prop)
        if not hit:
            return None
        _n, params, value = ics._prop(hit[0])
        return params, value

    def props(self):
        """Every line as ics.py reads one: [(NAME, params, value)]."""
        return [ics._prop(l) for l in self.items if isinstance(l, str)]

    def drop(self, *props):
        self.items = [x for x in self.items if not (isinstance(x, str) and _name(x) in props)]

    def add(self, line):
        """A line goes in before the first block inside (an alarm), so lines stay together."""
        at = next((i for i, x in enumerate(self.items) if isinstance(x, Comp)), len(self.items))
        self.items.insert(at, line)

    def set(self, prop, line):
        """Replace the line(s) of that name with one, where the first stood."""
        at = next((i for i, x in enumerate(self.items) if isinstance(x, str) and _name(x) == prop), None)
        self.drop(prop)
        if at is None:
            self.add(line)
        else:
            self.items.insert(min(at, len(self.items)), line)

    def copy(self):
        return Comp(self.name, [x.copy() if isinstance(x, Comp) else x for x in self.items])


def load(text):
    """A calendar file -> its VCALENDAR as a Comp. ValueError when it is not one."""
    root = Comp('')
    stack = [root]
    for line in ics._lines(text):
        head = line[:6].upper()
        if head == 'BEGIN:':
            comp = Comp(line[6:].strip().upper())
            stack[-1].items.append(comp)
            stack.append(comp)
        elif head[:4] == 'END:':
            if len(stack) > 1:
                stack.pop()
        else:
            stack[-1].items.append(line)
    cal = next(iter(root.comps('VCALENDAR')), None)
    if cal is None:
        raise ValueError("not a calendar file")
    return cal


def _fold(line):
    """A content line as the wire wants it: at most 75 bytes a line, a
    continuation starting with one space. Cut between characters, never inside one."""
    if len(line.encode('utf-8')) <= 75:
        return line
    parts, cur, size, limit = [], '', 0, 75
    for ch in line:
        n = len(ch.encode('utf-8'))
        if size + n > limit:
            parts.append(cur)
            cur, size, limit = '', 0, 74
        cur += ch
        size += n
    parts.append(cur)
    return '\r\n '.join(parts)


def dump(cal):
    out = []

    def walk(comp):
        out.append(f'BEGIN:{comp.name}')
        for x in comp.items:
            if isinstance(x, Comp):
                walk(x)
            else:
                out.append(x)
        out.append(f'END:{comp.name}')
    walk(cal)
    return ''.join(_fold(l) + '\r\n' for l in out)


def text(value):
    """A person's words as a TEXT value. A line break becomes the two
    characters \\n, so nothing typed into a title can start a new line."""
    s = str(value or '').replace('\r\n', '\n').replace('\r', '\n')
    s = s.replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,').replace('\n', '\\n')
    return _CONTROL.sub('', s)


def stamp_now():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


# -- time zones ---------------------------------------------------------------------

def _offset(delta):
    minutes = int(delta.total_seconds() // 60)
    sign = '-' if minutes < 0 else '+'
    minutes = abs(minutes)
    return f"{sign}{minutes // 60:02d}{minutes % 60:02d}"


def _transitions(tz, year):
    """The moments in `year` the zone's offset changes: [(naive local start,
    offset before, offset after, is daylight time after, short name after)]."""
    out = []
    t = datetime(year, 1, 1, tzinfo=timezone.utc)
    end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    before = t.astimezone(tz).utcoffset()
    while t < end:
        nxt = t + timedelta(days=1)
        if nxt.astimezone(tz).utcoffset() != before:
            lo, hi = t, nxt
            while hi - lo > timedelta(minutes=1):
                mid = lo + (hi - lo) / 2
                if mid.astimezone(tz).utcoffset() == before:
                    lo = mid
                else:
                    hi = mid
            at = hi.replace(second=0, microsecond=0)
            there = at.astimezone(tz)
            after = there.utcoffset()
            out.append(((at + before).replace(tzinfo=None), before, after, bool(there.dst()), there.tzname() or ''))
            before = after
        t = nxt
    return out


def _yearly(start, again):
    """'BYMONTH=3;BYDAY=2SU' when `start` and next year's `again` are the
    same nth weekday of the same month, else ''."""
    if again is None or start.month != again.month or start.weekday() != again.weekday() \
            or start.time() != again.time():
        return ''
    day = _DAY_CODES[start.weekday()]

    def spots(d):
        nth = [str((d.day - 1) // 7 + 1)]
        if d.day + 7 > _cal.monthrange(d.year, d.month)[1]:
            nth.insert(0, '-1')                       # the last one of the month: the commoner rule
        return nth
    both = [n for n in spots(start) if n in spots(again)]
    return f"BYMONTH={start.month};BYDAY={both[0]}{day}" if both else ''


def vtimezone(zone, year):
    """The VTIMEZONE block for an IANA zone name, good for events around
    `year`; None when the name is not a zone this machine knows."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(zone)
    except Exception:
        return None
    comp = Comp('VTIMEZONE', [f'TZID:{zone}'])

    def observance(start, before, after, daylight, short, rule=''):
        lines = [f'DTSTART:{start:%Y%m%dT%H%M%S}', f'TZOFFSETFROM:{_offset(before)}', f'TZOFFSETTO:{_offset(after)}']
        if short and re.fullmatch(r'[A-Za-z0-9+\-]{1,10}', short):
            lines.append(f'TZNAME:{short}')
        if rule:
            lines.append(f'RRULE:FREQ=YEARLY;{rule}')
        comp.items.append(Comp('DAYLIGHT' if daylight else 'STANDARD', lines))

    first, second = _transitions(tz, year - 1), _transitions(tz, year)
    if not first and not second:
        off = datetime(year, 1, 1, tzinfo=timezone.utc).astimezone(tz).utcoffset()
        observance(datetime(1970, 1, 1), off, off, False, datetime(year, 1, 1, tzinfo=tz).tzname() or '')
        return comp
    rules = [_yearly(a[0], b[0]) for a, b in zip(first, second)] if len(first) == len(second) else []
    if rules and all(rules):
        # A rule read off two years has to hold for the years an event may run into
        # (some zones move their change by a week now and then).
        last = second
        for y in range(year + 1, year + 11):
            nxt = _transitions(tz, y)
            if len(nxt) != len(last) or [_yearly(a[0], b[0]) for a, b in zip(last, nxt)] != rules:
                rules = []
                break
            last = nxt
    if rules and all(rules):
        for (start, before, after, daylight, short), rule in zip(first, rules):
            observance(start, before, after, daylight, short, rule)
        return comp
    # No yearly pattern (a zone that changed its rules): say each change, some years out.
    for y in range(year - 1, year + 11):
        for start, before, after, daylight, short in (first if y == year - 1 else _transitions(tz, y)):
            observance(start, before, after, daylight, short)
    if not comp.comps():
        off = datetime(year, 1, 1, tzinfo=timezone.utc).astimezone(tz).utcoffset()
        observance(datetime(1970, 1, 1), off, off, False, '')
    return comp


def _ensure_zone(cal, zone, year):
    for tzc in cal.comps('VTIMEZONE'):
        got = tzc.get('TZID')
        if got and got[1].strip() == zone:
            return
    block = vtimezone(zone, year)
    if block is not None:
        at = next((i for i, x in enumerate(cal.items) if isinstance(x, Comp)), len(cal.items))
        cal.items.insert(at, block)


# -- one event's lines ----------------------------------------------------------------

def _when(start, end, all_day, zone, to_utc):
    """Stored start and end -> (DTSTART line, DTEND line, the zone name used or '')."""
    if all_day:
        first = date.fromisoformat(start[:10])
        last = date.fromisoformat(end[:10]) + timedelta(days=1)          # the file's end day is exclusive
        return f'DTSTART;VALUE=DATE:{first:%Y%m%d}', f'DTEND;VALUE=DATE:{last:%Y%m%d}', ''
    a = datetime.strptime(start, '%Y-%m-%dT%H:%M')
    b = datetime.strptime(end, '%Y-%m-%dT%H:%M')
    if zone:
        return f'DTSTART;TZID={zone}:{a:%Y%m%dT%H%M%S}', f'DTEND;TZID={zone}:{b:%Y%m%dT%H%M%S}', zone
    return f'DTSTART:{to_utc(a):%Y%m%dT%H%M%SZ}', f'DTEND:{to_utc(b):%Y%m%dT%H%M%SZ}', ''


def apply(cal, ev, change, zone, to_utc):
    """Write the given fields into one VEVENT. `change` holds only what is
    meant to change: title, location, notes, start + end + all_day (stored
    form, local wall time), rrule. `to_utc`: naive local datetime -> aware UTC.
    The caller marks the event as touched() once it is done with it."""
    if 'title' in change:
        ev.set('SUMMARY', f"SUMMARY:{text(change['title'])}")
    if 'location' in change:
        ev.drop('X-APPLE-STRUCTURED-LOCATION')       # the old place's map pin, kept beside the words
        if change['location']:
            ev.set('LOCATION', f"LOCATION:{text(change['location'])}")
        else:
            ev.drop('LOCATION')
    if 'notes' in change:
        ev.drop('X-ALT-DESC')                        # another app's formatted copy of the old words
        if change['notes']:
            ev.set('DESCRIPTION', f"DESCRIPTION:{text(change['notes'])}")
        else:
            ev.drop('DESCRIPTION')
    if 'start' in change and 'end' in change:
        start_line, end_line, used = _when(change['start'], change['end'], change.get('all_day'), zone, to_utc)
        ev.drop('DURATION')
        ev.set('DTSTART', start_line)
        ev.set('DTEND', end_line)
        if used:
            _ensure_zone(cal, used, int(change['start'][:4]))
    if 'rrule' in change:
        if change['rrule']:
            ev.set('RRULE', f"RRULE:{change['rrule']}")
        else:
            ev.drop('RRULE', 'EXDATE', 'RDATE', 'EXRULE')


def touched(ev):
    """An event that was changed says so: other apps and servers go by these."""
    now = stamp_now()
    ev.set('DTSTAMP', f'DTSTAMP:{now}')
    ev.set('LAST-MODIFIED', f'LAST-MODIFIED:{now}')
    seq = ev.get('SEQUENCE')
    try:
        n = int(seq[1].strip()) + 1 if seq else 1
    except ValueError:
        n = 1
    ev.set('SEQUENCE', f'SEQUENCE:{n}')


def new_event(row, uid, zone, to_utc):
    """A calendar file holding one new event made from cleaned fields."""
    now = stamp_now()
    cal = Comp('VCALENDAR', ['VERSION:2.0', f'PRODID:{PRODID}', 'CALSCALE:GREGORIAN'])
    ev = Comp('VEVENT', [f'UID:{uid}', f'DTSTAMP:{now}', f'CREATED:{now}', 'SEQUENCE:0'])
    cal.items.append(ev)
    change = {k: row[k] for k in ('title', 'start', 'end', 'all_day') if k in row}
    for key in ('location', 'notes', 'rrule'):
        if row.get(key):
            change[key] = row[key]
    apply(cal, ev, change, zone, to_utc)
    return cal


# -- a series: moving it, and its exceptions ---------------------------------------------

def _shift_piece(piece, delta, days=None):
    """One DATE or DATE-TIME moved, written the way it was. A time moves by
    `delta`; a bare date moves by `days` (how many days the event's own day
    moved: 09:00 to 08:30 is no day at all, 23:30 to 00:30 is one)."""
    v = piece.strip()
    if len(v) == 8 and v.isdigit():
        step = delta.days if days is None else days
        return (date(int(v[:4]), int(v[4:6]), int(v[6:8])) + timedelta(days=step)).strftime('%Y%m%d')
    stamp, utc = ics._stamp(v)
    return (stamp + delta).strftime('%Y%m%dT%H%M%S') + ('Z' if utc else '')


def shifted(line, delta, days=None):
    """A DTSTART / DTEND / EXDATE / RDATE / RECURRENCE-ID line with every time in it moved (see _shift_piece)."""
    cut = _value_at(line)
    if cut < 0:
        return line
    try:
        return line[:cut + 1] + ','.join(_shift_piece(p, delta, days) for p in line[cut + 1:].split(',') if p.strip())
    except ValueError:
        return line


def _day_of(line):
    """The day a DTSTART line starts on, or None."""
    cut = _value_at(line)
    try:
        stamp, _utc = ics._stamp(line[cut + 1:]) if cut >= 0 else (None, False)
    except ValueError:
        return None
    return stamp.date() if isinstance(stamp, datetime) else stamp


def _value_at(line):
    """Where the value starts: the first ':' that is not inside a quoted parameter."""
    quoted = False
    for i, ch in enumerate(line):
        if ch == '"':
            quoted = not quoted
        elif ch == ':' and not quoted:
            return i
    return -1


def like_start(ev, stamp):
    """`stamp` (a date, or a naive datetime in the event's own zone) written
    the way this event's DTSTART is: ('' or ';TZID=..' or ';VALUE=DATE', value).
    That is the form an EXDATE and a RECURRENCE-ID of the event must have."""
    line = (ev.lines('DTSTART') or [''])[0]
    cut = _value_at(line)
    head, value = (line[:cut], line[cut + 1:].strip()) if cut >= 0 else ('DTSTART', '')
    params = head[len('DTSTART'):]
    if len(value) == 8 and value.isdigit():
        day = stamp.date() if isinstance(stamp, datetime) else stamp
        return params or ';VALUE=DATE', day.strftime('%Y%m%d')
    if not isinstance(stamp, datetime):
        stamp = datetime.combine(stamp, datetime.min.time())
    return params, stamp.strftime('%Y%m%dT%H%M%S') + ('Z' if value.upper().endswith('Z') else '')


def add_exdate(ev, stamp):
    """Take one occurrence (it starts at `stamp`, in the event's own zone) out of the series."""
    params, value = like_start(ev, stamp)
    ev.add(f'EXDATE{params}:{value}')


def move_series(ev, delta, length=None, all_day=False):
    """Move a repeating event by `delta`, exceptions and its end date with it.
    `length`: a new length (a timedelta; for all-day the days it covers), None = keep.
    Returns how many days the event's own day moved, which is what a bare
    date beside it (an all-day exception, an end date) has to move by."""
    start = (ev.lines('DTSTART') or [''])[0]
    new_start = shifted(start, delta)
    was, now = _day_of(start), _day_of(new_start)
    days = (now - was).days if was and now else delta.days
    ev.set('DTSTART', new_start)
    if length is None:
        for line in ev.lines('DTEND'):
            ev.set('DTEND', shifted(line, delta))
    else:
        ev.drop('DURATION')
        end = shifted(new_start, length + timedelta(days=1) if all_day else length)
        ev.set('DTEND', 'DTEND' + end[len('DTSTART'):])
    if delta:
        ev.items = [shifted(x, delta, days) if isinstance(x, str) and _name(x) in ('EXDATE', 'RDATE') else x
                    for x in ev.items]
        rule = (ev.lines('RRULE') or [''])[0]
        m = re.search(r'UNTIL=([0-9TZtz]+)', rule)
        if m:
            try:
                ev.set('RRULE', rule[:m.start(1)] + _shift_piece(m.group(1), delta, days) + rule[m.end(1):])
            except ValueError:
                pass
    return days


def override_of(master, stamp, offset):
    """A copy of a series' event that stands for the ONE occurrence starting
    at `stamp` (in the series' own zone), `offset` after the series' first.
    Its start and end are that occurrence's, in the form the series writes
    its own (same zone, same kind of value). The caller writes what changes."""
    ev = master.copy()
    ev.drop(*_SERIES_ONLY)
    for prop in ('DTSTART', 'DTEND'):
        for line in ev.lines(prop):
            ev.set(prop, shifted(line, offset))
    params, value = like_start(master, stamp)
    at = next((i for i, x in enumerate(ev.items) if isinstance(x, str) and _name(x) == 'UID'), -1)
    ev.items.insert(at + 1, f'RECURRENCE-ID{params}:{value}')
    return ev
