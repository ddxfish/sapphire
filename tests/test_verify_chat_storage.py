"""Tests for tools/verify_chat_storage.py (rowify safety net, layer 3).

Fixture DBs are built with raw sqlite3 — the tests, like the verifier,
share zero code with core/chat/history.py. Each corruption case is a
distinct failure mode the verifier must catch.
"""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parent.parent / "tools" / "verify_chat_storage.py"


def make_db(path, pre_rowify=False):
    conn = sqlite3.connect(str(path))
    if pre_rowify:
        conn.execute("""CREATE TABLE chats (
            name TEXT PRIMARY KEY, settings TEXT NOT NULL,
            messages TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    else:
        conn.execute("""CREATE TABLE chats (
            name TEXT PRIMARY KEY, settings TEXT NOT NULL,
            messages TEXT NOT NULL, updated_at TEXT NOT NULL,
            storage_format TEXT NOT NULL DEFAULT 'blob',
            conversion_failed INTEGER NOT NULL DEFAULT 0,
            created_at TEXT)""")
        conn.execute("""CREATE TABLE chat_messages (
            chat_name TEXT NOT NULL, seq INTEGER NOT NULL, role TEXT,
            message_json TEXT NOT NULL, PRIMARY KEY (chat_name, seq))""")
    conn.execute("""CREATE TABLE tool_images (
        id TEXT PRIMARY KEY, chat_name TEXT NOT NULL, data BLOB NOT NULL,
        media_type TEXT NOT NULL DEFAULT 'image/jpeg', created_at TEXT NOT NULL)""")
    return conn


def add_blob_chat(conn, name, messages):
    conn.execute(
        "INSERT INTO chats (name, settings, messages, updated_at) VALUES (?, '{}', ?, 't')",
        (name, json.dumps(messages)))


def add_rows_chat(conn, name, messages, frozen_blob=None, seqs=None):
    conn.execute(
        "INSERT INTO chats (name, settings, messages, updated_at, storage_format) "
        "VALUES (?, '{}', ?, 't', 'rows')",
        (name, json.dumps(frozen_blob) if frozen_blob is not None else "[]"))
    for i, msg in enumerate(messages):
        conn.execute(
            "INSERT INTO chat_messages VALUES (?, ?, ?, ?)",
            (name, seqs[i] if seqs else i,
             msg.get("role") if isinstance(msg, dict) else None,
             json.dumps(msg) if isinstance(msg, dict) else msg))


def run_tool(db_path):
    proc = subprocess.run(
        [sys.executable, str(TOOL), str(db_path)],
        capture_output=True, text=True)
    return proc.returncode, proc.stdout


MSGS = [
    {"role": "user", "content": "hello", "timestamp": "t1"},
    {"role": "assistant", "content": "hi there", "timestamp": "t2"},
]


class TestCleanDatabases:
    def test_clean_blob_passes(self, tmp_path):
        conn = make_db(tmp_path / "h.db")
        add_blob_chat(conn, "default", MSGS)
        conn.commit(); conn.close()
        code, out = run_tool(tmp_path / "h.db")
        assert code == 0, out
        assert "RESULT: PASS" in out

    def test_clean_rows_passes(self, tmp_path):
        conn = make_db(tmp_path / "h.db")
        add_rows_chat(conn, "rowschat", MSGS)
        conn.commit(); conn.close()
        code, out = run_tool(tmp_path / "h.db")
        assert code == 0, out

    def test_valid_frozen_blob_prefix_passes(self, tmp_path):
        conn = make_db(tmp_path / "h.db")
        extra = MSGS + [{"role": "user", "content": "post-conversion", "timestamp": "t3"}]
        add_rows_chat(conn, "converted", extra, frozen_blob=MSGS)
        conn.commit(); conn.close()
        code, out = run_tool(tmp_path / "h.db")
        assert code == 0, out

    def test_frozen_blob_with_thinking_raw_still_passes(self, tmp_path):
        """R5 must mirror the writer's thinking_raw strip: a blob frozen
        after a crash mid-tool-cycle retains thinking_raw, rows never do.
        Day-ruiner 2026-07-09 confirmed the raw compare false-FAILed here."""
        dirty = dict(MSGS[1])
        dirty["thinking_raw"] = [{"type": "thinking", "thinking": "x", "signature": "s"}]
        frozen = [MSGS[0], dirty]
        stripped_rows = [MSGS[0], MSGS[1]]  # what the converter writes

        conn = make_db(tmp_path / "h.db")
        add_rows_chat(conn, "crashed_cycle", stripped_rows, frozen_blob=frozen)
        conn.commit(); conn.close()
        code, out = run_tool(tmp_path / "h.db")
        assert code == 0, out

    def test_pre_rowify_schema_passes_with_note(self, tmp_path):
        conn = make_db(tmp_path / "h.db", pre_rowify=True)
        add_blob_chat(conn, "default", MSGS)
        conn.commit(); conn.close()
        code, out = run_tool(tmp_path / "h.db")
        assert code == 0, out
        assert "pre-rowify schema" in out


class TestCorruptions:
    def test_r1_seq_gap(self, tmp_path):
        conn = make_db(tmp_path / "h.db")
        add_rows_chat(conn, "gappy", MSGS, seqs=[0, 2])
        conn.commit(); conn.close()
        code, out = run_tool(tmp_path / "h.db")
        assert code == 1
        assert "[R1]" in out

    def test_r2_unparseable_json(self, tmp_path):
        conn = make_db(tmp_path / "h.db")
        add_rows_chat(conn, "badjson", [MSGS[0], "{not json"])
        conn.commit(); conn.close()
        code, out = run_tool(tmp_path / "h.db")
        assert code == 1
        assert "[R2]" in out

    def test_r3_role_sidecar_mismatch(self, tmp_path):
        conn = make_db(tmp_path / "h.db")
        add_rows_chat(conn, "liar", MSGS)
        conn.execute("UPDATE chat_messages SET role='assistant' WHERE seq=0")
        conn.commit(); conn.close()
        code, out = run_tool(tmp_path / "h.db")
        assert code == 1
        assert "[R3]" in out

    def test_r4_thinking_raw_persisted(self, tmp_path):
        conn = make_db(tmp_path / "h.db")
        leaky = dict(MSGS[1])
        leaky["thinking_raw"] = [{"type": "thinking", "thinking": "x"}]
        add_rows_chat(conn, "leaky", [MSGS[0], leaky])
        conn.commit(); conn.close()
        code, out = run_tool(tmp_path / "h.db")
        assert code == 1
        assert "[R4]" in out

    def test_r5_frozen_blob_divergence(self, tmp_path):
        conn = make_db(tmp_path / "h.db")
        mutated = [dict(MSGS[0], content="TAMPERED"), MSGS[1]]
        add_rows_chat(conn, "diverged", mutated, frozen_blob=MSGS)
        conn.commit(); conn.close()
        code, out = run_tool(tmp_path / "h.db")
        assert code == 1
        assert "[R5]" in out
        assert "index 0" in out

    def test_b1_blob_not_a_list(self, tmp_path):
        conn = make_db(tmp_path / "h.db")
        conn.execute(
            "INSERT INTO chats (name, settings, messages, updated_at) "
            "VALUES ('broken', '{}', '{\"oops\": 1}', 't')")
        conn.commit(); conn.close()
        code, out = run_tool(tmp_path / "h.db")
        assert code == 1
        assert "[B1]" in out

    def test_g2_orphan_rows_under_blob_chat(self, tmp_path):
        conn = make_db(tmp_path / "h.db")
        add_blob_chat(conn, "blobby", MSGS)
        conn.execute(
            "INSERT INTO chat_messages VALUES ('blobby', 0, 'user', ?)",
            (json.dumps(MSGS[0]),))
        conn.commit(); conn.close()
        code, out = run_tool(tmp_path / "h.db")
        assert code == 1
        assert "[G2]" in out

    def test_missing_db_exits_2(self, tmp_path):
        code, out = run_tool(tmp_path / "nope.db")
        assert code == 2
