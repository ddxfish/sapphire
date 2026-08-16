"""Test-time containment for story/game state.

v1.3 (2026-08-15): playthroughs live in core's plugin_chat_data table, not
files — so the floor here is a per-test HERMETIC ChatSessionManager bound
into state.py's store accessor. No test can reach the real chat DB, and the
old forgot-one-path hazard (2026-08-05: a fixture that missed DYNAMIC_FILE
quarantined the live story costumes) is structurally gone — there is one
seam now, and this autouse guard owns it.

Chats auto-create on first write: in production the chat always exists (the
chat IS the save — story _start runs inside one), test chat names are
synthetic. rooms.SAVES_ROOT still redirects for the file-based generated-
rooms read path.
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PLUGIN_DIR = Path(__file__).parent.parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

TEST_DEFAULTS = {"prompt": "default"}


@pytest.fixture(autouse=True)
def hermetic_chat_store(tmp_path, monkeypatch):
    with patch("core.chat.history.get_system_defaults",
               side_effect=lambda: dict(TEST_DEFAULTS)), \
         patch("core.chat.history.get_user_defaults",
               side_effect=lambda: dict(TEST_DEFAULTS)):
        from core.chat.history import ChatSessionManager
        sm = ChatSessionManager(history_dir=str(tmp_path / "chatdb"))

    from core.plugin_loader import PluginChatState

    class AutoChatState(PluginChatState):
        _sm = staticmethod(lambda: sm)

        def put(self, chat, key, value):
            sm.create_chat(chat)
            return super().put(chat, key, value)

        def append(self, chat, key, value):
            sm.create_chat(chat)
            return super().append(chat, key, value)

        def replace(self, chat, key, values):
            sm.create_chat(chat)
            return super().replace(chat, key, values)

    cs = AutoChatState("game-room")

    from gameroom_story import rooms, state as st
    monkeypatch.setattr(st, "_CS", cs, raising=False)
    import gameroom_core as gc
    monkeypatch.setattr(gc, "chat_store", cs, raising=False)

    base = tmp_path / "story_saves"
    base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(rooms, "SAVES_ROOT", base, raising=False)
    yield sm
