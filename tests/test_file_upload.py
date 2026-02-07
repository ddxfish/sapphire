"""Tests for file upload pipeline: extension mapping, flattening, history handling."""
import json
import pytest
from unittest.mock import patch, MagicMock


class TestExtToLang:
    """Test _ext_to_lang() extension-to-language mapping."""

    def test_python_extension(self):
        from core.chat.chat import _ext_to_lang
        assert _ext_to_lang("script.py") == "python"

    def test_javascript_extension(self):
        from core.chat.chat import _ext_to_lang
        assert _ext_to_lang("app.js") == "javascript"

    def test_typescript_extension(self):
        from core.chat.chat import _ext_to_lang
        assert _ext_to_lang("app.ts") == "typescript"

    def test_json_extension(self):
        from core.chat.chat import _ext_to_lang
        assert _ext_to_lang("config.json") == "json"

    def test_yaml_extension(self):
        from core.chat.chat import _ext_to_lang
        assert _ext_to_lang("config.yaml") == "yaml"
        assert _ext_to_lang("config.yml") == "yaml"

    def test_shell_extension(self):
        from core.chat.chat import _ext_to_lang
        assert _ext_to_lang("run.sh") == "bash"

    def test_rust_extension(self):
        from core.chat.chat import _ext_to_lang
        assert _ext_to_lang("main.rs") == "rust"

    def test_c_cpp_extensions(self):
        from core.chat.chat import _ext_to_lang
        assert _ext_to_lang("main.c") == "c"
        assert _ext_to_lang("main.cpp") == "cpp"
        assert _ext_to_lang("main.h") == "c"

    def test_unknown_extension_returns_text(self):
        from core.chat.chat import _ext_to_lang
        assert _ext_to_lang("file.xyz") == "text"

    def test_no_extension_returns_text(self):
        from core.chat.chat import _ext_to_lang
        assert _ext_to_lang("Makefile") == "text"

    def test_case_insensitive(self):
        from core.chat.chat import _ext_to_lang
        assert _ext_to_lang("README.MD") == "markdown"
        assert _ext_to_lang("script.PY") == "python"

    def test_dotfile_no_extension(self):
        """Dotfiles like .env have no extension per os.path.splitext."""
        from core.chat.chat import _ext_to_lang
        assert _ext_to_lang(".env") == "text"


class TestTextExtensions:
    """Test TEXT_EXTENSIONS dict completeness."""

    def test_all_values_are_strings(self):
        from core.chat.chat import TEXT_EXTENSIONS
        for ext, lang in TEXT_EXTENSIONS.items():
            assert isinstance(ext, str) and ext.startswith(".")
            assert isinstance(lang, str) and len(lang) > 0

    def test_common_extensions_present(self):
        from core.chat.chat import TEXT_EXTENSIONS
        for ext in ['.py', '.js', '.ts', '.json', '.md', '.txt', '.sh', '.html', '.css']:
            assert ext in TEXT_EXTENSIONS


class TestFormatMessagesForDisplay:
    """Test format_messages_for_display handles file blocks."""

    def test_plain_user_message(self):
        from core.api_fastapi import format_messages_for_display
        msgs = [{"role": "user", "content": "Hello", "timestamp": 1000}]
        result = format_messages_for_display(msgs)
        assert len(result) == 1
        assert result[0]["content"] == "Hello"

    def test_user_message_with_file_blocks(self):
        from core.api_fastapi import format_messages_for_display
        msgs = [{
            "role": "user",
            "timestamp": 1000,
            "content": [
                {"type": "text", "text": "Check this file"},
                {"type": "file", "filename": "test.py", "text": "print('hello')"}
            ]
        }]
        result = format_messages_for_display(msgs)
        assert result[0]["content"] == "Check this file"
        assert len(result[0]["files"]) == 1
        assert result[0]["files"][0]["filename"] == "test.py"
        assert result[0]["files"][0]["text"] == "print('hello')"

    def test_user_message_with_image_and_file(self):
        from core.api_fastapi import format_messages_for_display
        msgs = [{
            "role": "user",
            "timestamp": 1000,
            "content": [
                {"type": "text", "text": "Look at these"},
                {"type": "image", "data": "base64data", "media_type": "image/png"},
                {"type": "file", "filename": "notes.txt", "text": "some notes"}
            ]
        }]
        result = format_messages_for_display(msgs)
        assert "images" in result[0]
        assert "files" in result[0]
        assert len(result[0]["images"]) == 1
        assert len(result[0]["files"]) == 1

    def test_user_message_without_files_has_no_files_key(self):
        from core.api_fastapi import format_messages_for_display
        msgs = [{"role": "user", "content": "Just text", "timestamp": 1000}]
        result = format_messages_for_display(msgs)
        assert "files" not in result[0]

    def test_multiple_files(self):
        from core.api_fastapi import format_messages_for_display
        msgs = [{
            "role": "user",
            "timestamp": 1000,
            "content": [
                {"type": "text", "text": "Two files"},
                {"type": "file", "filename": "a.py", "text": "code_a"},
                {"type": "file", "filename": "b.js", "text": "code_b"}
            ]
        }]
        result = format_messages_for_display(msgs)
        assert len(result[0]["files"]) == 2


class TestHistoryFileFlattening:
    """Test that file blocks in history get flattened to code blocks for LLM."""

    def test_file_block_flattened_to_code_block(self):
        """File blocks in user messages should become fenced code blocks."""
        from core.chat.history import ConversationHistory

        history = ConversationHistory()

        # Add a user message with file block
        history.add_user_message([
            {"type": "text", "text": "Review this code"},
            {"type": "file", "filename": "example.py", "text": "def hello():\n    pass"}
        ])

        # Get messages for LLM - files should be flattened
        llm_msgs = history.get_messages_for_llm()
        user_msg = [m for m in llm_msgs if m["role"] == "user"][0]

        assert "Review this code" in user_msg["content"]
        assert "```python" in user_msg["content"]
        assert "# example.py" in user_msg["content"]
        assert "def hello():" in user_msg["content"]

    def test_file_block_uses_correct_language(self):
        """File extension should map to correct language in code fence."""
        from core.chat.history import ConversationHistory

        history = ConversationHistory()

        history.add_user_message([
            {"type": "text", "text": "Check this"},
            {"type": "file", "filename": "app.js", "text": "const x = 1;"}
        ])

        llm_msgs = history.get_messages_for_llm()
        user_msg = [m for m in llm_msgs if m["role"] == "user"][0]
        assert "```javascript" in user_msg["content"]

    def test_plain_text_message_unchanged(self):
        """Plain string messages should pass through unchanged."""
        from core.chat.history import ConversationHistory

        history = ConversationHistory()

        history.add_user_message("Just a question")
        llm_msgs = history.get_messages_for_llm()
        user_msg = [m for m in llm_msgs if m["role"] == "user"][0]
        assert user_msg["content"] == "Just a question"

    def test_file_token_counting(self, tmp_path):
        """File blocks should contribute to token counts."""
        from core.chat.history import ConversationHistory, count_message_tokens

        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": "Hello"},
                {"type": "file", "filename": "big.py", "text": "x = 1\n" * 100}
            ]
        }

        tokens = count_message_tokens(msg)
        assert tokens > 0
