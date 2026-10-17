# Sealed blanks through the editor (Krem 2026-08-23): the "ask the player
# (popup)" Effects row compiles to the verb's `sealed` block, the response
# box carries the hold message, and a riddle on the object + "riddle solved"
# Requirement gates the popup. This is the exact spec settings-modal.js
# emits for the mansion chest after Krem's intended edit (riddle password
# → open → popup), played end to end through the real engine:
# blocked → solved → held → player writes → revealed + effect.
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).parent.parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from gameroom_story import rooms, session, state as st  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "stories"

# compileObj output for: desc + riddle + one action card "open" whose
# Requirements = riddle solved (+ blocked message), Effects = ask the player
# (question, fallback) + set flag; response box = hold message. `wait` and
# `aliases` are passengers from the shipped chest.
EDITOR_CHEST = {
    "_replace": True,
    "desc": "The one box in this room that will not open.",
    "puzzle": {"riddle": "What word opens me?", "solution": "marmalade"},
    "interactions": {
        "open": {
            "condition": {"solved": "the_chest"},
            "blocked_message": "The lid will not budge — it wants a word.",
            "sealed": {
                "ask": "The chest gives way. What is inside it?",
                "hold_message": "The lid shifts a fraction, then settles back.",
                "fallback": "a bundle of letters",
                "wait": 240,
            },
            "set": {"chest_opened": True},
            "aliases": ["unlock", "force"],
        }
    },
}


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


@pytest.fixture(autouse=True)
def clean_module_state():
    session._seal_waits.clear()
    session._presence.clear()
    yield
    session._seal_waits.clear()
    session._presence.clear()


def _begin(story, chat):
    st.set_active(chat, "mad-manse", None)
    assert st.append_many("mad-manse", chat, [
        {"event": "started", "story": "mad-manse",
         "room": story["meta"]["start"], "turn": 0}])


def test_riddle_gates_the_popup_then_player_writes_the_reveal(story, cfg_store):
    chat = "seal-editor-chat"
    _begin(story, chat)
    room = story["meta"]["start"]
    msg, ok = session.upsert_user_object(chat, "mad-manse", room, "the_chest", EDITOR_CHEST)
    assert ok, msg

    # 1. Requirement first: the riddle stands between her and the popup
    msg, ok = session.act(None, "open", "the_chest", session=chat)
    assert not ok and "wants a word" in msg
    # alias rides as a passenger — same gate
    msg, ok = session.act(None, "force", "the_chest", session=chat)
    assert not ok and "wants a word" in msg

    # 2. Solve it
    msg, ok = session.act(None, "solve", "the_chest", answer="Marmalade", session=chat)
    assert ok, msg

    # 3. Now the seal: no player in the room → honest hold with the
    #    editor's hold message (the response box), flag NOT set yet
    msg, ok = session.act(None, "open", "the_chest", session=chat)
    assert not ok and "settles back" in msg
    s2, state = session.load_active(chat)
    assert not state["flags"].get("chest_opened")
    # the popup payload carries the editor's ask + fallback
    full = session.full_state(None, session=chat)
    seal = next(s for s in full["seals"] if s["object"] == "the_chest")
    assert seal["ask"] == "The chest gives way. What is inside it?"
    assert seal["has_fallback"] and not seal["filled"]

    # 4. Player writes → the reveal is canon and the effect fires at reveal
    msg, ok = session.fill_seal(None, seal["key"], text="a map of the lake, inked by hand",
                                session=chat)
    assert ok, msg
    msg, ok = session.act(None, "open", "the_chest", session=chat)
    assert ok and "Revealed for the first time" in msg
    assert "a map of the lake" in msg
    s3, state3 = session.load_active(chat)
    assert state3["flags"].get("chest_opened") is True


def test_skip_takes_the_editor_fallback(story, cfg_store):
    chat = "seal-editor-skip"
    _begin(story, chat)
    room = story["meta"]["start"]
    # same card, no riddle and no Requirement — popup straight away
    open_ = {k: v for k, v in EDITOR_CHEST["interactions"]["open"].items()
             if k not in ("condition", "blocked_message")}
    spec = {k: v for k, v in EDITOR_CHEST.items() if k != "puzzle"}
    spec["interactions"] = {"open": open_}
    msg, ok = session.upsert_user_object(chat, "mad-manse", room, "the_chest", spec)
    assert ok, msg
    msg, ok = session.act(None, "open", "the_chest", session=chat)
    assert not ok and "settles back" in msg
    full = session.full_state(None, session=chat)
    key = next(s["key"] for s in full["seals"] if s["object"] == "the_chest")
    msg, ok = session.fill_seal(None, key, skip=True, session=chat)
    assert ok, msg
    msg, ok = session.act(None, "open", "the_chest", session=chat)
    assert ok and "a bundle of letters" in msg and "author's line" in msg
