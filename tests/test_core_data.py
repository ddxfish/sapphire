"""
Phase 4: Core Data Layer Tests

Tests the foundational data management: history, prompts, settings, toolsets.
These are the silent failure points - if they break, the app misbehaves without errors.

Run with: pytest tests/test_core_data.py -v
"""
import pytest
import sys
import json
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock
from datetime import datetime

# Add project root
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# =============================================================================
# ConversationHistory Tests
# =============================================================================

class TestConversationHistory:
    """Test basic message handling."""
    
    def test_conversation_history_imports(self):
        """Should import ConversationHistory."""
        from core.chat.history import ConversationHistory
        assert ConversationHistory is not None
    
    def test_add_user_message(self):
        """Should add user message with timestamp."""
        from core.chat.history import ConversationHistory
        
        history = ConversationHistory(max_history=10)
        history.add_user_message("Hello")
        
        assert len(history.messages) == 1
        assert history.messages[0]["role"] == "user"
        assert history.messages[0]["content"] == "Hello"
        assert "timestamp" in history.messages[0]
    
    def test_add_assistant_final(self):
        """Should add assistant final message."""
        from core.chat.history import ConversationHistory
        
        history = ConversationHistory(max_history=10)
        history.add_assistant_final("Hi there!")
        
        assert len(history.messages) == 1
        assert history.messages[0]["role"] == "assistant"
        assert history.messages[0]["content"] == "Hi there!"
    
    def test_get_messages_returns_list(self):
        """get_messages should return messages list."""
        from core.chat.history import ConversationHistory
        
        history = ConversationHistory(max_history=10)
        history.add_user_message("Test")
        
        messages = history.get_messages()
        assert len(messages) == 1
    
    def test_clear_removes_all_messages(self):
        """clear() should remove all messages."""
        from core.chat.history import ConversationHistory
        
        history = ConversationHistory(max_history=10)
        history.add_user_message("One")
        history.add_assistant_final("Two")
        history.clear()
        
        assert len(history.messages) == 0
    
    def test_remove_last_messages(self):
        """Should remove last N messages."""
        from core.chat.history import ConversationHistory
        
        history = ConversationHistory(max_history=10)
        history.add_user_message("First")
        history.add_assistant_final("Second")
        history.add_user_message("Third")
        
        history.remove_last_messages(2)
        
        assert len(history.messages) == 1
        assert history.messages[0]["content"] == "First"
    
    def test_add_assistant_with_tool_calls(self):
        """Should add assistant message with tool calls."""
        from core.chat.history import ConversationHistory
        
        history = ConversationHistory(max_history=10)
        tool_calls = [{"id": "tc1", "function": {"name": "test"}}]
        history.add_assistant_with_tool_calls("Calling tool", tool_calls)
        
        assert len(history.messages) == 1
        assert history.messages[0]["tool_calls"] == tool_calls
    
    def test_add_tool_result(self):
        """Should add tool result message."""
        from core.chat.history import ConversationHistory
        
        history = ConversationHistory(max_history=10)
        history.add_tool_result("tc1", "test_func", "Result data")
        
        assert len(history.messages) == 1
        assert history.messages[0]["role"] == "tool"
        assert history.messages[0]["tool_call_id"] == "tc1"


# =============================================================================
# ChatSessionManager Tests
# =============================================================================

class TestChatSessionManager:
    """Test session management."""
    
    @pytest.fixture
    def temp_history_dir(self):
        """Create temporary history directory."""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    def test_session_manager_creates_default_chat(self, temp_history_dir):
        """Should create database with default chat on init."""
        from core.chat.history import ChatSessionManager

        manager = ChatSessionManager(history_dir=temp_history_dir)

        # Now uses SQLite - check database exists and default chat is loaded
        db_path = Path(temp_history_dir) / "sapphire_history.db"
        assert db_path.exists()
        assert manager.active_chat_name == "default"
    
    def test_session_manager_list_chat_files(self, temp_history_dir):
        """Should list available chat files."""
        from core.chat.history import ChatSessionManager
        
        manager = ChatSessionManager(history_dir=temp_history_dir)
        chats = manager.list_chat_files()
        
        assert isinstance(chats, list)
        # Should have at least default
        assert len(chats) >= 1
    
    def test_session_manager_create_chat(self, temp_history_dir):
        """Should create new chat in database."""
        from core.chat.history import ChatSessionManager

        manager = ChatSessionManager(history_dir=temp_history_dir)
        result = manager.create_chat("test_chat")

        assert result is True
        # Verify chat exists by listing chats (returns list of dicts with 'name' key)
        chats = manager.list_chat_files()
        chat_names = [c['name'] for c in chats]
        assert "test_chat" in chat_names
    
    def test_session_manager_set_active_chat(self, temp_history_dir):
        """Should switch between chats."""
        from core.chat.history import ChatSessionManager
        
        manager = ChatSessionManager(history_dir=temp_history_dir)
        manager.create_chat("other")
        
        result = manager.set_active_chat("other")
        
        assert result is True
        assert manager.active_chat_name == "other"
    
    def test_session_manager_get_active_chat_name(self, temp_history_dir):
        """Should return active chat name."""
        from core.chat.history import ChatSessionManager
        
        manager = ChatSessionManager(history_dir=temp_history_dir)
        
        assert manager.get_active_chat_name() == "default"
    
    def test_session_manager_delete_chat(self, temp_history_dir):
        """Should delete chat."""
        from core.chat.history import ChatSessionManager
        
        manager = ChatSessionManager(history_dir=temp_history_dir)
        manager.create_chat("deleteme")
        
        result = manager.delete_chat("deleteme")
        
        assert result is True
        assert not (Path(temp_history_dir) / "deleteme.json").exists()
    
    def test_session_manager_delete_default_recreates(self, temp_history_dir):
        """Deleting default chat should recreate it."""
        from core.chat.history import ChatSessionManager

        manager = ChatSessionManager(history_dir=temp_history_dir)

        result = manager.delete_chat("default")

        # Should succeed but recreate default
        assert result is True
        # Verify default chat still exists in database (returns list of dicts with 'name' key)
        chats = manager.list_chat_files()
        chat_names = [c['name'] for c in chats]
        assert "default" in chat_names
    
    def test_session_manager_add_user_message(self, temp_history_dir):
        """Should add user message through manager."""
        from core.chat.history import ChatSessionManager
        
        manager = ChatSessionManager(history_dir=temp_history_dir)
        manager.add_user_message("Test message")
        
        messages = manager.get_messages()
        assert len(messages) >= 1
        assert any(m.get("content") == "Test message" for m in messages)


# =============================================================================
# Prompt State Tests
# =============================================================================

class TestPromptState:
    """Test prompt assembly and state management."""
    
    def test_prompt_state_imports(self):
        """Should import prompt_state module."""
        from core.modules.system import prompt_state
        assert prompt_state is not None
    
    def test_get_current_state_function_exists(self):
        """get_current_state function should exist."""
        from core.modules.system import prompt_state
        
        assert callable(prompt_state.get_current_state)
    
    def test_get_prompt_mode(self):
        """Should return mode string."""
        from core.modules.system import prompt_state
        
        mode = prompt_state.get_prompt_mode()
        
        assert mode in ["monolith", "assembled"]
    
    def test_is_assembled_mode_returns_bool(self):
        """is_assembled_mode should return boolean."""
        from core.modules.system import prompt_state
        
        result = prompt_state.is_assembled_mode()
        
        assert isinstance(result, bool)
    
    def test_assemble_prompt_function_exists(self):
        """assemble_prompt function should exist."""
        from core.modules.system import prompt_state
        
        assert callable(prompt_state.assemble_prompt)
    
    def test_get_current_spice(self):
        """get_current_spice should return spice or None."""
        from core.modules.system import prompt_state
        
        result = prompt_state.get_current_spice()
        
        # Can be None or a spice dict
        assert result is None or isinstance(result, (dict, str))


# =============================================================================
# Prompt Manager Tests
# =============================================================================

class TestPromptManager:
    """Test PromptManager functionality."""
    
    def test_prompt_manager_imports(self):
        """Should import PromptManager."""
        from core.modules.system.prompt_manager import PromptManager
        assert PromptManager is not None
    
    def test_prompt_manager_has_assemble_method(self):
        """PromptManager should have assemble_from_components method."""
        from core.modules.system.prompt_manager import PromptManager
        
        assert hasattr(PromptManager, 'assemble_from_components')
    
    def test_prompt_manager_has_reload_method(self):
        """PromptManager should have reload method."""
        from core.modules.system.prompt_manager import PromptManager
        
        assert hasattr(PromptManager, 'reload')
    
    def test_prompt_manager_initializes(self):
        """PromptManager should initialize without error."""
        from core.modules.system.prompt_manager import PromptManager
        
        pm = PromptManager()
        
        # Should exist after init
        assert pm is not None
    
    def test_prompt_manager_has_replace_templates(self):
        """PromptManager should have template replacement method."""
        from core.modules.system.prompt_manager import PromptManager
        
        assert hasattr(PromptManager, '_replace_templates')


# =============================================================================
# Settings Manager Tests
# =============================================================================

class TestSettingsManager:
    """Test settings loading and management."""
    
    def test_settings_manager_imports(self):
        """Should import SettingsManager."""
        from core.settings_manager import SettingsManager
        assert SettingsManager is not None
    
    def test_settings_manager_has_get_method(self):
        """SettingsManager should have get method."""
        from core.settings_manager import SettingsManager
        
        assert hasattr(SettingsManager, 'get')
    
    def test_settings_manager_loads_defaults(self):
        """Should load settings_defaults.json."""
        from core.settings_manager import SettingsManager
        
        sm = SettingsManager()
        
        # Check that defaults were loaded
        assert sm._defaults is not None
        assert isinstance(sm._defaults, dict)
    
    def test_settings_get_returns_value(self):
        """get() should return setting value."""
        from core.settings_manager import SettingsManager
        
        sm = SettingsManager()
        
        # Get a known default setting
        value = sm.get("TTS_ENABLED")
        
        assert value is not None
    
    def test_settings_get_with_default(self):
        """get() should return default for missing key."""
        from core.settings_manager import SettingsManager
        
        sm = SettingsManager()
        
        value = sm.get("NONEXISTENT_KEY", default="fallback")
        
        assert value == "fallback"
    
    def test_settings_getattr_access(self):
        """Should support attribute access."""
        from core.settings_manager import SettingsManager
        
        sm = SettingsManager()
        
        # Attribute access should work
        assert hasattr(sm, '_config')
    
    def test_settings_config_merged(self):
        """Config should merge defaults with user settings."""
        from core.settings_manager import SettingsManager
        
        sm = SettingsManager()
        
        # _config should have merged values
        assert sm._config is not None
        assert isinstance(sm._config, dict)


# =============================================================================
# Toolset Manager Tests
# =============================================================================

class TestToolsetManager:
    """Test toolset loading and resolution."""
    
    def test_toolset_manager_imports(self):
        """Should import toolset_manager."""
        from core.modules.system.toolsets.toolset_manager import ToolsetManager
        assert ToolsetManager is not None
    
    def test_toolset_manager_has_get_toolset_method(self):
        """Should have get_toolset method."""
        from core.modules.system.toolsets.toolset_manager import ToolsetManager
        
        assert hasattr(ToolsetManager, 'get_toolset')
    
    def test_toolset_manager_has_get_toolset_names_method(self):
        """Should have get_toolset_names method."""
        from core.modules.system.toolsets.toolset_manager import ToolsetManager
        
        assert hasattr(ToolsetManager, 'get_toolset_names')
    
    def test_toolset_manager_loads_toolsets(self):
        """Should load toolsets on init."""
        from core.modules.system.toolsets.toolset_manager import ToolsetManager
        
        tm = ToolsetManager()
        
        # Should have loaded some toolsets
        assert tm._toolsets is not None
    
    def test_get_toolset_names_returns_list(self):
        """get_toolset_names should return list of names."""
        from core.modules.system.toolsets.toolset_manager import ToolsetManager
        
        tm = ToolsetManager()
        toolsets = tm.get_toolset_names()
        
        assert isinstance(toolsets, list)
    
    def test_get_toolset_functions_returns_list(self):
        """get_toolset_functions should return function list."""
        from core.modules.system.toolsets.toolset_manager import ToolsetManager
        
        tm = ToolsetManager()
        toolsets = tm.get_toolset_names()
        
        if toolsets:
            funcs = tm.get_toolset_functions(toolsets[0])
            assert isinstance(funcs, list)
    
    def test_get_toolset_functions_invalid_returns_empty(self):
        """get_toolset_functions with invalid name should return empty."""
        from core.modules.system.toolsets.toolset_manager import ToolsetManager
        
        tm = ToolsetManager()
        funcs = tm.get_toolset_functions("nonexistent_toolset_xyz")
        
        assert funcs == []


# =============================================================================
# Token Counting Tests
# =============================================================================

class TestTokenCounting:
    """Test token counting utility."""
    
    def test_count_tokens_exists(self):
        """count_tokens function should exist."""
        from core.chat.history import count_tokens
        assert callable(count_tokens)
    
    def test_count_tokens_with_mock(self):
        """count_tokens should work (mocked for network issues)."""
        # Mock tiktoken to avoid network calls
        with patch('core.chat.history.get_tokenizer') as mock_tok:
            mock_encoder = MagicMock()
            mock_encoder.encode.return_value = [1, 2, 3]  # 3 tokens
            mock_tok.return_value = mock_encoder
            
            from core.chat.history import count_tokens
            result = count_tokens("Hello world")
            
            assert isinstance(result, int)
            assert result == 3


# =============================================================================
# Integration Tests
# =============================================================================

class TestDataLayerIntegration:
    """Integration tests across data layer."""
    
    def test_chat_defaults_loaded(self):
        """Should load chat defaults."""
        from core.chat.history import get_user_defaults, SYSTEM_DEFAULTS
        
        defaults = get_user_defaults()
        
        assert isinstance(defaults, dict)
        assert "prompt" in defaults
        assert "ability" in defaults
    
    def test_system_defaults_complete(self):
        """SYSTEM_DEFAULTS should have all required keys."""
        from core.chat.history import SYSTEM_DEFAULTS
        
        required_keys = ["prompt", "ability", "voice", "spice_enabled"]
        
        for key in required_keys:
            assert key in SYSTEM_DEFAULTS, f"Missing key: {key}"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])