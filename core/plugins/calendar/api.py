# core/plugins/calendar/api.py - the one set of calendar operations
"""
Her tools (tools/calendar_tools.py) and the page's routes (routes/events.py)
both call these, so a rule lives once: what a valid event is, how a range is
listed with repeats expanded, what "remove" means for a repeating event.

Every refusal is a CalError whose text is fit to show the person or the model.

A calendar can have links: a Google calendar (two-way) or an ICS address
(read-only) whose events are copied in by sync.py. An event made here goes to
the calendar's "new events go to" choice: the local file, or a Google link.
A change to a Google event is sent to Google first and the copy is refreshed
after, so Google stays the authority and nothing is ever merged.

A repeating event, the same from every caller: its id alone means the whole
series, its id plus a day means that one occurrence.

Time: the person's local wall time, no offsets (see store.py). "Now" is read
in Sapphire's USER_TIMEZONE; when that is unset (or still the 'UTC' default)
the machine's own clock is used, the same rule as the Google Calendar plugin.
A time that arrives WITH an offset or a Z is converted to that local time.
"""
import logging
import re
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

from . import google, ics, repeat, store, sync
from .errors import CalError

logger = logging.getLogger(__name__)

MAX_RANGE_DAYS = 800          # a year view asks for ~372
MAX_SPAN_DAYS = 366
_NAME_RE = re.compile(r'[a-z0-9][a-z0-9_-]{0,31}')
_DAY_RE = re.compile(r'\d{4}-\d{2}-\d{2}')
_CLOCK_RE = re.compile(r'(\d{1,2}):(\d{2})')
_REPEAT_KEYS = ('repeat', 'every', 'days', 'until', 'times')
_FREQ_WORDS = {'daily': 'DAILY', 'weekly': 'WEEKLY', 'monthly': 'MONTHLY', 'yearly': 'YEARLY',
               'day': 'DAILY', 'week': 'WEEKLY', 'month': 'MONTHLY', 'year': 'YEARLY', 'annually': 'YEARLY'}
_DAY_WORDS = {'mo': 0, 'tu': 1, 'we': 2, 'th': 3, 'fr': 4, 'sa': 5, 'su': 6}


# -- time -------------------------------------------------------------------

def tz_name():
    """Sapphire's configured zone, '' when it is unset (the machine's clock is used)."""
    try:
        import config as cfg
        name = (getattr(cfg, 'USER_TIMEZONE', '') or '').strip()
        return '' if name.upper() == 'UTC' else name
    except Exception:
        return ''


def _zone():
    name = tz_name()
    if name:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(name)
        except Exception as e:
            logger.warning(f"[calendar] unknown time zone {name!r}, using the machine's clock: {e}")
    return None


def zone_name():
    """The IANA name of our local zone: Sapphire's setting, else the machine's;
    '' when the machine does not say (Windows)."""
    name = tz_name()
    if name:
        return name
    try:
        key = getattr(datetime.now().astimezone().tzinfo, 'key', None)
        if key:
            return key
        link = Path('/etc/localtime')
        if link.is_symlink():
            target = str(link.resolve())
            if 'zoneinfo/' in target:
                return target.split('zoneinfo/')[-1]
    except Exception as e:
        logger.debug(f"[calendar] local zone name not found: {e}")
    return ''


def _named(name):
    """A zone by name; '' is our local one (None = the machine's own clock)."""
    if not name:
        return _zone()
    from zoneinfo import ZoneInfo
    return ZoneInfo(name)


def _aware(naive, zone=''):
    tz = _named(zone)
    return naive.replace(tzinfo=tz) if tz else naive.astimezone()


def to_local(aware):
    """An instant as local wall time, naive, to the minute."""
    zone = _zone()
    return (aware.astimezone(zone) if zone else aware.astimezone()).replace(tzinfo=None, second=0, microsecond=0)


def convert(stamp, from_zone='', to_zone=''):
    """A naive wall time in one zone as the wall time in another ('' = local)."""
    if from_zone == to_zone:
        return stamp
    try:
        aware = _aware(stamp, from_zone)
        tz = _named(to_zone)
        return (aware.astimezone(tz) if tz else aware.astimezone()).replace(tzinfo=None)
    except Exception as e:
        logger.warning(f"[calendar] could not convert a time from {from_zone!r} to {to_zone!r}: {e}")
        return stamp


def offset_of(naive):
    """'-05:00' for a local wall time."""
    text = _aware(naive).strftime('%z') or '+0000'
    return f"{text[:3]}:{text[3:5]}"


def rfc3339(day):
    """The start of a local day as an instant Google reads."""
    return _aware(datetime.combine(day, dtime())).isoformat()


def now():
    """Local wall time, naive."""
    zone = _zone()
    return (datetime.now(zone) if zone else datetime.now()).replace(tzinfo=None)


def today():
    return now().date()


def parse_day(value, what='date'):
    """'YYYY-MM-DD' (or today / tomorrow / yesterday) -> date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or '').strip().lower()
    if text in ('today', 'tomorrow', 'yesterday'):
        return today() + timedelta(days={'today': 0, 'tomorrow': 1, 'yesterday': -1}[text])
    if 'T' in text.upper() or ' ' in text:
        text = re.split(r'[T ]', text.upper(), maxsplit=1)[0]
    if not _DAY_RE.fullmatch(text):
        raise CalError(f"{what} must be YYYY-MM-DD (got {value!r}).")
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise CalError(f"{what} {value!r} is not a real date.")


def _parse_when(value, what):
    """A start or end as given -> date (a day) or naive local datetime (a time)."""
    text = str(value or '').strip()
    if not text:
        raise CalError(f"{what} is missing.")
    if text.lower() in ('today', 'tomorrow', 'yesterday') or _DAY_RE.fullmatch(text):
        return parse_day(text, what)
    try:
        dt = datetime.fromisoformat(text.replace(' ', 'T', 1))
    except ValueError:
        raise CalError(f"{what} must be YYYY-MM-DD HH:MM, or YYYY-MM-DD for an all-day event (got {value!r}).")
    if dt.tzinfo is not None:
        zone = _zone()
        dt = (dt.astimezone(zone) if zone else dt.astimezone()).replace(tzinfo=None)
    return dt.replace(second=0, microsecond=0)


def _fmt(value):
    return value.strftime('%Y-%m-%dT%H:%M') if isinstance(value, datetime) else value.isoformat()


def _load(text):
    """A stored start/end back to date or datetime."""
    return datetime.strptime(text, '%Y-%m-%dT%H:%M') if 'T' in text else date.fromisoformat(text)


# -- calendars --------------------------------------------------------------

def clean_name(name):
    text = str(name or '').strip().lower().replace(' ', '-')
    if not _NAME_RE.fullmatch(text) or text == 'none':
        raise CalError("A calendar name is 1 to 32 letters, digits, '-' or '_' (and not 'none').")
    return text


def calendars():
    return store.get().calendars()


def add_calendar(name):
    name = clean_name(name)
    store.get().add_calendar(name)
    return name


def delete_calendar(name):
    name = str(name or '').strip()
    if not store.get().has_calendar(name):
        name = clean_name(name)
    if name == store.DEFAULT_CALENDAR:
        raise CalError("The default calendar stays. Remove its events instead.")
    if not store.get().has_calendar(name):
        raise CalError(f"There is no calendar named '{name}'.")
    removed = store.get().delete_calendar(name)
    if google.connected(name):
        # its Google account is still connected: without this mark the next
        # refresh would link it straight back and the calendar would reappear
        store.get().set_meta(f"gauto:{name}", 'off')
    return removed


def _need_calendar(name):
    """The calendar a caller named. One that exists is taken as written (an
    older Google account's name may not fit today's naming rule)."""
    if name in (None, ''):
        return store.DEFAULT_CALENDAR
    text = str(name).strip()
    if store.get().has_calendar(text):
        return text
    text = clean_name(text)
    if not store.get().has_calendar(text):
        raise CalError(f"There is no calendar named '{text}'.")
    return text


# -- events: what a valid one is ----------------------------------------------

def _text(fields, key, limit, base=''):
    if key not in fields or fields[key] is None:
        return base
    return str(fields[key]).strip()[:limit]


def _flag(value):
    if isinstance(value, str):
        return value.strip().lower() in ('1', 'true', 'yes', 'on')
    return bool(value)


def _weekdays(value):
    """'mon,wed' / ['MO','WE'] / [0, 2] -> sorted weekday numbers (Mon = 0)."""
    if value in (None, '', []):
        return []
    items = value if isinstance(value, (list, tuple)) else re.split(r'[,\s]+', str(value))
    out = set()
    for item in items:
        if isinstance(item, int) and not isinstance(item, bool) and 0 <= item <= 6:
            out.add(item)
            continue
        word = str(item).strip().lower()[:2]
        if not word:
            continue
        if word not in _DAY_WORDS:
            raise CalError(f"days must be weekday names like mon,wed,fri (got {item!r}).")
        out.add(_DAY_WORDS[word])
    return sorted(out)


def _rrule(fields, base_rule, start):
    """The RRULE the fields ask for. Fields left out keep the old rule's value."""
    old = None
    try:
        old = repeat.parse(base_rule)
    except ValueError:
        pass
    word = fields.get('repeat')
    if word is None:
        if not old:
            raise CalError("Say how it repeats: repeat = daily, weekly, monthly or yearly.")
        freq = old['freq']
    else:
        word = str(word).strip().lower()
        if word in ('', 'none', 'no', 'never', 'once', 'false'):
            return ''
        freq = _FREQ_WORDS.get(word)
        if not freq:
            raise CalError(f"repeat must be daily, weekly, monthly, yearly or none (got {fields.get('repeat')!r}).")
    same = bool(old and old['freq'] == freq)
    every = fields.get('every')
    if every in (None, ''):
        every = old['interval'] if same else 1
    days = _weekdays(fields['days']) if fields.get('days') not in (None, '') else (
        (old['byday'] or []) if same and 'days' not in fields else [])
    if freq != 'WEEKLY':
        days = []
    times, until = fields.get('times'), fields.get('until')
    if times in (None, '', 0) and until in (None, ''):
        if 'times' not in fields and 'until' not in fields and same:
            times, until = old['count'], old['until']
        else:
            times, until = None, None
    elif times not in (None, '', 0):
        until = None                                # one end, and a count is the more exact
    else:
        times = None
        until = parse_day(until, 'until')
    first = start.date() if isinstance(start, datetime) else start
    if until and until < first:
        raise CalError("until is before the event starts.")
    if days and first.weekday() not in days:
        names = ', '.join(repeat.DAY_NAMES[d] for d in days)
        raise CalError(f"{first.isoformat()} is a {repeat.DAY_NAMES[first.weekday()]}, which is not one of the "
                       f"repeat days ({names}). Start on one of those days, or add it to days.")
    if days == [first.weekday()]:
        days = []                      # "weekly on its own weekday" has one spelling: no BYDAY
    try:
        return repeat.build(freq, every, times, until, days)
    except ValueError as e:
        raise CalError(str(e))


def _clean(fields, base=None):
    """Caller's fields (+ the stored event on an update) -> the columns to write."""
    fields = dict(fields or {})
    out = {}
    title = _text(fields, 'title', 200, base['title'] if base else '')
    if not title:
        raise CalError("An event needs a title.")
    out['title'] = title
    out['location'] = _text(fields, 'location', 200, base['location'] if base else '')
    out['notes'] = _text(fields, 'notes', 4000, base['notes'] if base else '')

    old_start = _load(base['start']) if base else None
    old_end = _load(base['end']) if base else None
    start = _parse_when(fields['start'], 'start') if fields.get('start') not in (None, '') else old_start
    if start is None:
        raise CalError("An event needs a start: YYYY-MM-DD HH:MM, or YYYY-MM-DD for all day.")
    if fields.get('all_day') is not None:
        all_day = _flag(fields['all_day'])
    elif fields.get('start') not in (None, ''):
        all_day = not isinstance(start, datetime)
    else:
        all_day = base['all_day']

    raw_end = fields.get('end')
    if all_day:
        if isinstance(start, datetime):
            start = start.date()
        if raw_end not in (None, ''):
            end = parse_day(raw_end, 'end')
        elif base and base['all_day']:
            end = start + (old_end - old_start)                 # a moved event keeps its length
        else:
            end = start
        if end < start:
            raise CalError("end is before start.")
    else:
        if not isinstance(start, datetime):
            raise CalError("A timed event needs a start time: YYYY-MM-DD HH:MM (or make it all day).")
        if raw_end not in (None, ''):
            clock = _CLOCK_RE.fullmatch(str(raw_end).strip())
            if clock:                                           # '15:30' = that time on the start day
                if int(clock.group(1)) > 23 or int(clock.group(2)) > 59:
                    raise CalError(f"end time {raw_end!r} is not a real time.")
                end = start.replace(hour=int(clock.group(1)), minute=int(clock.group(2)))
            else:
                end = _parse_when(raw_end, 'end')
                if not isinstance(end, datetime):
                    raise CalError("A timed event needs an end time: YYYY-MM-DD HH:MM, or just HH:MM.")
        elif base and not base['all_day']:
            end = start + (old_end - old_start)
        else:
            end = start + timedelta(hours=1)
        if end <= start:
            raise CalError("end must be after start.")
    if (end - start).days > MAX_SPAN_DAYS:
        raise CalError("An event can be at most a year long.")
    out.update(start=_fmt(start), end=_fmt(end), all_day=all_day)

    base_rule = base['rrule'] if base else ''
    if any(k in fields for k in _REPEAT_KEYS):
        out['rrule'] = _rrule(fields, base_rule, start)
    else:
        out['rrule'] = base_rule
        try:
            known = bool(repeat.parse(base_rule))
        except ValueError:
            known = False
        if known:
            _rrule({}, base_rule, start)      # a moved start must still fit the rule (its days, its until)
    # Exceptions are days of the old series; a new start day or rule makes a new series.
    if base and (not _same_rule(out['rrule'], base_rule, start) or out['start'][:10] != base['start'][:10]):
        out['exdates'] = []
    return out


def _same_rule(a, b, start):
    """Two RRULE strings that mean the same repeat for an event starting at
    `start`, whatever their spelling."""
    if a == b:
        return True
    weekday = (start.date() if isinstance(start, datetime) else start).weekday()

    def meaning(rule):
        p = repeat.parse(rule)
        if p and p['byday'] == [weekday]:
            p['byday'] = None                    # weekly on the start's own weekday
        return p
    try:
        return meaning(a) == meaning(b)
    except ValueError:
        return False


# -- events: read -------------------------------------------------------------

def _occurrence(ev, occ_start, links=None):
    """One occurrence of a stored event, as the page and the tools see it."""
    start, end = _load(ev['start']), _load(ev['end'])
    occ_end = occ_start + (end - start)
    link = None
    if ev['source'] != 'local':
        link = (links or {}).get(ev['source']) if links is not None else store.get().get_link(ev['source'])
    series = bool(ev.get('series'))
    rule = None
    if ev['rrule']:
        try:
            p = repeat.parse(ev['rrule'])
            rule = {'repeat': p['freq'].lower(), 'every': p['interval'], 'days': p['byday'] or [],
                    'times': p['count'], 'until': p['until'].isoformat() if p['until'] else None}
        except ValueError:
            rule = {'repeat': 'custom', 'every': 1, 'days': [], 'times': None, 'until': None}
    return {
        'id': ev['id'], 'calendar': ev['calendar'], 'title': ev['title'],
        'start': _fmt(occ_start), 'end': _fmt(occ_end), 'all_day': ev['all_day'],
        'location': ev['location'], 'notes': ev['notes'],
        'repeats': bool(ev['rrule']) or series,
        'repeat_text': repeat.describe(ev['rrule']) or ('repeats' if series else ''), 'rule': rule,
        'series_start': ev['start'], 'series_end': ev['end'],
        'skipped': len(ev['exdates']),         # occurrences removed from the series
        'skipped_days': list(ev['exdates']),
        'day': _fmt(occ_start)[:10],           # the day that names this occurrence (for acting on just it)
        'by': ev['created_by'],
        # where it lives: 'local', or the kind of the link it is a copy from
        'source': 'local' if ev['source'] == 'local' else (link['kind'] if link else 'link'),
        'link': '' if ev['source'] == 'local' else ev['source'],
        'link_name': link['name'] if link else '',
        'color': link['color'] if link else None,
        'editable': ev['source'] == 'local' or bool(link and link['writable']),
        'guests': [g for g in ev.get('guests') or [] if not g.get('self')],
        'url': ev.get('url') or '',
    }


def _occurrences(ev, first_day, last_day, links=None):
    start, end = _load(ev['start']), _load(ev['end'])
    span = (end.date() - start.date()).days if isinstance(start, datetime) else (end - start).days
    # A linked event kept in another zone: expand it there, then bring each
    # occurrence home (a day of slack each side, since the day can differ).
    foreign = ev['tz'] if (ev['source'] != 'local' and ev['tz'] and isinstance(start, datetime)) else ''
    slack = 1 if foreign else 0
    lo, hi = first_day - timedelta(days=span + slack), last_day + timedelta(days=slack)
    try:
        hits = repeat.expand(start, ev['rrule'], lo, hi, ev['exdates'])
    except ValueError as e:
        logger.warning(f"[calendar] event {ev['id']}: repeat rule not understood, shown once ({e})")
        hits = repeat.expand(start, '', lo, hi)
    if not foreign:
        return [_occurrence(ev, h, links) for h in hits]
    out = []
    for h in hits:
        home = convert(h, foreign, '')
        if home.date() <= last_day and (home + (end - start)).date() >= first_day:
            out.append(_occurrence(ev, home, links))
    return out


def list_range(first_day, last_day, calendar=None, query=None):
    """Every occurrence that touches a day in [first_day, last_day], in order.
    calendar=None means all of them (the page); the tools always name one."""
    first_day, last_day = parse_day(first_day, 'start'), parse_day(last_day, 'end')
    if last_day < first_day:
        raise CalError("end is before start.")
    if (last_day - first_day).days > MAX_RANGE_DAYS:
        raise CalError(f"That range is too long (at most {MAX_RANGE_DAYS} days).")
    if calendar is not None:
        calendar = _need_calendar(calendar)
    needle = str(query or '').strip().lower()
    links = {l['id']: l for l in store.get().links()}
    out = []
    # a day wider than asked: a linked event kept in another zone can start on the day before or after
    lo, hi = (first_day - timedelta(days=1)).isoformat(), (last_day + timedelta(days=1)).isoformat()
    for ev in store.get().candidates(lo, hi, calendar):
        if needle and needle not in f"{ev['title']}\n{ev['location']}\n{ev['notes']}".lower():
            continue
        out.extend(_occurrences(ev, first_day, last_day, links))
    out.sort(key=lambda o: (o['start'][:10], 0 if o['all_day'] else 1, o['start'], o['title'].lower(), o['id']))
    return out


def get_event(event_id, calendar=None):
    """The stored event (its first occurrence's shape). `calendar` set = it must be in that one."""
    ev = store.get().get(str(event_id or '').strip())
    if not ev or (calendar is not None and ev['calendar'] != calendar):
        where = f" in the '{calendar}' calendar" if calendar is not None else ''
        raise CalError(f"There is no event with id {event_id!r}{where}. List the calendar to see the ids.")
    return ev


def describe(ev):
    return _occurrence(ev, _load(ev['start']))


# -- events: write ------------------------------------------------------------

_GOOGLE_READONLY = "That Google calendar is read-only for this account."


def _link_of(ev):
    """The link a copied event came from; None for a local one. A read-only
    source refuses here, in words."""
    if ev['source'] == 'local':
        return None
    link = store.get().get_link(ev['source'])
    if link is None:
        raise CalError(f"'{ev['title']}' came from a linked calendar that is no longer linked.")
    if link['kind'] == 'ics':
        raise CalError(f"'{ev['title']}' comes from the linked calendar '{link['name']}', which is read-only. "
                       "Change it where that calendar is kept.")
    if not link['writable']:
        raise CalError(_GOOGLE_READONLY)
    return link


def target_of(calendar):
    """Where a new event of this calendar goes: a writable Google link, or None for the local file."""
    link_id = store.get().meta(f"target:{calendar}")
    link = store.get().get_link(link_id) if link_id else None
    if link and link['calendar'] == calendar and link['kind'] == 'google' and link['writable']:
        return link
    return None


def _pick_target(calendar, to):
    if to in (None, ''):
        return target_of(calendar)
    word = str(to).strip().lower()
    if word == 'local':
        return None
    links = store.get().links(calendar)
    hit = [l for l in links if l['id'] == str(to).strip()] or [l for l in links if l['name'].lower() == word]
    if not hit:
        names = ', '.join(["local"] + [l['name'] for l in links if l['writable']])
        raise CalError(f"This calendar has no link named '{to}'. New events can go to: {names}.")
    if hit[0]['kind'] != 'google' or not hit[0]['writable']:
        raise CalError(f"'{hit[0]['name']}' is read-only, so an event cannot be added to it.")
    return hit[0]


def _days_of(row):
    return parse_day(row['start'][:10]), parse_day(row['end'][:10])


def _refreshed(link, need):
    """Refresh a Google link after a change. The change is already made at
    Google, so a refresh that fails is logged, not raised."""
    try:
        sync.pull(link, need)
    except CalError as e:
        logger.warning(f"[calendar] changed at Google but the copy did not refresh: {e}")


def _notify(ev):
    return any(not g.get('self') for g in ev.get('guests') or [])


def create(fields, calendar=None, by='user', to=None, guests=None):
    """Add an event. `to`: 'local', or a link's id or name; default the
    calendar's own choice. `guests`: Google attendee dicts (a Google link only)."""
    fields = dict(fields or {})
    calendar = _need_calendar(calendar if calendar is not None else fields.get('calendar'))
    row = _clean(fields)
    link = _pick_target(calendar, to if to is not None else fields.get('to'))
    if link is None:
        if guests:
            raise CalError("Guests are invited through a Google calendar, and this event would be saved locally. "
                           "Link a Google calendar on the Calendar page first.")
        row.update(calendar=calendar, created_by=by, tz=tz_name(), source='local')
        return store.get().insert(row)
    body = google.body(dict(row, attendees=guests or []), zone_name(), offset_of)
    item = google.insert(link['calendar'], link['ref'], body, notify=bool(guests))
    _refreshed(link, _days_of(row))
    s = store.get()
    ev = s.find(link['id'], item.get('id', '')) or next(iter(s.series(link['id'], item.get('id', ''))), None)
    if ev is None:                                   # the refresh failed: keep what Google answered
        got = google.to_row(item, to_local)
        if got and not item.get('recurrence'):
            s.sync_rows(link, [got], prune=False)
            ev = s.find(link['id'], got['source_ref'])
    if ev is None:
        raise CalError(f"'{row['title']}' was added to Google, but it could not be read back yet. "
                       "It will show after the next refresh.")
    return ev


def _local_occurrence(ev, day):
    """The start of the occurrence of a local repeating event on `day`, or a refusal."""
    if not ev['rrule']:
        raise CalError(f"'{ev['title']}' does not repeat. Leave the date out.")
    hits = repeat.expand(_load(ev['start']), ev['rrule'], day, day, ev['exdates'])
    if not hits:
        raise CalError(f"'{ev['title']}' has no occurrence that starts on {day.isoformat()}.")
    return hits[0]


def _series_member(link, ev, day):
    """The stored occurrence of a Google repeating event that starts on `day`."""
    if not ev['series']:
        raise CalError(f"'{ev['title']}' does not repeat. Leave the date out.")
    for row in store.get().series(link['id'], ev['series']):
        if row['start'][:10] == day.isoformat():
            return row
    raise CalError(f"'{ev['title']}' has no occurrence that starts on {day.isoformat()}.")


def update(event_id, fields, calendar=None, day=None):
    """Change an event. A repeating one: the whole series, or with `day` only
    the occurrence that starts on that day. `calendar` set = the caller may
    only touch events of that calendar (the tools)."""
    ev = get_event(event_id, calendar)
    fields = dict(fields or {})
    link = _link_of(ev)
    day = parse_day(day) if day not in (None, '') else None
    if link is not None:
        return _google_update(link, ev, fields, day)
    if day is not None:
        # One occurrence of a local series becomes its own event, and that day leaves the series.
        occ = _local_occurrence(ev, day)
        base = dict(ev, start=_fmt(occ), end=_fmt(occ + (_load(ev['end']) - _load(ev['start']))), rrule='', exdates=[])
        row = _clean({k: v for k, v in fields.items() if k not in _REPEAT_KEYS}, base)
        row.pop('exdates', None)
        row.update(rrule='', calendar=ev['calendar'], created_by=ev['created_by'], tz=ev['tz'], source='local')
        new = store.get().insert(row)
        store.get().update(ev['id'], {'exdates': list(ev['exdates']) + [day.isoformat()]})
        return new
    row = _clean(fields, ev)
    if calendar is None and fields.get('calendar') not in (None, ''):
        row['calendar'] = _need_calendar(fields['calendar'])     # the page may move an event
    return store.get().update(ev['id'], row)


def _google_update(link, ev, fields, day):
    scope, cal_id = link['calendar'], link['ref']
    timing = any(fields.get(k) not in (None, '') for k in ('start', 'end')) or fields.get('all_day') is not None
    repeating = any(k in fields for k in _REPEAT_KEYS)
    target = _series_member(link, ev, day) if day is not None else ev
    whole = bool(ev['series']) and day is None
    if repeating and not whole and ev['series']:
        raise CalError("One occurrence cannot have its own repeat. Leave the date out to change the whole series.")
    row = _clean({k: v for k, v in fields.items() if k not in _REPEAT_KEYS}, target)
    change = {k: row[k] for k in ('title', 'location', 'notes') if k in fields and fields[k] is not None}
    if not whole:
        if timing:
            change.update(start=row['start'], end=row['end'], all_day=row['all_day'])
        if repeating:                                 # a one-time Google event gains (or loses) a repeat
            change['rrule'] = _rrule(fields, '', _load(row['start']))
        if not change:
            raise CalError("Nothing to change.")
        google.patch(scope, cal_id, target['source_ref'],
                     google.body(change, zone_name(), offset_of, patching=True), notify=_notify(target))
    else:
        master = google.get(scope, cal_id, ev['series'])
        first = google.to_row(master, to_local)
        if timing:
            if first is None or row['all_day'] != target['all_day']:
                raise CalError("Switching a whole repeating series between timed and all-day is not supported here. "
                               "Change one occurrence (give its date), or change the series in Google.")
            moved = _load(row['start']) - _load(target['start'])
            length = _load(row['end']) - _load(row['start'])
            new_start = _load(first['start']) + moved
            change.update(start=_fmt(new_start), end=_fmt(new_start + length), all_day=row['all_day'])
        if repeating:
            old_rule = next((r[6:] for r in master.get('recurrence') or [] if str(r).upper().startswith('RRULE:')), '')
            change['rrule'] = _rrule(fields, old_rule, _load(change.get('start') or (first or target)['start']))
        if not change:
            raise CalError("Nothing to change.")
        google.patch(scope, cal_id, ev['series'],
                     google.body(change, zone_name(), offset_of, patching=True), notify=_notify(ev))
    _refreshed(link, _days_of(row))
    return store.get().get(target['id']) or dict(target, **{k: v for k, v in change.items() if k != 'rrule'})


def remove(event_id, day=None, calendar=None):
    """Remove an event, or with `day` just that one occurrence of a repeating
    event. Returns ('event' | 'occurrence', the event as it was)."""
    ev = get_event(event_id, calendar)
    link = _link_of(ev)
    day = parse_day(day) if day not in (None, '') else None
    if link is not None:
        if day is not None:
            target = _series_member(link, ev, day)
            google.delete(link['calendar'], link['ref'], target['source_ref'], notify=_notify(target))
            store.get().delete(target['id'])
            return 'occurrence', ev
        google.delete(link['calendar'], link['ref'], ev['series'] or ev['source_ref'], notify=_notify(ev))
        if ev['series']:
            for row in store.get().series(link['id'], ev['series']):
                store.get().delete(row['id'])
        store.get().delete(ev['id'])
        return 'event', ev
    if day is None:
        store.get().delete(ev['id'])
        return 'event', ev
    _local_occurrence(ev, day)
    store.get().update(ev['id'], {'exdates': list(ev['exdates']) + [day.isoformat()]})
    return 'occurrence', ev


# -- links ----------------------------------------------------------------------

def refresh(calendar=None, need=None, force=False, wait=True):
    """Bring linked calendars up to date (sync.refresh). `calendar` as a caller named it."""
    return sync.refresh(_need_calendar(calendar) if calendar is not None else None, need, force, wait)


def _link_view(link, target_id):
    host = ''
    if link['kind'] == 'ics':
        m = re.match(r'https?://([^/\s:]+)', link['ref'], re.I)
        host = m.group(1) if m else ''
    return {
        'id': link['id'], 'calendar': link['calendar'], 'kind': link['kind'], 'name': link['name'],
        'color': link['color'], 'writable': link['writable'], 'synced_at': link['synced_at'],
        'error': link['error'], 'note': store.get().meta(f"note:{link['id']}"),
        # an ICS address can be a private one: the page gets its host, never the address
        'where': host if link['kind'] == 'ics' else link['ref'],
        'target': link['id'] == target_id, 'refreshing': sync.running(link['id']),
    }


def links(calendar=None):
    """The links of one calendar (or all), as the page shows them."""
    s = store.get()
    names = [_need_calendar(calendar)] if calendar is not None else [c['name'] for c in s.calendars()]
    out = []
    for name in names:
        target = target_of(name)
        out.extend(_link_view(l, target['id'] if target else '') for l in s.links(name))
    return out


def _next_color(calendar):
    used = {l['color'] for l in store.get().links(calendar)}
    return next((c for c in (1, 2, 3, 4, 5) if c not in used), 1 + len(used) % 5)


def _link_name(text, fallback):
    name = ' '.join(str(text or '').split())[:40]
    return name or fallback


def add_ics(calendar, url, name=''):
    """Link a calendar address. It is fetched once here, so a bad address is refused now."""
    calendar = _need_calendar(calendar)
    url = ics.clean_url(url)
    s = store.get()
    if any(l['kind'] == 'ics' and l['ref'] == url for l in s.links(calendar)):
        raise CalError("That address is already linked to this calendar.")
    own_name, rows, notes = ics.parse(ics.fetch(url), convert, zone_name())
    link = s.add_link({'calendar': calendar, 'kind': 'ics', 'name': _link_name(name or own_name, 'Linked calendar'),
                       'ref': url, 'color': _next_color(calendar), 'writable': False})
    s.sync_rows(link, rows)
    s.set_meta(f"note:{link['id']}", ' '.join(notes))
    import time as _time
    return s.update_link(link['id'], {'synced_at': _time.time()})


def google_calendars(calendar):
    """The Google account connected under this calendar's name and what it offers."""
    calendar = _need_calendar(calendar)
    if not google.connected(calendar):
        return {'connected': False, 'calendars': []}
    linked = {l['ref'] for l in store.get().links(calendar) if l['kind'] == 'google'}
    acct = next((a for a in google.accounts() if a['scope'] == calendar), {'calendar_id': 'primary'})
    out = []
    for c in google.calendar_list(calendar):
        is_linked = c['id'] in linked or (c['primary'] and ('primary' in linked or acct['calendar_id'] in linked))
        out.append(dict(c, linked=is_linked))
    return {'connected': True, 'calendars': out}


def add_google(calendar, google_id, name=''):
    """Link one more calendar of the connected Google account."""
    calendar = _need_calendar(calendar)
    google_id = str(google_id or '').strip()
    if not google_id:
        raise CalError("Pick which Google calendar to link.")
    if not google.connected(calendar):
        raise CalError("Connect Google for this calendar first.")
    s = store.get()
    if any(l['kind'] == 'google' and l['ref'] == google_id for l in s.links(calendar)):
        raise CalError("That Google calendar is already linked.")
    found = next((c for c in google.calendar_list(calendar) if c['id'] == google_id), None)
    if found is None:
        raise CalError("That calendar is not in the connected Google account.")
    link = s.add_link({'calendar': calendar, 'kind': 'google', 'name': _link_name(name or found['name'], 'Google'),
                       'ref': google_id, 'color': _next_color(calendar), 'writable': found['writable']})
    s.set_meta(f"gauto:{calendar}", '')
    try:
        return sync.pull(link) or link
    except CalError:
        return s.get_link(link['id'])          # linked; the error is on the link for the page to show


def remove_link(link_id):
    """Unlink. The copies go; nothing is touched at the source. Returns how many events went."""
    s = store.get()
    link = s.get_link(str(link_id or ''))
    if link is None:
        raise CalError("That link is already gone.")
    if s.meta(f"target:{link['calendar']}") == link['id']:
        s.set_meta(f"target:{link['calendar']}", '')
    if link['kind'] == 'google' and not any(l['kind'] == 'google' and l['id'] != link['id']
                                            for l in s.links(link['calendar'])):
        s.set_meta(f"gauto:{link['calendar']}", 'off')       # removed by hand: do not link it again unasked
    s.set_meta(f"note:{link['id']}", '')
    return s.delete_link(link['id'])


def update_link(link_id, fields):
    """Rename a link or change its colour."""
    s = store.get()
    link = s.get_link(str(link_id or ''))
    if link is None:
        raise CalError("That link is gone.")
    change = {}
    if (fields or {}).get('name') not in (None, ''):
        change['name'] = _link_name(fields['name'], link['name'])
    if (fields or {}).get('color') is not None:
        try:
            change['color'] = int(fields['color']) % 6
        except (TypeError, ValueError):
            raise CalError("color is a number from 0 to 5.")
    return s.update_link(link['id'], change)


def set_target(calendar, to):
    """Choose where this calendar's new events go: 'local' or a writable Google link."""
    calendar = _need_calendar(calendar)
    link = _pick_target(calendar, to or 'local')
    store.get().set_meta(f"target:{calendar}", link['id'] if link else '')
    return link['id'] if link else ''


def google_connected(calendar):
    """Called when a Google account was just connected under this name: link it."""
    store.get().set_meta(f"gauto:{calendar}", '')
    sync.ensure_google()
