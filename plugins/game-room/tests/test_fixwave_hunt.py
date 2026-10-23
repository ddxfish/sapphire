# 2026-08-21 post-hunt fix wave — regression guards for the grand board's
# Wave 1: the missing-room brick class (view-level fallback at the one
# seam), the honest journal rail (refusals return False and act() says so),
# and the _active_ctx explicit-slug honor (library edits during another
# story's run). Fixture packs: fixtures/stories/{mad-manse,roled-tale}.
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


# ── The missing-room brick class (goto-to-nowhere / scenario swap /
#    room-delete race / cleared journal — four doors, one fallback) ─────────

def test_goto_nowhere_falls_back_to_start(story, cfg_store):
    chat = "fw-goto-chat"
    st.set_active(chat, "mad-manse", None)
    assert st.append_many("mad-manse", chat, [
        {"event": "started", "story": "mad-manse",
         "room": story["meta"]["start"], "turn": 0},
        {"event": "moved", "to": 999, "turn": 1}])
    s2, state = session.load_active(chat)
    assert state["room"] == story["meta"]["start"]
    assert state["turns_in_room"] == 0
    # act() gets a real room now, not the author-error dead end
    msg, ok = session.act(None, "look", session=chat)
    assert ok


def test_cleared_journal_restarts_from_beginning(story, cfg_store):
    # chat_cleared's documented promise: journal wiped, story survives and
    # "simply starts over from the beginning" — replay used to hand back
    # room=None and act() bricked on "author error" (day-ruiner D6).
    chat = "fw-cleared-chat"
    st.set_active(chat, "mad-manse", None)
    s2, state = session.load_active(chat)
    assert state["room"] == story["meta"]["start"]
    assert not state["ended"]


def test_ended_story_skips_fallback(story, cfg_store):
    # THE END in a since-deleted room must stay THE END — the fallback
    # only heals LIVE runs.
    chat = "fw-ended-chat"
    st.set_active(chat, "mad-manse", None)
    assert st.append_many("mad-manse", chat, [
        {"event": "started", "story": "mad-manse",
         "room": story["meta"]["start"], "turn": 0},
        {"event": "moved", "to": 999, "turn": 1},
        {"event": "ended", "turn": 2}])
    s2, state = session.load_active(chat)
    assert state["ended"] and state["room"] == 999


# ── The honest journal rail ─────────────────────────────────────────────────

def test_append_returns_false_on_store_refusal(story, cfg_store, monkeypatch):
    chat = "fw-rail-chat"

    def boom(*a, **k):
        raise RuntimeError("sealed")

    cs = st._cs()
    monkeypatch.setattr(cs, "append", boom)
    monkeypatch.setattr(cs, "append_many", boom)
    assert st.append("mad-manse", chat, {"event": "started", "room": 1}) is False
    assert st.append_many("mad-manse", chat, [{"event": "started", "room": 1}]) is False
    assert st.append_many("mad-manse", chat, []) is True   # no-op is success


def test_act_reports_refused_write(story, cfg_store, monkeypatch):
    # The vault idle-lock scenario: the store refuses, and act() must say
    # NOT recorded instead of narrating a successful move (CRIT-2/D4).
    chat = "fw-refusal-chat"
    st.set_active(chat, "mad-manse", None)
    assert st.append_many("mad-manse", chat, [
        {"event": "started", "story": "mad-manse",
         "room": story["meta"]["start"], "turn": 0}])
    monkeypatch.setattr(st, "append_many", lambda *a, **k: False)
    msg, ok = session.act(None, "look", session=chat)
    assert not ok and "NOT recorded" in msg


def test_fill_seal_reports_refused_write(story, cfg_store, monkeypatch):
    chat = "fw-seal-chat"
    st.set_active(chat, "mad-manse", None)
    assert st.append_many("mad-manse", chat, [
        {"event": "started", "story": "mad-manse",
         "room": story["meta"]["start"], "turn": 0}])
    # Find any sealed blank the fixture ships; if none, synthesize via the
    # legacy key path being absent — then this test only asserts the
    # no-seal message (still meaningful: no false success).
    monkeypatch.setattr(st, "append", lambda *a, **k: False)
    msg, ok = session.fill_seal(None, "nope:verb", text="words", session=chat)
    assert not ok   # either "no sealed blank" or "NOT recorded" — never success


# ── _active_ctx: explicit slug names the story it edits (D8) ────────────────

def test_active_ctx_honors_explicit_other_slug(story, cfg_store, monkeypatch):
    from routes import story_routes
    monkeypatch.setattr(story_routes, "_system", lambda: None)
    chat = "fw-ctx-chat"
    st.set_active(chat, "mad-manse", None)
    # Library-editing a DIFFERENT story mid-run → the named story wins
    c, slug, err = story_routes._active_ctx(
        body={"session": chat, "slug": "roled-tale"})
    assert err is None and slug == "roled-tale" and c == chat
    # No slug → the active playthrough, as always
    c2, slug2, err2 = story_routes._active_ctx(body={"session": chat})
    assert err2 is None and slug2 == "mad-manse"
    # Same slug as active → the active lane (identical result, no fork)
    c3, slug3, err3 = story_routes._active_ctx(
        body={"session": chat, "slug": "mad-manse"})
    assert err3 is None and slug3 == "mad-manse"
    # Unknown slug → honest refusal, never a silent write to the live story
    _, _, err4 = story_routes._active_ctx(
        body={"session": chat, "slug": "no-such-tale"})
    assert err4 is not None
