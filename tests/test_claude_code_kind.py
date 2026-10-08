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
        assert "row.get('wsname')" in src and 'self.remember(wsname=self.wsname, mode=self.mode)' in src
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


class TestSecondWave:
    """The 2026-10-08 fixes (six-scout hunt)."""

    def test_the_cli_env_is_an_allowlist_and_everything_else_is_blanked(self, monkeypatch):
        """The SDK lays `env` OVER os.environ: a SOCKS credential in Sapphire's
        process reached the CLI's Bash (privacy scout). Every inherited variable
        not on the allowlist is blanked by name; the allowlist is copied clean."""
        monkeypatch.setenv('HTTPS_PROXY', 'socks5h://user:secret@host:1080')
        monkeypatch.setenv('SAPPHIRE_SECRET', 'x')
        monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-live')
        monkeypatch.setenv('LC_ALL', 'C.UTF-8')
        monkeypatch.setenv('CLAUDE_CODE_FOO', 'bar')
        monkeypatch.setenv('HOME', '/home/k')
        env = helpers._cli_env()
        assert env['HTTPS_PROXY'] == '' and env['SAPPHIRE_SECRET'] == '' and env['ANTHROPIC_API_KEY'] == ''
        assert 'LC_ALL' not in env and 'CLAUDE_CODE_FOO' not in env         # allowlisted: inherited as-is, not blanked
        assert env['HOME'] == '/home/k' and 'PATH' in env
        src = (ROOT / 'plugins' / 'claude-code' / 'agent_kind.py').read_text(encoding='utf-8')
        assert 'env = h._cli_env()' in src.split('def _options(')[1]

    def test_the_workspace_name_comes_from_the_agent_never_the_mission(self, monkeypatch):
        """A slug of the mission's first words sat in agents.json (metadata-only
        by contract), in a directory name and in a replayed event (privacy scout)."""
        monkeypatch.setattr(kind, '_helpers', lambda: helpers)
        eng = SimpleNamespace(_row_update=lambda *a, **k: None)
        row = {'id': 'abcdef1234', 'name': 'Forge', 'kind': 'claude_code', 'chat': 'desk',
               'mission': 'Fix the export bug in my therapy notes diary', 'options': {'mode': 'project'}}
        a = kind.Agent(row, eng)
        assert a.wsname == 'forge-abcdef12'
        assert 'therapy' not in a.wsname and 'export' not in a.wsname
        src = (ROOT / 'plugins' / 'claude-code' / 'tools' / 'claude_code_tools.py').read_text(encoding='utf-8')
        assert "}, ephemeral=True)" in src.split('Events.WORKSPACE_READY')[1].split('except')[0]

    def test_windows_prefers_the_native_exe_and_refuses_the_npm_shim(self, monkeypatch):
        """`which('claude')` on an npm box returns claude.cmd - the one thing the
        SDK refuses to execute (windows scout)."""
        monkeypatch.setattr(helpers, '_IS_WINDOWS', True)
        table = {'claude.exe': r'D:\tools\claude\claude.exe', 'claude': r'D:\tools\npm\claude.cmd'}
        monkeypatch.setattr(helpers.shutil, 'which', lambda n, path='': table.get(n))
        path, err = helpers._resolve_claude_executable({'PATH': 'x'}, name='claude')
        assert err is None and path.endswith('claude.exe')
        table.pop('claude.exe')                                   # npm only: the shim is all there is
        path, err = helpers._resolve_claude_executable({'PATH': 'x'}, name='claude')
        assert path is None and 'batch shim' in err and 'EMPTY' in err
        path, err = helpers._resolve_claude_executable({'PATH': 'x'}, name='claude.cmd')
        assert path is None and 'batch shim' in err

    def test_workspace_dir_expands_vars_and_is_checked_before_it_is_made(self, monkeypatch, tmp_path):
        root = tmp_path / 'sapphire'
        root.mkdir()
        monkeypatch.setenv('MYWS', str(tmp_path / 'outside'))
        ws, err = helpers._resolve_workspace({'workspace_dir': '$MYWS/claude'}, 'thing', mode='project', root=str(root))
        assert err is None and ws == str(tmp_path / 'outside' / 'claude' / 'thing') and Path(ws).is_dir()
        # a refused path is NOT created first (it used to litter the repo root)
        ws, err = helpers._resolve_workspace({'workspace_dir': str(root / 'inside')}, 'thing', mode='project', root=str(root))
        assert ws is None and 'SAFETY' in err and not (root / 'inside').exists()

    def test_the_inside_sapphire_check_is_path_aware_not_a_string_prefix(self, tmp_path):
        root = tmp_path / 'sapphire'
        root.mkdir()
        sibling = tmp_path / 'sapphire-work'
        sibling.mkdir()
        assert helpers._sanity_check(str(sibling), mode='project', root=str(root)) is None
        assert 'SAFETY' in (helpers._sanity_check(str(root / 'x'), mode='project', root=str(root)) or '')

    def test_the_report_is_the_final_message_not_the_narration(self):
        src = (ROOT / 'plugins' / 'claude-code' / 'agent_kind.py').read_text(encoding='utf-8')
        turn = src.split('async def _turn(')[1].split('# --- what the director does')[0]
        assert "final = (msg.result or '').strip() or body" in turn
        assert "self.report((final or '(no text)') + tail)" in turn
        src = (ROOT / 'plugins' / 'claude-code' / 'plugin.json').read_text(encoding='utf-8')
        assert 'READ access to user/logs' not in src
