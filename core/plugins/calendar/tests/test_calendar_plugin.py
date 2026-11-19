"""Calendar plugin: the store, repeat expansion, the shared operations, her
tools (scope off, private chat, id addressing) and the page's routes.
Every test runs on a database in tmp_path; the real one is never opened."""
import sqlite3
from datetime import date, datetime

import pytest


TODAY = date(2026, 11, 18)          # a Wednesday; the fixture `cal` (conftest.py) pins the clock to it


def _days(hits):
    return [h.isoformat()[:10] for h in hits]


# -- repeat rules -------------------------------------------------------------

def test_rule_round_trip_and_words(cal):
    r = cal.repeat
    rule = r.build('weekly', 2, None, date(2027, 1, 1), [2, 0])
    assert rule == 'FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE;UNTIL=20270101'
    assert r.parse(rule) == {'freq': 'WEEKLY', 'interval': 2, 'count': None, 'until': date(2027, 1, 1), 'byday': [0, 2]}
    assert r.describe(rule) == 'every 2 weeks on Mon, Wed until 2027-01-01'
    assert r.describe('FREQ=DAILY;COUNT=3') == 'daily, 3 times'
    assert r.parse('') is None and r.describe('') == ''


def test_rule_outside_the_subset_is_refused(cal):
    for bad in ('FREQ=HOURLY', 'FREQ=MONTHLY;BYDAY=1MO', 'FREQ=WEEKLY;BYSETPOS=1', 'INTERVAL=2'):
        with pytest.raises(ValueError):
            cal.repeat.parse(bad)
    with pytest.raises(ValueError):
        cal.repeat.build('fortnightly')
    with pytest.raises(ValueError):
        cal.repeat.build('daily', 0)


def test_daily_interval_and_count(cal):
    ex = cal.repeat.expand
    assert _days(ex(date(2026, 11, 18), 'FREQ=DAILY;INTERVAL=3', date(2026, 11, 18), date(2026, 11, 30))) == \
        ['2026-11-18', '2026-11-21', '2026-11-24', '2026-11-27', '2026-11-30']
    assert _days(ex(date(2026, 11, 18), 'FREQ=DAILY;COUNT=3', date(2026, 11, 1), date(2026, 12, 31))) == \
        ['2026-11-18', '2026-11-19', '2026-11-20']
    # far ahead, no count: the jump lands on the same grid as counting from the start
    far = ex(date(2020, 1, 1), 'FREQ=DAILY;INTERVAL=7', date(2026, 11, 16), date(2026, 11, 29))
    assert _days(far) == ['2026-11-18', '2026-11-25']
    assert all((d - date(2020, 1, 1)).days % 7 == 0 for d in far)


def test_weekly_days_until_and_timed(cal):
    ex = cal.repeat.expand
    start = datetime(2026, 11, 18, 9, 30)                      # Wed
    hits = ex(start, 'FREQ=WEEKLY;BYDAY=MO,WE,FR;UNTIL=20261127', date(2026, 11, 1), date(2026, 12, 31))
    assert [h.isoformat() for h in hits] == ['2026-11-18T09:30:00', '2026-11-20T09:30:00', '2026-11-23T09:30:00',
                                              '2026-11-25T09:30:00', '2026-11-27T09:30:00']
    every_other = ex(date(2026, 11, 18), 'FREQ=WEEKLY;INTERVAL=2', date(2026, 11, 18), date(2026, 12, 31))
    assert _days(every_other) == ['2026-11-18', '2026-12-02', '2026-12-16', '2026-12-30']
    # count is counted from the first occurrence even when the range starts later
    counted = ex(date(2026, 11, 18), 'FREQ=WEEKLY;COUNT=3', date(2026, 12, 1), date(2027, 3, 1))
    assert _days(counted) == ['2026-12-02']


def test_month_end_skips_short_months(cal):
    hits = cal.repeat.expand(date(2026, 1, 31), 'FREQ=MONTHLY', date(2026, 1, 1), date(2026, 12, 31))
    assert _days(hits) == ['2026-01-31', '2026-03-31', '2026-05-31', '2026-07-31', '2026-08-31', '2026-10-31', '2026-12-31']
    quarterly = cal.repeat.expand(date(2026, 11, 30), 'FREQ=MONTHLY;INTERVAL=3', date(2026, 11, 1), date(2027, 12, 31))
    assert _days(quarterly) == ['2026-11-30', '2027-05-30', '2027-08-30', '2027-11-30']       # no Feb 30
    across_years = cal.repeat.expand(date(2025, 6, 15), 'FREQ=MONTHLY', date(2026, 11, 1), date(2027, 1, 31))
    assert _days(across_years) == ['2026-11-15', '2026-12-15', '2027-01-15']


def test_yearly_and_leap_day(cal):
    ex = cal.repeat.expand
    assert _days(ex(date(1990, 11, 18), 'FREQ=YEARLY', date(2026, 1, 1), date(2028, 12, 31))) == \
        ['2026-11-18', '2027-11-18', '2028-11-18']
    assert _days(ex(date(2024, 2, 29), 'FREQ=YEARLY', date(2024, 1, 1), date(2033, 1, 1))) == \
        ['2024-02-29', '2028-02-29', '2032-02-29']


def test_exceptions_are_taken_out_after_counting(cal):
    hits = cal.repeat.expand(date(2026, 11, 18), 'FREQ=DAILY;COUNT=4', date(2026, 11, 1), date(2026, 12, 1),
                             exdates=['2026-11-19'])
    assert _days(hits) == ['2026-11-18', '2026-11-20', '2026-11-21']


def test_one_off_event_and_range_edges(cal):
    ex = cal.repeat.expand
    assert _days(ex(date(2026, 11, 18), '', date(2026, 11, 18), date(2026, 11, 18))) == ['2026-11-18']
    assert ex(date(2026, 11, 18), '', date(2026, 11, 19), date(2026, 11, 30)) == []
    assert ex(date(2026, 11, 18), 'FREQ=DAILY', date(2026, 11, 1), date(2026, 11, 17)) == []
    assert ex(date(2026, 11, 18), 'FREQ=DAILY;UNTIL=20261120', date(2026, 11, 21), date(2026, 11, 30)) == []


# -- store --------------------------------------------------------------------

def test_store_crud_and_stable_ids(cal):
    db = cal.db
    assert db.path.parent == cal.tmp
    a = db.insert({'calendar': 'default', 'title': 'Dentist', 'start': '2026-11-18T09:00', 'end': '2026-11-18T10:00'})
    b = db.insert({'calendar': 'default', 'title': 'Trip', 'start': '2026-11-20', 'end': '2026-11-22', 'all_day': True})
    assert a['id'] != b['id'] and len(a['id']) == 6 and a['id'][0].isalpha()
    assert a['uid'].endswith('@sapphire') and a['source'] == 'local' and a['exdates'] == [] and a['all_day'] is False
    assert b['all_day'] is True
    changed = db.update(a['id'], {'title': 'Dentist (moved)', 'exdates': ['2026-11-25', '2026-11-25']})
    assert changed['id'] == a['id'] and changed['title'] == 'Dentist (moved)' and changed['exdates'] == ['2026-11-25']
    assert changed['updated_at'] >= a['updated_at'] and changed['created_at'] == a['created_at']
    assert db.update('nosuch', {'title': 'x'}) is None
    assert db.delete(a['id']) is True and db.delete(a['id']) is False and db.get(a['id']) is None
    assert db.get(b['id'])['title'] == 'Trip'


def test_store_survives_reopen_and_uses_one_connection(cal):
    db = cal.db
    row = db.insert({'calendar': 'default', 'title': 'Kept', 'start': '2026-11-18', 'end': '2026-11-18', 'all_day': True})
    conn = db._conn
    db.get(row['id'])
    db.calendars()
    assert db._conn is conn                      # nothing opens or closes per call
    db.close()
    assert db._conn is None
    assert db.get(row['id'])['title'] == 'Kept'  # reopened from the file
    raw = sqlite3.connect(str(db.path))
    try:
        cols = {r[1] for r in raw.execute("PRAGMA table_info(events)")}
    finally:
        raw.close()
    assert {'uid', 'source', 'source_ref', 'start', 'end', 'all_day', 'rrule', 'exdates', 'title', 'location', 'notes'} <= cols


def test_candidates_pick_overlaps_and_started_series(cal):
    db = cal.db
    for title, start, end, rule in (('before', '2026-11-01', '2026-11-02', ''),
                                    ('spans in', '2026-11-08', '2026-11-10', ''),
                                    ('inside', '2026-11-12T09:00', '2026-11-12T10:00', ''),
                                    ('after', '2026-11-20', '2026-11-20', ''),
                                    ('old series', '2020-01-01', '2020-01-01', 'FREQ=YEARLY'),
                                    ('future series', '2027-01-01', '2027-01-01', 'FREQ=DAILY')):
        db.insert({'calendar': 'default', 'title': title, 'start': start, 'end': end, 'rrule': rule,
                   'all_day': 'T' not in start})
    db.insert({'calendar': 'work', 'title': 'other calendar', 'start': '2026-11-12', 'end': '2026-11-12', 'all_day': True})
    got = {e['title'] for e in db.candidates('2026-11-10', '2026-11-15')}
    assert got == {'spans in', 'inside', 'old series', 'other calendar'}
    assert {e['title'] for e in db.candidates('2026-11-10', '2026-11-15', 'work')} == {'other calendar'}


def test_calendars_default_always_there_and_delete_takes_events(cal):
    db = cal.db
    assert db.calendars() == [{'name': 'default', 'events': 0}]
    db.add_calendar('work')
    db.insert({'calendar': 'work', 'title': 'a', 'start': '2026-11-18', 'end': '2026-11-18', 'all_day': True})
    db.insert({'calendar': 'default', 'title': 'b', 'start': '2026-11-18', 'end': '2026-11-18', 'all_day': True})
    assert db.calendars() == [{'name': 'default', 'events': 1}, {'name': 'work', 'events': 1}]
    assert db.delete_calendar('work') == 1
    assert db.calendars() == [{'name': 'default', 'events': 1}]


def test_module_store_is_the_test_one_and_shutdown_lets_go(cal):
    assert cal.store.get() is cal.db
    cal.store.get().insert({'calendar': 'default', 'title': 'x', 'start': '2026-11-18', 'end': '2026-11-18', 'all_day': True})
    cal.store.shutdown()
    assert cal.db._conn is None and cal.store._store is None


# -- the shared operations ----------------------------------------------------

def test_create_defaults_and_shapes(cal):
    api = cal.api
    timed = api.create({'title': ' Dentist ', 'start': '2026-11-20 09:00'})
    assert (timed['title'], timed['start'], timed['end'], timed['all_day']) == ('Dentist', '2026-11-20T09:00', '2026-11-20T10:00', False)
    short = api.create({'title': 'Call', 'start': '2026-11-20T14:00', 'end': '14:20'})
    assert short['end'] == '2026-11-20T14:20'
    allday = api.create({'title': 'Trip', 'start': '2026-11-20', 'end': '2026-11-22'})
    assert (allday['start'], allday['end'], allday['all_day']) == ('2026-11-20', '2026-11-22', True)
    one_day = api.create({'title': 'Birthday', 'start': '2026-11-20'})
    assert one_day['end'] == '2026-11-20' and one_day['created_by'] == 'user'
    assert api.create({'title': 'Tomorrow thing', 'start': 'tomorrow'})['start'] == '2026-11-19'


def test_time_with_an_offset_becomes_local_wall_time(cal, monkeypatch):
    monkeypatch.setattr(cal.api, 'tz_name', lambda: 'America/New_York')
    ev = cal.api.create({'title': 'Sync', 'start': '2026-11-20T15:00:00Z'})
    assert ev['start'] == '2026-11-20T10:00' and ev['tz'] == 'America/New_York'
    ev = cal.api.create({'title': 'Sync 2', 'start': '2026-11-20T15:00:00-05:00'})
    assert ev['start'] == '2026-11-20T15:00'


@pytest.mark.parametrize('fields, words', [
    ({'start': '2026-11-20 09:00'}, 'needs a title'),
    ({'title': 'x'}, 'needs a start'),
    ({'title': 'x', 'start': 'next friday'}, 'must be YYYY-MM-DD'),
    ({'title': 'x', 'start': '2026-02-30'}, 'not a real date'),
    ({'title': 'x', 'start': '2026-11-20 09:00', 'end': '2026-11-20 08:00'}, 'end must be after start'),
    ({'title': 'x', 'start': '2026-11-20', 'end': '2026-11-19'}, 'end is before start'),
    ({'title': 'x', 'start': '2026-11-20', 'all_day': False}, 'needs a start time'),
    ({'title': 'x', 'start': '2026-11-20', 'repeat': 'hourly'}, 'repeat must be'),
    ({'title': 'x', 'start': '2026-11-20', 'repeat': 'daily', 'until': '2026-11-01'}, 'until is before'),
    ({'title': 'x', 'start': '2026-11-20', 'repeat': 'daily', 'times': 5000}, 'times must be between'),
    ({'title': 'x', 'start': '2026-11-19', 'repeat': 'weekly', 'days': 'mon,wed'}, 'is a Thu'),
    ({'title': 'x', 'start': '2026-11-20', 'calendar': 'nope'}, "no calendar named 'nope'"),
])
def test_bad_events_are_refused_in_words(cal, fields, words):
    with pytest.raises(cal.api.CalError) as e:
        cal.api.create(fields)
    assert words in str(e.value)
    assert cal.db.candidates('2000-01-01', '2100-01-01') == []


def test_list_range_expands_sorts_and_spans(cal):
    api = cal.api
    api.create({'title': 'Standup', 'start': '2026-11-16 09:00', 'end': '09:15', 'repeat': 'weekly', 'days': 'mon,wed,fri'})
    api.create({'title': 'Trip', 'start': '2026-11-17', 'end': '2026-11-19'})
    api.create({'title': 'Lunch', 'start': '2026-11-18 12:00'})
    api.create({'title': 'Overnight', 'start': '2026-11-17 22:00', 'end': '2026-11-18 02:00', 'repeat': 'daily', 'times': 2})
    got = [(o['start'], o['title']) for o in api.list_range('2026-11-18', '2026-11-18')]
    assert got == [('2026-11-17', 'Trip'), ('2026-11-17T22:00', 'Overnight'),
                   ('2026-11-18T09:00', 'Standup'), ('2026-11-18T12:00', 'Lunch'), ('2026-11-18T22:00', 'Overnight')]
    week = api.list_range('2026-11-16', '2026-11-22')
    assert [o['start'][:10] for o in week if o['title'] == 'Standup'] == ['2026-11-16', '2026-11-18', '2026-11-20']
    standup = next(o for o in week if o['title'] == 'Standup')
    assert standup['repeats'] and standup['repeat_text'] == 'weekly on Mon, Wed, Fri'
    assert standup['rule'] == {'repeat': 'weekly', 'every': 1, 'days': [0, 2, 4], 'times': None, 'until': None}
    assert standup['end'] == standup['start'][:10] + 'T09:15' and standup['day'] == standup['start'][:10]
    assert [o['title'] for o in api.list_range('2026-11-16', '2026-11-22', query='lun')] == ['Lunch']
    with pytest.raises(api.CalError):
        api.list_range('2026-11-18', '2026-11-17')
    with pytest.raises(api.CalError):
        api.list_range('2026-01-01', '2030-01-01')


def test_update_keeps_what_was_not_sent(cal):
    api = cal.api
    ev = api.create({'title': 'Dentist', 'start': '2026-11-20 09:00', 'end': '2026-11-20 09:45', 'location': 'Main St',
                     'notes': 'bring card', 'repeat': 'monthly', 'times': 6})
    moved = api.update(ev['id'], {'start': '2026-11-21 14:00'})
    assert (moved['start'], moved['end']) == ('2026-11-21T14:00', '2026-11-21T14:45')        # length kept
    assert (moved['title'], moved['location'], moved['notes'], moved['rrule']) == ('Dentist', 'Main St', 'bring card', 'FREQ=MONTHLY;COUNT=6')
    assert moved['id'] == ev['id']
    cleared = api.update(ev['id'], {'location': '', 'repeat': 'none'})
    assert cleared['location'] == '' and cleared['rrule'] == '' and cleared['notes'] == 'bring card'
    allday = api.update(ev['id'], {'start': '2026-11-25'})
    assert (allday['start'], allday['end'], allday['all_day']) == ('2026-11-25', '2026-11-25', True)
    with pytest.raises(api.CalError):
        api.update('nosuch', {'title': 'x'})


def test_update_repeat_fields_merge_with_the_old_rule(cal):
    api = cal.api
    ev = api.create({'title': 'Gym', 'start': '2026-11-16 18:00', 'repeat': 'weekly', 'days': 'mon,thu', 'until': '2027-06-01'})
    assert ev['rrule'] == 'FREQ=WEEKLY;BYDAY=MO,TH;UNTIL=20270601'
    assert api.update(ev['id'], {'every': 2})['rrule'] == 'FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,TH;UNTIL=20270601'
    assert api.update(ev['id'], {'times': 10})['rrule'] == 'FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,TH;COUNT=10'
    assert api.update(ev['id'], {'repeat': 'daily'})['rrule'] == 'FREQ=DAILY'
    with pytest.raises(api.CalError) as e:
        api.update(api.create({'title': 'One off', 'start': '2026-11-20'})['id'], {'every': 2})
    assert 'Say how it repeats' in str(e.value)


def test_moving_a_weekly_event_off_its_days_is_refused(cal):
    api = cal.api
    ev = api.create({'title': 'Gym', 'start': '2026-11-16 18:00', 'repeat': 'weekly', 'days': 'mon,thu'})
    with pytest.raises(api.CalError) as e:
        api.update(ev['id'], {'start': '2026-11-17 18:00'})
    assert 'is a Tue' in str(e.value)
    assert cal.db.get(ev['id'])['start'] == '2026-11-16T18:00'
    assert api.update(ev['id'], {'start': '2026-11-19 19:00'})['start'] == '2026-11-19T19:00'


def test_remove_one_occurrence_or_the_series(cal):
    api = cal.api
    ev = api.create({'title': 'Standup', 'start': '2026-11-16 09:00', 'repeat': 'daily'})
    kind, _ = api.remove(ev['id'], '2026-11-18')
    assert kind == 'occurrence'
    assert [o['day'] for o in api.list_range('2026-11-16', '2026-11-20')] == ['2026-11-16', '2026-11-17', '2026-11-19', '2026-11-20']
    with pytest.raises(api.CalError) as e:
        api.remove(ev['id'], '2026-11-18')                    # already gone
    assert 'no occurrence' in str(e.value)
    with pytest.raises(api.CalError):
        api.remove(ev['id'], '2026-11-01')                    # before it starts
    # a change of rule or start day starts a new series: old exceptions go
    assert api.update(ev['id'], {'title': 'Daily standup'})['exdates'] == ['2026-11-18']
    assert api.update(ev['id'], {'every': 2})['exdates'] == []
    kind, _ = api.remove(ev['id'])
    assert kind == 'event' and api.list_range('2026-11-01', '2026-12-31') == []
    one = api.create({'title': 'One off', 'start': '2026-11-20'})
    with pytest.raises(api.CalError) as e:
        api.remove(one['id'], '2026-11-20')
    assert 'does not repeat' in str(e.value)


def test_a_full_resave_from_the_page_keeps_the_exceptions(cal):
    """The form sends every field back. The same rule in another spelling is
    still the same series, so a title edit must not bring removed days back."""
    api = cal.api
    ev = api.create({'title': 'Class', 'start': '2026-11-18 18:00', 'repeat': 'weekly'})      # no days given
    assert ev['rrule'] == 'FREQ=WEEKLY'
    api.remove(ev['id'], '2026-11-25')
    page_fields = {'title': 'Class (room 2)', 'all_day': False, 'start': '2026-11-18T18:00', 'end': '2026-11-18T19:00',
                   'location': '', 'notes': '', 'repeat': 'weekly', 'every': 1, 'days': [2], 'until': '', 'times': ''}
    saved = api.update(ev['id'], page_fields)
    assert saved['title'] == 'Class (room 2)' and saved['rrule'] == 'FREQ=WEEKLY' and saved['exdates'] == ['2026-11-25']
    assert [o['day'] for o in api.list_range('2026-11-18', '2026-12-02')] == ['2026-11-18', '2026-12-02']
    # an old row spelled the long way is the same series too
    cal.db.update(ev['id'], {'rrule': 'RRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=WE'})
    assert api.update(ev['id'], page_fields)['exdates'] == ['2026-11-25']
    # a real change of rule starts a new series
    assert api.update(ev['id'], {**page_fields, 'days': [2, 4]})['exdates'] == []


def test_calendar_names(cal):
    api = cal.api
    assert api.add_calendar(' Work Stuff ') == 'work-stuff'
    for bad in ('', 'none', 'a' * 40, 'has/slash', '../x'):
        with pytest.raises(api.CalError):
            api.add_calendar(bad)
    with pytest.raises(api.CalError):
        api.delete_calendar('default')
    with pytest.raises(api.CalError):
        api.delete_calendar('ghost')
    api.create({'title': 'x', 'start': '2026-11-20'}, calendar='work-stuff')
    assert api.delete_calendar('work-stuff') == 1


# -- her tools ----------------------------------------------------------------

def _run(cal, name, **args):
    return cal.tools.execute(name, args, None)


def test_tools_keep_the_google_plugins_names_so_saved_toolsets_still_work(cal):
    names = {t['function']['name'] for t in cal.tools.TOOLS}
    assert names == set(cal.tools.AVAILABLE_FUNCTIONS)
    assert names == {'calendar_today', 'calendar_range', 'calendar_add', 'calendar_update', 'calendar_delete'}
    # local so a private chat may read; the tool itself refuses to write or fetch there
    assert all(t['is_local'] is True and t['network'] is True for t in cal.tools.TOOLS)
    assert {t['function']['name'] for t in cal.tools.TOOLS if t.get('writes')} == names - {'calendar_today', 'calendar_range'}


@pytest.mark.parametrize('off', [None, 'none', ''])
def test_every_tool_refuses_when_the_scope_is_off(cal, off):
    ev = cal.api.create({'title': 'Dentist', 'start': '2026-11-20 09:00'})
    cal.scope.set(off)
    for name, args in (('calendar_range', {}), ('calendar_add', {'title': 'x', 'start': '2026-11-20'}),
                       ('calendar_update', {'id': ev['id'], 'title': 'y'}), ('calendar_delete', {'id': ev['id']})):
        msg, ok = _run(cal, name, **args)
        assert ok is False and msg == 'The calendar is off for this chat.'
    assert [e['title'] for e in cal.db.candidates('2000-01-01', '2100-01-01')] == ['Dentist']


def test_scope_that_cannot_be_read_is_off_not_default(cal, monkeypatch):
    class Broken:
        def get(self):
            raise LookupError('no scope')
    import core.chat.function_manager as fm
    monkeypatch.setitem(fm.SCOPE_REGISTRY['gcal'], 'var', Broken())
    assert cal.tools._scope() is None
    msg, ok = _run(cal, 'calendar_range')
    assert ok is False and 'off for this chat' in msg


def test_scope_naming_a_calendar_that_is_gone_refuses(cal):
    cal.scope.set('work')
    msg, ok = _run(cal, 'calendar_add', title='x', start='2026-11-20')
    assert ok is False and "no calendar named 'work'" in msg
    assert cal.db.candidates('2000-01-01', '2100-01-01') == []


def test_tools_create_list_update_remove_by_id(cal):
    msg, ok = _run(cal, 'calendar_add', title='Dentist', start='2026-11-20 09:00', location='Main St')
    assert ok, msg
    assert msg.startswith("Added 'Dentist': Fri Nov 20 09:00-10:00.") and '[id ' in msg
    event_id = msg.split('[id ')[1].rstrip(']')
    assert cal.db.get(event_id)['created_by'] == 'ai'

    _run(cal, 'calendar_add', title='Standup', start='2026-11-18 09:00', end='09:15', repeat='weekly', days='mon,wed,fri')
    _run(cal, 'calendar_add', title='Far away', start='2027-03-01')
    msg, ok = _run(cal, 'calendar_range')
    assert ok, msg
    assert "Calendar 'default', Wed Nov 18 to Wed Nov 25: 5 events." in msg     # Standup x4 + Dentist
    assert 'Wed Nov 18 (today)' in msg and f'  09:00-10:00  Dentist @ Main St  [id {event_id}]' in msg
    assert 'Standup (weekly on Mon, Wed, Fri)' in msg and 'Far away' not in msg
    assert msg.index('Standup') < msg.index('Dentist')        # in time order

    # a list in a different order does not change what an id means
    _run(cal, 'calendar_add', title='Earlier', start='2026-11-18 07:00')
    msg, ok = _run(cal, 'calendar_update', id=event_id, start='2026-11-21 14:00', notes='bring card')
    assert ok and msg == f"Updated 'Dentist': Sat Nov 21 14:00-15:00. [id {event_id}]"
    row = cal.db.get(event_id)
    assert (row['start'], row['location'], row['notes']) == ('2026-11-21T14:00', 'Main St', 'bring card')

    msg, ok = _run(cal, 'calendar_range', start='2026-11-21', days=0, query='dent')
    assert ok and 'bring card' in msg and '1 event.' in msg

    msg, ok = _run(cal, 'calendar_delete', id=event_id)
    assert ok and msg == "Deleted 'Dentist'."
    assert cal.db.get(event_id) is None
    msg, ok = _run(cal, 'calendar_delete', id=event_id)
    assert ok is False and 'no event with id' in msg


def test_tool_removes_one_occurrence_by_date(cal):
    msg, _ = _run(cal, 'calendar_add', title='Standup', start='2026-11-18 09:00', repeat='daily')
    event_id = msg.split('[id ')[1].rstrip(']')
    msg, ok = _run(cal, 'calendar_delete', id=event_id, date='2026-11-19')
    assert ok and msg == "Deleted the Thu Nov 19 occurrence of 'Standup'. The rest of the series stays."
    listing, _ = _run(cal, 'calendar_range', days=2)
    assert 'Wed Nov 18' in listing and '\nThu Nov 19' not in listing and 'Fri Nov 20' in listing
    assert 'Standup (daily, not Thu Nov 19)' in listing               # the removed day is named, not just counted
    msg, ok = _run(cal, 'calendar_delete', id=event_id)
    assert ok and msg == "Deleted 'Standup' and all its repeats."


def test_tools_cannot_reach_another_calendars_event(cal):
    cal.api.add_calendar('work')
    other = cal.api.create({'title': 'Board meeting', 'start': '2026-11-20 09:00'}, calendar='work')
    for name, args in (('calendar_update', {'id': other['id'], 'title': 'hijacked'}), ('calendar_delete', {'id': other['id']})):
        msg, ok = _run(cal, name, **args)
        assert ok is False and 'no event with id' in msg
    listing, ok = _run(cal, 'calendar_range')
    assert ok and 'Board meeting' not in listing and 'nothing' in listing
    assert cal.db.get(other['id'])['title'] == 'Board meeting'
    cal.scope.set('work')
    assert 'Board meeting' in _run(cal, 'calendar_range')[0]


def test_private_chat_reads_but_does_not_write(cal):
    ev = cal.api.create({'title': 'Dentist', 'start': '2026-11-20 09:00'})
    cal.private.set(True)
    msg, ok = _run(cal, 'calendar_range')
    assert ok and 'Dentist' in msg
    for name, args in (('calendar_add', {'title': 'secret', 'start': '2026-11-20'}),
                       ('calendar_update', {'id': ev['id'], 'title': 'secret'}), ('calendar_delete', {'id': ev['id']})):
        msg, ok = _run(cal, name, **args)
        assert ok is False and 'private' in msg
    assert [e['title'] for e in cal.db.candidates('2000-01-01', '2100-01-01')] == ['Dentist']


def test_tool_argument_problems_come_back_as_words(cal):
    assert _run(cal, 'calendar_update', title='x') == ('id is required (from calendar_today or calendar_range).', False)
    ev = cal.api.create({'title': 'Dentist', 'start': '2026-11-20 09:00'})
    msg, ok = _run(cal, 'calendar_update', id=ev['id'])
    assert ok is False and 'Nothing to change' in msg
    msg, ok = _run(cal, 'calendar_add', title='x', start='friday at 3')
    assert ok is False and 'YYYY-MM-DD' in msg
    msg, ok = _run(cal, 'calendar_range', start='2026-01-01', end='2028-01-01')
    assert ok is False and '366 days' in msg
    msg, ok = _run(cal, 'calendar_nope')
    assert ok is False and 'Unknown calendar function' in msg


# -- the page's routes ----------------------------------------------------------

def test_routes_event_round_trip(cal):
    r = cal.routes
    made = r.create_event(body={'title': 'Dentist', 'start': '2026-11-20T09:00', 'end': '2026-11-20T09:30',
                                'all_day': False, 'repeat': 'weekly', 'every': 1, 'days': [4], 'until': '', 'times': ''})
    assert made['success'] and made['event']['repeat_text'] == 'weekly' and made['event']['by'] == 'user'
    assert made['event']['rule']['days'] == []                # its own weekday needs no BYDAY
    event_id = made['event']['id']

    got = r.list_events(query={'start': '2026-11-01', 'end': '2026-11-30'}, settings={'week_start': 'monday'})
    assert [e['start'] for e in got['events']] == ['2026-11-20T09:00', '2026-11-27T09:00']
    assert got['today'] == '2026-11-18' and got['week_start'] == 'monday' and got['timezone'] == ''
    assert got['calendars'] == [{'name': 'default', 'events': 1, 'label': 'default (1 event)'}]
    assert r.list_events(query={}, settings=None)['week_start'] == 'sunday'

    changed = r.update_event(event_id=event_id, body={'title': 'Dentist!', 'start': '2026-11-20T10:00', 'end': '2026-11-20T10:30',
                                                      'all_day': False, 'repeat': 'none'})
    assert changed['event']['title'] == 'Dentist!' and changed['event']['repeats'] is False
    assert r.delete_event(event_id=event_id, body={}) == {'success': True, 'removed': 'event'}
    assert r.list_events(query={'start': '2026-11-01', 'end': '2026-11-30'})['events'] == []


def test_routes_delete_one_occurrence_and_refusals(cal):
    r = cal.routes
    event_id = r.create_event(body={'title': 'Standup', 'start': '2026-11-16T09:00', 'repeat': 'daily'})['event']['id']
    assert r.delete_event(event_id=event_id, body={'day': '2026-11-17'}) == {'success': True, 'removed': 'occurrence'}
    days = [e['day'] for e in r.list_events(query={'start': '2026-11-16', 'end': '2026-11-18'})['events']]
    assert days == ['2026-11-16', '2026-11-18']
    body, status = r.create_event(body={'title': '', 'start': '2026-11-16'})
    assert status == 400 and body == {'success': False, 'detail': 'An event needs a title.'}
    body, status = r.update_event(event_id='nosuch', body={'title': 'x'})
    assert status == 400 and 'no event with id' in body['detail']
    body, status = r.delete_event(event_id=event_id, body={'day': '2026-11-17'})
    assert status == 400 and 'no occurrence' in body['detail']
    body, status = r.list_events(query={'start': 'soon'})
    assert status == 400


def test_routes_calendars_and_moving_an_event(cal, monkeypatch):
    r = cal.routes
    seen = []
    monkeypatch.setattr(r, '_scope_changed', lambda action, name: seen.append((action, name)))
    assert r.put_calendar(name='Work')['name'] == 'work'
    body, status = r.put_calendar(name='none')
    assert status == 400
    made = r.create_event(body={'title': 'Review', 'start': '2026-11-20', 'calendar': 'work'})
    assert made['event']['calendar'] == 'work'
    moved = r.update_event(event_id=made['event']['id'], body={'calendar': 'default'})
    assert moved['event']['calendar'] == 'default'
    assert [c['name'] for c in r.list_calendars()['calendars']] == ['default', 'work']
    only_work = r.list_events(query={'start': '2026-11-01', 'end': '2026-11-30', 'calendar': 'work'})
    assert only_work['events'] == []
    body, status = r.delete_calendar(name='default')
    assert status == 400
    assert r.delete_calendar(name='work')['removed_events'] == 0
    assert seen == [('added', 'work'), ('deleted', 'work')]
