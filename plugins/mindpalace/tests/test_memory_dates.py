"""Memory dating cues — 2026-09-08 (Krem: she stamped today's date into every
memory; the temporal extractor read it as the event's date and the librarian
saw a calendar full of "today").

Three cues, all here:
  1. save_memory's description says never to write today's date.
  2. The save receipt carries the day the system stamped.
  3. Recall shows the calendar day once a memory is older than two weeks
     ('Jan 9'), with the year past 365 days ('Jan 9 2025').
"""
from datetime import datetime, timedelta, timezone

from plugins.mindpalace.tools import palace_tools as pt


def _iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec='seconds')


def test_description_forbids_todays_date_and_lies_about_the_cap():
    tool = next(t for t in pt.TOOLS if t['function']['name'] == 'save_memory')
    desc = tool['function']['description']
    assert "Never add today's date" in desc
    assert "450" in desc and "512" not in desc      # the cap is 512; 450 keeps her under it
    assert pt.MAX_CHUNK_LENGTH == 512


def test_recall_age_is_relative_then_calendar_then_year():
    assert pt._format_time_ago(_iso(0)).endswith(("m ago", "h ago", "just now")) or pt._format_time_ago(_iso(0)) == "just now"
    assert pt._format_time_ago(_iso(3)) == "3d ago"
    assert pt._format_time_ago(_iso(13)) == "13d ago"
    day = pt._format_time_ago(_iso(20))
    assert " ago" not in day and day.split()[0].isalpha() and len(day.split()) == 2   # 'Aug 19'
    with_year = pt._format_time_ago(_iso(400))
    assert len(with_year.split()) == 3 and with_year.split()[-1].isdigit()           # 'Aug 4 2025'


def test_goals_keep_relative_ages_for_their_weeks_conversion():
    assert pt._format_time_ago(_iso(20), absolute_after=None) == "20d ago"


def test_fmt_day_shape():
    d = pt._fmt_day(_iso(0))
    parts = d.split()
    assert len(parts) == 3 and parts[0].isalpha() and parts[1].isdigit() and parts[2].isdigit()
    assert pt._fmt_day("not a timestamp") == ""

# The receipt-carries-the-day check lives in test_mindpalace.py (it needs
# that module's `palace` fixture).
