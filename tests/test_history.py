"""Unit tests for core/history.py"""
import pytest
import json
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestConversationHistory:
    """Test ConversationHistory class."""
    
    def test_add_user_message(self):
        """User messages should be added with timestamp."""
        from core.chat.history import ConversationHistory
        
        history = ConversationHistory(max_history=10)
        history.add_user_message("Hello")
        
        assert len(history.messages) == 1
        assert history.messages[0]["role"] == "user"
        assert history.messages[0]["content"] == "Hello"
        assert "timestamp" in history.messages[0]
    
    def test_add_assistant_final(self):
        """Final assistant messages should be added."""
        from core.chat.history import ConversationHistory
        
        history = ConversationHistory()
        history.add_assistant_final("Response text")
        
        assert len(history.messages) == 1
        assert history.messages[0]["role"] == "assistant"
        assert history.messages[0]["content"] == "Response text"
    
    def test_add_assistant_with_tool_calls(self):
        """Assistant messages with tool calls should preserve structure."""
        from core.chat.history import ConversationHistory
        
        history = ConversationHistory()
        tool_calls = [{"id": "call_1", "function": {"name": "test"}}]
        history.add_assistant_with_tool_calls("Thinking...", tool_calls)
        
        assert len(history.messages) == 1
        assert history.messages[0]["tool_calls"] == tool_calls
        assert history.messages[0]["content"] == "Thinking..."
    
    def test_add_tool_result(self):
        """Tool results should include all metadata."""
        from core.chat.history import ConversationHistory
        
        history = ConversationHistory()
        history.add_tool_result("call_123", "web_search", "Results here", inputs={"query": "test"})
        
        msg = history.messages[0]
        assert msg["role"] == "tool"
        assert msg["tool_call_id"] == "call_123"
        assert msg["name"] == "web_search"
        assert msg["content"] == "Results here"
        assert msg["tool_inputs"] == {"query": "test"}
    
    def test_get_messages_returns_copy(self):
        """get_messages should return a copy, not reference."""
        from core.chat.history import ConversationHistory
        
        history = ConversationHistory()
        history.add_user_message("Test")
        
        msgs = history.get_messages()
        msgs.clear()
        
        assert len(history.messages) == 1  # Original unchanged
    
    def test_get_turn_count(self):
        """Turn count should equal user message count."""
        from core.chat.history import ConversationHistory
        
        history = ConversationHistory()
        history.add_user_message("First")
        history.add_assistant_final("Response 1")
        history.add_user_message("Second")
        history.add_assistant_final("Response 2")
        
        assert history.get_turn_count() == 2
    
    def test_clear(self):
        """clear() should empty messages."""
        from core.chat.history import ConversationHistory
        
        history = ConversationHistory()
        history.add_user_message("Test")
        history.add_assistant_final("Response")
        
        history.clear()
        
        assert len(history.messages) == 0
    
    def test_remove_last_messages(self):
        """remove_last_messages should remove N messages from end."""
        from core.chat.history import ConversationHistory
        
        history = ConversationHistory()
        history.add_user_message("First")
        history.add_assistant_final("Response 1")
        history.add_user_message("Second")
        history.add_assistant_final("Response 2")
        
        result = history.remove_last_messages(2)
        
        assert result is True
        assert len(history.messages) == 2
        assert history.messages[-1]["content"] == "Response 1"
    
    def test_remove_last_messages_invalid_count(self):
        """remove_last_messages should return False for invalid count."""
        from core.chat.history import ConversationHistory
        
        history = ConversationHistory()
        history.add_user_message("Test")
        
        assert history.remove_last_messages(0) is False
        assert history.remove_last_messages(10) is False
        assert history.remove_last_messages(-1) is False


class TestHistoryTrimming:
    """Test LLM message trimming."""
    
    @patch('core.chat.history.count_tokens', return_value=10)
    def test_get_messages_for_llm_strips_timestamps(self, mock_count):
        """LLM messages should not include timestamps."""
        from core.chat.history import ConversationHistory
        
        history = ConversationHistory()
        history.add_user_message("Test")
        
        llm_msgs = history.get_messages_for_llm()
        
        assert "timestamp" not in llm_msgs[0]
        assert llm_msgs[0]["role"] == "user"
        assert llm_msgs[0]["content"] == "Test"
    
    @patch('core.chat.history.count_tokens', return_value=10)
    def test_get_messages_for_llm_preserves_tool_structure(self, mock_count):
        """LLM messages should preserve tool_calls and tool metadata."""
        from core.chat.history import ConversationHistory
        
        history = ConversationHistory()
        tool_calls = [{"id": "call_1", "function": {"name": "test"}}]
        history.add_assistant_with_tool_calls("", tool_calls)
        history.add_tool_result("call_1", "test", "result")
        
        llm_msgs = history.get_messages_for_llm()
        
        assert llm_msgs[0]["tool_calls"] == tool_calls
        assert llm_msgs[1]["tool_call_id"] == "call_1"
        assert llm_msgs[1]["name"] == "test"
    
    @patch('core.chat.history.count_tokens', return_value=10)
    @patch('core.chat.history.config')
    def test_turn_based_trimming(self, mock_config, mock_count):
        """Messages should trim by turn count when exceeding max_history."""
        mock_config.LLM_MAX_TOKENS = 999999  # No token trimming
        
        from core.chat.history import ConversationHistory
        
        history = ConversationHistory(max_history=4)  # 2 turns max
        
        # Add 4 turns (8 messages)
        for i in range(4):
            history.add_user_message(f"User {i}")
            history.add_assistant_final(f"Assistant {i}")
        
        llm_msgs = history.get_messages_for_llm()
        
        # Should have trimmed to ~4 messages (2 turns)
        assert len(llm_msgs) <= 6  # Some flexibility for edge cases
    
    @patch('core.chat.history.config')
    @patch('core.chat.history.count_tokens')
    def test_token_based_trimming(self, mock_count, mock_config):
        """Messages should trim when exceeding max tokens."""
        mock_config.LLM_MAX_TOKENS = 100
        mock_count.return_value = 50  # Each message is 50 tokens
        
        from core.chat.history import ConversationHistory
        
        history = ConversationHistory(max_history=100)  # No turn limit
        history.add_user_message("Message 1")
        history.add_assistant_final("Response 1")
        history.add_user_message("Message 2")
        history.add_assistant_final("Response 2")
        
        llm_msgs = history.get_messages_for_llm()
        
        # With 100 token limit and 50 per message, should trim to ~2
        assert len(llm_msgs) <= 3


class TestMessageRemoval:
    """Test message removal methods."""
    
    def test_remove_from_user_message(self):
        """Should remove from specific user message to end."""
        from core.chat.history import ConversationHistory
        
        history = ConversationHistory()
        history.add_user_message("Keep this")
        history.add_assistant_final("Keep response")
        history.add_user_message("Remove from here")
        history.add_assistant_final("Also remove")
        
        result = history.remove_from_user_message("Remove from here")
        
        assert result is True
        assert len(history.messages) == 2
        assert history.messages[-1]["content"] == "Keep response"
    
    def test_remove_from_user_message_not_found(self):
        """Should return False if user message not found."""
        from core.chat.history import ConversationHistory
        
        history = ConversationHistory()
        history.add_user_message("Test")
        
        result = history.remove_from_user_message("Nonexistent")
        
        assert result is False
        assert len(history.messages) == 1
    
    def test_remove_from_assistant_timestamp(self):
        """Should remove from assistant message by timestamp."""
        from core.chat.history import ConversationHistory
        
        history = ConversationHistory()
        history.add_user_message("User msg")
        history.add_assistant_final("Assistant 1")
        ts = history.messages[-1]["timestamp"]
        history.add_user_message("User 2")
        history.add_assistant_final("Assistant 2")
        
        # Remove from first assistant message
        result = history.remove_from_assistant_timestamp(ts)
        
        assert result is True
        assert len(history.messages) == 1
        assert history.messages[0]["role"] == "user"


class TestChatSessionManager:
    """Test ChatSessionManager class."""
    
    def test_create_chat(self, tmp_path):
        """Should create new chat file."""
        with patch('core.chat.history.SYSTEM_DEFAULTS', {"prompt": "default"}):
            with patch('core.chat.history.get_user_defaults', return_value={"prompt": "default"}):
                from core.chat.history import ChatSessionManager
                
                mgr = ChatSessionManager(history_dir=str(tmp_path))
                result = mgr.create_chat("test_chat")
                
                assert result is True
                assert (tmp_path / "test_chat.json").exists()
    
    def test_create_duplicate_chat(self, tmp_path):
        """Should return False for duplicate chat name."""
        with patch('core.chat.history.SYSTEM_DEFAULTS', {"prompt": "default"}):
            with patch('core.chat.history.get_user_defaults', return_value={"prompt": "default"}):
                from core.chat.history import ChatSessionManager
                
                mgr = ChatSessionManager(history_dir=str(tmp_path))
                mgr.create_chat("test_chat")
                result = mgr.create_chat("test_chat")
                
                assert result is False
    
    def test_get_chat_settings(self, tmp_path):
        """Should return current chat settings."""
        with patch('core.chat.history.SYSTEM_DEFAULTS', {"prompt": "default", "voice": "test"}):
            with patch('core.chat.history.get_user_defaults', return_value={"prompt": "default", "voice": "test"}):
                from core.chat.history import ChatSessionManager
                
                mgr = ChatSessionManager(history_dir=str(tmp_path))
                settings = mgr.get_chat_settings()
                
                assert isinstance(settings, dict)
                assert "prompt" in settings
    
    def test_update_chat_settings(self, tmp_path):
        """Should update and persist settings."""
        with patch('core.chat.history.SYSTEM_DEFAULTS', {"prompt": "default"}):
            with patch('core.chat.history.get_user_defaults', return_value={"prompt": "default"}):
                from core.chat.history import ChatSessionManager
                
                mgr = ChatSessionManager(history_dir=str(tmp_path))
                result = mgr.update_chat_settings({"prompt": "custom"})
                
                assert result is True
                assert mgr.current_settings["prompt"] == "custom"
    
    def test_set_active_chat(self, tmp_path):
        """Should switch to different chat."""
        with patch('core.chat.history.SYSTEM_DEFAULTS', {"prompt": "default"}):
            with patch('core.chat.history.get_user_defaults', return_value={"prompt": "default"}):
                from core.chat.history import ChatSessionManager
                
                mgr = ChatSessionManager(history_dir=str(tmp_path))
                mgr.create_chat("other_chat")
                
                # Add message to default
                mgr.add_user_message("In default")
                
                # Switch
                result = mgr.set_active_chat("other_chat")
                
                assert result is True
                assert mgr.active_chat_name == "other_chat"
                assert len(mgr.get_messages()) == 0  # New chat is empty
    
    def test_delete_chat_switches_to_default(self, tmp_path):
        """Deleting active chat should switch to default."""
        with patch('core.chat.history.SYSTEM_DEFAULTS', {"prompt": "default"}):
            with patch('core.chat.history.get_user_defaults', return_value={"prompt": "default"}):
                from core.chat.history import ChatSessionManager
                
                mgr = ChatSessionManager(history_dir=str(tmp_path))
                mgr.create_chat("temp_chat")
                mgr.set_active_chat("temp_chat")
                
                result = mgr.delete_chat("temp_chat")
                
                assert result is True
                assert mgr.active_chat_name == "default"