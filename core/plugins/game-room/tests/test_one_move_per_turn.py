# One move per turn (Krem 2026-08-23): Sapph walked hall → storage in one
# message, passing through a room the player never stood in. The engine
# already knows "just arrived" (turns_in_room == 0, the NEW SCENE signal);
# now session.act refuses a second move until the player's next message
# ticks. Global GM-tab switch (storycfg:universal.one_move_per_turn),
# default on; the referee stays pure.
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).parent.parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from gameroom_story import rooms, session, state as st  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "stories"


class FakeStore:
    def __init__(self):
        self.d = {}

    def get(self, k):
        return self.d.get(k)

    def save(self, k, v):
        self.d[k] = v


@pytest.fixture
def story(monkeypatch):
    monkeypatch.setattr(rooms, "_story_roots", lambda: [FIXTURES])
    return rooms.load_story("mad-manse")


@pytest.fixture
def cfg_store(monkeypatch):
    fake = FakeStore()
    monkeypatch.setattr(session, "_cfg_store", fake, raising=False)
    from core.plugin_loader import plugin_loader
    monkeypatch.setattr(plugin_loader, "get_plugin_state", lambda name: fake)
    return fake


def _begin(chat, turn=1):
    """Start in the hall with the parlor door open and the player's first
    message already ticked — the state every real act() sees."""
    st.set_active(chat, "mad-manse", None)
    assert st.append_many("mad-manse", chat, [
        {"event": "started", "story": "mad-manse", "room": 1, "turn": 0},
        {"event": "state_set", "key": "chest_opened", "value": True, "turn": 0},
        {"event": "turn_tick", "turn": turn}])


def _moves(chat):
    return [e for e in st.read_journal("mad-manse", chat) if e.get("event") == "moved"]


def test_second_move_in_one_turn_is_refused(story, cfg_store):
    chat = "omt-refuse"
    _begin(chat)
    msg, ok = session.act(None, "move", "the parlor", session=chat)
    assert ok and "Moved to 'The Parlor'" in msg
    msg, ok = session.act(None, "move", "the hall", session=chat)
    assert not ok
    assert "Already moved this turn" in msg and "The Parlor" in msg
    _, state = session.load_active(chat)
    assert state["room"] == 2 and len(_moves(chat)) == 1
    # other acts still work in the fresh room — only movement is held
    msg, ok = session.act(None, "look", "room", session=chat)
    assert ok


def test_next_player_message_opens_the_way(story, cfg_store):
    chat = "omt-tick"
    _begin(chat)
    assert session.act(None, "move", "the parlor", session=chat)[1]
    assert st.append("mad-manse", chat, {"event": "turn_tick", "turn": 2})
    msg, ok = session.act(None, "move", "the hall", session=chat)
    assert ok and "Moved to 'The Hall'" in msg
    assert len(_moves(chat)) == 2


def test_switch_off_allows_the_dash(story, cfg_store):
    chat = "omt-off"
    cfg_store.save("storycfg:universal", {"one_move_per_turn": False})
    _begin(chat)
    assert session.act(None, "move", "the parlor", session=chat)[1]
    msg, ok = session.act(None, "move", "the hall", session=chat)
    assert ok and len(_moves(chat)) == 2


def test_gm_tab_switch_round_trips_and_survives_text_save(story, cfg_store, monkeypatch):
    from routes import story_routes
    monkeypatch.setattr(story_routes, "_system", lambda: None)
    r = story_routes.get_story_settings("mad-manse")
    field = next(f for f in r["schema"] if f["key"] == "one_move_per_turn")
    assert field["type"] == "checkbox" and field["tab"] == "GM"
    assert r["settings"]["one_move_per_turn"] is True
    story_routes.set_story_settings("mad-manse", body={"settings": {"one_move_per_turn": False}})
    assert session.one_move_per_turn() is False
    # a later GM-text save merges — it must not reset the switch
    story_routes.set_story_settings("mad-manse", body={"settings": {"gm_universal": "Terse GM."}})
    assert session.one_move_per_turn() is False
    assert session.conduct_for(story)[0] == "Terse GM."
    assert story_routes.get_story_settings("mad-manse")["settings"]["one_move_per_turn"] is False


def test_move_result_carries_the_new_scene(story, cfg_store):
    # Server-Sapph spiral (2026-08-23): the ghost block predates the move,
    # so promising "details in your turn context" pointed at the OLD room.
    # The destination scene now rides in the tool result itself.
    chat = "omt-scene"
    _begin(chat)
    # a hidden thing in the parlor must NOT leak through the move summary
    session.upsert_user_object(chat, "mad-manse", 2, "cobweb_key", {
        "desc": "a key in the cobwebs", "hidden": True, "found_by": "search"})
    msg, ok = session.act(None, "move", "the parlor", session=chat)
    assert ok
    assert "Moved to 'The Parlor'" in msg
    assert "A parlor. Someone whispered" in msg     # template inline
    assert "Exits: 'the hall'" in msg               # visible exits inline
    assert "cobweb_key" not in msg                  # hidden stays hidden
