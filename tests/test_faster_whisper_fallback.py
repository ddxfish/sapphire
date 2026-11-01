"""Whisper that loaded on CUDA and then can't transcribe (on Windows a missing
cuBLAS only shows at the first transcribe) reloads on the CPU and tries again."""
import sys
from types import SimpleNamespace
from threading import Lock

import pytest

from core.stt.providers import faster_whisper as fw


class _Model:
    def __init__(self, error=None):
        self.error, self.calls = error, 0

    def transcribe(self, path, **params):
        self.calls += 1
        if self.error:
            raise RuntimeError(self.error)
        return iter([SimpleNamespace(text='hi', no_speech_prob=0.0)]), None


def _provider(model, on_cuda=True):
    p = fw.FasterWhisperProvider.__new__(fw.FasterWhisperProvider)
    p.model, p._lock, p._model_size, p._on_cuda = model, Lock(), 'tiny', on_cuda
    return p


def test_cuda_failure_reloads_on_cpu_and_retries(monkeypatch):
    cpu, loads = _Model(), []
    monkeypatch.setattr(fw, '_load_whisper', lambda cls, size, **k: loads.append((size, k['device'], k['compute_type'])) or cpu)
    monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(WhisperModel=object))
    p = _provider(_Model('Library cublas64_12.dll is not found or cannot be loaded'))
    assert [s.text for s in p.transcribe_segments('a.wav')] == ['hi']
    assert loads == [('tiny', 'cpu', 'int8')] and p.model is cpu and p._on_cuda is False
    assert [s.text for s in p.transcribe_segments('a.wav')] == ['hi'] and len(loads) == 1


def test_other_errors_do_not_reload(monkeypatch):
    monkeypatch.setattr(fw, '_load_whisper', lambda *a, **k: pytest.fail('no reload'))
    with pytest.raises(RuntimeError):
        _provider(_Model('bad audio')).transcribe_segments('a.wav')
    with pytest.raises(RuntimeError):
        _provider(_Model('CUDA failed'), on_cuda=False).transcribe_segments('a.wav')


def test_the_chosen_card_reaches_the_model(monkeypatch):
    """CTranslate2 ignores torch's current device: only device_index moves it."""
    fake_cuda = SimpleNamespace(is_available=lambda: True, device_count=lambda: 2, set_device=lambda i: None,
                                get_device_name=lambda i: f'card {i}')
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(cuda=fake_cuda, version=SimpleNamespace(cuda='12.8')))
    monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(WhisperModel=object))
    monkeypatch.setattr(fw.config, 'FASTER_WHISPER_DEVICE', 'cuda', raising=False)
    monkeypatch.setattr(fw.config, 'FASTER_WHISPER_CUDA_DEVICE', 1, raising=False)
    seen = {}
    monkeypatch.setattr(fw, '_load_whisper', lambda cls, size, **k: seen.update(k) or _Model())
    p = fw.FasterWhisperProvider('tiny', 'en')
    assert seen['device_index'] == 1 and seen['device'] == 'cuda' and p._on_cuda is True
