"""stt_audio seam (core/stt/utterance.py) + the chat stamps that ride with it."""
import io
import wave
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from core.hooks import hook_runner
from core.stt import utterance

PLUGIN = "test-stt-audio"


@pytest.fixture
def caught():
    seen = []
    hook_runner.register("stt_audio", lambda e: seen.append(e), plugin_name=PLUGIN)
    yield seen
    hook_runner.unregister_plugin(PLUGIN)


def _wav_bytes(rate, seconds=1.0, freq=220.0):
    t = np.arange(int(rate * seconds)) / rate
    pcm = (np.sin(2 * np.pi * freq * t) * 12000).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue(), pcm


def test_nobody_listening_costs_nothing():
    with patch.object(utterance, "to_16k", side_effect=AssertionError("decoded")):
        assert utterance.heard(b"anything", lane="wake") is None


def test_wav_path_at_native_rate_arrives_16k(caught, tmp_path):
    data, _ = _wav_bytes(44100, seconds=1.0)
    p = tmp_path / "a.wav"
    p.write_bytes(data)
    uid = utterance.heard(str(p), chat="kitchen", lane="wake")
    assert uid and len(caught) == 1
    e = caught[0]
    a = e.metadata["audio"]
    assert a.dtype == np.float32 and abs(len(a) - 16000) <= 2
    assert e.metadata["sample_rate"] == 16000
    assert e.metadata["utterance"] == uid and e.metadata["lane"] == "wake"
    assert e.chat_name == "kitchen"
    assert 0.3 < np.abs(a).max() <= 1.0


def test_raw_pcm_with_rate(caught):
    _, pcm = _wav_bytes(16000, seconds=0.5)
    assert utterance.heard(pcm.tobytes(), rate=16000, lane="conversation")
    a = caught[0].metadata["audio"]
    assert len(a) == 8000
    assert np.allclose(a, pcm / 32768.0, atol=1e-6)


def test_encoded_bytes(caught):
    data, _ = _wav_bytes(48000, seconds=0.25)
    assert utterance.heard(data, lane="device")
    assert abs(len(caught[0].metadata["audio"]) - 4000) <= 2


def test_garbage_never_raises(caught):
    assert utterance.heard(b"not audio at all", lane="device") is None
    assert caught == []


def test_ghost_inject_is_stamped_with_its_chat():
    from core.ghost_messages import build_ghost_message
    seen = []
    hook_runner.register("ghost_inject", lambda e: seen.append(e.chat_name), plugin_name=PLUGIN)
    try:
        resolver = MagicMock(side_effect=lambda name=None: (name, False))
        with patch.object(hook_runner, "_privacy_resolver", resolver):
            build_ghost_message(None, {"spice_enabled": False}, "hi", chat_name="task-target")
    finally:
        hook_runner.unregister_plugin(PLUGIN)
    assert seen == ["task-target"]


def test_device_lane_fires_post_stt_with_its_chat():
    from core.devices import voice
    stt = MagicMock()
    stt.transcribe_file.return_value = " lights off "
    seen = []

    def fix(e):
        seen.append((e.chat_name, e.metadata.get("utterance")))
        e.input = e.input.upper()

    hook_runner.register("post_stt", fix, plugin_name=PLUGIN)
    hook_runner.register("stt_audio", lambda e: None, plugin_name=PLUGIN)
    try:
        resolver = MagicMock(side_effect=lambda name=None: (name, False))
        data, _ = _wav_bytes(16000, seconds=0.2)
        with patch.object(voice, "_system", return_value=SimpleNamespace(whisper_client=stt)), \
             patch.object(hook_runner, "_privacy_resolver", resolver):
            text, problem = voice.transcribe(data, chat="porch")
    finally:
        hook_runner.unregister_plugin(PLUGIN)
    assert (text, problem) == ("LIGHTS OFF", "")
    assert seen[0][0] == "porch" and seen[0][1]


def test_private_chat_audio_withheld_from_unaware_plugin(caught):
    resolver = MagicMock(side_effect=lambda name=None: (name, True))
    with patch.object(hook_runner, "_privacy_resolver", resolver):
        utterance.heard(np.zeros(1600, "<i2").tobytes(), rate=16000, chat="secret", lane="wake")
    assert caught == []

