"""get_self_info reports THIS turn's world (2026-10-28): on Discord she read
"80 tools" - the operator's web chat palette - and the web chat's name and
prompt; uptime read 0; the update line read a file nothing writes."""
import time
from unittest.mock import MagicMock

import pytest

import core.api_fastapi  # noqa: F401  (route modules import the app)
from plugins.status.routes import status as S


@pytest.fixture
def system(monkeypatch):
    sysm = MagicMock()
    sm = sysm.llm_chat.session_manager
    sm.get_chat_settings.return_value = {"prompt": "web-prompt", "private_chat": False}
    sm.get_active_chat_name.return_value = "web-chat"
    fm = sysm.llm_chat.function_manager
    fm._enabled_tools = [{"function": {"name": f"web_tool_{i}"}} for i in range(80)]
    fm.current_toolset_name = "sapphire"
    sysm.continuity_scheduler = None
    monkeypatch.setattr(core.api_fastapi, "get_system", lambda: sysm)
    return sysm


def test_a_discord_turn_reads_its_own_tools_chat_and_prompt(system):
    from core.chat.function_manager import turn_tools, tool_context
    t1 = turn_tools.set(frozenset({"get_self_info", "discord_send"}))
    t2 = tool_context.set({"channel": "continuity", "chat": "discord-chat", "persona": "discord-prompt",
                           "toolset": "discord"})
    try:
        s = S.get_full_status_sync(scope=None)["session"]
    finally:
        turn_tools.reset(t1)
        tool_context.reset(t2)
    assert s["function_count"] == 2 and s["tool_names"] == ["discord_send", "get_self_info"]
    assert s["toolset"] == "discord" and s["chat"] == "discord-chat" and s["prompt"] == "discord-prompt"
    assert s["channel"] == "continuity"


def test_the_web_chat_still_reads_the_live_palette(system):
    s = S.get_full_status_sync(scope=None)["session"]
    assert s["function_count"] == 80 and s["chat"] == "web-chat" and s["prompt"] == "web-prompt"


def test_uptime_is_the_process_age_not_the_modules(system, monkeypatch):
    import psutil
    monkeypatch.setattr(S, "_boot_time", time.time())          # a fresh re-import of the module
    up = S.get_full_status_sync(scope=None)["identity"]["uptime_seconds"]
    assert abs(up - (time.time() - psutil.Process().create_time())) < 5 and up >= 0


def test_the_update_line_reads_the_updater(system, monkeypatch):
    from core import updater
    monkeypatch.setattr(updater.updater, "update_available", True, raising=False)
    monkeypatch.setattr(updater.updater, "latest_version", "9.9.9", raising=False)
    u = S.get_full_status_sync(scope=None)["update"]
    assert u["available"] is True and u["latest_version"] == "9.9.9"
