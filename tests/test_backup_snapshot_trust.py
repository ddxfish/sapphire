"""Backup trust suite (negspace hunt 2026-08-31 — N10/F7/F8/N11).

N10: never tar a live SQLite file — snapshots via sqlite3's backup API must
capture un-checkpointed WAL content and the archive must hold NO -wal/-shm.
F7: a bad BACKUPS_HOUR must not kill the scheduler thread.
F8: run_scheduled must report failure, not print success over it.
N11: health_summary surfaces sentinel halt + newest-age honestly.
"""
import sqlite3
import sys
import tarfile
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    import core.backup as B
    from core.backup import Backup
    with patch.object(Backup, '__init__', lambda self: None):
        b = Backup()
    b.base_dir = tmp_path
    b.user_dir = tmp_path / "user"
    b.backup_dir = tmp_path / "user_backups"
    b.user_dir.mkdir(parents=True)
    b.backup_dir.mkdir(parents=True)
    b._stop_event = None
    b._backup_op_lock = threading.Lock()

    class Cfg:
        BASE_DIR = tmp_path
        BACKUPS_ENABLED = True
        BACKUPS_EXCLUDE_PATTERNS = []
        BACKUPS_KEEP_DAILY = 7
        BACKUPS_KEEP_WEEKLY = 0
        BACKUPS_KEEP_MONTHLY = 0
        BACKUPS_HOUR = 3
    monkeypatch.setattr(B, "config", Cfg)
    return b


def _extract(tar, dest):
    try:
        tar.extractall(dest, filter="data")
    except TypeError:          # < py3.11.4: no filter kwarg
        tar.extractall(dest)


def test_snapshot_captures_live_wal_content(mgr):
    """Rows living ONLY in an un-checkpointed WAL (writer still holds the db
    open) must land in the archive; the live -wal/-shm files must not."""
    db = mgr.user_dir / "main.db"
    con = sqlite3.connect(str(db))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA wal_autocheckpoint=0")
    con.execute("CREATE TABLE t (v TEXT)")
    con.execute("INSERT INTO t VALUES ('in-wal-only')")
    con.commit()               # committed to WAL; NOT checkpointed; con stays open
    (mgr.user_dir / "plain.txt").write_text("hello")
    assert (mgr.user_dir / "main.db-wal").exists(), "test setup: WAL missing"

    filename = mgr.create_backup("manual")
    con.close()
    assert filename, "backup failed"

    out = mgr.backup_dir / "extract"
    with tarfile.open(mgr.backup_dir / filename) as tar:
        names = tar.getnames()
        assert "user/main.db" in names
        assert "user/plain.txt" in names
        assert not any(n.endswith("-wal") or n.endswith("-shm") for n in names), names
        _extract(tar, out)

    rcon = sqlite3.connect(str(out / "user" / "main.db"))
    assert rcon.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    rows = [r[0] for r in rcon.execute("SELECT v FROM t")]
    rcon.close()
    assert rows == ["in-wal-only"], "WAL content missing from snapshot"


def test_no_staging_dir_left_behind(mgr):
    c = sqlite3.connect(str(mgr.user_dir / "a.db"))
    c.execute("CREATE TABLE t(x)"); c.commit(); c.close()
    assert mgr.create_backup("manual")
    leftovers = [p.name for p in mgr.backup_dir.iterdir() if p.name.startswith(".dbsnap-")]
    assert leftovers == [], leftovers


def test_next_run_seconds_survives_bad_hour(mgr, monkeypatch):
    import core.backup as B
    for bad in ("junk", None, 99, -1):
        monkeypatch.setattr(B.config, "BACKUPS_HOUR", bad, raising=False)
        secs, target = mgr._next_run_seconds()
        assert 0 < secs <= 24 * 3600
        assert target.hour == 3 and target.minute == 0
    monkeypatch.setattr(B.config, "BACKUPS_HOUR", "7", raising=False)
    _, target = mgr._next_run_seconds()
    assert target.hour == 7    # string "7" coerces instead of crashing


def test_run_scheduled_reports_failure(mgr, monkeypatch):
    monkeypatch.setattr(mgr, "create_backup", MagicMock(return_value=None))
    monkeypatch.setattr(mgr, "rotate_backups", MagicMock())
    result = mgr.run_scheduled()
    assert "FAILED" in result
    assert "complete" not in result
    mgr.rotate_backups.assert_called_once()


def test_run_scheduled_reports_success(mgr, monkeypatch):
    monkeypatch.setattr(mgr, "create_backup",
                        MagicMock(return_value="sapphire_x_daily.tar.gz"))
    monkeypatch.setattr(mgr, "rotate_backups", MagicMock())
    result = mgr.run_scheduled()
    assert "complete" in result and "daily" in result


def test_health_summary_flags_halt(mgr):
    d = mgr.base_dir / "user" / "health"
    d.mkdir(parents=True, exist_ok=True)
    (d / "CORRUPT_main.flag").write_text("x")
    h = mgr.health_summary()
    assert h["halted"] is True
    assert "CORRUPT_main.flag" in h["sentinels"]


def test_health_summary_clean_with_fresh_backup(mgr):
    c = sqlite3.connect(str(mgr.user_dir / "a.db"))
    c.execute("CREATE TABLE t(x)"); c.commit(); c.close()
    assert mgr.create_backup("manual")
    h = mgr.health_summary()
    assert h["halted"] is False
    assert h["newest"] is not None
    assert h["newest"]["age_hours"] is not None and h["newest"]["age_hours"] < 1.0
