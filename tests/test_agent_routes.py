"""Surface 5 P1/P2 — /api/agents/* + /api/workspace/* route coverage.

Covers:
  5.41 POST /api/agents/{id}/dismiss goes through cancel path
  5.42 POST /api/agents/{id}/dismiss unknown id → 404
  5.43 GET /api/agents/status filters by chat
  5.44 _validate_workspace rejects path traversal
  5.45 /api/workspace/run idempotent when already running
  5.46 /api/workspace/stop handles ProcessLookupError
  5.47 /api/workspace/status reaps dead procs
  5.48 _detect_run_command priority: main.py > app.py > server.py ...

See tmp/coverage-test-plan.md Surface 5 P2.
"""
import os
from unittest.mock import MagicMock

import pytest


# ─── /api/agents/status ──────────────────────────────────────────────────────

def test_get_agents_status_filters_by_chat(client, mock_system):
    """[PROACTIVE] check_all receives chat_name as filter and route forwards."""
    c, csrf = client
    mock_system.agent_manager.check_all.return_value = [
        {'id': 'a1', 'name': 'Alpha', 'chat_name': 'trinity'},
    ]
    r = c.get('/api/agents/status?chat=trinity')
    assert r.status_code == 200
    assert r.json() == {'agents': [{'id': 'a1', 'name': 'Alpha', 'chat_name': 'trinity'}]}
    mock_system.agent_manager.check_all.assert_called_once_with(chat_name='trinity')


def test_get_agents_status_no_filter_returns_all(client, mock_system):
    c, csrf = client
    mock_system.agent_manager.check_all.return_value = []
    r = c.get('/api/agents/status')
    assert r.status_code == 200
    # Hunt 2026-09-04 S5-06: '' now means the CHATLESS filter inside
    # check_all; the route translates its '' query default to None (= all).
    mock_system.agent_manager.check_all.assert_called_once_with(chat_name=None)


def test_get_agents_status_missing_agent_manager(client, mock_system):
    """If system has no agent_manager, return empty list instead of crashing."""
    c, csrf = client
    # Strip agent_manager from the mock system
    del mock_system.agent_manager
    r = c.get('/api/agents/status')
    assert r.status_code == 200
    assert r.json() == {'agents': []}


# ─── /api/agents/{id}/dismiss ────────────────────────────────────────────────

def test_dismiss_agent_route_delegates_to_manager(client, mock_system):
    """[REGRESSION_GUARD] Route dismiss goes through manager.dismiss, which
    shares the same cancel path as shutdown — ensures subprocess kills fire."""
    c, csrf = client
    mock_system.agent_manager.dismiss.return_value = {
        'name': 'Alpha', 'status': 'dismissed', 'last_result': None,
    }
    r = c.post('/api/agents/aaa111/dismiss', headers={'X-CSRF-Token': csrf})
    assert r.status_code == 200
    assert r.json()['status'] == 'dismissed'
    mock_system.agent_manager.dismiss.assert_called_once_with('aaa111')


def test_dismiss_agent_unknown_id_returns_404(client, mock_system):
    c, csrf = client
    mock_system.agent_manager.dismiss.return_value = {'error': 'Agent aaa not found.'}
    r = c.post('/api/agents/nonexistent/dismiss', headers={'X-CSRF-Token': csrf})
    assert r.status_code == 404


def test_dismiss_agent_missing_manager_returns_404(client, mock_system):
    c, csrf = client
    del mock_system.agent_manager
    r = c.post('/api/agents/any/dismiss', headers={'X-CSRF-Token': csrf})
    assert r.status_code == 404


# ─── _validate_workspace — path traversal guard ──────────────────────────────

def test_validate_workspace_rejects_path_traversal(client, mock_system, tmp_path, monkeypatch):
    """[REGRESSION_GUARD] Workspace lookups must stay inside the configured
    `workspace_dir` base. A project name like `../../etc` must NOT resolve
    outside the sandbox, even if the dir exists.
    """
    from core.routes import agents as agents_route
    base = tmp_path / 'workspaces'
    base.mkdir()
    # Legit project inside
    (base / 'legit_project').mkdir()
    monkeypatch.setattr(agents_route, '_get_workspace_base', lambda: str(base))

    c, csrf = client
    # Legit call works
    r = c.post('/api/workspace/run', headers={'X-CSRF-Token': csrf},
               json={'project': 'legit_project', 'command': 'true'})
    # May succeed or fail on subprocess — we care that it didn't 404 the project
    assert r.status_code in (200, 500), f"legit project rejected unexpectedly: {r.status_code}"

    # Path traversal attempt
    r = c.post('/api/workspace/run', headers={'X-CSRF-Token': csrf},
               json={'project': '../../etc', 'command': 'true'})
    assert r.status_code == 404, \
        f"path traversal should 404; got {r.status_code}"


def test_validate_workspace_rejects_absolute_path(client, mock_system, tmp_path, monkeypatch):
    """Absolute paths in `project` must also be rejected."""
    from core.routes import agents as agents_route
    base = tmp_path / 'workspaces'
    base.mkdir()
    monkeypatch.setattr(agents_route, '_get_workspace_base', lambda: str(base))

    c, csrf = client
    r = c.post('/api/workspace/run', headers={'X-CSRF-Token': csrf},
               json={'project': '/etc/passwd', 'command': 'true'})
    assert r.status_code == 404


# ─── /api/workspace/run idempotent when already running ──────────────────────

def test_workspace_run_idempotent_if_already_running(client, mock_system, tmp_path, monkeypatch):
    """[PROACTIVE] Calling /api/workspace/run on a project that's already
    running returns `already_running` with the existing PID — does NOT
    spawn a duplicate subprocess."""
    from core.routes import agents as agents_route
    base = tmp_path / 'workspaces'
    base.mkdir()
    ws = base / 'active_proj'
    ws.mkdir()
    (ws / 'main.py').write_text('import time; time.sleep(60)\n')
    monkeypatch.setattr(agents_route, '_get_workspace_base', lambda: str(base))

    # Seed _running with a fake live process
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None  # alive
    fake_proc.pid = 99999
    monkeypatch.setitem(agents_route._running, 'active_proj', {
        'proc': fake_proc, 'workspace': str(ws),
        'command': 'python main.py', 'project': 'active_proj',
    })

    c, csrf = client
    r = c.post('/api/workspace/run', headers={'X-CSRF-Token': csrf},
               json={'project': 'active_proj'})
    assert r.status_code == 200
    body = r.json()
    assert body['status'] == 'already_running'
    assert body['pid'] == 99999


# ─── /api/workspace/stop handles ProcessLookupError ──────────────────────────

def test_workspace_stop_handles_process_lookup_error(client, mock_system, tmp_path, monkeypatch):
    """[PROACTIVE] If the process has already died between poll and signal,
    ProcessLookupError must NOT crash the stop endpoint."""
    from core.routes import agents as agents_route

    fake_proc = MagicMock()
    fake_proc.poll.return_value = None  # claims alive
    fake_proc.pid = 88888
    fake_proc.wait.return_value = 0
    fake_proc.returncode = 0
    monkeypatch.setitem(agents_route._running, 'gone_proj', {
        'proc': fake_proc, 'workspace': '/tmp/x',
        'command': 'python main.py', 'project': 'gone_proj',
    })

    # os.killpg raises ProcessLookupError — route must swallow
    def _raise(*args, **kwargs):
        raise ProcessLookupError()
    monkeypatch.setattr(os, 'killpg', _raise)

    c, csrf = client
    r = c.post('/api/workspace/stop', headers={'X-CSRF-Token': csrf},
               json={'project': 'gone_proj'})
    assert r.status_code == 200, f"route crashed on ProcessLookupError: {r.status_code} {r.text}"
    assert r.json()['status'] == 'stopped'


def test_workspace_stop_not_running_returns_not_running(client, mock_system, monkeypatch):
    from core.routes import agents as agents_route
    monkeypatch.setattr(agents_route, '_running', {})
    c, csrf = client
    r = c.post('/api/workspace/stop', headers={'X-CSRF-Token': csrf},
               json={'project': 'never_started'})
    assert r.status_code == 200
    assert r.json()['status'] == 'not_running'


# ─── /api/workspace/status reaps dead procs ──────────────────────────────────

def test_workspace_status_reaps_dead_procs(client, mock_system, monkeypatch):
    """[PROACTIVE] /api/workspace/status removes entries for dead processes
    from the in-memory registry — otherwise the UI shows 'running' forever."""
    from core.routes import agents as agents_route

    live = MagicMock()
    live.poll.return_value = None
    live.pid = 1
    dead = MagicMock()
    dead.poll.return_value = 0  # exited
    dead.pid = 2

    fake_running = {
        'live_proj': {'proc': live, 'workspace': '/tmp/a', 'command': 'x', 'project': 'live_proj'},
        'dead_proj': {'proc': dead, 'workspace': '/tmp/b', 'command': 'y', 'project': 'dead_proj'},
    }
    monkeypatch.setattr(agents_route, '_running', fake_running)

    c, csrf = client
    r = c.get('/api/workspace/status')
    assert r.status_code == 200
    running = r.json()['running']
    names = {e['project'] for e in running}
    assert 'live_proj' in names
    assert 'dead_proj' not in names
    # Reaped from the registry too
    assert 'dead_proj' not in fake_running
    assert 'live_proj' in fake_running


# ─── _detect_run_command priority ────────────────────────────────────────────

def _expected_py_prefix():
    """sys.executable quoted iff the path contains spaces — matches agents.py."""
    import sys as _sys
    return f'"{_sys.executable}"' if ' ' in _sys.executable else _sys.executable


def test_detect_run_command_prefers_main_py(tmp_path):
    from core.routes.agents import _detect_run_command
    (tmp_path / 'main.py').write_text('')
    (tmp_path / 'app.py').write_text('')
    (tmp_path / 'server.py').write_text('')
    cmd = _detect_run_command(str(tmp_path))
    assert cmd == f'{_expected_py_prefix()} main.py'


def test_detect_run_command_falls_through_to_app_py(tmp_path):
    from core.routes.agents import _detect_run_command
    (tmp_path / 'app.py').write_text('')
    (tmp_path / 'server.py').write_text('')
    cmd = _detect_run_command(str(tmp_path))
    assert cmd == f'{_expected_py_prefix()} app.py'


def test_detect_run_command_single_py_file(tmp_path):
    from core.routes.agents import _detect_run_command
    (tmp_path / 'weird_name.py').write_text('')
    cmd = _detect_run_command(str(tmp_path))
    assert cmd == f'{_expected_py_prefix()} weird_name.py'


def test_detect_run_command_html_returns_none(tmp_path):
    """HTML-only projects use the /workspace/{name}/index.html link, not a
    subprocess. _detect_run_command returns None to signal that."""
    from core.routes.agents import _detect_run_command
    (tmp_path / 'index.html').write_text('<html></html>')
    cmd = _detect_run_command(str(tmp_path))
    assert cmd is None


def test_detect_run_command_empty_dir_returns_none_or_nothing(tmp_path):
    """Empty workspace can't be run — detect returns None (caller turns that into 400)."""
    from core.routes.agents import _detect_run_command
    cmd = _detect_run_command(str(tmp_path))
    assert not cmd
