"""The vault lease in the lanes that carry it (isolation hunt wave 2,
2026-11-26; core/prompt_vault.py "leases", tmp/vault-isolation-hunt-20261123.md).

tests/test_vault_isolation_hunt.py pins the lease itself (begin_stream takes
it, the seal stops or waits). Here: the turn engine hands it back when its
rows are written, only a person's turn restarts the vault's idle clock, and
the task lane can be stopped by a seal and says so when its rows are lost.
"""
from unittest.mock import MagicMock, patch

import pytest

import core.api_fastapi  # noqa: F401  (route modules trip circular imports alone)
import core.prompt_vault as pv
from test_dualpath_merge import _engine_run

REPLY = [{"type": "content", "text": "Hello"}, {"type": "done", "response": None}]


@pytest.fixture
def open_vault(monkeypatch):
    """An unlocked vault in memory only: enough for leases and the clock."""
    monkeypatch.setattr(pv, '_key', b'k' * 32)
    monkeypatch.setattr(pv, '_data', pv._normalize({}))
    monkeypatch.setattr(pv, '_leases', {})
    monkeypatch.setattr(pv, '_pending', False)
    monkeypatch.setattr(pv, '_sealing', False)
    pv._no_leases.set()
    yield
    pv._no_leases.set()


# ── the engine hands the lease back when the turn's rows are written ──

def test_the_turn_hands_its_lease_back_at_its_end(open_vault):
    token = pv.lease()
    assert pv.leases_held() == 1
    sc, out = _engine_run(REPLY, cancel_via=lambda s: setattr(s, 'vault_lease', token))
    assert any(e.get("type") == "final" for e in out if isinstance(e, dict))
    assert pv.leases_held() == 0 and sc.writes_done


def test_a_stopped_turn_hands_it_back_too(open_vault):
    token = pv.lease()

    def stopped(s):
        s.vault_lease = token
        s.cancel_flag = True
    _engine_run(REPLY, cancel_via=stopped)
    assert pv.leases_held() == 0


# ── only a person's turn restarts the idle clock ──

def _by_name(source, touched):
    from core.chat.history import ConversationHistory

    def setup(s):
        s.target_chat = "elsewhere"
        s.brain = {"chat": "elsewhere", "settings": {}, "system_prompt": "s",
                   "tools": [], "history": ConversationHistory(max_history=10)}
        if source is not None:
            s.turn_source = source
    with patch.object(pv, 'touch', side_effect=lambda: touched.append(source)):
        _engine_run(REPLY, cancel_via=setup)


@pytest.mark.parametrize("source", [None, "voice", "device:kitchen", "conversation"])
def test_a_persons_turn_by_name_restarts_the_idle_clock(open_vault, source):
    touched = []
    _by_name(source, touched)
    assert touched, "a satellite or phone turn is the person: the vault must not idle out under it"


@pytest.mark.parametrize("source", ["cadence", "continuity:heartbeat", "agent", "room"])
def test_her_own_lanes_do_not(open_vault, source):
    touched = []
    _by_name(source, touched)
    assert not touched, "a heartbeat on a private chat would keep the vault open for good"


# ── the task lane ──

def _task_system(appended):
    system = MagicMock()
    sm = system.llm_chat.session_manager
    sm.read_chat_settings.return_value = {"private_chat": True}
    sm.read_chat_messages.return_value = []
    sm.purge_mark.return_value = 7
    sm.append_messages_to_chat.return_value = appended
    hold = MagicMock()
    hold.cancel_flag = False
    system.llm_chat.begin_stream.return_value = (hold, "sid", "secret")
    return system, sm, hold


TASK = {"name": "nightly", "chat_target": "secret", "initial_message": "ping",
        "prompt": "default", "toolset": "none", "tts_enabled": False}


def _run(system, **kw):
    from core.continuity.executor import ContinuityExecutor
    ex = ContinuityExecutor(system)
    result = {"success": False, "errors": [], "responses": [], "iterations_completed": 0}
    with patch("core.continuity.execution_context.ExecutionContext") as EC:
        ctx = EC.return_value
        ctx.run.return_value = "reply"
        ctx.degraded_reason = None
        ctx.cancelled = False
        ctx.new_messages = [{"role": "user", "content": "ping"},
                            {"role": "assistant", "content": "reply"}]
        ex._run_foreground(dict(TASK), result, target_chat="secret", **kw)
    return result, EC


def test_a_task_whose_rows_were_not_saved_is_an_error_not_a_success():
    system, sm, _ = _task_system(appended=False)
    with patch("core.continuity.executor.publish") as pub:
        result, _ = _run(system)
    assert result["success"] is False
    assert any("could not be saved" in e for e in result["errors"])
    assert any("could not be saved" in str(c) for c in pub.call_args_list)


def test_a_saved_task_is_still_a_success():
    system, sm, _ = _task_system(appended=True)
    result, _ = _run(system)
    assert result["success"] is True and not result["errors"]
    assert sm.append_messages_to_chat.call_args.kwargs == {"purge_mark": 7}


def test_a_seal_stops_a_task_through_its_chat_hold():
    system, sm, hold = _task_system(appended=True)
    _, EC = _run(system, hold=True)
    cancel_check = EC.call_args.kwargs["cancel_check"]
    assert cancel_check() is False
    hold.cancel_flag = True                 # what the lease's stop sets
    assert cancel_check() is True
    system.llm_chat.end_stream.assert_called_once_with("sid", "secret")
