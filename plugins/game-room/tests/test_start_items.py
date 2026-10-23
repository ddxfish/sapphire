# Starting items (Krem's ask 2026-08-22, ruling B): a story-level object
# pool — things the player begins with, living in no room. Membership is
# DERIVED at load_active (never journaled — no rename orphans, add/remove
# mid-run is/never-was); specs resolve through the carried-objects pool arm,
# so declared verbs, dice, seals and show all fire from the pocket. Fixture:
# mad-manse ships a "locket" with a look+show (URL anchor: backdrops/
# lightbox-test.webp).
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).parent.parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from gameroom_story import referee, rooms, session, state as st  # noqa: E402

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


def _begin(story, chat, slots=None):
    st.set_active(chat, "mad-manse", None)
    if slots:
        st.update_active(chat, slots=slots)
    assert st.append_many("mad-manse", chat, [
        {"event": "started", "story": "mad-manse",
         "room": story["meta"]["start"], "turn": 0}])


def test_shipped_item_is_carried_from_turn_zero(story, cfg_store):
    chat = "si-basic-chat"
    _begin(story, chat, slots={"relationship": "rival"})
    s2, state = session.load_active(chat)
    assert "locket" in state["inventory"] and "locket" in state["taken"]
    # nothing journaled — membership is derived, not history
    assert all(e.get("event") != "taken"
               for e in st.read_journal("mad-manse", chat))
    # slot substitution reaches the pool like any room string
    assert "rival" in s2["items"]["locket"]["desc"]
    # {"has": ...} conditions see it with zero extra wiring
    assert referee.check_condition({"has": "locket"}, state)


def test_item_look_fires_authored_show_anywhere(story, cfg_store):
    chat = "si-look-chat"
    _begin(story, chat)
    # move to room 2 first — the pool travels
    assert st.append("mad-manse", chat, {"event": "moved", "to": 2, "turn": 1})
    msg, ok = session.act(None, "look", "locket", session=chat)
    assert ok and "Her face, painted small." in msg
    assert "(The player is shown an illustration: The locket.)" in msg
    full = session.full_state(None, session=chat)
    assert full["shown_last"] and full["shown_last"]["caption"] == "The locket"
    assert "locket" in full["inventory"]


def test_user_item_appears_immediately_and_deletes_clean(story, cfg_store):
    chat = "si-user-chat"
    _begin(story, chat)
    msg, ok = session.upsert_user_item(chat, "mad-manse", "picture_of_joey", {
        "desc": "a small photo",
        "interactions": {"look": {"message": "Joey grins back."}}})
    assert ok, msg
    s2, state = session.load_active(chat)
    assert "picture_of_joey" in state["inventory"]
    msg, ok = session.act(None, "look", "picture_of_joey", session=chat)
    assert ok and "Joey grins back." in msg
    # remove → it never was (derived semantics, the B ruling)
    session.delete_user_item(chat, "mad-manse", "picture_of_joey")
    s3, state3 = session.load_active(chat)
    assert "picture_of_joey" not in state3["inventory"]


def test_tombstoned_shipped_item_vanishes(story, cfg_store):
    chat = "si-tomb-chat"
    _begin(story, chat)
    session.upsert_user_item(chat, "mad-manse", "locket", {"_removed": True})
    s2, state = session.load_active(chat)
    assert "locket" not in state["inventory"]
    # restore = drop the layer entry
    session.delete_user_item(chat, "mad-manse", "locket")
    s3, state3 = session.load_active(chat)
    assert "locket" in state3["inventory"]


def test_ended_run_gets_no_injection(story, cfg_store):
    chat = "si-ended-chat"
    _begin(story, chat)
    assert st.append("mad-manse", chat, {"event": "ended", "turn": 1})
    s2, state = session.load_active(chat)
    assert "locket" not in state["inventory"]


def test_items_survive_other_layer_writes(story, cfg_store):
    # The spread-forward fix (2026-08-22): an object edit or scenario-tag
    # stamp must not erase the items bucket (the fixed-field rebuild class).
    chat = "si-survive-chat"
    _begin(story, chat)
    session.upsert_user_item(chat, "mad-manse", "coin", {"desc": "a coin"})
    session.upsert_user_object(chat, "mad-manse", 1, "vase", {"desc": "a vase"})
    session.set_room_text(chat, "mad-manse", 1, template="A grand hall.")
    layer = st.get_user_layer("mad-manse", chat)
    assert "coin" in (layer.get("items") or {})
    s2, state = session.load_active(chat)
    assert "coin" in state["inventory"]
