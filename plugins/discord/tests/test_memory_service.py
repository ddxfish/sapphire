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


def test_dm_pins_are_scoped_to_their_own_dm(tmp_path):
    # H15 (hunt 2026-09-12): DMs share guild_id '' — every DM user's pins pooled
    # into one bucket and user A's private /remember rode into user B's prompt.
    service = _service(tmp_path)
    service.pin_memory('alpha', '', 'dm-a', 'ua', 'alice', 'secret: moving to Oslo')
    service.pin_memory('alpha', '', 'dm-b', 'ub', 'bob', 'secret: proposing friday')

    for_a = service.get_pinned('alpha', guild_id='', channel_id='dm-a', limit=5)
    assert [row['content'] for row in for_a] == ['secret: moving to Oslo']

    recalled_b = service.recall('alpha', '', 'dm-b', 'secret', limit=5)
    assert [item['content'] for item in recalled_b] == ['secret: proposing friday']

    # In a server, pins stay guild-wide: a pin from #general shows in #random.
    service.pin_memory('alpha', 'g1', 'general', 'ua', 'alice', 'guild lore')
    in_random = service.get_pinned('alpha', guild_id='g1', channel_id='random', limit=5)
    assert [row['content'] for row in in_random] == ['guild lore']


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
