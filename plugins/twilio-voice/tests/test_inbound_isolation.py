"""B1 inbound-call isolation (2026-07-09).

_resolve_chat decides the chat + settings an inbound caller runs in. A stranger's
line is locked down BY DEFAULT: unset toolset -> 'none', unset scopes -> isolated
per-caller (never the owner's 'default'), and a non-ephemeral rule with no
chat_target REFUSES rather than dropping the caller into the owner's 'default'
chat. This guards the privacy fix for a public release where Sapphire answers
strangers — a silent regression here would re-expose the owner's tools + memory.
"""
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

from core.chat.function_manager import scope_setting_keys


def _ensure_scopes():
    """Scopes register dynamically at app boot (or via tests/conftest for the core
    suite). For a standalone `pytest plugins/twilio-voice/tests/` run the registry
    is empty, so register the core Mind scopes. Idempotent — skipped if already set
    (e.g. during a full-suite run where tests/conftest already registered them)."""
    from core.chat.function_manager import register_plugin_scope
    if not scope_setting_keys():
        for _k in ("memory", "goal", "knowledge", "people"):
            register_plugin_scope(_k, plugin_name="pytest-twilio-b1")


_ensure_scopes()

_DAEMON = Path(__file__).resolve().parent.parent / "daemon.py"


def _load_daemon():
    spec = importlib.util.spec_from_file_location("twilio_daemon_undertest", _DAEMON)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


daemon = _load_daemon()


def _system(existing_chats=()):
    system = MagicMock()
    system.llm_chat.list_chats.return_value = [{"name": n} for n in existing_chats]
    system.llm_chat.session_manager.read_chat_settings.return_value = {}
    return system


def _capture_patch(system):
    """Record the settings patch written to the caller's ephemeral chat."""
    calls = {}

    def sncs(name, patch):
        calls["name"], calls["patch"] = name, patch
        return True

    system.llm_chat.session_manager.set_named_chat_settings.side_effect = sncs
    return calls


# ── ephemeral (per-caller) path — locked down by default ─────────────────────

def test_ephemeral_unset_toolset_defaults_to_none():
    system = _system()
    calls = _capture_patch(system)
    chat, ephemeral = daemon._resolve_chat(
        system, "acct1", "+15551234567", {"trigger_config": {"ephemeral": True}})
    assert ephemeral is True
    assert calls["patch"]["toolset"] == "none"           # not the 'all' create_chat inherits


def test_ephemeral_unset_scopes_isolated_never_default():
    system = _system()
    calls = _capture_patch(system)
    chat, _ = daemon._resolve_chat(
        system, "acct1", "+15551234567", {"trigger_config": {"ephemeral": True}})
    keys = scope_setting_keys()
    assert keys, "no scope keys registered — test would be vacuous"
    for sk in keys:
        assert calls["patch"][sk] == chat                # isolated to the caller's own chat
        assert calls["patch"][sk] != "default"           # never the owner's default scope


def test_ephemeral_rule_toolset_and_scope_honored():
    """Opt-in still works: a rule that DOES set toolset/scope is respected."""
    system = _system()
    calls = _capture_patch(system)
    daemon._resolve_chat(system, "acct1", "+15551234567", {
        "trigger_config": {"ephemeral": True},
        "toolset": "phone_helpers", "memory_scope": "shared_line"})  # canonical {key}_scope form
    assert calls["patch"]["toolset"] == "phone_helpers"
    assert calls["patch"]["memory_scope"] == "shared_line"


# ── elevate default vs the modal's explicit 'none' (2026-07-16) ──────────────
# The Realtime modal always saves toolset='none' explicitly, which used to veto
# the elevate default — making passphrase elevation unreachable from any
# UI-created rule. 'none' now falls through to the elevate default; a real
# toolset still wins.

def _patch_account(monkeypatch, acct):
    from core.credentials_manager import credentials
    monkeypatch.setattr(credentials, "get_twilio_account", lambda scope: acct)


def test_explicit_none_with_key_gets_elevate(monkeypatch):
    _patch_account(monkeypatch, {"elevate_key": "alligator3"})
    system = _system()
    calls = _capture_patch(system)
    daemon._resolve_chat(system, "acct1", "+15551234567", {
        "trigger_config": {"ephemeral": True}, "toolset": "none"})
    assert calls["patch"]["toolset"] == "elevate_toolset"


def test_explicit_none_without_key_stays_none(monkeypatch):
    _patch_account(monkeypatch, {})
    system = _system()
    calls = _capture_patch(system)
    daemon._resolve_chat(system, "acct1", "+15551234567", {
        "trigger_config": {"ephemeral": True}, "toolset": "none"})
    assert calls["patch"]["toolset"] == "none"


def test_real_toolset_beats_elevate_default(monkeypatch):
    _patch_account(monkeypatch, {"elevate_key": "alligator3"})
    system = _system()
    calls = _capture_patch(system)
    daemon._resolve_chat(system, "acct1", "+15551234567", {
        "trigger_config": {"ephemeral": True}, "toolset": "phone_helpers"})
    assert calls["patch"]["toolset"] == "phone_helpers"


# ── call LLM timeout rides the ephemeral chat's settings (2026-07-16) ────────

def test_ephemeral_chat_gets_llm_timeout():
    """Plugin setting (default 20s with no loader) lands as llm_request_timeout
    so _select_provider builds the call's provider with a snappy read deadline."""
    system = _system()
    calls = _capture_patch(system)
    daemon._resolve_chat(system, "acct1", "+15551234567",
                         {"trigger_config": {"ephemeral": True}})
    assert calls["patch"]["llm_request_timeout"] == 20.0


def test_llm_timeout_zero_leaves_settings_alone(monkeypatch):
    monkeypatch.setattr(daemon, "_llm_timeout", lambda: 0.0)
    system = _system()
    calls = _capture_patch(system)
    daemon._resolve_chat(system, "acct1", "+15551234567",
                         {"trigger_config": {"ephemeral": True}})
    assert "llm_request_timeout" not in calls["patch"]


def test_ephemeral_setup_failure_refuses_not_default():
    system = _system()
    system.llm_chat.session_manager.set_named_chat_settings.side_effect = RuntimeError("boom")
    chat, ephemeral = daemon._resolve_chat(
        system, "acct1", "+1555", {"trigger_config": {"ephemeral": True}})
    assert chat is None                                  # refuse, never fall back to 'default'


# ── non-ephemeral (persistent chat_target) path — refuse if unnamed ──────────

def test_non_ephemeral_no_chat_target_refuses():
    system = _system()
    chat, ephemeral = daemon._resolve_chat(
        system, "acct1", "+1555", {"trigger_config": {"ephemeral": False}})
    assert chat is None                                  # never drop a stranger into 'default'


def test_non_ephemeral_with_chat_target_uses_it():
    system = _system()
    chat, ephemeral = daemon._resolve_chat(
        system, "acct1", "+1555",
        {"trigger_config": {"ephemeral": False}, "chat_target": "support_line"})
    assert chat == "support_line"
    assert ephemeral is False
