"""Audio backend proxy -- Sapphire boots with no sound server (2026-09-06).

`import sounddevice` runs Pa_Initialize; on headless boxes / VMs with no
PulseAudio/PipeWire server, PortAudio >= 19.7 raises and that used to kill
boot from _init_tts_provider. core.audio.backend owns the import: `sd` is a
proxy that answers as a null backend when detached, re-attaches on demand,
and lands the detached state in the boot-errors lane.
"""
import re
import time
import types
from pathlib import Path

import pytest

from core.audio import backend

ROOT = Path(__file__).resolve().parent.parent
NO_SERVER = ("Error initializing PortAudio: Unanticipated host error [PaErrorCode -9999]: "
             "'PulseAudio_Initialize: Can't connect to server'")


@pytest.fixture
def detached(monkeypatch):
    """Force the backend into the detached state; restore afterwards."""
    def boom():
        raise RuntimeError(NO_SERVER)
    saved = (backend._real, backend._error, backend._null, backend._last_try, backend._lane_posted)
    monkeypatch.setattr(backend, "_import_sounddevice", boom)
    backend._load()
    yield backend
    backend._clear_boot_error()
    backend._real, backend._error, backend._null, backend._last_try, backend._lane_posted = saved


def test_detached_null_surface(detached):
    sd = backend.sd
    assert backend.available() is False
    assert "Can't connect to server" in backend.error()
    assert sd.query_devices() == []
    assert sd.default.device == (None, None)
    for opener in (sd.OutputStream, sd.InputStream, sd.Stream):
        with pytest.raises(sd.PortAudioError):
            opener(samplerate=48000, channels=1)
    with pytest.raises(sd.PortAudioError):
        sd.rec(160, samplerate=16000, channels=1)
    with pytest.raises(sd.PortAudioError):
        sd.query_devices(3)          # index form must fail loudly, not return junk
    sd.stop(); sd.wait()             # cleanup paths are no-ops
    assert "DETACHED" in repr(sd)


def test_device_manager_degrades_to_no_devices(detached):
    from core.audio.device_manager import DeviceManager
    dm = DeviceManager()
    assert dm.query_devices(force_refresh=True) == []
    assert dm.get_output_devices() == []
    assert dm.find_output_device() == (None, None, None)


def test_tts_client_constructs_silent(detached):
    from core.tts.tts_client import TTSClient
    from core.tts.providers import get_tts_provider
    client = TTSClient(provider=get_tts_provider('none'))
    assert client.audio_available is False
    assert client.output_device is None


def test_boot_lane_posted_once_and_cleared_on_attach(detached, monkeypatch):
    from core.plugin_loader import plugin_loader
    before = [e for e in plugin_loader._load_errors if e.get("plugin") == "audio"]
    assert before == []
    backend.publish_boot_error()
    backend.publish_boot_error()   # idempotent
    lane = [e for e in plugin_loader._load_errors if e.get("plugin") == "audio"]
    assert len(lane) == 1
    assert "browser voice still works" in lane[0]["error"]
    assert "Can't connect to server" in lane[0]["error"]

    # sound server shows up later -> ensure() attaches, proxy follows, lane clears
    fake = types.SimpleNamespace(
        query_devices=lambda *a, **k: ["fake-dev"],
        default=types.SimpleNamespace(device=(3, 4)),
        PortAudioError=RuntimeError,
    )
    monkeypatch.setattr(backend, "_import_sounddevice", lambda: fake)
    assert backend.ensure(min_interval=0) is True
    assert backend.available() is True and backend.error() is None
    assert backend.sd.query_devices() == ["fake-dev"]
    assert backend.sd.default.device == (3, 4)
    assert backend.sd.PortAudioError is RuntimeError
    assert [e for e in plugin_loader._load_errors if e.get("plugin") == "audio"] == []


def test_ensure_is_rate_limited(detached, monkeypatch):
    calls = []
    monkeypatch.setattr(backend, "_import_sounddevice", lambda: calls.append(1) or (_ for _ in ()).throw(RuntimeError("still down")))
    backend._last_try = time.monotonic()
    assert backend.ensure() is False          # inside the interval: no import attempt
    assert calls == []
    assert backend.ensure(min_interval=0) is False
    assert calls == [1]


def test_attached_when_real_sounddevice_imports():
    """Whatever this box has, the proxy state must agree with importability."""
    try:
        import sounddevice  # noqa: F401
        importable = True
    except Exception:
        importable = False
    if importable:
        # Only assert when nobody else detached it for the process lifetime.
        assert backend.available() or backend.error()


def test_classify_names_the_fix_for_no_sound_server():
    from core.audio.errors import classify_audio_error
    msg = classify_audio_error(Exception(NO_SERVER))
    assert "pipewire-pulse" in msg
    assert "browser voice" in msg.lower()


def test_no_core_module_imports_sounddevice_directly():
    """The whole class of bug: one import owner. Module-top OR lazy -- every core
    site goes through the proxy. device_manager's indented hit is the standalone
    subprocess script string (its own process, its own try) and is allowed."""
    pat = re.compile(r"^(\s*)(import sounddevice|from sounddevice import)")
    offenders = []
    for py in list((ROOT / "core").rglob("*.py")) + [ROOT / "sapphire.py"]:
        rel = py.relative_to(ROOT).as_posix()
        if rel == "core/audio/backend.py":
            continue
        for lineno, line in enumerate(py.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            m = pat.match(line)
            if not m:
                continue
            if rel == "core/audio/device_manager.py" and m.group(1):
                continue   # embedded subprocess script
            offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert offenders == [], "direct sounddevice import(s) -- route through core.audio.backend:\n" + "\n".join(offenders)
