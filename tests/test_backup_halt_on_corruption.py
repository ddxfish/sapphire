"""Backup halt-on-corruption gate (2026-07-06 "backups stop on corruption").

If a Sapphire-Health corruption sentinel (user/health/CORRUPT_*.flag) is active,
the scheduled backup cycle must HALT — no create, no rotate — so a corrupt DB
isn't tarred over the last-known-good and good backups aren't aged out under it.

This is a silent-failure surface: nothing in the UI shows the halt, so a
regression that resumed backing up corruption would go unnoticed until a restore
failed. create_backup / rotate_backups are mocked so the test observes the gate,
not real tarring.
"""
import pytest
from unittest.mock import MagicMock


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    import core.backup as B
    monkeypatch.setattr(B.backup_manager, "base_dir", tmp_path)
    monkeypatch.setattr(B.backup_manager, "create_backup", MagicMock())
    monkeypatch.setattr(B.backup_manager, "rotate_backups", MagicMock())

    class Cfg:
        BACKUPS_ENABLED = True
        BACKUPS_KEEP_DAILY = 7
        BACKUPS_KEEP_WEEKLY = 0      # off → no weekday / day-of-month dependence
        BACKUPS_KEEP_MONTHLY = 0
    monkeypatch.setattr(B, "config", Cfg)
    return B.backup_manager


def _sentinel(mgr, name):
    d = mgr.base_dir / "user" / "health"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text("corrupt")


def test_no_sentinel_creates_and_rotates(mgr):
    result = mgr.run_scheduled()
    mgr.create_backup.assert_called_once_with("daily")
    mgr.rotate_backups.assert_called_once()
    assert "HALTED" not in result


def test_sentinel_halts_create_and_rotate(mgr):
    _sentinel(mgr, "CORRUPT_history.flag")
    result = mgr.run_scheduled()
    assert result.startswith("HALTED")
    mgr.create_backup.assert_not_called()      # no new backup of a corrupt DB
    mgr.rotate_backups.assert_not_called()     # last-known-good preserved


def test_cleared_sentinel_resumes(mgr):
    _sentinel(mgr, "CORRUPT_history.flag")
    assert mgr.run_scheduled().startswith("HALTED")
    # user clears the flag after fixing the corruption
    (mgr.base_dir / "user" / "health" / "CORRUPT_history.flag").unlink()
    result = mgr.run_scheduled()
    mgr.create_backup.assert_called_once_with("daily")
    assert "HALTED" not in result


def test_multiple_sentinels_reported_and_halted(mgr):
    _sentinel(mgr, "CORRUPT_history.flag")
    _sentinel(mgr, "CORRUPT_knowledge.flag")
    result = mgr.run_scheduled()
    assert result.startswith("HALTED: 2")
    mgr.create_backup.assert_not_called()
