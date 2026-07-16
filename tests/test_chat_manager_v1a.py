"""Chat Manager v1a — by-name storage operations (tmp/chat-manager.md).

clear_chat / export_chat / rename_chat / list_chat_files(stats=True) on the
SessionManager, plus knowledge rename_scope. Route layer is a thin loop over
these; live-call and agent guards are exercised by the live system.
"""
import json
import sqlite3
from pathlib import Path

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


def raw(tmp_path, sql, params=()):
    conn = sqlite3.connect(str(tmp_path / "sapphire_history.db"))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


class TestClearChatByName:
    def test_clears_non_active_rows_chat(self, chat_env, tmp_path):
        mgr = chat_env()
        mgr.create_chat("bulk_target")
        mgr.append_messages_to_chat("bulk_target", [
            {"role": "user", "content": "wipe me"}])
        mgr.save_tool_image("bt_img", b"X", "image/png", chat_name="bulk_target")

        assert mgr.clear_chat("bulk_target")
        assert mgr.active_chat_name == "default"  # never switched
        assert raw(tmp_path, "SELECT * FROM chat_messages WHERE chat_name='bulk_target'") == []
        assert raw(tmp_path, "SELECT * FROM tool_images WHERE chat_name='bulk_target'") == []
        assert mgr.export_chat("bulk_target")["messages"] == []

    def test_clears_active_chat_including_memory(self, chat_env):
        mgr = chat_env()
        mgr.add_user_message("in memory too")
        assert mgr.clear_chat("default")
        assert mgr.get_messages() == []  # in-memory reset via clear()

    def test_missing_chat_returns_false(self, chat_env):
        assert not chat_env().clear_chat("ghost")


class TestExportChat:
    def test_export_is_raw_and_untrimmed(self, chat_env):
        mgr = chat_env()
        mgr.add_user_message("q")
        mgr.add_assistant_with_tool_calls(
            "", [{"id": "ce", "type": "function",
                  "function": {"name": "t", "arguments": "{}"}}])
        mgr.add_tool_result("ce", "t", "res", inputs={"a": 1})
        mgr.add_assistant_final("done", thinking="thought")

        out = mgr.export_chat("default")
        assert out["messages"] == mgr.get_messages()  # timestamps, thinking, tool_inputs intact
        assert out["settings"]["prompt"] == "default"
        assert mgr.export_chat("ghost") is None

    def test_export_blob_chat(self, chat_env, tmp_path):
        mgr = chat_env()
        conn = sqlite3.connect(str(tmp_path / "sapphire_history.db"))
        msgs = [{"role": "user", "content": "old blob", "timestamp": "t"}]
        conn.execute(
            "INSERT INTO chats (name, settings, messages, updated_at, storage_format) "
            "VALUES ('legacy', '{}', ?, 't', 'blob')", (json.dumps(msgs),))
        conn.commit(); conn.close()
        assert mgr.export_chat("legacy")["messages"] == msgs


class TestRenameChat:
    def test_rename_moves_every_tentacle(self, chat_env, tmp_path):
        mgr = chat_env()
        mgr.create_chat("old_name")
        mgr.append_messages_to_chat("old_name", [{"role": "user", "content": "hi"}])
        mgr.save_tool_image("rn_img", b"X", "image/png", chat_name="old_name")

        ok, result = mgr.rename_chat("old_name", "New Name")
        assert ok
        assert result == "new_name"  # sanitized like create_chat
        assert raw(tmp_path, "SELECT 1 FROM chats WHERE name='old_name'") == []
        assert len(raw(tmp_path, "SELECT 1 FROM chat_messages WHERE chat_name='new_name'")) == 1
        assert len(raw(tmp_path, "SELECT 1 FROM tool_images WHERE chat_name='new_name'")) == 1
        # Content survives the move intact.
        assert mgr.export_chat("new_name")["messages"][0]["content"] == "hi"

    def test_rename_active_chat_updates_pointer(self, chat_env, tmp_path):
        mgr = chat_env()
        mgr.create_chat("workbench")
        mgr.set_active_chat("workbench")
        mgr.add_user_message("keep me")

        ok, result = mgr.rename_chat("workbench", "garage")
        assert ok
        assert mgr.active_chat_name == "garage"
        assert (tmp_path / ".active_chat").read_text().strip() == "garage"
        # Saves keep flowing to the new name (watermark state moved too).
        mgr.add_user_message("after rename")
        assert len(raw(tmp_path, "SELECT 1 FROM chat_messages WHERE chat_name='garage'")) == 2
        # Fresh instance restores the renamed chat as active.
        assert chat_env().active_chat_name == "garage"

    def test_rename_refusals(self, chat_env):
        mgr = chat_env()
        mgr.create_chat("a_chat")
        mgr.create_chat("b_chat")
        assert mgr.rename_chat("default", "anything")[0] is False   # default blocked
        assert mgr.rename_chat("a_chat", "b_chat")[0] is False      # collision
        assert mgr.rename_chat("ghost", "whatever")[0] is False     # missing
        assert mgr.rename_chat("a_chat", "!!!")[0] is False         # sanitizes to empty
        assert mgr.rename_chat("a_chat", "a_chat")[0] is False      # unchanged


class TestStats:
    def test_stats_adds_size_bytes(self, chat_env):
        mgr = chat_env()
        mgr.add_user_message("some content for sizing")
        mgr.save_tool_image("sz_img", b"0123456789", "image/png")

        plain = {c["name"]: c for c in mgr.list_chat_files()}
        assert "size_bytes" not in plain["default"]

        stat = {c["name"]: c for c in mgr.list_chat_files(stats=True)}
        assert stat["default"]["size_bytes"] > 10  # rows bytes + 10 image bytes
        assert "created" in stat["default"]


@pytest.fixture
def kt_tmp(tmp_path, monkeypatch):
    """knowledge_tools pointed at a THROWAWAY db. The path lives in the
    module-global `_db_path` cache (via _get_db_path) — patch THAT, not an
    invented constant: a raising=False patch of a wrong name silently runs
    the test against the REAL user knowledge.db (burned 2026-07-10)."""
    from plugins.memory.tools import knowledge_tools as kt
    monkeypatch.setattr(kt, "_db_path", tmp_path / "knowledge.db")
    # _ensure_db latches _db_initialized after the FIRST init — a second
    # test's fresh tmp DB would never get schema. Reset it alongside.
    monkeypatch.setattr(kt, "_db_initialized", False)
    # Sanity tripwire: the connection must resolve inside tmp_path.
    assert str(tmp_path) in str(kt._get_db_path())
    return kt


class TestKnowledgeRenameScope:
    def test_rename_scope_moves_tabs(self, kt_tmp):
        kt = kt_tmp
        kt.create_scope("__rag__:old_chat")
        assert kt.rename_scope("__rag__:old_chat", "__rag__:new_chat")
        names = [s["name"] for s in kt.get_scopes()]
        assert "__rag__:new_chat" in names
        assert "__rag__:old_chat" not in names

    def test_rename_missing_scope_is_noop_true(self, kt_tmp):
        kt = kt_tmp
        assert kt.rename_scope("__rag__:never_existed", "__rag__:x") is True
        assert kt.rename_scope("default", "nope") is False


class TestReapSweepsRows:
    def test_reap_deletes_message_rows_and_watermark(self, chat_env, tmp_path):
        """[REGRESSION_GUARD] Ephemeral chats are born rows — the reaper must
        sweep chat_messages + _rows_state like delete_chat does, or the
        'deleted' conversation resurrects when the same caller's chat name is
        recreated (bug hunt 2026-07-15 #1)."""
        mgr = chat_env()
        mgr.create_chat("eph_call")
        mgr.append_messages_to_chat("eph_call", [
            {"role": "user", "content": "old call"}])
        mgr.set_named_chat_settings("eph_call", {
            "ephemeral_source": "twilio",
            "ephemeral_last_call": 1000.0,
            "ephemeral_ttl_min": 10})
        mgr._rows_state["eph_call"] = {"offset": 0, "count": 1}

        reaped = mgr.reap_ephemeral_chats(now_epoch=1000.0 + 601)
        assert "eph_call" in reaped
        assert raw(tmp_path, "SELECT * FROM chats WHERE name='eph_call'") == []
        assert raw(tmp_path, "SELECT * FROM chat_messages WHERE chat_name='eph_call'") == []
        assert "eph_call" not in mgr._rows_state


class TestClearNamedChatMessages:
    def test_clears_rows_chat(self, chat_env, tmp_path):
        """[REGRESSION_GUARD] clear_named_chat_messages was blob-only — on a
        rows chat it no-oped and a caller phoning back got the previous call's
        entire history (bug hunt 2026-07-15 #2)."""
        mgr = chat_env()
        mgr.create_chat("callback")
        mgr.append_messages_to_chat("callback", [
            {"role": "user", "content": "last call"},
            {"role": "assistant", "content": "bye"}])
        mgr._rows_state["callback"] = {"offset": 0, "count": 2}

        assert mgr.clear_named_chat_messages("callback")
        assert raw(tmp_path, "SELECT * FROM chat_messages WHERE chat_name='callback'") == []
        assert len(raw(tmp_path, "SELECT * FROM chats WHERE name='callback'")) == 1  # chat survives
        assert mgr.export_chat("callback")["messages"] == []
        assert "callback" not in mgr._rows_state

    def test_clears_active_chat_memory_and_next_save_is_clean(self, chat_env):
        mgr = chat_env()
        mgr.add_user_message("wipe me")
        assert mgr.clear_named_chat_messages("default")
        assert mgr.get_messages() == []
        mgr.add_user_message("fresh start")  # post-clear save must not resurrect
        assert [m["content"] for m in mgr.export_chat("default")["messages"]] == ["fresh start"]

    def test_missing_chat_returns_false(self, chat_env):
        assert not chat_env().clear_named_chat_messages("ghost")


class TestForeignAppendHeal:
    def test_incremental_save_absorbs_background_append(self, chat_env, tmp_path):
        """[REGRESSION_GUARD] A background append (cron/agent) to a chat with a
        live rows watermark lands at MAX(seq)+1 without bumping the watermark.
        The next incremental save must absorb those rows ahead of its unsaved
        tail — the old code PK-collided at their seqs and, since the in-memory
        list never shrinks, every later save failed identically: the rest of a
        live call was lost (bug hunt 2026-07-15 #3)."""
        mgr = chat_env()
        mgr.create_chat("callchat")
        assert mgr.set_active_chat("callchat")
        mgr.add_user_message("turn one")  # establishes the rows watermark

        # Simulate the override-lane append: rows written behind the watermark
        # with NO in-memory/watermark sync (direct SQL, like another writer).
        state = dict(mgr._rows_state["callchat"])
        conn = sqlite3.connect(str(tmp_path / "sapphire_history.db"))
        conn.execute(
            "INSERT INTO chat_messages (chat_name, seq, role, message_json) VALUES (?, ?, ?, ?)",
            ("callchat", state["offset"] + state["count"], "user",
             json.dumps({"role": "user", "content": "foreign append"})))
        conn.commit()
        conn.close()

        mgr.add_user_message("turn two")  # must heal, not collide

        stored = [m["content"] for m in mgr.export_chat("callchat")["messages"]]
        assert stored == ["turn one", "foreign append", "turn two"]  # both writers survive, in order
        assert "foreign append" in [m.get("content") for m in mgr.get_messages()]  # absorbed into memory


class TestReplaceMessagesDigest:
    def test_equal_count_mutation_aborts(self, chat_env, tmp_path):
        """[REGRESSION_GUARD] expected_count alone is blind to equal-count
        mutations — an in-place edit during a minutes-long compress was
        silently reverted by the stale tail (bug hunt 2026-07-15 #10)."""
        mgr = chat_env()
        mgr.create_chat("comp")
        mgr.append_messages_to_chat("comp", [{"role": "user", "content": "original"}])
        stale_digest = mgr.messages_digest(mgr.export_chat("comp")["messages"])

        # Equal-count content mutation lands mid-job (edit-in-place class)
        conn = sqlite3.connect(str(tmp_path / "sapphire_history.db"))
        conn.execute(
            "UPDATE chat_messages SET message_json = ? WHERE chat_name = 'comp'",
            (json.dumps({"role": "user", "content": "edited meanwhile"}),))
        conn.commit()
        conn.close()

        ok, err = mgr.replace_messages(
            "comp", [{"role": "user", "content": "summary"}],
            expected_count=1, expected_digest=stale_digest)
        assert not ok and "changed" in err.lower()
        assert mgr.export_chat("comp")["messages"][0]["content"] == "edited meanwhile"  # nothing written

        # Fresh digest passes and the write lands
        fresh = mgr.messages_digest(mgr.export_chat("comp")["messages"])
        ok, err = mgr.replace_messages(
            "comp", [{"role": "user", "content": "summary"}],
            expected_count=1, expected_digest=fresh)
        assert ok, err
        assert mgr.export_chat("comp")["messages"][0]["content"] == "summary"
