from plugins.discord.conversation.media_service import MediaService
from plugins.discord.models.media import MediaArtifact
from plugins.discord.storage.repositories.media import MediaRepository
from plugins.discord.storage.sqlite import SQLiteService


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
