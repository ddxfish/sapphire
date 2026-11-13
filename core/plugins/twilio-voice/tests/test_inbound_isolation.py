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
# the elevate default. 'none' falls through to the elevate default; a real
# toolset still wins. Since the one-Jason consolidation, the key + allow switch
# both live on the rule's trigger_config — no account involvement at all.

def test_explicit_none_with_key_and_allowance_gets_elevate():
    system = _system()
    calls = _capture_patch(system)
    daemon._resolve_chat(system, "acct1", "+15551234567", {
        "trigger_config": {"ephemeral": True, "allow_elevation": True,
                           "elevate_key": "alligator3"},
        "toolset": "none"})
    assert calls["patch"]["toolset"] == "elevate_toolset"


def test_allowance_without_key_stays_none():
    """Checkbox alone isn't enough — no passphrase, no elevate tool."""
    system = _system()
    calls = _capture_patch(system)
    daemon._resolve_chat(system, "acct1", "+15551234567", {
        "trigger_config": {"ephemeral": True, "allow_elevation": True},
        "toolset": "none"})
    assert calls["patch"]["toolset"] == "none"


def test_key_without_allowance_stays_none():
    """A key in config with the checkbox off is refused — the checkbox is the switch."""
    system = _system()
    calls = _capture_patch(system)
    daemon._resolve_chat(system, "acct1", "+15551234567", {
        "trigger_config": {"ephemeral": True, "elevate_key": "alligator3"},
        "toolset": "none"})
    assert calls["patch"]["toolset"] == "none"


def test_real_toolset_beats_elevate_default():
    system = _system()
    calls = _capture_patch(system)
    daemon._resolve_chat(system, "acct1", "+15551234567", {
        "trigger_config": {"ephemeral": True, "allow_elevation": True,
                           "elevate_key": "alligator3"},
        "toolset": "phone_helpers"})
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


# ── the call's voice: one rule for the greeting and her turns (2026-11-13) ────

def test_default_voice_rule_speaks_in_her_voice_as_of_this_call(monkeypatch):
    """The caller's chat is reused between calls and kept the voice it was
    created with: changing her voice never reached a caller who had rung before."""
    monkeypatch.setattr(daemon, "_default_voice", lambda: "ash")
    system = _system(existing_chats=("_phone_acct1_15551234567",))      # rang before
    calls = _capture_patch(system)
    daemon._resolve_chat(system, "acct1", "+15551234567", {"trigger_config": {"ephemeral": True}})
    assert calls["patch"]["voice"] == "ash"
    assert calls["patch"]["tts_voice"] == ""             # a rule voice set back to default is cleared


def test_a_rule_that_names_a_voice_keeps_it(monkeypatch):
    monkeypatch.setattr(daemon, "_default_voice", lambda: "ash")
    system = _system()
    calls = _capture_patch(system)
    daemon._resolve_chat(system, "acct1", "+15551234567",
                         {"trigger_config": {"ephemeral": True, "tts_voice": "af_heart"}})
    assert calls["patch"]["tts_voice"] == "af_heart" and "voice" not in calls["patch"]


def test_an_unreadable_default_voice_leaves_the_chats_own(monkeypatch):
    monkeypatch.setattr(daemon, "_default_voice", lambda: "")
    system = _system()
    calls = _capture_patch(system)
    daemon._resolve_chat(system, "acct1", "+15551234567", {"trigger_config": {"ephemeral": True}})
    assert "voice" not in calls["patch"]


def test_the_greeting_and_her_turns_read_the_voice_the_same_way():
    """She answered in one voice and went on in another: the greeting read the
    call chat's tts_voice only and fell to the global voice (the web chat on
    screen), while her turns read tts_voice, else voice."""
    src = _DAEMON.read_text(encoding="utf-8")
    greeting = src.split("greeting_audio = None")[1].split("greeting_audio = system.tts.generate_audio_data")[0]
    assert '_cs.get("tts_voice") or _cs.get("voice")' in greeting
    pump = (_DAEMON.parents[3] / "core" / "tts" / "stream_pump.py").read_text(encoding="utf-8")
    assert '_st.get("tts_voice") or _st.get("voice")' in pump


def test_an_outbound_call_goes_out_in_the_origin_chats_voice():
    system = _system()
    system.llm_chat.session_manager.read_chat_settings.return_value = {"prompt": "sapphire", "voice": "ash"}
    calls = _capture_patch(system)
    daemon._setup_outbound_chat(system, "acct1", "+15551234567", "", "main")
    assert calls["patch"]["tts_voice"] == "ash"
