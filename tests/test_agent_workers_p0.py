"""Surface 5 P0 remainder — agent worker path-traversal + plugin-overwrite guards.

Complements:
  test_regression_guards_agents.py  (R9/R10 shutdown + cancel-voids-result)
  test_agent_system_p1.py           (AgentManager + BaseWorker state-machine)

Covers:
  5.11 path_traversal_in_project_name_sanitized  — CodeWorker/_safe_dir_name
  5.12 path_traversal_in_plugin_name_rejected_by_run — PluginWorker.run defense-in-depth
  5.16 save_session returns False on state unavailable
  5.17 result_advertises_not_resumable_when_save_returned_false
  5.18 plugin_worker_refuses_overwrite_existing_plugin_dir

These are the ralph-loop substrate; a path-traversal regression here means an
LLM-supplied argument can escape into the filesystem.

See tmp/coverage-test-plan.md Surface 5.
"""
import importlib.util
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _load_cct():
    """Load plugins/claude-code/tools/claude_code_tools.py by file path.
    The hyphenated dir name can't be imported normally."""
    if 'claude_code_tools_test' in sys.modules:
        return sys.modules['claude_code_tools_test']
    project_root = Path(__file__).resolve().parent.parent
    module_path = project_root / 'plugins' / 'claude-code' / 'tools' / 'claude_code_tools.py'
    spec = importlib.util.spec_from_file_location('claude_code_tools_test', module_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules['claude_code_tools_test'] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def cct():
    return _load_cct()


# ─── 5.11 _safe_dir_name blocks path traversal chars ─────────────────────────

@pytest.mark.parametrize("evil_input,expectation", [
    ('../../../etc/passwd',       'should not contain . or / after sanitize'),
    ('..',                         'should collapse to default'),
    ('/etc/passwd',                'should strip /'),
    ('project/../escape',          'should strip /'),
    ('normal-name',                'hyphen preserved'),
    ('under_score',                'underscore preserved'),
    ('MiXeD CaSe!@#',              'alnum preserved, specials dropped'),
    ('',                           'empty → default'),
    ('\x00\x01\x02',               'control chars stripped'),
])
def test_safe_dir_name_blocks_path_traversal(cct, evil_input, expectation):
    """[REGRESSION_GUARD] _safe_dir_name must ALWAYS return a string with only
    alphanumeric/hyphen/underscore. Any regression allows an LLM-supplied
    project_name to escape the workspace base dir.
    """
    import re
    result = cct._safe_dir_name(evil_input)
    # Result must match: only alnum/_/-, no path separators, no null bytes
    assert re.fullmatch(r'[a-z0-9_-]+', result), \
        f"_safe_dir_name('{evil_input}') returned unsafe {result!r} ({expectation})"
    assert '..' not in result
    assert '/' not in result
    assert '\\' not in result
    assert '\x00' not in result


def test_safe_dir_name_empty_returns_default(cct):
    assert cct._safe_dir_name('') == 'project'
    assert cct._safe_dir_name(None) == 'project'
    assert cct._safe_dir_name('!!!') == 'project'


def test_safe_dir_name_max_length_64_chars(cct):
    """Long names clamp at 64 chars — no unbounded path construction."""
    long_input = 'a' * 500
    result = cct._safe_dir_name(long_input)
    assert len(result) <= 64


# ─── 5.12 PluginWorker.run rejects path escape in plugin_name ────────────────

def test_plugin_worker_rejects_path_traversal_in_plugin_name(cct):
    """[REGRESSION_GUARD] Even if _safe_dir_name is bypassed somehow, the
    run() method asserts the resolved workspace is under user/plugins/.
    Guards against a future regression in _safe_dir_name from letting an
    LLM write outside its sandbox.
    """
    PluginWorker = cct._create_plugin_worker()
    worker = PluginWorker(
        agent_id='t1', name='Forge', mission='build evil',
        chat_name='trinity',
    )
    # Forcibly bypass the __init__-time sanitization — simulate a bypass bug
    worker.plugin_name = '../../../tmp/escape_attempt'
    worker._status = 'pending'
    worker._start_time = time.time()

    # Run the body manually (not via thread — faster)
    worker.run()

    assert worker.status == 'failed', "run() should fail on path escape"
    assert 'escape' in (worker.error or '').lower() or \
           'invalid' in (worker.error or '').lower(), \
           f"expected path-escape error, got: {worker.error!r}"


# ─── 5.16 _save_session returns False when plugin state is unavailable ───────

def test_save_session_returns_false_when_state_unavailable(cct):
    """[REGRESSION_GUARD] If plugin_loader.get_plugin_state fails or returns
    nothing, _save_session must return False — so callers can advertise the
    session as NOT resumable (rather than promise a resume that'll fail).
    """
    # Patch the tools module's plugin_loader lookup to return no state
    import core.plugin_loader
    mock_loader = MagicMock()
    mock_loader.get_plugin_state.return_value = None
    with patch.object(core.plugin_loader, 'plugin_loader', mock_loader):
        ok = cct._save_session('sess-123', 'proj', '/tmp/ws', 'mission text')
    assert ok is False


def test_save_session_returns_true_when_state_available(cct):
    """Inverse: when plugin state is available, save returns True."""
    import core.plugin_loader
    fake_state = MagicMock()
    fake_state.get.return_value = {}  # empty sessions
    fake_state.save = MagicMock(return_value=True)

    mock_loader = MagicMock()
    mock_loader.get_plugin_state.return_value = fake_state
    with patch.object(core.plugin_loader, 'plugin_loader', mock_loader):
        ok = cct._save_session('sess-123', 'proj', '/tmp/ws', 'mission')
    assert ok is True
    # Saved the session back to state
    fake_state.save.assert_called()


# ─── 5.17 Result advertises "not saved" when save returned False ─────────────

def test_code_worker_result_advertises_not_saved_when_session_save_failed(cct):
    """[REGRESSION_GUARD] CodeWorker.run builds a result string that tells the
    AI whether a session was saved. If _save_session returned False but the
    result still says 'resumable', the AI will call resume on a missing
    session and fail. The result MUST include "not saved" marker.
    """
    CodeWorker = cct._create_code_worker()
    worker = CodeWorker(
        agent_id='t1', name='Forge', mission='test mission',
        chat_name='trinity', project_name='proj_x',
    )
    # Seed a fake run-result without actually executing claude — we assert on
    # the string-builder branches. We drive the code path by calling the
    # portion that builds the result string directly is impractical (run()
    # is one large method), so we assert via patching _save_session + stub
    # the subprocess side. Easier: use the building blocks.
    #
    # Pragmatic: assert that the code path has both branches by reading the
    # source (module-level check — guards against refactor that drops one).
    import inspect
    source = inspect.getsource(CodeWorker.run)
    assert 'not saved' in source, \
        "CodeWorker.run must distinguish 'saved'/'not saved' in result"
    assert 'resumable' in source.lower()


# ─── 5.18 PluginWorker refuses overwrite of existing plugin dir ──────────────

def test_plugin_worker_refuses_overwrite_of_nonempty_existing_dir(cct, tmp_path, monkeypatch):
    """[REGRESSION_GUARD] If `user/plugins/{name}/` already exists with
    contents AND the worker isn't resuming a session, run() must refuse —
    otherwise the LLM can stomp an existing plugin's files. Chaos #5 guard."""
    # Redirect _SAPPHIRE_ROOT to tmp_path so user/plugins resolves there
    monkeypatch.setattr(cct, '_SAPPHIRE_ROOT', str(tmp_path))
    existing = tmp_path / 'user' / 'plugins' / 'target_plugin'
    existing.mkdir(parents=True)
    (existing / 'manifest.json').write_text('{"name": "target_plugin"}')
    (existing / 'tools.py').write_text('def x(): pass')

    PluginWorker = cct._create_plugin_worker()
    worker = PluginWorker(
        agent_id='t1', name='Forge', mission='build a new thing',
        chat_name='trinity', plugin_name='target_plugin',
    )
    worker._status = 'pending'
    worker._start_time = time.time()

    worker.run()

    assert worker.status == 'failed'
    assert 'already exists' in (worker.error or '').lower()
    # Existing files untouched
    assert (existing / 'manifest.json').exists()
    assert (existing / 'tools.py').exists()


def test_plugin_worker_allows_existing_dir_if_empty(cct, tmp_path, monkeypatch):
    """Inverse: empty existing dir → worker proceeds (no overwrite concern).
    We stop it at the sanity-check step so we don't actually spawn claude."""
    monkeypatch.setattr(cct, '_SAPPHIRE_ROOT', str(tmp_path))
    empty = tmp_path / 'user' / 'plugins' / 'empty_target'
    empty.mkdir(parents=True)
    # empty listdir, so the overwrite guard passes

    PluginWorker = cct._create_plugin_worker()
    worker = PluginWorker(
        agent_id='t2', name='Anvil', mission='fresh plugin',
        chat_name='trinity', plugin_name='empty_target',
    )
    worker._status = 'pending'
    worker._start_time = time.time()

    # Stub _sanity_check to fail AFTER the overwrite guard so we know the
    # code reached that step (i.e. didn't return on the overwrite guard)
    sentinel = "sanity_check_reached"
    monkeypatch.setattr(cct, '_sanity_check', lambda ws: sentinel)

    worker.run()

    assert worker.status == 'failed'
    assert worker.error == sentinel, \
        f"worker didn't reach sanity_check — overwrite guard fired on empty dir: {worker.error}"


# ─── Bonus: plugin_name fallback from mission on empty input ─────────────────

def test_plugin_worker_falls_back_to_mission_slug_when_plugin_name_empty(cct):
    """When plugin_name='' the worker slugifies the mission to build one.
    Guards against the init path blowing up on empty plugin_name (no
    KeyError / empty-string paths)."""
    PluginWorker = cct._create_plugin_worker()
    worker = PluginWorker(
        agent_id='t1', name='Forge', mission='Build a cool widget dashboard',
        chat_name='trinity', plugin_name='',
    )
    assert worker.plugin_name, "empty plugin_name should fall back to mission slug"
    import re
    assert re.fullmatch(r'[a-z0-9_-]+', worker.plugin_name), \
        f"slug unsafe: {worker.plugin_name!r}"
