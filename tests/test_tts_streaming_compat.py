"""Backwards-compatibility tests for v2.7.0 streaming TTS (M8).

Verifies that existing paths still work when the new streaming layer is
disabled or unavailable:

1. Non-streaming providers (ElevenLabs, gtts) keep `supports_streaming=False`,
   so the pump becomes inert and the legacy full-blob path takes over.
2. TTS_STREAMING_ENABLED=False with a streaming-capable provider — pump
   inert, no SSE tts_chunk events, no spurious hook fires.
3. TTS disabled entirely — pump inert, no hooks fire.
4. The pump being inert means NO hooks fire at all (a plugin that listens
   on `tts_stream_start` doesn't see ghost events).
"""
from unittest.mock import MagicMock

import pytest

from core.tts.providers.base import BaseTTSProvider
from core.tts.stream_pump import StreamingTTSPump
from core.paths import SYSTEM_PLUGINS_DIR


def _system_with(provider, voice="af_heart", speed=1.0):
    sys = MagicMock()
    sys.tts._provider = provider
    sys.tts.voice_name = voice
    sys.tts.speed = speed
    return sys


@pytest.fixture
def fresh_hooks():
    from core.hooks import hook_runner
    snapshot = dict(hook_runner._hooks)
    hook_runner._hooks.clear()
    hook_runner._sorted.clear()
    yield hook_runner
    hook_runner._hooks.clear()
    hook_runner._sorted.clear()
    hook_runner._hooks.update(snapshot)


@pytest.fixture
def streaming_on(monkeypatch):
    import config
    monkeypatch.setattr(config, "TTS_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "TTS_STREAMING_ENABLED", True, raising=False)


# ---------------------------------------------------------------------------
# 1. Provider capability defaults
# ---------------------------------------------------------------------------

def test_base_provider_does_not_support_streaming():
    """BaseTTSProvider default `supports_streaming` must stay False so any
    third-party provider that doesn't opt-in is automatically excluded."""
    assert BaseTTSProvider.supports_streaming is False


def test_elevenlabs_does_not_support_streaming():
    """ElevenLabs provider in the bundled plugin must inherit `False` so
    the chunked-transfer path isn't accidentally activated against an
    incompatible HTTP API."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "elevenlabs_provider",
        str(SYSTEM_PLUGINS_DIR / "elevenlabs" / "provider.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.ElevenLabsTTSProvider.supports_streaming is False


def test_gtts_does_not_support_streaming():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "gtts_provider",
        str(SYSTEM_PLUGINS_DIR / "gtts" / "provider.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.GoogleTranslateTTSProvider.supports_streaming is False


# ---------------------------------------------------------------------------
# 2. Pump inert when provider doesn't support streaming
# ---------------------------------------------------------------------------

class _StreamCapable:
    audio_content_type = "audio/ogg"
    supports_streaming = True
    def generate(self, text, voice, speed): return b"x"


class _LegacyOnly:
    audio_content_type = "audio/mpeg"
    supports_streaming = False
    def generate(self, text, voice, speed): return b"x"


def test_pump_inert_for_legacy_provider_no_hooks_fire(streaming_on, fresh_hooks):
    """A plugin listening to tts_stream_start must NOT see a ghost event
    when the active provider can't stream. Otherwise a captioning banner
    would show ghost captions for non-streaming providers."""
    seen = []
    fresh_hooks.register("tts_stream_start", lambda ev: seen.append("start"), plugin_name="t")
    fresh_hooks.register("tts_chunk_text",   lambda ev: seen.append("text"),  plugin_name="t")
    fresh_hooks.register("tts_chunk_audio",  lambda ev: seen.append("audio"), plugin_name="t")
    fresh_hooks.register("tts_stream_end",   lambda ev: seen.append("end"),   plugin_name="t")
    pump = StreamingTTSPump(system=_system_with(_LegacyOnly()))
    assert pump.enabled is False
    pump.push("Hello there. ")
    pump.push("More.")
    out = list(pump.flush_and_close())
    assert out == []
    assert seen == []  # zero hook fires


# ---------------------------------------------------------------------------
# 3. Setting disabled — no events, no hook fires (even with capable provider)
# ---------------------------------------------------------------------------

def test_pump_inert_when_setting_off_even_with_capable_provider(monkeypatch, fresh_hooks):
    import config
    monkeypatch.setattr(config, "TTS_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "TTS_STREAMING_ENABLED", False, raising=False)
    seen = []
    fresh_hooks.register("tts_stream_start", lambda ev: seen.append("start"), plugin_name="t")
    fresh_hooks.register("tts_chunk_text",   lambda ev: seen.append("text"),  plugin_name="t")
    pump = StreamingTTSPump(system=_system_with(_StreamCapable()))
    assert pump.enabled is False
    pump.push("Hello there. ")
    pump.push("More.")
    out = list(pump.flush_and_close())
    assert out == []
    assert seen == []


def test_pump_inert_when_tts_globally_off(monkeypatch, fresh_hooks):
    import config
    monkeypatch.setattr(config, "TTS_ENABLED", False, raising=False)
    monkeypatch.setattr(config, "TTS_STREAMING_ENABLED", True, raising=False)
    pump = StreamingTTSPump(system=_system_with(_StreamCapable()))
    assert pump.enabled is False
    assert pump.push("Hi. ") == []
    assert list(pump.flush_and_close()) == []


# ---------------------------------------------------------------------------
# 4. Existing pre_tts hook on TTSClient.speak() path is unaffected by
#    streaming pump being present in the codebase. (Smoke import only —
#    full TTSClient unit tests live in tests/test_tts_*.py.)
# ---------------------------------------------------------------------------

def test_pre_tts_hook_still_imported_in_tts_client():
    """Sanity that the legacy pre_tts hook site still exists; if a refactor
    accidentally removed it, plugins that rely on pre_tts (e.g. captioning
    in non-streaming mode) would silently stop firing."""
    from pathlib import Path
    src = Path("core/tts/tts_client.py").read_text(encoding="utf-8")
    assert 'hook_runner.fire("pre_tts"' in src, "pre_tts fire site missing — backwards compat broken"


def test_captioning_plugin_handlers_load_cleanly():
    """The demo plugin should load without errors even when streaming is
    off (default state). Its handlers are no-ops at registration time —
    only fire when the pump runs."""
    from pathlib import Path
    handler_file = SYSTEM_PLUGINS_DIR / "captioning" / "hooks" / "captions.py"
    assert handler_file.exists(), f"captioning ships with Sapphire; missing at {handler_file}"
    source = handler_file.read_text(encoding="utf-8")
    namespace = {"__name__": "captioning_hooks"}
    exec(compile(source, str(handler_file), "exec"), namespace)
    for hook_name in ("tts_stream_start", "tts_chunk_text", "tts_stream_end"):
        assert callable(namespace.get(hook_name)), f"Missing handler: {hook_name}"
