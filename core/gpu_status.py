"""GPU truth for voice: is there an NVIDIA card, and can this torch use it?

Kokoro and Whisper both fall back to the CPU quietly when torch cannot see
the card, and the card is almost never the reason: the default Windows torch
from PyPI has no CUDA at all, a cu126 build does not know Blackwell, or the
driver is older than the build. One probe at boot, in a child python so the
main process never imports torch for a status line. The verdict is cached
and read by the Status page, the TTS/STT settings tabs and the boot log.
"""
import json
import logging
import shutil
import subprocess
import sys
import threading

logger = logging.getLogger(__name__)

_PROBE = ("import json, torch; print(json.dumps({'torch': torch.__version__, 'cuda': torch.version.cuda, "
          "'available': bool(torch.cuda.is_available()), "
          "'archs': torch.cuda.get_arch_list() if torch.version.cuda else []}))")
_FLAGS = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}
_lock = threading.Lock()
_done = threading.Event()
_cached = {'state': 'probing', 'message': ''}


def _nvidia_smi():
    """The first NVIDIA card as {name, compute_cap, driver}, None without one.
    `compute_cap` is a newer query field; older drivers get a second, plainer ask."""
    exe = shutil.which('nvidia-smi')
    if not exe:
        return None
    for fields in ('name,compute_cap,driver_version', 'name,driver_version'):
        try:
            r = subprocess.run([exe, f'--query-gpu={fields}', '--format=csv,noheader'],
                               capture_output=True, text=True, timeout=15, **_FLAGS)
        except Exception as e:
            logger.debug(f"[GPU] nvidia-smi failed: {e}")
            return None
        text = (r.stdout or '') + (getattr(r, 'stderr', '') or '')
        if 'NVIDIA-SMI has failed' in text:
            return {'name': 'NVIDIA GPU', 'compute_cap': None, 'driver': None, 'broken': True}
        parts = [p.strip() for p in ((r.stdout or '').strip().splitlines() or [''])[0].split(',')]
        if len(parts) == fields.count(',') + 1 and parts[0]:
            cap = None
            if len(parts) == 3:
                try:
                    cap = float(parts[1])
                except ValueError:
                    pass
            return {'name': parts[0], 'compute_cap': cap, 'driver': parts[-1]}
    return None


def _torch_probe():
    """What this env's torch says about itself, from a child interpreter."""
    try:
        r = subprocess.run([sys.executable, '-c', _PROBE], capture_output=True, text=True, timeout=180, **_FLAGS)
    except Exception as e:
        return {'error': str(e)[:200]}
    lines = (r.stdout or '').strip().splitlines()
    if lines:
        try:
            return json.loads(lines[-1])
        except ValueError:
            pass
    if 'No module named' in (r.stderr or ''):
        return {'torch': None}
    return {'error': (r.stderr or '').strip()[-200:] or f'exit {r.returncode}'}


def _sm(cap):
    return f"sm_{int(round(cap * 10))}"


def _covers(archs, cap):
    """Whether a build's arch list runs a card of this compute capability:
    a cubin of the same major and no higher minor (CUDA binary compatibility),
    or PTX for an older one the driver can JIT forward."""
    want = int(round(cap * 10))
    for a in archs:
        kind, _, num = a.partition('_')
        if not num.isdigit():
            continue
        n = int(num)
        if kind == 'sm' and n // 10 == want // 10 and n % 10 <= want % 10:
            return True
        if kind == 'compute' and n <= want:
            return True
    return False


def classify(gpu, t, managed=False):
    """(state, message). An empty message means there is nothing to say."""
    if t.get('error'):
        return 'unknown', ''
    if gpu is None:
        return 'no_nvidia', ''
    name = gpu.get('name') or 'NVIDIA GPU'
    if gpu.get('broken'):
        return 'driver_broken', (f"{name}: nvidia-smi cannot reach the NVIDIA driver, so voice runs on the CPU. "
                                 "Reinstall or update the driver.")
    if t.get('torch') is None:
        return 'no_torch', ''
    fix = '' if managed else " Fix: docs/TROUBLESHOOTING.md, 'Voice runs on the CPU'."
    cuda = t.get('cuda')
    if not cuda:
        return 'cpu_torch', (f"{name} found, but this torch ({t['torch']}) has no CUDA, so voice runs on the CPU. "
                             f"On Windows that is the default torch.{fix}")
    cap = gpu.get('compute_cap')
    archs = t.get('archs') or []
    if cap is not None and archs and not _covers(archs, cap):
        return 'arch_unsupported', (f"{name} ({_sm(cap)}) is not in this torch build (CUDA {cuda}: {', '.join(archs)}), "
                                    f"so voice runs on the CPU. It needs a build whose CUDA knows this card "
                                    f"(cu128 for Volta and newer, cu126 for older).{fix}")
    if not t.get('available'):
        return 'driver_old', (f"{name} found and torch {t['torch']} has CUDA {cuda}, but torch cannot open it, "
                              f"so voice runs on the CPU. The NVIDIA driver ({gpu.get('driver') or 'unknown'}) may be "
                              f"too old for CUDA {cuda}"
                              + (", or this container has no GPU access." if managed else ". Update the driver."))
    return 'ok', ''


def _managed():
    try:
        from core.settings_manager import settings
        return bool(settings.is_managed())
    except Exception:
        return False


def _tell(state, message, verdict):
    """One warning row per torch build when the card is idle for voice; the
    body names the TTS tab's button where the installer is offered."""
    try:
        from core import gpu_install, notify
        card = (verdict.get('gpu') or {}).get('name') or 'NVIDIA GPU'
        button = gpu_install.plan(verdict, managed=_managed())
        body = message + (" Settings > TTS has the button: Install GPU torch and restart." if button else "")
        notify.notify(f"{card} is idle for voice", 'warning', body, go={'view': 'settings', 'tab': 'tts'},
                      key=f"gpu:{state}:{verdict.get('torch')}", once=True)
    except Exception as e:
        logger.debug(f"[GPU] row not posted: {e}")


def status(refresh=False):
    """The verdict, probing once (again on refresh). Blocks for the probe."""
    global _cached
    with _lock:
        if _done.is_set() and not refresh:
            return dict(_cached)
        gpu = _nvidia_smi()
        t = _torch_probe()
        state, message = classify(gpu, t, _managed())
        _cached = {'state': state, 'message': message, 'gpu': gpu, 'torch': t.get('torch'),
                   'cuda': t.get('cuda'), 'available': t.get('available'), 'archs': t.get('archs') or []}
        _done.set()
        if message:
            logger.warning(f"[GPU] {message}")
            _tell(state, message, _cached)
        elif gpu and t.get('torch'):
            logger.info(f"[GPU] {state}: {gpu['name']}, torch {t['torch']}, CUDA {t.get('cuda')}")
        else:
            logger.info(f"[GPU] {state}")
        return dict(_cached)


def peek():
    """The cached verdict without waiting: state 'probing' until the boot probe is in."""
    return dict(_cached)


def start_background(refresh=False):
    threading.Thread(target=status, kwargs={'refresh': refresh}, name='gpu-probe', daemon=True).start()
