"""TTSClient.set_voice -> provider.on_voice_selected hook (core, 2026-06-16).

set_voice is the chokepoint every voice change funnels through (chat voice
dropdown via _apply_chat_settings, and chat load). Providers that need to react
to a voice change (e.g. Piper pre-fetching a model) opt in via on_voice_selected.
The call must be isolated so a provider bug can't break voice setting.
"""
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def tts_client(monkeypatch):
    from core.tts.tts_client import TTSClient
    # Skip real audio-device probing in the test env
    monkeypatch.setattr(TTSClient, "_init_output_device", lambda self: None)
    prov = MagicMock()
    client = TTSClient(provider=prov)
    prov.reset_mock()  # ignore anything touched during __init__
    return client, prov


def test_set_voice_notifies_provider(tts_client):
    client, prov = tts_client
    client.set_voice("en_US-lessac-low")
    prov.on_voice_selected.assert_called_once_with("en_US-lessac-low")
    assert client.voice_name == "en_US-lessac-low"


def test_set_voice_empty_does_not_notify(tts_client):
    client, prov = tts_client
    client.set_voice("")
    prov.on_voice_selected.assert_not_called()


def test_set_voice_isolates_provider_exception(tts_client):
    client, prov = tts_client
    prov.on_voice_selected.side_effect = RuntimeError("boom")
    assert client.set_voice("en_US-amy-low") is True   # exception swallowed
    assert client.voice_name == "en_US-amy-low"          # voice still applied


def test_set_voice_provider_without_hook_is_fine(monkeypatch):
    """A provider without on_voice_selected (Kokoro/gtts/elevenlabs) must not error."""
    from core.tts.tts_client import TTSClient
    monkeypatch.setattr(TTSClient, "_init_output_device", lambda self: None)

    class _Bare:
        audio_content_type = "audio/ogg"
        SPEED_MIN, SPEED_MAX = 0.5, 2.0
        supports_streaming = False
        def generate(self, *a, **k): return b""
        def is_available(self): return True

    client = TTSClient(provider=_Bare())
    assert client.set_voice("af_heart") is True
