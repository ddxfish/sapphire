"""plugins/claude-code: the 2026-10-07 scout fixes to the kind and its helpers."""
import asyncio
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
kind = importlib.import_module('plugins.claude-code.agent_kind')
helpers = importlib.import_module('plugins.claude-code.tools.claude_code_tools')


class TestHelpers:
    def test_windows_reserved_names_never_become_a_workspace(self):
        for bad in ('con', 'NUL', 'aux', 'com1', 'LPT9', 'prn'):
            assert helpers._safe_dir_name(bad) == f"{bad.lower()}-ws"
            assert helpers._slugify(f"{bad} please") != bad.lower()
        assert helpers._safe_dir_name('console') == 'console'          # a prefix is not a device
        assert helpers._safe_dir_name('') == 'project' and helpers._slugify('!!!') == 'project'

    def test_python_environment_guard_reads_windows_paths_too(self, tmp_path):
        root = tmp_path / 'sapphire'
        root.mkdir()
        env_ws = tmp_path / 'miniconda3' / 'envs' / 'x' / 'work'
        env_ws.mkdir(parents=True)
        err = helpers._sanity_check(str(env_ws), mode='project', root=str(root))
        assert err and 'Python environment' in err
        # the check compares '/'-shaped markers against the posix form of the path,
        # so a backslash path on Windows is caught the same way
        src = (ROOT / 'plugins' / 'claude-code' / 'tools' / 'claude_code_tools.py').read_text(encoding='utf-8')
        assert '.as_posix().lower()' in src

    def test_a_typed_binary_name_is_looked_up_as_typed(self, monkeypatch, tmp_path):
        seen = []
        monkeypatch.setattr(helpers.shutil, 'which', lambda n, path='': seen.append(n) or (f'/bin/{n}' if n == 'claude-dev' else None))
        path, err = helpers._resolve_claude_executable({'PATH': '/bin'}, name='claude-dev')
        assert err is None and path == '/bin/claude-dev' and seen == ['claude-dev']
        seen.clear()
        path, err = helpers._resolve_claude_executable({'PATH': '/bin'}, name='nope')
        assert path is None and err and seen == ['nope', 'claude']    # falls back to the plain name


class TestKind:
    def test_the_sdk_is_declared(self):
        assert 'claude-agent-sdk' in (ROOT / 'requirements.txt').read_text(encoding='utf-8')

    def test_the_session_runs_on_its_own_loop_not_asyncio_run(self):
        """Windows: sapphire.py pins the Selector policy; a Selector loop cannot
        spawn the CLI. The kind builds a Proactor loop in its thread (as
        plugins/mcp_client does) and never calls asyncio.run()."""
        src = (ROOT / 'plugins' / 'claude-code' / 'agent_kind.py').read_text(encoding='utf-8')
        assert 'asyncio.run(self._main' not in src           # the call that inherited the Selector policy
        assert "asyncio.ProactorEventLoop() if sys.platform == 'win32' else asyncio.new_event_loop()" in src

    def test_run_loop_runs_a_coroutine_and_leaves_no_loop_behind(self):
        a = object.__new__(kind.Agent)

        async def work():
            await asyncio.sleep(0)
            return 'ran'

        assert kind.Agent._run_loop(a, work()) == 'ran'
        assert asyncio._get_running_loop() is None      # nothing left running on this thread

    def test_a_stop_before_the_cli_connects_runs_nothing(self, monkeypatch):
        """The × in the first seconds used to let the whole mission run anyway
        while the UI said 'stopped' (race scout)."""
        src = (ROOT / 'plugins' / 'claude-code' / 'agent_kind.py').read_text(encoding='utf-8')
        main = src.split('async def _main(')[1].split('def _next_say')[0]
        assert main.index('self._client = client') < main.index('if self._cancelled.is_set():\n                    return') < main.index('await self._turn(sdk, mission)')
        run = src.split('    def run(self, mission):')[1].split('def _run_loop')[0]
        assert 'if self._cancelled.is_set():\n            return' in run.split('settings = _settings()')[0]

    def test_a_wake_reuses_the_rows_workspace_name_and_the_idle_timeout_takes_a_late_say(self):
        src = (ROOT / 'plugins' / 'claude-code' / 'agent_kind.py').read_text(encoding='utf-8')
        assert "row.get('wsname')" in src and 'self.remember(wsname=self.wsname)' in src
        main = src.split('async def _main(')[1]
        assert "self.status = 'resting'\n                        text = self._next_say(0)" in main
        assert 'self._end_question(None)' in main.split('finally:')[1]

    def test_the_kind_builds_from_a_row_with_a_remembered_name(self, monkeypatch):
        monkeypatch.setattr(kind, '_helpers', lambda: helpers)
        eng = SimpleNamespace(_row_update=lambda *a, **k: None)
        row = {'id': 'x1', 'name': 'Forge', 'kind': 'claude_code', 'chat': 'desk', 'mission': 'fix the tests please',
               'options': {'mode': 'project'}, 'wsname': 'my-site', 'resume_token': 'sess'}
        a = kind.Agent(row, eng)
        assert a.wsname == 'my-site', 'a wake must not re-derive the workspace from the say text'
        row2 = dict(row, wsname=None, options={'mode': 'project', 'name': 'aux'})
        assert kind.Agent(row2, eng).wsname == 'aux-ws'
