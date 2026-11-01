"""The worker's own rules: never torch on Linux, a real pass before trusting the
card, CPU when the card can't, words in the protocol only."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parent.parent
WORKER = ROOT / 'core' / 'stt' / 'whisper_worker.py'

FAKES = """
import sys, types, json
fail = json.loads(%r)
class WhisperModel:
    def __init__(self, size, device='cpu', compute_type='', num_workers=1, **k):
        self.device, self.compute = device, compute_type
        if fail.get('load') and device == 'cuda':
            raise RuntimeError(fail['load'])
    def transcribe(self, audio, **params):
        if self.device == 'cuda' and fail.get('first'):
            raise RuntimeError(fail['first'])
        if self.device == 'cuda' and fail.get('later') and isinstance(audio, str):
            raise RuntimeError(fail['later'])
        if isinstance(audio, str) and audio == 'boom':
            raise ValueError('bad language')
        return iter([types.SimpleNamespace(text='hi', no_speech_prob=0.1, avg_logprob=-0.2, compression_ratio=1.0, start=0.0, end=1.0)]), None
fw = types.ModuleType('faster_whisper'); fw.WhisperModel = WhisperModel; sys.modules['faster_whisper'] = fw
ct2 = types.ModuleType('ctranslate2'); ct2.get_cuda_device_count = lambda: fail.get('cards', 1); sys.modules['ctranslate2'] = ct2
sys.argv = ['whisper_worker.py', %r]
import runpy
runpy.run_path(%r, run_name='__main__')
"""


def _run(tmp_path, requests=(), device='cuda', **fail):
    cfg = json.dumps({'size': 'tiny', 'device': device, 'compute_type': 'int8', 'num_workers': 1, 'cuda_device': 0})
    script = tmp_path / 'fake_host.py'
    script.write_text(FAKES % (json.dumps(fail), cfg, str(WORKER)), encoding='utf-8')
    p = subprocess.Popen([sys.executable, str(script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, cwd=str(ROOT))
    # stdin stays open until every answer is in: its closing means the parent died, and the worker exits at once
    msgs = [json.loads(p.stdout.readline())]
    for req in requests:
        p.stdin.write(json.dumps(req) + '\n')
        p.stdin.flush()
        msgs.append(json.loads(p.stdout.readline()))
    p.stdin.close()
    assert p.wait(timeout=20) == 0       # the parent gone: out, on its own
    return msgs, SimpleNamespace(stderr=p.stderr.read())


@pytest.mark.skipif(importlib.util.find_spec('numpy') is None, reason='numpy')
def test_loads_on_the_card_after_a_real_pass_and_answers(tmp_path):
    msgs, r = _run(tmp_path, [{'path': 'a.wav', 'params': {}}])
    assert msgs[0]['ready'] is True and msgs[0]['device'] == 'cuda' and msgs[0]['why'] is None
    assert msgs[1]['ok'] is True and msgs[1]['segments'][0]['text'] == 'hi'


def test_a_missing_library_at_the_first_pass_is_cpu_with_the_reason(tmp_path):
    msgs, _ = _run(tmp_path, first='Library libcublas.so.12 is not found or cannot be loaded')
    assert msgs[0]['device'] == 'cpu' and msgs[0]['kind'] == 'library' and 'libcublas' in msgs[0]['why']


def test_out_of_memory_on_cublas_is_not_a_missing_library(tmp_path):
    msgs, _ = _run(tmp_path, first='cuBLAS failed with status CUBLAS_STATUS_OUT_OF_MEMORY')
    assert msgs[0]['device'] == 'cpu' and msgs[0]['kind'] == 'other'


def test_a_card_failing_later_moves_to_the_cpu_and_still_answers(tmp_path):
    msgs, _ = _run(tmp_path, [{'path': 'a.wav', 'params': {}}], later='CUDA failed with error device lost')
    assert msgs[0]['device'] == 'cuda'
    assert msgs[1]['ok'] is True and msgs[1]['device'] == 'cpu' and msgs[1]['why']


def test_no_card_is_quiet_cpu(tmp_path):
    msgs, _ = _run(tmp_path, cards=0)
    assert msgs[0]['device'] == 'cpu' and msgs[0]['why'] is None


def test_cpu_by_choice_never_tries_the_card(tmp_path):
    msgs, _ = _run(tmp_path, device='cpu', load='must not be tried')
    assert msgs[0]['device'] == 'cpu' and msgs[0]['why'] is None


def test_a_bad_request_is_an_answer_not_a_death(tmp_path):
    msgs, _ = _run(tmp_path, [{'path': 'boom', 'params': {}}, {'path': 'a.wav', 'params': {}}], device='cpu')
    assert msgs[1]['ok'] is False and 'bad language' in msgs[1]['error']
    assert msgs[2]['ok'] is True


def test_a_library_that_prints_cannot_break_the_protocol():
    src = WORKER.read_text(encoding='utf-8')
    assert 'os.dup2(2, 1)' in src and 'import torch' in src
    assert src.index('os.dup2(2, 1)') < src.index('import numpy')


@pytest.mark.skipif(sys.platform == 'win32', reason='Windows needs torch for its DLL folder')
def test_torch_and_transformers_cannot_load_in_the_worker(tmp_path):
    """ctranslate2 reaches them through its converters on any full install."""
    script = tmp_path / 'probe.py'
    head = WORKER.read_text(encoding='utf-8').split('import numpy as np')[0]
    script.write_text(head + "\nimport ctranslate2\nprint('torch' in sys.modules and sys.modules['torch'] is not None, file=sys.stderr)\n", encoding='utf-8')
    r = subprocess.run([sys.executable, str(script)],
                       capture_output=True, text=True, timeout=60, cwd=str(ROOT), stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    assert r.stderr.strip().splitlines()[-1] == 'False'
