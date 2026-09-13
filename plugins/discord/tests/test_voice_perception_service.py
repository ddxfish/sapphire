from plugins.discord.models.observations import VoiceTranscriptObservation
from plugins.discord.models.settings import SettingsStore
from plugins.discord.storage.repositories.voice_sessions import VoiceSessionRepository
from plugins.discord.storage.sqlite import SQLiteService
from plugins.discord.voice.voice_perception_service import VoicePerceptionService


class FakeSpeechBridge:
    def transcribe_audio(self, audio_bytes, *, speaker_hint=''):
        return {'text': 'hello everyone', 'confidence': 0.85, 'speaker': speaker_hint or 'unknown'}


def _store(transcription_enabled: bool) -> SettingsStore:
    return SettingsStore.from_dict({'global': {'voice': {'transcription_enabled': transcription_enabled}}})


def _service(tmp_path, *, mode='conversational', store=None):
    sqlite = SQLiteService(tmp_path / 'voice_perception.sqlite3')
    sqlite.start()
    repo = VoiceSessionRepository(sqlite)
    session = repo.create_session('alpha', 'g1', 'vc1', mode=mode)
    service = VoicePerceptionService(voice_session_repository=repo, speech_bridge=FakeSpeechBridge(), settings_store=store)
    return service, repo, session


def _hear(service, session):
    return service.process_audio(
        session.session_id,
        audio_bytes=b'audio',
        speaker_id='u1',
        speaker_name='alice',
        guild_id='g1',
        guild_name='Guild',
        channel_name='voice',
    )


def test_transcribe_persists_segment_and_observation(tmp_path):
    service, repo, session = _service(tmp_path, store=_store(True))

    result = _hear(service, session)

    assert result['text'] == 'hello everyone'
    assert result['persisted'] is True
    segments = repo.list_transcripts(session.session_id)
    assert len(segments) == 1
    assert isinstance(result['observation'], VoiceTranscriptObservation)
    assert result['observation'].transcript_segment_id == segments[0]['id']


def test_transcription_off_hears_but_archives_nothing(tmp_path):
    # M7 (hunt 2026-09-12): Voice.md says Transcription defaults OFF — the row
    # landed anyway. The conversational turn still gets the text; the DB doesn't.
    service, repo, session = _service(tmp_path, store=_store(False))

    result = _hear(service, session)

    assert result['status'] == 'transcribed'
    assert result['text'] == 'hello everyone'
    assert result['persisted'] is False
    assert repo.list_transcripts(session.session_id) == []
    assert result['observation'].transcript_segment_id == 0
    assert result['observation'].observation_id.startswith(f'voice:{session.session_id}:')


def test_no_settings_store_fails_closed(tmp_path):
    service, repo, session = _service(tmp_path, store=None)

    _hear(service, session)

    assert repo.list_transcripts(session.session_id) == []


def test_transcribe_only_mode_archives_regardless_of_toggle(tmp_path):
    # Archiving IS the mode: an operator who picked Transcribe-only meant it.
    service, repo, session = _service(tmp_path, mode='transcribe_only', store=_store(False))

    _hear(service, session)

    assert len(repo.list_transcripts(session.session_id)) == 1
