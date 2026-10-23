"""Updater Wave 3 — backup completeness gate + branch allowlist (2026-08-06 hunt H6/M5).

- create_backup(require_complete=True) refuses instead of shipping a backup
  that silently skipped WAL-busy databases; reason lands in last_backup_error.
- Scheduled backups keep the partial-is-better-than-none default.
- do_update surfaces the busy-DB reason in its refusal message.
- Auto-update branch policy is an allowlist ({'main'}), not a dev blocklist —
  feature branches used to pass preflight and pull unadvertised commits.
"""
import json
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def backup_mgr(tmp_path):
    from core.backup import Backup
    with patch.object(Backup, '__init__', lambda self: None):
        b = Backup()
    b.user_dir = tmp_path / 'user'
    b.user_dir.mkdir()
    (b.user_dir / 'settings.json').write_text('{}')
    b.backup_dir = tmp_path / 'backups'
    b.backup_dir.mkdir()
    return b


def test_require_complete_refuses_on_busy_db(backup_mgr, monkeypatch):
    # N10 contract move (negspace 2026-08-31): consistency comes from the
    # snapshot lane now; require_complete gates on SNAPSHOT failures (a
    # checkpoint-BUSY DB snapshots fine — that's the whole point).
    busy = {Path('/fake/user/chat.db')}
    monkeypatch.setattr(backup_mgr, '_snapshot_databases',
                        lambda staging: ({}, busy))

    result = backup_mgr.create_backup('pre_update', require_complete=True)

    assert result is None
    assert 'busy' in backup_mgr.last_backup_error
    assert 'chat.db' in backup_mgr.last_backup_error
    assert not list(backup_mgr.backup_dir.glob('*.tar.gz')), \
        "refusal must not leave an archive behind"


def test_default_backup_still_ships_despite_busy_db(backup_mgr, monkeypatch):
    """Scheduled backups keep partial-is-better-than-none — only the
    pre-update path demands completeness."""
    busy = {Path('/fake/user/chat.db')}
    monkeypatch.setattr(backup_mgr, '_snapshot_databases',
                        lambda staging: ({}, busy))

    result = backup_mgr.create_backup('daily')

    assert result is not None
    assert (backup_mgr.backup_dir / result).exists()
    assert backup_mgr.last_backup_error is None


def test_complete_backup_passes_require_complete(backup_mgr, monkeypatch):
    monkeypatch.setattr(backup_mgr, '_snapshot_databases',
                        lambda staging: ({}, set()))

    result = backup_mgr.create_backup('pre_update', require_complete=True)

    assert result is not None
    assert (backup_mgr.backup_dir / result).exists()


def _bare_updater(branch='main'):
    from core.updater import Updater
    u = Updater.__new__(Updater)
    u.current_version = '2.6.0'
    u.latest_version = '2.6.1'
    u.update_available = True
    u.last_check = time.time()
    u.checking = False
    u._check_thread = None
    u._bg_thread = None
    u._update_lock = threading.Lock()
    u.git_available = True
    u.branch = branch
    u.is_fork = False
    u._target_sha = None
    return u


def test_do_update_surfaces_busy_db_reason(tmp_path, monkeypatch):
    import core.updater as upd
    monkeypatch.setattr(upd, 'PENDING_UPDATE_FILE', tmp_path / 'pending.json')
    monkeypatch.setattr(upd, 'UPDATE_RESULT_FILE', tmp_path / 'result.json')
    u = _bare_updater()
    monkeypatch.setattr(u, '_preflight_check', lambda: (True, 'ok'))

    fake = MagicMock()
    fake.backup_manager.create_backup.return_value = None
    fake.backup_manager.last_backup_error = '1 database(s) busy mid-write: chat.db'
    monkeypatch.setitem(sys.modules, 'core.backup', fake)

    ok, msg = u.do_update()
    assert ok is False
    assert 'busy' in msg
    assert 'chat.db' in msg
    fake.backup_manager.create_backup.assert_called_once_with(
        'pre_update', require_complete=True)


def test_preflight_refuses_feature_branch(monkeypatch):
    """[M5] Blocklist {'dev'} let ANY other branch through — but the check
    compares against main's VERSION, so pulling a feature branch applies
    commits nobody advertised. Allowlist {'main'} closes it."""
    u = _bare_updater(branch='feature-x')
    ok, msg = u._preflight_check()
    assert not ok
    assert 'main' in msg
    assert 'feature-x' in msg


def test_status_blocked_for_feature_branch():
    u = _bare_updater(branch='feature-x')
    assert u.status()['blocked_branch'] is True


def test_status_not_blocked_on_main():
    u = _bare_updater(branch='main')
    assert u.status()['blocked_branch'] is False
