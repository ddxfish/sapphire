"""The client of Whisper's worker: what a crash, a hang and a demotion do. The
worker is a fake here; tests/test_whisper_worker.py covers the worker's own rules."""
import json
import sys
from threading import Lock
from types import SimpleNamespace

import pytest

from core.stt.providers import faster_whisper as fw
from core.stt.providers.base import STTFailed


def _client(device='cuda', **state):
    p = fw.FasterWhisperProvider.__new__(fw.FasterWhisperProvider)
    p._lock, p._model_size, p._proc, p._inbox = Lock(), 'tiny', object(), None
    p._crashes, p._cpu_only, p._told, p._closed = 0, False, False, False
    p.device, p.compute, p.why, p.kind = device, 'int8', None, None
    p.__dict__.update(state)
    return p


@pytest.fixture
def rows(monkeypatch):
    seen = []
    from core import notify
    monkeypatch.setattr(notify, 'notify', lambda title, level='info', body='', **k: seen.append((title, level, body, k.get('key'))))
    monkeypatch.setattr(notify, 'forget', lambda key: seen.append(('forget', key)))
    return seen


def _wire(monkeypatch, p, answers, starts=None):
    """Answers in order: a dict is a message, an Exception is raised."""
    answers = iter(answers)
    monkeypatch.setattr(p, '_send', lambda msg: None)
    monkeypatch.setattr(p, '_kill', lambda: setattr(p, '_proc', None))

    def _next(wait):
        a = next(answers)
        if isinstance(a, Exception):
            raise a
        return a

    def _start():
        (starts if starts is not None else []).append(p._cpu_only)
        p._proc = SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(p, '_next', _next)
    monkeypatch.setattr(p, '_start', _start)
    p._proc = SimpleNamespace(poll=lambda: None)


def _ok(device='cuda', why=None, kind=None):
    return {'ok': True, 'segments': [{'text': 'hi', 'no_speech_prob': 0.0, 'avg_logprob': 0.0}],
            'device': device, 'compute': 'int8', 'why': why, 'kind': kind}


def test_a_crash_restarts_the_worker_and_tries_again(monkeypatch, rows):
    p, starts = _client(), []
    _wire(monkeypatch, p, [fw._Dead('exit code -11'), _ok()], starts)
    assert [s.text for s in p.transcribe_segments('a.wav')] == ['hi']
    assert starts == [False] and p._crashes == 0 and rows == []


def test_two_crashes_on_the_card_move_to_the_cpu_and_say_so(monkeypatch, rows):
    p, starts = _client(), []
    _wire(monkeypatch, p, [fw._Dead('exit code -11'), fw._Dead('exit code -11')], starts)
    with pytest.raises(STTFailed):
        p.transcribe_segments('a.wav')
    assert p._cpu_only is True and p.device == 'cpu'
    assert rows[0][0] == 'Whisper is running on the CPU'
    assert rows[-1][0] == 'Speech-to-text failed' and rows[-1][3] == 'stt-down'


def test_a_stuck_worker_is_one_lost_attempt_not_two_waits(monkeypatch, rows):
    p = _client()
    _wire(monkeypatch, p, [fw._Dead('no answer in 600 s')])       # a second _next would raise StopIteration
    with pytest.raises(STTFailed):
        p.transcribe_segments('a.wav')


def test_engine_error_is_failure_not_silence(monkeypatch, rows):
    p = _client()
    _wire(monkeypatch, p, [{'ok': False, 'error': 'bad language', 'device': 'cuda'}])
    with pytest.raises(STTFailed, match='bad language'):
        p.transcribe_segments('a.wav')
    assert rows[-1][3] == 'stt-down'


def test_failure_raises_through_transcribe_file_and_silence_stays_none(monkeypatch, rows, tmp_path):
    import numpy as np
    import soundfile as sf
    loud, quiet = tmp_path / 'loud.wav', tmp_path / 'quiet.wav'
    sf.write(loud, 0.5 * np.ones(1600), 16000)
    sf.write(quiet, np.zeros(1600), 16000)
    p = _client()
    _wire(monkeypatch, p, [{'ok': False, 'error': 'no engine', 'device': 'cuda'}])
    with pytest.raises(STTFailed):
        p.transcribe_file(str(loud))
    assert p.transcribe_file(str(quiet)) is None
    assert p.transcribe_file(str(tmp_path / 'not-there.wav')) is None


def test_demotion_mid_session_posts_the_row(monkeypatch, rows):
    p = _client()
    _wire(monkeypatch, p, [_ok('cpu', 'cuBLAS failed', 'library')])
    monkeypatch.setattr(p, 'gpu_offer', lambda: True)
    p.transcribe_segments('a.wav')
    assert p.device == 'cpu' and rows[0][0] == 'Whisper is running on the CPU'
    assert 'Install Whisper GPU support' in rows[0][2] and rows[0][3] == 'stt-cpu'


def test_a_closed_client_never_brings_a_worker_back(monkeypatch, rows):
    p = _client()
    starts = []
    _wire(monkeypatch, p, [], starts)
    p.close()
    with pytest.raises(STTFailed):
        p._ask('a.wav', {})
    assert not starts


def test_a_row_from_before_a_restart_is_taken_back(rows):
    _client()._tell()
    assert rows == [('forget', 'stt-cpu')]


def test_cpu_by_choice_or_a_cpu_torch_posts_nothing(rows):
    _client('cpu')._tell()                                     # device set to CPU: no reason recorded
    _client('cpu', why='this torch has no CUDA', kind='torch')._tell()      # gpu_status has its own row
    assert all(r[0] == 'forget' for r in rows)


def test_back_on_the_card_updates_the_same_row(rows):
    p = _client('cuda', _told=True)
    p._tell()
    assert rows[0][0] == 'Whisper is back on the GPU' and rows[0][3] == 'stt-cpu'


@pytest.mark.parametrize('state,platform_,machine,installed,managed,want', [
    (dict(device='cpu', kind='library'), 'linux', 'x86_64', False, False, True),
    (dict(device='cpu', kind='library'), 'linux', 'x86_64', True, False, False),
    (dict(device='cpu', kind='library'), 'linux', 'x86_64', False, True, False),
    (dict(device='cpu', kind='library'), 'linux', 'aarch64', False, False, False),
    (dict(device='cpu', kind='library'), 'win32', 'AMD64', False, False, False),
    (dict(device='cpu', kind='other'), 'linux', 'x86_64', False, False, False),
    (dict(device='cuda', kind=None), 'linux', 'x86_64', False, False, False),
])
def test_the_library_is_offered_only_where_it_helps(monkeypatch, state, platform_, machine, installed, managed, want):
    from core import extras
    monkeypatch.setattr(fw.sys, 'platform', platform_)
    monkeypatch.setattr(fw.platform, 'machine', lambda: machine)
    monkeypatch.setattr(extras, 'installed', lambda name: installed)
    monkeypatch.setattr(extras, '_managed', lambda: managed)
    p = _client()
    p.__dict__.update(state)
    assert p.gpu_offer() is want


def test_the_library_path_reaches_only_the_worker(monkeypatch, tmp_path):
    lib = tmp_path / 'nvidia' / 'cublas' / 'lib'
    lib.mkdir(parents=True)
    (lib / 'libcublas.so.12').touch()
    monkeypatch.setattr(fw.sys, 'platform', 'linux')
    monkeypatch.setattr(fw.sys, 'path', [str(tmp_path)])
    monkeypatch.setenv('LD_LIBRARY_PATH', '/somewhere')
    assert fw._worker_env()['LD_LIBRARY_PATH'] == f"{lib}{fw.os.pathsep}/somewhere"
    assert fw.os.environ['LD_LIBRARY_PATH'] == '/somewhere'


def test_settings_reach_the_worker(monkeypatch):
    for k, v in (('FASTER_WHISPER_DEVICE', 'cuda'), ('FASTER_WHISPER_CUDA_DEVICE', 1), ('FASTER_WHISPER_COMPUTE_TYPE', 'float16'),
                 ('FASTER_WHISPER_NUM_WORKERS', 4)):
        monkeypatch.setattr(fw.config, k, v, raising=False)
    p = _client()
    assert p._settings() == {'size': 'tiny', 'device': 'cuda', 'compute_type': 'float16', 'num_workers': 4, 'cuda_device': 1}
    p._cpu_only = True
    assert p._settings()['device'] == 'cpu'
    assert json.dumps(p._settings())
