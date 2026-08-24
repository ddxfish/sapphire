import json

from plugins.discord.cognition.world_model_service import WorldModelService
from plugins.discord.storage.repositories.channels import ChannelRepository
from plugins.discord.storage.repositories.messages import MessageRepository
from plugins.discord.storage.repositories.tasks import TaskRepository
from plugins.discord.storage.sqlite import SQLiteService


def _service(tmp_path):
    sqlite = SQLiteService(tmp_path / 'world-media.sqlite3')
    sqlite.start()
    return WorldModelService(
        channel_repository=ChannelRepository(sqlite),
        message_repository=MessageRepository(sqlite),
        task_repository=TaskRepository(sqlite),
        trace_repository=None,
    )


def test_record_media_observation_persists(tmp_path):
    service = _service(tmp_path)

    service.record_media_observation(
        account_name='alpha',
        channel_id='c1',
        message_id='m1',
        author_id='u1',
        media_kind='image',
        interpretation={
            'summary': 'A fox meme with hydrate text.',
            'entities': ['fox'],
            'ocr_text': 'hydrate',
            'confidence': 0.81,
            'source': 'vision',
        },
    )

    rows = service.channel_repository.sqlite_service.connection().execute(
        "SELECT observation_type, payload_json FROM observations WHERE observation_type = 'media_observation'"
    ).fetchall()

    assert len(rows) == 1
    assert rows[0]['observation_type'] == 'media_observation'
    assert json.loads(rows[0]['payload_json']) == {
        'message_id': 'm1',
        'author_id': 'u1',
        'account_name': 'alpha',
        'media_kind': 'image',
        'summary': 'A fox meme with hydrate text.',
        'entities': ['fox'],
        'ocr_text': 'hydrate',
        'confidence': 0.81,
        'source': 'vision',
    }
