"""Editing a user message keeps its non-text blocks (images/files).

`edit_message_by_timestamp` used to assign the new text over the whole
`content`, so a list-content (multimodal) message lost its image blocks on
edit. Now only the text is replaced; the message stays a block list.
"""
import pytest
from unittest.mock import patch

TEST_DEFAULTS = {"prompt": "default"}


@pytest.fixture
def mgr(tmp_path):
    with patch("core.chat.history.get_system_defaults",
               side_effect=lambda: dict(TEST_DEFAULTS)), \
         patch("core.chat.history.get_user_defaults",
               side_effect=lambda: dict(TEST_DEFAULTS)):
        from core.chat.history import ChatSessionManager
        yield ChatSessionManager(history_dir=str(tmp_path))


IMG = {"type": "image", "data": "abc", "media_type": "image/png"}
FILE = {"type": "file", "filename": "notes.txt", "text": "hi"}


def test_edit_keeps_image_and_file_blocks(mgr):
    mgr.add_user_message([{"type": "text", "text": "old"}, IMG, FILE])
    ts = mgr.get_messages()[0]["timestamp"]

    assert mgr.edit_message_by_timestamp("user", ts, "*new*\nline")

    content = mgr.get_messages()[0]["content"]
    assert content == [{"type": "text", "text": "*new*\nline"}, IMG, FILE]


def test_edit_collapses_multiple_text_blocks(mgr):
    mgr.add_user_message([{"type": "text", "text": "a"}, IMG, {"type": "text", "text": "b"}])
    ts = mgr.get_messages()[0]["timestamp"]

    assert mgr.edit_message_by_timestamp("user", ts, "edited")

    content = mgr.get_messages()[0]["content"]
    assert content == [{"type": "text", "text": "edited"}, IMG]


def test_edit_plain_string_stays_string(mgr):
    mgr.add_user_message("plain")
    ts = mgr.get_messages()[0]["timestamp"]

    assert mgr.edit_message_by_timestamp("user", ts, "*stage*\nsaid")

    assert mgr.get_messages()[0]["content"] == "*stage*\nsaid"


def test_assistant_edit_splits_think_back_out(mgr):
    mgr.add_user_message("q")
    mgr.add_assistant_final("old body", thinking="old think")
    ts = mgr.get_messages()[1]["timestamp"]

    # What the editor shows is the display reconstruction — think inline.
    assert mgr.edit_message_by_timestamp(
        "assistant", ts, "<think>new think</think>\n\nnew body")

    msg = mgr.get_messages()[1]
    assert msg["content"] == "new body"
    assert msg["thinking"] == "new think"
    # Display reconstructs exactly ONE think block.
    shown = mgr.get_messages_for_display()[1]["content"]
    assert shown.count("<think>") == 1


def test_assistant_edit_without_think_drops_thinking(mgr):
    mgr.add_user_message("q")
    mgr.add_assistant_final("old body", thinking="old think")
    ts = mgr.get_messages()[1]["timestamp"]

    assert mgr.edit_message_by_timestamp("assistant", ts, "just body")

    msg = mgr.get_messages()[1]
    assert msg["content"] == "just body"
    assert "thinking" not in msg
