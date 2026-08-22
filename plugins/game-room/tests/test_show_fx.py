# Show-image fx / lightbox (2026-08-22): {"show": ...} in any effects block
# journals a `shown` event; full_state carries the latest as shown_last for
# the client's one-shot popup, and act() tells her an illustration fired
# (she can't see it — yet). Fixture pack: fixtures/stories/mad-manse, whose
# backdrops/lightbox-test.webp anchors URL resolution.
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).parent.parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from gameroom_story import referee, rooms, session, state as st  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "stories"
ART = "lightbox-test.webp"


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


# ── The effect key: two shapes in, junk ignored ─────────────────────────────

def test_effect_events_show_shapes():
    state = {"inventory": []}
    evs = referee._effect_events({"show": ART}, state)
    assert evs == [{"event": "shown", "image": ART, "caption": ""}]
    evs = referee._effect_events(
        {"show": {"image": ART, "caption": "Gold everywhere"}}, state)
    assert evs == [{"event": "shown", "image": ART, "caption": "Gold everywhere"}]
    # junk shapes never crash a turn and never emit
    for junk in (7, [], {"caption": "no image"}, "", "   ", {"image": ""}):
        assert referee._effect_events({"show": junk}, state) == []


# ── The fold: accumulates in order, revert un-shows ─────────────────────────

def test_fold_accumulates_and_truncate_unshows(story, cfg_store):
    chat = "show-fold-chat"
    _begin(story, chat)
    assert st.append_many("mad-manse", chat, [
        {"event": "shown", "image": ART, "caption": "one", "turn": 1},
        {"event": "shown", "image": ART, "caption": "two", "turn": 2}])
    state = st.replay("mad-manse", chat)
    assert [s["caption"] for s in state["shown"]] == ["one", "two"]
    assert state["shown"][-1]["turn"] == 2
    st.truncate("mad-manse", chat, 1)
    assert [s["caption"] for s in st.replay("mad-manse", chat)["shown"]] == ["one"]


# ── End-to-end: act fires it, tells her, full_state serves the client ───────

def test_act_pops_and_reports(story, cfg_store):
    chat = "show-act-chat"
    _begin(story, chat)
    start = story["meta"]["start"]
    msg, ok = session.upsert_user_object(chat, "mad-manse", start, "gold_chest", {
        "desc": "A chest.",
        "interactions": {"open": {"message": "It opens.",
                                  "show": {"image": ART,
                                           "caption": "Gold everywhere"}}}})
    assert ok, msg
    msg, ok = session.act(None, "open", "gold_chest", session=chat)
    assert ok
    assert "(The player is shown an illustration: Gold everywhere.)" in msg
    full = session.full_state(None, session=chat)
    sl = full["shown_last"]
    assert sl and sl["seq"] == 1 and sl["caption"] == "Gold everywhere"
    assert sl["url"] and ART in sl["url"]
    # a second act bumps seq — the client's one-shot dedup counter
    msg2, ok2 = session.act(None, "open", "gold_chest", session=chat)
    assert ok2
    assert session.full_state(None, session=chat)["shown_last"]["seq"] == 2


def test_string_show_reports_without_caption(story, cfg_store):
    chat = "show-str-chat"
    _begin(story, chat)
    session.upsert_user_object(chat, "mad-manse", story["meta"]["start"], "mirror", {
        "desc": "A mirror.",
        "interactions": {"touch": {"message": "Cold glass.", "show": ART}}})
    msg, ok = session.act(None, "touch", "mirror", session=chat)
    assert ok and msg.endswith("(The player is shown an illustration.)")


def test_shown_last_unresolvable_is_none(story, cfg_store):
    # a name that is neither a store hash nor a pack backdrop → no popup,
    # never a broken img
    chat = "show-miss-chat"
    _begin(story, chat)
    assert st.append("mad-manse", chat,
                     {"event": "shown", "image": "no-such-file.webp",
                      "caption": "", "turn": 1})
    assert session.full_state(None, session=chat)["shown_last"] is None
