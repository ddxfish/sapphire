"""[REGRESSION_GUARD] DB watchdog — 2026-08-21 frozen-close incident.

A conn.close() hang wedged every DB op in the process (SQLite's unix VFS
takes one process-global mutex during open AND close) with zero
diagnostics until a manual SIGABRT. The watchdog registers every
_get_connection context; an op stuck past the threshold gets one
[DB-WATCHDOG] error + an all-thread stack dump to stderr, then heartbeat
lines. _db_watchdog_scan() is driven directly here — no timing
dependence on the daemon loop.
"""
import logging
import time
from unittest.mock import patch

TEST_DEFAULTS = {"prompt": "default"}


def _sm(tmp_path):
    with patch("core.chat.history.get_system_defaults",
               side_effect=lambda: dict(TEST_DEFAULTS)), \
         patch("core.chat.history.get_user_defaults",
               side_effect=lambda: dict(TEST_DEFAULTS)):
        from core.chat.history import ChatSessionManager
        return ChatSessionManager(history_dir=str(tmp_path))


def test_watchdog_reports_stuck_op_once_and_drains(tmp_path, monkeypatch, caplog):
    import core.chat.history as hist
    sm = _sm(tmp_path)
    monkeypatch.setattr(hist, "_DB_WATCHDOG_SECS", 0.05)
    with caplog.at_level(logging.ERROR, logger="core.chat.history"):
        with sm._get_connection():
            time.sleep(0.12)
            assert hist._db_watchdog_scan() == 1   # stuck → dump + log
            assert hist._db_watchdog_scan() == 1   # still stuck — no re-dump
    msgs = [r.message for r in caplog.records if "[DB-WATCHDOG]" in r.message]
    assert len(msgs) == 1 and "dumping all thread stacks" in msgs[0]
    assert not hist._db_ops                        # registry drained on exit


def test_watchdog_quiet_on_fast_ops(tmp_path, monkeypatch, caplog):
    import core.chat.history as hist
    sm = _sm(tmp_path)
    monkeypatch.setattr(hist, "_DB_WATCHDOG_SECS", 30.0)
    with caplog.at_level(logging.ERROR, logger="core.chat.history"):
        sm.create_chat("pub")                      # several fast DB ops
        assert hist._db_watchdog_scan() == 0
    assert not [r for r in caplog.records if "[DB-WATCHDOG]" in r.message]
    assert not hist._db_ops
