"""Chat Manager v1b — trim + compress (tmp/chat-manager.md).

turn_spans / replace_messages / trim_chat on the SessionManager, and the
compress engine with a mock provider (no real LLM calls in pytest).
"""
import json
import sqlite3
import threading
import time

import pytest
from unittest.mock import patch

TEST_DEFAULTS = {"prompt": "default"}


@pytest.fixture
def chat_env(tmp_path, monkeypatch):
    import core.privacy as privacy
    monkeypatch.setattr(privacy, "is_privacy_mode", lambda: False, raising=False)

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


def turn(i, with_tools=False):
    """One turn's messages: user + (optional tool cycle) + assistant."""
    msgs = [{"role": "user", "content": f"question {i}", "timestamp": f"t{i}"}]
    if with_tools:
        msgs.append({"role": "assistant", "content": "",
                     "tool_calls": [{"id": f"tc{i}", "type": "function",
                                     "function": {"name": "t", "arguments": "{}"}}],
                     "timestamp": f"t{i}"})
        msgs.append({"role": "tool", "tool_call_id": f"tc{i}", "name": "t",
                     "content": f"result {i}", "timestamp": f"t{i}"})
    msgs.append({"role": "assistant", "content": f"answer {i}", "timestamp": f"t{i}"})
    return msgs


class TestTurnSpans:
    def test_basic_and_glued_prefix(self):
        from core.chat.history import turn_spans
        assert turn_spans([]) == []
        # No user messages at all → one span covering everything
        only_asst = [{"role": "assistant", "content": "hello"}]
        assert turn_spans(only_asst) == [(0, 1)]
        # Leading assistant greeting glues to the first turn
        msgs = [{"role": "assistant", "content": "greeting"}] + turn(1) + turn(2)
        spans = turn_spans(msgs)
        assert len(spans) == 2
        assert spans[0] == (0, 3)  # greeting + user1 + answer1

    def test_tool_chains_never_split(self):
        from core.chat.history import turn_spans
        msgs = turn(1) + turn(2, with_tools=True) + turn(3)
        spans = turn_spans(msgs)
        assert len(spans) == 3
        for start, end in spans:
            span = msgs[start:end]
            # Census: every tool result's tool_call lives in the SAME span
            for m in span:
                if m["role"] == "tool":
                    ids = [tc["id"] for s in span if s.get("tool_calls")
                           for tc in s["tool_calls"]]
                    assert m["tool_call_id"] in ids


class TestReplaceMessages:
    def test_rows_chat_full_rewrite(self, chat_env, tmp_path):
        mgr = chat_env()
        mgr.create_chat("target")
        mgr.append_messages_to_chat("target", turn(1) + turn(2))
        new = turn(9)
        ok, err = mgr.replace_messages("target", new)
        assert ok, err
        rows = raw(tmp_path, "SELECT seq, message_json FROM chat_messages "
                             "WHERE chat_name='target' ORDER BY seq")
        assert [r["seq"] for r in rows] == list(range(len(new)))  # gapless from 0
        assert json.loads(rows[0]["message_json"])["content"] == "question 9"
        blob = raw(tmp_path, "SELECT messages FROM chats WHERE name='target'")
        assert blob[0]["messages"] == "[]"

    def test_blob_chat_stays_blob(self, chat_env, tmp_path):
        mgr = chat_env()
        conn = sqlite3.connect(str(tmp_path / "sapphire_history.db"))
        conn.execute(
            "INSERT INTO chats (name, settings, messages, updated_at, storage_format) "
            "VALUES ('legacy', '{}', ?, 't', 'blob')", (json.dumps(turn(1)),))
        conn.commit(); conn.close()
        ok, err = mgr.replace_messages("legacy", turn(7))
        assert ok, err
        row = raw(tmp_path, "SELECT messages, storage_format FROM chats "
                            "WHERE name='legacy'")[0]
        assert row["storage_format"] == "blob"  # format-preserving, no conversion
        assert json.loads(row["messages"])[0]["content"] == "question 7"
        assert raw(tmp_path, "SELECT 1 FROM chat_messages WHERE chat_name='legacy'") == []

    def test_active_chat_memory_and_store_agree(self, chat_env, tmp_path):
        mgr = chat_env()
        mgr.add_user_message("original")
        # Fake a capped-load watermark: replacement must still wipe from seq 0
        mgr._rows_state["default"] = {"offset": 5, "count": 1}
        new = turn(3)
        ok, err = mgr.replace_messages("default", new)
        assert ok, err
        assert [m["content"] for m in mgr.get_messages()] == \
               [m["content"] for m in new]
        rows = raw(tmp_path, "SELECT seq FROM chat_messages "
                             "WHERE chat_name='default' ORDER BY seq")
        assert [r["seq"] for r in rows] == list(range(len(new)))

    def test_expected_count_aborts_on_concurrent_write(self, chat_env):
        mgr = chat_env()
        mgr.create_chat("busy")
        mgr.append_messages_to_chat("busy", turn(1) + turn(2))
        snapshot_len = len(mgr.export_chat("busy")["messages"])
        # Someone writes between the read and the replace
        mgr.append_messages_to_chat("busy", turn(3))
        ok, err = mgr.replace_messages("busy", turn(9),
                                       expected_count=snapshot_len)
        assert not ok and "changed during" in err
        # The concurrent turn survives untouched
        contents = [m["content"] for m in mgr.export_chat("busy")["messages"]]
        assert "question 3" in contents and "question 9" not in contents
        # Matching count proceeds normally
        ok, err = mgr.replace_messages(
            "busy", turn(9), expected_count=len(mgr.export_chat("busy")["messages"]))
        assert ok, err

    def test_missing_chat_and_streaming_refusal(self, chat_env):
        mgr = chat_env()
        ok, err = mgr.replace_messages("ghost", turn(1))
        assert not ok and "not found" in err
        mgr._is_streaming = True
        ok, err = mgr.replace_messages("default", turn(1))
        assert not ok and "streaming" in err.lower()


class TestTrimChat:
    def build(self, mgr, name="trimme", n_turns=6):
        mgr.create_chat(name)
        msgs = []
        for i in range(1, n_turns + 1):
            msgs += turn(i, with_tools=(i == 3))  # tool cycle mid-chat
        mgr.append_messages_to_chat(name, msgs)
        return msgs

    def test_keeps_first_and_last_turns(self, chat_env):
        mgr = chat_env()
        msgs = self.build(mgr)
        ok, report = mgr.trim_chat("trimme", 1, 2)
        assert ok, report
        out = mgr.export_chat("trimme")["messages"]
        contents = [m["content"] for m in out]
        assert contents[0] == "question 1" and contents[1] == "answer 1"
        assert "question 5" in contents and "question 6" in contents
        assert "question 3" not in contents  # tool-cycle turn deleted whole
        assert not any(m["role"] == "tool" for m in out)
        assert report["deleted_messages"] == len(msgs) - len(out)
        assert not report["no_op"]

    def test_preview_matches_actual_and_does_not_write(self, chat_env):
        mgr = chat_env()
        msgs = self.build(mgr, "prev")
        ok, p = mgr.trim_chat("prev", 1, 2, preview=True)
        assert ok
        assert len(mgr.export_chat("prev")["messages"]) == len(msgs)  # untouched
        ok, actual = mgr.trim_chat("prev", 1, 2)
        assert ok
        assert p["deleted_messages"] == actual["deleted_messages"]
        assert p["messages_after"] == actual["messages_after"]
        assert len(mgr.export_chat("prev")["messages"]) == p["messages_after"]

    def test_no_op_when_window_covers_chat(self, chat_env):
        mgr = chat_env()
        self.build(mgr, "small", n_turns=3)
        ok, report = mgr.trim_chat("small", 2, 2)
        assert ok and report["no_op"]
        assert report["deleted_messages"] == 0

    def test_keep_last_floors_at_one(self, chat_env):
        mgr = chat_env()
        self.build(mgr, "floor", n_turns=4)
        ok, report = mgr.trim_chat("floor", 0, 0)  # 0 → floored to 1
        assert ok
        out = mgr.export_chat("floor")["messages"]
        assert [m["content"] for m in out] == ["question 4", "answer 4"]

    def test_trim_prunes_orphaned_images_keeps_live(self, chat_env, tmp_path):
        mgr = chat_env()
        mgr.create_chat("imgs")
        msgs = (turn(1)
                + [{"role": "user", "content": "mid", "timestamp": "t"},
                   {"role": "assistant", "content": "pic <<IMG::tool:doomed>> here",
                    "timestamp": "t"}]
                + [{"role": "user", "content": "late", "timestamp": "t"},
                   {"role": "assistant", "content": "keep <<IMG::tool:alive>> ok",
                    "timestamp": "t"}])
        mgr.append_messages_to_chat("imgs", msgs)
        mgr.save_tool_image("doomed", b"X", "image/png", chat_name="imgs")
        mgr.save_tool_image("alive", b"Y", "image/png", chat_name="imgs")
        ok, _ = mgr.trim_chat("imgs", 1, 1)  # deletes the middle turn
        assert ok
        ids = [r["id"] for r in raw(tmp_path,
               "SELECT id FROM tool_images WHERE chat_name='imgs'")]
        assert ids == ["alive"]


class TestListStatsTurnCount:
    def test_turn_count_rows_and_blob(self, chat_env, tmp_path):
        mgr = chat_env()
        mgr.create_chat("rowsy")  # born rows
        mgr.append_messages_to_chat("rowsy", turn(1) + turn(2, with_tools=True) + turn(3))
        conn = sqlite3.connect(str(tmp_path / "sapphire_history.db"))
        conn.execute(
            "INSERT INTO chats (name, settings, messages, updated_at, storage_format) "
            "VALUES ('blobby', '{}', ?, 't', 'blob')",
            (json.dumps(turn(1) + turn(2)),))
        conn.commit(); conn.close()

        listed = {c["name"]: c for c in mgr.list_chat_files(stats=True)}
        assert listed["rowsy"]["turn_count"] == 3   # tool cycle ≠ extra turn
        assert listed["blobby"]["turn_count"] == 2
        # And the hot dropdown path stays lean — no turn_count without stats
        assert "turn_count" not in mgr.list_chat_files(stats=False)[0]


class MockProvider:
    def __init__(self, text="SUMMARY TEXT"):
        self.calls = []
        self.text = text

    def chat_completion(self, messages, tools=None, generation_params=None):
        self.calls.append(messages[0]["content"])
        resp = type("R", (), {})()
        resp.content = self.text
        return resp


class TestCompressEngine:
    def test_chunk_by_turns_snaps_and_respects_budget(self):
        from core.chat import compress
        msgs = []
        for i in range(4):
            msgs += [{"role": "user", "content": "x " * 100},
                     {"role": "assistant", "content": "y " * 100}]
        chunks = compress.chunk_by_turns(msgs, budget=250)
        assert len(chunks) > 1
        assert sum(len(c) for c in chunks) == len(msgs)  # nothing dropped
        for c in chunks:
            assert c[0]["role"] == "user"  # every chunk starts on a turn

    def test_whole_mode_single_chunk_one_pair(self):
        from core.chat import compress
        head = turn(1) + turn(2)
        prov = MockProvider()
        pairs = compress.compress_messages(head, "whole", prov, 2000)
        assert len(pairs) == 2
        assert pairs[0]["role"] == "user" and pairs[1]["role"] == "assistant"
        assert pairs[1]["content"] == "SUMMARY TEXT"
        assert pairs[0]["compressed"] and pairs[1]["compressed_at"]
        assert f"{len(head)} messages" in pairs[0]["content"]
        assert len(prov.calls) == 1  # single chunk → direct, no merge step

    def test_whole_mode_multi_chunk_map_reduce(self, monkeypatch):
        from core.chat import compress
        monkeypatch.setattr(compress, "CHUNK_INPUT_TOKENS", 10)
        head = turn(1) + turn(2) + turn(3)
        prov = MockProvider()
        pairs = compress.compress_messages(head, "whole", prov, 2000)
        assert len(pairs) == 2  # still ONE pair out
        # 3 partials + 1 merge; merge prompt carries the partial summaries
        assert len(prov.calls) == 4
        assert "PARTIAL SUMMARIES" in prov.calls[-1]

    def test_chunked_mode_pair_per_chunk(self, monkeypatch):
        from core.chat import compress
        monkeypatch.setattr(compress, "CHUNK_INPUT_TOKENS", 10)
        head = turn(1) + turn(2) + turn(3)
        prov = MockProvider()
        pairs = compress.compress_messages(head, "chunked", prov, 2000)
        assert len(pairs) == 6  # 3 chunks × one pair each
        assert [p["role"] for p in pairs] == ["user", "assistant"] * 3

    def test_empty_summary_raises(self):
        from core.chat import compress
        with pytest.raises(RuntimeError, match="empty"):
            compress.compress_messages(turn(1), "whole", MockProvider(text=""), 2000)

    def test_cap_text_middle_truncates_over_ceiling(self):
        from core.chat import compress
        short = "hello world"
        assert compress.cap_text(short, 100) == short  # under: untouched
        long = "alpha " * 3000  # ~3k tokens
        capped = compress.cap_text(long, 500)
        from core.chat.history import count_tokens
        assert count_tokens(capped) < 700  # ceiling + marker slack
        assert "tokens omitted for length" in capped
        assert capped.startswith("alpha") and capped.rstrip().endswith("alpha")

    def test_every_llm_call_input_is_capped(self, monkeypatch):
        from core.chat import compress
        # One turn with a giant tool result — its chunk far exceeds budget
        big = turn(1, with_tools=True)
        big[2]["content"] = "data " * 20000  # ~20k tokens in one message
        prov = MockProvider()
        compress.compress_messages(big, "whole", prov, 2000)
        from core.chat.history import count_tokens
        for call in prov.calls:
            assert count_tokens(call) < compress.INPUT_CEILING_TOKENS + 1000

    def test_hierarchical_merge_rounds_terminate(self, monkeypatch):
        from core.chat import compress
        monkeypatch.setattr(compress, "CHUNK_INPUT_TOKENS", 10)
        # Ceiling so small each merge group holds only ~2 partials → rounds
        monkeypatch.setattr(compress, "INPUT_CEILING_TOKENS", 8)
        head = sum((turn(i) for i in range(1, 9)), [])  # 8 chunks
        prov = MockProvider()
        pairs = compress.compress_messages(head, "whole", prov, 2000)
        assert len(pairs) == 2  # still exactly ONE pair out
        merge_calls = [c for c in prov.calls if "PARTIAL SUMMARIES" in c]
        assert len(merge_calls) >= 4  # 8 partials, groups of ~2 → multi-round

    def test_progress_callback_narrates(self, monkeypatch):
        from core.chat import compress
        monkeypatch.setattr(compress, "CHUNK_INPUT_TOKENS", 10)
        seen = []
        compress.compress_messages(turn(1) + turn(2) + turn(3), "chunked",
                                   MockProvider(), 2000,
                                   on_progress=seen.append)
        assert seen == ["chunk 1/3", "chunk 2/3", "chunk 3/3"]


class FailingProvider:
    """Succeeds once, then dies — the mid-compress crash scenario."""
    def __init__(self):
        self.calls = 0

    def chat_completion(self, messages, tools=None, generation_params=None):
        self.calls += 1
        if self.calls >= 2:
            raise RuntimeError("model fell over mid-compress")
        resp = type("R", (), {})()
        resp.content = "PARTIAL"
        return resp


class TestCompressChat:
    def test_end_to_end_with_backup(self, chat_env, tmp_path, monkeypatch):
        from core.chat import compress
        mgr = chat_env()
        mgr.create_chat("bigone")
        msgs = turn(1) + turn(2, with_tools=True) + turn(3) + turn(4)
        mgr.append_messages_to_chat("bigone", msgs)
        monkeypatch.setattr(compress, "make_provider",
                            lambda k, m: MockProvider())
        report = compress._compress_chat(mgr, "bigone", "whole", "mock", "",
                                         5000, 1, backup=True)
        out = mgr.export_chat("bigone")["messages"]
        # 1 summary pair + last turn verbatim
        assert out[0]["compressed"] and out[1]["content"] == "SUMMARY TEXT"
        assert [m["content"] for m in out[2:]] == ["question 4", "answer 4"]
        assert report["messages_before"] == len(msgs)
        assert report["messages_after"] == len(out)
        assert report["summary_pairs"] == 1
        backups = list((tmp_path / "exports").glob("bigone_*.json"))
        assert len(backups) == 1
        saved = json.loads(backups[0].read_text(encoding="utf-8"))
        assert len(saved["messages"]) == len(msgs)  # full pre-compress snapshot

    def test_write_last_failed_compress_leaves_chat_untouched(
            self, chat_env, tmp_path, monkeypatch):
        """THE safety invariant: the chat is only written after every LLM
        call has succeeded. A crash mid-compress = no write, byte-identical
        chat, backup already on disk."""
        from core.chat import compress
        mgr = chat_env()
        mgr.create_chat("precious")
        mgr.append_messages_to_chat("precious",
                                    sum((turn(i) for i in range(1, 7)), []))
        before = mgr.export_chat("precious")
        monkeypatch.setattr(compress, "CHUNK_INPUT_TOKENS", 10)  # force multi-call
        monkeypatch.setattr(compress, "make_provider",
                            lambda k, m: FailingProvider())
        with pytest.raises(RuntimeError, match="fell over"):
            compress._compress_chat(mgr, "precious", "whole", "mock", "",
                                    5000, 1, backup=True)
        assert mgr.export_chat("precious") == before  # byte-identical
        backups = list((tmp_path / "exports").glob("precious_*.json"))
        assert len(backups) == 1  # backup landed BEFORE any LLM spend

    def test_nothing_to_compress_raises(self, chat_env, monkeypatch):
        from core.chat import compress
        mgr = chat_env()
        mgr.create_chat("tiny")
        mgr.append_messages_to_chat("tiny", turn(1))
        monkeypatch.setattr(compress, "make_provider",
                            lambda k, m: MockProvider())
        with pytest.raises(ValueError, match="kept tail"):
            compress._compress_chat(mgr, "tiny", "whole", "mock", "",
                                    5000, 10, backup=False)

    def test_compress_aborts_if_someone_talks_mid_job(self, chat_env, monkeypatch):
        """Sapphire's heartbeat drops a turn into the chat while compress is
        mid-LLM-work: the final write must abort, keeping her words."""
        from core.chat import compress
        mgr = chat_env()
        mgr.create_chat("livewire")
        mgr.append_messages_to_chat("livewire",
                                    sum((turn(i) for i in range(1, 5)), []))

        class TalkativeProvider:
            def chat_completion(self, messages, tools=None, generation_params=None):
                # A turn lands DURING the summarize call
                mgr.append_messages_to_chat("livewire", turn(99))
                resp = type("R", (), {})()
                resp.content = "SUMMARY"
                return resp

        monkeypatch.setattr(compress, "make_provider",
                            lambda k, m: TalkativeProvider())
        with pytest.raises(RuntimeError, match="changed during"):
            compress._compress_chat(mgr, "livewire", "whole", "mock", "",
                                    5000, 1, backup=False)
        contents = [m["content"] for m in mgr.export_chat("livewire")["messages"]]
        assert "question 99" in contents      # the mid-job turn survives
        assert "SUMMARY" not in contents      # no partial compress written

    def test_job_serialization_and_status(self, chat_env, monkeypatch):
        from core.chat import compress
        mgr = chat_env()
        gate = threading.Event()

        def slow_compress(*a, **kw):
            gate.wait(timeout=5)
            return {"chat": "j1", "mode": "whole"}

        monkeypatch.setattr(compress, "_compress_chat", slow_compress)
        ok, err = compress.start_compress_job(
            mgr, "j1", mode="whole", provider_key="mock", model="",
            target_tokens=5000, keep_last_turns=1, backup=False)
        assert ok, err
        assert compress.get_job_status()["running"]
        ok2, err2 = compress.start_compress_job(
            mgr, "j2", mode="whole", provider_key="mock", model="",
            target_tokens=5000, keep_last_turns=1, backup=False)
        assert not ok2 and "already running" in err2
        gate.set()
        for _ in range(50):
            s = compress.get_job_status()
            if s.get("done"):
                break
            time.sleep(0.1)
        assert s["done"] and s["ok"] and s["result"]["chat"] == "j1"
