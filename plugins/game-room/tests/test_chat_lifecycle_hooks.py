# Chat lifecycle carry — v1.3: CORE owns it now.
#
# The old plugin-side chat_renamed/chat_deleted handlers walked the disk
# moving journal dirs, rewriting active.json, and re-keying game saves
# (~190 lines, findings 2.1/2.2). With playthroughs on plugin_chat_data
# rows, rename/delete carry happens inside core's own transaction — the
# handlers are retired (tmp/old/game-room-lifecycle-retired-20260815.py).
# Core-level carry is pinned in tests/test_plugin_chat_data.py; these pin
# the same guarantee THROUGH the story/game state layer, end to end.
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PLUGIN_DIR))

import gameroom_core as gc  # noqa: E402
from gameroom_story import state as st  # noqa: E402


def _seed(chat):
    st.set_active(chat, "goblin-den", "prev")
    st.append("goblin-den", chat, {"event": "started", "room": 1, "turn": 0})
    gc.save_state("holdem", {"pot": 5}, session=chat)


def test_rename_carries_playthrough(hermetic_chat_store):
    sm = hermetic_chat_store
    _seed("alpha")
    ok, new = sm.rename_chat("alpha", "beta")
    assert ok, new
    assert st.get_active_entry("beta")["story"] == "goblin-den"
    assert st.get_active_entry("alpha") is None
    assert [e["event"] for e in st.read_journal("goblin-den", "beta")] == ["started"]
    assert gc.load_state("holdem", session="beta") == {"pot": 5}
    assert gc.load_state("holdem", session="alpha") is None


def test_delete_drops_playthrough(hermetic_chat_store):
    sm = hermetic_chat_store
    _seed("alpha")
    assert sm.delete_chat("alpha")
    assert st.get_active_entry("alpha") is None
    assert st.read_journal("goblin-den", "alpha") == []
    assert gc.load_state("holdem", session="alpha") is None


def test_recreated_chat_starts_clean(hermetic_chat_store):
    """Finding 2.2: delete left a ghost the next same-named chat inherited
    mid-story. Rows die with the chat, so the recreated chat is virgin."""
    sm = hermetic_chat_store
    _seed("alpha")
    assert sm.delete_chat("alpha")
    sm.create_chat("alpha")
    assert st.get_active_entry("alpha") is None
    assert st.read_journal("goblin-den", "alpha") == []
