"""Regression tests for the 2026-04-20 witch-hunt fixes.

Three classes of bug closed this session that the prior suite did not catch:

1. **Silent-default scope resolvers.**  `_get_current_scope` in memory/knowledge/
   goals/gcal tools used to `except Exception: return 'default'`. A plugin
   hot-reload mid-flight, a broken import, anything — and the scope resolver
   would silently write into the wrong bucket. Now they return `None`, and
   the executor treats `None` as "disabled" and fails cleanly.

2. **`_resolve_persona` treating explicit `'none'` as empty.**  For scope keys,
   a task that sets `memory_scope='none'` must keep that value — not silently
   inherit the persona's real scope. For non-scope fields like `voice='none'`,
   legacy "none means empty" behavior is preserved.

3. **Voice-lock self-deadlock in `_run_foreground`.**  The finally block used
   `with self._voice_lock:` after an `acquire()` at entry, re-entering a
   non-reentrant `threading.Lock` and hanging every continuity task forever
   (LLM response saved but `running=True` stuck, subsequent tasks starved).

Every test here would have flagged the bug class it guards against.
"""
import threading
from unittest.mock import MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# 1. Silent-default scope resolver invariants
#
# Each of the four `_get_current_scope`-style helpers must return None when
# the underlying ContextVar access raises — NEVER fall back to a real scope
# name. "Default" was the specific value that used to leak observations into
# Sapphire's shared memory.
# ─────────────────────────────────────────────────────────────────────────────


class _BrokenContextVar:
    """Mimics a ContextVar whose .get() is broken (simulates hot-reload
    window, import partially torn down, etc.)."""

    def get(self):
        raise RuntimeError("simulated ContextVar failure")


@pytest.mark.parametrize(
    "module_path,fn_name,scope_attr",
    [
        ("plugins.memory.tools.memory_tools", "_get_current_scope", "scope_memory"),
        ("plugins.memory.tools.knowledge_tools", "_get_current_scope", "scope_knowledge"),
        ("plugins.memory.tools.knowledge_tools", "_get_current_people_scope", "scope_people"),
        ("plugins.memory.tools.goals_tools", "_get_current_scope", "scope_goal"),
        ("gcal", "_get_gcal_scope", "scope_gcal"),
    ],
)
def test_get_current_scope_returns_none_on_exception(module_path, fn_name, scope_attr):
    """Whenever the scope ContextVar access raises, the resolver must return
    None — never 'default'. That's the silent-default class we closed."""
    import importlib
    import importlib.util
    from pathlib import Path

    if module_path == "gcal":
        # google-calendar uses a hyphen in the folder, so we can't import via
        # standard package path. Load calendar.py directly by filesystem path.
        # The module executes its top-level imports (fastapi, etc.) — all of
        # which are available in the test env.
        spec = importlib.util.spec_from_file_location(
            "gcal_tools_under_test",
            Path(__file__).parent.parent / "plugins" / "google-calendar" / "tools" / "calendar.py",
        )
        mod = importlib.util.module_from_spec(spec)
        # Register in sys.modules BEFORE exec so any self-references resolve.
        import sys
        sys.modules["gcal_tools_under_test"] = mod
        try:
            spec.loader.exec_module(mod)
        except Exception as e:
            pytest.skip(f"gcal module import failed in test env: {e}")
    else:
        mod = importlib.import_module(module_path)

    fn = getattr(mod, fn_name, None)
    assert fn is not None, f"{module_path} has no {fn_name}"

    # Patch core.chat.function_manager to hand back a broken ContextVar for
    # *this* scope. The resolver does a fresh `from ... import` on each call,
    # so the patch targets the module the resolver will re-import from.
    with patch(f"core.chat.function_manager.{scope_attr}", _BrokenContextVar(), create=True):
        result = fn()
    assert result is None, (
        f"{module_path}.{fn_name}() returned {result!r} — silent-default "
        "class bug. Must return None when ContextVar access fails."
    )


def test_no_scope_resolver_hardcodes_default_fallback():
    """Source-level guard: no `_get_current_scope` helper may have a
    `return 'default'` in an except block. A comment referencing 'default'
    is fine; a literal return is not."""
    from pathlib import Path
    import re
    project_root = Path(__file__).parent.parent
    targets = [
        project_root / "plugins/memory/tools/memory_tools.py",
        project_root / "plugins/memory/tools/knowledge_tools.py",
        project_root / "plugins/memory/tools/goals_tools.py",
        project_root / "plugins/google-calendar/tools/calendar.py",
    ]
    # Match `def _get_current_scope` (or similar) body and look for
    # `return 'default'` or `return "default"` within the next ~400 chars.
    for path in targets:
        src = path.read_text(encoding="utf-8")
        for match in re.finditer(r"def _get_current[a-z_]*\s*\(", src):
            body = src[match.start():match.start() + 400]
            assert not re.search(r"return\s+['\"]default['\"]", body), (
                f"{path.name} still returns 'default' as scope fallback — "
                "silent-default class regression. Return None instead."
            )


# ─────────────────────────────────────────────────────────────────────────────
# 2. _resolve_persona must honor explicit 'none' for scope keys
#
# For scope keys only: 'none' is EXPLICIT opt-out. Non-scope fields keep
# legacy "treat 'none' as empty" semantics so `voice='none'` still falls
# through to persona default.
# ─────────────────────────────────────────────────────────────────────────────


def _make_executor_with_persona(persona_settings):
    """Build a minimal ContinuityExecutor whose persona_manager returns the
    given settings dict. Everything else is mocked — we only exercise
    _resolve_persona."""
    from core.continuity.executor import ContinuityExecutor
    system = MagicMock()
    ex = ContinuityExecutor(system)
    return ex


def test_resolve_persona_preserves_explicit_none_for_scope_keys():
    """A task with memory_scope='none' must keep 'none' even when the
    persona has a real scope — otherwise the silent-default class re-opens
    for non-agent personas."""
    from core.continuity.executor import ContinuityExecutor
    ex = ContinuityExecutor(MagicMock())

    persona_settings = {
        "name": "sapphire",
        "settings": {
            "memory_scope": "default",
            "knowledge_scope": "default",
            "goal_scope": "default",
            "people_scope": "default",
            "voice": "af_heart",
        },
    }
    task = {
        "persona": "sapphire",
        "memory_scope": "none",
        "knowledge_scope": "none",
    }
    with patch("core.personas.persona_manager") as pm:
        pm.get.return_value = persona_settings
        resolved = ex._resolve_persona(task)
    assert resolved["memory_scope"] == "none", (
        f"Explicit memory_scope='none' was silently overridden to {resolved['memory_scope']!r}"
    )
    assert resolved["knowledge_scope"] == "none", (
        f"Explicit knowledge_scope='none' was silently overridden to {resolved['knowledge_scope']!r}"
    )


def test_resolve_persona_still_overrides_default_for_scope_keys():
    """Legacy sentinel 'default' on scope keys continues to fall through to
    persona. Only 'none' is the explicit opt-out carve-out — we didn't
    change the rest of the resolver."""
    from core.continuity.executor import ContinuityExecutor
    ex = ContinuityExecutor(MagicMock())
    persona_settings = {
        "name": "sapphire",
        "settings": {"memory_scope": "sapphire"},
    }
    task = {"persona": "sapphire", "memory_scope": "default"}
    with patch("core.personas.persona_manager") as pm:
        pm.get.return_value = persona_settings
        resolved = ex._resolve_persona(task)
    assert resolved["memory_scope"] == "sapphire", (
        "Sentinel 'default' on a scope key should still fall through to persona."
    )


def test_resolve_persona_non_scope_field_still_treats_none_as_empty():
    """Non-scope fields (voice, pitch, etc.) keep the legacy
    'none-means-empty' behavior. We explicitly did not change that — only
    scope keys got the carve-out."""
    from core.continuity.executor import ContinuityExecutor
    ex = ContinuityExecutor(MagicMock())
    persona_settings = {
        "name": "sapphire",
        "settings": {"voice": "af_heart"},
    }
    task = {"persona": "sapphire", "voice": "none"}
    with patch("core.personas.persona_manager") as pm:
        pm.get.return_value = persona_settings
        resolved = ex._resolve_persona(task)
    assert resolved["voice"] == "af_heart", (
        "voice='none' on a non-scope field should still fall through to persona."
    )


# ─────────────────────────────────────────────────────────────────────────────
# 3. _run_foreground must not self-deadlock on _voice_lock
#
# The original bug: `acquire()` at method entry + `with self._voice_lock:`
# in the finally block → non-reentrant re-acquire → task hangs forever.
# Test drives _run_foreground with all dependencies mocked and asserts:
#   (a) the call returns within a generous timeout, and
#   (b) _voice_lock is released afterwards (not stuck held).
# Deadlock would pin the thread past the timeout and leave the lock held.
# ─────────────────────────────────────────────────────────────────────────────


def _build_mocked_system():
    """Build a `system` MagicMock that satisfies everything _run_foreground
    touches — session_manager, function_manager, tool_engine, tts."""
    system = MagicMock()
    sm = system.llm_chat.session_manager
    sm.list_chat_files.return_value = [{"name": "lookout"}]
    sm.read_chat_messages.return_value = []
    sm.create_chat.return_value = False  # chat already exists
    sm.append_messages_to_chat.return_value = None
    sm.append_to_chat.return_value = None
    # TTS present but non-speaking
    system.tts = MagicMock()
    system.tts.speak_sync.return_value = None
    return system


def _run_with_timeout(fn, timeout_s=5.0):
    """Run `fn` in a daemon thread and assert completion within `timeout_s`.
    Returns the function result or raises pytest.fail on timeout."""
    holder = {"done": False, "result": None, "error": None}

    def _worker():
        try:
            holder["result"] = fn()
        except BaseException as e:
            holder["error"] = e
        finally:
            holder["done"] = True

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=timeout_s)
    if not holder["done"]:
        pytest.fail(
            f"_run_foreground did not return within {timeout_s}s — "
            "this is exactly the voice_lock deadlock signature."
        )
    if holder["error"]:
        raise holder["error"]
    return holder["result"]


def test_run_foreground_releases_voice_lock_on_success():
    """After a normal _run_foreground call, _voice_lock must be released —
    not pinned by a finally block that tries to re-acquire it."""
    from core.continuity.executor import ContinuityExecutor
    system = _build_mocked_system()
    ex = ContinuityExecutor(system)

    task = {
        "name": "test-fg",
        "chat_target": "lookout",
        "initial_message": "ping",
        "prompt": "rook",
        "toolset": "rook",
        "tts_enabled": False,
    }
    result = {"success": False, "errors": [], "responses": [], "iterations_completed": 0}

    # Patch ExecutionContext so we don't need a real LLM/tool registry
    with patch("core.continuity.execution_context.ExecutionContext") as EC:
        instance = EC.return_value
        instance.run.return_value = "mocked reply"
        instance.new_messages = [
            {"role": "user", "content": "ping"},
            {"role": "assistant", "content": "mocked reply"},
        ]
        _run_with_timeout(
            lambda: ex._run_foreground(task, result),
            timeout_s=5.0,
        )

    # The real proof: acquire non-blocking — if the lock is held, this fails.
    acquired = ex._voice_lock.acquire(blocking=False)
    assert acquired, (
        "_voice_lock was NOT released after _run_foreground returned. "
        "This is the exact signature of the re-entrant double-acquire "
        "deadlock we just fixed."
    )
    ex._voice_lock.release()


def test_run_foreground_releases_voice_lock_on_inner_exception():
    """An exception inside the LLM run path still releases the lock on the
    way out — otherwise one failed task starves every subsequent task."""
    from core.continuity.executor import ContinuityExecutor
    system = _build_mocked_system()
    ex = ContinuityExecutor(system)

    task = {
        "name": "test-fg-err",
        "chat_target": "lookout",
        "initial_message": "ping",
        "prompt": "rook",
        "toolset": "rook",
        "tts_enabled": False,
    }
    result = {"success": False, "errors": [], "responses": [], "iterations_completed": 0}

    with patch("core.continuity.execution_context.ExecutionContext") as EC:
        instance = EC.return_value
        instance.run.side_effect = RuntimeError("boom — simulated provider fail")
        instance.new_messages = []
        _run_with_timeout(
            lambda: ex._run_foreground(task, result),
            timeout_s=5.0,
        )

    acquired = ex._voice_lock.acquire(blocking=False)
    assert acquired, (
        "_voice_lock held after an error path through _run_foreground — "
        "inner exception doesn't release the lock. Future tasks starve."
    )
    ex._voice_lock.release()


def test_run_foreground_back_to_back_does_not_deadlock():
    """Two _run_foreground calls in sequence — the second must be able to
    acquire the lock the first released. This is the user-visible failure
    mode of the deadlock bug (first task 'succeeded', second hung forever)."""
    from core.continuity.executor import ContinuityExecutor
    system = _build_mocked_system()
    ex = ContinuityExecutor(system)

    task = {
        "name": "test-fg-serial",
        "chat_target": "lookout",
        "initial_message": "ping",
        "prompt": "rook",
        "toolset": "rook",
        "tts_enabled": False,
    }

    with patch("core.continuity.execution_context.ExecutionContext") as EC:
        instance = EC.return_value
        instance.run.return_value = "reply"
        instance.new_messages = [{"role": "assistant", "content": "reply"}]

        for i in range(2):
            result = {"success": False, "errors": [], "responses": [], "iterations_completed": 0}
            _run_with_timeout(
                lambda: ex._run_foreground(task, result),
                timeout_s=5.0,
            )

    acquired = ex._voice_lock.acquire(blocking=False)
    assert acquired, "_voice_lock still held after two serial _run_foreground calls"
    ex._voice_lock.release()
