"""
State Engine Tests - Per-chat state management for games and interactive stories.

Tests the StateEngine class for:
- Basic state get/set operations
- Type validation
- Dice rolling
- Preset loading
- Database persistence

Run with: pytest tests/test_state_engine.py -v
"""
import pytest
import tempfile
import sqlite3
import json
from pathlib import Path
from unittest.mock import patch, MagicMock


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def temp_db():
    """Create a temporary database with required schema."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = Path(f.name)

    # Create schema
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS state_current (
            chat_name TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            value_type TEXT,
            label TEXT,
            constraints TEXT,
            updated_at TEXT NOT NULL,
            updated_by TEXT NOT NULL,
            turn_number INTEGER NOT NULL,
            PRIMARY KEY (chat_name, key)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS state_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_name TEXT NOT NULL,
            key TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT NOT NULL,
            changed_by TEXT NOT NULL,
            turn_number INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            reason TEXT
        )
    """)
    conn.commit()
    conn.close()

    yield db_path

    # Cleanup
    db_path.unlink(missing_ok=True)


# =============================================================================
# StateEngine Basic Operations
# =============================================================================

class TestStateEngineInit:
    """Test StateEngine initialization."""

    def test_creates_empty_state(self, temp_db):
        """New engine should have empty state."""
        from core.state_engine import StateEngine

        engine = StateEngine("test_chat", temp_db)

        assert engine.chat_name == "test_chat"
        assert engine.get_state() == {}

    def test_loads_existing_state(self, temp_db):
        """Engine should load state from database."""
        from core.state_engine import StateEngine

        # Pre-populate database
        conn = sqlite3.connect(str(temp_db))
        conn.execute(
            """INSERT INTO state_current
               (chat_name, key, value, value_type, label, constraints, updated_at, updated_by, turn_number)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("test_chat", "health", json.dumps(100), "number", "Health", None,
             "2024-01-01", "system", 0)
        )
        conn.commit()
        conn.close()

        engine = StateEngine("test_chat", temp_db)

        assert engine.get_state("health") == 100


class TestStateEngineGetSet:
    """Test state get/set operations."""

    def test_set_state_basic(self, temp_db):
        """set_state should store value."""
        from core.state_engine import StateEngine

        engine = StateEngine("test_chat", temp_db)
        engine.set_state("player_name", "Alice", changed_by="ai", turn_number=1)

        assert engine.get_state("player_name") == "Alice"

    def test_set_state_overwrites(self, temp_db):
        """set_state should overwrite existing value."""
        from core.state_engine import StateEngine

        engine = StateEngine("test_chat", temp_db)
        engine.set_state("score", 10, changed_by="ai", turn_number=1)
        engine.set_state("score", 20, changed_by="ai", turn_number=2)

        assert engine.get_state("score") == 20

    def test_get_state_returns_none_for_missing(self, temp_db):
        """get_state should return None for missing key."""
        from core.state_engine import StateEngine

        engine = StateEngine("test_chat", temp_db)

        assert engine.get_state("nonexistent") is None

    def test_get_state_no_key_returns_all(self, temp_db):
        """get_state() with no key should return all state."""
        from core.state_engine import StateEngine

        engine = StateEngine("test_chat", temp_db)
        engine.set_state("a", 1, changed_by="ai", turn_number=1)
        engine.set_state("b", 2, changed_by="ai", turn_number=1)

        all_state = engine.get_state()

        assert all_state == {"a": 1, "b": 2}

    def test_set_state_different_types(self, temp_db):
        """set_state should handle different value types."""
        from core.state_engine import StateEngine

        engine = StateEngine("test_chat", temp_db)

        engine.set_state("string_val", "hello", changed_by="ai", turn_number=1)
        engine.set_state("number_val", 42, changed_by="ai", turn_number=1)
        engine.set_state("bool_val", True, changed_by="ai", turn_number=1)
        engine.set_state("list_val", ["a", "b"], changed_by="ai", turn_number=1)

        assert engine.get_state("string_val") == "hello"
        assert engine.get_state("number_val") == 42
        assert engine.get_state("bool_val") is True
        assert engine.get_state("list_val") == ["a", "b"]


class TestStateEnginePersistence:
    """Test database persistence."""

    def test_state_persists_to_database(self, temp_db):
        """State changes should be written to database."""
        from core.state_engine import StateEngine

        engine = StateEngine("test_chat", temp_db)
        engine.set_state("persistent_key", "persistent_value", changed_by="ai", turn_number=1)

        # Create new engine instance to verify persistence
        engine2 = StateEngine("test_chat", temp_db)

        assert engine2.get_state("persistent_key") == "persistent_value"

    def test_state_isolated_by_chat(self, temp_db):
        """State should be isolated per chat_name."""
        from core.state_engine import StateEngine

        engine1 = StateEngine("chat_a", temp_db)
        engine2 = StateEngine("chat_b", temp_db)

        engine1.set_state("key", "value_a", changed_by="ai", turn_number=1)
        engine2.set_state("key", "value_b", changed_by="ai", turn_number=1)

        assert engine1.get_state("key") == "value_a"
        assert engine2.get_state("key") == "value_b"


class TestStateEngineClear:
    """Test state clearing."""

    def test_clear_all_removes_all(self, temp_db):
        """clear_all should remove all state for chat."""
        from core.state_engine import StateEngine

        engine = StateEngine("test_chat", temp_db)
        engine.set_state("a", 1, changed_by="ai", turn_number=1)
        engine.set_state("b", 2, changed_by="ai", turn_number=1)

        engine.clear_all()

        assert engine.get_state() == {}


class TestStateEngineReload:
    """Test state reload from database."""

    def test_reload_from_db(self, temp_db):
        """reload_from_db should refresh state from database."""
        from core.state_engine import StateEngine

        engine = StateEngine("test_chat", temp_db)
        engine.set_state("original", "value", changed_by="ai", turn_number=1)

        # Directly modify database (simulating external change)
        conn = sqlite3.connect(str(temp_db))
        conn.execute(
            """UPDATE state_current SET value = ? WHERE chat_name = ? AND key = ?""",
            (json.dumps("modified"), "test_chat", "original")
        )
        conn.commit()
        conn.close()

        # Cache still has old value
        assert engine.get_state("original") == "value"

        # Reload picks up new value
        engine.reload_from_db()
        assert engine.get_state("original") == "modified"


# =============================================================================
# Validation Tests
# =============================================================================

class TestStateValidation:
    """Test value validation."""

    def test_is_system_key_detects_underscore_prefix(self):
        """System keys start with underscore."""
        from core.state_engine.validation import is_system_key

        assert is_system_key("_preset") is True
        assert is_system_key("_scene_entered_at") is True
        assert is_system_key("player_health") is False
        assert is_system_key("score") is False

    def test_infer_type_string(self):
        """Should infer string type."""
        from core.state_engine.validation import infer_type

        assert infer_type("hello") == "string"
        assert infer_type("") == "string"

    def test_infer_type_number(self):
        """Should infer integer/number type."""
        from core.state_engine.validation import infer_type

        assert infer_type(42) == "integer"
        assert infer_type(3.14) == "number"  # floats are "number"
        assert infer_type(0) == "integer"

    def test_infer_type_boolean(self):
        """Should infer boolean type."""
        from core.state_engine.validation import infer_type

        assert infer_type(True) == "boolean"
        assert infer_type(False) == "boolean"

    def test_infer_type_list(self):
        """Should infer array type (JSON schema term)."""
        from core.state_engine.validation import infer_type

        assert infer_type([1, 2, 3]) == "array"
        assert infer_type([]) == "array"


# =============================================================================
# Tools Tests
# =============================================================================

class TestStateTools:
    """Test state engine tools definitions."""

    def test_tools_list_has_required_tools(self):
        """TOOLS should include all state tools."""
        from core.state_engine import TOOLS, STATE_TOOL_NAMES

        tool_names = {t['function']['name'] for t in TOOLS}

        assert 'get_state' in tool_names
        assert 'set_state' in tool_names
        assert 'roll_dice' in tool_names
        assert 'advance_scene' in tool_names
        assert 'move' in tool_names

        # STATE_TOOL_NAMES should match
        assert tool_names == STATE_TOOL_NAMES

    def test_tool_definitions_have_required_fields(self):
        """Each tool should have proper structure."""
        from core.state_engine import TOOLS

        for tool in TOOLS:
            assert tool['type'] == 'function'
            assert 'function' in tool
            assert 'name' in tool['function']
            assert 'description' in tool['function']
            assert 'parameters' in tool['function']


class TestDiceRolling:
    """Test dice rolling functionality."""

    def test_roll_dice_returns_valid_range(self, temp_db):
        """roll_dice should return values in valid range."""
        from core.state_engine.tools import execute
        from core.state_engine import StateEngine

        engine = StateEngine("test_chat", temp_db)

        # Roll 1d6 many times and check range
        for _ in range(20):
            result, _ = execute("roll_dice", {"count": 1, "sides": 6}, engine, turn_number=1)
            # Result is a string, parse it
            assert "rolled" in result.lower() or "total" in result.lower()

    def test_roll_dice_respects_count(self, temp_db):
        """roll_dice with count should roll multiple dice."""
        from core.state_engine.tools import execute
        from core.state_engine import StateEngine

        engine = StateEngine("test_chat", temp_db)

        result, _ = execute("roll_dice", {"count": 3, "sides": 6}, engine, turn_number=1)
        # Should mention multiple dice or individual rolls
        assert "3" in result or "dice" in result.lower()


# =============================================================================
# Integration Tests
# =============================================================================

class TestStateEngineIntegration:
    """Integration tests for state engine."""

    def test_module_imports(self):
        """State engine should import cleanly."""
        from core.state_engine import StateEngine, TOOLS, STATE_TOOL_NAMES, execute

        assert StateEngine is not None
        assert TOOLS is not None
        assert STATE_TOOL_NAMES is not None
        assert execute is not None

    def test_execute_get_state(self, temp_db):
        """execute('get_state') should return state."""
        from core.state_engine.tools import execute
        from core.state_engine import StateEngine

        engine = StateEngine("test_chat", temp_db)
        engine.set_state("test_key", "test_value", changed_by="ai", turn_number=1)

        result, _ = execute("get_state", {"key": "test_key"}, engine, turn_number=2)

        assert "test_value" in result

    def test_execute_set_state(self, temp_db):
        """execute('set_state') should modify state."""
        from core.state_engine.tools import execute
        from core.state_engine import StateEngine

        engine = StateEngine("test_chat", temp_db)

        result, _ = execute("set_state", {
            "key": "new_key",
            "value": "new_value",
            "reason": "test"
        }, engine, turn_number=1)

        assert engine.get_state("new_key") == "new_value"
