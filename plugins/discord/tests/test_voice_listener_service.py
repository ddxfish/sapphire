"""S6: one voice lane — STT, the add-on door, the runner's addressing gate."""
from types import SimpleNamespace

from plugins.discord.models.voice import VoiceSession
from plugins.discord.voice import voice_listener_service as vls
from plugins.discord.voice.voice_listener_service import VoiceListenerService


class FakeTransport:
    def __init__(self):
        self.started, self.stopped = [], []

    def start_listening_sync(self, account_name, channel_id, *, on_utterance, loop=None, **kwargs):
        self.started.append((account_name, channel_id, kwargs))
        return {'status': 'listening'}

    def stop_listening_sync(self, account_name, channel_id):
        self.stopped.append((account_name, channel_id))
        return {'status': 'stopped'}


class FakeRunner:
    def __init__(self):
        self.started, self.turns, self.stopped = [], [], []
        self.active = set()

    def start(self, session):
        self.started.append(session.session_id)
        self.active.add(session.session_id)
        return {'status': 'active'}

    def ensure_started(self, session):
        return self.start(session)

    def is_active(self, session_id):
        return session_id in self.active

    def frame_feed_for(self, session_id):
        return None

    def submit_turn_text(self, session_id, text, *, speaker_id='', humans=None):
        self.turns.append((session_id, text, speaker_id, humans))
        return {'status': 'submitted'}

    def stop(self, session_id):
        self.stopped.append(session_id)
        self.active.discard(session_id)


class FakeSpeech:
    def transcribe_audio(self, wav, *, speaker_hint=''):
        return {'text': 'hello there', 'confidence': 0.9}


def _session():
    return VoiceSession(session_id='sess1', account_name='alpha', guild_id='g1', channel_id='vc1')


def _store(min_silence=1.5):
    return SimpleNamespace(resolve=lambda: SimpleNamespace(voice=SimpleNamespace(min_silence_seconds=min_silence)))


def test_start_listens_and_starts_the_runner():
    transport, runner = FakeTransport(), FakeRunner()
    svc = VoiceListenerService(voice_transport=transport, conversation_runner=runner, settings_store=_store(2.0))
    assert svc.start(_session())['status'] == 'listening'
    assert transport.started[0][:2] == ('alpha', 'vc1') and transport.started[0][2]['silence_seconds'] == 3.2
    assert runner.started == ['sess1']
    svc.stop('alpha', 'vc1')
    assert runner.stopped == ['sess1'] and transport.stopped == [('alpha', 'vc1')]


def test_utterance_is_transcribed_offered_to_addons_and_submitted(monkeypatch):
    seen = []
    monkeypatch.setattr(vls.hooks_out, 'fire', lambda name, payload: seen.append((name, payload)))
    transport, runner = FakeTransport(), FakeRunner()
    svc = VoiceListenerService(voice_transport=transport, conversation_runner=runner, speech_bridge=FakeSpeech())
    svc.start(_session())
    svc._handle_utterance('alpha', 'vc1', 42, 'Krem', b'RIFF')
    assert seen[0][0] == 'discord_voice_utterance' and seen[0][1]['text'] == 'hello there' and seen[0][1]['speaker_id'] == '42'
    assert runner.turns == [('sess1', 'Krem: hello there', '42', None)]
    svc._handle_utterance('alpha', 'nope', 42, 'Krem', b'RIFF')        # unknown channel: nothing
    assert len(runner.turns) == 1


def test_no_speech_bridge_means_no_turn():
    transport, runner = FakeTransport(), FakeRunner()
    svc = VoiceListenerService(voice_transport=transport, conversation_runner=runner)
    svc.start(_session())
    svc._handle_utterance('alpha', 'vc1', 42, 'Krem', b'RIFF')
    assert runner.turns == []
