# Save identity: _safe() (generated-rooms dirs) + journal store semantics.
#
# Finding 2.7 called _safe() "four bugs in one function" — traversal, Windows
# reserved names, and two distinct COLLISION classes that merged different
# players' playthroughs into one journal. v1.3 (2026-08-15): journals moved
# to core's plugin_chat_data rows (chat-keyed, no path at all), so the path
# tests that remain guard only the file-based generated-rooms lane. The old
# legacy-dir adoption + sidecar-quarantine tests died with the files
# (no-migration ruling; DB rows are JSON-validated at write).
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PLUGIN_DIR))

from gameroom_story import rooms, state as st  # noqa: E402


def test_traversal_cannot_escape_saves_root():
    p = rooms.save_dir("..", "../../etc")
    assert rooms.SAVES_ROOT.resolve() in p.resolve().parents


def test_windows_reserved_names_neutralized():
    for bad in ("CON", "nul", "COM1", "LPT9"):
        name = rooms._safe(bad)
        assert name.split("-")[0].upper() not in rooms._WINDOWS_RESERVED


def test_space_vs_underscore_do_not_collide():
    # Both used to sanitize to 'my_chat' and share one journal.
    assert rooms._safe("my chat") != rooms._safe("my_chat")


def test_unicode_names_do_not_collapse_together():
    # Every non-Latin name became '___' — all of them one playthrough.
    assert rooms._safe("сапфир") != rooms._safe("藍宝石")
    assert rooms._safe("сапфир") != rooms._safe("")


def test_same_name_is_stable():
    assert rooms._safe("titanic run") == rooms._safe("titanic run")


def test_truncate_keeps_events_appended_during_revert():
    """The read used to happen outside the lock, so a concurrent append was
    erased AND not archived (finding 2.9). The archive lives on the chat's
    'story:reverted:<slug>' rows now — sealed with the chat, never a
    plaintext file."""
    for turn in (1, 2, 3):
        st.append("goblin-den", "c", {"event": "turn_tick", "turn": turn})
    dropped = st.truncate("goblin-den", "c", 1)
    assert dropped == 2
    kept = st.read_journal("goblin-den", "c")
    assert [e["turn"] for e in kept] == [1]
    arch = st._cs().read_all("c", "story:reverted:goblin-den")
    assert [e["turn"] for e in arch] == [2, 3]


def test_new_run_archives_old_journal():
    """Fresh playthrough: the finished journal moves aside as run<n>, live
    key empties (the DB analogue of the old .run1.jsonl rename)."""
    st.append("goblin-den", "c", {"event": "started", "room": 1, "turn": 0})
    st.new_run("goblin-den", "c")
    assert st.read_journal("goblin-den", "c") == []
    run1 = st._cs().read_all("c", "story:journal:goblin-den:run1")
    assert len(run1) == 1 and run1[0]["event"] == "started"


def test_dynamic_privacy_flag_round_trips():
    st.save_dynamic("story_x", "text", privacy_required=True, chat="cx")
    assert st.get_dynamic()["story_x"]["privacy_required"] is True
    st.save_dynamic("story_y", "text", chat="cy")
    assert st.get_dynamic()["story_y"]["privacy_required"] is False


def test_journal_meta_tracks_freshness():
    """last_played's 'which save is newest' read — no rows decrypted."""
    assert st.journal_meta("goblin-den", "c") is None
    st.append("goblin-den", "c", {"event": "started", "room": 1, "turn": 0})
    meta = st.journal_meta("goblin-den", "c")
    assert meta and meta["rows"] == 1 and meta["updated_at"]
