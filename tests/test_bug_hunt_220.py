"""
Bug Hunt 2.2.0 Regression Tests

Tests for bugs found and fixed during the 2.2.0 pre-release bug hunt.
Prevents regressions on:
  - redacted_thinking block stripping (claude.py)
  - STT/TTS migration zombie key cleanup (migration.py)
  - Goals PERMANENT flag enforcement (goals.py)

Run with: pytest tests/test_bug_hunt_220.py -v
"""
import pytest
import sys
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# =============================================================================
# _strip_thinking_blocks must strip BOTH thinking AND redacted_thinking
# Bug: Only "thinking" was stripped, "redacted_thinking" survived.
# Impact: Claude API rejects redacted_thinking blocks when thinking is disabled,
#         killing multi-turn conversations with extended thinking + tool calls.
# =============================================================================

class TestStripThinkingBlocks:
    """Regression tests for claude.py _strip_thinking_blocks."""

    @pytest.fixture
    def strip_fn(self):
        """Get the _strip_thinking_blocks method without full provider init."""
        from core.chat.llm_providers.claude import ClaudeProvider
        # Create a minimal instance just to access the method
        with patch.object(ClaudeProvider, '__init__', lambda self, *a, **kw: None):
            provider = ClaudeProvider()
            return provider._strip_thinking_blocks

    def test_strips_thinking_blocks(self, strip_fn):
        """Standard thinking blocks must be removed."""
        messages = [
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "Let me think...", "signature": "sig123"},
                {"type": "text", "text": "Hello!"}
            ]}
        ]
        result = strip_fn(messages)
        assert len(result) == 1
        assert len(result[0]["content"]) == 1
        assert result[0]["content"][0]["type"] == "text"

    def test_strips_redacted_thinking_blocks(self, strip_fn):
        """redacted_thinking blocks must also be removed — this was the bug."""
        messages = [
            {"role": "assistant", "content": [
                {"type": "redacted_thinking", "data": "encrypted_blob_here"},
                {"type": "text", "text": "Here's my answer."}
            ]}
        ]
        result = strip_fn(messages)
        assert len(result) == 1
        assert len(result[0]["content"]) == 1
        assert result[0]["content"][0]["type"] == "text"
        assert result[0]["content"][0]["text"] == "Here's my answer."

    def test_strips_mixed_thinking_types(self, strip_fn):
        """Both thinking and redacted_thinking in same message must be stripped."""
        messages = [
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "Normal thinking", "signature": "sig1"},
                {"type": "redacted_thinking", "data": "encrypted"},
                {"type": "text", "text": "Final answer"}
            ]}
        ]
        result = strip_fn(messages)
        assert len(result) == 1
        assert len(result[0]["content"]) == 1
        assert result[0]["content"][0]["text"] == "Final answer"

    def test_preserves_non_thinking_content(self, strip_fn):
        """Text, tool_use, and other blocks must survive stripping."""
        messages = [
            {"role": "assistant", "content": [
                {"type": "text", "text": "Let me search."},
                {"type": "tool_use", "id": "toolu_123", "name": "search", "input": {"q": "test"}}
            ]}
        ]
        result = strip_fn(messages)
        assert len(result) == 1
        assert len(result[0]["content"]) == 2

    def test_removes_message_with_only_thinking(self, strip_fn):
        """Message containing only thinking blocks should be dropped entirely."""
        messages = [
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "hmm", "signature": "s1"},
                {"type": "redacted_thinking", "data": "blob"}
            ]},
            {"role": "user", "content": "What did you decide?"}
        ]
        result = strip_fn(messages)
        # Assistant message dropped, user message preserved
        assert len(result) == 1
        assert result[0]["role"] == "user"

    def test_ignores_user_messages(self, strip_fn):
        """User messages should pass through untouched."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": [
                {"type": "redacted_thinking", "data": "x"},
                {"type": "text", "text": "Hi"}
            ]}
        ]
        result = strip_fn(messages)
        assert len(result) == 2
        assert result[0]["content"] == "Hello"

    def test_handles_string_content(self, strip_fn):
        """Assistant messages with string content (not list) should pass through."""
        messages = [
            {"role": "assistant", "content": "Simple string response"}
        ]
        result = strip_fn(messages)
        assert len(result) == 1
        assert result[0]["content"] == "Simple string response"


# =============================================================================
# Migration: STT/TTS zombie key cleanup
# Bug: migrate_stt_to_provider / migrate_tts_to_provider popped root-level
#      keys from memory but returned before writing file. Zombie keys persist.
# Impact: Stale root-level STT_ENABLED/TTS_ENABLED keys never cleaned up.
# =============================================================================

class TestMigrationSTTCleanup:
    """Regression tests for migrate_stt_to_provider zombie key cleanup."""

    def test_cleans_root_stt_enabled_when_already_migrated(self, tmp_path):
        """Root-level STT_ENABLED should be removed and file written when STT_PROVIDER exists."""
        settings = {
            "stt": {"STT_PROVIDER": "faster_whisper"},
            "STT_ENABLED": True  # zombie key from wizard
        }
        settings_path = tmp_path / "user" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text(json.dumps(settings), encoding='utf-8')

        with patch('core.migration.USER_DIR', tmp_path / "user"):
            from core.migration import migrate_stt_to_provider
            migrate_stt_to_provider()

        result = json.loads(settings_path.read_text(encoding='utf-8'))
        assert 'STT_ENABLED' not in result, "Zombie STT_ENABLED should be cleaned from file"
        assert result['stt']['STT_PROVIDER'] == 'faster_whisper'

    def test_cleans_root_stt_engine_when_already_migrated(self, tmp_path):
        """Root-level STT_ENGINE should also be cleaned up."""
        settings = {
            "stt": {"STT_PROVIDER": "fireworks_whisper"},
            "STT_ENABLED": False,
            "STT_ENGINE": "faster_whisper"  # another zombie
        }
        settings_path = tmp_path / "user" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text(json.dumps(settings), encoding='utf-8')

        with patch('core.migration.USER_DIR', tmp_path / "user"):
            from core.migration import migrate_stt_to_provider
            migrate_stt_to_provider()

        result = json.loads(settings_path.read_text(encoding='utf-8'))
        assert 'STT_ENABLED' not in result
        assert 'STT_ENGINE' not in result

    def test_no_write_when_already_clean(self, tmp_path):
        """Already-migrated with no zombie keys should not rewrite file."""
        settings = {"stt": {"STT_PROVIDER": "faster_whisper"}}
        settings_path = tmp_path / "user" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text(json.dumps(settings), encoding='utf-8')
        mtime_before = settings_path.stat().st_mtime

        with patch('core.migration.USER_DIR', tmp_path / "user"):
            from core.migration import migrate_stt_to_provider
            migrate_stt_to_provider()

        # File should not have been touched
        assert settings_path.stat().st_mtime == mtime_before


class TestMigrationTTSCleanup:
    """Regression tests for migrate_tts_to_provider zombie key cleanup."""

    def test_cleans_root_tts_enabled_when_already_migrated(self, tmp_path):
        """Root-level TTS_ENABLED should be removed and file written when TTS_PROVIDER exists."""
        settings = {
            "tts": {"TTS_PROVIDER": "kokoro"},
            "TTS_ENABLED": True  # zombie key
        }
        settings_path = tmp_path / "user" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text(json.dumps(settings), encoding='utf-8')

        with patch('core.migration.USER_DIR', tmp_path / "user"):
            from core.migration import migrate_tts_to_provider
            migrate_tts_to_provider()

        result = json.loads(settings_path.read_text(encoding='utf-8'))
        assert 'TTS_ENABLED' not in result, "Zombie TTS_ENABLED should be cleaned from file"
        assert result['tts']['TTS_PROVIDER'] == 'kokoro'

    def test_no_write_when_already_clean(self, tmp_path):
        """Already-migrated with no zombie keys should not rewrite file."""
        settings = {"tts": {"TTS_PROVIDER": "elevenlabs"}}
        settings_path = tmp_path / "user" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text(json.dumps(settings), encoding='utf-8')
        mtime_before = settings_path.stat().st_mtime

        with patch('core.migration.USER_DIR', tmp_path / "user"):
            from core.migration import migrate_tts_to_provider
            migrate_tts_to_provider()

        assert settings_path.stat().st_mtime == mtime_before


# =============================================================================
# Goals PERMANENT flag enforcement
# Bug risk: AI tools must not modify or delete permanent goals.
# The user API path (update_goal_api) intentionally has no guard.
# =============================================================================

class TestGoalsPermanentFlag:
    """Regression tests for goals.py PERMANENT enforcement."""

    @pytest.fixture
    def goals_db(self, tmp_path):
        """Set up a temporary goals database with a permanent goal."""
        import functions.goals as goals_mod

        db_path = tmp_path / "goals.db"
        goals_mod._db_path = db_path
        goals_mod._db_initialized = False
        goals_mod._ensure_db()

        # Insert a permanent goal directly
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO goals (id, title, priority, scope, permanent) VALUES (1, 'Monitor systems', 'high', 'default', 1)"
        )
        # And a normal goal for comparison
        conn.execute(
            "INSERT INTO goals (id, title, priority, scope, permanent) VALUES (2, 'Normal goal', 'medium', 'default', 0)"
        )
        conn.commit()
        conn.close()

        yield goals_mod

        # Cleanup
        goals_mod._db_path = None
        goals_mod._db_initialized = False

    def test_ai_cannot_update_permanent_goal_status(self, goals_db):
        """AI tool path must reject status changes on permanent goals."""
        with patch.object(goals_db, '_get_current_scope', return_value='default'):
            result, success = goals_db._update_goal(1, scope='default', status='completed')
        assert not success
        assert "permanent" in result.lower()

    def test_ai_cannot_update_permanent_goal_title(self, goals_db):
        """AI tool path must reject title changes on permanent goals."""
        with patch.object(goals_db, '_get_current_scope', return_value='default'):
            result, success = goals_db._update_goal(1, scope='default', title='New title')
        assert not success
        assert "permanent" in result.lower()

    def test_ai_cannot_update_permanent_goal_priority(self, goals_db):
        """AI tool path must reject priority changes on permanent goals."""
        with patch.object(goals_db, '_get_current_scope', return_value='default'):
            result, success = goals_db._update_goal(1, scope='default', priority='low')
        assert not success
        assert "permanent" in result.lower()

    def test_ai_can_add_progress_note_to_permanent_goal(self, goals_db):
        """AI should still be able to add progress notes to permanent goals."""
        with patch.object(goals_db, '_get_current_scope', return_value='default'):
            result, success = goals_db._update_goal(1, scope='default', progress_note='Systems nominal')
        assert success

    def test_ai_cannot_delete_permanent_goal(self, goals_db):
        """AI tool path must reject deletion of permanent goals."""
        with patch.object(goals_db, '_get_current_scope', return_value='default'):
            result, success = goals_db._delete_goal(1, scope='default')
        assert not success
        assert "permanent" in result.lower()

    def test_ai_can_update_normal_goal(self, goals_db):
        """Non-permanent goals should update normally (sanity check)."""
        with patch.object(goals_db, '_get_current_scope', return_value='default'):
            result, success = goals_db._update_goal(2, scope='default', status='completed')
        assert success

    def test_ai_can_delete_normal_goal(self, goals_db):
        """Non-permanent goals should delete normally (sanity check)."""
        with patch.object(goals_db, '_get_current_scope', return_value='default'):
            result, success = goals_db._delete_goal(2, scope='default')
        assert success

    def test_user_api_can_modify_permanent_goal(self, goals_db):
        """User API path (update_goal_api) intentionally has NO permanent guard."""
        # This is by design — users have full control
        goals_db.update_goal_api(1, status='completed')
        # If we get here without ValueError, the guard is correctly absent

        # Verify it actually changed
        detail = goals_db.get_goal_detail(1)
        assert detail['status'] == 'completed'

    def test_user_api_can_toggle_permanent_flag(self, goals_db):
        """User API path can remove the permanent flag."""
        goals_db.update_goal_api(1, permanent=False)
        detail = goals_db.get_goal_detail(1)
        assert detail['permanent'] is False


# =============================================================================
# D3: Foreground continuity must NOT switch active chat
# Old bug: _run_foreground called set_active_chat(), hijacking UI
# Fix: read_chat_messages + append_to_chat — no UI impact
# =============================================================================

class TestForegroundNoUISwitch:
    """Foreground tasks must never switch the active chat."""

    def test_foreground_never_calls_set_active_chat(self):
        """_run_foreground must use read/append methods, never set_active_chat."""
        from core.continuity.executor import ContinuityExecutor

        with patch.object(ContinuityExecutor, '__init__', lambda self: None):
            executor = ContinuityExecutor()
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

            with patch('core.continuity.execution_context.ExecutionContext.run', return_value="ok"):
                out = executor._run_foreground(task, result)
            assert out["success"] is True
            mock_session.set_active_chat.assert_not_called()
            mock_session.read_chat_messages.assert_called_once()
            mock_session.append_to_chat.assert_called_once()


# =============================================================================
# D4: Settings atomic write
# Bug: save() used direct open() — crash mid-write could corrupt settings.json
# Fix: Write to .tmp then atomic rename
# =============================================================================

class TestSettingsAtomicWrite:
    """Settings save should use atomic tmp+rename pattern."""

    def test_save_uses_tmp_rename(self, tmp_path):
        """save() should write to .tmp then rename."""
        from core.settings_manager import SettingsManager

        with patch.object(SettingsManager, '__init__', lambda self: None):
            sm = SettingsManager()
            sm.BASE_DIR = tmp_path
            sm._user = {"TEST_KEY": "test_value"}
            sm._defaults = {}
            sm._config = {}
            sm._lock = __import__('threading').Lock()
            sm._last_mtime = None

            # Create user dir and initial settings file
            user_dir = tmp_path / 'user'
            user_dir.mkdir()
            settings_file = user_dir / 'settings.json'
            settings_file.write_text('{}')

            # Create defaults file for _deep_update_from_flat
            core_dir = tmp_path / 'core'
            core_dir.mkdir()
            (core_dir / 'settings_defaults.json').write_text('{}')

            sm.save()

            # Settings file should exist and contain our key
            assert settings_file.exists()
            data = json.loads(settings_file.read_text())
            assert data.get("TEST_KEY") == "test_value"

            # Tmp file should be cleaned up
            assert not (user_dir / 'settings.json.tmp').exists()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
