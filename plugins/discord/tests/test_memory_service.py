import time

from plugins.discord.memory.memory_service import MemoryService
from plugins.discord.storage.repositories.memory import MemoryRepository
from plugins.discord.storage.repositories.messages import MessageRepository
from plugins.discord.storage.sqlite import SQLiteService


def _service(tmp_path):
    sqlite = SQLiteService(tmp_path / 'memory.sqlite3')
    sqlite.start()
    return MemoryService(
        memory_repository=MemoryRepository(sqlite),
        message_repository=MessageRepository(sqlite),
    )


def test_pinned_memory_round_trip(tmp_path):
    service = _service(tmp_path)

    service.pin_memory('alpha', 'g1', 'c1', 'u1', 'alice', 'likes tea')
    results = service.get_pinned('alpha', guild_id='g1', limit=5)

    assert len(results) == 1
    assert results[0]['content'] == 'likes tea'


def test_recall_includes_recent_and_pinned(tmp_path):
    service = _service(tmp_path)
    msg_repo = service.message_repository
    now = time.time()
    from plugins.discord.models.observations import TextMessageObservation

    obs = TextMessageObservation(
        observation_id='obs-1', account_name='alpha', guild_id='g1', guild_name='Guild',
        channel_id='c1', channel_name='general', author_id='u1', username='alice',
        display_name='Alice', message_id='m1', content='deploy tomorrow',
        clean_content='deploy tomorrow', created_at=now, is_dm=False, mentioned=False,
        attachments=[],
    )
    msg_repo.save_message(obs)
    service.pin_memory('alpha', 'g1', 'c1', 'u1', 'alice', 'deployment fan')

    recalled = service.recall('alpha', 'g1', 'c1', 'deploy', limit=5)

    contents = {item['content'] for item in recalled}
    assert 'deploy tomorrow' in contents or any('deploy' in c for c in contents)
    assert 'deployment fan' in contents
