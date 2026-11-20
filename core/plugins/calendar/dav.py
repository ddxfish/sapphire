# core/plugins/calendar/dav.py - a CalDAV calendar as a link (see sources.py)
"""
One calendar of a CalDAV account, copied in and written through. caldav.py is
the wire; this is what the events mean.

The copy. An event is stored the way ics.py reads a calendar file: a
repeating event is ONE row with its rule (in its own time zone), and an
occurrence someone changed is a row of its own. Every row remembers the
address of its file on the server (`href`); a series and its changed
occurrences share one file.

A change is made in the server's own file (icsw.py): it is fetched, the lines
that change are replaced, and it is sent back only if it is still the version
that was fetched. So an alarm, a guest list or another app's own fields
survive a change made here, and a change made elsewhere a moment ago is never
overwritten. The copy here is then made to match the file that was sent.

A repeating event, the same from every caller as everywhere in this plugin:
  - its id alone is the whole series. A new time moves the series: the
    exceptions and the end date move with it.
  - its id plus a day is that one occurrence, which becomes a changed
    occurrence in the series' file (and a row of its own here).

Guests: not written from here. A guest list a server already holds is left
exactly as it is, and shown by name.

A read and a write can overlap (sync.py says how that is kept from undoing
the write): every change here is announced with sync.wrote() before it is
sent, and the copy is made to match under sync.storing().
"""
import logging
import re
import uuid
from datetime import datetime, time as dtime, timedelta, timezone

from . import accounts, caldav, ics, icsw, repeat, store
from .errors import CalError

logger = logging.getLogger(__name__)

KIND = 'caldav'
WINDOWED = True               # a window of days is copied; it grows when someone looks further out
GUESTS = False
EVERY, RETRY = 60, 120        # seconds a copy counts as fresh; seconds before a failed read is tried again

_NO_GUESTS = "Inviting guests works on a Google calendar only, and this event is kept in '{name}'."
_ONE_DAY = ("'{title}' repeats on set days, so the whole series cannot be moved to another day from here. "
            "Move one occurrence (give its date), or change the series where the calendar is kept.")
# a rule that pins its days: moving the start to another day would leave the exceptions on the wrong days
_PINS = re.compile(r'(?:^|;)(BYDAY|BYMONTHDAY|BYYEARDAY|BYWEEKNO|BYSETPOS|BYMONTH)=([^;]*)', re.I)
_REF = re.compile(r'(.+)#(\d{4}-\d{2}-\d{2})')
# what this calendar keeps of an event's words (ics.py cuts at these): a longer one is not rewritten from here
_KEPT = {'title': ('SUMMARY', 200, 'title'), 'location': ('LOCATION', 200, 'place'),
         'notes': ('DESCRIPTION', 4000, 'notes text')}
_NO_ANSWER = ("Answering an invitation from here works on a Google calendar only, and this event is kept in "
              "'{name}'. Answer it in that calendar's own app.")
_ONE_NO_REPEAT = "One occurrence cannot have its own repeat. Leave the date out to change the whole series."


def _api():
    from . import api
    return api


def _sync():
    from . import sync
    return sync


def _utc(naive):
    return _api()._aware(naive).astimezone(timezone.utc)


def _reader():
    api = _api()
    return ics._Reader(api.convert, api.zone_name())


def _account(link):
    row = accounts.get(link['calendar'], link.get('account') or '')
    if row is None:
        raise CalError(f"The account behind '{link['name']}' has been removed. "
                       "Take it off the list, or add the account again (the Calendar page, Accounts).")
    if not accounts.allows(row, link['ref']):
        # the account was pointed at another server since this calendar was linked: its login stays home
        raise CalError(f"'{link['name']}' is not on the server its account is set to now. "
                       "Untick it and tick it again (the Calendar page, Accounts).")
    return row


def login_of(link):
    return accounts.login_of(_account(link))


def _held(link, call):
    """Run one piece of work against the server; a refused login is put on hold (accounts.py)."""
    row = _account(link)
    login = accounts.login_of(row)
    try:
        return call(login)
    except caldav.Refused as e:
        accounts.refused(row, login, e)
        raise


def _me(link):
    """The addresses that are the account's own owner on a guest list."""
    row = accounts.get(link['calendar'], link.get('account') or '')
    return {a.strip().lower() for a in ((row['username'], row['email_address']) if row else ()) if '@' in a}


def _rows_of(text, href, me=()):
    api = _api()
    _name, rows, notes = ics.parse(text, api.convert, api.zone_name())
    for r in rows:
        r['href'] = href
        for g in r.get('guests') or []:
            g['self'] = bool(g.get('email')) and g['email'].lower() in me      # the owner is not their own guest
    return rows, notes


# -- reading ---------------------------------------------------------------------------

def pull(link, lo, hi, force=False):
    """The events of days [lo, hi] -> (rows, a note for the person, a call
    to make once the rows are stored). None when the server says nothing
    has changed since the last read of the same days."""
    s = store.get()
    try:
        mark = _held(link, lambda login: caldav.ctag(login, link['ref']))
    except caldav.Gone:
        raise CalError(f"'{link['name']}' is no longer on its server (it was removed there). Untick it here.")
    key = f"ctag:{link['id']}"
    # our own zone is part of what the copy means: after a move to another zone it is read again
    stamp = f"{mark}|{lo.isoformat()}|{hi.isoformat()}|{_api().zone_name()}"
    if mark and not force and s.meta(key) == stamp:
        return None
    first = _utc(datetime.combine(lo, dtime()))
    last = _utc(datetime.combine(hi + timedelta(days=1), dtime()))
    rows, notes, me = [], [], _me(link)
    for href, _etag, text in _held(link, lambda login: caldav.events(login, link['ref'], first, last)):
        try:
            got, said = _rows_of(text, href, me)
        except Exception as e:
            logger.debug(f"[calendar] an event file of '{link['name']}' was skipped: {type(e).__name__}")
            continue
        rows.extend(got)
        notes.extend(n for n in said if n not in notes)
    if len(rows) > ics.MAX_EVENTS:
        rows.sort(key=lambda r: r['start'], reverse=True)
        notes.append(f"Only the newest {ics.MAX_EVENTS} of {len(rows)} events were kept.")
        rows = rows[:ics.MAX_EVENTS]
    return rows, ' '.join(notes), lambda: s.set_meta(key, stamp if mark else '')


def _foreign(ev):
    """The zone a stored series is kept in when that is not ours ('' otherwise)."""
    return ev['tz'] if (ev['tz'] and 'T' in ev['start']) else ''


def _occurrences(ev, first_day, last_day):
    """The occurrences of a stored series that start on a LOCAL day in
    [first_day, last_day]: [(start in the series' own zone, the same moment here)]."""
    api = _api()
    foreign = _foreign(ev)
    slack = timedelta(days=1 if foreign else 0)
    out = []
    for there in repeat.expand(api._load(ev['start']), ev['rrule'], first_day - slack, last_day + slack, ev['exdates']):
        home = api.convert(there, foreign, '') if foreign else there
        day = home.date() if isinstance(home, datetime) else home
        if first_day <= day <= last_day:
            out.append((there, home))
    return out


def _on(ev, day):
    """The occurrence of a series that starts on local day `day`, or a refusal."""
    if not ev['rrule']:
        raise CalError(f"'{ev['title']}' does not repeat. Leave the date out.")
    try:
        hits = _occurrences(ev, day, day)
    except ValueError:
        hits = []
    if not hits:
        raise CalError(f"'{ev['title']}' has no occurrence that starts on {day.isoformat()}.")
    return hits[0]


def _reference(ev, shown, fields):
    """The occurrence a change to a whole series is measured from: the one
    the caller was looking at (`shown`), else the one nearest the new start,
    else the first. A series moves by how far that occurrence was moved."""
    api = _api()
    if shown not in (None, ''):
        return _on(ev, api.parse_day(shown))
    if fields.get('start') not in (None, ''):
        when = api._parse_when(fields['start'], 'start')
        day = when.date() if isinstance(when, datetime) else when
        try:
            hits = _occurrences(ev, day - timedelta(days=400), day + timedelta(days=400))
        except ValueError:
            hits = []
        if hits:
            at = lambda x: x if isinstance(x, datetime) else datetime.combine(x, dtime())
            return min(hits, key=lambda h: abs(at(h[1]) - at(when)))
    start = api._load(ev['start'])
    foreign = _foreign(ev)
    return start, (api.convert(start, foreign, '') if foreign else start)


# -- the server's file ---------------------------------------------------------------------

def _split(ref):
    """A stored row's source_ref -> (the event's uid, the day of its changed
    occurrence or ''). A '#' inside a uid is part of the uid."""
    m = _REF.fullmatch(ref)
    return (m.group(1), m.group(2)) if m else (ref, '')


def _open(link, ev):
    """The server's file of a stored event, as it is right now: (its tree,
    its version mark). The decisions of a change are made from the stored
    row, so the row has to be what the file says: when the event was changed
    somewhere else since it was read, the copy here is brought up to date
    and the change is refused, to be made again on what is really there."""
    if not ev.get('href'):
        raise CalError(f"'{ev['title']}' has to be read from its server again first. Refresh the calendar, then repeat that.")
    try:
        text, etag = _held(link, lambda login: caldav.get(login, ev['href']))
    except caldav.Gone:
        raise CalError(f"'{ev['title']}' is no longer on its server (it was removed there).")
    try:
        cal = icsw.load(text)
        fresh = next((r for r in _rows_of(text, ev['href'], _me(link))[0] if r['source_ref'] == ev['source_ref']), None)
    except (ValueError, OverflowError):
        raise CalError(f"The server's copy of '{ev['title']}' could not be read.")
    if fresh is None or any(fresh[k] != ev[k] for k in ('start', 'end', 'all_day', 'tz', 'rrule', 'exdates')):
        _keep(link, ev['href'], text)
        raise CalError(f"'{ev['title']}' was changed somewhere else since it was read here. It is up to date now: "
                       "look at it again, then repeat that.")
    return cal, etag


def _events_of(cal, uid, reader):
    """The event `uid` in a file: (its main VEVENT or None, {day: the VEVENT
    of the occurrence changed on that day}, {day: when that occurrence would
    have started, in the series' own zone}, the series' first start there).
    A day is named as ics.parse names it, so it matches a stored row's
    '<uid>#<day>'; of two entries for one day the one ics.parse keeps."""
    master, loose = None, []
    for ev in cal.comps('VEVENT'):
        got = ev.get('UID')
        if not got or got[1].strip() != uid:
            continue
        if ev.get('RECURRENCE-ID') is None:
            master = ev
        else:
            loose.append(ev)
    zone, start = '', None
    if master is not None:
        try:
            info = ics._event(master.props(), reader) or {}
            zone = info.get('_zone', '')
            start = _api()._load(info['start']) if info.get('start') else None
        except (ValueError, OverflowError):
            zone = ''
    changed, originals = {}, {}
    for ev in loose:
        try:
            rid, rid_zone = reader.when(*ev.get('RECURRENCE-ID'))
        except ValueError:
            continue
        if master is not None and isinstance(rid, datetime) and rid_zone != zone:
            rid = ics._shift(reader, rid, rid_zone, zone)
        day = (rid.date() if isinstance(rid, datetime) else rid).isoformat()
        if day not in changed:
            changed[day], originals[day] = ev, rid
    return master, changed, originals, start


def _keep(link, href, text=None):
    """Make the copy here match a file as it now is on the server (text=None:
    the file was deleted), without asking the server again. The link is then
    due for a real read, over days wide enough to hold what was written."""
    s, sync = store.get(), _sync()
    rows = _rows_of(text, href, _me(link))[0] if text is not None else []
    with sync.storing(link['id']):
        s.sync_rows(link, rows, prune=False)
        have = {r['source_ref'] for r in rows}
        for row in s.at_href(link['id'], href):
            if row['source_ref'] not in have:
                s.delete(row['id'])
        sync.wrote(link['id'])
    s.set_meta(f"ctag:{link['id']}", '')              # the server's mark has moved: the next refresh reads again
    now = s.get_link(link['id'])
    if now is None:
        return
    due = {'synced_at': 0}
    days = [r['start'][:10] for r in rows] + [r['end'][:10] for r in rows]
    if days and now['win_from'] and now['win_to']:    # an event outside the copied days must not be dropped by the next read
        due.update(win_from=min([now['win_from']] + days), win_to=max([now['win_to']] + days))
    s.update_link(link['id'], due)


def _send(link, cal, href, etag):
    text = icsw.dump(cal)
    _held(link, lambda login: caldav.put(login, href, text, etag))
    _keep(link, href, text)


def _texts(fields, row, base):
    """The words that really change. A title and a place are one line here:
    one that differs only in its line breaks or spacing (another app's
    two-line address) is the same, and is left exactly as the file has it."""
    one_line = lambda v: ' '.join(str(v).split())
    out = {k: row[k] for k in ('title', 'location') if fields.get(k) is not None and one_line(row[k]) != one_line(base[k])}
    if fields.get('notes') is not None and row['notes'] != base['notes']:
        out['notes'] = row['notes']
    return out


def _fits(vevent, delta, ev):
    """Refuse to rewrite words the file holds more of than this calendar
    keeps: the change would be made on a copy that was cut short."""
    for key, (prop, limit, words) in _KEPT.items():
        got = vevent.get(prop) if key in delta else None
        if got and len(ics._text(got[1])) > limit:
            raise CalError(f"'{ev['title']}' has a {words} longer than this calendar keeps ({limit} characters), "
                           "so a change from here would cut it short. Change it where the calendar is kept.")


def _retimed(row, base):
    return (row['start'], row['end'], bool(row['all_day'])) != (base['start'], base['end'], bool(base['all_day']))


def _gone(ev):
    return CalError(f"'{ev['title']}' is no longer on its server the way it was. Refresh the calendar, then repeat that.")


def _wire_rule(rule, zone, all_day):
    """A rule as a file wants it. Here a repeat ends on a DAY; in a file, a
    timed event's end is an instant in UTC: the last second of that day where
    the event is kept (`zone`, '' = here). A bare day would cut the last
    occurrence off in apps that read it as midnight."""
    m = re.search(r'UNTIL=(\d{8})(?![\dT])', rule or '', re.I)
    if not m or all_day:
        return rule
    v = m.group(1)
    end = datetime(int(v[:4]), int(v[4:6]), int(v[6:8]), 23, 59, 59)
    try:
        utc = _api()._aware(end, zone).astimezone(timezone.utc)
    except Exception:
        return rule
    return rule[:m.start()] + f"UNTIL={utc:%Y%m%dT%H%M%SZ}" + rule[m.end():]


# -- writing -------------------------------------------------------------------------------

def add(link, row, guests=None):
    """A new event, saved to the server."""
    if guests:
        raise CalError(_NO_GUESTS.format(name=link['name']))
    uid = str(uuid.uuid4())
    row = dict(row, rrule=_wire_rule(row.get('rrule') or '', '', row.get('all_day')))
    text = icsw.dump(icsw.new_event(row, uid, _api().zone_name(), _utc))
    href = link['ref'].rstrip('/') + '/' + uid + '.ics'
    _sync().wrote(link['id'])
    _held(link, lambda login: caldav.put(login, href, text, new=True))
    _keep(link, href, text)
    ev = store.get().find(link['id'], uid)
    if ev is None:
        raise CalError(f"'{row['title']}' was saved to '{link['name']}', but could not be read back yet. "
                       "It will show after the next refresh.")
    return ev


def change(link, ev, fields, day=None, guests=None, shown=None):
    """Change an event (see the module's words on repeating ones). `shown`:
    the day of the occurrence the caller was looking at, for a whole series."""
    if guests:
        raise CalError((_NO_ANSWER if guests.get('rsvp') and not (guests.get('add') or guests.get('remove'))
                        else _NO_GUESTS).format(name=link['name']))
    api = _api()
    uid, own_day = _split(ev['source_ref'])
    repeating = any(k in fields for k in api._REPEAT_KEYS)
    plain = {k: v for k, v in fields.items() if k not in api._REPEAT_KEYS}
    cal, etag = _open(link, ev)
    master, changed, _originals, _start = _events_of(cal, uid, _reader())
    zone = api.zone_name()
    ref = ev['source_ref']

    if own_day:                                       # an occurrence that was already changed: an event of its own
        target = changed.get(own_day)
        if target is None:
            raise _gone(ev)
        if day is not None:
            raise CalError(f"'{ev['title']}' does not repeat. Leave the date out.")
        if repeating and str(fields.get('repeat') or 'none').strip().lower() not in ('none', 'no', 'never', 'once', 'false'):
            raise CalError(_ONE_NO_REPEAT)
        row = api._clean(plain, ev)
        delta = _texts(fields, row, ev)
        if _retimed(row, ev):
            delta.update(start=row['start'], end=row['end'], all_day=row['all_day'])
        if not delta:
            raise CalError("Nothing to change.")
        _fits(target, delta, ev)
        icsw.apply(cal, target, delta, zone, _utc)
        icsw.touched(target)
    elif master is None:
        raise _gone(ev)
    elif day is not None:                             # one occurrence of a series
        if repeating:
            raise CalError(_ONE_NO_REPEAT)
        there, home = _on(ev, day)
        start0 = api._load(ev['start'])
        length = api._load(ev['end']) - start0
        base = dict(ev, start=api._fmt(home), end=api._fmt(home + length), rrule='', exdates=[])
        row = api._clean(plain, base)
        delta = _texts(fields, row, base)
        retimed = _retimed(row, base)
        if not delta and not retimed:
            raise CalError("Nothing to change.")
        _fits(master, delta, ev)
        # The copy starts as that occurrence, in the series' own zone and form. Only a time the person
        # really changed is written anew (in our zone): a new title must never move the occurrence.
        new = icsw.override_of(master, there, there - start0)
        if retimed:
            delta.update(start=row['start'], end=row['end'], all_day=row['all_day'])
        icsw.apply(cal, new, delta, zone, _utc)
        icsw.touched(new)
        cal.items.insert(cal.items.index(master) + 1, new)
        key = there.date() if isinstance(there, datetime) else there
        ref = f"{uid}#{key.isoformat()}"
    elif ev['rrule']:                                 # the whole series
        _change_series(cal, master, changed, ev, fields, plain, repeating, shown, zone)
    else:                                             # a one-time event (it may gain a repeat)
        row = api._clean(fields, ev)
        delta = _texts(fields, row, ev)
        gains = repeating and bool(row['rrule'])
        if _retimed(row, ev) or gains:                # a repeat needs its start written in a named zone
            delta.update(start=row['start'], end=row['end'], all_day=row['all_day'])
        if gains:
            delta['rrule'] = _wire_rule(row['rrule'], '', row['all_day'])
        if not delta:
            raise CalError("Nothing to change.")
        _fits(master, delta, ev)
        icsw.apply(cal, master, delta, zone, _utc)
        icsw.touched(master)

    _sync().wrote(link['id'])
    _send(link, cal, ev['href'], etag)
    return store.get().find(link['id'], ref) or store.get().get(ev['id']) or ev


def _change_series(cal, master, changed, ev, fields, plain, repeating, shown, zone):
    api = _api()
    there, home = _reference(ev, shown, plain)
    start0 = api._load(ev['start'])
    length0 = api._load(ev['end']) - start0
    foreign = _foreign(ev)
    base = dict(ev, start=api._fmt(home), end=api._fmt(home + length0), rrule='', exdates=[])
    row = api._clean(plain, base)
    if bool(row['all_day']) != bool(ev['all_day']):
        raise CalError("Switching a whole repeating series between timed and all-day is not supported here. "
                       "Change one occurrence (give its date), or change the series where the calendar is kept.")
    new_home = api._load(row['start'])
    # how far the occurrence moved, measured where the series is kept (the two zones' clocks can differ by a change)
    moved = (api.convert(new_home, '', foreign) if foreign else new_home) - there
    length = api._load(row['end']) - new_home
    new_start = start0 + moved
    day_of = lambda x: x.date() if isinstance(x, datetime) else x
    rule = ev['rrule']
    if repeating:
        rule = api._rrule(fields, ev['rrule'], new_start)
    new_rule = not api._same_rule(rule, ev['rrule'], new_start)
    if new_rule and changed:
        raise CalError(f"'{ev['title']}' has occurrences that were changed on their own, so how it repeats cannot be "
                       "changed from here. Change that where the calendar is kept.")
    weekday = None                                    # 'weekly on Mondays' moved to a Tuesday: the rule's one day follows
    if day_of(new_start) != day_of(start0) and not new_rule:
        pins = _PINS.findall(ev['rrule'])
        was = icsw._DAY_CODES[day_of(start0).weekday()]
        if pins and not (len(pins) == 1 and pins[0][0].upper() == 'BYDAY' and pins[0][1].upper() == was
                         and re.search(r'FREQ=WEEKLY', ev['rrule'], re.I)):
            raise CalError(_ONE_DAY.format(title=ev['title']))
        weekday = icsw._DAY_CODES[day_of(new_start).weekday()] if pins else None
    delta = _texts(fields, row, ev)
    if not (delta or moved or length != length0 or new_rule):
        raise CalError("Nothing to change.")
    _fits(master, delta, ev)
    if moved or length != length0:
        days = icsw.move_series(master, moved, length if length != length0 else None, ev['all_day'])
        if moved:                                     # a changed occurrence still stands for the same one
            for other in changed.values():
                for line in other.lines('RECURRENCE-ID'):
                    other.set('RECURRENCE-ID', icsw.shifted(line, moved, days))
        if weekday:
            line = (master.lines('RRULE') or [''])[0]
            master.set('RRULE', re.sub(r'BYDAY=[A-Za-z]{2}(?=;|$)', f'BYDAY={weekday}', line))
    if new_rule:
        delta['rrule'] = _wire_rule(rule, foreign, ev['all_day'])
        master.drop('EXDATE')                         # the days taken out were days of the old series
    if delta:
        icsw.apply(cal, master, delta, zone, _utc)
    icsw.touched(master)


def remove(link, ev, day=None):
    """Remove an event, or with `day` one occurrence of a series. -> 'event' | 'occurrence'."""
    uid, own_day = _split(ev['source_ref'])
    if not own_day and day is None:                   # the event, with every occurrence of it
        if not ev.get('href'):
            raise _gone(ev)
        _sync().wrote(link['id'])
        _held(link, lambda login: caldav.delete(login, ev['href']))
        _keep(link, ev['href'])
        return 'event'
    cal, etag = _open(link, ev)
    master, changed, originals, start = _events_of(cal, uid, _reader())
    kind = 'event'
    if own_day:                                       # a changed occurrence: it goes, and its day leaves the series
        if day is not None:
            raise CalError(f"'{ev['title']}' does not repeat. Leave the date out.")
        target = changed.get(own_day)
        if target is None:
            raise _gone(ev)
        cal.items.remove(target)
        if master is not None and master.get('RRULE') is not None and start is not None:
            was = originals[own_day]                  # when it would have started, written the way the series writes
            if not isinstance(was, datetime) and isinstance(start, datetime):
                was = datetime.combine(was, start.time())
            icsw.add_exdate(master, was)
            icsw.touched(master)
        elif master is not None:
            line = re.sub(r';RANGE=[^;:]*', '', target.lines('RECURRENCE-ID')[0], flags=re.I)
            master.add('EXDATE' + line[len('RECURRENCE-ID'):])
            icsw.touched(master)
    else:
        if master is None:
            raise _gone(ev)
        there, _home = _on(ev, day)
        icsw.add_exdate(master, there)
        icsw.touched(master)
        kind = 'occurrence'
    _sync().wrote(link['id'])
    if cal.comps('VEVENT'):
        _send(link, cal, ev['href'], etag)
    else:
        _held(link, lambda login: caldav.delete(login, ev['href']))
        _keep(link, ev['href'])
    return kind
