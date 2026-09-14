"""Wave F: attachment bytes ride the event payload (the image link, in-direction)."""

import base64
from types import SimpleNamespace

from plugins.discord.conversation.conversation_service import ConversationService
from plugins.discord.conversation.media_service import MediaService


class Bridge:
    def __init__(self):
        self.fetched = []

    def fetch_bytes(self, url):
        self.fetched.append(url)
        if url.endswith('.zip'):
            raise AssertionError('must not fetch non-images')
        return b'\x89PNG\r\n\x1a\n' + b'\x00' * 32, 'image/png'


def _service(bridge):
    svc = ConversationService.__new__(ConversationService)
    svc.media_service = MediaService(vision_bridge=bridge)
    return svc


def _trigger(*attachments):
    return SimpleNamespace(message_id='m1', channel_id='c1', account_name='bot', attachments=list(attachments))


def test_image_attachments_become_payload_images_gated_by_the_understanding_switch():
    bridge = Bridge()
    svc = _service(bridge)
    trigger = _trigger(
        {'url': 'https://cdn/a.png', 'filename': 'a.png', 'content_type': 'image/png'},
        {'url': 'https://cdn/b.zip', 'filename': 'b.zip', 'content_type': 'application/zip'},
    )
    on = SimpleNamespace(media=SimpleNamespace(enabled=True, image_understanding_enabled=True))
    images = svc._payload_images(trigger, on)
    assert len(images) == 1 and images[0]['media_type'] == 'image/png'
    assert base64.b64decode(images[0]['data']).startswith(b'\x89PNG')
    assert bridge.fetched == ['https://cdn/a.png']
    off = SimpleNamespace(media=SimpleNamespace(enabled=True, image_understanding_enabled=False))
    assert svc._payload_images(trigger, off) == []


def test_payload_images_are_capped_and_fetch_failures_are_skipped():
    class Flaky(Bridge):
        def fetch_bytes(self, url):
            if url.endswith('3.png'):
                raise RuntimeError('boom')
            return super().fetch_bytes(url)

    svc = _service(Flaky())
    trigger = _trigger(*[{'url': f'https://cdn/{i}.png', 'filename': f'{i}.png', 'content_type': 'image/png'} for i in range(7)])
    on = SimpleNamespace(media=SimpleNamespace(enabled=True, image_understanding_enabled=True))
    images = svc._payload_images(trigger, on)
    assert len(images) == ConversationService._PAYLOAD_IMAGE_MAX
