"""Regression guards for the Hubble Deep Field fixes (2026-04-19 scout dispatch).

Covers:
  - Scout 2 Race #1: append_messages_to_chat waits for active-chat stream to end
  - Scout 4 Chaos #4: Whisper hallucination filter blocks canned phrases
  - Scout 1 Longevity #1: kokoro.log truncates on restart (behavior verified via file mode)

See tmp/coverage-test-plan.md (Hubble sweep notes).
"""
import threading
import time
from unittest.mock import MagicMock, patch

import pytest


# ─── Scout 2 Race #1 — stream guard on append ────────────────────────────────

@pytest.fixture
def session_manager_tmp(tmp_path, monkeypatch):
    from core.chat.history import ChatSessionManager
    sm = ChatSessionManager()
    sm._db_path = tmp_path / "chats.db"
    sm._init_db()
    sm.create_chat('trinity')
    sm.set_active_chat('trinity')
    yield sm


def test_append_waits_for_active_chat_stream_to_end(session_manager_tmp):
    """[REGRESSION_GUARD] If target is active chat AND streaming, append must
    wait for the stream to finish. Otherwise cron writes can interleave between
    tool_call/tool_result OR get overwritten by a per-message save of a stale
    in-memory snapshot. Scout 2 Race #1.
    """
    sm = session_manager_tmp
    sm._is_streaming = True

    started_at = time.time()

    def release_stream():
        time.sleep(0.5)
        sm._is_streaming = False

    t = threading.Thread(target=release_stream, daemon=True)
    t.start()

    # Append should block until release_stream clears the flag
    sm.append_messages_to_chat(
        'trinity',
        [{'role': 'user', 'content': 'cron msg'}],
        max_wait_if_streaming=5.0,
    )
    elapsed = time.time() - started_at
    t.join(timeout=2)

    assert elapsed >= 0.5, f"expected append to wait ~0.5s, waited {elapsed}"
    assert elapsed < 2.0, f"waited too long ({elapsed}s), polling may be broken"
    # The cron message actually landed. Format-aware read — chats are born
    # 'rows' post rowify step 2, so probing the blob column directly would
    # always see '[]' regardless of whether the append worked.
    stored = sm.read_chat_messages('trinity')
    assert any(m.get('content') == 'cron msg' for m in stored), \
        "cron message not persisted after stream released"


def test_append_does_not_wait_when_stream_on_different_chat(session_manager_tmp):
    """Cron targeting a DIFFERENT chat than the active one must not block
    on the active chat's stream — they don't conflict."""
    sm = session_manager_tmp
    sm.create_chat('other_chat')
    sm._is_streaming = True  # active='trinity' is streaming

    started_at = time.time()
    sm.append_messages_to_chat(
        'other_chat',
        [{'role': 'user', 'content': 'unrelated cron'}],
        max_wait_if_streaming=5.0,
    )
    elapsed = time.time() - started_at
    sm._is_streaming = False

    assert elapsed < 0.3, f"should not have blocked, waited {elapsed}"


def test_append_skips_after_max_wait(session_manager_tmp, caplog):
    """[REGRESSION_GUARD] If stream doesn't end within max_wait_if_streaming,
    the write is SKIPPED — corruption is worse than a lost heartbeat append.
    Pre-2026-05-07 the path fell through and wrote anyway, interleaving cron
    messages between in-flight tool_call/tool_result pairs. Race scout #1
    fix: skip + return False + log error. Voice mode amplifies the timeout
    rate so this matters going forward."""
    sm = session_manager_tmp
    sm._is_streaming = True  # never clears

    started_at = time.time()
    with caplog.at_level("ERROR"):
        result = sm.append_messages_to_chat(
            'trinity',
            [{'role': 'user', 'content': 'should not land'}],
            max_wait_if_streaming=0.6,
        )
    elapsed = time.time() - started_at
    sm._is_streaming = False

    assert 0.5 < elapsed < 2.0, f"expected ~0.6s wait, got {elapsed}"
    assert result is False, "append should return False to signal skipped write"
    assert any('SKIPPED' in r.message or 'gave up' in r.message
               for r in caplog.records), \
        "expected an error log about skipped write"


def test_append_no_wait_when_nothing_streaming(session_manager_tmp):
    """Happy path: no stream, append happens immediately."""
    sm = session_manager_tmp
    assert sm._is_streaming is False

    started_at = time.time()
    sm.append_messages_to_chat('trinity', [{'role': 'user', 'content': 'normal'}])
    elapsed = time.time() - started_at
    assert elapsed < 0.3


# ─── Scout 4 Chaos #4 — Whisper hallucination filter ─────────────────────────

def test_whisper_hallucination_filter_catches_common_phrases():
    """[REGRESSION_GUARD] Whisper hallucinates canned phrases on silence —
    filter must block them before they become phantom LLM queries. Scout 4."""
    from core.stt.hallucination import is_whisper_hallucination as _is_whisper_hallucination
    for phrase in [
        'thank you',
        'Thank you.',
        'Thanks for watching',
        'Thanks for watching!',
        'THANKS FOR WATCHING',
        'Bye.',
        'goodbye',
        '[music]',
        '[Applause]',
        'Subtitles by',
        '',
        '   ',
    ]:
        assert _is_whisper_hallucination(phrase), \
            f"expected hallucination filter to catch {phrase!r}"


def test_whisper_filter_passes_real_phrases():
    """Inverse — real user speech must NOT be filtered out."""
    from core.stt.hallucination import is_whisper_hallucination as _is_whisper_hallucination
    for phrase in [
        "what time is it",
        "remind me about the grocery list",
        "thank you for making dinner",  # 'thank you' substring but context differs
        "play some music please",
        "tell me about memory management",
    ]:
        assert not _is_whisper_hallucination(phrase), \
            f"real speech wrongly filtered: {phrase!r}"


def test_whisper_filter_case_and_punctuation_insensitive():
    from core.stt.hallucination import is_whisper_hallucination as _is_whisper_hallucination
    assert _is_whisper_hallucination("THANK YOU")
    assert _is_whisper_hallucination("thank you.")
    assert _is_whisper_hallucination("thank you!")
    assert _is_whisper_hallucination("  Thank You.  ")


# ─── Scout 1 Longevity #1 — kokoro.log truncate on restart ───────────────────

def test_process_manager_opens_log_for_write_not_append():
    """[REGRESSION_GUARD] ProcessManager truncates the log on each restart
    instead of appending forever. Scout 1 found linear unbounded growth;
    fix is `'a'` → `'w'` because process always restarts at boot anyway."""
    import inspect
    from core import process_manager
    src = inspect.getsource(process_manager)
    # Must open with 'w' (truncate), must NOT open with 'a' (append-forever)
    # for the log file. Looking for the specific pattern.
    assert 'open(self.log_file, "w")' in src, \
        "process_manager.py should use write-mode for log — did someone revert to append?"
    assert 'open(self.log_file, "a")' not in src, \
        "process_manager.py should not use append mode — log would grow unboundedly"
