"""core.gpu_status: the verdict behind Kokoro's and Whisper's quiet CPU fallback.

2026-10-16, Windows report: an RTX 5060 Ti and `torch.cuda.is_available()=False`.
The card was fine; the default Windows torch from PyPI has no CUDA. Classification
is tested with faked probes: no nvidia-smi and no child interpreter run here."""
import threading

from core import gpu_status as g

CARD = {'name': 'NVIDIA GeForce RTX 5060 Ti', 'compute_cap': 12.0, 'driver': '576.02'}
CU128 = {'torch': '2.11.0+cu128', 'cuda': '12.8', 'available': True,
         'archs': ['sm_70', 'sm_75', 'sm_80', 'sm_86', 'sm_90', 'sm_100', 'sm_120']}
CPU = {'torch': '2.10.0', 'cuda': None, 'available': False, 'archs': []}


def test_no_nvidia_is_silent():
    assert g.classify(None, CU128) == ('no_nvidia', '')


def test_ok_is_silent():
    assert g.classify(CARD, CU128) == ('ok', '')


def test_windows_cpu_torch_names_the_cause():
    state, msg = g.classify(CARD, CPU)
    assert state == 'cpu_torch'
    assert 'no CUDA' in msg and 'Windows' in msg and 'TROUBLESHOOTING' in msg
    assert CARD['name'] in msg


def test_managed_has_no_pip_hint():
    _, msg = g.classify(CARD, CPU, managed=True)
    assert 'TROUBLESHOOTING' not in msg and 'no CUDA' in msg


def test_blackwell_on_a_cu126_build():
    cu126 = dict(CU128, torch='2.14.1+cu126', cuda='12.6',
                 archs=['sm_50', 'sm_60', 'sm_70', 'sm_75', 'sm_80', 'sm_86', 'sm_90'])
    state, msg = g.classify(CARD, cu126)
    assert state == 'arch_unsupported' and 'sm_120' in msg and '12.6' in msg


def test_pascal_on_a_cu128_build():
    state, msg = g.classify({'name': 'GTX 1080', 'compute_cap': 6.1, 'driver': '550.0'}, CU128)
    assert state == 'arch_unsupported' and 'sm_61' in msg and 'cu126' in msg


def test_binary_compat_within_a_major():
    assert g._covers(['sm_80'], 8.6)            # an 8.0 cubin runs on 8.6
    assert not g._covers(['sm_86'], 8.0)        # not the other way
    assert not g._covers(['sm_90', 'sm_100'], 12.0)
    assert g._covers(['sm_90', 'compute_90'], 12.0)   # PTX the driver can JIT forward


def test_unknown_compute_cap_skips_the_arch_check():
    card = dict(CARD, compute_cap=None)
    assert g.classify(card, CU128) == ('ok', '')
    assert g.classify(card, dict(CU128, available=False))[0] == 'driver_old'


def test_driver_too_old_names_the_driver():
    state, msg = g.classify(CARD, dict(CU128, available=False))
    assert state == 'driver_old' and '576.02' in msg and 'Update the driver' in msg
    _, managed = g.classify(CARD, dict(CU128, available=False), managed=True)
    assert 'container' in managed


def test_broken_driver():
    state, msg = g.classify({'name': 'NVIDIA GPU', 'compute_cap': None, 'driver': None, 'broken': True}, CU128)
    assert state == 'driver_broken' and 'driver' in msg


def test_probe_error_and_no_torch_are_silent():
    assert g.classify(CARD, {'error': 'timeout'}) == ('unknown', '')
    assert g.classify(CARD, {'torch': None}) == ('no_torch', '')


def test_sm_names():
    assert g._sm(8.6) == 'sm_86' and g._sm(12.0) == 'sm_120' and g._sm(7.5) == 'sm_75'


class _R:
    def __init__(self, stdout, stderr='', returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


def test_nvidia_smi_parses_three_fields(monkeypatch):
    monkeypatch.setattr(g.shutil, 'which', lambda n: '/usr/bin/nvidia-smi')
    monkeypatch.setattr(g.subprocess, 'run', lambda *a, **k: _R('NVIDIA GeForce RTX 3090, 8.6, 570.86\n'))
    assert g._nvidia_smi() == {'name': 'NVIDIA GeForce RTX 3090', 'compute_cap': 8.6, 'driver': '570.86'}


def test_nvidia_smi_old_driver_without_compute_cap(monkeypatch):
    calls = []

    def run(cmd, **k):
        calls.append(cmd[1])
        if 'compute_cap' in cmd[1]:
            return _R('Field "compute_cap" is not a valid field to query.\n')
        return _R('Tesla P40, 470.10\n')
    monkeypatch.setattr(g.shutil, 'which', lambda n: 'nvidia-smi')
    monkeypatch.setattr(g.subprocess, 'run', run)
    assert g._nvidia_smi() == {'name': 'Tesla P40', 'compute_cap': None, 'driver': '470.10'}
    assert len(calls) == 2


def test_nvidia_smi_driver_broken(monkeypatch):
    monkeypatch.setattr(g.shutil, 'which', lambda n: 'nvidia-smi')
    monkeypatch.setattr(g.subprocess, 'run',
                        lambda *a, **k: _R("NVIDIA-SMI has failed because it couldn't communicate with the NVIDIA driver.\n"))
    assert g._nvidia_smi()['broken'] is True


def test_nvidia_smi_absent(monkeypatch):
    monkeypatch.setattr(g.shutil, 'which', lambda n: None)
    assert g._nvidia_smi() is None


def test_torch_probe_shapes(monkeypatch):
    monkeypatch.setattr(g.subprocess, 'run', lambda *a, **k: _R('{"torch": "2.10.0", "cuda": null, "available": false, "archs": []}\n'))
    assert g._torch_probe()['cuda'] is None
    monkeypatch.setattr(g.subprocess, 'run', lambda *a, **k: _R('', "ModuleNotFoundError: No module named 'torch'", 1))
    assert g._torch_probe() == {'torch': None}
    monkeypatch.setattr(g.subprocess, 'run', lambda *a, **k: _R('', 'Segmentation fault', 139))
    assert 'error' in g._torch_probe()


def test_status_caches_until_refresh_and_logs_once(monkeypatch, caplog):
    monkeypatch.setattr(g, '_nvidia_smi', lambda: CARD)
    monkeypatch.setattr(g, '_torch_probe', lambda: CPU)
    monkeypatch.setattr(g, '_managed', lambda: False)
    monkeypatch.setattr(g, '_done', threading.Event())
    with caplog.at_level('WARNING', logger='core.gpu_status'):
        out = g.status(refresh=True)
    assert out['state'] == 'cpu_torch' and out['torch'] == '2.10.0' and out['gpu'] == CARD
    assert g.peek()['state'] == 'cpu_torch'
    assert any('[GPU]' in r.message and 'no CUDA' in r.message for r in caplog.records)
    monkeypatch.setattr(g, '_torch_probe', lambda: CU128)
    assert g.status()['state'] == 'cpu_torch'          # cached
    assert g.status(refresh=True)['state'] == 'ok'     # re-probed


def test_route_returns_the_cached_verdict(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(g, 'peek', lambda: {'state': 'cpu_torch', 'message': 'x'})
    kicked = []
    monkeypatch.setattr(g, 'start_background', lambda refresh=False: kicked.append(refresh))
    r = c.get('/api/system/gpu')
    assert r.status_code == 200
    assert r.json()['state'] == 'cpu_torch' and r.json()['message'] == 'x' and 'install' in r.json()
    assert kicked == []
    assert c.get('/api/system/gpu?refresh=1').status_code == 200
    assert kicked == [True]
