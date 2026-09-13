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


def test_close_stale_sessions_closes_every_active_row(tmp_path):
    # Crash/kill leftovers listed as live forever; boot + shutdown sweep them.
    service, repo = _service(tmp_path)
    a = service.start_session('alpha', 'g1', 'vc1')
    b = service.start_session('beta', 'g1', 'vc2')
    closed_already = service.start_session('alpha', 'g1', 'vc3')
    service.close_session(closed_already.session_id)

    assert repo.close_stale_sessions() == 2
    assert repo.get_session(a.session_id).state == 'closed'
    assert repo.get_session(a.session_id).ended_at > 0
    assert repo.get_session(b.session_id).health == 'disconnected'
    assert service.list_active('alpha') == []
    assert repo.close_stale_sessions() == 0


def test_close_stale_sessions_can_scope_to_one_account(tmp_path):
    service, repo = _service(tmp_path)
    service.start_session('alpha', 'g1', 'vc1')
    b = service.start_session('beta', 'g1', 'vc2')

    assert repo.close_stale_sessions('alpha') == 1
    assert repo.get_session(b.session_id).state == 'active'
