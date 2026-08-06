# Save-path identity: _safe() / save_dir() / chat lifecycle carry.
#
# Finding 2.7 called _safe() "four bugs in one function" — traversal, Windows
# reserved names, and two distinct COLLISION classes that merged different
# players' playthroughs into one journal. Findings 2.1/2.2: rename stranded
# the save and delete left a ghost the next same-named chat inherited.
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PLUGIN_DIR))

from gameroom_story import rooms, state as st  # noqa: E402


@pytest.fixture
def saves(tmp_path, monkeypatch):
    # ALL FOUR module-level paths must be redirected. DYNAMIC_FILE was the
    # one the old fixtures missed (Tier 5 fixture-hygiene item) — without it
    # these tests write into the developer's real sidecar and quarantine the
    # live story costumes. Caught the hard way, 2026-08-05.
    monkeypatch.setattr(rooms, "SAVES_ROOT", tmp_path)
    monkeypatch.setattr(st, "SAVES_ROOT", tmp_path)
    monkeypatch.setattr(st, "ACTIVE_FILE", tmp_path / "active.json")
    monkeypatch.setattr(st, "DYNAMIC_FILE", tmp_path / "_dynamic_monoliths.json")
    return tmp_path


def test_traversal_cannot_escape_saves_root(saves):
    p = st.journal_path("..", "../../etc")
    assert saves.resolve() in p.resolve().parents


def test_windows_reserved_names_neutralized(saves):
    for bad in ("CON", "nul", "COM1", "LPT9"):
        name = rooms._safe(bad)
        assert name.split("-")[0].upper() not in rooms._WINDOWS_RESERVED


def test_space_vs_underscore_do_not_collide(saves):
    # Both used to sanitize to 'my_chat' and share one journal.
    assert rooms._safe("my chat") != rooms._safe("my_chat")


def test_unicode_names_do_not_collapse_together(saves):
    # Every non-Latin name became '___' — all of them one playthrough.
    assert rooms._safe("сапфир") != rooms._safe("藍宝石")
    assert rooms._safe("сапфир") != rooms._safe("")


def test_same_name_is_stable(saves):
    assert rooms._safe("titanic run") == rooms._safe("titanic run")


def test_legacy_save_dir_is_adopted(saves):
    """Saves written under the old scheme must keep playing."""
    legacy = saves / rooms._legacy_safe("goblin-den") / rooms._legacy_safe("my chat")
    legacy.mkdir(parents=True)
    (legacy / "journal.jsonl").write_text('{"event":"started","room":1,"turn":0}\n',
                                          encoding="utf-8")
    events = st.read_journal("goblin-den", "my chat")
    assert len(events) == 1 and events[0]["event"] == "started"


def test_generated_rooms_and_journal_agree_on_dir(saves):
    """load_generated_rooms used the raw slug while journal_path sanitized
    it — the two disagreed for any slug needing sanitization (Tier 5)."""
    slug, chat = "my story", "my chat"
    jp = st.journal_path(slug, chat)
    gen = rooms.save_dir(slug, chat) / "rooms"
    assert gen.parent == jp.parent


def test_truncate_keeps_events_appended_during_revert(saves):
    """The read used to happen outside the lock, so a concurrent append was
    erased AND not archived (finding 2.9)."""
    for turn in (1, 2, 3):
        st.append("goblin-den", "c", {"event": "turn_tick", "turn": turn})
    dropped = st.truncate("goblin-den", "c", 1)
    assert dropped == 2
    kept = st.read_journal("goblin-den", "c")
    assert [e["turn"] for e in kept] == [1]
    arch = st.journal_path("goblin-den", "c").with_suffix(".reverted.jsonl")
    assert arch.exists() and len(arch.read_text().strip().splitlines()) == 2


def test_corrupt_dynamic_sidecar_is_quarantined_not_swallowed(saves):
    st.DYNAMIC_FILE.parent.mkdir(parents=True, exist_ok=True)
    st.DYNAMIC_FILE.write_text("{not json", encoding="utf-8")
    assert st.get_dynamic() == {}
    assert not st.DYNAMIC_FILE.exists(), "corrupt file must be moved aside"
    assert list(st.DYNAMIC_FILE.parent.glob("*.bad-*")), "quarantine copy must exist"


def test_dynamic_privacy_flag_round_trips(saves):
    st.save_dynamic("story_x", "text", privacy_required=True)
    assert st.get_dynamic()["story_x"]["privacy_required"] is True
    st.save_dynamic("story_y", "text")
    assert st.get_dynamic()["story_y"]["privacy_required"] is False
