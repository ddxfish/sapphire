"""Chat Manager deep search — search_chat_content (Chat Manager tabs wave).

Whole-store content search: {chat_name: matching_message_count}, both storage
formats, content-only matching, LIKE-wildcard escaping. Same fixture pattern
as test_chat_manager_v1a.py; the route is a thin wrapper over this method.
"""
import json
import sqlite3

import pytest
from unittest.mock import patch

TEST_DEFAULTS = {"prompt": "default"}


@pytest.fixture
def chat_env(tmp_path, monkeypatch):
    with patch("core.chat.history.get_system_defaults",
               side_effect=lambda: dict(TEST_DEFAULTS)), \
         patch("core.chat.history.get_user_defaults",
               side_effect=lambda: dict(TEST_DEFAULTS)):
        from core.chat.history import ChatSessionManager

        def make():
            return ChatSessionManager(history_dir=str(tmp_path))

        yield make


def add_blob_chat(tmp_path, name, messages):
    conn = sqlite3.connect(str(tmp_path / "sapphire_history.db"))
    conn.execute(
        "INSERT INTO chats (name, settings, messages, updated_at, storage_format) "
        "VALUES (?, '{}', ?, 't', 'blob')", (name, json.dumps(messages)))
    conn.commit()
    conn.close()


def test_rows_chat_counts_matching_messages(chat_env):
    mgr = chat_env()
    mgr.create_chat("voyage")
    mgr.append_messages_to_chat("voyage", [
        {"role": "user", "content": "the Titanic sets sail"},
        {"role": "assistant", "content": "Titanic clears the harbor"},
        {"role": "user", "content": "nothing relevant"}])
    assert mgr.search_chat_content("titanic") == {"voyage": 2}   # case-insensitive


def test_blob_chat_is_searched_too(chat_env, tmp_path):
    mgr = chat_env()
    add_blob_chat(tmp_path, "legacy", [
        {"role": "user", "content": "old blob mentions Titanic once"},
        {"role": "user", "content": "unrelated"}])
    assert mgr.search_chat_content("titanic") == {"legacy": 1}


def test_matches_content_only_never_json_keys(chat_env):
    mgr = chat_env()
    mgr.create_chat("plain")
    mgr.append_messages_to_chat("plain", [
        {"role": "user", "content": "hello world"}])
    # 'user'/'role'/'content' live in every message's JSON keys — a raw
    # message_json LIKE would hit all of them. Content-only must not.
    assert mgr.search_chat_content("user") == {}
    assert mgr.search_chat_content("role") == {}
    assert mgr.search_chat_content("hello") == {"plain": 1}


def test_like_wildcards_are_literal(chat_env):
    mgr = chat_env()
    mgr.create_chat("meta")
    mgr.append_messages_to_chat("meta", [
        {"role": "user", "content": "progress: 100% done"},
        {"role": "user", "content": "progress: 100x done"},
        {"role": "user", "content": "key a_b here"},
        {"role": "user", "content": "key axb here"}])
    assert mgr.search_chat_content("100%") == {"meta": 1}
    assert mgr.search_chat_content("a_b") == {"meta": 1}


def test_empty_and_missing_queries(chat_env):
    mgr = chat_env()
    mgr.create_chat("anything")
    assert mgr.search_chat_content("") == {}
    assert mgr.search_chat_content("   ") == {}
    assert mgr.search_chat_content(None) == {}
    assert mgr.search_chat_content("no-such-string-anywhere") == {}
