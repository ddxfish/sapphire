# core/plugins/calendar/google.py - a Google calendar as a link
"""
Plain REST through core.net; no Google client library.

The account (client id or Easy Connect relay, refresh token) is the one the
credentials manager has always kept for Google Calendar, per calendar name
(`gcal_accounts`), so a connection made before this plugin existed keeps
working with no new consent.

Google stays the authority for its events. sync.py copies a window of them
here, already expanded into single occurrences (`singleEvents`), so a repeat
rule of any shape is Google's to work out. A change made here is sent to
Google first and the copy is refreshed after: nothing is merged.

Every refusal is a CalError in words for the person or the model.
"""
import logging
import threading
import time
from datetime import date, datetime, timedelta
from urllib.parse import quote

import requests
from core import net

from .errors import CalError

logger = logging.getLogger(__name__)

TOKEN_URL = 'https://oauth2.googleapis.com/token'
API = 'https://www.googleapis.com/calendar/v3'
DEFAULT_RELAY = 'https://oauth.sapphireblue.dev'
RECONNECT = "Open the Calendar page, Calendars, and connect Google again."
_RSVP = {'accepted': 'yes', 'declined': 'no', 'tentative': 'maybe', 'needsAction': 'pending'}

# One refresh at a time per account: two callers that both see an expired
# token would both ask Google, and the slower one could save a refresh token
# Google has already rotated out.
_locks_guard = threading.Lock()
_locks: dict = {}


def _lock_for(scope):
    with _locks_guard:
        return _locks.setdefault(scope, threading.Lock())


def _creds():
    from core.credentials_manager import credentials
    return credentials


def connected(scope):
    try:
        return bool(_creds().has_gcal_account(scope))
    except Exception as e:
        logger.warning(f"[calendar] could not read the Google account for '{scope}': {e}")
        return False


def accounts():
    """Calendar names that have a connected Google account, with its chosen calendar id."""
    try:
        rows = _creds().list_gcal_accounts()
    except Exception as e:
        logger.warning(f"[calendar] could not list Google accounts: {e}")
        return []
    return [{'scope': r['scope'], 'calendar_id': r.get('calendar_id') or 'primary'}
            for r in rows if connected(r['scope'])]


def _token(scope, force=False):
    creds = _creds()
    acct = creds.get_gcal_account(scope)
    refresh = acct.get('refresh_token', '')
    if not refresh:
        raise CalError(f"Google is not connected for the '{scope}' calendar. {RECONNECT}")
    relay_mode = acct.get('auth_mode', 'byo') == 'relay'
    if not relay_mode and not (acct.get('client_id') and acct.get('client_secret')):
        raise CalError("The Google client ID and secret are not set (Settings, Plugins, Calendar).")
    with _lock_for(scope):
        snap = creds.get_gcal_tokens_snapshot(scope)
        if not force and snap.get('access_token') and snap.get('expires_at', 0) > time.time() + 60:
            return snap['access_token']
        where = "the Sapphire connect relay" if relay_mode else "Google"
        try:
            if relay_mode:
                relay = (acct.get('relay_url') or DEFAULT_RELAY).rstrip('/')
                resp = net.post(f"{relay}/refresh", json={'refresh_token': refresh}, timeout=15)
            else:
                resp = net.post(TOKEN_URL, data={
                    'refresh_token': refresh, 'client_id': acct['client_id'],
                    'client_secret': acct['client_secret'], 'grant_type': 'refresh_token'}, timeout=15)
        except requests.RequestException as e:
            logger.warning(f"[calendar] token refresh could not reach {where}: {e}")
            raise CalError(f"Couldn't reach {where} ({type(e).__name__}). Try again in a moment.")
        if resp.status_code == 200:
            try:
                tokens = resp.json()
                access = tokens['access_token']
            except (ValueError, KeyError, TypeError) as e:
                logger.error(f"[calendar] malformed token response: {e}")
                raise CalError("Google sent a token answer that could not be read.")
            # Google may hand back a new refresh token; saving the old one
            # would fail with invalid_grant at the next refresh.
            new_refresh = tokens.get('refresh_token', refresh)
            if new_refresh != refresh:
                logger.info(f"[calendar] Google refresh token rotated for '{scope}'")
            creds.update_gcal_tokens(scope, new_refresh, access, time.time() + tokens.get('expires_in', 3600))
            return access
        try:
            code = (resp.json() or {}).get('error', '') or ''
        except ValueError:
            code = ''
        logger.error(f"[calendar] token refresh HTTP {resp.status_code} error={code!r}")
        if code == 'invalid_grant':
            raise CalError(f"Google no longer accepts this connection. {RECONNECT}")
        if resp.status_code >= 500:
            raise CalError(f"{where} is not answering right now (HTTP {resp.status_code}). Try again in a few minutes.")
        raise CalError(f"Google refused to renew the connection (HTTP {resp.status_code}"
                       f"{', ' + code if code else ''}). If it keeps happening: {RECONNECT}")


def call(scope, method, path, params=None, body=None):
    """One signed call; a 401 forces a fresh token and one retry. Returns the
    JSON answer (True when there is none)."""
    for attempt in (0, 1):
        token = _token(scope, force=attempt == 1)
        headers = {'Authorization': f'Bearer {token}'}
        try:
            resp = net.request(method, f"{API}{path}", headers=headers, params=params, json=body, timeout=20)
        except requests.RequestException as e:
            raise CalError(f"Couldn't reach Google Calendar ({type(e).__name__}).")
        if resp.status_code == 401 and attempt == 0:
            continue
        if resp.status_code == 401:
            raise CalError(f"Google turned the connection down. {RECONNECT}")
        if resp.status_code in (200, 201):
            try:
                return resp.json()
            except ValueError:
                return True
        if resp.status_code == 204:
            return True
        if resp.status_code in (404, 410):
            raise CalError("Google no longer has that (it was removed or moved there).")
        if resp.status_code == 403:
            raise CalError("Google refused: this calendar cannot be changed from this account.")
        logger.error(f"[calendar] Google {method} {path}: HTTP {resp.status_code} {resp.text[:200]}")
        raise CalError(f"Google Calendar answered with an error (HTTP {resp.status_code}).")
    raise CalError("Google Calendar could not be reached.")


def _events(cal_id, event_id=None):
    path = f"/calendars/{quote(cal_id or 'primary', safe='')}/events"
    return path if event_id is None else f"{path}/{quote(str(event_id), safe='')}"


def calendar_list(scope):
    """The account's calendars: [{'id', 'name', 'primary', 'writable'}]."""
    out, page = [], None
    for _ in range(10):
        data = call(scope, 'GET', '/users/me/calendarList', {'maxResults': 250, **({'pageToken': page} if page else {})})
        for c in data.get('items') or []:
            out.append({'id': c.get('id', ''), 'name': c.get('summaryOverride') or c.get('summary') or c.get('id', ''),
                        'primary': bool(c.get('primary')), 'writable': c.get('accessRole') in ('owner', 'writer')})
        page = data.get('nextPageToken')
        if not page:
            break
    out.sort(key=lambda c: (not c['primary'], c['name'].lower()))
    return out


def instances(scope, cal_id, time_min, time_max, tz=''):
    """Every occurrence between two RFC 3339 instants, repeats expanded by Google."""
    out, page = [], None
    params = {'timeMin': time_min, 'timeMax': time_max, 'singleEvents': 'true', 'maxResults': 2500}
    if tz:
        params['timeZone'] = tz
    for _ in range(40):
        data = call(scope, 'GET', _events(cal_id), {**params, **({'pageToken': page} if page else {})})
        out.extend(data.get('items') or [])
        page = data.get('nextPageToken')
        if not page:
            return out
    raise CalError("That Google calendar has more events than can be copied.")


def get(scope, cal_id, event_id):
    return call(scope, 'GET', _events(cal_id, event_id))


def insert(scope, cal_id, body, notify=False):
    return call(scope, 'POST', _events(cal_id), {'sendUpdates': 'all'} if notify else None, body)


def patch(scope, cal_id, event_id, body, notify=False):
    return call(scope, 'PATCH', _events(cal_id, event_id), {'sendUpdates': 'all'} if notify else None, body)


def delete(scope, cal_id, event_id, notify=False):
    return call(scope, 'DELETE', _events(cal_id, event_id), {'sendUpdates': 'all'} if notify else None)


# -- Google's event <-> this calendar's row -----------------------------------

def _instant(text):
    return datetime.fromisoformat(str(text).replace('Z', '+00:00'))


def to_row(item, to_local):
    """A Google event as the fields of a stored row; None for one that is
    cancelled or has no time. `to_local`: aware datetime -> naive local."""
    if not isinstance(item, dict) or item.get('status') == 'cancelled' or not item.get('id'):
        return None
    s, e = item.get('start') or {}, item.get('end') or {}
    try:
        if s.get('date'):
            first = date.fromisoformat(s['date'])
            last = date.fromisoformat(e['date']) - timedelta(days=1) if e.get('date') else first   # Google's end is exclusive
            start, end, all_day = first.isoformat(), max(first, last).isoformat(), True
        elif s.get('dateTime'):
            a = to_local(_instant(s['dateTime']))
            b = to_local(_instant(e['dateTime'])) if e.get('dateTime') else a + timedelta(hours=1)
            start, end, all_day = a.strftime('%Y-%m-%dT%H:%M'), max(a, b).strftime('%Y-%m-%dT%H:%M'), False
        else:
            return None
    except (ValueError, TypeError) as err:
        logger.warning(f"[calendar] a Google event has a time that cannot be read: {err}")
        return None
    guests = []
    for g in item.get('attendees') or []:
        if g.get('resource'):
            continue
        guests.append({'name': g.get('displayName') or g.get('email') or '?', 'email': g.get('email') or '',
                       'rsvp': _RSVP.get(g.get('responseStatus'), 'pending'), 'self': bool(g.get('self'))})
    return {
        'source_ref': item['id'], 'uid': item.get('iCalUID') or '',
        'title': (item.get('summary') or '(No title)')[:200],
        'start': start, 'end': end, 'all_day': all_day, 'tz': '', 'rrule': '', 'exdates': [],
        'location': (item.get('location') or '')[:200], 'notes': (item.get('description') or '')[:4000],
        'guests': guests, 'series': item.get('recurringEventId') or '', 'url': item.get('htmlLink') or '',
    }


def body(fields, tz='', offset_of=None, patching=False):
    """Cleaned event fields (only the ones given) as a Google event body.
    `tz`: the IANA zone name; without one, times go out with the numeric
    offset `offset_of(naive datetime)` returns. `patching`: also clear the
    other time form, so an all-day event can become a timed one and back."""
    out = {}
    clear = {'dateTime': None} if patching else {}
    clear_day = {'date': None} if patching else {}
    if 'title' in fields:
        out['summary'] = fields['title']
    if 'notes' in fields:
        out['description'] = fields['notes']
    if 'location' in fields:
        out['location'] = fields['location']
    if 'start' in fields and 'end' in fields:
        if fields.get('all_day'):
            last = date.fromisoformat(fields['end'][:10]) + timedelta(days=1)
            out['start'] = {'date': fields['start'][:10], **clear}
            out['end'] = {'date': last.isoformat(), **clear}
        else:
            for key in ('start', 'end'):
                naive = datetime.strptime(fields[key], '%Y-%m-%dT%H:%M')
                if tz:
                    out[key] = {'dateTime': naive.strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': tz, **clear_day}
                else:
                    out[key] = {'dateTime': naive.strftime('%Y-%m-%dT%H:%M:%S') + (offset_of(naive) if offset_of else 'Z'),
                                'timeZone': 'UTC', **clear_day}
    if 'rrule' in fields:
        out['recurrence'] = [f"RRULE:{fields['rrule']}"] if fields['rrule'] else []
    if fields.get('attendees'):
        out['attendees'] = fields['attendees']
    return out
