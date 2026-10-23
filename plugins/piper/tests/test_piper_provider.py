"""Piper provider unit tests — self-contained (no tests/conftest fixtures).

Covers the provider's own logic: voice resolution (ignore Kokoro voices),
OGG/Opus encode + 22050->24000 resample, speed->length_scale, generate /
generate_stream over a fake voice, and on_settings_saved gating + the
background warm-download that publishes PLUGIN_NOTICE.

Heavy bits (real model load / network download) are mocked — hermetic, no GPU,
no network, no voice files needed. The generic core save-hook itself is tested
in tests/test_plugin_settings_saved_hook.py (it's core, not plugin, code).
"""
import io
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from plugins.piper.provider import PiperTTSProvider, _is_piper_voice


@pytest.fixture
def provider():
    return PiperTTSProvider()


@pytest.fixture
def captured_notices(monkeypatch):
    """Capture event_bus.publish() calls. The provider does
    `from core.event_bus import publish` at call-time, so patching the module
    attribute is picked up."""
    import core.event_bus as eb
    events = []
    monkeypatch.setattr(eb, "publish", lambda et, data=None: events.append((et, data or {})))
    return events


# fake piper voice so synth tests need no model/network
class _FakeChunk:
    def __init__(self, arr):
        self.audio_float_array = arr


class _FakeVoice:
    def __init__(self, sr=22050, n_sentences=2):
        self.config = SimpleNamespace(sample_rate=sr)
        self._sr = sr
        self._n = n_sentences

    def synthesize(self, text, syn_config=None):
        for _ in range(self._n):
            yield _FakeChunk(np.linspace(-0.2, 0.2, self._sr // 2, dtype="float32"))


def _decode(ogg_bytes):
    return sf.read(io.BytesIO(ogg_bytes))


# ─── voice resolution ──────────────────────────────────────────────────────

def test_is_piper_voice():
    assert _is_piper_voice("en_US-hfc_female-medium")
    assert _is_piper_voice("en_US-amy-low")
    assert not _is_piper_voice("af_heart")     # Kokoro voice
    assert not _is_piper_voice("")
    assert not _is_piper_voice("rachel")


def test_resolve_voice_ignores_kokoro_voice(provider, monkeypatch):
    monkeypatch.setattr(type(provider), "_voice_name",
                        property(lambda self: "en_US-hfc_female-medium"))
    assert provider._resolve_voice("af_heart") == "en_US-hfc_female-medium"
    assert provider._resolve_voice("en_US-amy-low") == "en_US-amy-low"


# ─── encode / resample ──────────────────────────────────────────────────────

def test_encode_opus_resamples_22050_to_24000(provider):
    audio = np.linspace(-0.5, 0.5, 22050, dtype="float32")
    data, sr = _decode(provider._encode_opus(audio, 22050))
    assert sr == 24000 and len(data) > 0


def test_encode_opus_native_16000_no_resample(provider):
    audio = np.linspace(-0.5, 0.5, 16000, dtype="float32")
    data, sr = _decode(provider._encode_opus(audio, 16000))
    assert sr == 16000


def test_encode_opus_empty_is_empty(provider):
    assert provider._encode_opus(np.zeros(0, dtype="float32"), 22050) == b""


# ─── speed -> length_scale ──────────────────────────────────────────────────

def test_syn_config_speed_inverse(provider):
    assert provider._syn_config(2.0).length_scale == pytest.approx(0.5)
    assert provider._syn_config(1.0).length_scale == pytest.approx(1.0)


def test_syn_config_speed_clamped(provider):
    assert provider._syn_config(5.0).length_scale == pytest.approx(1.0 / provider.SPEED_MAX)


# ─── generate / generate_stream over a fake voice ───────────────────────────

def test_generate_concatenates_and_encodes(provider, monkeypatch):
    monkeypatch.setattr(provider, "_get_voice", lambda name: _FakeVoice(sr=22050, n_sentences=3))
    data, sr = _decode(provider.generate("Hello there friend.", "af_heart", 1.0))
    assert sr == 24000 and len(data) > 0


def test_generate_stream_one_blob_per_sentence(provider, monkeypatch):
    monkeypatch.setattr(provider, "_get_voice", lambda name: _FakeVoice(sr=16000, n_sentences=2))
    chunks = list(provider.generate_stream("One. Two.", "af_heart", 1.0))
    assert len(chunks) == 2
    for c in chunks:
        data, sr = _decode(c)
        assert sr == 16000 and len(data) > 0


def test_generate_empty_text_returns_none(provider):
    assert provider.generate("   ", "af_heart", 1.0) is None


# ─── on_settings_saved gating + background thread ───────────────────────────

def test_on_settings_saved_ignores_other_plugins(provider, monkeypatch):
    called = []
    monkeypatch.setattr(provider, "_warm_download", lambda name: called.append(name))
    provider.on_settings_saved("some-other-plugin", {"voice": "en_US-amy-low"})
    assert called == []


def test_on_settings_saved_skips_if_present(provider, monkeypatch):
    called = []
    monkeypatch.setattr(provider, "_warm_download", lambda name: called.append(name))
    monkeypatch.setattr(provider, "_model_path", lambda name: Path("/exists.onnx"))
    monkeypatch.setattr(Path, "exists", lambda self: True)
    provider.on_settings_saved("piper", {"voice": "en_US-amy-low"})
    assert called == []


def test_on_settings_saved_triggers_background_download(provider, monkeypatch):
    started = threading.Event()
    seen = {}
    monkeypatch.setattr(provider, "_warm_download",
                        lambda name: (seen.__setitem__("name", name), started.set()))
    monkeypatch.setattr(provider, "_model_path", lambda name: Path("/nope.onnx"))
    monkeypatch.setattr(Path, "exists", lambda self: False)
    provider.on_settings_saved("piper", {"voice": "en_US-lessac-medium"})
    assert started.wait(5), "background warm-download thread never ran"
    assert seen["name"] == "en_US-lessac-medium"


# ─── PLUGIN_NOTICE publishing ───────────────────────────────────────────────

def test_warm_download_publishes_info_then_success(provider, monkeypatch, captured_notices):
    monkeypatch.setattr(provider, "_download_voice", lambda name: None)
    provider._warm_download("en_US-kristin-medium")
    notices = [d for t, d in captured_notices if t == "plugin_notice"]
    sevs = [d.get("severity") for d in notices]
    assert "info" in sevs and "success" in sevs
    assert all(d.get("plugin") == "piper" for d in notices)


def test_warm_download_publishes_error_on_failure(provider, monkeypatch, captured_notices):
    def boom(name):
        raise RuntimeError("network down")
    monkeypatch.setattr(provider, "_download_voice", boom)
    provider._warm_download("en_US-kristin-medium")
    sevs = [d.get("severity") for t, d in captured_notices if t == "plugin_notice"]
    assert "error" in sevs


# ─── on_voice_selected (the real chokepoint: chat voice dropdown / chat load) ─

def test_ensure_voice_async_ignores_non_piper(provider, monkeypatch):
    called = []
    monkeypatch.setattr(provider, "_warm_download", lambda name: called.append(name))
    provider._ensure_voice_async("af_heart")     # Kokoro voice
    assert called == []


def test_ensure_voice_async_skips_if_present(provider, monkeypatch):
    called = []
    monkeypatch.setattr(provider, "_warm_download", lambda name: called.append(name))
    monkeypatch.setattr(provider, "_model_path", lambda name: Path("/exists.onnx"))
    monkeypatch.setattr(Path, "exists", lambda self: True)
    provider._ensure_voice_async("en_US-amy-low")
    assert called == []


def test_on_voice_selected_downloads_missing_piper_voice(provider, monkeypatch):
    started = threading.Event()
    seen = {}
    monkeypatch.setattr(provider, "_warm_download",
                        lambda name: (seen.__setitem__("name", name), started.set()))
    monkeypatch.setattr(provider, "_model_path", lambda name: Path("/nope.onnx"))
    monkeypatch.setattr(Path, "exists", lambda self: False)
    provider.on_voice_selected("en_US-lessac-low")
    assert started.wait(5), "set_voice path never kicked off a background download"
    assert seen["name"] == "en_US-lessac-low"


def test_on_voice_selected_kokoro_voice_falls_back_to_default(provider, monkeypatch):
    """A Kokoro voice on the piper provider resolves to the configured default."""
    monkeypatch.setattr(type(provider), "_voice_name",
                        property(lambda self: "en_US-hfc_female-medium"))
    ensured = []
    monkeypatch.setattr(provider, "_ensure_voice_async", lambda name: ensured.append(name))
    provider.on_voice_selected("af_heart")
    assert ensured == ["en_US-hfc_female-medium"]


# ─── self-heal on corrupt model (the day-ruiner fix) ────────────────────────

def test_get_voice_self_heals_corrupt_model(provider, monkeypatch, tmp_path):
    """A model that EXISTS but fails to load (truncated / corrupt — e.g. left by
    an old non-atomic download) is deleted + re-downloaded once, then loaded.
    Without self-heal that voice would be permanently bricked."""
    import plugins.piper.provider as pp
    from piper import PiperVoice
    monkeypatch.setattr(pp, "VOICES_DIR", tmp_path)
    name = "en_US-lessac-medium"
    (tmp_path / f"{name}.onnx").write_bytes(b"corrupt")
    (tmp_path / f"{name}.onnx.json").write_text("{}")

    loads = []
    def fake_load(path):
        loads.append(path)
        if len(loads) == 1:
            raise RuntimeError("onnx load failed: corrupt header")
        return SimpleNamespace(config=SimpleNamespace(sample_rate=22050))
    monkeypatch.setattr(PiperVoice, "load", fake_load)

    dl = []
    def fake_download(n):
        dl.append(n)
        (tmp_path / f"{name}.onnx").write_bytes(b"freshmodel")
    monkeypatch.setattr(provider, "_download_voice", fake_download)

    v = provider._get_voice(name)
    assert len(loads) == 2, "should retry the load after deleting the corrupt model"
    assert dl == [name], "should re-download exactly once"
    assert v is not None
