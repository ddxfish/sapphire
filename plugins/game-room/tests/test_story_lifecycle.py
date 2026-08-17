# Story LIFECYCLE tests — start/end/set_mode/session targeting.
#
# The 2026-08-05 war campaign found zero coverage here (Tier 5, front D) on
# the very code Tier 1 was about to re-cut. These lock the session-targeting
# contract (finding 1.1): a call naming a session must never touch the live
# chat's costume or cockpit.
import sys
import types
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PLUGIN_DIR))

from gameroom_story import rooms, session as sess, state as st  # noqa: E402


class FakeFunctionManager:
    def __init__(self):
        self.calls = []

    def update_enabled_functions(self, names, extra_toolsets=None):
        self.calls.append((list(names), list(extra_toolsets or [])))

    def get_enabled_function_names(self):
        return ["story_act"]


class FakeSessionManager:
    """Two chats, one of them live. Mirrors the real API surface the story
    engine touches: effective-chat resolution + by-name settings writes."""

    def __init__(self, live="main"):
        self.active_chat_name = live
        self.settings = {"main": {"prompt": "sapphire", "toolset": "all"},
                         "story-chat": {"prompt": "sapphire", "toolset": "all"}}

    def _effective_chat_name(self):
        return self.active_chat_name

    def get_active_chat_name(self):
        return self.active_chat_name

    def get_settings_for(self, name):
        s = self.settings.get(name)
        return dict(s) if s is not None else None

    def get_chat_settings(self):
        return dict(self.settings[self.active_chat_name])

    def update_chat_settings(self, patch):
        self.settings[self.active_chat_name].update(patch)
        return True

    def set_named_chat_settings(self, name, patch, touch_updated=True):
        if name not in self.settings:
            return False
        self.settings[name].update(patch)
        return True


@pytest.fixture
def system(monkeypatch):
    sm = FakeSessionManager()
    fm = FakeFunctionManager()
    sys_obj = types.SimpleNamespace(
        llm_chat=types.SimpleNamespace(session_manager=sm, function_manager=fm))
    return sys_obj


@pytest.fixture
def engine(tmp_path, monkeypatch):
    """Story roots → the test pack; saves + sidecar → tmp (fixture hygiene:
    DYNAMIC_FILE was previously left pointing at the real user dir)."""
    monkeypatch.setattr(rooms, "_story_roots",
                        lambda: [Path(__file__).parent / "fixtures" / "stories"])
    # v1.3: journal storage bound hermetically by conftest's autouse guard.
    # Prompt registration is core-side; the lifecycle contract is what we test.
    # (chat param added 2026-08-05: costume names are playthrough-scoped now.)
    monkeypatch.setattr(sess, "_register_prompt", lambda story, state, entry, chat:
                        (entry or {}).get("prompt_name") or "story_goblin-den")
    monkeypatch.setattr(sess, "_restore_pack", lambda: None)
    return tmp_path


def test_start_targets_named_session_not_live_chat(system, engine):
    msg, ok = sess.start(system, "goblin-den", session="story-chat")
    assert ok, msg
    sm = system.llm_chat.session_manager
    # The story landed on story-chat...
    assert st.get_active().get("story-chat")
    assert sm.settings["story-chat"]["toolset"] == "none"
    # Playthrough-scoped costume name (Krem's ruling 2026-08-05)
    assert sm.settings["story-chat"]["prompt"] == "story_goblin-den@story-chat"
    # ...and the live chat is untouched: no costume, no cockpit, no runtime.
    assert st.get_active().get("main") is None
    assert sm.settings["main"] == {"prompt": "sapphire", "toolset": "all"}
    assert system.llm_chat.function_manager.calls == []


def test_start_on_live_chat_applies_runtime(system, engine):
    msg, ok = sess.start(system, "goblin-den", session="main")
    assert ok, msg
    sm = system.llm_chat.session_manager
    assert sm.settings["main"]["toolset"] == "none"
    assert sm.settings["main"]["extra_toolsets"] == [sess.STORY_TOOLS_MODULE]
    # Live chat → the enabled-tools runtime leg fires too.
    assert system.llm_chat.function_manager.calls == [
        (["none"], [sess.STORY_TOOLS_MODULE])]


def test_start_refuses_unknown_session(system, engine):
    msg, ok = sess.start(system, "goblin-den", session="ghost-chat")
    assert not ok and "ghost-chat" in msg
    assert st.get_active() == {}


def test_start_stashes_target_chats_own_clothes(system, engine):
    sm = system.llm_chat.session_manager
    sm.settings["story-chat"] = {"prompt": "falcon", "toolset": "mind",
                                 "extra_toolsets": ["x"]}
    sess.start(system, "goblin-den", session="story-chat")
    entry = st.get_active()["story-chat"]
    assert entry["prev_prompt"] == "falcon"        # not the live chat's 'sapphire'
    assert entry["prev_toolset"] == "mind"
    assert entry["prev_extras"] == ["x"]


def test_end_restores_only_with_return_prompt(system, engine):
    sess.start(system, "goblin-den", session="story-chat")
    sess.set_mode(system, return_prompt="falcon", session="story-chat")
    msg, ok = sess.end(system, session="story-chat")
    assert ok, msg
    sm = system.llm_chat.session_manager
    assert sm.settings["story-chat"]["prompt"] == "falcon"
    assert sm.settings["story-chat"]["toolset"] == "all"
    assert st.get_active().get("story-chat") is None
    assert sm.settings["main"]["prompt"] == "sapphire"     # live chat untouched


def test_end_without_return_prompt_keeps_costume(system, engine):
    sess.start(system, "goblin-den", session="story-chat")
    sess.end(system, session="story-chat")
    sm = system.llm_chat.session_manager
    # Krem's checkbox ruling: no return_prompt → the tale leaves a wearable
    # costume and the story cockpit stays. (Scoped name, ruling 2026-08-05.)
    assert sm.settings["story-chat"]["prompt"] == "story_goblin-den@story-chat"
    assert sm.settings["story-chat"]["toolset"] == "none"


def test_end_clears_active_last(system, engine, monkeypatch):
    """A throw during restore must leave the story recoverable (finding 4.8)."""
    sess.start(system, "goblin-den", session="story-chat")
    sess.set_mode(system, return_prompt="falcon", session="story-chat")
    monkeypatch.setattr(sess, "_restore_pack",
                        lambda: (_ for _ in ()).throw(RuntimeError("disk full")))
    with pytest.raises(RuntimeError):
        sess.end(system, session="story-chat")
    assert st.get_active().get("story-chat"), "save must survive a failed end()"


def test_act_uses_effective_chat_when_no_session(system, engine):
    """Her tool call carries no session — it must resolve to the chat the
    turn actually runs in, not the global active chat (finding 1.2)."""
    sess.start(system, "goblin-den", session="story-chat")
    system.llm_chat.session_manager.active_chat_name = "story-chat"
    msg, ok = sess.act(system, "look", "pallet")
    assert ok, msg
    system.llm_chat.session_manager.active_chat_name = "main"
    msg, ok = sess.act(system, "look", "pallet")
    assert not ok and "No story is active" in msg


def test_two_sessions_do_not_collide(system, engine):
    sm = system.llm_chat.session_manager
    sm.settings["second"] = {"prompt": "sapphire", "toolset": "all"}
    sess.start(system, "goblin-den", session="story-chat")
    sess.start(system, "goblin-den", session="second")
    active = st.get_active()
    assert set(active) == {"story-chat", "second"}
    sess.end(system, session="story-chat")
    assert set(st.get_active()) == {"second"}
