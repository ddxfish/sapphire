"""Whisper in its own process, started by core/stt/providers/faster_whisper.py.

CTranslate2 wants CUDA 12's cuBLAS whatever CUDA torch moved to, and a native
crash in it must not take Sapphire down. So torch can't be imported here on Linux
(the parent points LD_LIBRARY_PATH at the library, for this process only), and
on Windows torch is imported for one reason: its folder holds cublas64_12.dll.

One JSON line per message on stdin/stdout, no port. stdin closing means the
parent is gone, so this exits even from inside a stuck native call. The file
names and words stay out of the log: only errors go to stderr (whisper.log).
"""
import faulthandler
import json
import logging
import os
import queue
import re
import sys
import threading

_out = os.fdopen(os.dup(1), 'w', encoding='utf-8', buffering=1)
os.dup2(2, 1)       # a library that prints must not write into the protocol
faulthandler.enable()
sys.path[0] = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # not core/stt: its names shadow

# ctranslate2's model converters import transformers when it is there, and that
# loads torch. Nothing here converts, and a broken torch must not stop the ears.
sys.modules['transformers'] = None
if sys.platform != 'win32':
    sys.modules['torch'] = None

import numpy as np
from core.gpu_status import cuda_failed

logging.basicConfig(stream=sys.stderr, level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('whisper_worker')
logger.setLevel(logging.INFO)

COMPUTE_TYPES = ["int8", "int8_float16", "float16", "int8_float32"]
_inbox = queue.Queue()
_model = None
_state = {'device': None, 'compute': None, 'why': None, 'kind': None}


def _send(**msg):
    _out.write(json.dumps(msg) + '\n')


def _read_stdin():
    for line in sys.stdin:
        _inbox.put(json.loads(line))
    os._exit(0)


def _no_library(why):
    """The library is missing or too old for this card. Every compute type would
    say the same, and only this is fixed by installing it."""
    return bool(re.search(r'cublas', str(why), re.I)) and 'memory' not in str(why).lower()


def _first_word(model):
    """One real pass: a model loads on the card without ever needing cuBLAS."""
    t = np.arange(16000) / 16000
    tone = (0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    segments, _ = model.transcribe(tone, beam_size=1, vad_filter=False, language='en')
    list(segments)


def _load(size, **kwargs):
    """Local-first: a warm cache loads with zero network. A model genuinely
    missing is fetched by the parent, whose download shows in the bell."""
    from faster_whisper import WhisperModel
    try:
        return WhisperModel(size, local_files_only=True, **kwargs)
    except Exception as e:
        if not (type(e).__name__ in ('LocalEntryNotFoundError', 'FileNotFoundError')
                or 'local_files_only' in str(e) or 'no such file' in str(e).lower()
                or 'model.bin' in str(e)):      # a download cut short: the folder is there, the model isn't
            raise
    from faster_whisper import utils
    _send(need=size if '/' in size else utils._MODELS.get(size), size=size)
    return WhisperModel(_inbox.get().get('path') or size, **kwargs)


def _on_card(cfg):
    """(model, compute type, why it stayed off the card, kind of why)."""
    no_torch = None
    if sys.platform == 'win32':
        try:
            import torch
            if not torch.version.cuda:
                no_torch = "this torch has no CUDA"
        except Exception as e:
            no_torch = str(e)
            sys.modules['torch'] = None     # older ctranslate2 imports it too, and a broken one must not stop the CPU
    import ctranslate2
    count = ctranslate2.get_cuda_device_count()
    if count <= 0:
        return None, None, None, None       # no card: nothing to say
    if no_torch:
        return None, None, no_torch, 'torch'
    try:
        index = int(cfg.get('cuda_device') or 0)
    except (TypeError, ValueError):
        index = 0
    if not 0 <= index < count:
        return None, None, f"CUDA device {index} is not there ({count} found)", 'other'
    wanted = cfg.get('compute_type')
    why = None
    for compute in [wanted] * (wanted in COMPUTE_TYPES) + [c for c in COMPUTE_TYPES if c != wanted]:
        try:
            model = _load(cfg['size'], device='cuda', device_index=index, compute_type=compute,
                          num_workers=cfg['num_workers'])
            _first_word(model)
            return model, compute, None, None
        except Exception as e:
            why = str(e)
            logger.warning(f"compute_type={compute} failed on the card: {why}")
            if _no_library(why):
                return None, None, why, 'library'
    return None, None, why, 'other'


def _on_cpu(cfg, why=None, kind=None):
    global _model
    _model = _load(cfg['size'], device='cpu', compute_type='int8', num_workers=cfg['num_workers'])
    _state.update(device='cpu', compute='int8', why=why, kind=kind)


def _run(cfg, path, params):
    """Segments as plain data. The first-word pass proves the library, but a card
    can still fail later (lost device, out of memory): CPU until the next start."""
    try:
        segments, _ = _model.transcribe(path, **params)
        return list(segments)
    except Exception as e:
        if _state['device'] != 'cuda' or not cuda_failed(e):
            raise
        logger.warning(f"failed on the card ({e}), moving to the CPU until the next start")
        _on_cpu(cfg, str(e), 'library' if _no_library(e) else 'other')
    segments, _ = _model.transcribe(path, **params)
    return list(segments)


def main():
    global _model
    cfg = json.loads(sys.argv[1])
    threading.Thread(target=_read_stdin, daemon=True).start()
    try:
        if str(cfg.get('device')).strip().lower() == 'cpu':
            sys.modules['torch'] = None
            _on_cpu(cfg)
        else:
            _model, compute, why, kind = _on_card(cfg)
            if _model is None:
                _on_cpu(cfg, why, kind)
            else:
                _state.update(device='cuda', compute=compute)
    except Exception as e:
        logger.error(f"could not load: {e}")
        _send(ready=False, error=str(e))
        return
    logger.info(f"ready on {_state['device']} ({_state['compute']})")
    _send(ready=True, **_state)
    while True:
        req = _inbox.get()
        try:
            segments = _run(cfg, req['path'], req['params'])
            _send(ok=True, segments=[{'text': s.text, 'no_speech_prob': s.no_speech_prob, 'avg_logprob': s.avg_logprob,
                                      'compression_ratio': s.compression_ratio, 'start': s.start, 'end': s.end}
                                     for s in segments], **_state)
        except Exception as e:
            logger.error(f"transcribe failed: {type(e).__name__}: {e}")
            _send(ok=False, error=str(e), **_state)


if __name__ == '__main__':
    main()
