from plugins.discord.memory.profile_service import ProfileService
from plugins.discord.storage.repositories.profiles import ProfileRepository
from plugins.discord.storage.sqlite import SQLiteService


def _service(tmp_path):
    sqlite = SQLiteService(tmp_path / 'profiles.sqlite3')
    sqlite.start()
    return ProfileService(profile_repository=ProfileRepository(sqlite))


def test_remember_fact_and_recall_context(tmp_path):
    service = _service(tmp_path)

    service.remember_fact('alpha', 'u1', 'Prefers concise replies', source='explicit', confidence=1.0)
    service.record_interaction('alpha', 'u1', username='alice', positive=True)

    context = service.build_context('alpha', 'u1')

    assert any('concise' in fact['content'] for fact in context['facts'])
    assert context['relationship']['fondness'] > 0.5


def test_forget_user_removes_profile_and_facts(tmp_path):
    service = _service(tmp_path)
    service.remember_fact('alpha', 'u1', 'secret', source='explicit')
    service.record_interaction('alpha', 'u1', username='alice')

    service.forget_user('alpha', 'u1')
    context = service.build_context('alpha', 'u1')

    assert context['facts'] == []
    assert context['relationship']['familiarity'] == 0.0
