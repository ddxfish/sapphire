"""The GPU torch installer: opt-in, and run where nothing holds torch.

Windows gets a CPU-only torch from PyPI (core/gpu_status.py says so). The
fix is a wheel from PyTorch's own index, but sapphire.py imports torch for
Whisper and the Kokoro child imports it too, and on Windows a loaded DLL
cannot be replaced. So the button only QUEUES the install and restarts;
main.py runs pip here, before sapphire.py exists, with nothing loaded
(2026-10-16, tmp/single-instance-plan.md, Wave C step 3).

Wheel rule, no choices shown: compute capability below 7.0 (Maxwell, Pascal)
gets cu126, everything else cu128. cu128 covers Volta through Blackwell and
its index stops at torch 2.11; cu126 has no Blackwell. Offered on Windows
only: Linux gets CUDA from PyPI already and its failures are driver cases.
Testing knobs: SAPPHIRE_GPU_INDEX=cu126|cu128 forces the offer and the index
(a box without a card); SAPPHIRE_GPU_INSTALL_DRY_RUN=1 makes pip only resolve.
"""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).absolute().parent.parent
REQUEST = ROOT / 'user' / 'pending_gpu_install.json'
RESULT = ROOT / 'user' / 'last_gpu_install.json'
LOG = ROOT / 'user' / 'logs' / 'gpu-install.log'
INDEX_URL = 'https://download.pytorch.org/whl/{index}'
INDEXES = ('cu126', 'cu128')
TORCH_SPEC = 'torch>=2.7.0'
STEP_TIMEOUT = 2700        # seconds per pip step: a 2.5 GB wheel on a slow line


def index_for(compute_cap):
    return 'cu126' if compute_cap is not None and compute_cap < 7.0 else 'cu128'


def plan(status, managed=False):
    """What the button would do for this box, or None when there is nothing to offer."""
    forced = os.environ.get('SAPPHIRE_GPU_INDEX', '').strip().lower()
    if forced in INDEXES:
        return {'index': forced, 'why': f'forced by SAPPHIRE_GPU_INDEX={forced}'}
    if managed or sys.platform != 'win32':
        return None
    status = status or {}
    if status.get('state') not in ('cpu_torch', 'arch_unsupported'):
        return None
    gpu = status.get('gpu') or {}
    index = index_for(gpu.get('compute_cap'))
    return {'index': index, 'why': f"{gpu.get('name') or 'NVIDIA GPU'} (compute {gpu.get('compute_cap')}) -> {index}"}


def request(index):
    if index not in INDEXES:
        raise ValueError(f'unknown index {index!r}')
    REQUEST.parent.mkdir(parents=True, exist_ok=True)
    REQUEST.write_text(json.dumps({'index': index, 'requested_at': time.strftime('%Y-%m-%d %H:%M:%S')}), encoding='utf-8')


def pending():
    return REQUEST.exists()


def last_result():
    try:
        return json.loads(RESULT.read_text(encoding='utf-8'))
    except Exception:
        return None


def pip_commands(index, python=None):
    """Two pip steps. The first swaps torch itself (force, no deps: the CPU
    wheel's dependencies are the same pure-python set and are already there).
    The second lets pip fill any dependency the new wheel wants, from the same
    index, without touching torch again."""
    py = python or sys.executable
    base = [py, '-m', 'pip', 'install', '--index-url', INDEX_URL.format(index=index), '--progress-bar', 'off']
    if os.environ.get('SAPPHIRE_GPU_INSTALL_DRY_RUN'):
        base.append('--dry-run')
    return [base + ['--force-reinstall', '--no-deps', TORCH_SPEC], base + [TORCH_SPEC]]


def _write_result(ok, index, message):
    try:
        RESULT.parent.mkdir(parents=True, exist_ok=True)
        tmp = RESULT.with_suffix('.json.tmp')
        tmp.write_text(json.dumps({'ok': ok, 'index': index, 'message': message,
                                   'at': time.strftime('%Y-%m-%d %H:%M:%S')}), encoding='utf-8')
        tmp.replace(RESULT)
    except OSError:
        pass


def _run(cmd, log, logfile):
    """One pip step, its lines to `log` and the logfile; killed after STEP_TIMEOUT."""
    logfile.write(' '.join(cmd) + '\n')
    logfile.flush()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            text=True, encoding='utf-8', errors='replace',
                            env=dict(os.environ, PIP_DISABLE_PIP_VERSION_CHECK='1', PYTHONUNBUFFERED='1'))
    killer = threading.Timer(STEP_TIMEOUT, proc.kill)
    killer.daemon = True
    killer.start()
    try:
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                logfile.write(line + '\n')
                log(line)
        return proc.wait()
    finally:
        killer.cancel()


def apply_pending(log=print):
    """Called by main.py before spawning sapphire.py. No-op without a request.
    Returns True (installed) / False (failed) / None (nothing to do). The
    request is removed either way: a failing install never loops a boot."""
    if not REQUEST.exists():
        return None
    try:
        index = json.loads(REQUEST.read_text(encoding='utf-8')).get('index')
    except Exception:
        index = None
    REQUEST.unlink(missing_ok=True)
    if index not in INDEXES:
        _write_result(False, index, f'bad request ({index!r}) ignored')
        return False
    log(f"[GPU] Installing torch from PyTorch's {index} index (a 2-3 GB download; Sapphire starts when it is done)")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(LOG, 'w', encoding='utf-8') as logfile:
            for cmd in pip_commands(index):
                code = _run(cmd, log, logfile)
                if code:
                    msg = f"pip exited {code}; the log is user/logs/{LOG.name}"
                    log(f"[GPU] {msg}")
                    _write_result(False, index, msg)
                    return False
    except Exception as e:
        msg = f"install raised: {e}"
        log(f"[GPU] {msg}")
        _write_result(False, index, msg)
        return False
    log(f"[GPU] torch from {index} installed; the Status page says whether the card is in use now")
    _write_result(True, index, f'installed from {index}')
    return True
