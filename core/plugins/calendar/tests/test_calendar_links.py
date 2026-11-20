"""Calendar plugin, linked calendars: the copies the store keeps, the ICS
reader, Google (mapping, tokens, write-through), the sync rules, guests, the
links and sign-in routes. Kit and FakeGoogle: conftest.py."""
import hashlib
import json
import sqlite3
import time
from base64 import urlsafe_b64encode
from datetime import date, datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

TODAY = date(2026, 11, 18)

ICS = """BEGIN:VCALENDAR
VERSION:2.0
X-WR-CALNAME:Team\\, shared
BEGIN:VEVENT
UID:allday-1
SUMMARY:Offsite
DTSTART;VALUE=DATE:20261120
DTEND;VALUE=DATE:20261122
LOCATION:Lake house\\; north dock
DESCRIPTION:Bring boots\\nand a very long line that the file folds over two
  lines of text
END:VEVENT
BEGIN:VEVENT
UID:utc-1
SUMMARY:Call
DTSTART:20261119T150000Z
DURATION:PT45M
BEGIN:VALARM
TRIGGER:-PT10M
DESCRIPTION:not the event's description
END:VALARM
END:VEVENT
BEGIN:VEVENT
UID:weekly-1
SUMMARY:Standup
DTSTART;TZID=Europe/London:20261005T090000
DTEND;TZID=Europe/London:20261005T091500
RRULE:FREQ=WEEKLY;BYDAY=MO
EXDATE;TZID=Europe/London:20261109T090000
END:VEVENT
BEGIN:VEVENT
UID:weekly-1
RECURRENCE-ID;TZID=Europe/London:20261116T090000
SUMMARY:Standup (moved)
DTSTART;TZID=Europe/London:20261116T140000
DTEND;TZID=Europe/London:20261116T141500
END:VEVENT
BEGIN:VEVENT
UID:gone-1
SUMMARY:Cancelled thing
DTSTART:20261119T100000
STATUS:CANCELLED
END:VEVENT
BEGIN:VEVENT
UID:outlook-1
SUMMARY:Review
DTSTART;TZID="Pacific Standard Time":20261119T090000
DTEND;TZID="Pacific Standard Time":20261119T100000
END:VEVENT
BEGIN:VEVENT
UID:odd-1
SUMMARY:Odd zone
DTSTART;TZID=Mars/Olympus:20261119T120000
END:VEVENT
END:VCALENDAR
"""


def _in_new_york(cal):
    from zoneinfo import ZoneInfo
    cal.patch.setattr(cal.api, 'tz_name', lambda: 'America/New_York')
    cal.patch.setattr(cal.api, '_zone', lambda: ZoneInfo('America/New_York'))


def _run(cal, name, **args):
    return cal.tools.execute(name, args, None)


# -- the store: copies that keep their ids --------------------------------------

def test_sync_rows_keeps_ids_changes_and_removes(cal):
    link = cal.db.add_link({'calendar': 'default', 'kind': 'ics', 'name': 'Feed', 'ref': 'https://x.example/a.ics'})
    row = lambda ref, title: {'source_ref': ref, 'title': title, 'start': '2026-11-20', 'end': '2026-11-20', 'all_day': True}
    assert cal.db.sync_rows(link, [row('a', 'A'), row('b', 'B')]) == (2, 0, 0)
    first = cal.db.find(link['id'], 'a')
    assert first['calendar'] == 'default' and first['source'] == link['id'] and first['created_by'] == 'link'
    assert cal.db.sync_rows(link, [row('a', 'A again'), row('c', 'C'), row('c', 'a duplicate ref')]) == (1, 1, 1)
    again = cal.db.find(link['id'], 'a')
    assert again['id'] == first['id'] and again['title'] == 'A again'       # the id she was given still means it
    assert cal.db.find(link['id'], 'b') is None and cal.db.find(link['id'], 'c')['title'] == 'C'
    assert cal.db.sync_rows(link, [row('a', 'A again'), row('c', 'C')]) == (0, 0, 0)     # nothing new: nothing written
    assert cal.db.sync_rows(link, [row('z', 'Z')], prune=False) == (1, 0, 0)
    assert cal.db.find(link['id'], 'a') is not None
    assert cal.db.delete_link(link['id']) == 3 and cal.db.links() == []


def test_a_database_from_before_links_gets_the_new_columns(cal, tmp_path):
    path = tmp_path / 'old.db'
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE calendars (name TEXT PRIMARY KEY, created_at REAL NOT NULL);
        CREATE TABLE events (id TEXT PRIMARY KEY, uid TEXT NOT NULL, calendar TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'local', source_ref TEXT NOT NULL DEFAULT '', title TEXT NOT NULL,
            start TEXT NOT NULL, end TEXT NOT NULL, all_day INTEGER NOT NULL DEFAULT 0, tz TEXT NOT NULL DEFAULT '',
            rrule TEXT NOT NULL DEFAULT '', exdates TEXT NOT NULL DEFAULT '[]', location TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL DEFAULT 'user',
            created_at REAL NOT NULL, updated_at REAL NOT NULL);
        INSERT INTO events (id, uid, calendar, title, start, end, all_day, created_at, updated_at)
            VALUES ('abc123', 'u1', 'default', 'Kept', '2026-11-18', '2026-11-18', 1, 1, 1);
    """)
    conn.commit()
    conn.close()
    old = cal.store.Store(path)
    try:
        ev = old.get('abc123')
        assert ev['title'] == 'Kept' and ev['guests'] == [] and ev['series'] == '' and ev['url'] == ''
        assert old.links() == [] and old.meta('target:default') == ''
    finally:
        old.close()


# -- ICS ------------------------------------------------------------------------

def test_ics_file_is_read_into_rows(cal):
    _in_new_york(cal)
    name, rows, notes = cal.ics.parse(ICS, cal.api.convert, 'America/New_York')
    assert name == 'Team, shared'
    by = {r['source_ref']: r for r in rows}
    assert set(by) == {'allday-1', 'utc-1', 'weekly-1', 'weekly-1#2026-11-16', 'outlook-1', 'odd-1'}   # cancelled one is out
    off = by['allday-1']
    assert (off['start'], off['end'], off['all_day']) == ('2026-11-20', '2026-11-21', True)      # the file's end is exclusive
    assert off['location'] == 'Lake house; north dock'
    assert off['notes'] == 'Bring boots\nand a very long line that the file folds over two lines of text'
    call = by['utc-1']                                               # 15:00 UTC is 10:00 in New York; 45 minutes
    assert (call['start'], call['end'], call['tz']) == ('2026-11-19T10:00', '2026-11-19T10:45', '')
    assert call['notes'] == ''                                       # the alarm's text is not the event's
    weekly = by['weekly-1']                                          # a repeat stays in its own zone
    assert (weekly['start'], weekly['tz'], weekly['rrule']) == ('2026-10-05T09:00', 'Europe/London', 'FREQ=WEEKLY;BYDAY=MO')
    assert weekly['exdates'] == ['2026-11-09', '2026-11-16']         # one removed, one moved
    moved = by['weekly-1#2026-11-16']                                # 14:00 London is 09:00 New York
    assert (moved['title'], moved['start'], moved['rrule']) == ('Standup (moved)', '2026-11-16T09:00', '')
    assert by['outlook-1']['start'] == '2026-11-19T12:00'            # 09:00 Pacific, by its Windows name
    assert by['odd-1']['start'] == '2026-11-19T12:00' and 'Mars/Olympus' in notes[0]


def test_a_repeat_kept_in_another_zone_is_right_across_both_clock_changes(cal):
    """Weekly 09:00 London, seen from New York. Britain leaves summer time on
    Oct 25 2026 and the US on Nov 1, so for one week the gap is 4 hours, not 5."""
    _in_new_york(cal)
    cal.use_ics(ICS)
    cal.api.add_ics('default', 'https://x.example/team.ics')
    seen = {o['day']: o['start'][11:] for o in cal.api.list_range('2026-10-12', '2026-11-30', 'default')
            if o['title'] == 'Standup'}
    assert seen == {'2026-10-12': '04:00', '2026-10-19': '04:00', '2026-10-26': '05:00',
                    '2026-11-02': '04:00', '2026-11-23': '04:00', '2026-11-30': '04:00'}
    moved = [o for o in cal.api.list_range('2026-11-16', '2026-11-16', 'default') if 'moved' in o['title']]
    assert [m['start'] for m in moved] == ['2026-11-16T09:00']


def test_a_rule_outside_our_subset_goes_to_dateutil(cal):
    pytest.importorskip('dateutil')
    hits = cal.repeat.expand(datetime(2026, 11, 10, 18, 0), 'FREQ=MONTHLY;BYDAY=2TU;UNTIL=20270301T000000Z',
                             date(2026, 11, 1), date(2027, 6, 30), ['2027-01-12'])
    assert [h.isoformat() for h in hits] == ['2026-11-10T18:00:00', '2026-12-08T18:00:00', '2027-02-09T18:00:00']
    assert cal.repeat.describe('FREQ=MONTHLY;BYDAY=2TU') == 'repeats'
    with pytest.raises(ValueError):
        cal.repeat.expand(date(2026, 11, 10), 'FREQ=SOMETIMES', date(2026, 11, 1), date(2026, 12, 1))


def test_calendar_address_rules(cal):
    assert cal.ics.clean_url(' webcal://cal.example/a.ics ') == 'https://cal.example/a.ics'
    for bad in ('', 'file:///etc/passwd', 'ftp://x.example/a.ics', 'cal.example/a.ics', 'javascript:alert(1)'):
        with pytest.raises(cal.api.CalError):
            cal.ics.clean_url(bad)

    def page(url, **kw):
        resp = MagicMock(status_code=200)
        resp.iter_content.return_value = [b'<html><body>Sign in</body></html>']
        return resp
    with patch.object(cal.ics.net, 'get', side_effect=page):
        with pytest.raises(cal.api.CalError, match='did not return a calendar file'):
            cal.ics.fetch('https://cal.example/a.ics')

    def big(url, **kw):
        resp = MagicMock(status_code=200)
        resp.iter_content.return_value = [b'BEGIN:VCALENDAR\n'] + [b'x' * 65536] * 90
        return resp
    with patch.object(cal.ics.net, 'get', side_effect=big):
        with pytest.raises(cal.api.CalError, match='too big'):
            cal.ics.fetch('https://cal.example/a.ics')


def test_an_ics_link_is_read_only_and_leaves_with_its_events(cal):
    _in_new_york(cal)
    cal.use_ics(ICS)
    link = cal.api.add_ics('default', 'webcal://x.example/team.ics')
    assert (link['name'], link['kind'], link['writable'], link['ref']) == ('Team, shared', 'ics', False, 'https://x.example/team.ics')
    with pytest.raises(cal.api.CalError, match='already linked'):
        cal.api.add_ics('default', 'https://x.example/team.ics')
    view = cal.api.links('default')[0]
    assert view['where'] == 'x.example' and 'team.ics' not in json.dumps(view)       # the page never gets the address
    off = next(o for o in cal.api.list_range('2026-11-20', '2026-11-20', 'default') if o['title'] == 'Offsite')
    assert (off['source'], off['link_name'], off['editable'], off['color']) == ('ics', 'Team, shared', False, link['color'])
    for act in (lambda: cal.api.update(off['id'], {'title': 'x'}), lambda: cal.api.remove(off['id'])):
        with pytest.raises(cal.api.CalError, match='read-only'):
            act()
    msg, ok = _run(cal, 'calendar_delete', id=off['id'])
    assert not ok and 'read-only' in msg
    listing, _ = _run(cal, 'calendar_range', start_date='2026-11-20', end_date='2026-11-20')
    assert '(Team, shared, read-only)' in listing and 'note from Team, shared: Bring boots' in listing
    with pytest.raises(cal.api.CalError, match='read-only'):
        cal.api.set_target('default', link['id'])
    assert cal.api.remove_link(link['id']) == 6 and cal.api.list_range('2026-10-01', '2026-12-31', 'default') == []


def test_a_bad_address_is_refused_before_anything_is_linked(cal):
    cal.use_ics(cal.api.CalError("That address did not return a calendar file."))
    with pytest.raises(cal.api.CalError, match='did not return'):
        cal.api.add_ics('default', 'https://x.example/page')
    assert cal.db.links() == []


# -- Google: mapping --------------------------------------------------------------

def test_google_event_to_row(cal):
    g = cal.google
    local = lambda aware: aware.astimezone(timezone(timedelta(hours=-5))).replace(tzinfo=None)
    row = g.to_row({'id': 'e1', 'summary': 'Trip', 'start': {'date': '2026-11-20'}, 'end': {'date': '2026-11-23'},
                    'htmlLink': 'https://calendar.google.com/x'}, local)
    assert (row['start'], row['end'], row['all_day'], row['url']) == ('2026-11-20', '2026-11-22', True, 'https://calendar.google.com/x')
    row = g.to_row({'id': 'e2_20261119T140000Z', 'recurringEventId': 'e2', 'start': {'dateTime': '2026-11-19T14:00:00Z'},
                    'end': {'dateTime': '2026-11-19T15:30:00Z'}, 'description': 'd', 'location': 'l',
                    'attendees': [{'email': 'me@example.com', 'self': True, 'responseStatus': 'accepted'},
                                  {'email': 'rob@example.com', 'displayName': 'Rob', 'responseStatus': 'tentative'},
                                  {'email': 'room@example.com', 'resource': True}]}, local)
    assert (row['start'], row['end'], row['series'], row['title']) == ('2026-11-19T09:00', '2026-11-19T10:30', 'e2', '(No title)')
    assert [(x['name'], x['rsvp'], x['self']) for x in row['guests']] == [('me@example.com', 'yes', True), ('Rob', 'maybe', False)]
    assert g.to_row({'id': 'e3', 'status': 'cancelled'}, local) is None
    assert g.to_row({'id': 'e4', 'start': {}, 'end': {}}, local) is None
    assert g.to_row({'id': 'e5', 'start': {'dateTime': 'not a time'}}, local) is None


def test_row_to_google_body(cal):
    body = cal.google.body
    all_day = body({'title': 'Trip', 'start': '2026-11-20', 'end': '2026-11-22', 'all_day': True, 'notes': 'n',
                    'location': '', 'rrule': 'FREQ=YEARLY'}, 'America/New_York')
    assert all_day == {'summary': 'Trip', 'description': 'n', 'location': '', 'start': {'date': '2026-11-20'},
                       'end': {'date': '2026-11-23'}, 'recurrence': ['RRULE:FREQ=YEARLY']}       # Google's end is exclusive
    timed = body({'start': '2026-11-20T09:00', 'end': '2026-11-20T10:00', 'all_day': False}, 'America/New_York', patching=True)
    assert timed['start'] == {'dateTime': '2026-11-20T09:00:00', 'timeZone': 'America/New_York', 'date': None}
    no_zone = body({'start': '2026-11-20T09:00', 'end': '2026-11-20T10:00', 'all_day': False}, '', lambda naive: '-05:00')
    assert no_zone['start'] == {'dateTime': '2026-11-20T09:00:00-05:00', 'timeZone': 'UTC'}
    assert body({'title': 'x'}) == {'summary': 'x'} and body({'rrule': ''}) == {'recurrence': []}


# -- Google: the connection ---------------------------------------------------------

@pytest.fixture
def account(cal, monkeypatch):
    """A credentials store with one Google account under 'default'."""
    state = {'access_token': '', 'expires_at': 0, 'refresh_token': 'rt-current', 'client_id': 'cid',
             'client_secret': 'csec', 'auth_mode': 'byo', 'relay_url': ''}
    creds = MagicMock()
    creds.get_gcal_tokens_snapshot.side_effect = lambda scope: {'access_token': state['access_token'], 'expires_at': state['expires_at']}
    creds.get_gcal_account.side_effect = lambda scope: dict(state, calendar_id='primary', label=scope)

    def save(scope, refresh_token, access_token, expires_at):
        state.update(refresh_token=refresh_token or state['refresh_token'], access_token=access_token, expires_at=expires_at)
        return True
    creds.update_gcal_tokens.side_effect = save
    monkeypatch.setattr(cal.google, '_creds', lambda: creds)
    monkeypatch.setattr(cal.google, '_locks', {})
    return state


def _answer(status=200, **body):
    resp = MagicMock(status_code=status, text=json.dumps(body))
    resp.json.return_value = body
    return resp


def test_a_rotated_refresh_token_is_the_one_saved(cal, account):
    with patch.object(cal.google.net, 'post', return_value=_answer(access_token='new', expires_in=3600, refresh_token='rt-ROTATED')) as post:
        assert cal.google._token('default') == 'new'
        assert account['refresh_token'] == 'rt-ROTATED'
        assert post.call_args[0][0] == cal.google.TOKEN_URL and post.call_args[1]['data']['client_id'] == 'cid'
        assert cal.google._token('default') == 'new' and post.call_count == 1          # still fresh: not asked again
    with patch.object(cal.google.net, 'post', return_value=_answer(access_token='newer', expires_in=3600)):
        assert cal.google._token('default', force=True) == 'newer'
        assert account['refresh_token'] == 'rt-ROTATED'                                # none sent back: the old one stays


def test_an_easy_connect_account_refreshes_through_its_relay(cal, account):
    account.update(auth_mode='relay', client_id='', client_secret='', relay_url='https://relay.example/')
    with patch.object(cal.google.net, 'post', return_value=_answer(access_token='at', expires_in=3600)) as post:
        assert cal.google._token('default') == 'at'
    assert post.call_args[0][0] == 'https://relay.example/refresh'
    assert post.call_args[1]['json'] == {'refresh_token': 'rt-current'}


@pytest.mark.parametrize('status,body,words', [
    (400, {'error': 'invalid_grant'}, 'connect Google again'),
    (503, {}, 'not answering right now'),
    (400, {'error': 'weird'}, 'refused to renew'),
])
def test_refresh_failures_say_what_to_do(cal, account, status, body, words):
    with patch.object(cal.google.net, 'post', return_value=_answer(status, **body)):
        with pytest.raises(cal.api.CalError, match=words):
            cal.google._token('default')


def test_no_account_and_a_dead_network_are_refusals_in_words(cal, account):
    import requests
    with patch.object(cal.google.net, 'post', side_effect=requests.ConnectionError('down')):
        with pytest.raises(cal.api.CalError, match="Couldn't reach Google"):
            cal.google._token('default')
    account['refresh_token'] = ''
    with pytest.raises(cal.api.CalError, match='not connected'):
        cal.google._token('default')
    account.update(refresh_token='rt', client_secret='')
    with pytest.raises(cal.api.CalError, match='client ID and secret'):
        cal.google._token('default')


def test_a_401_forces_one_fresh_token_and_one_retry(cal, account):
    tokens = iter(['stale', 'fresh', 'again'])
    forced = []

    def token(scope, force=False):
        forced.append(force)
        return next(tokens)
    cal.patch.setattr(cal.google, '_token', token)
    answers = iter([_answer(401), _answer(200, items=[])])
    with patch.object(cal.google.net, 'request', side_effect=lambda *a, **k: next(answers)) as req:
        assert cal.google.call('default', 'GET', '/calendars/primary/events') == {'items': []}
    assert forced == [False, True] and req.call_args[1]['headers'] == {'Authorization': 'Bearer fresh'}
    with patch.object(cal.google.net, 'request', return_value=_answer(401)):
        tokens = iter(['a', 'b'])
        with pytest.raises(cal.api.CalError, match='turned the connection down'):
            cal.google.call('default', 'GET', '/x')


def test_calendar_ids_are_quoted_in_the_address(cal):
    assert cal.google._events('en.usa#holiday@group.v.calendar.google.com', 'a/b') == \
        '/calendars/en.usa%23holiday%40group.v.calendar.google.com/events/a%2Fb'


# -- Google: linking and refreshing -------------------------------------------------

def test_a_connected_account_is_linked_once_and_becomes_the_target(cal):
    g = cal.use_google()
    g.put(summary='Dentist', **g.timed('2026-11-19'))
    assert cal.api.refresh('default') == []
    link = cal.db.links('default')[0]
    assert (link['kind'], link['ref'], link['writable'], link['name']) == ('google', 'primary', True, 'Google')
    assert cal.api.target_of('default')['id'] == link['id']
    got = cal.api.list_range('2026-11-19', '2026-11-19', 'default')
    assert [(o['title'], o['start'], o['source'], o['editable']) for o in got] == [('Dentist', '2026-11-19T09:00', 'google', True)]
    win = cal.db.links('default')[0]
    assert win['win_from'] == '2026-09-17' and win['win_to'] == '2027-12-23' and win['error'] == ''
    # unlinked by hand: it is not linked again behind the person's back
    cal.api.remove_link(link['id'])
    cal.api.refresh('default', force=True)
    assert cal.db.links('default') == [] and cal.api.target_of('default') is None
    # connecting again (the sign-in route) links it again
    cal.api.google_connected('default')
    assert [l['kind'] for l in cal.db.links('default')] == ['google']


def test_refresh_is_throttled_and_a_wider_look_grows_the_window(cal):
    g = cal.use_google()
    cal.api.refresh('default')
    cal.api.refresh('default')
    assert len(g.named('instances')) == 1                         # fresh for a minute
    cal.api.refresh('default', need=(date(2028, 6, 1), date(2028, 6, 30)))
    assert len(g.named('instances')) == 2 and cal.db.links('default')[0]['win_to'] == '2028-06-30'
    cal.api.refresh('default', need=(date(2028, 6, 1), date(2028, 6, 30)))
    assert len(g.named('instances')) == 2
    assert g.named('instances')[1][3].startswith('2026-09-17T00:00:00-0')       # an instant with its offset


def test_a_failed_refresh_keeps_the_copy_and_says_so(cal):
    g = cal.use_google()
    g.put(summary='Dentist', **g.timed('2026-11-19'))
    cal.api.refresh('default')
    g.down = "Couldn't reach Google Calendar (ConnectionError)."
    failed = cal.api.refresh('default', force=True)
    assert [(l['name'], why) for l, why in failed] == [('Google', g.down)]
    assert cal.db.links('default')[0]['error'] == g.down
    assert [o['title'] for o in cal.api.list_range('2026-11-19', '2026-11-19', 'default')] == ['Dentist']
    calls = len(g.named('instances'))
    cal.db.update_link(cal.db.links('default')[0]['id'], {'synced_at': 0})
    assert cal.api.refresh('default') == [] and len(g.named('instances')) == calls        # not hammered while it is down
    listing, ok = _run(cal, 'calendar_range', start_date='2026-11-19', end_date='2026-11-19')
    assert ok and 'Dentist' in listing


def test_her_read_says_when_a_copy_is_old(cal):
    g = cal.use_google()
    g.put(summary='Dentist', **g.timed('2026-11-18', '14:00', '15:00'))
    cal.api.refresh('default')
    g.down = "Couldn't reach Google Calendar (ConnectionError)."
    cal.db.update_link(cal.db.links('default')[0]['id'], {'synced_at': time.time() - 3600})
    msg, ok = _run(cal, 'calendar_today')
    assert ok and 'Dentist' in msg and "('Google' could not be refreshed" in msg and 'Showing its copy from' in msg


def test_a_private_chat_reads_the_copy_and_never_calls_out(cal):
    g = cal.use_google()
    g.put(summary='Dentist', **g.timed('2026-11-18', '14:00', '15:00'))
    cal.api.refresh('default')
    cal.db.update_link(cal.db.links('default')[0]['id'], {'synced_at': 0})
    calls = len(g.calls)
    cal.private.set(True)
    msg, ok = _run(cal, 'calendar_today')
    assert ok and 'Dentist' in msg and 'Private chat: linked calendars were not refreshed' in msg
    msg, ok = _run(cal, 'calendar_add', title='secret', start='2026-11-20')
    assert not ok and 'private' in msg.lower()
    assert len(g.calls) == calls


# -- Google: changes go there first ---------------------------------------------------

def test_a_new_event_goes_to_google_when_that_is_the_target(cal):
    g = cal.use_google()
    cal.api.refresh('default')
    msg, ok = _run(cal, 'calendar_add', title='Dentist', start='2026-11-20 09:00', location='Main St', description='bring card')
    assert ok and msg.startswith("Added 'Dentist': Fri Nov 20 09:00-10:00. Saved to Google. [id ")
    _, scope, cal_id, body, notify = g.named('insert')[0]
    assert (scope, cal_id, notify) == ('default', 'primary', False)
    assert body == {'summary': 'Dentist', 'description': 'bring card', 'location': 'Main St', 'recurrence': [],
                    'start': {'dateTime': '2026-11-20T09:00:00', 'timeZone': 'America/New_York'},
                    'end': {'dateTime': '2026-11-20T10:00:00', 'timeZone': 'America/New_York'}}
    row = cal.db.find(cal.db.links('default')[0]['id'], 'g1')
    assert row['title'] == 'Dentist' and f"[id {row['id']}]" in msg
    # 'local' for this one event; the calendar's choice stays
    msg, ok = _run(cal, 'calendar_add', title='Private note', start='2026-11-21', save_to='local')
    assert ok and 'Saved to' not in msg and len(g.named('insert')) == 1
    msg, ok = _run(cal, 'calendar_add', title='x', start='2026-11-21', save_to='Nowhere')
    assert not ok and "no link named 'Nowhere'" in msg and 'local, Google' in msg
    # the person can point new events back at this machine
    assert cal.api.set_target('default', 'local') == '' and cal.api.target_of('default') is None
    _run(cal, 'calendar_add', title='Local again', start='2026-11-22')
    assert len(g.named('insert')) == 1


def test_google_down_means_the_event_is_not_added_anywhere(cal):
    g = cal.use_google()
    cal.api.refresh('default')
    g.down = "Couldn't reach Google Calendar (ConnectionError)."
    msg, ok = _run(cal, 'calendar_add', title='Dentist', start='2026-11-20 09:00')
    assert not ok and msg == g.down
    assert cal.api.list_range('2026-11-20', '2026-11-20', 'default') == []


def test_changing_and_deleting_a_google_event(cal):
    g = cal.use_google()
    g.put(id='solo', summary='Dentist', attendees=[{'email': 'rob@example.com', 'displayName': 'Rob'}], **g.timed('2026-11-19'))
    cal.api.refresh('default')
    link = cal.db.links('default')[0]
    row = cal.db.find(link['id'], 'solo')
    msg, ok = _run(cal, 'calendar_update', id=row['id'], start='2026-11-19 14:00', title='Dentist!')
    assert ok and msg == f"Updated 'Dentist!': Thu Nov 19 14:00-15:00. [id {row['id']}]"
    _, _, _, event_id, body, notify = g.named('patch')[0]
    assert event_id == 'solo' and notify is True                    # a guest hears about the change
    assert body == {'summary': 'Dentist!',
                    'start': {'dateTime': '2026-11-19T14:00:00', 'timeZone': 'America/New_York', 'date': None},
                    'end': {'dateTime': '2026-11-19T15:00:00', 'timeZone': 'America/New_York', 'date': None}}
    assert cal.db.get(row['id'])['start'] == '2026-11-19T14:00'     # same id, refreshed from Google
    msg, ok = _run(cal, 'calendar_update', id=row['id'], date='2026-11-19', title='x')
    assert not ok and 'does not repeat' in msg
    msg, ok = _run(cal, 'calendar_delete', event_id=row['id'])       # the old tool's argument name still works
    assert ok and msg == "Deleted 'Dentist!'." and g.named('delete')[0][3] == 'solo'
    assert cal.db.get(row['id']) is None and 'solo' not in g.items['primary']


def _series(g):
    g.put(id='std', summary='Standup', recurrence=['RRULE:FREQ=WEEKLY;BYDAY=MO,WE'], **g.timed('2026-11-02'))
    for day in ('2026-11-16', '2026-11-18', '2026-11-23'):
        g.put(id=f"std_{day.replace('-', '')}", summary='Standup', recurringEventId='std', **g.timed(day))


def test_a_google_repeat_id_alone_is_the_series_id_plus_date_is_one_day(cal):
    g = cal.use_google()
    _series(g)
    cal.api.refresh('default')
    link = cal.db.links('default')[0]
    wed = cal.db.find(link['id'], 'std_20261118')
    listing, _ = _run(cal, 'calendar_range', start_date='2026-11-16', end_date='2026-11-23')
    assert listing.count('Standup (repeats) (Google)') == 3
    # one day: that occurrence is changed at Google, by the id of any occurrence plus the date
    msg, ok = _run(cal, 'calendar_update', id=wed['id'], date='2026-11-23', start='2026-11-23 11:00')
    assert ok and 'that one occurrence' in msg and g.named('patch')[0][3] == 'std_20261123'
    # the whole series: the master moves by the same amount the addressed occurrence moved
    msg, ok = _run(cal, 'calendar_update', id=wed['id'], start='2026-11-18 10:30', title='Daily sync')
    assert ok and g.named('get')[0][3] == 'std'
    _, _, _, event_id, body, _ = g.named('patch')[1]
    assert event_id == 'std' and body['summary'] == 'Daily sync'
    assert body['start']['dateTime'] == '2026-11-02T10:30:00' and body['end']['dateTime'] == '2026-11-02T11:30:00'
    msg, ok = _run(cal, 'calendar_update', id=wed['id'], date='2026-11-19', title='x')
    assert not ok and 'no occurrence that starts on 2026-11-19' in msg
    msg, ok = _run(cal, 'calendar_update', id=wed['id'], date='2026-11-18', repeat='daily')
    assert not ok and 'One occurrence cannot have its own repeat' in msg
    # delete one day, then the series
    msg, ok = _run(cal, 'calendar_delete', id=wed['id'], date='2026-11-16')
    assert ok and msg == "Deleted the Mon Nov 16 occurrence of 'Standup'. The rest of the series stays."
    assert g.named('delete')[0][3] == 'std_20261116' and cal.db.get(wed['id']) is not None
    msg, ok = _run(cal, 'calendar_delete', id=wed['id'])
    assert ok and msg == "Deleted 'Standup' and all its repeats." and g.named('delete')[1][3] == 'std'
    assert cal.db.series(link['id'], 'std') == []


def test_one_occurrence_of_a_local_series_can_be_changed(cal):
    made = cal.api.create({'title': 'Standup', 'start': '2026-11-16T09:00', 'end': '2026-11-16T09:15',
                           'repeat': 'weekly', 'days': 'mon,wed'}, 'default')
    msg, ok = _run(cal, 'calendar_update', id=made['id'], date='2026-11-18', start='2026-11-18 11:00', title='Standup (late)')
    assert ok and 'that one occurrence' in msg and 'Wed Nov 18 11:00-11:15' in msg
    week = [(o['title'], o['start'], o['repeats']) for o in cal.api.list_range('2026-11-16', '2026-11-23', 'default')]
    assert week == [('Standup', '2026-11-16T09:00', True), ('Standup (late)', '2026-11-18T11:00', False),
                    ('Standup', '2026-11-23T09:00', True)]
    assert cal.db.get(made['id'])['exdates'] == ['2026-11-18']
    msg, ok = _run(cal, 'calendar_update', id=made['id'], date='2026-11-18', title='again')
    assert not ok and 'no occurrence that starts on 2026-11-18' in msg


# -- guests -----------------------------------------------------------------------

PEOPLE = [
    {'id': 1, 'name': 'Rob Smith', 'email': 'rob@example.com', 'email_whitelisted': True},
    {'id': 2, 'name': 'Sam Lee', 'email': 'sam@example.com', 'email_whitelisted': True},
    {'id': 3, 'name': 'Sam Park', 'email': 'park@example.com', 'email_whitelisted': True},
    {'id': 4, 'name': 'Dana', 'email': 'dana@example.com', 'email_whitelisted': False},
    {'id': 5, 'name': 'Noemail', 'email': '', 'email_whitelisted': True},
]


@pytest.fixture
def people(cal, monkeypatch):
    import core.contacts as contacts
    monkeypatch.setattr(contacts, 'get_people', lambda scope='default': list(PEOPLE))
    monkeypatch.setattr(cal.tools, '_people_scope', lambda: 'default')


def test_guests_come_only_from_the_people_whitelist(cal, people):
    resolve = cal.tools._resolve_attendees
    assert resolve(None) == ([], None) and resolve([]) == ([], None)
    assert resolve('Rob and Sam Lee, 1') == ([{'email': 'rob@example.com', 'displayName': 'Rob Smith'},
                                              {'email': 'sam@example.com', 'displayName': 'Sam Lee'}], None)
    assert resolve('["Rob"]')[0][0]['email'] == 'rob@example.com'       # an array inside a string
    for raw, words in ((['Sam'], 'matches several contacts'), (['Dana'], "isn't allowed for invites"),
                       (['Noemail'], 'has no email address'), (['stranger@example.com'], 'No contact named'),
                       (['99'], 'Contact id 99 not found')):
        got, err = resolve(raw)
        assert got is None and words in err, raw
    assert 'Invitable contacts: Rob Smith, Sam Lee, Sam Park' in resolve(['nobody'])[1]
    cal.patch.setattr(cal.tools, '_people_scope', lambda: None)
    assert 'disabled for this chat' in resolve(['Rob'])[1]


def test_guests_are_invited_through_google_and_shown_with_their_answer(cal, people):
    msg, ok = _run(cal, 'calendar_add', title='Lunch', start='2026-11-20 12:00', attendees=['Rob'])
    assert not ok and 'Guests are invited through a Google calendar' in msg         # nothing linked: nothing half-made
    assert cal.api.list_range('2026-11-20', '2026-11-20', 'default') == []
    g = cal.use_google()
    cal.api.refresh('default')
    msg, ok = _run(cal, 'calendar_add', title='Lunch', start='2026-11-20 12:00', attendees=['Dana'])
    assert not ok and g.named('insert') == []                                       # a bad guest: nothing reaches Google
    msg, ok = _run(cal, 'calendar_add', title='Lunch', start='2026-11-20 12:00', attendees='Rob, Sam Lee')
    assert ok and msg.endswith('Invited (Google is emailing them): Rob Smith, Sam Lee')
    _, _, _, body, notify = g.named('insert')[0]
    assert notify is True and [a['email'] for a in body['attendees']] == ['rob@example.com', 'sam@example.com']
    listing, _ = _run(cal, 'calendar_range', start_date='2026-11-20', end_date='2026-11-20')
    assert '[guests: Rob Smith (pending), Sam Lee (pending)]' in listing


def test_a_part_of_a_name_must_be_a_whole_word_of_it(cal, people, monkeypatch):
    import core.contacts as contacts
    folks = PEOPLE + [{'id': 6, 'name': 'Samantha Ruiz', 'email': 'sr@example.com', 'email_whitelisted': True},
                      {'id': 7, 'name': 'Sally Brown', 'email': 'sb@example.com', 'email_whitelisted': True}]
    monkeypatch.setattr(contacts, 'get_people', lambda scope='default': list(folks))
    resolve = cal.tools._resolve_attendees
    assert resolve(['Rob'])[0][0]['email'] == 'rob@example.com'               # a first name on its own still works
    assert resolve(['sam lee'])[0][0]['email'] == 'sam@example.com'
    got, err = resolve(['Al'])                                                 # never Sally: an invite is an email
    assert got is None and "No contact named 'Al'" in err and 'Sally Brown' in err
    got, err = resolve(['Saman'])
    assert got is None and 'Close: Samantha Ruiz' in err
    got, err = resolve(['Sam'])                                                # Sam Lee or Sam Park, not Samantha
    assert got is None and 'matches several' in err and 'Samantha' not in err.split('Invitable')[0]


def test_a_guest_google_gives_no_name_for_is_not_shown_by_address(cal, people):
    g = cal.use_google()
    g.put(id='x', summary='Sync', attendees=[{'email': 'me@example.com', 'self': True, 'responseStatus': 'accepted'},
                                             {'email': 'stranger@corp.example', 'responseStatus': 'needsAction'}],
          **g.timed('2026-11-20', '09:00', '10:00'))
    cal.api.refresh('default')
    listing, _ = _run(cal, 'calendar_range', start_date='2026-11-20', end_date='2026-11-20')
    assert '[guests: stranger (pending)]' in listing and 'corp.example' not in listing
    eid = cal.api.list_range('2026-11-20', '2026-11-20', 'default')[0]['id']
    msg, ok = _run(cal, 'calendar_update', id=eid, remove_guests=['stranger'])  # and can be named the way it is shown
    assert ok and [a['email'] for a in g.named('patch')[0][4]['attendees']] == ['me@example.com']
    cal.patch.setattr(cal.tools, '_people_scope', lambda: 'default')
    import core.contacts as contacts
    cal.patch.setattr(contacts, 'get_people', lambda scope='default': [
        {'id': 9, 'name': 'Odd', 'email': 'not an address', 'email_whitelisted': True}])
    got, err = cal.tools._resolve_attendees(['Odd'])
    assert got is None and "isn't a valid one" in err and 'not an address' not in err


def _lunch(cal, people_fixture=None, **extra):
    """A Google event with the owner and Rob on it, copied down. Returns (fake Google, its id here)."""
    g = cal.use_google()
    g.put(id='lunch', summary='Lunch', attendees=[
        {'email': 'me@example.com', 'self': True, 'responseStatus': 'accepted', 'organizer': True},
        {'email': 'rob@example.com', 'displayName': 'Rob Smith', 'responseStatus': 'accepted'}],
        **g.timed('2026-11-20', '12:00', '13:00'), **extra)
    cal.api.refresh('default')
    return g, cal.api.list_range('2026-11-20', '2026-11-20', 'default')[0]['id']


def test_a_guest_can_be_added_later_and_nobody_elses_answer_is_lost(cal, people):
    g, eid = _lunch(cal)
    msg, ok = _run(cal, 'calendar_update', id=eid, add_guests=['Dana'])
    assert not ok and "isn't allowed for invites" in msg and g.named('patch') == []      # the whitelist holds here too
    msg, ok = _run(cal, 'calendar_update', id=eid, add_guests=['Sam Lee', 'Rob'])
    assert ok and 'Invited (Google is emailing them): Sam Lee, Rob Smith' in msg
    _, _, _, _, body, notify = g.named('patch')[0]
    assert notify is True and set(body) == {'attendees'}                                 # nothing else is touched
    assert [(a['email'], a.get('responseStatus')) for a in body['attendees']] == [
        ('me@example.com', 'accepted'), ('rob@example.com', 'accepted'), ('sam@example.com', None)]   # Rob once, still a yes
    listing, _ = _run(cal, 'calendar_range', start_date='2026-11-20', end_date='2026-11-20')
    assert '[guests: Rob Smith (yes), Sam Lee (pending)]' in listing


def test_a_guest_can_be_taken_off_by_the_name_the_event_shows(cal, people):
    g, eid = _lunch(cal)
    msg, ok = _run(cal, 'calendar_update', id=eid, remove_guests=['Nobody'])
    assert not ok and "'Nobody' is not a guest of 'Lunch'" in msg and g.named('patch') == []
    msg, ok = _run(cal, 'calendar_update', id=eid, remove_guests='rob', location='Cafe')
    assert ok and 'Taken off the guest list (Google tells them): rob' in msg
    _, _, _, _, body, notify = g.named('patch')[0]
    assert notify is True and body['location'] == 'Cafe'
    assert [a['email'] for a in body['attendees']] == ['me@example.com']                  # the owner is never removed
    assert 'guests:' not in _run(cal, 'calendar_range', start_date='2026-11-20', end_date='2026-11-20')[0]


def test_an_invitation_is_shown_as_one_and_can_be_answered(cal, people):
    g = cal.use_google()
    g.put(id='inv', summary='Review', attendees=[
        {'email': 'boss@example.com', 'displayName': 'The Boss', 'responseStatus': 'accepted', 'organizer': True},
        {'email': 'me@example.com', 'self': True, 'responseStatus': 'needsAction'}], **g.timed('2026-11-20', '15:00', '16:00'))
    g.put(id='mine', summary='Solo', **g.timed('2026-11-20', '17:00', '18:00'))
    cal.api.refresh('default')
    by_title = {e['title']: e for e in cal.api.list_range('2026-11-20', '2026-11-20', 'default')}
    assert by_title['Review']['my_rsvp'] == 'pending' and by_title['Solo']['my_rsvp'] == ''
    listing, _ = _run(cal, 'calendar_range', start_date='2026-11-20', end_date='2026-11-20')
    assert '[guests: The Boss (yes); your answer: pending]' in listing

    msg, ok = _run(cal, 'calendar_update', id=by_title['Solo']['id'], rsvp='yes')
    assert not ok and 'is not an invitation' in msg
    msg, ok = _run(cal, 'calendar_update', id=by_title['Review']['id'], rsvp='later')
    assert not ok and 'yes, no or maybe' in msg and g.named('patch') == []
    msg, ok = _run(cal, 'calendar_update', id=by_title['Review']['id'], rsvp='maybe')
    assert ok and 'Answered the invitation: maybe.' in msg
    _, _, _, _, body, notify = g.named('patch')[0]
    assert notify is True                                                                  # the organizer hears the answer
    assert [(a['email'], a['responseStatus']) for a in body['attendees']] == [
        ('boss@example.com', 'accepted'), ('me@example.com', 'tentative')]
    assert 'your answer: maybe' in _run(cal, 'calendar_range', start_date='2026-11-20', end_date='2026-11-20')[0]


def test_guests_hear_of_a_change_and_of_a_cancellation(cal, people):
    g, eid = _lunch(cal)
    msg, ok = _run(cal, 'calendar_update', id=eid, start='2026-11-20 13:00', notes='Bring the report')
    assert ok
    _, _, _, _, body, notify = g.named('patch')[0]
    assert notify is True and body['description'] == 'Bring the report' and 'attendees' not in body
    assert g.items['primary']['lunch']['attendees'][1]['responseStatus'] == 'accepted'    # an edit resets no answers
    msg, ok = _run(cal, 'calendar_delete', id=eid)
    assert ok and g.named('delete')[0][-1] is True


def test_a_local_event_has_no_guests_and_says_why(cal, people):
    ev = cal.api.create({'title': 'Here only', 'start': '2026-11-20T09:00'}, 'default')
    for change in ({'add_guests': ['Rob']}, {'remove_guests': ['Rob']}, {'rsvp': 'yes'}):
        msg, ok = _run(cal, 'calendar_update', id=ev['id'], **change)
        assert not ok and 'go through a Google calendar' in msg, change
    msg, ok = _run(cal, 'calendar_update', id=ev['id'])
    assert not ok and 'add_guests, remove_guests, rsvp' in msg


def test_the_page_invites_by_address_and_edits_the_guest_list(cal):
    g = cal.use_google()
    cal.api.refresh('default')
    link = cal.api.links('default')[0]
    made = cal.routes.create_event(body={'title': 'Demo', 'start': '2026-11-20T10:00', 'end': '2026-11-20T11:00',
                                         'calendar': 'default', 'to': link['id'], 'guests': 'a@x.org, B <b@x.org>'})
    assert isinstance(made, tuple) and "is not an email address" in made[0]['detail'] and g.named('insert') == []
    made = cal.routes.create_event(body={'title': 'Demo', 'start': '2026-11-20T10:00', 'end': '2026-11-20T11:00',
                                         'calendar': 'default', 'to': link['id'], 'guests': 'a@x.org; b@x.org, A@x.org'})
    assert made['success'] and [x['email'] for x in g.named('insert')[0][3]['attendees']] == ['a@x.org', 'b@x.org']
    assert g.named('insert')[0][4] is True
    eid = made['event']['id']
    got = cal.routes.update_event(eid, body={'title': 'Demo', 'add_guests': 'c@x.org', 'remove_guests': ['a@x.org']})
    assert got['success'] and [x['email'] for x in g.named('patch')[0][4]['attendees']] == ['b@x.org', 'c@x.org']
    local = cal.routes.create_event(body={'title': 'Local', 'start': '2026-11-21T10:00', 'calendar': 'default',
                                          'to': 'local', 'guests': 'a@x.org'})
    assert isinstance(local, tuple) and 'Google calendar' in local[0]['detail']


# -- today ------------------------------------------------------------------------

def test_today_marks_what_is_past_and_on_now(cal):
    for title, start, end in (('Early', '2026-11-18T07:00', '2026-11-18T08:00'), ('Now', '2026-11-18T08:00', '2026-11-18T09:00'),
                              ('Later', '2026-11-18T14:00', '2026-11-18T15:30')):
        cal.api.create({'title': title, 'start': start, 'end': end}, 'default')
    cal.api.create({'title': 'Holiday', 'start': '2026-11-18'}, 'default')
    cal.api.create({'title': 'Tomorrow', 'start': '2026-11-19T09:00'}, 'default')
    msg, ok = _run(cal, 'calendar_today')
    lines = msg.splitlines()
    assert ok and lines[0] == "Today (Wednesday November 18), calendar 'default':"
    assert [l.split('  [id')[0] for l in lines[1:5]] == ['  all day  Holiday', '  07:00-08:00  Early [past]',
                                                         '  08:00-09:00  Now [NOW]', '  14:00-15:30  Later']
    assert lines[5] == '4 events, about 3.5 hours scheduled.' and 'Tomorrow' not in msg
    cal.scope.set('work')
    cal.api.add_calendar('work')
    assert _run(cal, 'calendar_today') == ("Today (Wednesday November 18), calendar 'work': nothing scheduled.", True)


# -- the links routes ---------------------------------------------------------------

def test_links_routes(cal):
    _in_new_york(cal)
    cal.use_ics(ICS)
    r = cal.links
    cal.patch.setattr(r, '_account', lambda calendar: {'client_id': '', 'calendar_id': 'primary'})
    first = r.get_links(query={})
    assert first['success'] and first['links'] == [] and first['google'] == {'connected': False, 'calendars': [], 'error': ''}
    added = r.add_link(body={'calendar': 'default', 'kind': 'ics', 'url': 'https://x.example/team.ics', 'name': '  Team   cal '})
    link = added['links'][0]
    assert (link['name'], link['kind'], link['writable'], link['target'], link['refreshing']) == ('Team cal', 'ics', False, False, False)
    assert r.add_link(body={'kind': 'webdav'}) == ({'success': False, 'detail': "kind is 'ics', 'google' or 'caldav'."}, 400)
    assert r.add_link(body={'kind': 'ics', 'url': 'nope'})[1] == 400
    assert r.update_link(link_id=link['id'], body={'name': 'Renamed', 'color': 9})['links'][0]['color'] == 3
    assert r.set_target(body={'calendar': 'default', 'to': link['id']})[1] == 400       # read-only cannot be the target
    seen = cal.routes.list_events(query={'start': '2026-11-20', 'end': '2026-11-20'})
    assert seen['links'][0]['name'] == 'Renamed' and seen['refreshing'] is False
    assert [e['title'] for e in seen['events']] == ['Offsite']
    gone = r.delete_link(link_id=link['id'])
    assert gone['removed_events'] == 6 and gone['links'] == []
    assert r.delete_link(link_id=link['id'])[1] == 400


def test_google_calendars_can_be_picked_and_refreshed_from_the_page(cal):
    g = cal.use_google()
    r = cal.links
    cal.patch.setattr(r, '_account', lambda calendar: {'client_id': '', 'calendar_id': 'primary'})
    cal.api.refresh('default')
    state = r.get_links(query={'calendar': 'default'})
    assert state['target'] == state['links'][0]['id'] and state['links'][0]['target'] is True
    assert [(c['name'], c['linked']) for c in state['google']['calendars']] == [('Me', True), ('Team', False)]
    g.put('team@group', summary='Team lunch', **g.timed('2026-11-19', '12:00', '13:00'))
    state = r.add_link(body={'calendar': 'default', 'kind': 'google', 'google_id': 'team@group'})
    team = state['links'][1]
    assert (team['name'], team['writable']) == ('Team', False) and all(c['linked'] for c in state['google']['calendars'])
    assert r.add_link(body={'calendar': 'default', 'kind': 'google', 'google_id': 'team@group'})[1] == 400
    lunch = cal.api.list_range('2026-11-19', '2026-11-19', 'default')[0]
    assert (lunch['title'], lunch['editable']) == ('Team lunch', False)
    with pytest.raises(cal.api.CalError, match='read-only for this account'):
        cal.api.update(lunch['id'], {'title': 'x'})
    g.down = 'Google is down.'
    out = r.sync_now(body={'calendar': 'default'})
    assert out['success'] and [f['name'] for f in out['failed']] == ['Google', 'Team']
    assert all(l['error'] == 'Google is down.' for l in out['links'])


def test_the_page_refresh_does_not_wait_for_google(cal):
    g = cal.use_google()
    g.put(summary='Dentist', **g.timed('2026-11-19'))
    started = []
    cal.patch.setattr(cal.sync.threading, 'Thread', lambda **kw: SimpleNamespace(start=lambda: started.append(kw)))
    seen = cal.routes.list_events(query={'start': '2026-11-01', 'end': '2026-11-30'})
    assert seen['events'] == [] and seen['refreshing'] is True and len(started) == 1 and g.named('instances') == []
    started[0]['target'](*started[0]['args'])                      # the background pull lands
    seen = cal.routes.list_events(query={'start': '2026-11-01', 'end': '2026-11-30'})
    assert [e['title'] for e in seen['events']] == ['Dentist'] and seen['refreshing'] is False and len(started) == 1


# -- connecting Google (the sign-in routes) --------------------------------------------

@pytest.fixture
def oauth(cal, tmp_path, monkeypatch):
    creds_file, salt_file = tmp_path / 'credentials.json', tmp_path / '.scramble_salt'
    with patch('core.credentials_manager.CREDENTIALS_FILE', creds_file), \
         patch('core.credentials_manager.SCRAMBLE_SALT_FILE', salt_file), \
         patch('core.credentials_manager.CONFIG_DIR', tmp_path):
        from core.credentials_manager import CredentialsManager
        creds = CredentialsManager()
        import plugins.calendar.routes.oauth as mod
        monkeypatch.setattr(mod, '_get_csrf_path', lambda: tmp_path / 'csrf.json')
        monkeypatch.setattr('core.credentials_manager.credentials', creds)
        monkeypatch.setattr(cal.google, 'accounts', lambda: [{'scope': a['scope'], 'calendar_id': a['calendar_id']}
                                                             for a in creds.list_gcal_accounts() if creds.has_gcal_account(a['scope'])])
        posts, answer = [], {'status': 200, 'start': {'auth_url': 'https://accounts.google.com/o/oauth2/v2/auth?fake=1'}}

        def post(url, json=None, data=None, timeout=None):
            posts.append({'url': url, 'json': json, 'data': data})
            body = answer['start'] if url.endswith('/start') else {'access_token': 'at-new', 'refresh_token': 'rt-new', 'expires_in': 3600}
            return _answer(answer['status'], **body)
        monkeypatch.setattr(mod.net, 'post', post)
        yield SimpleNamespace(mod=mod, creds=creds, posts=posts, answer=answer, csrf=tmp_path / 'csrf.json',
                              req=SimpleNamespace(base_url='http://192.168.1.5:8073/'))


def test_easy_connect_goes_through_the_relay_with_pkce(oauth):
    resp = oauth.mod.start_auth(request=oauth.req, query={'scope': 'default'}, settings={})
    assert resp.status_code == 302 and 'accounts.google.com' in resp.headers['location']
    start = oauth.posts[0]
    assert start['url'] == 'https://oauth.sapphireblue.dev/start'
    assert start['json']['return_url'].startswith('http://192.168.1.5:8073/api/plugin/calendar/callback?state=')
    entry = list(json.loads(oauth.csrf.read_text()).values())[0]
    assert entry['mode'] == 'relay' and entry['scope'] == 'default'
    challenge = urlsafe_b64encode(hashlib.sha256(entry['verifier'].encode()).digest()).rstrip(b'=').decode()
    assert start['json']['code_challenge'] == challenge and entry['verifier'] not in json.dumps(start['json'])


def test_a_relay_that_does_not_know_the_new_address_says_so_on_the_page(oauth):
    oauth.answer.update(status=400, start={'error': 'return_url path must be /api/plugin/google-calendar/callback'})
    resp = oauth.mod.start_auth(request=oauth.req, query={}, settings={})
    where = resp.headers['location']
    assert resp.status_code == 302 and where.startswith('/#app-calendar/connect-error/')
    assert 'does%20not%20know%20this%20version' in where and '/' not in where.split('connect-error/')[1]


def test_own_client_goes_straight_to_google(oauth):
    oauth.creds.set_gcal_account('default', 'cid', 'csec')
    resp = oauth.mod.start_auth(request=oauth.req, query={}, settings={})
    where = resp.headers['location']
    assert resp.status_code == 302 and 'client_id=cid' in where and oauth.posts == []
    assert 'redirect_uri=http%3A%2F%2F192.168.1.5%3A8073%2Fapi%2Fplugin%2Fcalendar%2Fcallback' in where


def test_the_callback_claims_the_tokens_and_links_the_calendar(cal, oauth):
    oauth.csrf.write_text(json.dumps({'tok1': {'scope': 'default', 'created': time.time(), 'mode': 'relay',
                                               'verifier': 'v-123', 'relay': 'https://oauth.sapphireblue.dev'}}))
    resp = oauth.mod.handle_callback(query={'state': 'tok1', 'ticket': 'tick-9'})
    assert resp.status_code == 302 and resp.headers['location'] == '/#app-calendar/connected'
    assert oauth.posts[0] == {'url': 'https://oauth.sapphireblue.dev/claim', 'data': None,
                              'json': {'ticket': 'tick-9', 'code_verifier': 'v-123'}}
    acct = oauth.creds.get_gcal_account('default')
    assert (acct['auth_mode'], acct['refresh_token']) == ('relay', 'rt-new') and oauth.creds.has_gcal_account('default')
    assert [(l['kind'], l['ref']) for l in cal.db.links('default')] == [('google', 'primary')]
    assert json.loads(oauth.csrf.read_text()) == {}                 # a sign-in token works once
    again = oauth.mod.handle_callback(query={'state': 'tok1', 'ticket': 'tick-9'})
    assert again.headers['location'].startswith('/#app-calendar/connect-error/')


def test_sign_in_refusals_come_back_to_the_page_in_words(oauth):
    for query in ({'relay_error': 'access_denied', 'state': 'x'}, {'state': 'x'}, {'state': 'nope', 'code': 'c'}):
        resp = oauth.mod.handle_callback(query=query)
        assert resp.status_code == 302 and resp.headers['location'].startswith('/#app-calendar/connect-error/'), query
    assert 'access_denied' in oauth.mod.handle_callback(query={'error': 'access_denied'}).headers['location']


def test_disconnect_clears_the_tokens_and_keeps_the_account(oauth):
    oauth.creds.set_gcal_account('default', '', '', auth_mode='relay', relay_url='https://oauth.sapphireblue.dev')
    oauth.creds.update_gcal_tokens('default', 'rt', 'at', time.time() + 3600)
    assert oauth.mod.get_status(query={'scope': 'default'}) == {'connected': True}
    assert oauth.mod.disconnect(body={'scope': 'default'}) == {'status': 'disconnected'}
    assert oauth.mod.get_status(query={}) == {'connected': False}
    assert oauth.creds.get_gcal_account('default')['auth_mode'] == 'relay'
    assert oauth.mod.disconnect(query={'scope': 'nobody'}) == {'status': 'no_account'}
