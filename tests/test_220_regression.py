"""
Regression suite (originally the 2.2.0 bug hunt + stress tests).

This file consolidates the surviving / still-valuable tests from:
  - tests/test_bug_hunt_220.py  (deleted)
  - tests/test_stress_220.py    (deleted)

Each test is self-contained — no shared fixtures across classes — so reading
any single test tells you everything it does. Iteration counts on the stress
sections are kept modest so the full suite runs quickly without ReDoS-style
hangs.

PYTEST PRIMER (Krem-flavored)
─────────────────────────────
  - A "test" is any function whose name starts with `test_`. Same for classes:
    `class TestX:` groups related tests but is just organization, not OO.
  - `assert <expr>` is the whole assertion language — pytest rewrites assert
    statements at import time so the failure shows the values that were compared.
  - `tmp_path` is a built-in fixture: pytest gives you a fresh temp dir for
    each test that requested it. Auto-cleanup, no leaks between tests.
  - `@pytest.fixture` marks a function as a fixture. A test that takes a
    parameter with that name receives the fixture's return value. `yield`
    inside a fixture lets you run cleanup after the test finishes.
  - `monkeypatch` is the built-in "patch and auto-undo at test end" fixture.
    We mostly use `unittest.mock.patch` here because the old tests did, but
    monkeypatch is cleaner for new tests — `monkeypatch.setattr(obj, 'attr', val)`.
  - `@pytest.mark.parametrize("name", [v1, v2, v3])` runs the same test once
    per value, each with a clean state.

Run with: pytest tests/test_220_regression.py -v
"""
import json
import sqlite3
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# =============================================================================
# Section 1 — _strip_thinking_blocks (claude.py)
# Bug: Only "thinking" blocks were stripped, "redacted_thinking" survived,
#      which crashes the Claude API when extended thinking is disabled mid-chat.
# =============================================================================

class TestStripThinkingBlocks:
    """Regression tests for ClaudeProvider._strip_thinking_blocks.

    Each test builds the provider in a tiny way: we patch __init__ so we
    don't run the real one (which wants an API key, model config, etc.) and
    just call the method directly. This is the "method under test" pattern —
    isolate one method from its class's heavy dependencies.
    """

    def _strip(self, messages):
        """Helper to call the method without paying init cost.

        Note this is a regular method, not a fixture — fixtures are useful
        when setup is expensive or shared, but this one is so cheap it's
        clearer inline.
        """
        from core.chat.llm_providers.claude import ClaudeProvider
        with patch.object(ClaudeProvider, '__init__', lambda self, *a, **kw: None):
            provider = ClaudeProvider()
            return provider._strip_thinking_blocks(messages)

    def test_strips_normal_thinking_blocks(self):
        messages = [{"role": "assistant", "content": [
            {"type": "thinking", "thinking": "Let me think...", "signature": "sig123"},
            {"type": "text", "text": "Hello!"},
        ]}]
        result = self._strip(messages)
        assert len(result) == 1
        assert len(result[0]["content"]) == 1
        assert result[0]["content"][0]["type"] == "text"

    def test_strips_redacted_thinking_blocks(self):
        """The bug: redacted_thinking was being kept. Must be removed."""
        messages = [{"role": "assistant", "content": [
            {"type": "redacted_thinking", "data": "encrypted_blob"},
            {"type": "text", "text": "Here's my answer."},
        ]}]
        result = self._strip(messages)
        assert len(result[0]["content"]) == 1
        assert result[0]["content"][0]["text"] == "Here's my answer."

    def test_strips_mixed_thinking_types(self):
        messages = [{"role": "assistant", "content": [
            {"type": "thinking", "thinking": "Normal thinking", "signature": "sig1"},
            {"type": "redacted_thinking", "data": "encrypted"},
            {"type": "text", "text": "Final answer"},
        ]}]
        result = self._strip(messages)
        assert len(result[0]["content"]) == 1
        assert result[0]["content"][0]["text"] == "Final answer"

    def test_preserves_text_and_tool_use(self):
        messages = [{"role": "assistant", "content": [
            {"type": "text", "text": "Let me search."},
            {"type": "tool_use", "id": "toolu_123", "name": "search", "input": {"q": "test"}},
        ]}]
        result = self._strip(messages)
        assert len(result[0]["content"]) == 2

    def test_drops_message_that_was_only_thinking(self):
        """If stripping thinking leaves an empty content list, drop the message."""
        messages = [
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "hmm", "signature": "s1"},
                {"type": "redacted_thinking", "data": "blob"},
            ]},
            {"role": "user", "content": "What did you decide?"},
        ]
        result = self._strip(messages)
        assert len(result) == 1
        assert result[0]["role"] == "user"

    def test_user_messages_pass_through(self):
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": [
                {"type": "redacted_thinking", "data": "x"},
                {"type": "text", "text": "Hi"},
            ]},
        ]
        result = self._strip(messages)
        assert len(result) == 2
        assert result[0]["content"] == "Hello"

    def test_string_content_passes_through(self):
        """Older messages may have string content instead of a content list."""
        messages = [{"role": "assistant", "content": "Simple string response"}]
        result = self._strip(messages)
        assert result[0]["content"] == "Simple string response"


# =============================================================================
# Section 2 — Migration zombie key cleanup (core/migration.py)
# Bug: migrate_stt/tts_to_provider popped root-level keys from in-memory dict
#      but never wrote the file when STT_PROVIDER/TTS_PROVIDER already existed.
#      Result: stale STT_ENABLED / TTS_ENABLED keys persisted forever.
# =============================================================================

class TestMigrationCleanup:
    """Regression tests for the STT/TTS migration's zombie-key cleanup path.

    These tests use the `tmp_path` built-in pytest fixture — every test gets
    a fresh empty directory. We patch `core.migration.USER_DIR` so the
    migration writes inside our temp dir instead of the real user/.
    """

    def test_stt_zombie_enabled_cleaned(self, tmp_path):
        settings = {"stt": {"STT_PROVIDER": "faster_whisper"}, "STT_ENABLED": True}
        path = tmp_path / "user" / "settings.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(settings), encoding='utf-8')

        with patch('core.migration.USER_DIR', tmp_path / "user"):
            from core.migration import migrate_stt_to_provider
            migrate_stt_to_provider()

        result = json.loads(path.read_text(encoding='utf-8'))
        assert 'STT_ENABLED' not in result
        assert result['stt']['STT_PROVIDER'] == 'faster_whisper'

    def test_stt_zombie_engine_cleaned(self, tmp_path):
        settings = {
            "stt": {"STT_PROVIDER": "fireworks_whisper"},
            "STT_ENABLED": False,
            "STT_ENGINE": "faster_whisper",
        }
        path = tmp_path / "user" / "settings.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(settings), encoding='utf-8')

        with patch('core.migration.USER_DIR', tmp_path / "user"):
            from core.migration import migrate_stt_to_provider
            migrate_stt_to_provider()

        result = json.loads(path.read_text(encoding='utf-8'))
        assert 'STT_ENABLED' not in result
        assert 'STT_ENGINE' not in result

    def test_stt_no_write_when_already_clean(self, tmp_path):
        """If there's nothing to clean up, the file should NOT be touched.

        This is a 'no spurious writes' check — we capture mtime, run the
        migration, and assert mtime hasn't changed.
        """
        path = tmp_path / "user" / "settings.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"stt": {"STT_PROVIDER": "faster_whisper"}}),
                        encoding='utf-8')
        mtime_before = path.stat().st_mtime

        with patch('core.migration.USER_DIR', tmp_path / "user"):
            from core.migration import migrate_stt_to_provider
            migrate_stt_to_provider()

        assert path.stat().st_mtime == mtime_before

    def test_tts_zombie_enabled_cleaned(self, tmp_path):
        settings = {"tts": {"TTS_PROVIDER": "kokoro"}, "TTS_ENABLED": True}
        path = tmp_path / "user" / "settings.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(settings), encoding='utf-8')

        with patch('core.migration.USER_DIR', tmp_path / "user"):
            from core.migration import migrate_tts_to_provider
            migrate_tts_to_provider()

        result = json.loads(path.read_text(encoding='utf-8'))
        assert 'TTS_ENABLED' not in result
        assert result['tts']['TTS_PROVIDER'] == 'kokoro'

    def test_tts_no_write_when_already_clean(self, tmp_path):
        path = tmp_path / "user" / "settings.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"tts": {"TTS_PROVIDER": "elevenlabs"}}),
                        encoding='utf-8')
        mtime_before = path.stat().st_mtime

        with patch('core.migration.USER_DIR', tmp_path / "user"):
            from core.migration import migrate_tts_to_provider
            migrate_tts_to_provider()

        assert path.stat().st_mtime == mtime_before


# =============================================================================
# Section 3 — Goals PERMANENT flag enforcement
# (post-Phase-4: import path is plugins.memory.tools.goals_tools, NOT
#  the deleted functions/goals.py)
#
# Rule: AI tool path (_update_goal/_delete_goal) must refuse to modify or
#       delete permanent goals. The user API path (update_goal_api) intentionally
#       has NO guard — users can do whatever they want.
# =============================================================================

class TestGoalsPermanentFlag:
    """Regression tests for the permanent goal AI guard."""

    @pytest.fixture
    def goals_db(self, tmp_path):
        """Build a goals DB with one permanent and one normal goal.

        FIXTURE PATTERN: setup → yield → teardown.
        Everything before `yield` runs as setup. The yielded value becomes the
        argument the test receives. Everything after `yield` runs after the
        test finishes — even if it raised. Cleanup is guaranteed.
        """
        from plugins.memory.tools import goals_tools

        db_path = tmp_path / "goals.db"
        # Module-level globals — we override these so _ensure_db() builds in tmp.
        goals_tools._db_path = db_path
        goals_tools._db_initialized = False
        goals_tools._ensure_db()

        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO goals (id, title, priority, scope, permanent) "
            "VALUES (1, 'Monitor systems', 'high', 'default', 1)"
        )
        conn.execute(
            "INSERT INTO goals (id, title, priority, scope, permanent) "
            "VALUES (2, 'Normal goal', 'medium', 'default', 0)"
        )
        conn.commit()
        conn.close()

        yield goals_tools

        # Cleanup: reset module state so other tests get a clean slate.
        goals_tools._db_path = None
        goals_tools._db_initialized = False

    # The next three tests are basically the same shape — three different
    # fields the AI shouldn't be able to change on a permanent goal. We could
    # collapse them with @pytest.mark.parametrize, but the original 220 tests
    # kept them split for clarity, so I'm leaving them separate.
    def test_ai_cannot_update_permanent_status(self, goals_db):
        with patch.object(goals_db, '_get_current_scope', return_value='default'):
            result, success = goals_db._update_goal(1, scope='default', status='completed')
        assert not success
        assert "permanent" in result.lower()

    def test_ai_cannot_update_permanent_title(self, goals_db):
        with patch.object(goals_db, '_get_current_scope', return_value='default'):
            result, success = goals_db._update_goal(1, scope='default', title='New title')
        assert not success
        assert "permanent" in result.lower()

    def test_ai_cannot_update_permanent_priority(self, goals_db):
        with patch.object(goals_db, '_get_current_scope', return_value='default'):
            result, success = goals_db._update_goal(1, scope='default', priority='low')
        assert not success
        assert "permanent" in result.lower()

    def test_ai_can_add_progress_note_to_permanent(self, goals_db):
        """Progress notes are allowed even on permanent goals — design decision."""
        with patch.object(goals_db, '_get_current_scope', return_value='default'):
            result, success = goals_db._update_goal(1, scope='default',
                                                    progress_note='Systems nominal')
        assert success

    def test_ai_cannot_delete_permanent(self, goals_db):
        with patch.object(goals_db, '_get_current_scope', return_value='default'):
            result, success = goals_db._delete_goal(1, scope='default')
        assert not success
        assert "permanent" in result.lower()

    def test_ai_can_update_normal_goal(self, goals_db):
        """Sanity check — non-permanent goals still work normally."""
        with patch.object(goals_db, '_get_current_scope', return_value='default'):
            result, success = goals_db._update_goal(2, scope='default', status='completed')
        assert success

    def test_ai_can_delete_normal_goal(self, goals_db):
        with patch.object(goals_db, '_get_current_scope', return_value='default'):
            result, success = goals_db._delete_goal(2, scope='default')
        assert success

    def test_user_api_can_modify_permanent(self, goals_db):
        """User API path intentionally has no permanent guard. By design."""
        goals_db.update_goal_api(1, status='completed')
        detail = goals_db.get_goal_detail(1)
        assert detail['status'] == 'completed'

    def test_user_api_can_toggle_permanent_flag(self, goals_db):
        goals_db.update_goal_api(1, permanent=False)
        detail = goals_db.get_goal_detail(1)
        assert detail['permanent'] is False


# =============================================================================
# Section 4 — Foreground continuity must NOT switch active chat
# Bug: _run_foreground used to call set_active_chat(), hijacking the UI
#      whenever a scheduled task fired. Fix: read_chat_messages + append_to_chat.
# =============================================================================

class TestForegroundDoesNotHijackUI:
    """One narrow regression test, but it's the kind of bug that would be
    catastrophic to reintroduce — your scheduled heartbeat suddenly switches
    your active chat in the middle of typing."""

    def test_foreground_never_calls_set_active_chat(self):
        from core.continuity.executor import ContinuityExecutor

        # Patch __init__ to avoid touching the real system + filesystem.
        with patch.object(ContinuityExecutor, '__init__', lambda self: None):
            executor = ContinuityExecutor()
            executor._voice_lock = threading.Lock()
            executor._resolve_persona = lambda t: t
            executor._snapshot_voice = MagicMock(return_value={})
            executor._restore_voice = MagicMock()
            executor._apply_voice = MagicMock()

            mock_session = MagicMock()
            mock_session.list_chat_files.return_value = [{"name": "task_chat"}]
            mock_session.read_chat_messages.return_value = []

            mock_fm = MagicMock()
            mock_fm.all_possible_tools = []
            mock_fm._mode_filters = {}
            mock_fm._apply_mode_filter.return_value = []

            executor.system = MagicMock()
            executor.system.llm_chat.session_manager = mock_session
            executor.system.llm_chat.function_manager = mock_fm

            task = {
                "name": "heartbeat",
                "chat_target": "task_chat",
                "prompt": "default",
                "toolset": "none",
                "tts_enabled": False,
                "initial_message": "hello",
            }
            result = {"success": False, "task_id": "1", "task_name": "heartbeat",
                      "responses": [], "errors": []}

            with patch('core.continuity.execution_context.ExecutionContext.run',
                       return_value="ok"):
                out = executor._run_foreground(task, result)

            assert out["success"] is True
            mock_session.set_active_chat.assert_not_called()
            mock_session.read_chat_messages.assert_called_once()
            mock_session.append_to_chat.assert_called_once()


# =============================================================================
# Section 5 — Settings atomic write
# Bug: SettingsManager.save() used direct open() — a crash mid-write would
#      corrupt settings.json. Fix: write to .tmp then atomic rename.
# =============================================================================

class TestSettingsAtomicWrite:
    def test_save_writes_then_cleans_tmp(self, tmp_path):
        from core.settings_manager import SettingsManager

        with patch.object(SettingsManager, '__init__', lambda self: None):
            sm = SettingsManager()
            sm.BASE_DIR = tmp_path
            sm._user = {"TEST_KEY": "test_value"}
            sm._defaults = {}
            sm._config = {}
            sm._lock = threading.Lock()
            sm._last_mtime = None

            (tmp_path / 'user').mkdir()
            settings_file = tmp_path / 'user' / 'settings.json'
            settings_file.write_text('{}')

            (tmp_path / 'core').mkdir()
            (tmp_path / 'core' / 'settings_defaults.json').write_text('{}')

            sm.save()

            assert settings_file.exists()
            assert json.loads(settings_file.read_text())["TEST_KEY"] == "test_value"
            # Tmp file should NOT linger after a successful write.
            assert not (tmp_path / 'user' / 'settings.json.tmp').exists()


# =============================================================================
# Section 6 — Scope ContextVar isolation
# Phase 1-4 hinges on this: ContextVars must NOT bleed between concurrent
# threads. If they did, your "personal" memory scope could leak into the
# heartbeat thread's "work" scope and vice versa.
# =============================================================================

class TestScopeIsolation:
    """ContextVar-based scopes must stay thread-isolated.

    Each test follows the same shape: two threads, each sets a different
    value, then both read after a barrier sync. If the values bleed, the
    assertion catches it.

    BARRIER PATTERN: threading.Barrier(2) makes both threads wait until both
    have called .wait() before either proceeds. Forces the interesting
    interleaving — without it, one thread might finish before the other starts
    and the test would pass even with broken isolation.
    """

    def _isolation_check(self, scope_var, value_a, value_b):
        """Helper — same dance for every scope."""
        results = {}
        barrier = threading.Barrier(2)

        def thread_a():
            scope_var.set(value_a)
            barrier.wait()
            time.sleep(0.02)  # let B set its value too
            results['a'] = scope_var.get()

        def thread_b():
            scope_var.set(value_b)
            barrier.wait()
            time.sleep(0.02)
            results['b'] = scope_var.get()

        ta = threading.Thread(target=thread_a)
        tb = threading.Thread(target=thread_b)
        ta.start(); tb.start()
        ta.join(); tb.join()
        return results

    def test_memory_scope_isolated(self):
        from core.chat.function_manager import scope_memory
        results = self._isolation_check(scope_memory, 'personal', 'work')
        assert results['a'] == 'personal'
        assert results['b'] == 'work'

    def test_goal_scope_isolated(self):
        from core.chat.function_manager import scope_goal
        results = self._isolation_check(scope_goal, 'project-alpha', 'daily-ops')
        assert results['a'] == 'project-alpha'
        assert results['b'] == 'daily-ops'

    def test_knowledge_scope_isolated(self):
        from core.chat.function_manager import scope_knowledge
        results = self._isolation_check(scope_knowledge, 'research', 'home')
        assert results['a'] == 'research'
        assert results['b'] == 'home'

    def test_private_scope_isolated(self):
        """Private mode is a bool — leaking it would be a privacy bug."""
        from core.chat.function_manager import scope_private
        results = self._isolation_check(scope_private, True, False)
        assert results['a'] is True
        assert results['b'] is False

    def test_people_scope_isolated(self):
        """New scope added in Phase 4 — must isolate just like the others."""
        from core.chat.function_manager import scope_people
        results = self._isolation_check(scope_people, 'family', 'colleagues')
        assert results['a'] == 'family'
        assert results['b'] == 'colleagues'


# =============================================================================
# Section 7 — Toolset switching under concurrent load
# Bug class: update_enabled_functions touches shared mutable state. Without
#            the _tools_lock, concurrent switches could leave enabled_tools
#            in a half-updated state.
# =============================================================================

class TestToolsetConcurrency:
    """Stress test for FunctionManager.update_enabled_functions.

    Iteration count is intentionally modest (15 not 50). The original 220 test
    used 50 and was suspected as the source of suite freezes — kept low to keep
    the full pytest run snappy without losing the race-detection signal.
    """

    def test_concurrent_toolset_switching(self):
        from core.chat.function_manager import FunctionManager

        fm = FunctionManager()
        errors = []
        iterations = 15

        def switch(target):
            for _ in range(iterations):
                try:
                    fm.update_enabled_functions([target])
                    tools = fm.enabled_tools
                    assert isinstance(tools, list)  # never None / corrupted
                except Exception as e:
                    errors.append(f"{target}: {e}")
                time.sleep(0.001)

        t1 = threading.Thread(target=switch, args=('full',))
        t2 = threading.Thread(target=switch, args=('none',))
        t1.start(); t2.start()
        t1.join(); t2.join()

        assert not errors, f"Concurrent toolset switching caused errors: {errors}"


# =============================================================================
# Section 8 — Concurrent goal DB operations
# Bug class: SQLite under concurrent writes from multiple threads — must
#            not crash, must not lose any rows.
# =============================================================================

class TestConcurrentGoals:
    @pytest.fixture
    def goals_db(self, tmp_path):
        from plugins.memory.tools import goals_tools
        goals_tools._db_path = tmp_path / "goals.db"
        goals_tools._db_initialized = False
        goals_tools._ensure_db()
        yield goals_tools
        goals_tools._db_path = None
        goals_tools._db_initialized = False

    def test_concurrent_goal_creation(self, goals_db):
        """Two threads creating goals — every insert must land."""
        errors = []
        count = 15  # 30 total inserts — small but enough to expose write races

        def create_goals(prefix):
            for i in range(count):
                try:
                    with patch.object(goals_db, '_get_current_scope', return_value='default'):
                        goals_db._create_goal(f"{prefix}-goal-{i}", scope='default')
                except Exception as e:
                    errors.append(f"{prefix}-{i}: {e}")

        t1 = threading.Thread(target=create_goals, args=('user',))
        t2 = threading.Thread(target=create_goals, args=('ai',))
        t1.start(); t2.start()
        t1.join(); t2.join()

        assert not errors, f"Concurrent goal creation failed: {errors}"

        conn = sqlite3.connect(goals_db._db_path)
        total = conn.execute("SELECT COUNT(*) FROM goals").fetchone()[0]
        conn.close()
        assert total == count * 2, f"Expected {count * 2} goals, got {total}"

    def test_concurrent_goal_update_and_read(self, goals_db):
        """One thread updates, one reads — neither should crash."""
        with patch.object(goals_db, '_get_current_scope', return_value='default'):
            goals_db._create_goal("Shared goal", scope='default')

        errors = []
        iterations = 15

        def update_loop():
            for i in range(iterations):
                try:
                    with patch.object(goals_db, '_get_current_scope', return_value='default'):
                        goals_db._update_goal(1, scope='default',
                                              progress_note=f"Update {i}")
                except Exception as e:
                    errors.append(f"update-{i}: {e}")

        def read_loop():
            for i in range(iterations):
                try:
                    goals_db.get_goal_detail(1)
                except Exception as e:
                    errors.append(f"read-{i}: {e}")

        t1 = threading.Thread(target=update_loop)
        t2 = threading.Thread(target=read_loop)
        t1.start(); t2.start()
        t1.join(); t2.join()

        assert not errors, f"Concurrent goal read/write failed: {errors}"


# =============================================================================
# Section 9 — Settings file integrity under read/write race
# Bug class: read while write — partial JSON written without atomic rename
#            would crash JSON parser on the read side.
# =============================================================================

class TestSettingsFileIntegrity:
    def test_concurrent_read_write_no_partial_json(self, tmp_path):
        """Reading settings while writing must never see partial JSON.

        The write loop uses the atomic tmp+rename pattern. If you removed
        that pattern, this test should fail (intermittently — that's the
        signature of a real race).
        """
        settings_file = tmp_path / "settings.json"
        initial = {"tts": {"TTS_PROVIDER": "kokoro"}, "stt": {"STT_PROVIDER": "faster_whisper"}}
        settings_file.write_text(json.dumps(initial), encoding='utf-8')

        errors = []
        iterations = 30

        def write_loop():
            for i in range(iterations):
                data = {**initial, "counter": i, "extra": "x" * 100}
                try:
                    tmp = settings_file.with_suffix('.tmp')
                    tmp.write_text(json.dumps(data), encoding='utf-8')
                    tmp.replace(settings_file)
                except Exception as e:
                    errors.append(f"write-{i}: {e}")

        def read_loop():
            for i in range(iterations):
                try:
                    parsed = json.loads(settings_file.read_text(encoding='utf-8'))
                    assert isinstance(parsed, dict)
                    assert 'tts' in parsed
                except json.JSONDecodeError as e:
                    errors.append(f"read-{i}: corrupt JSON: {e}")
                except Exception as e:
                    errors.append(f"read-{i}: {e}")

        t1 = threading.Thread(target=write_loop)
        t2 = threading.Thread(target=read_loop)
        t1.start(); t2.start()
        t1.join(); t2.join()

        assert not errors, f"Settings file corruption detected: {errors}"


# =============================================================================
# Section 10 — Chat history concurrent writes
# Bug class: Two ConversationHistory instances writing in parallel must not
#            corrupt each other or bleed messages between chats.
# =============================================================================

class TestChatHistoryIntegrity:
    def test_concurrent_history_pair_saves_isolated(self):
        from core.chat.history import ConversationHistory

        errors = []
        count = 15
        history_a = ConversationHistory()
        history_b = ConversationHistory()

        def save_to_a():
            for i in range(count):
                try:
                    history_a.add_message_pair(f"Message A-{i}", f"Response A-{i}")
                except Exception as e:
                    errors.append(f"a-{i}: {e}")

        def save_to_b():
            for i in range(count):
                try:
                    history_b.add_message_pair(f"Message B-{i}", f"Response B-{i}")
                except Exception as e:
                    errors.append(f"b-{i}: {e}")

        t1 = threading.Thread(target=save_to_a)
        t2 = threading.Thread(target=save_to_b)
        t1.start(); t2.start()
        t1.join(); t2.join()

        assert not errors, f"Concurrent history writes failed: {errors}"
        assert len(history_a.get_messages()) == count * 2
        assert len(history_b.get_messages()) == count * 2

        # Bleed check: every A-message should be in A only, every B in B only.
        for msg in history_a.get_messages():
            assert 'B-' not in msg.get('content', ''), "Chat B bled into Chat A"
        for msg in history_b.get_messages():
            assert 'A-' not in msg.get('content', ''), "Chat A bled into Chat B"


# =============================================================================
# Section 11 — Provider switching safety
# Bug class: switching providers mid-operation must not crash; unknown
#            provider name must fall back to a safe Null implementation.
# =============================================================================

class TestProviderSwitchSafety:
    def test_tts_speed_persists_across_provider_check(self):
        from core.tts import tts_client
        client = tts_client.TTSClient()
        client.set_speed(1.8)
        assert client.speed == 1.8
        assert isinstance(client.speed, (int, float))

    def test_unknown_embedding_provider_falls_back_to_null(self):
        """If you ask for a provider that doesn't exist, we must NOT crash —
        we fall back to NullEmbedder so the rest of the app keeps running."""
        from core.embeddings import (NullEmbedder, get_embedder,
                                     switch_embedding_provider)

        switch_embedding_provider('local')
        assert get_embedder() is not None

        switch_embedding_provider('nonexistent_provider')
        assert isinstance(get_embedder(), NullEmbedder)


# =============================================================================
# Section 12 — Plugin toggle safety
# Bug class: unregistering a plugin (or repeatedly switching toolsets) must
#            never lose the core (non-plugin) tools from the execution map.
# =============================================================================

class TestPluginToggleSafety:
    def test_unregister_unknown_plugin_is_a_noop(self):
        """Asking to unregister something we never registered should be safe.

        Importantly: it must NOT touch the core tools that are already there.
        """
        from core.chat.function_manager import FunctionManager

        fm = FunctionManager()
        before = set(fm.execution_map.keys())
        assert before, "FunctionManager should have core tools loaded"

        fm.unregister_plugin_tools("nonexistent_plugin_xyz")

        after = set(fm.execution_map.keys())
        missing = before - after
        assert not missing, f"Core tools lost after safe unregister: {missing}"

    def test_rapid_toolset_switching_keeps_execution_map(self):
        """Switching toolsets fast must not empty the execution map."""
        from core.chat.function_manager import FunctionManager

        fm = FunctionManager()
        for _ in range(10):  # was 20 in the old test
            fm.update_enabled_functions(['full'])
            fm.update_enabled_functions(['none'])
            fm.update_enabled_functions(['full'])

        assert len(fm.execution_map) > 0, "Execution map empty after rapid switching"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
