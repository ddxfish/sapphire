"""Shared pytest fixtures for Sapphire tests."""
import sys
import json
from pathlib import Path

# Add project root to path BEFORE any other imports
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Pre-register dynamic plugin scopes BEFORE test collection imports any test module.
# Real plugin_loader.scan() doesn't run during pytest, so we fake the scopes that
# plugins would register. This lets test modules do
# `from core.chat.function_manager import scope_memory` and have the import resolve
# via the module's __getattr__ shim.
#
# After Phase 4: all 9 dynamic scopes (memory/goal/knowledge/people come from the
# memory plugin manifest; email/bitcoin/gcal/telegram/discord come from their
# respective plugin manifests) need pre-registration. Only `rag` and `private` remain
# hardcoded in function_manager.py's module-level declaration.
try:
    from core.chat.function_manager import register_plugin_scope
    _TEST_PLUGIN_SCOPES = (
        'memory', 'goal', 'knowledge', 'people',
        'email', 'bitcoin', 'gcal', 'telegram', 'discord',
    )
    for _scope_key in _TEST_PLUGIN_SCOPES:
        register_plugin_scope(_scope_key, plugin_name='pytest-conftest')
except Exception as _e:
    # Don't crash collection if function_manager can't be imported — let
    # individual tests surface the real error.
    import warnings
    warnings.warn(f"conftest scope pre-registration failed: {_e}")

import pytest

# ─── Thread leak guard ────────────────────────────────────────────────────────
# Default every threading.Thread created during tests to daemon=True. Some
# concurrency tests (SQLite write stress in test_220_regression, scope-bleed
# 10-thread CRUD tests) occasionally deadlock under contention. With non-
# daemon threads the stuck thread blocks interpreter shutdown, leaving the
# pytest process alive forever — and under `conda run -n sapphire pytest …`
# the wrapper also hangs, accumulating stuck shells that Krem has been
# reporting for months.
#
# Tests that explicitly want non-daemon threads can still pass `daemon=False`.
import threading as _threading
_orig_thread_init = _threading.Thread.__init__
def _daemon_default_thread_init(self, *args, **kwargs):
    kwargs.setdefault('daemon', True)
    _orig_thread_init(self, *args, **kwargs)
_threading.Thread.__init__ = _daemon_default_thread_init


@pytest.fixture
def settings_defaults():
    """Minimal settings defaults for testing."""
    return {
        "identity": {
            "DEFAULT_USERNAME": "TestUser"
        },
        "features": {},
        "llm": {
            "LLM_MAX_HISTORY": 10,
            "CONTEXT_LIMIT": 4000,
            "LLM_PRIMARY": {
                "base_url": "http://test:1234",
                "enabled": True
            }
        },
        "wakeword": {
            "RECORDER_PREFERRED_DEVICES": ["default"],
            "RECORDER_PREFERRED_DEVICES_LINUX": ["pipewire", "pulse", "default"],
            "RECORDER_PREFERRED_DEVICES_WINDOWS": ["default", "speakers"]
        }
    }


@pytest.fixture
def settings_defaults_file(tmp_path, settings_defaults):
    """Create a temporary settings_defaults.json file."""
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    defaults_file = core_dir / "settings_defaults.json"
    defaults_file.write_text(json.dumps(settings_defaults), encoding='utf-8')
    return defaults_file


@pytest.fixture
def user_settings_file(tmp_path):
    """Create a temporary user settings.json file."""
    user_dir = tmp_path / "user"
    user_dir.mkdir()
    settings_file = user_dir / "settings.json"
    settings_file.write_text('{}', encoding='utf-8')
    return settings_file


@pytest.fixture
def sample_messages():
    """Sample conversation messages for history tests."""
    return [
        {"role": "user", "content": "Hello", "timestamp": "2025-01-01T10:00:00"},
        {"role": "assistant", "content": "Hi there!", "timestamp": "2025-01-01T10:00:01"},
        {"role": "user", "content": "How are you?", "timestamp": "2025-01-01T10:00:02"},
        {"role": "assistant", "content": "I'm doing well!", "timestamp": "2025-01-01T10:00:03"},
    ]


@pytest.fixture
def sample_tool_messages():
    """Messages with tool calls for history tests."""
    return [
        {"role": "user", "content": "Search for cats", "timestamp": "2025-01-01T11:00:00"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call_123", "type": "function", "function": {"name": "web_search", "arguments": '{"query": "cats"}'}}],
            "timestamp": "2025-01-01T11:00:01"
        },
        {
            "role": "tool",
            "tool_call_id": "call_123",
            "name": "web_search",
            "content": "Found 10 results about cats",
            "timestamp": "2025-01-01T11:00:02"
        },
        {"role": "assistant", "content": "I found info about cats!", "timestamp": "2025-01-01T11:00:03"},
    ]


@pytest.fixture
def prompts_dir(tmp_path):
    """Create a temporary prompts directory with sample files."""
    prompts_path = tmp_path / "user" / "prompts"
    prompts_path.mkdir(parents=True)
    
    # Create sample prompt files
    pieces = {
        "components": {
            "character": {"default": "You are a helpful AI assistant."},
            "goals": {"helpful": "Be helpful and informative."},
            "location": {},
            "relationship": {},
            "format": {},
            "scenario": {},
            "extras": {},
            "emotions": {}
        },
        "scenario_presets": {}
    }
    (prompts_path / "prompt_pieces.json").write_text(
        json.dumps(pieces), encoding='utf-8'
    )
    
    monoliths = {
        "_comment": "Test monoliths",
        "default": "You are a helpful AI assistant named Sapphire."
    }
    (prompts_path / "prompt_monoliths.json").write_text(
        json.dumps(monoliths), encoding='utf-8'
    )
    
    spices = {
        "_comment": "Test spices",
        "humor": ["Be witty", "Use puns"]
    }
    (prompts_path / "prompt_spices.json").write_text(
        json.dumps(spices), encoding='utf-8'
    )
    
    return prompts_path


@pytest.fixture
def unicode_content():
    """Sample unicode content for encoding tests."""
    return {
        "japanese": "日本語テスト",
        "chinese": "中文测试",
        "korean": "한국어 테스트",
        "emoji": "Hello 👋 World 🌍",
        "mixed": "Test テスト 测试 🎉"
    }


@pytest.fixture
def mock_bcrypt_hash():
    """A valid bcrypt hash for testing (password: 'testpass')."""
    return '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/X4.kPQCHLxNKUQIMe'

# =============================================================================
# PHASE 0 FIXTURES (2026-04-18)
# Shared scaffolding for route integration, plugin lifecycle, agent system,
# and data-path tests. See tmp/coverage-test-plan.md for context.
# =============================================================================


@pytest.fixture
def client(tmp_path, monkeypatch):
    """FastAPI TestClient with auth bypassed.

    Yields (client, csrf_token). state-changing routes need the CSRF header
    even though auth is bypassed — use client.post(url, headers={'X-CSRF-Token': csrf}).

    IMPORTANT: This mounts the real app with its real middleware stack. Tests
    should use `mock_system` fixture if they need a controllable system singleton.
    """
    from fastapi.testclient import TestClient
    from core.api_fastapi import app
    from core.auth import require_login

    # Override auth dependency for test client
    def _no_auth():
        return {"logged_in": True}

    app.dependency_overrides[require_login] = _no_auth
    try:
        c = TestClient(app)
        csrf_token = "test-csrf-token"
        yield c, csrf_token
    finally:
        app.dependency_overrides.pop(require_login, None)


@pytest.fixture
def temp_user_dir(tmp_path, monkeypatch):
    """tmp_path-backed user/ dir with webui/plugins/, plugin_state/, webui/plugins.json scaffolded.

    Monkeypatches every USER_* path constant across core.plugin_loader and
    core.routes.plugins so tests write to an isolated FS, not the real user/ dir.

    Returns the `user` directory Path. Subdirs: `user/webui/plugins/`,
    `user/plugin_state/`, `user/plugins/`. plugins.json seeded empty.
    """
    user = tmp_path / "user"
    webui = user / "webui"
    webui_plugins = webui / "plugins"
    plugin_state = user / "plugin_state"
    user_plugins = user / "plugins"
    for d in (user, webui, webui_plugins, plugin_state, user_plugins):
        d.mkdir(parents=True, exist_ok=True)

    plugins_json = webui / "plugins.json"
    plugins_json.write_text('{"enabled": [], "disabled": []}', encoding='utf-8')

    # Monkeypatch every module-level path constant we can reach
    import core.plugin_loader as pl
    import core.routes.plugins as rp_plugins
    monkeypatch.setattr(pl, "USER_PLUGINS_JSON", plugins_json, raising=False)
    monkeypatch.setattr(pl, "USER_PLUGINS_DIR", user_plugins, raising=False)
    monkeypatch.setattr(rp_plugins, "USER_PLUGINS_JSON", plugins_json, raising=False)
    monkeypatch.setattr(rp_plugins, "USER_WEBUI_DIR", webui, raising=False)
    monkeypatch.setattr(rp_plugins, "USER_PLUGIN_SETTINGS_DIR", webui_plugins, raising=False)

    return user


@pytest.fixture
def mock_system(monkeypatch):
    """Stub VoiceChatSystem wired into core.api_fastapi via set_system().

    Exposes commonly-needed sub-objects as MagicMocks:
      mock_system.llm_chat.function_manager
      mock_system.llm_chat.session_manager
      mock_system.agent_manager
      mock_system.tts
      mock_system.history

    Route tests can assign behavior: e.g. `mock_system.llm_chat.session_manager.get_active_chat_name.return_value = 'trinity'`.
    Auto-clears on teardown.
    """
    from unittest.mock import MagicMock
    import core.api_fastapi as apifa

    sys_mock = MagicMock()
    sys_mock.llm_chat = MagicMock()
    sys_mock.llm_chat.function_manager = MagicMock()
    sys_mock.llm_chat.session_manager = MagicMock()
    sys_mock.agent_manager = MagicMock()
    sys_mock.tts = MagicMock()
    sys_mock.history = MagicMock()

    # Prior set_system value to restore on teardown
    old = getattr(apifa, "_system", None) or getattr(apifa, "system", None)
    apifa.set_system(sys_mock) if hasattr(apifa, "set_system") else monkeypatch.setattr(apifa, "_system", sys_mock, raising=False)
    try:
        yield sys_mock
    finally:
        if old is not None and hasattr(apifa, "set_system"):
            try:
                apifa.set_system(old)
            except Exception:
                pass


@pytest.fixture
def scope_snapshot():
    """Snapshot SCOPE_REGISTRY before test, restore full entries (incl. ContextVar identity) after.

    Matches the pattern proven in test_memory_plugin_integration.py — reload_plugin
    replaces ContextVars under the same key, so we must restore the ORIGINAL entry
    dicts, not just re-add missing keys.
    """
    from core.chat.function_manager import SCOPE_REGISTRY
    snapshot = dict(SCOPE_REGISTRY)
    yield snapshot
    # Restore every original entry (preserves ContextVar identity)
    for k, v in snapshot.items():
        SCOPE_REGISTRY[k] = v
    # Remove any keys added during the test
    for k in list(SCOPE_REGISTRY.keys()):
        if k not in snapshot:
            SCOPE_REGISTRY.pop(k, None)


@pytest.fixture
def event_bus_capture(monkeypatch):
    """Capture every event_bus.publish() call during the test; expose .events list.

    event_bus's subscribe() is generator-based (SSE streams), not callback-based —
    so we intercept at the module-level publish() function instead. Records
    (event_type, data) tuples without breaking real delivery.

    Usage:
        def test_something(event_bus_capture):
            # do something that should publish
            types_seen = [ev for ev, _ in event_bus_capture.events]
            assert 'toolset_changed' in types_seen
    """
    from core import event_bus
    from types import SimpleNamespace
    import importlib

    # Snapshot the global replay buffer so this test's publishes don't
    # pollute subsequent tests (the buffer is a fixed-size deque).
    _bus = event_bus.get_event_bus()
    _buffer_snapshot = list(_bus._replay_buffer)

    captured = []
    orig_publish = event_bus.publish

    def _capturing_publish(event_type, data=None):
        captured.append((event_type, data))
        return orig_publish(event_type, data)

    monkeypatch.setattr(event_bus, 'publish', _capturing_publish)

    # Modules that import `publish` at module scope have already bound a
    # reference to the original function; patching event_bus.publish alone
    # doesn't affect them. Patch their local bindings too.
    _MODULE_LEVEL_PUBLISH_IMPORTERS = (
        'core.agents.manager',
        'core.stt.recorder',
        'core.tts.tts_client',
        'core.api_fastapi',
        'core.routes.chat',
        'core.routes.settings',
        'core.continuity.executor',
        'core.continuity.scheduler',
        'core.wakeword.wake_detector',
        'core.chat.history',
    )
    for _mod_name in _MODULE_LEVEL_PUBLISH_IMPORTERS:
        try:
            _mod = importlib.import_module(_mod_name)
        except Exception:
            continue
        if hasattr(_mod, 'publish'):
            monkeypatch.setattr(_mod, 'publish', _capturing_publish, raising=False)

    cap = SimpleNamespace(events=captured)
    yield cap

    # Restore the replay buffer so tests don't leak into each other.
    _bus._replay_buffer.clear()
    _bus._replay_buffer.extend(_buffer_snapshot)


@pytest.fixture
def fake_popen(monkeypatch):
    """Drop-in replacement for subprocess.Popen returning a configurable fake proc.

    Default: returncode=0, stdout='{"result":"ok","session_id":"test-session"}', stderr=''.
    Customize via fake_popen.returncode / .stdout / .stderr / .communicate_raises before
    the code under test invokes Popen.

    The returned object tracks .terminate / .kill / .wait / killpg calls via .calls dict.
    """
    import subprocess
    from types import SimpleNamespace

    state = SimpleNamespace(
        returncode=0,
        stdout='{"result":"ok","session_id":"test-session"}',
        stderr='',
        communicate_raises=None,
        pid=12345,
        calls={"terminate": 0, "kill": 0, "wait": 0, "killpg": 0},
    )

    class _FakeProc:
        def __init__(self, args, **kwargs):
            self.args = args
            self.pid = state.pid
            self.returncode = None
            self._alive = True

        def communicate(self, timeout=None):
            if state.communicate_raises:
                raise state.communicate_raises
            self._alive = False
            self.returncode = state.returncode
            return (state.stdout, state.stderr)

        def poll(self):
            return self.returncode if not self._alive else None

        def terminate(self):
            state.calls["terminate"] += 1
            self._alive = False
            self.returncode = -15

        def kill(self):
            state.calls["kill"] += 1
            self._alive = False
            self.returncode = -9

        def wait(self, timeout=None):
            state.calls["wait"] += 1
            return self.returncode if self.returncode is not None else 0

    monkeypatch.setattr(subprocess, "Popen", _FakeProc)
    # os.killpg also tracked
    import os as _os
    orig_killpg = getattr(_os, "killpg", None)
    def _fake_killpg(pid, sig):
        state.calls["killpg"] += 1
    if orig_killpg:
        monkeypatch.setattr(_os, "killpg", _fake_killpg)

    return state


@pytest.fixture
def blocking_worker_cls():
    """BaseWorker subclass with a threading.Event gate. Deterministic concurrency tests.

    Usage:
        def test_x(blocking_worker_cls):
            w = blocking_worker_cls(agent_id='a', name='Test', mission='m')
            w.start()
            # worker is blocked on its gate
            w.finish_with_result('done')
            # worker unblocks, completes with that result
    """
    from core.agents.base_worker import BaseWorker
    import threading

    class _BlockingWorker(BaseWorker):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._gate = threading.Event()
            self._queued_result = None
            self._queued_raise = None

        def run(self):
            self._gate.wait(timeout=5)
            if self._queued_raise:
                raise self._queued_raise
            if self._queued_result is not None:
                self.result = self._queued_result

        def finish_with_result(self, result):
            self._queued_result = result
            self._gate.set()

        def finish_with_error(self, exc):
            self._queued_raise = exc
            self._gate.set()

        def finish(self):
            self._gate.set()

    return _BlockingWorker
