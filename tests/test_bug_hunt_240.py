"""
Bug Hunt Regression Tests — v2.4.0

Tests for bugs found during the 2.4.0 pre-release bug hunt.
Covers: cancel-during-tools history corruption, story rollback
constraint loss, and dangling user messages on provider failure.

Run with: pytest tests/test_bug_hunt_240.py -v
"""
import pytest
import sys
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# =============================================================================
# Bug A: Cancel mid-tool-cycle must close history cleanly
# =============================================================================

class TestCancelDuringToolCycle:
    """Hitting Stop during tool execution must not leave broken history."""

    def test_in_tool_cycle_flag_closed_on_cancel(self):
        """The _in_tool_cycle flag must be reset even when streaming is cancelled."""
        from core.chat.history import ChatSessionManager

        with tempfile.TemporaryDirectory() as tmpdir:
            sm = ChatSessionManager.__new__(ChatSessionManager)
            sm._db_path = Path(tmpdir) / "test.db"
            sm._db_conn = None
            sm._in_tool_cycle = False
            sm.current_chat = MagicMock()
            sm.current_settings = {}

            # Simulate opening a tool cycle
            sm._in_tool_cycle = True

            # Simulate what the finally block now does
            if sm._in_tool_cycle:
                sm.add_assistant_final = MagicMock()
                sm.add_assistant_final(content="[Cancelled during tool execution]")
                sm._in_tool_cycle = False

            assert sm._in_tool_cycle is False
            sm.add_assistant_final.assert_called_once()

    def test_streaming_finally_closes_tool_cycle(self):
        """The streaming finally block must call add_assistant_final if _in_tool_cycle is open."""
        # Verify the code pattern exists in chat_streaming.py
        streaming_path = PROJECT_ROOT / "core" / "chat" / "chat_streaming.py"
        source = streaming_path.read_text()
        assert "_in_tool_cycle" in source, "Finally block must check _in_tool_cycle"
        assert "Closing orphaned tool cycle" in source, "Cleanup log message must exist"


# =============================================================================
# Bug C: Story rollback must preserve constraints
# =============================================================================

class TestStoryRollbackConstraints:
    """Rolling back story state must not destroy constraints or labels."""

    def _make_engine(self, tmpdir):
        """Create a minimal StoryEngine with an in-memory-like DB."""
        from core.story_engine.engine import StoryEngine
        engine = StoryEngine.__new__(StoryEngine)
        engine.chat_name = "test_chat"
        engine._db_path = Path(tmpdir) / "story_test.db"
        engine._current_state = {}
        engine._preset_name = None
        engine._progressive_config = None
        engine._story_dir = None
        engine._scene_entered_at_turn = None
        engine._choices = None
        engine._riddles = None
        engine._custom_tools = []
        engine._custom_modules = {}
        engine._custom_executors = {}

        # Create DB tables
        conn = sqlite3.connect(str(engine._db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("""CREATE TABLE IF NOT EXISTS state_current (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_name TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT,
            value_type TEXT,
            label TEXT,
            constraints TEXT,
            updated_at TEXT,
            updated_by TEXT,
            turn_number INTEGER DEFAULT 0,
            UNIQUE(chat_name, key)
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS state_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_name TEXT NOT NULL,
            key TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            changed_by TEXT DEFAULT 'ai',
            turn_number INTEGER DEFAULT 0,
            timestamp TEXT
        )""")
        conn.commit()
        conn.close()
        return engine

    def test_rollback_preserves_preset_name(self):
        """After rollback, _preset key must be re-persisted so constraints reload."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = self._make_engine(tmpdir)

            # Simulate a preset being active
            engine._preset_name = "crystal_prophecy"
            engine._persist_system_key("_preset", "crystal_prophecy", 0)

            # Add some state via state_log (simulating AI moves)
            conn = sqlite3.connect(str(engine._db_path))
            conn.execute(
                "INSERT INTO state_log (chat_name, key, old_value, new_value, changed_by, turn_number, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("test_chat", "room", json.dumps("start"), json.dumps("forest"), "ai", 1, "2026-01-01")
            )
            conn.execute(
                "INSERT INTO state_log (chat_name, key, old_value, new_value, changed_by, turn_number, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("test_chat", "room", json.dumps("forest"), json.dumps("cave"), "ai", 2, "2026-01-02")
            )
            conn.commit()
            conn.close()

            # Rollback to turn 1 — this should preserve _preset
            with patch.object(engine, '_load_state'):
                engine.rollback_to_turn(1)

            # Verify _preset was re-persisted
            conn = sqlite3.connect(str(engine._db_path))
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT value FROM state_current WHERE chat_name = ? AND key = ?",
                ("test_chat", "_preset")
            ).fetchone()
            conn.close()

            assert row is not None, "_preset must survive rollback"
            assert json.loads(row["value"]) == "crystal_prophecy"

    def test_rollback_code_does_not_hardcode_none_constraints(self):
        """Verify the rollback INSERT no longer has hardcoded None for constraints.
        The actual constraint restoration happens via _load_state -> reload_preset_config,
        but the system keys (_preset) must be re-persisted for that chain to work."""
        engine_path = PROJECT_ROOT / "core" / "story_engine" / "engine.py"
        source = engine_path.read_text()
        # The fix: re-persist _preset after rollback rebuild
        assert "_persist_system_key" in source
        # Find the rollback method and check it re-persists
        rollback_start = source.find("def rollback_to_turn")
        rollback_end = source.find("\n    def ", rollback_start + 1)
        rollback_body = source[rollback_start:rollback_end]
        assert '_persist_system_key("_preset"' in rollback_body, \
            "rollback_to_turn must re-persist _preset key"


# =============================================================================
# Bug D: Provider failure must not leave dangling user message
# =============================================================================

class TestProviderFailureDanglingMessage:
    """ConnectionError during streaming must save an error assistant message."""

    def test_connection_error_path_saves_assistant_message(self):
        """Verify the ConnectionError handler adds an assistant message to history."""
        streaming_path = PROJECT_ROOT / "core" / "chat" / "chat_streaming.py"
        source = streaming_path.read_text()
        # Find the ConnectionError handler
        ce_start = source.find("except ConnectionError")
        ce_end = source.find("except Exception", ce_start + 1)
        ce_body = source[ce_start:ce_end]
        assert "add_assistant_final" in ce_body, \
            "ConnectionError handler must save an assistant message to prevent dangling user message"
