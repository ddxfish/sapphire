"""core.gpu_status: the verdict behind Kokoro's and Whisper's quiet CPU fallback.

2026-10-16, Windows report: an RTX 5060 Ti and `torch.cuda.is_available()=False`.
The card was fine; the default Windows torch from PyPI has no CUDA. Classification
is tested with faked probes: no nvidia-smi and no child interpreter run here."""
import threading

from core import gpu_status as g

CARD = {'name': 'NVIDIA GeForce RTX 5060 Ti', 'compute_cap': 12.0, 'driver': '576.02'}
CU128 = {'torch': '2.11.0+cu128', 'cuda': '12.8', 'available': True,
         'archs': ['sm_75', 'sm_80', 'sm_86', 'sm_90', 'sm_100', 'sm_120']}
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


def test_nvidia_smi_picks_the_newest_card_in_any_slot(monkeypatch):
    monkeypatch.setattr(g.shutil, 'which', lambda n: 'nvidia-smi')
    monkeypatch.setattr(g.subprocess, 'run',
                        lambda *a, **k: _R('GT 1030, 6.1, 570.86\nRTX 5090, 12.0, 570.86\n'))
    assert g._nvidia_smi()['name'] == 'RTX 5090'


def test_volta_is_not_in_the_cu128_wheel():
    state, msg = g.classify({'name': 'Titan V', 'compute_cap': 7.0, 'driver': '1'}, CU128)
    assert state == 'arch_unsupported' and 'cu126' in msg


def test_a_card_no_wheel_carries_gets_no_button():
    from core import gpu_install
    kepler = {'name': 'GT 710', 'compute_cap': 3.5, 'driver': '474'}
    state, msg = g.classify(kepler, CPU)
    assert state == 'card_too_old' and 'too old' in msg and 'TROUBLESHOOTING' not in msg
    assert gpu_install.plan({'state': state, 'gpu': kepler}) is None
    assert gpu_install.plan({'state': 'torch_broken', 'gpu': kepler}) is None


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


def test_idle_card_posts_one_warning_row_per_torch_build(monkeypatch):
    from core import notify, gpu_install
    monkeypatch.setattr(g, '_nvidia_smi', lambda: CARD)
    monkeypatch.setattr(g, '_torch_probe', lambda: CPU)
    monkeypatch.setattr(g, '_managed', lambda: False)
    monkeypatch.setattr(g, '_voice_tab', lambda state: 'tts')
    monkeypatch.setattr(g, '_done', threading.Event())
    monkeypatch.setattr(gpu_install.sys, 'platform', 'win32')
    monkeypatch.delenv('SAPPHIRE_GPU_INDEX', raising=False)
    gpu_rows = lambda: [r for r in notify.rows() if (r.get('key') or '').startswith('gpu:')]
    g.status(refresh=True)
    g.status(refresh=True)
    rows = gpu_rows()
    assert len(rows) == 1
    r = rows[0]
    assert r['level'] == 'warning' and r['go']['tab'] == 'tts' and CARD['name'] in r['title']
    assert 'no CUDA' in r['body'] and 'Install GPU torch and restart' in r['body']
    monkeypatch.setattr(gpu_install.sys, 'platform', 'linux')
    monkeypatch.setattr(g, '_torch_probe', lambda: dict(CU128, available=False))
    g.status(refresh=True)                      # a driver case: its own row, no button line
    rows = gpu_rows()
    assert len(rows) == 2
    assert 'Install GPU torch' not in next(x for x in rows if 'driver' in x['body'])['body']
    monkeypatch.setattr(g, '_torch_probe', lambda: CU128)
    g.status(refresh=True)                      # all well: nothing new
    assert len(gpu_rows()) == 2


def _boot(monkeypatch, torch, tab='tts', last=None, platform='win32'):
    """One boot's row, with the probes faked."""
    from core import notify, gpu_install
    monkeypatch.setattr(g, '_nvidia_smi', lambda: CARD)
    monkeypatch.setattr(g, '_torch_probe', lambda: torch)
    monkeypatch.setattr(g, '_managed', lambda: False)
    monkeypatch.setattr(g, '_voice_tab', lambda state: tab)
    monkeypatch.setattr(g, '_done', threading.Event())
    monkeypatch.setattr(gpu_install.sys, 'platform', platform)
    monkeypatch.setattr(gpu_install, 'last_result', lambda: last)
    monkeypatch.delenv('SAPPHIRE_GPU_INDEX', raising=False)
    g.status(refresh=False)
    return [r for r in notify.rows() if (r.get('key') or '').startswith('gpu:')]


def test_every_boot_brings_the_row_back_unread(monkeypatch):
    from core import notify
    rows = _boot(monkeypatch, CPU)
    assert len(rows) == 1 and rows[0]['read'] is False and 'to CPU to stop' in rows[0]['body']
    notify.mark_read(rows[0]['id'])
    rows = _boot(monkeypatch, CPU)
    assert len(rows) == 1 and rows[0]['read'] is False


def test_a_refresh_does_not_bring_it_back(monkeypatch):
    from core import notify
    rows = _boot(monkeypatch, CPU)
    notify.mark_read(rows[0]['id'])
    g.status(refresh=True)
    assert notify.get(rows[0]['id'])['read'] is True


def test_no_row_when_no_local_engine_wants_the_card(monkeypatch):
    assert _boot(monkeypatch, CPU, tab=None) == []


def test_voice_tab_rules(monkeypatch):
    import config
    def setup(**kw):
        for k in ('TTS_PROVIDER', 'STT_PROVIDER', 'KOKORO_DEVICE', 'FASTER_WHISPER_DEVICE'):
            monkeypatch.setattr(config, k, kw.get(k, 'none' if 'PROVIDER' in k else 'cuda'), raising=False)
    setup(TTS_PROVIDER='kokoro')
    assert g._voice_tab('cpu_torch') == 'tts'
    setup(TTS_PROVIDER='kokoro', KOKORO_DEVICE='cpu')
    assert g._voice_tab('cpu_torch') is None
    assert g._voice_tab('torch_broken') == 'tts'
    setup(STT_PROVIDER='faster_whisper')
    assert g._voice_tab('cpu_torch') == 'stt'
    setup(TTS_PROVIDER='elevenlabs')
    assert g._voice_tab('cpu_torch') is None


def test_a_last_failed_install_is_in_the_row(monkeypatch):
    rows = _boot(monkeypatch, CPU, last={'ok': False, 'message': 'pip exited 1'})
    assert 'Last install failed: pip exited 1' in rows[0]['body']


def test_probe_tells_broken_torch_from_no_verdict(monkeypatch):
    tb = 'Traceback (most recent call last):\nOSError: [WinError 126] fbgemm.dll'
    monkeypatch.setattr(g.subprocess, 'run', lambda *a, **k: _R('', tb, 1))
    assert g._torch_probe()['broken'] is True
    monkeypatch.setattr(g.subprocess, 'run', lambda *a, **k: _R('', "Traceback\nNo module named 'torch._C'", 1))
    assert g._torch_probe()['broken'] is True
    monkeypatch.setattr(g.subprocess, 'run', lambda *a, **k: _R('', 'Segmentation fault', 139))
    assert g._torch_probe()['broken'] is False


def test_broken_torch_is_a_red_row_with_the_repair_button(monkeypatch):
    from core import gpu_install
    t = {'error': 'Traceback\nOSError: [WinError 126] fbgemm.dll', 'broken': True}
    assert g.classify(CARD, t)[0] == 'torch_broken' and 'WinError 126' in g.classify(CARD, t)[1]
    r = _boot(monkeypatch, t)[0]
    assert r['level'] == 'error' and 'will not load' in r['title'] and 'Install GPU torch' in r['body']
    assert 'to CPU to stop' not in r['body']
    assert gpu_install.plan(g.peek())['index'] == 'cu128'


def test_broken_torch_with_no_button_gives_the_command(monkeypatch):
    r = _boot(monkeypatch, {'error': 'Traceback\nOSError: x', 'broken': True}, platform='linux')[0]
    assert 'pip install --force-reinstall torch' in r['body'] and 'button' not in r['body']


def test_an_install_that_took_torch_away_is_broken_not_absent(monkeypatch):
    rows = _boot(monkeypatch, {'torch': None}, last={'ok': False, 'message': 'interrupted'})
    assert g.peek()['state'] == 'torch_broken' and rows[0]['level'] == 'error'


def test_cuda_failed_is_only_the_cards_fault():
    for words in ('CUDA out of memory', 'Library cublas64_12.dll is not found', 'cudnn failed',
                  'no kernel image is available for execution on the device'):
        assert g.cuda_failed(RuntimeError(words))
    assert not g.cuda_failed(RuntimeError('bad phoneme')) and not g.cuda_failed(ValueError(''))
