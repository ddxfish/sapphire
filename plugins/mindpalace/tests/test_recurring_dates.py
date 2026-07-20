"""Recurring dates v1 (2026-07-19, Krem's B) — annual '--MM-DD' entries.

Policy under test: recurrence only from fixed-date holiday names or a
birthday/anniversary/annual keyword NEAR an explicit month+day. "The state
fair this year" stays one-shot. One-shot resolution is untouched; the
dashboard folds recurrences in as computed occurrences and dedups when
both forms land on the same day.
"""
import json
import sqlite3
from datetime import date

import pytest

from plugins.mindpalace.tools import temporal


# ─── temporal.recurring ──────────────────────────────────────────────────────

def test_holidays_always_recur():
    assert temporal.recurring('we decorated at christmas') == ['--12-25']
    assert temporal.recurring('halloween party planning') == ['--10-31']


def test_birthday_near_date_recurs():
    assert temporal.recurring('Birthday: June 3') == ['--06-03']
    assert temporal.recurring("Krem's birthday is march 3rd") == ['--03-03']
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

def _mind(rows):
    """In-memory chunks table shaped like mind.db for the _event_rows query.
    rows = [(id, content, meta_dict)]"""
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE chunks (id INTEGER PRIMARY KEY, scope TEXT, '
                 'layer TEXT, content TEXT, meta TEXT, private_key TEXT)')
    for cid, content, meta in rows:
        conn.execute('INSERT INTO chunks VALUES (?,?,?,?,?,NULL)',
                     (cid, 's', 'entities', content, json.dumps(meta)))
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
        "INSERT INTO chunks VALUES (2,'s','events','secret dinner',?,?)",
        (json.dumps({'event_dates': [d]}), 'gateword'))
    hits = _upcoming(cur, 's')
    assert [h['id'] for h in hits] == [1]


def test_stale_one_shot_holiday_now_recurs():
    """[707]'s bug in miniature: christmas resolved to a PAST one-shot is
    invisible to Upcoming forever — the recurring form brings it back."""
    from plugins.mindpalace.tools.self_tools import _upcoming
    cur = _mind([(1, 'client dinner at christmas',
                  {'event_dates': ['2025-12-25'], 'event_date_src': 'regex',
                   'recurring_dates': ['--12-25']})])
    hits = _upcoming(cur, 's')
    assert len(hits) == 1
    assert hits[0]['date'].endswith('-12-25')
    assert hits[0]['date'] >= _iso_in(0)
