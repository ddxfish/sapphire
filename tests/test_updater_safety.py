"""Updater safety regression tests (2026-04-19).

Covers the 14-item fix pass after scout dispatch — pre-flight, concurrency
lock, deferred-pull architecture, subprocess hardening, fork parsing, result
surfacing. Named after the bug class they guard, so breaking one tells you
exactly which scout finding came back.
"""
import json
import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ─── #11 fork detection via parsed path ──────────────────────────────────

def test_parse_github_slug_https():
    from core.updater import _parse_github_slug
    assert _parse_github_slug('https://github.com/ddxfish/sapphire.git') == 'ddxfish/sapphire'
    assert _parse_github_slug('https://github.com/ddxfish/sapphire') == 'ddxfish/sapphire'


def test_parse_github_slug_ssh():
    from core.updater import _parse_github_slug
    assert _parse_github_slug('git@github.com:ddxfish/sapphire.git') == 'ddxfish/sapphire'


def test_parse_github_slug_case_normalized():
    from core.updater import _parse_github_slug
    assert _parse_github_slug('https://github.com/DDXFish/Sapphire.git') == 'ddxfish/sapphire'


def test_parse_github_slug_rejects_similarly_named_fork():
    """[REGRESSION_GUARD] Old substring check treated `ddxfish/sapphire-extra`
    as NOT a fork because 'ddxfish/sapphire' was a substring of the URL.
    Scout finding #11. Parsed-path comparison fixes it."""
    from core.updater import _parse_github_slug, GITHUB_REPO
    slug = _parse_github_slug('https://github.com/ddxfish/sapphire-extra.git')
    assert slug != GITHUB_REPO.lower()
    assert slug == 'ddxfish/sapphire-extra'


def test_parse_github_slug_non_github_returns_none():
    from core.updater import _parse_github_slug
    assert _parse_github_slug('https://gitlab.com/user/repo.git') is None
    assert _parse_github_slug('') is None
    assert _parse_github_slug(None) is None


# ─── #8 subprocess hardening ──────────────────────────────────────────────

def test_run_git_uses_safe_env_and_stdin():
    """[REGRESSION_GUARD] git subprocess must set GIT_TERMINAL_PROMPT=0 and
    GCM_INTERACTIVE=Never (Windows credential-manager GUI hang prevention),
    and stdin=DEVNULL so no prompt can consume from our stdin. Also explicit
    utf-8 encoding since cp1252 crashes on non-ASCII output."""
    import core.updater as u
    import inspect
    src = inspect.getsource(u._run_git) + inspect.getsource(u._git_env)
    assert 'GIT_TERMINAL_PROMPT' in src
    assert 'GCM_INTERACTIVE' in src
    assert 'DEVNULL' in src
    assert "encoding='utf-8'" in src or 'encoding="utf-8"' in src
    assert "errors='replace'" in src or 'errors="replace"' in src


# ─── Updater class fixtures ───────────────────────────────────────────────

@pytest.fixture
def updater_stub(tmp_path, monkeypatch):
    """Build a fresh Updater without touching the real repo/network/files."""
    from core.updater import Updater
    import core.updater as upd
    u = Updater.__new__(Updater)
    u.current_version = '2.6.0'
    u.latest_version = '2.6.1'
    u.update_available = False
    u.last_check = 0
    u.checking = False
    u._check_thread = None
    u._bg_thread = None
    u._update_lock = threading.Lock()
    u.git_available = True
    u.branch = 'main'
    u.is_fork = False
    u._target_sha = None
    # Isolate file paths
    monkeypatch.setattr(upd, 'PENDING_UPDATE_FILE', tmp_path / 'pending.json')
    monkeypatch.setattr(upd, 'UPDATE_RESULT_FILE', tmp_path / 'result.json')
    return u


# ─── #7 dev branch block / #12 git-available / #11 fork / status ──────────

def test_status_exposes_new_fields(updater_stub):
    u = updater_stub
    u.branch = 'dev'
    s = u.status()
    assert s['blocked_branch'] is True
    assert 'git_available' in s
    assert 'pending_update' in s


def test_preflight_refuses_on_dev_branch(updater_stub):
    u = updater_stub
    u.branch = 'dev'
    ok, msg = u._preflight_check()
    assert not ok
    assert 'dev' in msg


def test_preflight_refuses_on_fork(updater_stub):
    u = updater_stub
    u.is_fork = True
    ok, msg = u._preflight_check()
    assert not ok
    assert 'fork' in msg.lower()


def test_preflight_refuses_when_git_missing(updater_stub):
    u = updater_stub
    u.git_available = False
    ok, msg = u._preflight_check()
    assert not ok
    assert 'git' in msg.lower()


# ─── #1 working tree dirty refusal ────────────────────────────────────────

def test_preflight_refuses_on_dirty_tree(updater_stub, monkeypatch):
    """[REGRESSION_GUARD] Uncommitted changes must yield a specific toast
    naming files — not raw git stderr. Scout #1 finding."""
    import core.updater as upd
    u = updater_stub
    # Simulate `git status --porcelain=v2 --branch` with dirty files
    porcelain = (
        "# branch.oid abc123\n"
        "# branch.head main\n"
        "# branch.upstream origin/main\n"
        "1 .M N... 100644 100644 100644 aaa bbb core/plugins/memory/tools/memory_tools.py\n"
        "? user/notes.txt\n"
    )
    monkeypatch.setattr(upd, '_run_git', lambda args, timeout=60: MagicMock(
        returncode=0, stdout=porcelain, stderr=''))
    ok, msg = u._preflight_check()
    assert not ok
    assert 'uncommitted' in msg.lower()
    assert 'memory_tools.py' in msg or 'notes.txt' in msg


def test_preflight_refuses_on_detached_head(updater_stub, monkeypatch):
    import core.updater as upd
    u = updater_stub
    porcelain = "# branch.head (detached)\n# branch.oid abc\n"
    monkeypatch.setattr(upd, '_run_git', lambda args, timeout=60: MagicMock(
        returncode=0, stdout=porcelain, stderr=''))
    ok, msg = u._preflight_check()
    assert not ok
    assert 'detached' in msg.lower()


# ─── #3 concurrency lock ──────────────────────────────────────────────────

def test_do_update_concurrent_calls_reject_cleanly(updater_stub, monkeypatch):
    """[REGRESSION_GUARD] Two rapid clicks / two browser tabs must NOT fire
    two backups + two pulls. The lock rejects the second call with a clear
    message. Scout #3."""
    import core.updater as upd
    u = updater_stub

    # Hold inside preflight so the second call tries to acquire concurrently
    entered = threading.Event()
    release = threading.Event()

    def _slow_preflight():
        entered.set()
        release.wait(timeout=2)
        return True, 'ok'

    monkeypatch.setattr(u, '_preflight_check', _slow_preflight)
    monkeypatch.setattr(upd, '_run_git', lambda args, timeout=60: MagicMock(
        returncode=0, stdout='deadbeef\n', stderr=''))
    # Real backup would require a user/ dir + sqlite — mock it
    mock_backup = MagicMock()
    mock_backup.create_backup.return_value = 'pre_update.tar.gz'
    monkeypatch.setattr('core.backup.backup_manager', mock_backup, raising=False)
    # Also pre-import the backup module so the lazy import inside do_update
    # doesn't need the real file tree
    import sys
    fake = MagicMock()
    fake.backup_manager = mock_backup
    monkeypatch.setitem(sys.modules, 'core.backup', fake)

    results = []
    results_lock = threading.Lock()

    def _click():
        r = u.do_update()
        with results_lock:
            results.append(r)

    t1 = threading.Thread(target=_click, daemon=True)
    t1.start()
    assert entered.wait(timeout=2), "t1 never entered preflight"

    t2 = threading.Thread(target=_click, daemon=True)
    t2.start()
    time.sleep(0.1)  # t2 tries to acquire, should bounce off

    release.set()
    t1.join(timeout=3)
    t2.join(timeout=3)

    assert len(results) == 2, f"expected 2 results, got {results}"
    successes = [r for r in results if r[0] is True]
    failures = [r for r in results if r[0] is False]
    assert len(successes) == 1, f"expected 1 success, got {successes}"
    assert len(failures) == 1, f"expected 1 rejection, got {failures}"
    assert 'already' in failures[0][1].lower()


# ─── #6 backup-must-succeed gate ──────────────────────────────────────────

def test_do_update_refuses_when_backup_fails(updater_stub, monkeypatch):
    """[REGRESSION_GUARD] Silent backup failure used to let update proceed.
    Scout #6. Now it's fatal — no backup = no update."""
    import core.updater as upd
    import sys
    u = updater_stub
    monkeypatch.setattr(u, '_preflight_check', lambda: (True, 'ok'))
    mock_backup = MagicMock()
    mock_backup.create_backup.return_value = None  # backup failed
    fake = MagicMock()
    fake.backup_manager = mock_backup
    monkeypatch.setitem(sys.modules, 'core.backup', fake)
    ok, msg = u.do_update()
    assert not ok
    assert 'backup' in msg.lower()
    assert 'refusing' in msg.lower() or 'failed' in msg.lower()
    # Pending marker must NOT have been written
    assert not upd.PENDING_UPDATE_FILE.exists()


# ─── apply_pending_update failure paths ───────────────────────────────────

def test_apply_pending_update_no_marker_is_noop(tmp_path, monkeypatch):
    import core.updater as upd
    monkeypatch.setattr(upd, 'PENDING_UPDATE_FILE', tmp_path / 'pending.json')
    monkeypatch.setattr(upd, 'UPDATE_RESULT_FILE', tmp_path / 'result.json')
    upd.apply_pending_update()
    assert not (tmp_path / 'result.json').exists()


def test_apply_pending_update_corrupt_marker_writes_result(tmp_path, monkeypatch):
    import core.updater as upd
    pending = tmp_path / 'pending.json'
    result = tmp_path / 'result.json'
    monkeypatch.setattr(upd, 'PENDING_UPDATE_FILE', pending)
    monkeypatch.setattr(upd, 'UPDATE_RESULT_FILE', result)
    pending.write_text('{not valid json', encoding='utf-8')
    upd.apply_pending_update()
    assert result.exists()
    r = json.loads(result.read_text())
    assert r['success'] is False
    assert 'unreadable' in r['message'].lower()
    assert not pending.exists()


def test_apply_pending_update_git_missing_records_failure(tmp_path, monkeypatch):
    import core.updater as upd
    pending = tmp_path / 'pending.json'
    result = tmp_path / 'result.json'
    monkeypatch.setattr(upd, 'PENDING_UPDATE_FILE', pending)
    monkeypatch.setattr(upd, 'UPDATE_RESULT_FILE', result)
    pending.write_text(json.dumps({
        'branch': 'main',
        'target_sha': 'abc',
        'from_version': '2.6.0',
    }), encoding='utf-8')

    def _no_git(*a, **k):
        raise FileNotFoundError('no git')

    monkeypatch.setattr(upd, '_run_git', _no_git)
    upd.apply_pending_update()
    r = json.loads(result.read_text())
    assert r['success'] is False
    assert 'git' in r['message'].lower()
    assert not pending.exists()


def test_apply_pending_update_pull_fail_records_stderr(tmp_path, monkeypatch):
    import core.updater as upd
    pending = tmp_path / 'pending.json'
    result = tmp_path / 'result.json'
    monkeypatch.setattr(upd, 'PENDING_UPDATE_FILE', pending)
    monkeypatch.setattr(upd, 'UPDATE_RESULT_FILE', result)
    pending.write_text(json.dumps({'branch': 'main', 'from_version': '2.6.0'}), encoding='utf-8')
    monkeypatch.setattr(upd, '_run_git', lambda args, timeout=60: MagicMock(
        returncode=1, stdout='', stderr='Your local changes would be overwritten'))
    upd.apply_pending_update()
    r = json.loads(result.read_text())
    assert r['success'] is False
    assert 'would be overwritten' in r['message']
    assert not pending.exists()


def test_apply_pending_update_timeout_clears_index_lock(tmp_path, monkeypatch):
    """[REGRESSION_GUARD] Windows: timed-out git leaves `.git/index.lock`
    behind. apply_pending_update must clear it so the next attempt isn't
    permanently wedged."""
    import core.updater as upd
    pending = tmp_path / 'pending.json'
    result = tmp_path / 'result.json'
    monkeypatch.setattr(upd, 'PENDING_UPDATE_FILE', pending)
    monkeypatch.setattr(upd, 'UPDATE_RESULT_FILE', result)
    pending.write_text(json.dumps({'branch': 'main', 'from_version': '2.6.0'}), encoding='utf-8')

    def _timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd='git', timeout=180)

    monkeypatch.setattr(upd, '_run_git', _timeout)
    lock_cleared = [False]
    monkeypatch.setattr(upd, '_clear_index_lock', lambda: lock_cleared.__setitem__(0, True))
    upd.apply_pending_update()
    assert lock_cleared[0] is True
    r = json.loads(result.read_text())
    assert r['success'] is False
    assert 'timed out' in r['message'].lower()


# ─── #4 / #2 main.py wires the deferred apply ─────────────────────────────

def test_main_py_calls_apply_pending_before_popen():
    """[REGRESSION_GUARD] main.py must call apply_pending_update() BEFORE
    subprocess.Popen(sapphire.py) — that's the entire point of deferring the
    pull. Windows file locks prevent git pull from replacing .py files while
    sapphire.py is running. Scout #4."""
    src = (Path(__file__).parent.parent / 'main.py').read_text()
    assert 'apply_pending_update' in src, "main.py must import apply_pending_update"
    apply_idx = src.find('apply_pending_update()')
    popen_idx = src.find('subprocess.Popen(')
    assert apply_idx > 0
    assert popen_idx > 0
    assert apply_idx < popen_idx, "apply_pending_update must run BEFORE subprocess.Popen"


def test_apply_pending_update_runs_pip_sync():
    """[REGRESSION_GUARD] Scout #2 — new deps after pull used to silently
    brick the next boot. apply_pending_update must call pip install after a
    successful pull."""
    import core.updater as upd
    import inspect
    src = inspect.getsource(upd.apply_pending_update)
    assert 'pip' in src
    assert 'requirements.txt' in src


# ─── Result file ──────────────────────────────────────────────────────────

def test_read_last_update_result_clears_by_default(tmp_path, monkeypatch):
    import core.updater as upd
    result = tmp_path / 'result.json'
    monkeypatch.setattr(upd, 'UPDATE_RESULT_FILE', result)
    result.write_text(json.dumps({'success': True, 'message': 'yay'}), encoding='utf-8')
    data = upd.read_last_update_result(clear=True)
    assert data['success'] is True
    assert not result.exists()


def test_read_last_update_result_can_preserve(tmp_path, monkeypatch):
    import core.updater as upd
    result = tmp_path / 'result.json'
    monkeypatch.setattr(upd, 'UPDATE_RESULT_FILE', result)
    result.write_text(json.dumps({'success': False, 'message': 'nope'}), encoding='utf-8')
    data = upd.read_last_update_result(clear=False)
    assert data['success'] is False
    assert result.exists()


def test_read_last_update_result_none_when_absent(tmp_path, monkeypatch):
    import core.updater as upd
    monkeypatch.setattr(upd, 'UPDATE_RESULT_FILE', tmp_path / 'nope.json')
    assert upd.read_last_update_result() is None


# ─── #13 plugin_verify stray-file tolerance ───────────────────────────────

def test_plugin_verify_ignores_platform_stray_files():
    """[REGRESSION_GUARD] Windows `Thumbs.db`/`desktop.ini`, macOS `.DS_Store`,
    etc. in a plugin dir must not cause the 'unrecognized file not in
    manifest' rejection. Scout Windows #2."""
    from core.plugin_verify import _IGNORABLE_FILENAMES
    assert 'Thumbs.db' in _IGNORABLE_FILENAMES
    assert 'desktop.ini' in _IGNORABLE_FILENAMES
    assert '.DS_Store' in _IGNORABLE_FILENAMES


def test_plugin_verify_tolerates_stray_file_in_real_plugin_dir(tmp_path):
    """Behavior test: a plugin dir with a Thumbs.db verifies cleanly."""
    import base64
    import hashlib
    import json as js
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from core import plugin_verify as pv

    plug = tmp_path / 'demo'
    plug.mkdir()
    (plug / 'plugin.json').write_text('{"name": "demo", "version": "1.0"}', encoding='utf-8')
    # The platform-injected junk
    (plug / 'Thumbs.db').write_bytes(b'\x00\x01\x02')

    # Build a valid sig signed by a fresh test key
    priv = Ed25519PrivateKey.generate()
    pub_bytes = priv.public_key().public_bytes_raw()

    manifest_path = plug / 'plugin.json'
    content = manifest_path.read_bytes().replace(b'\r\n', b'\n')
    h = f"sha256:{hashlib.sha256(content).hexdigest()}"
    sig_data = {'files': {'plugin.json': h}}
    payload = js.dumps(sig_data, sort_keys=True, separators=(',', ':')).encode()
    sig_bytes = priv.sign(payload)
    sig_data['signature'] = base64.b64encode(sig_bytes).decode()
    (plug / 'plugin.sig').write_text(js.dumps(sig_data), encoding='utf-8')

    # Swap the baked-in pubkey for our test key, then verify
    orig_key = pv.SIGNING_PUBLIC_KEY
    try:
        pv.SIGNING_PUBLIC_KEY = pub_bytes
        ok, msg = pv._verify_file_integrity(plug, sig_data)
        assert ok, f"verify failed despite Thumbs.db being in ignored set: {msg}"
    finally:
        pv.SIGNING_PUBLIC_KEY = orig_key


# ─── #10 route returns response before restart ────────────────────────────

def test_update_route_defers_restart():
    """[REGRESSION_GUARD] Route must return HTTP 200 BEFORE triggering
    restart, otherwise the TCP connection can be torn down mid-response and
    client sees 'update failed' for a scheduled update. Scout #10. Done
    via asyncio.create_task delay."""
    src = (Path(__file__).parent.parent / 'core/routes/system.py').read_text()
    start = src.find('async def do_update')
    assert start > 0
    end = src.find('\n@router', start + 1)
    body = src[start:end if end > 0 else len(src)]
    assert 'asyncio.create_task' in body or 'asyncio.sleep' in body, \
        "do_update must defer the restart callback"


# ─── #14 pollForRestart bumped to cover real deferred-update time ─────────

def test_frontend_poll_timeout_covers_pip_install_window():
    """[REGRESSION_GUARD] Deferred update can take minutes (git pull + pip
    install before FastAPI boot). The old 30-attempt/30-second timeout gave
    up too early. Scout #14-adjacent."""
    src = (Path(__file__).parent.parent / 'interfaces/web/static/views/settings-tabs/dashboard.js').read_text()
    assert 'maxAttempts' in src
    # Must be significantly larger than the original 30 to handle real update
    # cycles. Source regex check: at least 100.
    import re
    m = re.search(r'maxAttempts\s*=\s*(\d+)', src)
    assert m, "maxAttempts constant not found"
    assert int(m.group(1)) >= 100, f"maxAttempts too low for deferred update flow: {m.group(1)}"
