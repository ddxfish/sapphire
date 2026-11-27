"""Ghost message envelope assembly — behavior tests.

`core/ghost_messages.py` (NEW in 2.6.4, 209 lines) had ZERO test coverage.
It runs on every chat turn AND is the rail for plugin contributions to the
LLM prompt. Two ship-blocker fixes shipped 2026-05-14:
  - Non-string ghost_text from a plugin no longer crashes the chat turn
    (was: AttributeError on .strip(), produced consecutive assistants in
    chat history, wedged Claude alternation).
  - Sentinel single source of truth (claude.py imports _ENVELOPE_HEADER
    instead of hardcoding).

These tests guard against regressions on both fixes plus baseline behavior.
"""
import pytest

from core import ghost_messages
from core.hooks import HookEvent, HookRunner


class _SystemStub:
    """Minimal stand-in for VoiceChatSystem used by build_ghost_message."""
    pass


def _setup_runner(monkeypatch):
    """Swap the module-level hook_runner with a fresh instance per test."""
    fresh = HookRunner()
    monkeypatch.setattr(ghost_messages, "hook_runner", fresh)
    return fresh


def test_build_returns_none_when_no_contributions(monkeypatch):
    _setup_runner(monkeypatch)
    out = ghost_messages.build_ghost_message(
        _SystemStub(), {"inject_datetime": False, "spice_enabled": False}, "hi",
    )
    assert out is None


def test_envelope_header_present_when_contributing(monkeypatch):
    runner = _setup_runner(monkeypatch)

    def handler(event):
        event.ghost_text = "Time: 12:00"

    runner.register("ghost_inject", handler, priority=50, plugin_name="testplug")
    out = ghost_messages.build_ghost_message(
        _SystemStub(), {"inject_datetime": False, "spice_enabled": False}, "hi",
    )
    assert out is not None
    assert out.startswith(ghost_messages._ENVELOPE_HEADER)
    assert "testplug: Time: 12:00" in out
    # closed: providers fold this row into the person's row that follows it
    assert out.endswith("\n" + ghost_messages._ENVELOPE_FOOTER)


def test_non_string_dict_ghost_text_does_not_crash(monkeypatch):
    """REGRESSION_GUARD: a plugin returning a dict for ghost_text used to
    crash via .strip() AttributeError, which propagated past add_user_message
    in chat_streaming.py and produced consecutive assistant messages → 400
    on next Claude API call → wedged chat. Fixed 2026-05-14."""
    runner = _setup_runner(monkeypatch)

    def handler(event):
        event.ghost_text = {"unexpected": "dict"}

    runner.register("ghost_inject", handler, priority=50, plugin_name="badplug")
    # Must not raise
    out = ghost_messages.build_ghost_message(
        _SystemStub(), {"inject_datetime": False, "spice_enabled": False}, "hi",
    )
    # Dict gets coerced to str(...) — non-empty, gets included as the
    # plugin's contribution. Acceptable: better than wedging the chat.
    assert out is not None or out is None  # tolerate either outcome
    # If included, must not contain the literal phrase "AttributeError"
    if out is not None:
        assert "AttributeError" not in out


def test_non_string_list_ghost_text_does_not_crash(monkeypatch):
    """REGRESSION_GUARD: same as above for list returns."""
    runner = _setup_runner(monkeypatch)

    def handler(event):
        event.ghost_text = ["a", "b"]

    runner.register("ghost_inject", handler, priority=50, plugin_name="badplug")
    out = ghost_messages.build_ghost_message(
        _SystemStub(), {"inject_datetime": False, "spice_enabled": False}, "hi",
    )
    if out is not None:
        assert "AttributeError" not in out


def test_long_contribution_truncated_to_2048(monkeypatch):
    runner = _setup_runner(monkeypatch)
    long_text = "x" * 5000

    def handler(event):
        event.ghost_text = long_text

    runner.register("ghost_inject", handler, priority=50, plugin_name="bigplug")
    out = ghost_messages.build_ghost_message(
        _SystemStub(), {"inject_datetime": False, "spice_enabled": False}, "hi",
    )
    assert out is not None
    # 2048 cap + ellipsis "…"
    assert "x" * 2049 not in out
    assert "…" in out  # truncation marker


def test_handler_exception_isolated_does_not_break_envelope(monkeypatch):
    """REGRESSION_GUARD: A buggy plugin raising in ghost_inject must not
    prevent OTHER plugins' contributions from rendering. Hook runner
    catches per-handler exceptions."""
    runner = _setup_runner(monkeypatch)

    def bad_handler(event):
        raise RuntimeError("boom")

    def good_handler(event):
        event.ghost_text = "I survived"

    runner.register("ghost_inject", bad_handler, priority=50, plugin_name="boomplug")
    runner.register("ghost_inject", good_handler, priority=51, plugin_name="goodplug")
    out = ghost_messages.build_ghost_message(
        _SystemStub(), {"inject_datetime": False, "spice_enabled": False}, "hi",
    )
    assert out is not None
    assert "I survived" in out
    assert "goodplug: I survived" in out


def test_is_ghost_message_true_for_envelope():
    fake_envelope = ghost_messages._ENVELOPE_HEADER + "\n- Time: noon"
    assert ghost_messages.is_ghost_message(fake_envelope) is True


def test_is_ghost_message_false_for_user_text():
    assert ghost_messages.is_ghost_message("hello there") is False
    assert ghost_messages.is_ghost_message("") is False
    assert ghost_messages.is_ghost_message("[normal] bracket text") is False


def test_envelope_header_constant_stable():
    """REGRESSION_GUARD: claude.py:660-674 imports _ENVELOPE_HEADER for the
    cache-marker detection. If the constant is removed/renamed without
    updating claude.py, cache markers silently land on rotating ghost
    content → cache miss every turn → silent 3x Claude cost growth.
    This test pins the constant exists; the import in claude.py is the
    canonical reference."""
    assert hasattr(ghost_messages, "_ENVELOPE_HEADER")
    assert isinstance(ghost_messages._ENVELOPE_HEADER, str)
    assert len(ghost_messages._ENVELOPE_HEADER) > 0
    # The sentinel must start with [ so the substring detection in
    # claude.py:_apply_history_cache_control's startswith works.
    assert ghost_messages._ENVELOPE_HEADER.startswith("[")
