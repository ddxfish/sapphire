"""
Pitch parity across ALL THREE synth paths (found 2026-08-17 via a 0.78
pitch test: wake/voice blob replies spoke at 1.0 while browser turns
honored the setting — broken since supports_pitch landed 2026-08-08).

The three paths and their pitch plumbing:
  1. blob speak      — tts_client._fetch_audio: pitch kwarg / legacy shift
  2. streaming speak — tts_client._generate_and_play_audio_stream (THE FIX):
                       was the one call site that dropped the kwarg entirely
  3. SSE pump        — stream_pump._synth: pitch kwarg / (no legacy needed,
                       browser plays what the server made)

Run with: pytest tests/test_tts_speak_pitch.py -v
"""
import threading
from unittest.mock import MagicMock

from core.tts.tts_client import TTSClient


def _bare_client(provider, pitch=0.78):
    obj = TTSClient.__new__(TTSClient)
    obj.audio_available = True
    obj.should_stop = threading.Event()
    obj.lock = threading.Lock()
    obj._is_playing = False
    obj._provider = provider
    obj.voice_name = "af_heart"
    obj.speed = 1.3
    obj.pitch_shift = pitch
    obj.output_rate = 48000
    obj.output_device = None
    return obj


class TestStreamingSpeakPitch:
    def test_supports_pitch_provider_gets_pitch_kwarg(self):
        provider = MagicMock()
        provider.supports_pitch = True
        provider.generate_stream.return_value = iter([])  # no audio: synth-call contract only

        client = _bare_client(provider, pitch=0.78)
        client._generate_and_play_audio_stream("Hello there friend.")

        provider.generate_stream.assert_called_once_with(
            "Hello there friend.", "af_heart", 1.3, pitch=0.78)

    def test_non_pitch_provider_gets_no_pitch_kwarg(self):
        """Double-shift guard: providers without server-side pitch get the
        legacy client-side shift instead (applied per decoded chunk)."""
        provider = MagicMock()
        provider.supports_pitch = False
        provider.generate_stream.return_value = iter([])

        client = _bare_client(provider, pitch=0.78)
        client._generate_and_play_audio_stream("Hello there friend.")

        provider.generate_stream.assert_called_once_with(
            "Hello there friend.", "af_heart", 1.3)

    def test_blob_path_parity_still_passes_pitch(self):
        """Regression guard on path 1 — the blob fetch already did this
        right; keep it that way."""
        provider = MagicMock()
        provider.supports_pitch = True
        provider.generate.return_value = None  # short-circuits after the call

        client = _bare_client(provider, pitch=0.78)
        client.temp_dir = None
        audio, rate = client._fetch_audio("Hello there friend.")

        assert (audio, rate) == (None, None)
        provider.generate.assert_called_once_with(
            "Hello there friend.", "af_heart", 1.3, pitch=0.78)
