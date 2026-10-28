"""Backups, hunt 19 (2026-10-27).

- Windows Errno 13: the instance lock's locked byte failed every backup. The
  lock left user/, and the skip probe now reads a byte (a byte-range lock
  opens fine and refuses the read).
- A backup missing a database is loud, and holds rotation so a nightly
  failure never ages out the archives that still hold it.
- A crashed run's plaintext leftovers (.dbsnap-*, *.partial) are swept.
- A folder that cannot really be written falls back to the default.
"""
import os
import sqlite3
import sys
import tarfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


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
        BACKUPS_KEEP_DAILY = 1
        BACKUPS_KEEP_WEEKLY = 0
        BACKUPS_KEEP_MONTHLY = 0
        BACKUPS_HOUR = 3
    monkeypatch.setattr(B, "config", Cfg)
    return b


def test_a_file_that_opens_but_refuses_reads_is_skipped_not_fatal(mgr):
    """Windows LockFile: open() succeeds, read() raises Errno 13."""
    (mgr.user_dir / "keep.txt").write_text("hi")
    locked = mgr.user_dir / "locked.bin"
    locked.write_bytes(b"x")
    real_open = open

    class Refuses:
        def __init__(self, fh): self.fh = fh
        def __enter__(self): return self
        def __exit__(self, *a): self.fh.close()
        def read(self, *a): raise PermissionError(13, "Permission denied")

    def fake_open(path, mode='r', *a, **k):
        fh = real_open(path, mode, *a, **k)
        return Refuses(fh) if str(path) == str(locked) and 'b' in mode else fh

    with patch("builtins.open", fake_open):
        fn = mgr.create_backup("manual")
    assert fn
    with tarfile.open(mgr.backup_dir / fn) as t:
        names = t.getnames()
    assert "user/keep.txt" in names and "user/locked.bin" not in names


def test_an_omitted_database_is_loud_and_holds_rotation(mgr):
    (mgr.user_dir / "a.txt").write_text("a")
    db = mgr.user_dir / "chats.db"
    sqlite3.connect(str(db)).close()
    old = mgr.backup_dir / "sapphire_2020-01-01_000000_daily.tar.gz"
    old.write_bytes(b"old")
    with patch.object(type(mgr), "_snapshot_databases", lambda self, d: ({}, {db.resolve()})):
        msg = mgr.run_scheduled()
    assert mgr.last_backup_omitted == ["chats.db"]
    assert "INCOMPLETE" in msg and "rotation held" in msg
    assert old.exists()                 # keep=1 would have deleted it


def test_a_complete_run_still_rotates(mgr):
    (mgr.user_dir / "a.txt").write_text("a")
    old = mgr.backup_dir / "sapphire_2020-01-01_000000_daily.tar.gz"
    old.write_bytes(b"old")
    assert "complete" in mgr.run_scheduled()
    assert not old.exists()


def test_crashed_run_leftovers_are_swept(mgr):
    snap = mgr.backup_dir / ".dbsnap-2020-01-01_000000"
    snap.mkdir()
    (snap / "0_chats.db").write_bytes(b"plain")
    part = mgr.backup_dir / "sapphire_2020-01-01_000000_daily.tar.gz.partial"
    part.write_bytes(b"plain")
    fresh = mgr.backup_dir / ".dbsnap-now"
    fresh.mkdir()
    day_ago = time.time() - 2 * 86400
    for p in (snap, part):
        os.utime(p, (day_ago, day_ago))
    mgr._sweep_stale_tmp()
    assert not snap.exists() and not part.exists()
    assert fresh.exists()               # a run in flight keeps its staging


def test_an_unwritable_backup_folder_falls_back(tmp_path, monkeypatch):
    import core.backup as B
    from core.backup import Backup
    with patch.object(Backup, '__init__', lambda self: None):
        b = Backup()
    b.base_dir = tmp_path
    b.user_dir = tmp_path / "user"
    b.user_dir.mkdir()
    b._backup_dir_override = None
    target = tmp_path / "protected"
    target.mkdir()

    class Cfg:
        BACKUPS_DIR = str(target)
    monkeypatch.setattr(B, "config", Cfg)

    def refuse(*a, **k):
        raise PermissionError(13, "Permission denied")
    with patch.object(B.tempfile, "TemporaryFile", refuse):
        chosen = b.backup_dir
    assert chosen == tmp_path / "user_backups"
    assert "unusable" in b.backup_dir_error
