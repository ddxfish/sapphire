"""core.gpu_install: the opt-in GPU torch install, queued by a button and run
by main.py before sapphire.py exists (a loaded torch DLL cannot be replaced
on Windows). 2026-10-16, tmp/single-instance-plan.md Wave C step 3."""
import pytest

from core import gpu_install as gi


def _card(cap, state='cpu_torch'):
    return {'state': state, 'gpu': {'name': 'RTX', 'compute_cap': cap, 'driver': '1'}}


def test_index_rule():
    assert gi.index_for(12.0) == 'cu128' and gi.index_for(7.0) == 'cu128'
    assert gi.index_for(6.1) == 'cu126' and gi.index_for(5.2) == 'cu126'
    assert gi.index_for(None) == 'cu128'


def test_plan_is_windows_only_unless_forced(monkeypatch):
    monkeypatch.delenv('SAPPHIRE_GPU_INDEX', raising=False)
    monkeypatch.setattr(gi.sys, 'platform', 'linux')
    assert gi.plan(_card(12.0)) is None
    monkeypatch.setattr(gi.sys, 'platform', 'win32')
    assert gi.plan(_card(12.0))['index'] == 'cu128'
    assert gi.plan(_card(6.1))['index'] == 'cu126'
    assert gi.plan(_card(12.0, 'arch_unsupported'))['index'] == 'cu128'
    assert gi.plan({'state': 'ok'}) is None
    assert gi.plan({'state': 'driver_old', 'gpu': {'compute_cap': 12.0}}) is None
    assert gi.plan(_card(12.0), managed=True) is None
    assert gi.plan(None) is None


def test_forced_index_for_a_box_without_a_card(monkeypatch):
    monkeypatch.setattr(gi.sys, 'platform', 'linux')
    monkeypatch.setenv('SAPPHIRE_GPU_INDEX', 'cu126')
    assert gi.plan({'state': 'no_nvidia'})['index'] == 'cu126'
    monkeypatch.setenv('SAPPHIRE_GPU_INDEX', 'cu999')
    assert gi.plan({'state': 'no_nvidia'}) is None


def test_pip_commands_shape(monkeypatch):
    monkeypatch.delenv('SAPPHIRE_GPU_INSTALL_DRY_RUN', raising=False)
    one, two = gi.pip_commands('cu128', python='py')
    assert one[:4] == ['py', '-m', 'pip', 'install']
    assert one[one.index('--index-url') + 1] == 'https://download.pytorch.org/whl/cu128'
    assert '--force-reinstall' in one and '--no-deps' in one and one[-1] == 'torch>=2.7.0'
    assert '--force-reinstall' not in two and '--no-deps' not in two and two[-1] == 'torch>=2.7.0'
    assert '--dry-run' not in one
    monkeypatch.setenv('SAPPHIRE_GPU_INSTALL_DRY_RUN', '1')
    assert all('--dry-run' in c for c in gi.pip_commands('cu126'))


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.setattr(gi, 'REQUEST', tmp_path / 'pending.json')
    monkeypatch.setattr(gi, 'RESULT', tmp_path / 'result.json')
    monkeypatch.setattr(gi, 'LOG', tmp_path / 'logs' / 'gpu-install.log')
    return tmp_path


class _Proc:
    def __init__(self, lines, code):
        self.stdout, self._code = iter(lines), code

    def wait(self):
        return self._code

    def kill(self):
        pass


def test_apply_runs_both_steps_and_records(files, monkeypatch):
    ran = []
    monkeypatch.setattr(gi.subprocess, 'Popen',
                        lambda cmd, **k: ran.append(cmd) or _Proc(['Collecting torch\n', 'Successfully installed torch-2.11.0+cu128\n'], 0))
    gi.request('cu128')
    assert gi.pending() is True
    said = []
    assert gi.apply_pending(log=said.append) is True
    assert len(ran) == 2 and gi.pending() is False
    assert gi.last_result()['ok'] is True and gi.last_result()['index'] == 'cu128'
    assert any('Successfully installed' in s for s in said)
    assert 'Successfully installed' in gi.LOG.read_text(encoding='utf-8')


def test_apply_failure_records_and_never_loops(files, monkeypatch):
    monkeypatch.setattr(gi.subprocess, 'Popen', lambda cmd, **k: _Proc(['ERROR: No matching distribution\n'], 1))
    gi.request('cu126')
    assert gi.apply_pending(log=lambda m: None) is False
    assert gi.pending() is False                 # a failing install is not retried every boot
    r = gi.last_result()
    assert r['ok'] is False and 'pip exited 1' in r['message']


def test_apply_without_a_request_is_a_noop(files, monkeypatch):
    monkeypatch.setattr(gi.subprocess, 'Popen', lambda *a, **k: pytest.fail('pip must not run'))
    assert gi.apply_pending() is None


def test_bad_request_is_dropped(files):
    gi.REQUEST.write_text('{"index": "cu999"}', encoding='utf-8')
    assert gi.apply_pending(log=lambda m: None) is False and gi.pending() is False
    with pytest.raises(ValueError):
        gi.request('cu999')


def test_routes(client, files, monkeypatch):
    from core import gpu_status
    import core.api_fastapi as af
    c, csrf = client
    monkeypatch.setattr(gpu_status, 'peek', lambda: {'state': 'cpu_torch', 'message': 'x',
                                                      'gpu': {'compute_cap': 12.0, 'name': 'RTX'}})
    monkeypatch.setattr(gi.sys, 'platform', 'win32')
    monkeypatch.delenv('SAPPHIRE_GPU_INDEX', raising=False)
    body = c.get('/api/system/gpu').json()
    assert body['state'] == 'cpu_torch' and body['install']['index'] == 'cu128' and body['pending'] is False
    restarted = []
    monkeypatch.setattr(af, 'get_restart_callback', lambda: (lambda: restarted.append(1)))
    r = c.post('/api/system/gpu/install', headers={'X-CSRF-Token': csrf})
    assert r.status_code == 200 and r.json()['index'] == 'cu128'
    assert gi.pending() is True and restarted == [1]
    assert c.get('/api/system/gpu').json()['pending'] is True
    gi.REQUEST.unlink()
    monkeypatch.setattr(gpu_status, 'peek', lambda: {'state': 'ok', 'message': ''})
    assert c.post('/api/system/gpu/install', headers={'X-CSRF-Token': csrf}).status_code == 400
    assert gi.pending() is False
