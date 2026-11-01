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


# ─── plans — the sure-FUTURE subset that feeds meta.when (2026-10-30) ───────

def test_plans_are_only_sure_future():
    p = lambda t: temporal.plans(t, A)
    assert p('dentist on 2026-11-04 at 10am') == ['2026-11-04T10:00']
    assert p('their wedding is June 20, 2027') == ['2027-06-20']
    assert p('we fly out in 2 weeks') == ['2026-07-29']
    assert p('lunch tomorrow at 3pm') == ['2026-07-16T15:00']
    assert p('July 10th next year we sail') == ['2027-07-10']
    # a regex cannot read tense or intent — none of these are plans
    assert p('On Monday we fixed the bug') == []
    assert p('we talked tonight about the boat') == []
    assert p('John 3:16 was his favorite verse') == []
    assert p('Remember when we met on Feb 14th?') == []
    assert p('we watched Halloween (the movie)') == []
    assert p('he got the job on June 5, 2024') == []
    assert p('2026-07-15 we shipped it') == []            # the save day itself
    assert p('see you at christmas') == []


def test_when_from_and_parse_when():
    assert temporal.when_from(['2026-07-10', '2026-07-20', '2026-08-01'], A) == '2026-07-20'
    assert temporal.when_from(['2026-07-15'], A) is None            # the save day, date-only
    assert temporal.when_from(['2026-07-15T20:00'], A) == '2026-07-15T20:00'
    assert temporal.when_from([], A) is None
    assert temporal.parse_when('2026-11-04') == ('2026-11-04', None)
    assert temporal.parse_when('2026-11-04T10:00') == ('2026-11-04T10:00', None)
    assert temporal.parse_when('2026-11-04 10:00') == ('2026-11-04T10:00', None)
    assert temporal.parse_when('2026-02-30')[0] is None
    assert temporal.parse_when('2026-11-04T25:00')[0] is None
    assert temporal.parse_when('tomorrow at 3pm', A) == ('2026-07-16T15:00', None)
    assert temporal.parse_when('in 2 weeks', A) == ('2026-07-29', None)
    assert temporal.parse_when('next friday', A)[0] is None
    assert temporal.parse_when('', A)[0] is None


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


# ─── the zone hook (2026-10-31): tests prove out on every box the same way ──

@pytest.fixture(autouse=True)
def _utc_local(monkeypatch):
    from datetime import timezone
    monkeypatch.setattr(temporal, '_local_tz', timezone.utc)


def test_local_zone_governs_the_day(monkeypatch):
    from zoneinfo import ZoneInfo
    monkeypatch.setattr(temporal, '_local_tz', ZoneInfo('Australia/Sydney'))   # 00:30 next day
    assert temporal.plans('lunch tomorrow', A) == ['2026-07-17']
    monkeypatch.setattr(temporal, '_local_tz', ZoneInfo('America/New_York'))   # 10:30 same day
    assert temporal.plans('lunch tomorrow', A) == ['2026-07-16']


def test_noon_and_midnight():
    assert temporal.plans('lunch at noon tomorrow', A) == ['2026-07-16T12:00']
    assert r('we leave at midnight') == ['2026-07-15T00:00']
    assert temporal.parse_when('noon tomorrow', A) == ('2026-07-16T12:00', None)


def test_pre_1970_anchor_never_raises():
    assert temporal.resolve('tomorrow', '1960-01-01T00:00:00+00:00') == ['1960-01-02']


def test_a_recurrence_keyword_names_one_date():
    assert temporal.recurring("June 24 2026: I chose my birthday — July 10th") == ['--07-10']
    assert temporal.recurring("July 10 2026 — MY FIRST BIRTHDAY") == ['--07-10']
    assert temporal.recurring("MY BIRTHDAY: January 21, 2025 at 12:11 AM. First commit") == ['--01-21']
    assert temporal.recurring("Skye's birthday is March 3 and Anita's anniversary is Dec 25") == ['--03-03', '--12-25']


# ─── her today-stamp (2026-10-31) ────────────────────────────────────────────
# Chats span days and she wrote the date she last saw: on dev 185 of 339
# stamps ran one to seven days early of the save. The save time is the truth.

S = '2026-03-05T14:30:00+00:00'      # saved March 5; the chat began March 3


def test_strip_stamp_forms():
    s = lambda t: temporal.strip_stamp(t, S)
    assert s('March 3 2026: today we fixed the bug') == ('today we fixed the bug', '2026-03-03')
    assert s('March 3, 2026 — today we fixed the bug') == ('today we fixed the bug', '2026-03-03')
    assert s('2026-03-05: Trinity was born') == ('Trinity was born', '2026-03-05')
    assert s('3/3/2026: a slash date') == ('a slash date', '2026-03-03')
    assert s('Core memory — March 3 2026: the boat') == ('Core memory: the boat', '2026-03-03')
    assert s('Milestone (March 3 2026): shipped it') == ('Milestone: shipped it', '2026-03-03')
    assert s('Change log, March 3rd 2026, moved the lake') == ('Change log: moved the lake', '2026-03-03')
    assert s('March 3 2026 We fixed the bug') == ('We fixed the bug', '2026-03-03')
    assert s('March 3 2026\nA new line body') == ('A new line body', '2026-03-03')


def test_strip_stamp_leaves_content_dates_alone():
    s = lambda t: temporal.strip_stamp(t, S)
    for t in ('March 3 2026 was a hard day',                 # a sentence, not a stamp
              'On March 3 2026 we did X',
              'Sapphire since March 3 2026: the whole story',
              "1997-05-12: Krem's birth date",               # history is content
              'March 20 2026: the launch',                    # ahead = a plan
              'February 2026: a month only',
              'March 3 2026:',                                # nothing but a date
              'Today is my anniversary',
              'February 18 2026: fifteen days back'):
        assert s(t) == (t, None), t
    # the window: fourteen days before the save day to one after (UTC)
    assert s('February 19 2026: two weeks back')[1] == '2026-02-19'
    assert s('March 6 2026: a day late')[1] == '2026-03-06'
    assert s('March 7 2026: two days ahead')[1] is None
    assert temporal.strip_stamp(None, S) == (None, None)


def test_recurring_takes_today_from_the_anchor():
    assert temporal.recurring('Today is my anniversary', S) == ['--03-05']
    assert temporal.recurring('my birthday is tomorrow', S) == ['--03-06']
    assert temporal.recurring("June 24 2026: I chose my birthday — July 10th", S) == ['--07-10']
