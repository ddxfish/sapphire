import time

from plugins.discord.models.voice import VoiceMode, VoiceSession
from plugins.discord.storage.repositories.voice_sessions import VoiceSessionRepository
from plugins.discord.storage.sqlite import SQLiteService
from plugins.discord.voice.voice_session_service import VoiceSessionService


def _service(tmp_path):
    sqlite = SQLiteService(tmp_path / 'voice.sqlite3')
    sqlite.start()
    repo = VoiceSessionRepository(sqlite)
    return VoiceSessionService(voice_session_repository=repo), repo


def test_create_and_close_session(tmp_path):
    service, repo = _service(tmp_path)

    session = service.start_session('alpha', 'g1', 'vc1', mode=VoiceMode.TRANSCRIBE_ONLY)

    assert session.account_name == 'alpha'
    assert session.channel_id == 'vc1'
    assert session.mode == VoiceMode.TRANSCRIBE_ONLY
    active = service.get_active_session('alpha', 'vc1')
    assert active is not None
    closed = service.close_session(session.session_id)
    assert closed.ended_at > 0
    assert service.get_active_session('alpha', 'vc1') is None


def test_update_participants(tmp_path):
    service, _repo = _service(tmp_path)
    session = service.start_session('alpha', 'g1', 'vc1')

    updated = service.update_participants(session.session_id, ['u1', 'u2'])

    assert updated.participants == ['u1', 'u2']
