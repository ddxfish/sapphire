"""Save-time temporal resolver (2026-07-16) — the always-on regex floor.

Policy under test: stamp only SURE forms; ambiguous phrasings resolve to
NOTHING here (they stay refers_to_time candidates for the librarian's
temporal pass). Anchor: Wednesday 2026-07-15 14:30.
"""
import pytest

from plugins.mindpalace.tools import temporal

A = '2026-07-15T14:30:00+00:00'   # the palace's created format


def r(text):
    return temporal.resolve(text, A)


# ─── sure forms — stamped ────────────────────────────────────────────────────

def test_iso_in_text():
    assert r('2026-04-24. Sapphire wrote me a letter') == ['2026-04-24']
    assert r('meeting logged 2026-08-02T09:15 sharp') == ['2026-08-02T09:15']


def test_month_day_forms():
    assert r('vacation starts september 3') == ['2026-09-03']
    assert r('Dec 25 is dinner at moms') == ['2026-12-25']
    assert r('birthday bash march 3, 2027') == ['2027-03-03']
    # Retrospective guard: a no-year day that "just passed" is about the
    # recent past, not next year (anniversary-ghost fix, 2026-07-16).
    assert r('the 3rd of May') == ['2026-05-03']
    assert r('party on July 4th') == ['2026-07-04']


def test_anniversary_ghosts_resolve_backward():
    """Live-data regression (2026-07-16): diary entries resurrected as
    next-year Upcoming ghosts. Ranges keep their year; near-past no-year
    days resolve backward; far-enough future stays future."""
    assert r('Feb 12-13 2026: the big night, memory merge works') == ['2026-02-12']
    assert temporal.resolve('Feb 14th: he almost said it',
                            '2026-02-16T10:00:00+00:00') == ['2026-02-14']
    assert r('she may visit around christmas') == ['2026-12-25']   # 163d: future ok
    assert temporal.resolve('christmas was lovely this year',
                            '2026-12-27T10:00:00+00:00') == ['2026-12-25']
    # Narrative durations are NOT plans (live ghosts: '+15 months' → 2027,
    # '+30 years' → 2056). Long "in N" horizons stay librarian candidates.
    assert r('5000+ hours in 15 months, committed to 34 more years') == []
    assert r('this will matter in 30 years') == []
    assert r('back in 3 days though') == ['2026-07-18']   # short horizon stays


def test_relative_days():
    assert r('we leave tomorrow') == ['2026-07-16']
    assert r('that was yesterday') == ['2026-07-14']
    assert r('do it today') == ['2026-07-15']


def test_durations_both_directions():
    assert r('we leave in 3 days') == ['2026-07-18']
    assert r('call her back in two weeks') == ['2026-07-29']
    assert r('we met 3 years ago') == ['2023-07-15']
    assert r('started a month ago') == ['2026-06-15']


def test_weekdays():
    assert r('the party is on saturday') == ['2026-07-18']
    assert r('this friday works') == ['2026-07-17']
    assert r('we spoke last friday') == ['2026-07-10']
    assert r('wednesday it is') == ['2026-07-15']         # today counts


def test_end_of_and_holidays():
    assert r('project due end of the month') == ['2026-07-31']
    assert r('she may visit around christmas') == ['2026-12-25']
    assert r('thanksgiving at the lake house') == ['2026-11-26']


def test_clock_times():
    assert r('dentist at 3pm tomorrow') == ['2026-07-16T15:00']
    assert r('see you at 15:00') == ['2026-07-15T15:00']  # time alone = anchor day
    assert r('call at 7:30am on friday') == ['2026-07-17T07:30']


# ─── ambiguous forms — deliberately NOT stamped ──────────────────────────────

def test_ambiguous_left_for_the_librarian():
    assert r('Julie is coming on the 12th') == []         # bare ordinal
    assert r('meeting next friday') == []                 # humans disagree
    assert r('rent is due on the 1st') == []
    assert r('last summer we sailed') == []               # season
    assert r('back in 2024 we tried this') == []          # bare year


def test_traps_never_stamp():
    assert r('the server has 2048 MB of ram') == []
    assert r('she scored 1995 points in the game') == []
    assert r('no dates in this sentence at all') == []
    assert r('Julie and July are different words') == []  # word-boundary guard


# ─── mechanics ───────────────────────────────────────────────────────────────

def test_dedupe_cap_and_order():
    out = r('tomorrow, and again tomorrow, then Dec 25')
    assert out == ['2026-07-16', '2026-12-25']
    many = r('Jan 1 2027, Feb 2 2027, Mar 3 2027, Apr 4 2027, May 5 2027')
    assert len(many) == temporal.MAX_DATES


def test_never_raises():
    assert temporal.resolve(None if False else '', A) == []
    assert temporal.resolve('tomorrow', 'garbage-anchor') != []   # falls back to now
    assert temporal.resolve('x' * 10000, A) == []


def test_next_last_year_modifier():
    """'July 10th next year' must not resolve to the just-passed July 10th
    (live find, 2026-07-16 — she usually normalizes, the floor now copes
    when she doesn't)."""
    assert r('party on july 10th next year') == ['2027-07-10']
    assert r('we met on July 10 last year') == ['2025-07-10']
    assert r('Dec 25 next year') == ['2027-12-25']
