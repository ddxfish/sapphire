# sampler_client.py - Sapphire's side of the sampler: start it on demand under the plugin env (ProcessManager, so
# it dies with the plugin), wait for /health, fetch a sample. Lives in this light module so routes and the daemon
# share one manager.
import logging
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_proc = None
_port = None


def _free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def _alive():
    if not _port:
        return False
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{_port}/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def ensure(python, plugin_dir, root, device='auto', wait=30):
    """Start the sampler if it is not answering. Returns the port."""
    global _proc, _port
    with _lock:
        if _alive():
            return _port
        from core.process_manager import ProcessManager
        from core.plugin_envs import PROJECT_ROOT
        if _proc:
            try:
                _proc.stop()
            except Exception:
                pass
        _port = _free_port()
        from .paths import job_env
        env = {**job_env(root), 'WWM_DEVICE': device, 'PYTHONPATH': str(plugin_dir), 'PYTHONUNBUFFERED': '1'}
        import os
        full = {**os.environ, **env}
        _proc = ProcessManager(script_path=Path(plugin_dir) / 'wakeword_maker' / 'sampler.py', log_name='wakeword-maker-sampler',
                               base_dir=PROJECT_ROOT, command_args=[str(python), '-m', 'wakeword_maker.sampler', '--port', str(_port)],
                               env_callback=lambda: full, python_exe=str(python))
        if not _proc.start():
            raise RuntimeError('the voice sampler could not start (see user/logs/wakeword-maker-sampler.log)')
        end = time.time() + wait
        while time.time() < end:
            if _alive():
                return _port
            time.sleep(0.25)
        raise RuntimeError('the voice sampler did not answer in time')


def fetch(port, engine, voice, text, speaker=None, speed=1.0, blend=None, timeout=90):
    return get(port, 'sample', {'engine': engine, 'voice': voice, 'text': text, 'speaker': speaker, 'speed': speed, 'blend': blend}, timeout)


def get(port, door, params, timeout=90):
    """(wav bytes, X-Sample json text). A 500 from the sampler comes back as RuntimeError with its message."""
    q = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, '')})
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/{door}?{q}", timeout=timeout) as r:
            return r.read(), r.headers.get('X-Sample', '{}')
    except urllib.error.HTTPError as e:
        try:
            import json
            msg = json.loads(e.read().decode('utf-8', 'replace')).get('error', str(e))
        except Exception:
            msg = str(e)
        raise RuntimeError(msg)


def stop():
    global _proc, _port
    with _lock:
        if _proc:
            try:
                _proc.stop()
            except Exception as e:
                logger.debug(f"[WWM] sampler stop: {e}")
        _proc, _port = None, None


def post(port, door, params, data, timeout=120):
    q = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, '')})
    req = urllib.request.Request(f"http://127.0.0.1:{port}/{door}?{q}", data=data, method='POST', headers={'Content-Type': 'audio/wav'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        try:
            import json
            msg = json.loads(e.read().decode('utf-8', 'replace')).get('error', str(e))
        except Exception:
            msg = str(e)
        raise RuntimeError(msg)
