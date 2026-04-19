"""Round 3 of Hubble Deep Field LHF fixes (2026-04-19).

Covers:
  - Scout 4 #5: wakeword recovery loop publishes event + stops on failure
  - Scout 1 #3: metrics prune(keep_days) deletes old token_usage rows
"""
import sqlite3
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest


# ─── Scout 4 #5 — wakeword recovery failure handling ─────────────────────────

def test_wakeword_listen_loop_publishes_event_on_recovery_failure():
    """[REGRESSION_GUARD] When stream recovery raises, the loop must:
      (a) publish CONTINUITY_TASK_ERROR so UI can show the failure
      (b) exit the loop (self.running=False) instead of spinning on a dead
          stream forever. Without this, UI toggle reads 'on' but Sapphire
          is silently deaf. Scout 4 finding."""
    import inspect
    from core.wakeword.wake_detector import WakeWordDetector
    src = inspect.getsource(WakeWordDetector._listen_loop)
    # Expect the publish on recovery-failure path
    assert 'CONTINUITY_TASK_ERROR' in src, \
        "wake detector recovery-failure should publish CONTINUITY_TASK_ERROR"
    # Expect the loop to actually exit
    assert 'self.running = False' in src, \
        "wake detector should stop the loop on recovery failure, not spin"


def test_wakeword_listen_loop_verifies_stream_after_recovery():
    """[REGRESSION_GUARD] After start_recording, verify get_stream() != None.
    start_recording can silently set self.stream=None on failure — without
    the check, the loop keeps polling a None stream forever."""
    import inspect
    from core.wakeword.wake_detector import WakeWordDetector
    src = inspect.getsource(WakeWordDetector._listen_loop)
    assert 'get_stream() is None' in src, \
        "recovery path must verify stream is non-None after start_recording"


# ─── Scout 1 #3 — metrics.prune ──────────────────────────────────────────────

@pytest.fixture
def isolated_metrics(tmp_path, monkeypatch):
    from core import metrics as metrics_mod
    db_path = tmp_path / "token_usage.db"
    monkeypatch.setattr(metrics_mod, 'DB_PATH', db_path)
    # Force a fresh TokenMetrics pointing at the new path
    m = metrics_mod.TokenMetrics()
    # Patch the singleton too in case tests reach for it via import
    monkeypatch.setattr(metrics_mod, 'metrics', m)
    return m, db_path


def test_prune_deletes_rows_older_than_window(isolated_metrics):
    """[DATA_INTEGRITY] prune(keep_days=90) removes rows with timestamp
    older than 90 days; recent rows untouched."""
    m, db_path = isolated_metrics
    now = datetime.now()
    old_ts = (now - timedelta(days=100)).isoformat()
    recent_ts = (now - timedelta(days=30)).isoformat()
    with sqlite3.connect(db_path) as conn:
        for i, ts in enumerate([old_ts, old_ts, recent_ts, recent_ts]):
            conn.execute(
                "INSERT INTO token_usage (timestamp, chat_name, provider, model, call_type) "
                "VALUES (?, ?, 'anthropic', 'claude', 'conversation')",
                (ts, f'chat_{i}')
            )

    deleted = m.prune(keep_days=90)
    assert deleted == 2

    conn = sqlite3.connect(db_path)
    remaining = conn.execute("SELECT COUNT(*) FROM token_usage").fetchone()[0]
    conn.close()
    assert remaining == 2


def test_prune_noop_when_all_rows_within_window(isolated_metrics):
    m, db_path = isolated_metrics
    now = datetime.now()
    recent_ts = (now - timedelta(days=5)).isoformat()
    with sqlite3.connect(db_path) as conn:
        for i in range(3):
            conn.execute(
                "INSERT INTO token_usage (timestamp, chat_name, provider, model, call_type) "
                "VALUES (?, ?, 'anthropic', 'claude', 'conversation')",
                (recent_ts, f'chat_{i}')
            )
    deleted = m.prune(keep_days=90)
    assert deleted == 0


def test_prune_custom_window(isolated_metrics):
    """prune(keep_days=7) deletes rows older than a week."""
    m, db_path = isolated_metrics
    now = datetime.now()
    two_weeks_old = (now - timedelta(days=14)).isoformat()
    yesterday = (now - timedelta(days=1)).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO token_usage (timestamp, chat_name, provider, model, call_type) "
            "VALUES (?, 'a', 'x', 'y', 'z')", (two_weeks_old,))
        conn.execute(
            "INSERT INTO token_usage (timestamp, chat_name, provider, model, call_type) "
            "VALUES (?, 'b', 'x', 'y', 'z')", (yesterday,))
    deleted = m.prune(keep_days=7)
    assert deleted == 1


def test_prune_is_wired_into_backup_daily_loop():
    """[REGRESSION_GUARD] The backup scheduler's daily cycle should also
    fire metrics.prune — otherwise the retention window is never enforced."""
    import inspect
    from core import backup
    src = inspect.getsource(backup)
    assert 'metrics.prune' in src, \
        "backup daily loop should call metrics.prune"
