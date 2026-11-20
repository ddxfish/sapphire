# core/plugins/calendar/gcal.py - a Google calendar as a link (see sources.py)
"""
One calendar of the Google account connected under a calendar's name, copied
in and written through. google.py is the wire; this is what the events mean.

The copy. Google hands a repeating event over already expanded, so every
occurrence is a row of its own here, and the rows of one series share its
`series` id. A change goes to Google first and the copy is refreshed after:
Google stays the authority and nothing is ever merged.

Guests live at Google. The attendee list is always sent back whole, as Google
gave it, so nobody's answer is lost; Google emails the guests itself.
"""
import logging
from datetime import timedelta

from . import google, store
from .errors import CalError

logger = logging.getLogger(__name__)

KIND = 'google'
WINDOWED = True               # a window of days is copied; it grows when someone looks further out
GUESTS = True
EVERY, RETRY = 60, 60         # seconds a copy counts as fresh; seconds before a failed read is tried again

_RSVP_OUT = {'yes': 'accepted', 'no': 'declined', 'maybe': 'tentative'}


def _api():
    from . import api
    return api


def pull(link, lo, hi, force=False):
    api = _api()
    items = google.instances(link['calendar'], link['ref'], api.rfc3339(lo),
                             api.rfc3339(hi + timedelta(days=1)), api.zone_name())
    return [r for r in (google.to_row(i, api.to_local) for i in items) if r], '', None


def _refreshed(link, need):
    """Refresh the copy after a change. The change is already made at
    Google, so a refresh that fails is logged, not raised."""
    from . import sync
    try:
        sync.pull(link, need)
    except CalError as e:
        logger.warning(f"[calendar] changed at Google but the copy did not refresh: {e}")


def _notify(ev):
    return any(not g.get('self') for g in ev.get('guests') or [])


def guest_list(current, guests, title):
    """Google's attendee list for an event after `guests` is applied:
    {'add': [attendee dicts], 'remove': [a name or address each], 'rsvp':
    'yes' | 'no' | 'maybe'}. The list is sent back whole, as Google gave it,
    so nobody's answer is lost."""
    out = [dict(a) for a in current if isinstance(a, dict)]
    shown = lambda a: a.get('displayName') or (a.get('email') or '').partition('@')[0] or 'a guest with no name'
    for key in guests.get('remove') or []:
        low = str(key).strip().lower()
        if not low:
            continue
        others = [a for a in out if not a.get('self')]
        hits = [a for a in others if low in ((a.get('displayName') or '').lower(), (a.get('email') or '').lower(),
                                             (a.get('email') or '').lower().partition('@')[0])]
        if not hits:
            hits = [a for a in others if low in (a.get('displayName') or '').lower()]
        if not hits:
            raise CalError(f"'{key}' is not a guest of '{title}'.")
        if len(hits) > 1:
            raise CalError(f"'{key}' matches several guests of '{title}': {', '.join(shown(a) for a in hits)}. Use the full name.")
        out.remove(hits[0])
    have = {(a.get('email') or '').lower() for a in out}
    for a in guests.get('add') or []:
        if a['email'].lower() not in have:
            have.add(a['email'].lower())
            out.append(dict(a))
    if guests.get('rsvp'):
        answer = _RSVP_OUT.get(str(guests['rsvp']).strip().lower())
        if not answer:
            raise CalError("An answer to an invitation is yes, no or maybe.")
        me = next((a for a in out if a.get('self')), None)
        if me is None:
            raise CalError(f"'{title}' is not an invitation: the owner of this calendar is not on its guest list.")
        me['responseStatus'] = answer
    return out


def add(link, row, guests=None):
    """A new event, saved to Google. `guests`: attendee dicts; Google emails each an invitation."""
    api = _api()
    body = google.body(dict(row, attendees=guests or []), api.zone_name(), api.offset_of)
    item = google.insert(link['calendar'], link['ref'], body, notify=bool(guests))
    _refreshed(link, api._days_of(row))
    s = store.get()
    ev = s.find(link['id'], item.get('id', '')) or next(iter(s.series(link['id'], item.get('id', ''))), None)
    if ev is None:                                   # the refresh failed: keep what Google answered
        got = google.to_row(item, api.to_local)
        if got and not item.get('recurrence'):
            s.sync_rows(link, [got], prune=False)
            ev = s.find(link['id'], got['source_ref'])
    if ev is None:
        raise CalError(f"'{row['title']}' was added to Google, but it could not be read back yet. "
                       "It will show after the next refresh.")
    return ev


def _series_member(link, ev, day):
    """The stored occurrence of a repeating event that starts on `day`."""
    if not ev['series']:
        raise CalError(f"'{ev['title']}' does not repeat. Leave the date out.")
    for row in store.get().series(link['id'], ev['series']):
        if row['start'][:10] == day.isoformat():
            return row
    raise CalError(f"'{ev['title']}' has no occurrence that starts on {day.isoformat()}.")


def change(link, ev, fields, day=None, guests=None, shown=None):
    """Change an event. A repeating one: the whole series, or with `day` only
    the occurrence that starts on that day. `guests`: who to invite, who to
    take off, or an answer to an invitation (see guest_list). `shown` is not
    needed here: the row named is already one occurrence."""
    api = _api()
    scope, cal_id = link['calendar'], link['ref']
    timing = any(fields.get(k) not in (None, '') for k in ('start', 'end')) or fields.get('all_day') is not None
    repeating = any(k in fields for k in api._REPEAT_KEYS)
    target = _series_member(link, ev, day) if day is not None else ev
    whole = bool(ev['series']) and day is None
    if repeating and not whole and ev['series']:
        raise CalError("One occurrence cannot have its own repeat. Leave the date out to change the whole series.")
    row = api._clean({k: v for k, v in fields.items() if k not in api._REPEAT_KEYS}, target)
    delta = {k: row[k] for k in ('title', 'location', 'notes') if k in fields and fields[k] is not None}
    if not whole:
        if timing:
            delta.update(start=row['start'], end=row['end'], all_day=row['all_day'])
        if repeating:                                 # a one-time Google event gains (or loses) a repeat
            delta['rrule'] = api._rrule(fields, '', api._load(row['start']))
        if not delta and not guests:
            raise CalError("Nothing to change.")
        body = google.body(delta, api.zone_name(), api.offset_of, patching=True)
        if guests:
            live = google.get(scope, cal_id, target['source_ref'])
            body['attendees'] = guest_list(live.get('attendees') or [], guests, target['title'])
        # Guests are told of a change to their event, of being added and of being taken off.
        google.patch(scope, cal_id, target['source_ref'], body, notify=_notify(target) or bool(guests))
    else:
        master = google.get(scope, cal_id, ev['series'])
        first = google.to_row(master, api.to_local)
        if timing:
            if first is None or row['all_day'] != target['all_day']:
                raise CalError("Switching a whole repeating series between timed and all-day is not supported here. "
                               "Change one occurrence (give its date), or change the series in Google.")
            moved = api._load(row['start']) - api._load(target['start'])
            length = api._load(row['end']) - api._load(row['start'])
            new_start = api._load(first['start']) + moved
            delta.update(start=api._fmt(new_start), end=api._fmt(new_start + length), all_day=row['all_day'])
        if repeating:
            old_rule = next((r[6:] for r in master.get('recurrence') or [] if str(r).upper().startswith('RRULE:')), '')
            delta['rrule'] = api._rrule(fields, old_rule, api._load(delta.get('start') or (first or target)['start']))
        if not delta and not guests:
            raise CalError("Nothing to change.")
        body = google.body(delta, api.zone_name(), api.offset_of, patching=True)
        if guests:
            body['attendees'] = guest_list(master.get('attendees') or [], guests, ev['title'])
        google.patch(scope, cal_id, ev['series'], body, notify=_notify(ev) or bool(guests))
    _refreshed(link, api._days_of(row))
    return store.get().get(target['id']) or dict(target, **{k: v for k, v in delta.items() if k != 'rrule'})


def remove(link, ev, day=None):
    """Remove an event, or with `day` one occurrence of a series. -> 'event' | 'occurrence'."""
    from . import sync
    s = store.get()
    sync.wrote(link['id'])                           # a read that asked Google before this must not bring it back
    if day is not None:
        target = _series_member(link, ev, day)
        google.delete(link['calendar'], link['ref'], target['source_ref'], notify=_notify(target))
        with sync.storing(link['id']):
            s.delete(target['id'])
            sync.wrote(link['id'])
        return 'occurrence'
    google.delete(link['calendar'], link['ref'], ev['series'] or ev['source_ref'], notify=_notify(ev))
    with sync.storing(link['id']):
        if ev['series']:
            for row in s.series(link['id'], ev['series']):
                s.delete(row['id'])
        s.delete(ev['id'])
        sync.wrote(link['id'])
    return 'event'
