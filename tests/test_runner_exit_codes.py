"""main.py, the runner: 43 and 44 are terminal, a crash after a healthy run is
not the next strike, the instance lock comes before anything else, and the
child is told the runner holds it. main.py is loaded as a module; nothing is
spawned (2026-10-16, tmp/single-instance-plan.md)."""
import importlib.util
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture
def runner(monkeypatch):
    spec = importlib.util.spec_from_file_location('sapphire_runner', ROOT / 'main.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    lines = []
    monkeypatch.setattr(mod, 'log', lambda msg, color=None: lines.append(msg))
    monkeypatch.setattr(mod.signal, 'signal', lambda *a: None)
    monkeypatch.setattr(mod.time, 'sleep', lambda s: None)
    monkeypatch.setattr(sys, 'argv', ['main.py'])
    monkeypatch.setitem(os.environ, 'SAPPHIRE_INSTANCE_LOCK', '')
    import core.single_instance as si
    monkeypatch.setattr(si, 'acquire_or_exit', lambda root, who, log=print: lines.append('LOCK') or object())
    mod.lines = lines
    return mod


def _runs(mod, codes, lived=0):
    def fake():
        mod.lines.append('RUN')
        mod._child_started = mod.time.monotonic() - lived
        return codes.pop(0)
    mod.run_sapphire = fake


def _main(mod):
    with pytest.raises(SystemExit) as ei:
        mod.main()
    return ei.value.code


def test_lock_first_then_the_child_knows(runner):
    _runs(runner, [0])
    assert _main(runner) == 0
    assert runner.lines.index('LOCK') < runner.lines.index('RUN')
    assert os.environ['SAPPHIRE_INSTANCE_LOCK'] == f"v2:{os.getpid()}"


def test_43_another_instance_is_terminal(runner):
    _runs(runner, [43, 0])
    assert _main(runner) == 43
    assert runner.lines.count('RUN') == 1
    assert any('Not restarting' in l and 'another Sapphire' in l for l in runner.lines)
    assert not any('Crashed' in l for l in runner.lines)


def test_44_configuration_is_terminal(runner):
    _runs(runner, [44, 0])
    assert _main(runner) == 44
    assert runner.lines.count('RUN') == 1
    assert any('configuration' in l for l in runner.lines)


def test_42_restarts_then_clean_exit(runner):
    _runs(runner, [42, 42, 0])
    assert _main(runner) == 0
    assert runner.lines.count('RUN') == 3


def test_five_quick_crashes_give_up(runner):
    _runs(runner, [1] * 5 + [0])
    assert _main(runner) == 1
    assert runner.lines.count('RUN') == 5
    assert any('giving up' in l for l in runner.lines)


def test_a_crash_after_a_healthy_run_is_not_the_next_strike(runner):
    _runs(runner, [1] * 9 + [0], lived=runner.HEALTHY_SECONDS + 5)
    assert _main(runner) == 0
    assert runner.lines.count('RUN') == 10
    assert all('(crash 1/' in l for l in runner.lines if 'Crashed' in l)


def test_exit_1_without_traceback_hint(runner):
    _runs(runner, [1, 0])
    _main(runner)
    assert any('ended from outside' in l for l in runner.lines if 'Crashed with exit code 1' in l)
    runner.lines.clear()
    _runs(runner, [3, 0])
    _main(runner)
    assert not any('ended from outside' in l for l in runner.lines)


def test_once_mode_passes_terminal_codes_through(runner, monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['main.py', '--once'])
    _runs(runner, [43])
    assert _main(runner) == 43
    assert 'LOCK' in runner.lines


class _Child:
    """A sapphire.py that will not end until killed."""
    def __init__(self, exits_after=None):
        self.waits, self.killed, self._left = 0, False, exits_after

    def wait(self, timeout=None):
        self.waits += 1
        if self.killed:
            return -9
        if self._left is not None and self.waits >= self._left:
            return 0
        import subprocess
        raise subprocess.TimeoutExpired('sapphire.py', timeout)

    def kill(self):
        self.killed = True


def test_wait_for_child_kills_after_the_stop_grace(runner, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(runner.time, 'monotonic', lambda: clock.__setitem__(0, clock[0] + 10) or clock[0])
    runner._runner_stopping = True
    child = _Child()
    assert runner._wait_for_child(child) == -9
    assert child.killed is True
    assert any('did not stop within' in l for l in runner.lines)


def test_wait_for_child_is_patient_while_nobody_asked_it_to_stop(runner, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(runner.time, 'monotonic', lambda: clock.__setitem__(0, clock[0] + 1000) or clock[0])
    runner._runner_stopping = False
    child = _Child(exits_after=5)
    assert runner._wait_for_child(child) == 0
    assert child.killed is False and child.waits == 5


def test_windows_ctrl_break_is_a_stop_signal(runner, monkeypatch):
    """The launcher's graceful stop is CTRL_BREAK to the runner's process group;
    the runner registers SIGBREAK wherever the platform has it."""
    registered = []
    monkeypatch.setattr(runner.signal, 'signal', lambda sig, h: registered.append(sig))
    monkeypatch.setattr(runner.signal, 'SIGBREAK', 21, raising=False)
    _runs(runner, [0])
    _main(runner)
    assert 21 in registered


def test_a_stop_during_the_update_window_means_no_boot(runner, monkeypatch):
    """Ctrl+C mid-pip used to kill pip, roll the update back, and boot anyway."""
    import core.updater, core.restore, core.integrity, core.gpu_install
    monkeypatch.setattr(core.updater, 'apply_pending_update', lambda: setattr(runner, '_runner_stopping', True))
    monkeypatch.setattr(core.restore, 'apply_pending_restore', lambda log=print: None)
    monkeypatch.setattr(core.integrity, 'log_boot_status', lambda: None)
    monkeypatch.setattr(core.gpu_install, 'apply_pending', lambda log=print: None)
    monkeypatch.setattr(runner.subprocess, 'Popen', lambda *a, **k: pytest.fail('must not spawn'))
    assert runner.run_sapphire() == 0
    assert any('not starting Sapphire' in l for l in runner.lines)


def test_the_gpu_install_runs_in_the_pre_start_window(runner, monkeypatch):
    order = []
    import core.updater, core.restore, core.integrity, core.gpu_install
    monkeypatch.setattr(core.updater, 'apply_pending_update', lambda: order.append('update'))
    monkeypatch.setattr(core.restore, 'apply_pending_restore', lambda log=print: order.append('restore'))
    monkeypatch.setattr(core.gpu_install, 'apply_pending', lambda log=print: order.append('gpu'))
    monkeypatch.setattr(core.integrity, 'log_boot_status', lambda: order.append('integrity'))

    class Done:
        returncode = 0
        def wait(self, timeout=None):
            order.append('spawn')
            return 0
    monkeypatch.setattr(runner.subprocess, 'Popen', lambda *a, **k: Done())
    assert runner.run_sapphire() == 0
    assert order == ['update', 'restore', 'gpu', 'integrity', 'spawn']
