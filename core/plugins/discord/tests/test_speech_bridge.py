import struct
import wave
import io

import pytest

from plugins.discord.sapphire.speech_bridge import SapphireSpeechBridge


class FakeWhisper:
    def __init__(self):
        self.last_path = None

    def transcribe_file(self, path):
        self.last_path = path
        return 'hello voice'

    def is_available(self):
        return True


class _FakeSegment:
    def __init__(self, text, *, no_speech_prob=0.0, avg_logprob=0.0):
        self.text = text
        self.no_speech_prob = no_speech_prob
        self.avg_logprob = avg_logprob


class FakeWhisperWithModel(FakeWhisper):
    def __init__(self, segments=None):
        super().__init__()
        self.model = _FakeWhisperModel(segments or [_FakeSegment('hello voice')])
        self._lock = None


class _FakeWhisperModel:
    def __init__(self, segments):
        self._segments = segments

    def transcribe(self, path, **params):
        return self._segments, None


class FakeTts:
    def generate_audio_data(self, text):
        return f'audio:{text}'.encode()


class FakeSystem:
    def __init__(self):
        self.whisper_client = FakeWhisper()
        self.tts = FakeTts()


class FakePluginLoader:
    pass


def _tiny_wav() -> bytes:
    buffer = io.BytesIO()
    frames = struct.pack('<400h', *([5000, -5000] * 200))
    with wave.open(buffer, 'wb') as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(frames)
    return buffer.getvalue()


def test_speech_bridge_transcribes_wav_via_system():
    system = FakeSystem()
    bridge = SapphireSpeechBridge(object())
    bridge._system = lambda: system
    result = bridge.transcribe_audio(_tiny_wav())
    assert result['text'] == 'hello voice'
    assert system.whisper_client.last_path


def test_speech_bridge_uses_model_path_when_available():
    pytest.importorskip('soundfile')
    system = FakeSystem()
    system.whisper_client = FakeWhisperWithModel()
    bridge = SapphireSpeechBridge(object())
    bridge._system = lambda: system
    result = bridge.transcribe_audio(_tiny_wav())
    assert result['text'] == 'hello voice'


def test_speech_bridge_rejects_near_silent_wav():
    pytest.importorskip('soundfile')
    system = FakeSystem()
    bridge = SapphireSpeechBridge(object())
    bridge._system = lambda: system
    silent = io.BytesIO()
    with wave.open(silent, 'wb') as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b'\x00\x00' * 200)
    result = bridge.transcribe_audio(silent.getvalue())
    assert result['text'] == ''


def test_speech_bridge_synthesizes_via_system():
    system = FakeSystem()
    bridge = SapphireSpeechBridge(object())
    bridge._system = lambda: system
    result = bridge.synthesize_speech('test')
    assert result['audio_bytes'] == b'audio:test'
