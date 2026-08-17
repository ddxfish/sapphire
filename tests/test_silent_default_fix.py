"""Silent-default fallback fixes (2026-04-19).

Follow-up to the scope-isolation witch hunt. New class of bug:
  - /api/chats/{name}/settings 404'd for every non-active chat post-SQLite
    migration because it still checked a legacy JSON path.
  - tools/ask-sapphire.sh silently fell back to 'sapphire'/'default' on the
    404, routing messages to the wrong persona/scope.
  - core/continuity/executor.py::_extract_task_settings silently defaulted
    every missing scope key to 'default' without logging.

These guards ensure we don't regress.
"""
import inspect
import logging
import sqlite3
from pathlib import Path

import pytest


# ─── read_chat_settings: the new SQLite reader ───────────────────────────

def test_read_chat_settings_returns_none_for_missing_chat():
    """[REGRESSION_GUARD] read_chat_settings returns None (not empty dict,
    not raise) when the chat doesn't exist. The route uses this to 404."""
    from core.chat.history import ChatSessionManager
    assert hasattr(ChatSessionManager, 'read_chat_settings'), \
        "ChatSessionManager must expose read_chat_settings for the new route"
    src = inspect.getsource(ChatSessionManager.read_chat_settings)
    # Reads from SQLite directly
    assert 'SELECT settings FROM chats' in src
    assert 'return None' in src


def test_chat_settings_route_uses_sqlite_not_legacy_json():
    """[REGRESSION_GUARD] Root-cause fix: the route must query SQLite for
    non-active chats, not check a legacy JSON file path that no longer
    exists post-migration. Before this fix, every non-active chat 404'd —
    which in turn made ask-sapphire.sh silently fall back to sapphire
    persona + default scopes. Scout root-cause finding 2026-04-19."""
    src = (Path(__file__).parent.parent / 'core/routes/chat.py').read_text()
    start = src.find('async def get_chat_settings')
    assert start > 0
    end = src.find('\n@router', start)
    body = src[start:end if end > 0 else len(src)]
    # Must call the new SQLite reader
    assert 'read_chat_settings' in body
    # Must NOT check _get_chat_path.exists() — that's the legacy JSON path
    assert '_get_chat_path' not in body, \
        "legacy JSON path must be removed from the route; use read_chat_settings"
    # Must NOT open() a json file — that was the dead code
    assert 'json.load' not in body


# ─── ask-sapphire.sh: fail loud ──────────────────────────────────────────

def test_ask_sapphire_fails_loud_on_missing_chat_settings():
    """[REGRESSION_GUARD] When the chat-settings HTTP endpoint returns non-200,
    the script must refuse and exit non-zero. Silent fallback to 'default' +
    'sapphire' routes private writes to the shared scope."""
    script = (Path(__file__).parent.parent / 'tools/ask-sapphire.sh').read_text()
    # Captures HTTP status
    assert 'HTTP_STATUS' in script
    # Refuses on non-200
    assert 'HTTP_STATUS" != "200"' in script
    # Exits with specific non-zero code (not just the default shell exit)
    assert 'exit 2' in script
    # Prints a clear error message to stderr
    assert 'Refusing to send' in script


def test_ask_sapphire_rejects_empty_settings_response():
    """Even a 200 with no 'settings' key is refused."""
    script = (Path(__file__).parent.parent / 'tools/ask-sapphire.sh').read_text()
    # Python fallback: sys.exit(3) on missing settings
    assert 'sys.exit(3)' in script
    assert "no 'settings' key" in script


# ─── executor: warn-not-silent on missing scope keys ─────────────────────

def test_executor_omits_missing_scope_keys_and_warns(caplog):
    """[REGRESSION_GUARD] Pre-2026-05-07, _extract_task_settings silently
    substituted 'default' for missing scope keys (with a warning). That
    fallback defeated the force-None protection in
    `execution_context.py:_build_scopes` — by the time it ran, every key
    was already in task_settings, so the `if setting_key not in
    task_settings` branch was dead code. Tasks with missing scopes
    silently wrote to shared 'default' memory.

    Today's fix: the pre-fill is removed. Missing keys now propagate to
    ExecutionContext where force-None DISABLES the scope (sets ContextVar
    to None). The warning still fires for diagnostic visibility.

    Assertion: missing keys are NOT in the returned settings (so
    force-None can fire downstream), AND the warning is emitted.

    See `scope_isolation_is_fine.md` 2026-05-07 update."""
    from core.continuity.executor import ContinuityExecutor
    task = {
        "name": "test-missing-scopes",
        "prompt": "falcon",
        "toolset": "all",
        # deliberately NO memory_scope / knowledge_scope / etc.
    }
    with caplog.at_level(logging.WARNING, logger='core.continuity.executor'):
        settings = ContinuityExecutor._extract_task_settings(task)
    # Missing scope keys must NOT be present in the returned dict — that's
    # what lets `_build_scopes` force-None them. If a future change re-adds
    # the 'default' fallback, this assertion fails loud.
    assert 'memory_scope' not in settings, (
        f"memory_scope should NOT be in extracted settings (forces silent "
        f"'default' fallback to land in shared scope). got: {settings.get('memory_scope')!r}"
    )
    # But logged loudly so users see the omission
    msgs = [r.message for r in caplog.records]
    assert any('missing scope keys' in m for m in msgs), \
        f"expected 'missing scope keys' warning, got: {msgs}"


def test_executor_silent_when_task_has_explicit_scopes(caplog):
    """Inverse: when scopes ARE set, no warning fires."""
    from core.chat.function_manager import scope_setting_keys
    from core.continuity.executor import ContinuityExecutor
    task = {
        "name": "test-has-scopes",
        "prompt": "falcon",
        "toolset": "all",
    }
    # Explicitly set every scope key the registry knows about
    for key in scope_setting_keys():
        task[key] = "falcon"
    with caplog.at_level(logging.WARNING, logger='core.continuity.executor'):
        ContinuityExecutor._extract_task_settings(task)
    msgs = [r.message for r in caplog.records]
    assert not any('missing scope keys' in m for m in msgs), \
        "no warning should fire when scopes are explicit"


# ─── API helper tool: smoke ──────────────────────────────────────────────

def test_sapphire_api_helper_exists_and_runnable():
    """[REGRESSION_GUARD] The sapphire-api.sh helper exists, is executable,
    and takes METHOD + PATH args. Kept minimal so it can't grow silent
    fallbacks of its own."""
    p = Path(__file__).parent.parent / 'tools/sapphire-api.sh'
    assert p.exists()
    assert p.stat().st_mode & 0o111, "must be executable"
    src = p.read_text()
    assert 'CSRF' in src
    assert 'METHOD' in src
    assert 'PATH_' in src
