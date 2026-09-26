"""Google Calendar guest invites + range numbering (2026-09-26).

- attendees resolve through People with the email whitelist as the ONLY gate
  (names, ids, or one comma/'and' string); misses and ambiguity error with the
  names that would work; people scope None refuses (silent-default invariant)
- calendar_add sends attendees + sendUpdates=all (Google's default is OFF:
  guests listed, nobody emailed); a bad guest never creates a guest-less event
- calendar_range numbers events and writes the delete map (it printed raw ids
  and never wrote the map, while the README promised numbering); an empty
  today-listing clears a stale map
- listings show guest RSVPs, skipping the owner's own row and room resources
"""
import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _load_calendar():
    mod_name = 'gcal_calendar_attendees_test'
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(mod_name, root / 'plugins/google-calendar/tools/calendar.py')
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


PEOPLE = [
    {'id': 1, 'name': 'Rob Smith', 'email': 'rob@example.com', 'email_whitelisted': True},
    {'id': 2, 'name': 'Sam Lee', 'email': 'sam@example.com', 'email_whitelisted': True},
    {'id': 3, 'name': 'Samantha Ortiz', 'email': 'sammy@example.com', 'email_whitelisted': False},
    {'id': 4, 'name': 'No Mail', 'email': '', 'email_whitelisted': True},
    {'id': 5, 'name': 'Bad Mail', 'email': 'not-an-address', 'email_whitelisted': True},
]


@pytest.fixture
def cal(monkeypatch):
    cal = _load_calendar()
    monkeypatch.setattr(cal, '_get_gcal_scope', lambda: 'default')
    monkeypatch.setattr(cal, '_get_people_scope', lambda: 'default')
    import core.contacts as contacts
    monkeypatch.setattr(contacts, 'get_people', lambda scope='default': list(PEOPLE))
    cal._id_maps.clear()
    return cal


@pytest.fixture
def api(cal, monkeypatch):
    """Short-circuit auth and capture whatever goes to Google."""
    monkeypatch.setattr(cal, '_get_access_token', lambda force_refresh=False: ('tok', 'primary', None))
    calls = []

    def fake_request(method, url, headers=None, params=None, json=None, timeout=None):
        calls.append({'method': method, 'url': url, 'params': params, 'json': json})
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {'id': 'gid-new', 'start': {'date': '2026-10-01'}, 'end': {'date': '2026-10-02'}}
        return resp

    monkeypatch.setattr(cal.net, 'request', fake_request)
    return calls


def _ev(gid, summary, day, hour=None, attendees=None):
    e = {'id': gid, 'summary': summary}
    if hour is None:
        e['start'], e['end'] = {'date': day}, {'date': day}
    else:
        e['start'] = {'dateTime': f'{day}T{hour:02d}:00:00-04:00'}
        e['end'] = {'dateTime': f'{day}T{hour + 1:02d}:00:00-04:00'}
    if attendees:
        e['attendees'] = attendees
    return e


# ─── guest resolution ─────────────────────────────────────────────────────

def test_names_resolve_to_attendees(cal):
    out, err = cal._resolve_attendees(['Rob Smith', 'sam lee'])
    assert err is None
    assert out == [{'email': 'rob@example.com', 'displayName': 'Rob Smith'},
                   {'email': 'sam@example.com', 'displayName': 'Sam Lee'}]


def test_unique_partial_and_id_resolve(cal):
    out, err = cal._resolve_attendees(['Rob', '#2'])
    assert err is None
    assert [a['displayName'] for a in out] == ['Rob Smith', 'Sam Lee']


def test_one_string_splits_on_comma_and_and(cal):
    for raw in ('Rob and Sam Lee', 'Rob, Sam Lee', 'Rob & Sam Lee'):
        out, err = cal._resolve_attendees(raw)
        assert err is None and len(out) == 2, raw


def test_json_encoded_string_array_accepted(cal):
    """Sapphire's GLM run passed attendees as the STRING '["Zzyzx Nobody"]'
    (live, 2026-09-26) — decode it instead of hunting for a contact literally
    named that."""
    out, err = cal._resolve_attendees('["Rob Smith", "Sam Lee"]')
    assert err is None and [a['displayName'] for a in out] == ['Rob Smith', 'Sam Lee']
    out, err = cal._resolve_attendees('[not json')
    assert out is None and "No contact named" in err  # falls through, no crash


def test_ambiguous_partial_names_candidates(cal):
    out, err = cal._resolve_attendees(['Sam'])
    assert out is None
    assert 'Sam Lee' in err and 'Samantha Ortiz' in err


def test_unknown_name_lists_invitable_only(cal):
    out, err = cal._resolve_attendees(['Zed'])
    assert out is None
    assert "No contact named 'Zed'" in err
    assert 'Rob Smith' in err and 'Samantha Ortiz' not in err  # whitelisted + address only


def test_not_whitelisted_refused(cal):
    out, err = cal._resolve_attendees(['Samantha Ortiz'])
    assert out is None and 'Allow AI to send email' in err


def test_missing_or_invalid_email_refused(cal):
    assert cal._resolve_attendees(['No Mail'])[1].startswith('No Mail has no email')
    assert "isn't a valid address" in cal._resolve_attendees(['Bad Mail'])[1]


def test_people_scope_none_refuses(cal, monkeypatch):
    """[REGRESSION_GUARD] silent-default class: a disabled people scope must
    refuse, never resolve guests against 'default'."""
    monkeypatch.setattr(cal, '_get_people_scope', lambda: None)
    out, err = cal._resolve_attendees(['Rob Smith'])
    assert out is None and 'disabled' in err


def test_empty_attendees_is_noop(cal):
    for raw in (None, [], ''):
        assert cal._resolve_attendees(raw) == ([], None)


def test_duplicate_guest_collapses(cal):
    out, err = cal._resolve_attendees(['Rob Smith', 'rob smith', '1'])
    assert err is None and len(out) == 1


# ─── calendar_add ─────────────────────────────────────────────────────────

def test_add_with_guests_sends_attendees_and_send_updates(cal, api):
    out, ok = cal.execute('calendar_add',
                          {'title': 'Lunch', 'start': '2026-10-01', 'attendees': ['Rob', 'Sam Lee']}, {})
    assert ok, out
    post = api[0]
    assert post['method'] == 'POST'
    assert post['params'] == {'sendUpdates': 'all'}
    assert post['json']['attendees'] == [
        {'email': 'rob@example.com', 'displayName': 'Rob Smith'},
        {'email': 'sam@example.com', 'displayName': 'Sam Lee'}]
    assert 'Invited' in out and 'Rob Smith' in out and 'Sam Lee' in out


def test_add_without_guests_unchanged(cal, api):
    """[REGRESSION_GUARD] no attendees -> no attendees key, no sendUpdates."""
    out, ok = cal.execute('calendar_add', {'title': 'Solo', 'start': '2026-10-01'}, {})
    assert ok, out
    assert 'attendees' not in api[0]['json']
    assert api[0]['params'] is None
    assert 'Invited' not in out


def test_bad_guest_creates_nothing(cal, api):
    out, ok = cal.execute('calendar_add',
                          {'title': 'Lunch', 'start': '2026-10-01', 'attendees': ['Samantha Ortiz']}, {})
    assert not ok and 'Allow AI to send email' in out
    assert api == []  # no guest-less event left behind


def test_api_post_forwards_params(cal, monkeypatch):
    seen = {}
    monkeypatch.setattr(cal, '_api_call',
                        lambda m, t, params=None, body=None: seen.update(m=m, params=params, body=body) or (True, None))
    cal._api_post('/x', {'a': 1}, params={'sendUpdates': 'all'})
    assert seen == {'m': 'POST', 'params': {'sendUpdates': 'all'}, 'body': {'a': 1}}


# ─── listings: numbering + RSVPs ──────────────────────────────────────────

def test_range_numbers_events_and_feeds_delete(cal, api, monkeypatch):
    """[REGRESSION_GUARD] range used to print raw ids and never write the
    delete map, so 'delete #2 from next week' 404'd at Google."""
    events = [_ev('gid-a', 'Dentist', '2026-10-01', 9),
              _ev('gid-b', 'Lunch', '2026-10-02'),
              _ev('gid-c', 'Gym', '2026-10-02', 18)]
    monkeypatch.setattr(cal, '_api_get', lambda tpl, params=None: ({'items': events}, None))
    out, ok = cal.execute('calendar_range', {'start_date': '2026-10-01', 'end_date': '2026-10-03'}, {})
    assert ok, out
    assert '#1  9:00 AM' in out and '#2  All day  Lunch' in out and '#3' in out
    assert '(id:' not in out
    assert '3 events total' in out
    assert cal._resolve_event_id('2') == ('gid-b', None)
    assert cal._resolve_event_id('#3') == ('gid-c', None)


def test_empty_today_clears_stale_map(cal):
    cal._format_events([_ev('gid-old', 'Old', '2026-09-01')], 'Yesterday')
    assert cal._resolve_event_id('1') == ('gid-old', None)
    cal._format_events([], 'Today')
    _, err = cal._resolve_event_id('1')
    assert err and 'calendar_today' in err


def test_listing_shows_guest_rsvps_skipping_self_and_rooms(cal):
    ev = _ev('gid-g', 'Planning', '2026-10-01', 10, attendees=[
        {'email': 'me@example.com', 'self': True, 'organizer': True, 'responseStatus': 'accepted'},
        {'email': 'rob@example.com', 'displayName': 'Rob Smith', 'responseStatus': 'accepted'},
        {'email': 'sam@example.com', 'responseStatus': 'needsAction'},
        {'email': 'room@resource.calendar.google.com', 'resource': True, 'responseStatus': 'accepted'},
    ])
    out = cal._format_events([ev], 'Today')
    assert '[guests: Rob Smith (yes), sam@example.com (pending)]' in out
    assert 'me@example.com' not in out and 'room@' not in out
