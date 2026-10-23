# Live seal wait (Krem 2026-08-06) — act() blocks its tool-call thread while
# the player writes the blank, then re-resolves so the REVEAL is the tool
# result. These tests drive the real act()/fill_seal() pair across threads.
# Run: conda run -n sapphire python -m pytest plugins/game-room/tests/ -q
import sys
import threading
import time
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PLUGIN_DIR))

from gameroom_story import rooms, session, state as st  # noqa: E402

CHAT = "seal-wait-test"
KEY = "1:chest:open"


@pytest.fixture
def sealed_story(monkeypatch):
    """goblin-den with a sealed chest grafted into room 1, injected at the
    loader so load_active() (which reloads from disk every act) sees it."""
    monkeypatch.setattr(rooms, "_story_roots",
                        lambda: [Path(__file__).parent / "fixtures" / "stories"])
    real_load = rooms.load_story

    def load_with_seal(slug):
        story = real_load(slug)
        r1 = story["rooms"][1]
        r1["objects"] = dict(r1.get("objects") or {})
        r1["objects"]["chest"] = {
            "desc": "a barnacled sea chest",
            "interactions": {"open": {"sealed": {"ask": "What's inside?",
                                                 "fallback": "old boots"}}}}
        return story

    monkeypatch.setattr(rooms, "load_story", load_with_seal)
    return load_with_seal("goblin-den")


@pytest.fixture
def tmp_saves(tmp_path):
    # v1.3: journal storage bound hermetically by conftest's autouse guard.
    return tmp_path


@pytest.fixture(autouse=True)
def clean_module_state():
    session._seal_waits.clear()
    session._presence.clear()
    yield
    session._seal_waits.clear()
    session._presence.clear()


def _begin(tmp_saves):
    """An active goblin-den playthrough standing in room 1."""
    st.set_active(CHAT, "goblin-den", None)
    st.append("goblin-den", CHAT, {"event": "moved", "to": 1, "turn": 0})


def _act_in_thread():
    out = {}

    def run():
        out["msg"], out["ok"] = session.act(None, "open", "chest", session=CHAT)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, out


def _await_wait_registered():
    for _ in range(150):
        if session._seal_wait_payload(CHAT):
            return
        time.sleep(0.02)
    pytest.fail("live wait never registered")


# ── The headline: fill mid-block → reveal IS the tool result ────────────────

def test_act_blocks_then_reveals_players_words(sealed_story, tmp_saves, monkeypatch):
    _begin(tmp_saves)
    session._stamp_presence(CHAT)
    monkeypatch.setattr(session, "_wait_secs", lambda story, held: 10)
    t, out = _act_in_thread()
    _await_wait_registered()
    msg, ok = session.fill_seal(None, KEY, text="a brass key, still warm", session=CHAT)
    assert ok, msg
    t.join(timeout=5)
    assert not t.is_alive(), "act never woke after the fill"
    assert out["ok"]
    assert "a brass key, still warm" in out["msg"]
    assert "written by the player just now" in out["msg"]
    assert not session._seal_wait_payload(CHAT), "wait must unregister"


def test_act_blocks_then_skip_fires_fallback(sealed_story, tmp_saves, monkeypatch):
    _begin(tmp_saves)
    session._stamp_presence(CHAT)
    monkeypatch.setattr(session, "_wait_secs", lambda story, held: 10)
    t, out = _act_in_thread()
    _await_wait_registered()
    msg, ok = session.fill_seal(None, KEY, skip=True, session=CHAT)
    assert ok, msg
    t.join(timeout=5)
    assert not t.is_alive()
    assert out["ok"] and "old boots" in out["msg"] and "author's line" in out["msg"]


# ── Degradation ladder: every rung is the shipped hold ──────────────────────

def test_timeout_returns_the_hold(sealed_story, tmp_saves, monkeypatch):
    _begin(tmp_saves)
    session._stamp_presence(CHAT)
    monkeypatch.setattr(session, "_wait_secs", lambda story, held: 0.3)
    start = time.monotonic()
    msg, ok = session.act(None, "open", "chest", session=CHAT)
    elapsed = time.monotonic() - start
    assert not ok and "hasn't" in msg          # the honest hold, verbatim
    assert 0.25 < elapsed < 5
    assert not session._seal_wait_payload(CHAT)


def test_no_player_in_room_means_no_block(sealed_story, tmp_saves):
    _begin(tmp_saves)                           # presence never stamped
    start = time.monotonic()
    msg, ok = session.act(None, "open", "chest", session=CHAT)
    assert not ok and "hasn't" in msg
    assert time.monotonic() - start < 1, "must not wait for an empty room"


def test_pause_mid_wait_bails(sealed_story, tmp_saves, monkeypatch):
    _begin(tmp_saves)
    session._stamp_presence(CHAT)
    monkeypatch.setattr(session, "_wait_secs", lambda story, held: 30)
    t, out = _act_in_thread()
    _await_wait_registered()
    st.update_active(CHAT, paused=True)
    t.join(timeout=5)
    assert not t.is_alive(), "pause must release the wait"
    assert not out["ok"]


def test_fill_landing_before_registration_wakes_first_slice(sealed_story, tmp_saves):
    _begin(tmp_saves)
    st.append("goblin-den", CHAT, {"event": "sealed", "key": KEY,
                                   "text": "pre-filled", "turn": 1})
    room = rooms.load_story("goblin-den")["rooms"][1]
    start = time.monotonic()
    assert session._wait_for_fill("goblin-den", CHAT, room, "chest", "open", KEY, 10)
    assert time.monotonic() - start < 2, "already-filled must return immediately"


# ── Presence + extension plumbing ───────────────────────────────────────────

def test_presence_window():
    assert not session._present(CHAT)
    session._stamp_presence(CHAT)
    assert session._present(CHAT)
    session._presence[CHAT] -= session._PRESENCE_WINDOW + 1
    assert not session._present(CHAT)


def test_extend_bumps_and_caps():
    now = time.monotonic()
    session._seal_waits[(CHAT, KEY)] = {
        "event": threading.Event(), "deadline": now + 10, "cap": now + 200}
    r1 = session.extend_seal_wait(None, KEY, session=CHAT)
    assert r1 is not None and 60 < r1 <= 70       # +60 on the 10 left
    session._seal_waits[(CHAT, KEY)]["deadline"] = now + 195
    r2 = session.extend_seal_wait(None, KEY, session=CHAT)
    assert r2 is not None and r2 <= 200           # capped at the wait's ceiling
    assert session.extend_seal_wait(None, "no:such:seal", session=CHAT) is None


def test_wait_secs_precedence_and_clamp():
    story = {"meta": {}}
    assert session._wait_secs(story, {}) == 120
    assert session._wait_secs(story, {"wait": 30}) == 30
    assert session._wait_secs(story, {"wait": 2}) == 5          # floor
    assert session._wait_secs(story, {"wait": 9999}) == 600     # ceiling
    assert session._wait_secs({"meta": {"seal_wait": 200}}, {}) == 200
    assert session._wait_secs({"meta": {"seal_wait": 200}}, {"wait": 40}) == 40
    assert session._wait_secs(story, {"wait": "abc"}) == 120    # coercion falls through
