"""core.process_manager.kill_process_on_port: kill the listener on a port only
when it is ours, never a stranger, never a client, never ourselves.

Before 2026-10-16 it matched by port alone: localized Windows netstat never
matched, a missing fuser fell to `lsof -ti :port` which lists CLIENTS, so
Sapphire could SIGTERM herself (exit 0: the runner stopped for good)."""
import os
import sys
import types

import core.process_manager as pm


class _Proc:
    def __init__(self, pid, cmd, name='python'):
        self.pid, self._cmd, self._name, self.ended = pid, cmd, name, []

    def cmdline(self):
        return self._cmd

    def name(self):
        return self._name

    def terminate(self):
        self.ended.append('terminate')

    def wait(self, timeout=None):
        self.ended.append('wait')

    def kill(self):
        self.ended.append('kill')


def _conn(port, pid, status='LISTEN'):
    return types.SimpleNamespace(status=status, laddr=types.SimpleNamespace(port=port), pid=pid)


def _psutil(conns, procs):
    return types.SimpleNamespace(
        CONN_LISTEN='LISTEN', TimeoutExpired=type('TimeoutExpired', (Exception,), {}),
        net_connections=lambda kind='tcp': conns, Process=lambda pid: procs[pid])


TTS = '/srv/sapphire/core/tts/tts_server.py'


def test_kills_our_listeners_never_a_client_a_stranger_or_another_port(monkeypatch):
    ours4 = _Proc(222, ['python', TTS])
    ours6 = _Proc(333, ['python', TTS])
    stranger = _Proc(555, ['/usr/bin/otherapp', '--port', '5012'], 'otherapp')
    client = _Proc(444, ['python', '/srv/sapphire/sapphire.py'])
    other_port = _Proc(111, ['python', TTS])
    procs = {p.pid: p for p in (ours4, ours6, stranger, client, other_port)}
    conns = [_conn(50120, 111), _conn(5012, 222), _conn(5012, 333), _conn(5012, 444, 'ESTABLISHED'), _conn(5012, 555)]
    monkeypatch.setitem(sys.modules, 'psutil', _psutil(conns, procs))
    assert pm.kill_process_on_port(5012, owner=TTS) is True
    assert ours4.ended == ['terminate', 'wait'] and ours6.ended == ['terminate', 'wait']
    assert stranger.ended == [] and client.ended == [] and other_port.ended == []


def test_a_stranger_alone_means_nothing_killed(monkeypatch):
    stranger = _Proc(555, ['/usr/bin/otherapp'], 'otherapp')
    monkeypatch.setitem(sys.modules, 'psutil', _psutil([_conn(5012, 555)], {555: stranger}))
    assert pm.kill_process_on_port(5012, owner=TTS) is False
    assert stranger.ended == []


def test_never_itself_or_its_parent(monkeypatch):
    me = _Proc(os.getpid(), ['python', '/srv/sapphire/sapphire.py'])
    dad = _Proc(os.getppid(), ['python', '/srv/sapphire/main.py'])
    monkeypatch.setitem(sys.modules, 'psutil',
                        _psutil([_conn(8073, me.pid), _conn(8073, dad.pid)], {me.pid: me, dad.pid: dad}))
    assert pm.kill_process_on_port(8073, owner='/srv/sapphire') is False
    assert me.ended == [] and dad.ended == []


def test_unidentifiable_is_left_alone(monkeypatch):
    ps = _psutil([_conn(5012, 999)], {})

    def denied(pid):
        raise PermissionError('access denied')
    ps.Process = denied
    monkeypatch.setitem(sys.modules, 'psutil', ps)
    assert pm.kill_process_on_port(5012, owner=TTS) is False


def test_without_psutil_nothing_is_killed(monkeypatch):
    monkeypatch.setitem(sys.modules, 'psutil', None)      # `import psutil` raises
    assert pm.kill_process_on_port(5012, owner=TTS) is False


def test_kill_follows_a_terminate_that_does_not_take(monkeypatch):
    ps = _psutil([_conn(5012, 7)], {})

    class Stubborn(_Proc):
        def wait(self, timeout=None):
            self.ended.append('wait')
            raise ps.TimeoutExpired()
    p = Stubborn(7, ['python', TTS])
    ps.Process = lambda pid: p
    monkeypatch.setitem(sys.modules, 'psutil', ps)
    assert pm.kill_process_on_port(5012, owner=TTS) is True
    assert p.ended == ['terminate', 'wait', 'kill']


def test_default_owner_is_the_sapphire_root(monkeypatch):
    root = str(pm.Path(pm.__file__).absolute().parent.parent)
    ours = _Proc(9, ['python', root + '/plugins/x/service.py'])
    stranger = _Proc(10, ['python', '/elsewhere/service.py'])
    monkeypatch.setitem(sys.modules, 'psutil', _psutil([_conn(7000, 9), _conn(7000, 10)], {9: ours, 10: stranger}))
    assert pm.kill_process_on_port(7000) is True
    assert ours.ended and not stranger.ended
