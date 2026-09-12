from plugins.discord.conversation.media_service import MediaService
from plugins.discord.models.media import MediaArtifact
from plugins.discord.storage.repositories.media import MediaRepository
from plugins.discord.storage.sqlite import SQLiteService


class FakeVisionBridge:
    def describe_media(self, source_url, *, media_kind, settings, filename='', content_type='', reply_llm_provider=''):
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
    def describe_media(self, source_url, *, media_kind, settings, filename='', content_type='', reply_llm_provider=''):
        raise RuntimeError('bridge exploded')


class CapturingVisionBridge:
    def __init__(self):
        self.reply_llm_provider = None

    def describe_media(self, source_url, *, media_kind, settings, filename='', content_type='', reply_llm_provider=''):
        self.reply_llm_provider = reply_llm_provider
        return {'summary': 'ok', 'entities': [], 'tone': '', 'ocr_text': '',
                'confidence': 0.9, 'source': 'vision'}


class StubSchedulerBridge:
    def __init__(self, provider='', model=''):
        self.provider = provider
        self.model = model
        self.calls = []

    def daemon_task_llm(self, event_name, account=None):
        self.calls.append((event_name, account))
        return self.provider, self.model


def _service(tmp_path):
    sqlite = SQLiteService(tmp_path / 'media.sqlite3')
    sqlite.start()
    return MediaService(media_repository=MediaRepository(sqlite), vision_bridge=FakeVisionBridge())


def test_detects_image_attachment():
    service = MediaService(media_repository=None, llm_bridge=None)
    attachments = [{'url': 'https://cdn/a.png', 'filename': 'cat.png', 'content_type': 'image/png'}]

    artifacts = service.detect_artifacts('m1', 'c1', 'alpha', attachments)

    assert len(artifacts) == 1
    assert artifacts[0].media_kind == 'image'


def test_store_and_interpret_round_trip(tmp_path):
    service = _service(tmp_path)
    artifact = MediaArtifact(
        message_id='m1',
        channel_id='c1',
        account_name='alpha',
        media_kind='image',
        source_url='https://cdn/a.png',
        filename='cat.png',
        content_type='image/png',
    )

    stored = service.store_and_interpret(artifact)

    assert stored.interpretation['summary'] == 'a cat picture'
    assert stored.interpretation['entities'] == ['cat']
    assert stored.interpretation['tone'] == 'cute'
    assert stored.interpretation['ocr_text'] == ''
    assert stored.interpretation['confidence'] == 0.9
    assert stored.interpretation['source'] == 'vision'
    loaded = service.media_repository.get_by_message('m1')
    assert len(loaded) == 1
    assert loaded[0]['media_kind'] == 'image'
    assert loaded[0]['interpretation']['summary'] == 'a cat picture'


def test_interpret_artifact_returns_metadata_schema_when_disabled():
    service = MediaService(media_repository=None, vision_bridge=None)
    artifact = MediaArtifact(
        message_id='m1',
        channel_id='c1',
        account_name='alpha',
        media_kind='image',
        source_url='https://cdn/a.png',
        filename='cat.png',
        content_type='image/png',
    )

    result = service.interpret_artifact(artifact, image_understanding_enabled=False)

    assert result == {
        'summary': 'Image attachment named cat.png',
        'entities': [],
        'tone': '',
        'ocr_text': '',
        'confidence': 0.3,
        'source': 'metadata',
    }


def test_interpret_artifact_uses_consistent_fallback_on_bridge_exception():
    service = MediaService(media_repository=None, vision_bridge=RaisingVisionBridge())
    artifact = MediaArtifact(
        message_id='m1',
        channel_id='c1',
        account_name='alpha',
        media_kind='image',
        source_url='https://cdn/a.png',
        filename='cat.png',
        content_type='image/png',
    )

    result = service.interpret_artifact(artifact)

    assert result == {
        'summary': 'Image attachment named cat.png',
        'entities': [],
        'tone': '',
        'ocr_text': '',
        'confidence': 0.2,
        'source': 'fallback',
        'fallback': {
            'reason': 'vision_error',
            'error_type': 'RuntimeError',
            'error_message': 'bridge exploded',
        },
    }


def _artifact():
    return MediaArtifact(
        message_id='m1',
        channel_id='c1',
        account_name='alpha',
        media_kind='image',
        source_url='https://cdn/a.png',
        filename='cat.png',
        content_type='image/png',
    )


def _settings(llm_primary='auto'):
    from types import SimpleNamespace
    return SimpleNamespace(
        media=SimpleNamespace(),
        cognitive=SimpleNamespace(llm_primary=llm_primary, llm_model=''),
    )


def test_interpret_auto_threads_daemon_task_provider():
    """'Daemon chooses' must resolve to the daemon TASK's pinned provider —
    the model her reply actually uses — not a fallback-order scan. 2026-08-06:
    server shipped an image to LM Studio while the daemon pinned Claude."""
    bridge = CapturingVisionBridge()
    sched = StubSchedulerBridge(provider='claude')
    service = MediaService(media_repository=None, vision_bridge=bridge, scheduler_bridge=sched)

    service.interpret_artifact(_artifact(), settings=_settings('auto'))

    assert bridge.reply_llm_provider == 'claude'
    assert sched.calls == [('discord_message', 'alpha')]


def test_interpret_pinned_cognitive_beats_daemon_task():
    bridge = CapturingVisionBridge()
    sched = StubSchedulerBridge(provider='claude')
    service = MediaService(media_repository=None, vision_bridge=bridge, scheduler_bridge=sched)

    service.interpret_artifact(_artifact(), settings=_settings('openai'))

    assert bridge.reply_llm_provider == 'openai'
    assert sched.calls == []


def test_interpret_auto_with_auto_daemon_stays_auto():
    bridge = CapturingVisionBridge()
    sched = StubSchedulerBridge(provider='')  # daemon task on auto / no task
    service = MediaService(media_repository=None, vision_bridge=bridge, scheduler_bridge=sched)

    service.interpret_artifact(_artifact(), settings=_settings('auto'))

    assert bridge.reply_llm_provider == 'auto'


def test_store_and_interpret_falls_back_when_vision_fails(tmp_path):
    sqlite = SQLiteService(tmp_path / 'fallback-media.sqlite3')
    sqlite.start()
    service = MediaService(
        media_repository=MediaRepository(sqlite),
        vision_bridge=RaisingVisionBridge(),
    )
    artifact = MediaArtifact(
        message_id='m1',
        channel_id='c1',
        account_name='alpha',
        media_kind='image',
        source_url='https://cdn/a.png',
        filename='cat.png',
        content_type='image/png',
    )

    stored = service.store_and_interpret(artifact, image_understanding_enabled=True)

    assert stored.interpretation == {
        'summary': 'Image attachment named cat.png',
        'entities': [],
        'tone': '',
        'ocr_text': '',
        'confidence': 0.2,
        'source': 'fallback',
        'fallback': {
            'reason': 'vision_error',
            'error_type': 'RuntimeError',
            'error_message': 'bridge exploded',
        },
    }
    loaded = service.media_repository.get_by_message('m1')
    assert len(loaded) == 1
    assert loaded[0]['interpretation'] == stored.interpretation
