"""Updater Wave 2 — core rollback + SHA pin + cancel (2026-08-06 hunt C1/H5/H4).

apply_pending_update contract after the wave:
- refuses markers older than 24h (stranded-marker fast-forward hazard)
- refuses on a dirty tree (slim re-preflight)
- fetch + `merge --ff-only <target_sha>` — the pin is now READ, not just written
- pip failure rolls the code back to from_sha ("rather not update than fail")
- cancel_pending_update() clears a scheduled marker
"""
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest


class GitRecorder:
    """Fake _run_git — records every call, returns per-subcommand results."""

    def __init__(self, results=None):
        self.calls = []
        self.results = results or {}

    def __call__(self, args, timeout=60):
        self.calls.append(list(args))
        r = self.results.get(args[0])
        if r is None:
            r = MagicMock(returncode=0, stdout='', stderr='')
        return r

    def called(self, *prefix):
        return any(c[:len(prefix)] == list(prefix) for c in self.calls)


def _marker(tmp_path, monkeypatch, **overrides):
    import core.updater as upd
    pending = tmp_path / 'pending.json'
    result = tmp_path / 'result.json'
    monkeypatch.setattr(upd, 'PENDING_UPDATE_FILE', pending)
    monkeypatch.setattr(upd, 'UPDATE_RESULT_FILE', result)
    data = {'branch': 'main', 'from_version': '2.6.0', 'to_version': '2.6.1',
            'target_sha': 'abc123def', 'from_sha': 'aaa111', 'requested_at': time.time()}
    data.update(overrides)
    pending.write_text(json.dumps(data), encoding='utf-8')
    return pending, result


def _pip(monkeypatch, returncodes=(0,)):
    """Patch subprocess.run (pip calls only — _run_git is replaced wholesale).
    Yields the given returncodes in order, then repeats the last."""
    calls = []
    rcs = list(returncodes)

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        rc = rcs.pop(0) if len(rcs) > 1 else rcs[0]
        return MagicMock(returncode=rc, stdout='', stderr='pip boom' if rc else '')

    monkeypatch.setattr(subprocess, 'run', fake_run)
    return calls


# ─── H5: the SHA pin is finally read ──────────────────────────────────────

def test_apply_merges_to_pinned_target_sha(tmp_path, monkeypatch):
    import core.updater as upd
    pending, result = _marker(tmp_path, monkeypatch)
    git = GitRecorder()
    monkeypatch.setattr(upd, '_run_git', git)
    _pip(monkeypatch)

    upd.apply_pending_update()

    assert git.called('fetch', 'origin', 'main')
    assert git.called('merge', '--ff-only', 'abc123def'), f"calls: {git.calls}"
    assert not git.called('pull'), "bare pull lands whatever origin HEAD is NOW"
    r = json.loads(result.read_text())
    assert r['success'] is True
    assert not pending.exists()


def test_apply_without_target_sha_merges_branch_head(tmp_path, monkeypatch):
    """Older marker format (no target_sha) still updates — via origin/<branch>."""
    import core.updater as upd
    pending, result = _marker(tmp_path, monkeypatch, target_sha=None)
    git = GitRecorder()
    monkeypatch.setattr(upd, '_run_git', git)
    _pip(monkeypatch)

    upd.apply_pending_update()

    assert git.called('merge', '--ff-only', 'origin/main')
    assert json.loads(result.read_text())['success'] is True


def test_apply_merge_failure_keeps_previous(tmp_path, monkeypatch):
    """Upstream force-pushed the pinned SHA away → ff-merge fails → refuse."""
    import core.updater as upd
    pending, result = _marker(tmp_path, monkeypatch)
    git = GitRecorder({'merge': MagicMock(returncode=1, stdout='', stderr='not possible to fast-forward')})
    monkeypatch.setattr(upd, '_run_git', git)
    _pip(monkeypatch)

    upd.apply_pending_update()

    r = json.loads(result.read_text())
    assert r['success'] is False
    assert 'Kept previous version' in r['message']
    assert not pending.exists()


# ─── H4: stale markers expire instead of fast-forwarding blind ────────────

def test_apply_expired_marker_refuses_without_touching_git(tmp_path, monkeypatch):
    import core.updater as upd
    pending, result = _marker(tmp_path, monkeypatch, requested_at=time.time() - 90000)
    git = GitRecorder()
    monkeypatch.setattr(upd, '_run_git', git)

    upd.apply_pending_update()

    assert git.calls == [], "expired marker must not run ANY git"
    r = json.loads(result.read_text())
    assert r['success'] is False
    assert 'expired' in r['message'].lower()
    assert not pending.exists()


# ─── H5: slim re-preflight ────────────────────────────────────────────────

def test_apply_dirty_tree_refuses(tmp_path, monkeypatch):
    import core.updater as upd
    pending, result = _marker(tmp_path, monkeypatch)
    git = GitRecorder({'status': MagicMock(returncode=0, stdout=' M core/foo.py\n', stderr='')})
    monkeypatch.setattr(upd, '_run_git', git)

    upd.apply_pending_update()

    assert not git.called('fetch')
    r = json.loads(result.read_text())
    assert r['success'] is False
    assert 'Working tree' in r['message']


# ─── C1: pip failure rolls back ───────────────────────────────────────────

def test_pip_failure_rolls_back_to_from_sha(tmp_path, monkeypatch):
    import core.updater as upd
    pending, result = _marker(tmp_path, monkeypatch)
    git = GitRecorder()
    monkeypatch.setattr(upd, '_run_git', git)
    _pip(monkeypatch, returncodes=(1, 0))  # install fails, rollback re-pip succeeds

    upd.apply_pending_update()

    assert git.called('reset', '--hard', 'aaa111'), f"calls: {git.calls}"
    r = json.loads(result.read_text())
    assert r['success'] is False
    assert 'Rolled back' in r['message']
    assert not pending.exists()


def test_pip_failure_without_from_sha_reports_manual_fix(tmp_path, monkeypatch):
    """Older marker without from_sha — can't roll back, must say so plainly."""
    import core.updater as upd
    pending, result = _marker(tmp_path, monkeypatch, from_sha=None)
    git = GitRecorder()
    monkeypatch.setattr(upd, '_run_git', git)
    _pip(monkeypatch, returncodes=(1,))

    upd.apply_pending_update()

    assert not git.called('reset')
    r = json.loads(result.read_text())
    assert r['success'] is False
    assert 'manually' in r['message']


def test_pip_failure_reset_failure_reports_both(tmp_path, monkeypatch):
    import core.updater as upd
    pending, result = _marker(tmp_path, monkeypatch)
    git = GitRecorder({'reset': MagicMock(returncode=1, stdout='', stderr='denied')})
    monkeypatch.setattr(upd, '_run_git', git)
    _pip(monkeypatch, returncodes=(1,))

    upd.apply_pending_update()

    r = json.loads(result.read_text())
    assert r['success'] is False
    assert 'ALSO failed' in r['message']
    assert 'aaa111' in r['message'], "message must carry the manual-recovery SHA"


# ─── H4: cancel endpoint backing ──────────────────────────────────────────

def test_cancel_pending_update_clears_marker(tmp_path, monkeypatch):
    import core.updater as upd
    from core.updater import Updater
    pending = tmp_path / 'pending.json'
    monkeypatch.setattr(upd, 'PENDING_UPDATE_FILE', pending)
    pending.write_text('{}', encoding='utf-8')
    u = Updater.__new__(Updater)

    assert u.cancel_pending_update() is True
    assert not pending.exists()
    assert u.cancel_pending_update() is False


# ─── C1: do_update records the rollback anchor ────────────────────────────

def test_do_update_marker_contains_from_sha(tmp_path, monkeypatch):
    import core.updater as upd
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
    u.branch = 'main'
    u.is_fork = False
    u._target_sha = None
    monkeypatch.setattr(upd, 'PENDING_UPDATE_FILE', tmp_path / 'pending.json')
    monkeypatch.setattr(upd, 'UPDATE_RESULT_FILE', tmp_path / 'result.json')
    monkeypatch.setattr(u, '_preflight_check', lambda: (True, 'ok'))

    fake_backup = MagicMock()
    fake_backup.backup_manager.create_backup.return_value = True
    monkeypatch.setitem(sys.modules, 'core.backup', fake_backup)

    def fake_git(args, timeout=60):
        if args[0] == 'rev-parse' and args[1] == 'origin/main':
            return MagicMock(returncode=0, stdout='TARGETSHA\n', stderr='')
        if args[0] == 'rev-parse' and args[1] == 'HEAD':
            return MagicMock(returncode=0, stdout='FROMSHA\n', stderr='')
        return MagicMock(returncode=0, stdout='', stderr='')

    monkeypatch.setattr(upd, '_run_git', fake_git)

    ok, msg = u.do_update()
    assert ok is True, msg
    marker = json.loads((tmp_path / 'pending.json').read_text())
    assert marker['from_sha'] == 'fromsha'
    assert marker['target_sha'] == 'targetsha'
