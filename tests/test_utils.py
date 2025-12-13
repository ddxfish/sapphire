"""Unit tests for utility functions in chat_tool_calling.py"""
import pytest
from unittest.mock import patch


class TestStripUIMarkers:
    """Test strip_ui_markers function."""
    
    def test_strips_img_marker(self):
        """Should remove <<IMG::...>> markers."""
        from core.chat.chat_tool_calling import strip_ui_markers
        
        content = "Here's an image: <<IMG::abc123>> Pretty cool!"
        result = strip_ui_markers(content)
        
        assert "<<IMG::" not in result
        assert "abc123" not in result
        assert "Pretty cool" in result
    
    def test_strips_file_marker(self):
        """Should remove <<FILE::...>> markers."""
        from core.chat.chat_tool_calling import strip_ui_markers
        
        content = "Created file <<FILE::report.pdf>> for you"
        result = strip_ui_markers(content)
        
        assert "<<FILE::" not in result
        assert "Created file" in result
        assert "for you" in result
    
    def test_strips_multiple_markers(self):
        """Should remove all markers from content."""
        from core.chat.chat_tool_calling import strip_ui_markers
        
        content = "<<IMG::1>> text <<FILE::2>> more <<IMG::3>>"
        result = strip_ui_markers(content)
        
        assert "<<" not in result
        assert "text" in result
        assert "more" in result
    
    def test_preserves_content_without_markers(self):
        """Should return content unchanged if no markers."""
        from core.chat.chat_tool_calling import strip_ui_markers
        
        content = "Just regular text here"
        result = strip_ui_markers(content)
        
        assert result == content
    
    def test_handles_empty_string(self):
        """Should handle empty string."""
        from core.chat.chat_tool_calling import strip_ui_markers
        
        assert strip_ui_markers("") == ""
    
    def test_handles_none(self):
        """Should handle None input."""
        from core.chat.chat_tool_calling import strip_ui_markers
        
        assert strip_ui_markers(None) is None


class TestFilterToThinkingOnly:
    """Test filter_to_thinking_only function."""
    
    def test_extracts_think_tags(self):
        """Should extract content within <think> tags."""
        from core.chat.chat_tool_calling import filter_to_thinking_only
        
        content = "<think>I should search for this</think> Let me help you!"
        result = filter_to_thinking_only(content)
        
        assert "<think>" in result
        assert "I should search" in result
        assert "Let me help you" not in result
    
    def test_extracts_multiple_think_blocks(self):
        """Should extract all think blocks."""
        from core.chat.chat_tool_calling import filter_to_thinking_only
        
        content = "<think>First thought</think> words <think>Second thought</think>"
        result = filter_to_thinking_only(content)
        
        assert "First thought" in result
        assert "Second thought" in result
        assert "words" not in result or "<think>" in result  # words stripped or wrapped
    
    def test_wraps_content_without_think_tags(self):
        """Content without think tags should be wrapped."""
        from core.chat.chat_tool_calling import filter_to_thinking_only
        
        content = "Just some planning text"
        result = filter_to_thinking_only(content)
        
        assert "<think>" in result
        assert "</think>" in result
        assert "Just some planning text" in result
    
    def test_handles_empty_string(self):
        """Should return empty string for empty input."""
        from core.chat.chat_tool_calling import filter_to_thinking_only
        
        assert filter_to_thinking_only("") == ""
    
    def test_handles_none(self):
        """Should handle None input."""
        from core.chat.chat_tool_calling import filter_to_thinking_only
        
        assert filter_to_thinking_only(None) == ""
    
    def test_handles_seed_think_tags(self):
        """Should handle <seed:think> variant tags."""
        from core.chat.chat_tool_calling import filter_to_thinking_only
        
        content = "<seed:think>Planning here</seed:think> Response text"
        result = filter_to_thinking_only(content)
        
        # Should extract the seed:think content
        assert "Planning here" in result


class TestWrapToolResult:
    """Test wrap_tool_result function."""
    
    def test_creates_proper_structure(self):
        """Should create properly formatted tool message."""
        from core.chat.chat_tool_calling import wrap_tool_result
        
        result = wrap_tool_result("call_123", "web_search", "Found results")
        
        assert result["role"] == "tool"
        assert result["tool_call_id"] == "call_123"
        assert result["name"] == "web_search"
        assert result["content"] == "Found results"
    
    def test_strips_ui_markers_from_result(self):
        """Should strip UI markers from result content."""
        from core.chat.chat_tool_calling import wrap_tool_result
        
        result = wrap_tool_result("call_1", "test", "Result <<IMG::abc>> here")
        
        assert "<<IMG::" not in result["content"]
        assert "Result" in result["content"]
        assert "here" in result["content"]