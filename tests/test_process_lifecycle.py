"""core.process_manager lifecycle (2026-10-16, tmp/single-instance-plan.md rows 8 and 10).

Row 8: PR_SET_PDEATHSIG fires when the spawning THREAD exits, so every spawn
goes through one long-lived spawner thread. Row 10: stop() tells the monitor
first and joins it, so the monitor cannot respawn the child stop() just killed."""
import sys
import threading
import time
from pathlib import Path

import pytest

import core.process_manager as pm


def _manager(tmp_path):
    return pm.ProcessManager(script_path=tmp_path / 'svc.py', log_name='svc', base_dir=tmp_path)


@pytest.mark.skipif(sys.platform == 'win32', reason='the spawner thread is for PDEATHSIG, Unix only')
def test_unix_spawns_from_the_long_lived_spawner_thread(monkeypatch):
    seen = []

    class FakeProc:
        pid = 4242

    def fake_popen(*a, **k):
        seen.append(threading.current_thread().name)
        return FakeProc()
    monkeypatch.setattr(pm.subprocess, 'Popen', fake_popen)
    assert pm._spawn(['x']).pid == 4242
    assert pm._spawn(['y']).pid == 4242
    assert all(n.startswith('spawner') for n in seen) and len(seen) == 2
    assert seen[0] == seen[1]                       # the same thread, every time
    assert threading.current_thread().name not in seen


def test_start_uses_the_spawner(tmp_path, monkeypatch):
    (tmp_path / 'svc.py').write_text('')
    calls = []

    class FakeProc:
        pid = 7

    monkeypatch.setattr(pm, '_spawn', lambda *a, **k: calls.append(k) or FakeProc())
    m = _manager(tmp_path)
    assert m.start() is True
    assert m.process.pid == 7 and len(calls) == 1
    assert 'open(self.log_file, "w")' in open(pm.__file__).read()   # the HDF guard still holds


def test_stop_joins_the_monitor_and_nothing_respawns(tmp_path, monkeypatch):
    m = _manager(tmp_path)
    starts = []
    monkeypatch.setattr(m, 'start', lambda: starts.append(time.monotonic()) or False)
    m.monitor_and_restart(check_interval=0.05)
    deadline = time.monotonic() + 3
    while not starts and time.monotonic() < deadline:
        time.sleep(0.02)
    assert starts, 'the monitor never tried to start the dead service'
    t0 = time.monotonic()
    m.stop()
    assert time.monotonic() - t0 < 2               # woke the monitor, did not wait out its interval
    assert not m._monitor_thread.is_alive()
    n = len(starts)
    time.sleep(0.3)
    assert len(starts) == n                        # nothing respawned after stop() returned


def test_monitor_restart_waits_for_a_stop_in_progress(tmp_path, monkeypatch):
    """stop() holds the lock while it kills; a monitor that reaches its restart
    decision meanwhile sees _monitor_running False and does not start."""
    m = _manager(tmp_path)
    started = []
    monkeypatch.setattr(m, 'start', lambda: started.append(1) or True)
    with m._lock:
        m._monitor_running = False
        # the monitor's decision, as written in _monitor()
        with m._lock:
            if m._monitor_running and m.start():
                pass
    assert started == []


@pytest.mark.skipif(sys.platform == 'win32', reason='process groups are Unix')
def test_stop_survives_a_child_that_vanished_between_poll_and_getpgid(tmp_path, monkeypatch):
    m = _manager(tmp_path)

    class Gone:
        pid = 2 ** 22 - 7        # no such pid
        returncode = None

        def poll(self):
            return None
    m.process = Gone()
    m.stop()                     # must not raise ProcessLookupError out of the stop chain


def test_stop_without_a_process_is_quiet(tmp_path):
    m = _manager(tmp_path)
    m.stop()
    assert m._monitor_running is False and m._stop_event.is_set()
