import asyncio
import json
from types import SimpleNamespace

from plugins.discord.cognition.world_model_service import WorldModelService
from plugins.discord.conversation.media_service import MediaService
from plugins.discord.storage.repositories.channels import ChannelRepository
from plugins.discord.storage.repositories.media import MediaRepository
from plugins.discord.storage.repositories.messages import MessageRepository
from plugins.discord.storage.repositories.tasks import TaskRepository
from plugins.discord.storage.sqlite import SQLiteService
from plugins.discord.models.observations import TextMessageObservation, TypingObservation
from plugins.discord.transport.discord_event_adapter import DiscordEventAdapter


class FakeMessageRepo:
    def __init__(self):
        self.saved = []

    def save_message(self, observation):
        self.saved.append(observation)


class FakeTraceRepo:
    def __init__(self):
        self.traces = []

    def record_trace(self, trace_type, summary, detail=None):
        self.traces.append((trace_type, summary, detail or {}))


class FakeAuthor:
    def __init__(self, user_id, name, display_name, bot=False):
        self.id = user_id
        self.name = name
        self.display_name = display_name
        self.bot = bot


class FakeChannel:
    def __init__(self, channel_id, name):
        self.id = channel_id
        self.name = name


class FakeGuild:
    def __init__(self, guild_id, name):
        self.id = guild_id
        self.name = name


class FakeVisionBridge:
    def describe_media(self, source_url, *, media_kind, settings, filename='', content_type=''):
        assert source_url == 'https://cdn/a.png'
        assert media_kind == 'image'
        assert filename == 'cat.png'
        assert content_type == 'image/png'
        return {
            'summary': 'a cat picture',
            'entities': ['cat'],
            'tone': 'cute',
            'ocr_text': '',
            'confidence': 0.9,
            'source': 'vision',
        }


class RaisingVisionBridge:
    def describe_media(self, source_url, *, media_kind, settings, filename='', content_type=''):
        raise RuntimeError('bridge exploded')


class FakeSettingsStore:
    def resolve(self, **_kwargs):
        return SimpleNamespace(media=SimpleNamespace(enabled=True, image_understanding_enabled=True))


class FakeDisabledSettingsStore:
    def resolve(self, **_kwargs):
        return SimpleNamespace(media=SimpleNamespace(enabled=True, image_understanding_enabled=False))


class FakeMediaGloballyDisabledSettingsStore:
    def resolve(self, **_kwargs):
        return SimpleNamespace(media=SimpleNamespace(enabled=False, image_understanding_enabled=True))


def _world(tmp_path):
    sqlite = SQLiteService(tmp_path / 'event-adapter.sqlite3')
    sqlite.start()
    return WorldModelService(
        channel_repository=ChannelRepository(sqlite),
        message_repository=MessageRepository(sqlite),
        task_repository=TaskRepository(sqlite),
        trace_repository=None,
    )


def _message_with_image():
    return SimpleNamespace(
        id=111,
        content='check this out',
        clean_content='check this out',
        author=FakeAuthor(7, 'alice', 'Alice'),
        channel=FakeChannel(22, 'general'),
        guild=FakeGuild(33, 'Guild'),
        attachments=[
            SimpleNamespace(
                url='https://cdn/a.png',
                filename='cat.png',
                content_type='image/png',
            )
        ],
        mentions=[],
    )


def _trace_details(trace_repo, trace_type):
    return [detail for current_type, _summary, detail in trace_repo.traces if current_type == trace_type]


def test_adapt_guild_message_and_persist():
    adapter = DiscordEventAdapter(message_repository=FakeMessageRepo(), trace_repository=FakeTraceRepo())
    message = SimpleNamespace(
        id=111,
        content='hello there',
        clean_content='hello there',
        author=FakeAuthor(7, 'alice', 'Alice'),
        channel=FakeChannel(22, 'general'),
        guild=FakeGuild(33, 'Guild'),
        attachments=[],
        mentions=[],
    )

    obs = adapter.adapt_message_event('alpha', 99, message)

    assert isinstance(obs, TextMessageObservation)
    assert obs.account_name == 'alpha'
    assert obs.guild_id == '33'
    assert obs.channel_name == 'general'
    assert adapter.message_repository.saved[0].message_id == '111'


def test_ignore_self_authored_message():
    adapter = DiscordEventAdapter(message_repository=FakeMessageRepo(), trace_repository=FakeTraceRepo())
    message = SimpleNamespace(
        id=111,
        content='self',
        clean_content='self',
        author=FakeAuthor(99, 'bot', 'Bot'),
        channel=FakeChannel(22, 'general'),
        guild=FakeGuild(33, 'Guild'),
        attachments=[],
        mentions=[],
    )

    obs = adapter.adapt_message_event('alpha', 99, message)

    assert obs is None
    assert adapter.message_repository.saved == []


def test_adapt_dm_typing_event():
    adapter = DiscordEventAdapter(message_repository=FakeMessageRepo(), trace_repository=FakeTraceRepo())
    user = FakeAuthor(7, 'alice', 'Alice')
    channel = FakeChannel(44, 'Direct Message')

    obs = asyncio.run(adapter.adapt_typing_event('alpha', 99, channel, user, when=None))

    assert isinstance(obs, TypingObservation)
    assert obs.is_dm is True
    assert obs.guild_id == ''


def test_adapt_message_records_media_observation(tmp_path):
    world = _world(tmp_path)
    media_service = MediaService(
        media_repository=MediaRepository(world.channel_repository.sqlite_service),
        vision_bridge=FakeVisionBridge(),
    )
    adapter = DiscordEventAdapter(
        message_repository=FakeMessageRepo(),
        trace_repository=FakeTraceRepo(),
        world_model_service=world,
        media_service=media_service,
        settings_store=FakeSettingsStore(),
    )
    message = _message_with_image()

    obs = adapter.adapt_message_event('alpha', 99, message)

    assert isinstance(obs, TextMessageObservation)
    stored = media_service.media_repository.get_by_message('111')
    assert len(stored) == 1
    assert stored[0]['interpretation']['summary'] == 'a cat picture'

    rows = world.channel_repository.sqlite_service.connection().execute(
        "SELECT payload_json FROM observations WHERE observation_type = 'media_observation'"
    ).fetchall()

    assert len(rows) == 1
    assert json.loads(rows[0]['payload_json']) == {
        'message_id': '111',
        'author_id': '7',
        'account_name': 'alpha',
        'media_kind': 'image',
        'summary': 'a cat picture',
        'entities': ['cat'],
        'ocr_text': '',
        'confidence': 0.9,
        'source': 'vision',
    }


def test_adapt_message_records_media_detected_trace(tmp_path):
    sqlite = SQLiteService(tmp_path / 'detected-trace.sqlite3')
    sqlite.start()
    trace_repo = FakeTraceRepo()
    adapter = DiscordEventAdapter(
        message_repository=FakeMessageRepo(),
        trace_repository=trace_repo,
        media_service=MediaService(
            media_repository=MediaRepository(sqlite),
            vision_bridge=FakeVisionBridge(),
        ),
        settings_store=FakeSettingsStore(),
    )

    obs = adapter.adapt_message_event('alpha', 99, _message_with_image())

    assert isinstance(obs, TextMessageObservation)
    assert _trace_details(trace_repo, 'media_detected') == [{
        'message_id': '111',
        'channel_id': '22',
        'media_kind': 'image',
        'filename': 'cat.png',
    }]


def test_adapt_message_records_media_fallback_trace_when_image_understanding_disabled(tmp_path):
    sqlite = SQLiteService(tmp_path / 'disabled-fallback-trace.sqlite3')
    sqlite.start()
    trace_repo = FakeTraceRepo()
    media_service = MediaService(
        media_repository=MediaRepository(sqlite),
        vision_bridge=FakeVisionBridge(),
    )
    adapter = DiscordEventAdapter(
        message_repository=FakeMessageRepo(),
        trace_repository=trace_repo,
        media_service=media_service,
        settings_store=FakeDisabledSettingsStore(),
    )

    obs = adapter.adapt_message_event('alpha', 99, _message_with_image())

    assert isinstance(obs, TextMessageObservation)
    assert _trace_details(trace_repo, 'media_fallback_used') == [{
        'message_id': '111',
        'channel_id': '22',
        'media_kind': 'image',
        'source': 'metadata',
        'reason': 'image_understanding_disabled',
    }]


def test_adapt_message_records_media_failure_and_fallback_traces_when_vision_raises(tmp_path):
    sqlite = SQLiteService(tmp_path / 'failed-fallback-trace.sqlite3')
    sqlite.start()
    trace_repo = FakeTraceRepo()
    media_service = MediaService(
        media_repository=MediaRepository(sqlite),
        vision_bridge=RaisingVisionBridge(),
    )
    adapter = DiscordEventAdapter(
        message_repository=FakeMessageRepo(),
        trace_repository=trace_repo,
        media_service=media_service,
        settings_store=FakeSettingsStore(),
    )

    obs = adapter.adapt_message_event('alpha', 99, _message_with_image())

    assert isinstance(obs, TextMessageObservation)
    assert _trace_details(trace_repo, 'media_fallback_used') == [{
        'message_id': '111',
        'channel_id': '22',
        'media_kind': 'image',
        'source': 'fallback',
        'reason': 'vision_error',
    }]
    assert _trace_details(trace_repo, 'media_interpretation_failed') == [{
        'message_id': '111',
        'channel_id': '22',
        'media_kind': 'image',
        'error_type': 'RuntimeError',
        'error_message': 'bridge exploded',
    }]


def test_adapt_message_skips_media_pipeline_when_media_disabled(tmp_path):
    world = _world(tmp_path)
    sqlite = SQLiteService(tmp_path / 'media-disabled.sqlite3')
    sqlite.start()
    trace_repo = FakeTraceRepo()
    media_service = MediaService(
        media_repository=MediaRepository(sqlite),
        vision_bridge=FakeVisionBridge(),
    )
    adapter = DiscordEventAdapter(
        message_repository=FakeMessageRepo(),
        trace_repository=trace_repo,
        world_model_service=world,
        media_service=media_service,
        settings_store=FakeMediaGloballyDisabledSettingsStore(),
    )

    obs = adapter.adapt_message_event('alpha', 99, _message_with_image())

    assert isinstance(obs, TextMessageObservation)
    assert media_service.media_repository.get_by_message('111') == []
    rows = world.channel_repository.sqlite_service.connection().execute(
        "SELECT payload_json FROM observations WHERE observation_type = 'media_observation'"
    ).fetchall()
    assert rows == []
    assert _trace_details(trace_repo, 'media_detected') == []
    assert _trace_details(trace_repo, 'media_fallback_used') == []
    assert _trace_details(trace_repo, 'media_interpretation_failed') == []
