"""core.single_instance: one Sapphire per user/, one owner for the web port.

2026-10-16: a second `python main.py` booted completely and idled headless on
the same user/ after uvicorn's bind died in a daemon thread. The lock is a
kernel lock (dies with the process); the web socket is bound and LISTENING in
the main thread before the banner."""
import os
import pathlib
import subprocess
import sys

import pytest

from core import single_instance as si

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_lock_file_lives_in_user_dir(tmp_path):
    assert si.InstanceLock(tmp_path).path == os.path.join(str(tmp_path), 'user', '.instance.lock')


def test_lock_is_exclusive_readable_and_releasable(tmp_path):
    a = si.InstanceLock(tmp_path)
    assert a.acquire() is True
    assert f"pid={os.getpid()}" in a.holder()
    b = si.InstanceLock(tmp_path)
    assert b.acquire() is False
    assert f"pid={os.getpid()}" in b.holder()      # the loser can read who holds it
    a.release()
    assert b.acquire() is True
    b.release()


def test_lock_holds_against_another_process(tmp_path):
    a = si.InstanceLock(tmp_path)
    assert a.acquire()
    code = ("import sys; from core.single_instance import InstanceLock; "
            "sys.exit(0 if InstanceLock(sys.argv[1]).acquire() else 43)")
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    run = lambda: subprocess.run([sys.executable, '-c', code, str(tmp_path)], cwd=str(ROOT), env=env, timeout=60).returncode
    assert run() == 43
    a.release()
    assert run() == 0


def test_a_dead_holder_leaves_no_stale_lock(tmp_path):
    """The file persists; the lock does not. A kill -9 must never block the next boot."""
    code = "import sys; from core.single_instance import InstanceLock; assert InstanceLock(sys.argv[1]).acquire()"
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    assert subprocess.run([sys.executable, '-c', code, str(tmp_path)], cwd=str(ROOT), env=env, timeout=60).returncode == 0
    assert os.path.exists(os.path.join(str(tmp_path), 'user', '.instance.lock'))
    a = si.InstanceLock(tmp_path)
    assert a.acquire() is True
    a.release()


def test_acquire_or_exit_names_the_holder(tmp_path):
    a = si.InstanceLock(tmp_path)
    assert a.acquire()
    said = []
    with pytest.raises(SystemExit) as ei:
        si.acquire_or_exit(tmp_path, who='The runner', log=said.append)
    assert ei.value.code == si.EXIT_ANOTHER_INSTANCE == 43
    assert 'already running' in said[0] and f"pid={os.getpid()}" in said[0] and 'The runner' in said[0]
    a.release()
    lock = si.acquire_or_exit(tmp_path, who='x', log=said.append)
    assert len(said) == 1
    lock.release()


def test_bind_listener_refuses_a_taken_port():
    first = si.bind_listener('127.0.0.1', 0)
    port = first.getsockname()[1]
    with pytest.raises(OSError) as ei:
        si.bind_listener('127.0.0.1', port)
    assert si.is_port_taken(ei.value)
    first.close()
    again = si.bind_listener('127.0.0.1', port)     # free again at once (no TIME_WAIT trip)
    again.close()


def test_bind_listener_bad_host_is_not_port_taken():
    with pytest.raises(OSError) as ei:
        si.bind_listener('203.0.113.9', 0)           # TEST-NET-3: never on this box
    assert not si.is_port_taken(ei.value)


def test_exit_codes_are_what_the_runner_expects():
    assert (si.EXIT_ANOTHER_INSTANCE, si.EXIT_CONFIG) == (43, 44)
    assert si.ENV_HELD == 'SAPPHIRE_INSTANCE_LOCK'
