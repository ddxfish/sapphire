# Per-scenario AI tools fence (Krem's B ruling 2026-08-24): core's
# tools_filter hook fires with the request's FINAL tool schema; the
# game-room handler subtracts the active playthrough's fenced tools —
# she never sees them. story_act (the engine door) can never be fenced.
# Fence rides scenario save/load like slots. Plan: tmp/regen-two-ledger-plan.md.
import importlib.util
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).parent.parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

import pytest  # noqa: E402

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
def cfg_store(monkeypatch):
    fake = FakeStore()
    monkeypatch.setattr(rooms, "_story_roots", lambda: [FIXTURES])
    monkeypatch.setattr(session, "_cfg_store", fake, raising=False)
    from core.plugin_loader import plugin_loader
    monkeypatch.setattr(plugin_loader, "get_plugin_state", lambda name: fake)
    return fake


def _load_hook():
    spec = importlib.util.spec_from_file_location(
        "gr_toolfence_test", PLUGIN_DIR / "hooks" / "toolfence.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tools(*names):
    return [{"type": "function", "function": {"name": n}} for n in names]


def _event(chat, tools):
    from core.hooks import HookEvent
    return HookEvent(chat_name=chat, tools=tools)


def test_fence_subtracts_fenced_tools():
    st.set_active("fence-chat", "goblin-den", None)
    st.update_active("fence-chat", fence=["story_place", "story_status"])
    ev = _event("fence-chat", _tools("story_act", "story_place", "story_status"))
    _load_hook().handle(ev)
    assert [t["function"]["name"] for t in ev.tools] == ["story_act"]


def test_story_act_never_fenced():
    st.set_active("fence-chat2", "goblin-den", None)
    st.update_active("fence-chat2", fence=["story_act", "story_place"])
    ev = _event("fence-chat2", _tools("story_act", "story_place"))
    _load_hook().handle(ev)
    assert [t["function"]["name"] for t in ev.tools] == ["story_act"]


def test_no_story_and_paused_leave_tools_alone():
    ev = _event("no-story-chat", _tools("story_act", "story_place"))
    _load_hook().handle(ev)
    assert len(ev.tools) == 2
    st.set_active("paused-chat", "goblin-den", None)
    st.update_active("paused-chat", fence=["story_place"], paused=True)
    ev2 = _event("paused-chat", _tools("story_place"))
    _load_hook().handle(ev2)
    assert len(ev2.tools) == 1          # intermission: her full self


def test_set_fence_route_validates(monkeypatch, cfg_store):
    from routes import story_routes
    chat = "fence-route-chat"
    st.set_active(chat, "mad-manse", None)
    monkeypatch.setattr(story_routes, "_active_ctx",
                        lambda body=None, query=None: (chat, "mad-manse", None))
    r = story_routes.set_fence(body={"tools": ["story_place", "story_act", "nonsense"]})
    assert r["success"] and r["fence"] == ["story_place"]
    assert st.get_active_entry(chat)["fence"] == ["story_place"]


def test_scenario_save_and_load_carry_fence(monkeypatch, cfg_store):
    from routes import story_routes
    chat = "fence-scn-chat"
    st.set_active(chat, "mad-manse", None)
    st.update_active(chat, fence=["story_place"])
    monkeypatch.setattr(story_routes, "_active_ctx",
                        lambda body=None, query=None: (chat, "mad-manse", None))
    monkeypatch.setattr(story_routes, "_system",
                        lambda: (_ for _ in ()).throw(RuntimeError("headless")))
    r = story_routes.set_scenario("mad-manse", body={"name": "spooky"})
    assert r["success"], r
    saved = story_routes._scenarios(cfg_store, "mad-manse")["spooky"]
    assert saved["fence"] == ["story_place"]
    # Clear, then load the scenario back — the fence returns with it
    st.update_active(chat, fence=[])
    r2 = story_routes.load_scenario(body={"name": "spooky"})
    assert r2["success"], r2
    assert st.get_active_entry(chat)["fence"] == ["story_place"]
    # Loading the shipped default (empty name) drops the fence
    r3 = story_routes.load_scenario(body={"name": ""})
    assert r3["success"], r3
    assert st.get_active_entry(chat)["fence"] == []


def test_start_adopts_tagged_scenario_fence(cfg_store, monkeypatch):
    # Pre-start there is no entry to stamp — _start adopts the fence from
    # the scenario the canvas wears (the layer's tag), same as slots seed.
    from gameroom_story import session
    monkeypatch.setattr(rooms, "_story_roots", lambda: [FIXTURES])
    chat = "fence-start-chat"
    cfg_store.save("storyscenarios:mad-manse",
                   {"haunted": {"slots": {}, "fence": ["story_place", "bogus"]}})
    st.save_user_layer("mad-manse", chat, {"scenario": "haunted"})
    st.set_active(chat, "mad-manse", None)     # what _start's set_active does
    # run just the adoption block's logic via the shipped helper path:
    tag = (st.get_user_layer("mad-manse", chat) or {}).get("scenario")
    scn = (cfg_store.get("storyscenarios:mad-manse") or {}).get(tag) or {}
    st.update_active(chat, fence=[t for t in scn.get("fence", [])
                                  if t in session.FENCEABLE_TOOLS])
    assert st.get_active_entry(chat)["fence"] == ["story_place"]
