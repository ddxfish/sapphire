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


# ─── the claude_code kind's workspace guards ─────────────────────────────────

class _Engine:
    def install_carrier(self, a): return None
    def release_carrier(self, t): pass
    def finished(self, a): pass
    def report_out(self, a, t): pass
    def event_out(self, a, k): pass
    def question_out(self, a, q): pass


def _kind():
    import importlib
    return importlib.import_module('plugins.claude-code.agent_kind')


def _agent(**opts):
    row = {'id': 't1', 'name': 'Forge', 'kind': 'claude_code', 'chat': 'trinity',
           'mission': opts.pop('mission', 'build evil'), 'options': opts, 'privacy': False,
           'resume_token': opts.pop('resume_token', None)}
    return _kind().Agent(row, _Engine())


def test_plugin_mode_rejects_path_traversal_in_the_name(cct, tmp_path, monkeypatch):
    """Even if _safe_dir_name were bypassed, _workspace() asserts the resolved
    dir is under user/plugins/ (defense in depth)."""
    monkeypatch.setattr(_kind(), '_ROOT', str(tmp_path))
    a = _agent(mode='plugin', name='x')
    a.wsname = '../../../tmp/escape_attempt'
    with pytest.raises(RuntimeError, match='escape'):
        a._workspace({})


def test_plugin_mode_refuses_overwrite_of_a_nonempty_existing_dir(cct, tmp_path, monkeypatch):
    """Chaos #5: user/plugins/<name> exists with contents and this is not a
    resume -> refuse, files untouched."""
    monkeypatch.setattr(_kind(), '_ROOT', str(tmp_path))
    existing = tmp_path / 'user' / 'plugins' / 'target_plugin'
    existing.mkdir(parents=True)
    (existing / 'manifest.json').write_text('{"name": "target_plugin"}')
    a = _agent(mode='plugin', name='target_plugin')
    with pytest.raises(RuntimeError, match='already exists'):
        a._workspace({})
    assert (existing / 'manifest.json').exists()
    # a resume of the agent that built it is allowed back in
    b = _agent(mode='plugin', name='target_plugin', resume_token='sess-1')
    assert b._workspace({}) == str(existing.resolve())


def test_plugin_mode_allows_an_empty_existing_dir(cct, tmp_path, monkeypatch):
    monkeypatch.setattr(_kind(), '_ROOT', str(tmp_path))
    (tmp_path / 'user' / 'plugins' / 'empty_target').mkdir(parents=True)
    a = _agent(mode='plugin', name='empty_target')
    assert a._workspace({}).endswith('empty_target')


def test_workspace_name_falls_back_to_the_mission_slug(cct):
    import re
    a = _agent(mode='project', mission='Build a cool widget dashboard')
    assert a.wsname and re.fullmatch(r'[a-z0-9_-]+', a.wsname)


def test_core_mode_is_the_root_and_project_mode_stays_outside_it(cct, tmp_path, monkeypatch):
    k = _kind()
    monkeypatch.setattr(k, '_ROOT', str(tmp_path / 'sapphire'))
    (tmp_path / 'sapphire').mkdir()
    assert _agent(mode='core')._workspace({}) == str(tmp_path / 'sapphire')
    ws = _agent(mode='project', name='proj')._workspace({'workspace_dir': str(tmp_path / 'work')})
    assert ws == str((tmp_path / 'work' / 'proj').resolve())
    # a project workspace inside Sapphire's tree is refused
    with pytest.raises(RuntimeError, match='inside'):
        _agent(mode='project', name='inside')._workspace({'workspace_dir': str(tmp_path / 'sapphire')})


def test_sanity_check_is_mode_aware(cct, tmp_path, monkeypatch):
    monkeypatch.setattr(cct, '_SAPPHIRE_ROOT', str(tmp_path))
    assert cct._sanity_check(str(tmp_path), mode='core') is None
    assert 'SAFETY' in cct._sanity_check(str(tmp_path / 'sub'), mode='core')
    assert 'SAFETY' in cct._sanity_check(str(tmp_path), mode='project')
    assert cct._sanity_check(str(tmp_path / 'user' / 'plugins' / 'x'), mode='plugin') is None
    assert 'SAFETY' in cct._sanity_check(str(tmp_path / 'elsewhere'), mode='plugin')
