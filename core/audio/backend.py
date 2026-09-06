# core/audio/backend.py - the ONE place sounddevice is imported.
"""
sounddevice runs Pa_Initialize at import time. On a box with no reachable
PulseAudio/PipeWire server (headless, VM, or the socket not up yet at boot)
PortAudio >= 19.7 (Ubuntu 26.04) aborts the whole init — upstream #900 — and
the import raises. Before this module that killed boot from _init_tts_provider,
even with every voice feature switched off.

Every core consumer now does `from core.audio.backend import sd`. `sd` is a
proxy: attached, it forwards to the real module (byte-identical behaviour);
detached, it answers as a null backend — no devices, streams raise
sd.PortAudioError — so the existing audio_available / Null* fallbacks light
up and Sapphire boots without a speaker or mic. ensure() retries the import
(rate-limited) so a late-starting sound server is picked up on the next
device query, no restart. 2026-09-06.
"""
import importlib
import logging
import sys
import threading
import time

logger = logging.getLogger(__name__)

RETRY_INTERVAL = 5.0
_LANE_KEY = "audio"

_lock = threading.Lock()
_real = None          # the real sounddevice module once it imports
_error = None         # str reason while detached
_last_try = 0.0
_lane_posted = False


class PortAudioError(Exception):
    """Raised by the null backend. Once attached the proxy exposes the real
    module's class instead, so `except sd.PortAudioError` works in both states."""


class _NullDefault:
    device = (None, None)
    samplerate = None
    channels = (None, None)
    dtype = ('float32', 'float32')
    latency = ('high', 'high')
    hostapi = None


class _NullBackend:
    """Exactly the surface core uses. Enumeration is empty, anything that
    would open a stream raises, cleanup calls (stop/wait) are no-ops."""
    PortAudioError = PortAudioError

    def __init__(self, reason):
        self.reason = reason
        self.default = _NullDefault()

    def _raise(self, *_a, **_k):
        raise PortAudioError(f"audio backend unavailable: {self.reason}")

    def query_devices(self, device=None, kind=None):
        if device is None and kind is None:
            return []
        self._raise()

    def query_hostapis(self, index=None):
        if index is None:
            return ()
        self._raise()

    def get_portaudio_version(self):
        return (0, f"unavailable: {self.reason}")

    InputStream = OutputStream = Stream = RawInputStream = RawOutputStream = _raise
    rec = play = playrec = check_input_settings = check_output_settings = _raise

    def stop(self, ignore_errors=True):
        pass

    def wait(self, ignore_errors=True):
        return None


class _Proxy:
    """`sd` -- forwards to the real sounddevice module, or the null backend."""
    __slots__ = ()

    def __getattr__(self, name):
        target = _real if _real is not None else _null
        return getattr(target, name)

    def __repr__(self):
        state = 'attached' if _real is not None else f'DETACHED: {_error}'
        return f"<sounddevice proxy: {state}>"


_null = _NullBackend("not initialized")
sd = _Proxy()


def _import_sounddevice():
    """Isolated so tests can swap the loader."""
    return importlib.import_module("sounddevice")


def _load():
    global _real, _error, _null, _last_try
    _last_try = time.monotonic()
    try:
        mod = _import_sounddevice()
    except Exception as e:  # PortAudioError (no server), OSError (no libportaudio), ImportError
        _real = None
        _error = f"{type(e).__name__}: {e}"
        _null = _NullBackend(_error)
        sys.modules.pop("sounddevice", None)   # never leave a half-initialized module behind
        logger.warning(f"Audio backend unavailable -- running without local speaker/mic: {_error}")
        return False
    _real = mod
    _error = None
    return True


def available():
    return _real is not None


def error():
    """Detached reason, or None when attached."""
    return _error


def ensure(min_interval=RETRY_INTERVAL):
    """Retry the import if detached (rate-limited). True when attached."""
    if _real is not None:
        return True
    with _lock:
        if _real is not None:
            return True
        if time.monotonic() - _last_try < min_interval:
            return False
        if _load():
            logger.info("Audio backend attached -- local speaker/mic available now")
            _clear_boot_error()
            return True
        return False


def publish_boot_error():
    """Land the detached state in the boot-errors lane (/api/init toast) --
    the same lane the deaf-wakeword case uses. Idempotent; no-op when attached."""
    global _lane_posted
    if _real is not None or _lane_posted:
        return
    try:
        from core.plugin_loader import plugin_loader
        plugin_loader._load_errors.append({
            "plugin": _LANE_KEY,
            "error": (f"Local audio unavailable -- PortAudio failed to start ({_error}). "
                      "Sapphire booted with no local speaker/mic; browser voice still works. "
                      "Headless/VM: 'systemctl --user start pipewire-pulse.socket' (install "
                      "pipewire-pulse if missing), then open Settings > Audio to re-detect."),
        })
        _lane_posted = True
    except Exception as e:
        logger.debug(f"audio boot-lane publish skipped: {e}")


def _clear_boot_error():
    global _lane_posted
    if not _lane_posted:
        return
    try:
        from core.plugin_loader import plugin_loader
        plugin_loader._load_errors[:] = [e for e in plugin_loader._load_errors
                                         if e.get("plugin") != _LANE_KEY]
    except Exception:
        pass
    _lane_posted = False


_load()
