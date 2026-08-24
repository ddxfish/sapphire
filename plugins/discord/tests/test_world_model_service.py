import time

from plugins.discord.cognition.world_model_service import WorldModelService
from plugins.discord.models.observations import TextMessageObservation
from plugins.discord.storage.repositories.channels import ChannelRepository
from plugins.discord.storage.repositories.messages import MessageRepository
from plugins.discord.storage.repositories.tasks import TaskRepository
from plugins.discord.storage.sqlite import SQLiteService


def _observation(**overrides):
    base = dict(
        observation_id='obs-1',
        account_name='alpha',
        guild_id='g1',
        guild_name='Guild',
        channel_id='c1',
        channel_name='general',
        author_id='u1',
        username='alice',
        display_name='Alice',
        message_id='m1',
        content='hello',
        clean_content='hello',
        created_at=time.time(),
        is_dm=False,
        mentioned=True,
        attachments=[],
    )
    base.update(overrides)
    return TextMessageObservation(**base)


def _service(tmp_path):
    sqlite = SQLiteService(tmp_path / 'world.sqlite3')
    sqlite.start()
    return WorldModelService(
        channel_repository=ChannelRepository(sqlite),
        message_repository=MessageRepository(sqlite),
        task_repository=TaskRepository(sqlite),
        trace_repository=None,
    )


def test_record_observation_updates_channel_and_user(tmp_path):
    service = _service(tmp_path)
    obs = _observation()

    service.record_text_observation(obs)

    channel = service.get_channel('c1')
    user = service.get_user('u1')
    assert channel['channel_id'] == 'c1'
    assert channel['name'] == 'general'
    assert user['user_id'] == 'u1'
    assert user['username'] == 'alice'


def test_create_task_persists(tmp_path):
    service = _service(tmp_path)

    task_id = service.create_task('alpha', 'follow_up', target_id='c1', reason='user asked')

    tasks = service.list_tasks('alpha', status='pending')
    assert len(tasks) == 1
    assert tasks[0]['id'] == task_id
    assert tasks[0]['task_type'] == 'follow_up'
