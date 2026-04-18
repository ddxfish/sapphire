"""Regression guards for agent-system bugs shipped 2026-04-18.

Covered:
  R9  [REGRESSION_GUARD] AgentManager.shutdown() kills subprocesses, not just threads
  R10 [REGRESSION_GUARD] CodeWorker.cancel() during subprocess kills proc AND clears result

See tmp/coverage-test-plan.md for the full plan these are part of.
"""
import importlib.util
import sys
import time
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _load_claude_code_tools():
    """Load plugins/claude-code/tools/claude_code_tools.py by file path.
    The `claude-code` directory can't be imported via normal `import`
    syntax (hyphen in name), so we use importlib here."""
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
    """The claude_code_tools module, loaded by file path (dir has a hyphen)."""
    return _load_claude_code_tools()


# ─── R10: CodeWorker.cancel() kills proc + clears result ─────────────────────

def test_R10_code_worker_cancel_kills_proc_and_clears_result(fake_popen, cct):
    """[REGRESSION_GUARD] Cancelling a CodeWorker while its subprocess is running
    must (a) trigger the subprocess termination path and (b) null out self.result.

    Before today's fix: cancel() only flipped status; .result stayed populated
    and orphan `claude` process kept running after Sapphire shutdown.
    """
    CodeWorker = cct._create_code_worker()

    worker = CodeWorker(
        agent_id='t1',
        name='Forge',
        mission='noop',
        chat_name='trinity',
        project_name='test_project',
    )

    # Simulate that a subprocess is running
    import subprocess
    fake_proc = subprocess.Popen(['claude'])
    worker._proc = fake_proc

    # Also simulate some partial result (the pre-fix scenario: run() had started
    # producing output before cancel arrived)
    worker.result = 'partial output the agent had queued'
    worker._status = 'running'
    worker._start_time = time.time()

    # Act
    worker.cancel()

    # Assert: status flipped, result cleared, proc terminated
    assert worker.status == 'cancelled', f"expected cancelled, got {worker.status}"
    assert worker.result is None, "cancel must void stale result (pre-fix bug)"
    # fake_popen increments terminate OR killpg — either is a kill-signal observation
    killed = fake_popen.calls['terminate'] > 0 or fake_popen.calls['killpg'] > 0
    assert killed, (
        f"subprocess was not killed; calls: {fake_popen.calls}"
    )


# ─── R9: AgentManager.shutdown() kills subprocesses ──────────────────────────

def test_R9_agent_manager_shutdown_kills_subprocess_backed_workers(fake_popen, cct):
    """[REGRESSION_GUARD] AgentManager.shutdown() must terminate subprocess-backed
    workers, not just their Python threads. Orphan `claude` processes would
    otherwise keep running after Sapphire shuts down.

    The mechanism: shutdown → worker.cancel() → (overridden in CodeWorker) _kill_proc(self._proc).
    """
    from core.agents import AgentManager
    CodeWorker = cct._create_code_worker()

    mgr = AgentManager(max_concurrent=3)

    # Register a code-worker factory so we can spawn through the manager
    def factory(agent_id, name, mission, chat_name='', on_complete=None, **kwargs):
        w = CodeWorker(
            agent_id=agent_id, name=name, mission=mission,
            chat_name=chat_name, on_complete=on_complete,
            project_name=kwargs.get('project_name', 'test'),
        )
        # Simulate a running subprocess without actually running run()
        import subprocess
        w._proc = subprocess.Popen(['claude'])
        w._status = 'running'
        w._start_time = time.time()
        # Give it a dummy thread so shutdown's join() has something to wait on
        w._thread = threading.Thread(target=lambda: time.sleep(0.01), daemon=True)
        w._thread.start()
        return w

    mgr.register_type(
        'claude_code',
        display_name='Claude Code',
        factory=factory,
        spawn_args={},
        names=['Forge', 'Anvil', 'Crucible'],
    )

    # Manually build workers (not via .spawn() which calls run())
    w1 = factory('w1', 'Forge', 'mission1', chat_name='c')
    w2 = factory('w2', 'Anvil', 'mission2', chat_name='c')
    mgr._agents['w1'] = w1
    mgr._agents['w2'] = w2

    # Pre-shutdown: procs alive
    assert w1._proc.poll() is None, "proc should start alive"
    assert w2._proc.poll() is None

    # Act
    mgr.shutdown(timeout=2)

    # Assert: each worker's subprocess got a kill signal
    # Two procs × (terminate or killpg) — total kill signals should be >= 2
    kills = fake_popen.calls['terminate'] + fake_popen.calls['killpg']
    assert kills >= 2, (
        f"expected at least 2 kill signals (one per worker); "
        f"got {kills}: {fake_popen.calls}"
    )

    # Manager registry cleared
    assert len(mgr._agents) == 0, "shutdown should clear registry"

    # Workers marked cancelled + result cleared
    assert w1.status == 'cancelled'
    assert w2.status == 'cancelled'
    assert w1.result is None
    assert w2.result is None


# ─── Secondary: the kill chain itself is not a no-op ─────────────────────────

def test_kill_proc_helper_sends_signal_when_alive(fake_popen, cct):
    """Sanity check: _kill_proc is not a silent no-op when proc.poll() is None.
    This pins the behavior of the helper the other two tests rely on."""
    import subprocess
    proc = subprocess.Popen(['claude'])
    assert proc.poll() is None  # alive
    cct._kill_proc(proc)
    kills = fake_popen.calls['terminate'] + fake_popen.calls['killpg']
    assert kills >= 1, f"_kill_proc did not signal; calls: {fake_popen.calls}"


def test_kill_proc_helper_noop_when_already_dead(fake_popen, cct):
    """_kill_proc on a dead proc must NOT double-signal. Otherwise shutdown
    could terminate the wrong process if PIDs are recycled."""
    import subprocess
    proc = subprocess.Popen(['claude'])
    proc.communicate()  # completes, returncode is set
    assert proc.poll() is not None  # dead
    before = dict(fake_popen.calls)
    cct._kill_proc(proc)
    after = dict(fake_popen.calls)
    assert before == after, f"_kill_proc signaled a dead proc: {before} → {after}"
