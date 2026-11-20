# core/plugins/calendar/ics.py - a calendar address (.ics / webcal) as a link
"""
Read-only by nature: an ICS address is a file somebody else publishes
(holidays, a team calendar, the "secret address in iCal format" of a Google
or Outlook calendar). fetch() downloads it, parse() turns its VEVENTs into
the fields of stored rows.

What parse() keeps from RFC 5545: UID, SUMMARY, DESCRIPTION, LOCATION, URL,
DTSTART, DTEND or DURATION, RRULE, EXDATE, RECURRENCE-ID, STATUS, and the
guests (ATTENDEE: name, address, answer), so an event with guests shows them.

Time zones. A time with a Z or a TZID is a real instant somewhere else. A
one-time event is turned into local time here. A repeating one is kept in
its own zone (the row's `tz`) and api.py converts each occurrence, so a
weekly 9:00 London meeting stays right across both countries' clock changes.
A TZID that is neither an IANA name nor one of the common Windows names is
read as local time and counted in the notes parse() returns.

A changed occurrence (RECURRENCE-ID) becomes its own row, and its day is
taken out of the series.
"""
import logging
import re
from datetime import date, datetime, timedelta

import requests
from core import net

from .errors import CalError

logger = logging.getLogger(__name__)

MAX_BYTES = 5 * 1024 * 1024
MAX_EVENTS = 10000

# As a kind of link (sources.py): the whole file each time, read-only.
KIND = 'ics'
WINDOWED = False
GUESTS = False
EVERY, RETRY = 900, 300
MAX_GUESTS = 100
_ANSWERS = {'ACCEPTED': 'yes', 'DECLINED': 'no', 'TENTATIVE': 'maybe'}
_DURATION = re.compile(r'([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?')

# Outlook and Exchange name zones their own way. The common ones.
_WINDOWS = {
    'utc': 'UTC', 'gmt standard time': 'Europe/London', 'greenwich standard time': 'Atlantic/Reykjavik',
    'w. europe standard time': 'Europe/Berlin', 'central europe standard time': 'Europe/Budapest',
    'romance standard time': 'Europe/Paris', 'central european standard time': 'Europe/Warsaw',
    'e. europe standard time': 'Europe/Chisinau', 'fle standard time': 'Europe/Kiev',
    'gtb standard time': 'Europe/Bucharest', 'russian standard time': 'Europe/Moscow',
    'turkey standard time': 'Europe/Istanbul', 'israel standard time': 'Asia/Jerusalem',
    'south africa standard time': 'Africa/Johannesburg', 'egypt standard time': 'Africa/Cairo',
    'arabian standard time': 'Asia/Dubai', 'arab standard time': 'Asia/Riyadh',
    'india standard time': 'Asia/Kolkata', 'pakistan standard time': 'Asia/Karachi',
    'se asia standard time': 'Asia/Bangkok', 'china standard time': 'Asia/Shanghai',
    'singapore standard time': 'Asia/Singapore', 'taipei standard time': 'Asia/Taipei',
    'tokyo standard time': 'Asia/Tokyo', 'korea standard time': 'Asia/Seoul',
    'w. australia standard time': 'Australia/Perth', 'aus eastern standard time': 'Australia/Sydney',
    'e. australia standard time': 'Australia/Brisbane', 'cen. australia standard time': 'Australia/Adelaide',
    'new zealand standard time': 'Pacific/Auckland', 'hawaiian standard time': 'Pacific/Honolulu',
    'alaskan standard time': 'America/Anchorage', 'pacific standard time': 'America/Los_Angeles',
    'us mountain standard time': 'America/Phoenix', 'mountain standard time': 'America/Denver',
    'central standard time': 'America/Chicago', 'canada central standard time': 'America/Regina',
    'eastern standard time': 'America/New_York', 'us eastern standard time': 'America/Indiana/Indianapolis',
    'atlantic standard time': 'America/Halifax', 'newfoundland standard time': 'America/St_Johns',
    'central standard time (mexico)': 'America/Mexico_City', 'sa pacific standard time': 'America/Bogota',
    'argentina standard time': 'America/Argentina/Buenos_Aires', 'e. south america standard time': 'America/Sao_Paulo',
}


def clean_url(url):
    """The address as it will be fetched. webcal:// is https:// under another name."""
    text = str(url or '').strip()
    low = text.lower()
    if low.startswith('webcal://'):
        text = 'https://' + text[9:]
    elif low.startswith('webcals://'):
        text = 'https://' + text[10:]
    if not re.match(r'https?://[^\s/]+', text, re.I) or len(text) > 2000:
        raise CalError("A calendar address starts with https:// (or webcal://).")
    return text


def fetch(url):
    """Download the calendar file as text."""
    url = clean_url(url)
    try:
        resp = net.get(url, timeout=25, stream=True, headers={'Accept': 'text/calendar, text/plain, */*'})
    except requests.RequestException as e:
        raise CalError(f"Couldn't reach that calendar address ({type(e).__name__}).")
    try:
        if resp.status_code in (401, 403):
            raise CalError(f"That calendar address refused the request (HTTP {resp.status_code}). "
                           "It may need a private or 'secret' address instead.")
        if resp.status_code != 200:
            raise CalError(f"That calendar address answered with an error (HTTP {resp.status_code}).")
        data = b''
        try:
            for chunk in resp.iter_content(65536):
                data += chunk
                if len(data) > MAX_BYTES:
                    raise CalError("That calendar file is too big (over 5 MB).")
        except requests.RequestException as e:
            raise CalError(f"The calendar download broke off ({type(e).__name__}).")
    finally:
        resp.close()
    text = data.decode('utf-8', errors='replace').lstrip('\ufeff')
    if 'BEGIN:VCALENDAR' not in text[:2000].upper():
        raise CalError("That address did not return a calendar file (it may be a web page, not the .ics address).")
    return text


def pull(link, lo, hi, force=False):
    """The link's events (sources.py). The days asked for do not matter: an address is one whole file."""
    from . import api
    _name, rows, notes = parse(fetch(link['ref']), api.convert, api.zone_name())
    return rows, ' '.join(notes), None


# -- reading the file -----------------------------------------------------------

def _lines(text):
    """Content lines, folded continuations joined."""
    out = []
    for raw in text.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        if raw[:1] in (' ', '\t') and out:
            out[-1] += raw[1:]
        elif raw.strip():
            out.append(raw)
    return out


def _prop(line):
    """'NAME;P=v;Q="a:b":value' -> (NAME, {P: v}, value)."""
    quoted, cut = False, -1
    for i, ch in enumerate(line):
        if ch == '"':
            quoted = not quoted
        elif ch == ':' and not quoted:
            cut = i
            break
    if cut < 0:
        return line.strip().upper(), {}, ''
    head, value = line[:cut], line[cut + 1:]
    parts = re.split(r';(?=(?:[^"]*"[^"]*")*[^"]*$)', head)
    params = {}
    for p in parts[1:]:
        k, _, v = p.partition('=')
        params[k.strip().upper()] = v.strip().strip('"')
    return parts[0].strip().upper(), params, value


def _text(value):
    return re.sub(r'\\([nN,;\\])', lambda m: '\n' if m.group(1) in 'nN' else m.group(1), value).strip()


def zone_name(tzid):
    """An IANA zone name for a TZID, or '' when it is not one we know."""
    name = str(tzid or '').strip().strip('"')
    if not name:
        return ''
    if name.startswith('/'):                       # '/freeassociation.sourceforge.net/Tzfile/Europe/Paris'
        m = re.search(r'([A-Z][A-Za-z_]+/[A-Za-z_+\-]+(?:/[A-Za-z_]+)?)$', name)
        name = m.group(1) if m else name.lstrip('/')
    if name.lower() in _WINDOWS:
        return _WINDOWS[name.lower()]
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(name)
        return name
    except Exception:
        return ''


def _stamp(value):
    """'20261118' -> date; '20261118T093000[Z]' -> (naive datetime, is_utc)."""
    v = value.strip()
    if len(v) == 8 and v.isdigit():
        return date(int(v[:4]), int(v[4:6]), int(v[6:8])), False
    utc = v.upper().endswith('Z')
    core = v[:-1] if utc else v
    core = core + '00' if len(core) == 13 else core              # seconds left out
    return datetime.strptime(core[:15], '%Y%m%dT%H%M%S').replace(second=0), utc


class _Reader:
    def __init__(self, convert, local_zone):
        self.convert = convert              # (naive datetime, from zone, to zone) -> naive; '' = local
        self.local_zone = local_zone        # our own IANA name, '' when unknown
        self.unknown_zones = set()

    def when(self, params, value):
        """-> (date | naive datetime, zone name). zone '' = already local."""
        stamp, utc = _stamp(value)
        if isinstance(stamp, date) and not isinstance(stamp, datetime):
            return stamp, ''
        if utc:
            return stamp, 'UTC'
        tzid = params.get('TZID')
        if not tzid:
            return stamp, ''                # floating: the same clock time everywhere
        zone = zone_name(tzid)
        if not zone:
            self.unknown_zones.add(tzid)
            return stamp, ''
        return stamp, ('' if zone == self.local_zone else zone)

    def local(self, stamp, zone):
        return self.convert(stamp, zone, '') if zone and isinstance(stamp, datetime) else stamp


def _fmt(v):
    return v.strftime('%Y-%m-%dT%H:%M') if isinstance(v, datetime) else v.isoformat()


def _duration(value):
    m = _DURATION.fullmatch(value.strip().upper())
    if not m:
        return None
    sign, w, d, h, mi, s = m.groups()
    span = timedelta(weeks=int(w or 0), days=int(d or 0), hours=int(h or 0), minutes=int(mi or 0), seconds=int(s or 0))
    return -span if sign == '-' else span


def _event(props, reader):
    """One VEVENT's properties -> a row dict (with private _keys), or None."""
    one = {}
    exdates = []
    guests = []
    for name, params, value in props:
        if name == 'EXDATE':
            exdates.append((params, value))
        elif name == 'ATTENDEE':
            if len(guests) < MAX_GUESTS and params.get('CUTYPE', '').upper() not in ('ROOM', 'RESOURCE'):
                address = re.sub(r'^mailto:', '', value.strip(), flags=re.I)
                address = address if re.fullmatch(r'[^@\s<>,;]+@[^@\s<>,;]+', address) else ''
                guests.append({'name': (' '.join(_text(params.get('CN') or '').split()) or address or '?')[:100],
                               'email': address[:200],
                               'rsvp': _ANSWERS.get(params.get('PARTSTAT', '').upper(), 'pending'), 'self': False})
        elif name not in one:
            one[name] = (params, value)
    if 'DTSTART' not in one or 'UID' not in one:
        return None
    start, zone = reader.when(*one['DTSTART'])
    all_day = not isinstance(start, datetime)
    if 'DTEND' in one:
        end, end_zone = reader.when(*one['DTEND'])
        if isinstance(end, datetime) != isinstance(start, datetime):
            end = start
        elif not all_day and end_zone != zone:           # an end in another zone: bring it into the start's
            end = _shift(reader, end, end_zone, zone)
    elif 'DURATION' in one and _duration(one['DURATION'][1]) is not None:
        end = start + _duration(one['DURATION'][1])
    else:
        end = start + (timedelta(days=1) if all_day else timedelta(0))
    if all_day:
        end = max(start, end - timedelta(days=1))        # the file's end day is exclusive
    elif end < start:
        end = start
    rrule = _until_day(one['RRULE'][1].strip(), start, zone, reader) if 'RRULE' in one else ''
    status = one.get('STATUS', ({}, ''))[1].strip().upper()
    skipped = []
    for params, value in exdates:
        for piece in value.split(','):
            if not piece.strip():
                continue
            try:
                ex, ex_zone = reader.when(params, piece)
            except ValueError:
                continue
            if isinstance(ex, datetime) and ex_zone != zone:
                ex = _shift(reader, ex, ex_zone, zone)
            skipped.append((ex.date() if isinstance(ex, datetime) else ex).isoformat())
    recur_day = None
    if 'RECURRENCE-ID' in one:
        try:
            rid, rid_zone = reader.when(*one['RECURRENCE-ID'])
            recur_day = (rid, rid_zone)
        except ValueError:
            recur_day = None
    if not rrule:                                        # one time: store it as local time
        start, end, zone = reader.local(start, zone), reader.local(end, zone), ''
    return {
        'uid': one['UID'][1].strip(),
        'title': (_text(one.get('SUMMARY', ({}, ''))[1]) or '(No title)')[:200],
        'start': _fmt(start), 'end': _fmt(end), 'all_day': all_day, 'tz': zone,
        'rrule': rrule, 'exdates': skipped,
        'location': _text(one.get('LOCATION', ({}, ''))[1])[:200],
        'notes': _text(one.get('DESCRIPTION', ({}, ''))[1])[:4000],
        'url': one.get('URL', ({}, ''))[1].strip()[:500],
        'guests': guests, 'series': '',
        '_cancelled': status == 'CANCELLED', '_recur': recur_day, '_zone': zone,
    }


def _until_day(rule, start, zone, reader):
    """A rule whose end is an instant in UTC (UNTIL=...Z), with that end as
    the DAY of the last occurrence where the event is kept. Read as a UTC
    date it is a day off for anyone far from Greenwich: one occurrence too
    few in Sydney, one too many in New York."""
    m = re.search(r'UNTIL=(\d{8}T\d{4,6})Z', rule, re.I)
    if not m or not isinstance(start, datetime):
        return rule
    try:
        until = _shift(reader, _stamp(m.group(1) + 'Z')[0], 'UTC', zone)
    except (ValueError, OverflowError):
        return rule
    day = until.date()
    if until.time() < start.time():                   # that day's occurrence would start after the end
        day -= timedelta(days=1)
    return rule[:m.start()] + f"UNTIL={day:%Y%m%d}" + rule[m.end():]


def _shift(reader, stamp, from_zone, to_zone):
    """A naive time in one zone as the naive time in another ('' = local)."""
    return stamp if from_zone == to_zone else reader.convert(stamp, from_zone, to_zone)


def parse(text, convert, local_zone=''):
    """The file -> (its own name, rows for store.sync_rows, notes for the person).
    `convert(naive datetime, from_zone, to_zone)` -> naive datetime, '' = local."""
    reader = _Reader(convert, local_zone)
    name, events, current, depth = '', [], None, 0
    for line in _lines(text):
        prop, params, value = _prop(line)
        if prop == 'BEGIN':
            kind = value.strip().upper()
            if kind == 'VEVENT' and current is None:
                current, depth = [], 0
            elif current is not None:
                depth += 1                                # an alarm inside the event
        elif prop == 'END':
            if current is not None and depth:
                depth -= 1
            elif current is not None and value.strip().upper() == 'VEVENT':
                try:
                    ev = _event(current, reader)
                except (ValueError, OverflowError) as e:
                    logger.debug(f"[calendar] an event in the calendar file was skipped: {e}")
                    ev = None
                if ev:
                    events.append(ev)
                current = None
        elif current is not None:
            if not depth:
                current.append((prop, params, value))
        elif prop == 'X-WR-CALNAME' and not name:
            name = _text(value)[:60]

    masters = {e['uid']: e for e in events if e['_recur'] is None}
    rows, notes = [], []
    for ev in events:
        if ev['_recur'] is None:
            continue
        rid, rid_zone = ev['_recur']
        master = masters.get(ev['uid'])
        if master is not None:                            # that day leaves the series
            if isinstance(rid, datetime) and rid_zone != master['_zone']:
                rid = _shift(reader, rid, rid_zone, master['_zone'])
            master['exdates'].append((rid.date() if isinstance(rid, datetime) else rid).isoformat())
        day = (rid.date() if isinstance(rid, datetime) else rid).isoformat()
        if not ev['_cancelled']:
            rows.append(dict(ev, source_ref=f"{ev['uid']}#{day}", uid=f"{ev['uid']}#{day}"))
    for ev in masters.values():
        if not ev['_cancelled']:
            rows.append(dict(ev, source_ref=ev['uid']))
    if len(rows) > MAX_EVENTS:
        rows.sort(key=lambda r: r['start'], reverse=True)
        notes.append(f"Only the newest {MAX_EVENTS} of {len(rows)} events were kept.")
        rows = rows[:MAX_EVENTS]
    if reader.unknown_zones:
        some = ', '.join(sorted(reader.unknown_zones)[:3])
        notes.append(f"Unknown time zone ({some}): those times are shown as written.")
    for r in rows:
        for k in ('_cancelled', '_recur', '_zone'):
            r.pop(k, None)
        r['exdates'] = sorted(set(r['exdates']))
    return name, rows, notes
