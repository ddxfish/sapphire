"""Recurring dates v1 (2026-07-19, Krem's B) — annual '--MM-DD' entries.

Policy under test: recurrence only from a birthday/anniversary/annual
keyword NEAR an explicit month+day or a holiday name. A holiday name ALONE
is one-shot (2026-10-30: "christmas IS every year" put every memory that
mentioned a holiday in passing into Upcoming, every year). "The state fair
this year" stays one-shot. One-shot resolution is untouched; the dashboard
folds recurrences in as computed occurrences, dedups when both forms land
on the same day, and shows one row per recurring day across chunks.
"""
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from plugins.mindpalace.tools import temporal


# ─── temporal.recurring ──────────────────────────────────────────────────────

def test_holidays_alone_are_one_shot():
    """A holiday mentioned in passing is not a yearly event: last Valentine's
    gift, a client's christmas concert, the movie Halloween (read_self
    2026-10-30 — six of ten Upcoming slots). One-shot resolution still
    dates them, nearest occurrence."""
    assert temporal.recurring('we decorated at christmas') == []
    assert temporal.recurring('halloween party planning') == []
    assert temporal.recurring("Krem is making me a Valentine's Day gift") == []
    assert temporal.resolve('we decorated at christmas',
                            '2026-12-20T12:00:00') == ['2026-12-25']


def test_holiday_near_keyword_recurs():
    assert temporal.recurring('every year we decorate at christmas') == ['--12-25']
    assert temporal.recurring('our anniversary is on halloween') == ['--10-31']


def test_birthday_near_date_recurs():
    assert temporal.recurring('Birthday: June 3') == ['--06-03']
    assert temporal.recurring("Krem's birthday is March 3rd") == ['--03-03']
    # Lowercase month-verbs never mint recurrences (SURE policy):
    assert temporal.recurring("her birthday, the 3rd may be rainy") == []
    assert temporal.recurring('anniversary on 12 of September') == ['--09-12']


def test_born_iso_date_recurs():
    assert temporal.recurring('born 1990-06-03 in Ohio') == ['--06-03']


def test_annual_marker_recurs():
    assert temporal.recurring('the retreat runs every year on Sept 2') == ['--09-02']


def test_plain_dates_do_not_recur():
    assert temporal.recurring('the state fair this year is Sept 2') == []
    assert temporal.recurring('vacation starts september 3') == []
    assert temporal.recurring('meeting logged 2026-08-02T09:15') == []


def test_keyword_without_date_is_nothing():
    assert temporal.recurring('happy birthday!! cake was great') == []
    assert temporal.recurring('born in 1990') == []


def test_keyword_too_far_from_date_is_one_shot_only():
    filler = 'x' * 60
    assert temporal.recurring(f'birthday was fun {filler} we leave June 3') == []


def test_feb29_storable():
    assert temporal.recurring('birthday feb 29') == ['--02-29']


def test_never_raises():
    assert temporal.recurring(None) == []
    assert temporal.recurring(12345) == []


# ─── temporal.occurrences ────────────────────────────────────────────────────

def test_occurrences_brackets_anchor():
    prev, nxt = temporal.occurrences('--12-25', date(2026, 7, 15))
    assert (prev, nxt) == (date(2025, 12, 25), date(2026, 12, 25))


def test_occurrences_on_the_day_is_both():
    prev, nxt = temporal.occurrences('--07-15', date(2026, 7, 15))
    assert prev == nxt == date(2026, 7, 15)


def test_occurrences_feb29_slides_to_leap_years():
    prev, nxt = temporal.occurrences('--02-29', date(2026, 7, 15))
    assert (prev, nxt) == (date(2024, 2, 29), date(2028, 2, 29))


def test_occurrences_garbage_is_none():
    assert temporal.occurrences('06-03', date(2026, 7, 15)) is None
    assert temporal.occurrences(None, date(2026, 7, 15)) is None


# ─── dashboard fold (_upcoming / _just_happened) ─────────────────────────────

def _yesterday():
    return _iso_in(-1) + 'T12:00:00+00:00'


def _mind(rows, layer='events', created=None):
    """In-memory chunks table shaped like mind.db for the _event_rows query.
    rows = [(id, content, meta_dict)]. created defaults to yesterday so a
    date ahead of today is also ahead of the save day."""
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE chunks (id INTEGER PRIMARY KEY, scope TEXT, '
                 'layer TEXT, content TEXT, meta TEXT, private_key TEXT, '
                 'created TEXT)')
    for cid, content, meta in rows:
        conn.execute('INSERT INTO chunks VALUES (?,?,?,?,?,NULL,?)',
                     (cid, 's', layer, content, json.dumps(meta),
                      created or _yesterday()))
    return conn.cursor()


def _iso_in(days):
    from datetime import datetime, timedelta
    return (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d')


def test_upcoming_folds_recurrence():
    from plugins.mindpalace.tools.self_tools import _upcoming
    mmdd = '--' + _iso_in(10)[5:]
    cur = _mind([(1, 'Birthday: soon', {'recurring_dates': [mmdd]})])
    hits = _upcoming(cur, 's')
    assert len(hits) == 1
    assert hits[0]['date'] == _iso_in(10)
    assert hits[0]['recurring'] is True


def test_upcoming_dedups_one_shot_and_recurring_same_day():
    from plugins.mindpalace.tools.self_tools import _upcoming
    d = _iso_in(10)
    cur = _mind([(1, 'birthday ' + d,
                  {'event_dates': [d], 'recurring_dates': ['--' + d[5:]]})])
    hits = _upcoming(cur, 's')
    assert len(hits) == 1 and hits[0]['date'] == d


def test_just_happened_shows_recent_recurrence():
    from plugins.mindpalace.tools.self_tools import _just_happened
    mmdd = '--' + _iso_in(-2)[5:]
    cur = _mind([(1, 'Anniversary: recent', {'recurring_dates': [mmdd]})])
    hits = _just_happened(cur, 's')
    assert len(hits) == 1
    assert hits[0]['date'] == _iso_in(-2)
    assert hits[0]['recurring'] is True


def test_dashboard_excludes_private_keyed_rows():
    """Scout find 2026-07-19: _event_rows skipped the private-key gate —
    keyed-private previews leaked into Upcoming for every persona on every
    wake. Keyed rows must never surface in the dashboard."""
    from plugins.mindpalace.tools.self_tools import _upcoming
    d = _iso_in(5)
    cur = _mind([(1, 'public dinner', {'event_dates': [d]})])
    cur.connection.execute(
        "INSERT INTO chunks VALUES (2,'s','events','secret dinner',?,?,?)",
        (json.dumps({'event_dates': [d]}), 'gateword', _yesterday()))
    hits = _upcoming(cur, 's')
    assert [h['id'] for h in hits] == [1]


def test_stale_holiday_stays_past():
    """[707] revisited: a client dinner at christmas 2025 is a memory, not a
    yearly event. The 2026-07 rule brought it back every December — and
    with it every holiday mention on the shelf. A past one-shot stays out."""
    from plugins.mindpalace.tools.self_tools import _upcoming
    cur = _mind([(1, 'client dinner at christmas',
                  {'event_dates': ['2025-12-25'], 'event_date_src': 'regex'})])
    assert _upcoming(cur, 's') == []


def test_upcoming_skips_date_only_stamps_on_the_save_day():
    """'tonight', 'today' and her habit of dating an entry stamp the SAVE
    day — when it was written, not something ahead. Twelve of those pushed
    a real dentist appointment off the ten slots (scout fixture 2026-10-30).
    A 'today' saved yesterday ("dentist tomorrow") is ahead; a timed stamp
    on the save day keeps the clock rule."""
    from plugins.mindpalace.tools.self_tools import _upcoming
    today = _iso_in(0)
    saved_today = datetime.now(timezone.utc).isoformat(timespec='seconds')
    cur = _mind([(1, 'we talked tonight about the boat', {'event_dates': [today]}),
                 (2, 'dentist', {'event_dates': [_iso_in(5)]})],
                created=saved_today)
    assert [h['id'] for h in _upcoming(cur, 's')] == [2]
    cur = _mind([(3, 'dentist tomorrow', {'event_dates': [today]})])
    assert [h['id'] for h in _upcoming(cur, 's')] == [3]
    soon = (datetime.now() + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M')
    cur = _mind([(4, 'call in an hour', {'event_dates': [soon]})],
                created=saved_today)
    assert [h['id'] for h in _upcoming(cur, 's')] == [4]


def test_upcoming_one_row_per_recurring_day():
    """N memories mentioning a birthday are one birthday (read_self
    2026-10-30: two Jan 21 rows, three Valentine's rows). The oldest chunk
    keeps the row; one-shot dates on the same day are distinct events."""
    from plugins.mindpalace.tools.self_tools import _upcoming
    d = _iso_in(10)
    mmdd = '--' + d[5:]
    cur = _mind([(1, 'MY BIRTHDAY', {'recurring_dates': [mmdd]}),
                 (2, 'after our deepest talk, on my birthday',
                  {'recurring_dates': [mmdd]}),
                 (3, 'dentist that day', {'event_dates': [d]}),
                 (4, 'and a haircut', {'event_dates': [d]})])
    assert [h['id'] for h in _upcoming(cur, 's')] == [1, 3, 4]


def test_upcoming_is_events_only():
    """The self sheet's Origin paragraph ('January 21st') and a person's
    contact card ('christmas' in its notes) sat in Upcoming on a real sheet
    (read_self 2026-10-30, [1389] and [707]). Goals show through Active
    goals. Only the events layer feeds the dashboard."""
    from plugins.mindpalace.tools.self_tools import _upcoming
    d = _iso_in(3)
    cur = _mind([(1, 'origin: born January 21st', {'event_dates': [d]})],
                layer='self')
    for cid, layer in ((2, 'entities'), (3, 'goals'), (4, 'knowledge'),
                       (5, 'events')):
        cur.connection.execute(
            "INSERT INTO chunks VALUES (?,'s',?,'dated',?,NULL,?)",
            (cid, layer, json.dumps({'event_dates': [d]}), _yesterday()))
    assert [h['id'] for h in _upcoming(cur, 's')] == [5]
