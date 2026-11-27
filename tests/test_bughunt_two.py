"""The bug hunt on the bug hunt (2026-11-26): seven scouts over the five vault
waves. These are the side-door and edge fixes that have no better home; the
memory, vault, token and web fixes are tested beside the code they changed.
"""
import inspect
import threading
import types
from unittest.mock import MagicMock

import pytest

import core.api_fastapi as af
import core.prompt_vault as pv


# ── calls and voice channels use ordinary chats ──

def _manager(settings_for):
    from core.conversation.manager import ConversationManager
    m = ConversationManager.__new__(ConversationManager)
    sm = types.SimpleNamespace(get_settings_for=settings_for, get_chat_settings=lambda: settings_for(None))
    m.system = types.SimpleNamespace(llm_chat=types.SimpleNamespace(session_manager=sm))
    m._external_lock = threading.Lock()
    m.external = {}
    m._build_driver = MagicMock(side_effect=RuntimeError("reached the build: the door let it in"))
    return m


def test_a_call_into_a_private_chat_is_turned_away_at_the_door():
    """A call rule or a voice channel pointed at a private chat: its audio
    crosses a carrier, and its replies would draw on the chat's history."""
    m = _manager(lambda name: {'private_chat': True})
    assert m.start_external(lambda d, g: None, chat_name='bedroom', source_label='twilio') is None
    m._build_driver.assert_not_called()


def test_a_call_into_a_chat_that_cannot_be_read_is_turned_away_too():
    m = _manager(lambda name: None)
    assert m.start_external(lambda d, g: None, chat_name='sealed', source_label='twilio') is None
    m._build_driver.assert_not_called()


def test_a_call_into_an_ordinary_chat_goes_on_to_be_built():
    m = _manager(lambda name: {'private_chat': False})
    try:
        m.start_external(lambda d, g: None, chat_name='desk', source_label='twilio')
    except RuntimeError as e:
        assert 'reached the build' in str(e)
    m._build_driver.assert_called_once()


def test_the_call_drivers_speech_gate_fails_closed():
    from core.conversation import driver
    src = inspect.getsource(driver)
    turn = src.split('def _run_turn', 1)[1].split('message_id = uuid', 1)[0]
    assert "_settings = {'private_chat': True}" in turn, "cannot read = judged as private"
    assert '_gate = "voice privacy could not be checked"' in turn, "a failed check refuses the turn"
    assert "_gate = ''" not in turn


# ── remote MCP servers are the network ──

def test_a_far_mcp_servers_tools_say_they_are_the_network():
    from plugins.mcp_client import daemon
    assert daemon._far_server({'type': 'sse', 'url': 'https://mcp.example.com/sse'}) is True
    assert daemon._far_server({'type': 'http', 'url': 'http://192.168.1.20:9000/mcp'}) is False
    assert daemon._far_server({'type': 'http', 'url': 'http://localhost:9000/mcp'}) is False
    assert daemon._far_server({'type': 'stdio', 'command': 'npx'}) is False
    assert daemon._far_server({'command': 'npx'}) is False, "no type = stdio"
    assert daemon._far_server({'type': 'http', 'url': ''}) is True, "an address that cannot be judged is far"
    assert '"is_local": False' in inspect.getsource(daemon._register_tools)


# ── a task that cannot read its chat ──

def test_a_task_does_not_run_as_if_an_unreadable_chat_were_ordinary():
    from core.continuity import executor
    src = inspect.getsource(executor)
    assert 'read_chat_settings(target_chat) or {}' not in src
    after = src.split('chat_settings = session_manager.read_chat_settings(target_chat)', 1)[1][:700]
    assert 'if chat_settings is None:' in after and 'raise RuntimeError' in after
    assert 'task_settings["chat_private"] = bool(chat_settings.get("private_chat"))' in src


# ── a game seat whose session cannot be read ──

def test_an_unreadable_game_session_is_held_to_local_models(monkeypatch):
    from plugins import __path__ as _  # noqa: F401  (the plugins package is importable in tests)
    import importlib
    core = importlib.import_module('plugins.game-room.gameroom_core')
    src = inspect.getsource(core)
    branch = src.split("if not isinstance(s, dict):", 1)[1].split('return cfg', 1)[0]
    assert "cfg['privacy_required'] = True" in branch


# ── a sealed room, a private clip, a private command ──

def test_a_sealed_room_reads_as_a_name_that_was_never_a_room(monkeypatch):
    from core import rooms
    rt = types.SimpleNamespace(running=True, round=3, turn='a', waiting=False, skip=0, since_human=1,
                               rounds_run=9, heard='speaker', doors={})
    monkeypatch.setitem(rooms._rooms, 'secret-room', rt)
    monkeypatch.setattr(rooms, '_sm', lambda: types.SimpleNamespace(is_chat_hidden=lambda c: True))
    blank = rooms.state('secret-room')
    assert blank['rounds_run'] == 0 and blank['heard'] is None and blank['running'] is False
    monkeypatch.setattr(rooms, '_sm', lambda: types.SimpleNamespace(is_chat_hidden=lambda c: False))
    assert rooms.state('secret-room')['rounds_run'] == 9


def test_a_private_chats_clip_is_never_kept_by_the_browser():
    import importlib
    api = importlib.import_module('plugins.comfyui.routes.api')
    src = inspect.getsource(api.file)
    assert "'no-store' if" in src and 'PRIVATE_MARK' in src


def test_commands_run_in_a_private_chat_are_logged_by_size_only():
    import importlib
    for mod, marker in (('plugins.ssh.tools.ssh_tool', 'SSH ['), ('plugins.coding-harness.tools.harness', 'HARNESS [')):
        src = inspect.getsource(importlib.import_module(mod))
        line = [l for l in src.splitlines() if marker in l and 'logger.info' in l][0]
        assert 'log_safe.words(' in line, mod


# ── the idle clock and the open chat ──

def test_a_chat_cannot_be_made_private_while_a_turn_is_running_on_it():
    """A turn picks its provider and tools once, at its start: a cloud turn
    already under way kept going to the cloud after the flip (Krem voted
    refuse until idle)."""
    from core.chat.chat import LLMChat
    llm = object.__new__(LLMChat)
    llm._streams_lock = threading.Lock()
    live = types.SimpleNamespace(llm_done=False, cancel_flag=False, writes_done=False)
    tail = types.SimpleNamespace(llm_done=True, cancel_flag=False, writes_done=True)
    llm._streams_by_id = {'a': live, 'b': tail}
    llm._streams_by_chat = {'desk': {'a'}, 'den': {'b'}}
    assert llm.chat_busy('desk') is True
    assert llm.chat_busy('den') is False, "an audio tail does not hold the chat"
    assert llm.chat_busy('nowhere') is False
    import core.routes.chat as routes
    src = inspect.getsource(routes)
    assert "if new_settings.get('private_chat') is True:" in src
    assert 'Make it private once it has finished.' in src


def test_a_sealed_chat_still_open_serves_nothing(monkeypatch):
    """Locked, and a turn that would not stop still holds the pointer on the
    private chat: its rows are decrypted in memory, and are not handed out."""
    import core.chat.history as hist
    sm = object.__new__(hist.ChatSessionManager)
    sm.current_settings = {'private_chat': True}
    sm.current_chat = types.SimpleNamespace(get_messages_for_display=lambda: [{'role': 'user', 'content': 'x'}])
    monkeypatch.setattr(hist, '_vault_sealed', lambda: True)
    assert sm.pointer_sealed() is True and sm.get_messages_for_display() == []
    monkeypatch.setattr(hist, '_vault_sealed', lambda: False)
    assert sm.pointer_sealed() is False and len(sm.get_messages_for_display()) == 1
    sm.current_settings = {'private_chat': False}
    monkeypatch.setattr(hist, '_vault_sealed', lambda: True)
    assert sm.pointer_sealed() is False


# ── an agent from a private chat is stopped by a lock ──

def test_an_agent_spawned_from_a_private_chat_holds_a_lease_and_is_stopped_by_a_seal(monkeypatch):
    from core.agents.base import Agent as BaseAgent
    held, released = [], []

    def lease(cancel=None):
        held.append(cancel)
        return 'token'
    monkeypatch.setattr(pv, 'lease', lease)
    monkeypatch.setattr(pv, 'release', lambda t: released.append(t))
    ran = []
    engine = types.SimpleNamespace(install_carrier=lambda a: None, release_carrier=lambda t: None,
                                   finished=lambda a: None)
    agent = BaseAgent({'id': 'a1', 'name': 'scout', 'kind': 'llm', 'mission': 'm', 'privacy': True}, engine)
    agent.status = 'running'
    agent.run = lambda mission: ran.append(mission)
    agent._run_wrapper()
    assert ran == ['m'] and released == ['token']
    assert held and held[0] == agent.stop, "the seal's stop is the agent's own stop"

    # an ordinary chat's agent takes no lease
    held.clear(); released.clear()
    agent.privacy, agent.status = False, 'running'
    agent._run_wrapper()
    assert held == [] and released == []

    # the vault is locking right now: it does not start
    def sealing(cancel=None):
        raise pv.Sealing()
    monkeypatch.setattr(pv, 'lease', sealing)
    ran.clear()
    agent.privacy, agent.status = True, 'running'
    agent._run_wrapper()
    assert ran == [] and agent.status == 'stopped'


# ── tools at the MCP door while a private chat is open ──

def test_a_tool_at_the_mcp_door_runs_with_no_chat_while_a_private_chat_is_open():
    from core import mcp_server
    src = inspect.getsource(mcp_server.call)
    assert "get('private_chat') or sm.pointer_sealed()" in src
    assert 'make_ephemeral_override' in src and 'reset_override(carried)' in src
