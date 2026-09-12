from plugins.discord.memory.profile_distill_service import ProfileDistillService
from plugins.discord.memory.profile_service import ProfileService
from plugins.discord.storage.repositories.profiles import ProfileRepository
from plugins.discord.storage.sqlite import SQLiteService


class FakeLlmBridge:
    def distill_profile(self, messages):
        return {
            'summary': 'Alice is friendly and technical.',
            'facts': [{'content': 'works on deployments', 'confidence': 0.8}],
            'disposition': {'fondness': 0.1},
        }


def _services(tmp_path):
    sqlite = SQLiteService(tmp_path / 'distill.sqlite3')
    sqlite.start()
    profile_repo = ProfileRepository(sqlite)
    profile_service = ProfileService(profile_repository=profile_repo)
    distill = ProfileDistillService(
        profile_repository=profile_repo,
        profile_service=profile_service,
        llm_bridge=FakeLlmBridge(),
    )
    return profile_service, distill


def test_buffer_and_distill_merges_facts(tmp_path):
    profile_service, distill = _services(tmp_path)

    profile_service.buffer_message('alpha', 'u1', 'I deploy every Friday')
    result = distill.run_pending('alpha', 'u1')

    assert result['status'] == 'distilled'
    context = profile_service.build_context('alpha', 'u1')
    assert 'deploy' in context['summary'].lower() or any('deploy' in f['content'] for f in context['facts'])


def test_distill_gracefully_fails_without_llm(tmp_path):
    sqlite = SQLiteService(tmp_path / 'distill2.sqlite3')
    sqlite.start()
    profile_repo = ProfileRepository(sqlite)
    profile_service = ProfileService(profile_repository=profile_repo)

    class BrokenBridge:
        def distill_profile(self, messages):
            raise RuntimeError('llm unavailable')

    distill = ProfileDistillService(
        profile_repository=profile_repo,
        profile_service=profile_service,
        llm_bridge=BrokenBridge(),
    )
    profile_service.buffer_message('alpha', 'u1', 'hello')
    result = distill.run_pending('alpha', 'u1')

    assert result['status'] == 'skipped'
