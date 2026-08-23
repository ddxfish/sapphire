# Story-builder grammar wave (Krem 2026-08-23, "zippity pippity"): the
# engine half of the rows the editor grew so a from-scratch story can
# reproduce the shipped mansion —
#   after_turns condition (hints-as-objects), the take-compose law
#   (declared take on a takeable pockets it), room on_enter override on
#   the playthrough layer ({_clear} strips shipped), and the editor's
#   CSV aliases / CSV answers / once-dice shapes played through resolve().
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


def _begin(story, chat):
    st.set_active(chat, "mad-manse", None)
    assert st.append_many("mad-manse", chat, [
        {"event": "started", "story": "mad-manse",
         "room": story["meta"]["start"], "turn": 0}])


# ── after_turns ─────────────────────────────────────────────────────────────

def test_after_turns_condition():
    state = st.initial_state()
    state["turns_in_room"] = 2
    assert not referee.check_condition({"after_turns": 3}, state)
    state["turns_in_room"] = 3
    assert referee.check_condition({"after_turns": 3}, state)
    assert referee.check_condition({"after_turns": 0}, state)
    # junk threshold: gate stays closed, never crashes
    assert not referee.check_condition({"after_turns": "lots"}, state)


def test_hint_object_surfaces_after_turns(story, cfg_store):
    chat = "bg-hint-chat"
    _begin(story, chat)
    room = story["meta"]["start"]
    msg, ok = session.upsert_user_object(chat, "mad-manse", room, "nagging_doubt", {
        "desc": "A thought you can't shake: the chest wants a WORD.",
        "condition": {"after_turns": 2}})
    assert ok, msg
    s2, state = session.load_active(chat)
    r = s2["rooms"][state["room"]]
    assert "nagging_doubt" not in referee._visible_objects(r, state)
    state["turns_in_room"] = 2
    assert "nagging_doubt" in referee._visible_objects(r, state)


# ── take compose law ─────────────────────────────────────────────────────────

def test_declared_take_on_takeable_pockets_and_speaks(story, cfg_store):
    chat = "bg-take-chat"
    _begin(story, chat)
    room = story["meta"]["start"]
    # exactly the shape the editor compiles from a "take, grab" card
    msg, ok = session.upsert_user_object(chat, "mad-manse", room, "comic", {
        "desc": "a comic, face down", "takeable": True,
        "interactions": {"take": {"aliases": ["grab"],
                                  "message": "You pick it up carefully, keeping its place.",
                                  "set": {"comic_taken": True}}}})
    assert ok, msg
    msg, ok = session.act(None, "grab", "comic", session=chat)
    assert ok and "keeping its place" in msg
    s2, state = session.load_active(chat)
    assert "comic" in state["inventory"]
    assert state["flags"].get("comic_taken") is True
    # second take: the declared verb still answers, but no double pocket
    msg, ok = session.act(None, "take", "comic", session=chat)
    assert ok and "keeping its place" in msg
    s3, state3 = session.load_active(chat)
    assert state3["inventory"].count("comic") == 1
    assert sum(1 for e in st.read_journal("mad-manse", chat) if e.get("event") == "taken") == 1


def test_declared_take_on_non_takeable_stays_narrative(story, cfg_store):
    chat = "bg-hand-chat"
    _begin(story, chat)
    room = story["meta"]["start"]
    session.upsert_user_object(chat, "mad-manse", room, "her_hand", {
        "desc": "her hand", "interactions": {"take": {"message": "She lets you."}}})
    msg, ok = session.act(None, "take", "her_hand", session=chat)
    assert ok and "She lets you." in msg
    s2, state = session.load_active(chat)
    assert "her_hand" not in state["inventory"]


# ── on_enter override ────────────────────────────────────────────────────────

def test_on_enter_override_fires_on_arrival_and_clear_strips(story, cfg_store):
    chat = "bg-enter-chat"
    _begin(story, chat)
    start = story["meta"]["start"]
    # pick any exit from the start room
    dest = story["rooms"][start]["exits"][0]["to"]
    msg, ok = session.set_room_enter(chat, "mad-manse", dest, {"set": {"arrived": True},
                                                               "adjust": {"dread": 1}})
    assert ok, msg
    s2, state = session.load_active(chat)
    assert s2["rooms"][dest]["on_enter"] == {"set": {"arrived": True}, "adjust": {"dread": 1}}
    label = story["rooms"][start]["exits"][0]["label"]
    # the fixture's hall door is flag-gated — open it the replayable way
    assert st.append("mad-manse", chat, {"event": "state_set", "key": "chest_opened",
                                         "value": True, "turn": 1})
    msg, ok = session.act(None, "move", label, session=chat)
    assert ok, msg
    s3, state3 = session.load_active(chat)
    assert state3["room"] == dest
    assert state3["flags"].get("arrived") is True and state3["flags"].get("dread") == 1
    # {_clear} strips the shipped block; None drops the override
    assert session.set_room_enter(chat, "mad-manse", dest, {"_clear": True})[1]
    s4, _ = session.load_active(chat)
    assert "on_enter" not in s4["rooms"][dest]
    assert session.set_room_enter(chat, "mad-manse", dest, None)[1]
    s5, _ = session.load_active(chat)
    assert s5["rooms"][dest].get("on_enter") == story["rooms"][dest].get("on_enter")
    # unknown keys refused; survives other layer writes (spread-forward)
    assert not session.set_room_enter(chat, "mad-manse", dest, {"teleport": 3})[1]


# ── editor CSV shapes through the engine ─────────────────────────────────────

def test_csv_answers_and_once_dice_shapes(story, cfg_store):
    chat = "bg-csv-chat"
    _begin(story, chat)
    room = story["meta"]["start"]
    session.upsert_user_object(chat, "mad-manse", room, "case", {
        "desc": "a brass case",
        "puzzle": {"riddle": "What am I?", "solutions": ["iceberg", "an iceberg"]},
        "interactions": {"force": {
            "aliases": ["break", "kick"],
            "roll": {"sides": 20, "beat": 21, "once": True,          # beat 21 on d20 = sure miss
                     "success": {"message": "It gives."},
                     "failure": {"message": "Nothing. Your shoulder aches."}}}}})
    msg, ok = session.act(None, "solve", "case", answer="An Iceberg!", session=chat)
    assert ok, msg
    msg, ok = session.act(None, "kick", "case", session=chat)      # alias → roll → miss line
    assert "Your shoulder aches" in msg
    msg, ok = session.act(None, "force", "case", session=chat)     # once → spent
    assert not ok and "spent" in msg
