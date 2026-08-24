import time

from plugins.discord.models.observations import VoiceTranscriptObservation
from plugins.discord.storage.repositories.voice_sessions import VoiceSessionRepository
from plugins.discord.storage.sqlite import SQLiteService
from plugins.discord.voice.voice_perception_service import VoicePerceptionService


class FakeSpeechBridge:
    def transcribe_audio(self, audio_bytes, *, speaker_hint=''):
        return {'text': 'hello everyone', 'confidence': 0.85, 'speaker': speaker_hint or 'unknown'}


def _service(tmp_path):
    sqlite = SQLiteService(tmp_path / 'voice_perception.sqlite3')
    sqlite.start()
    repo = VoiceSessionRepository(sqlite)
    session = repo.create_session('alpha', 'g1', 'vc1', mode='transcribe_only')
    return VoicePerceptionService(voice_session_repository=repo, speech_bridge=FakeSpeechBridge()), repo, session


def test_transcribe_persists_segment_and_observation(tmp_path):
    service, repo, session = _service(tmp_path)

    result = service.process_audio(
        session.session_id,
        audio_bytes=b'audio',
        speaker_id='u1',
        speaker_name='alice',
        guild_id='g1',
        guild_name='Guild',
        channel_name='voice',
    )

    assert result['text'] == 'hello everyone'
    segments = repo.list_transcripts(session.session_id)
    assert len(segments) == 1
    assert isinstance(result['observation'], VoiceTranscriptObservation)
