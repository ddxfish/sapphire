"""Vaulted chats Phase 3 (T12b + F5) — mind.db chat provenance.

Ruling 8: memories saved FROM a private chat must not carry its name into
plaintext mind.db. Forward writes mask at `_context_fields()`; the
chat_vaulted hook retro-scrubs rows deposited while the chat was public.
Placeholder ('__private__'), never dropped — the added_by migration infers
'ai' from meta.chat being present.
"""
import json
import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace


class TestContextFieldsMask:
    def _with_ctx(self, ctx, private, fn):
        from core.chat.function_manager import tool_context, scope_private
        t1 = tool_context.set(ctx)
        t2 = scope_private.set(private)
        try:
            return fn()
        finally:
            scope_private.reset(t2)
            tool_context.reset(t1)

    def test_private_chat_masked(self):
        from plugins.mindpalace.tools import metadata
        fields = self._with_ctx({"chat": "secret", "persona": "sapphire"},
                                True, metadata._context_fields)
        assert fields["chat"] == "__private__"
        assert fields["persona"] == "sapphire"   # only the chat name masks

    def test_public_chat_untouched(self):
        from plugins.mindpalace.tools import metadata
        fields = self._with_ctx({"chat": "open", "persona": "sapphire"},
                                False, metadata._context_fields)
        assert fields["chat"] == "open"


class TestChatVaultedScrub:
    def _tmp_mind(self, tmp_path):
        db = tmp_path / "mind.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE chunks (id INTEGER PRIMARY KEY, meta TEXT)")
        conn.execute("CREATE TABLE entities (id INTEGER PRIMARY KEY, meta TEXT)")
        conn.execute("INSERT INTO chunks (meta) VALUES (?)",
                     (json.dumps({"chat": "secret", "persona": "s"}),))
        conn.execute("INSERT INTO chunks (meta) VALUES (?)",
                     (json.dumps({"chat": "other"}),))
        conn.execute("INSERT INTO entities (meta) VALUES (?)",
                     (json.dumps({"chat": "secret"}),))
        conn.commit()
        conn.close()
        return db

    def test_scrubs_matching_rows_only(self, tmp_path, monkeypatch):
        db = self._tmp_mind(tmp_path)
        from plugins.mindpalace.tools import palace_tools

        @contextmanager
        def fake_conn():
            conn = sqlite3.connect(str(db))
            try:
                yield conn
            finally:
                conn.close()
        monkeypatch.setattr(palace_tools, "_get_connection", fake_conn)

        from plugins.mindpalace.hooks.chat_vaulted import chat_vaulted
        chat_vaulted(SimpleNamespace(metadata={"name": "secret"}))

        conn = sqlite3.connect(str(db))
        chunks = [json.loads(r[0]) for r in
                  conn.execute("SELECT meta FROM chunks ORDER BY id")]
        ents = [json.loads(r[0]) for r in
                conn.execute("SELECT meta FROM entities")]
        conn.close()
        assert chunks[0]["chat"] == "__private__"
        assert chunks[0]["persona"] == "s"        # rest of the meta intact
        assert chunks[1]["chat"] == "other"       # other chats untouched
        assert ents[0]["chat"] == "__private__"

    def test_missing_name_is_noop(self):
        from plugins.mindpalace.hooks.chat_vaulted import chat_vaulted
        chat_vaulted(SimpleNamespace(metadata={}))   # must not raise
