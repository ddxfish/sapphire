"""Wave F: attachment bytes ride the event payload (the image link, in-direction).

Broadsword H5 (2026-09-18): the payload lane no longer fetches — it reads the
bytes the caption lane already pulled off-loop (VisionBridge's cache). These
tests drive the REAL bridge with a fake raw fetch so the contract is the one
the daemon runs."""

import base64
from types import SimpleNamespace

from plugins.discord.conversation.conversation_service import ConversationService
from plugins.discord.conversation.media_service import MediaService
from plugins.discord.vision.vision_bridge import VisionBridge


class RawFetch:
    def __init__(self, failing=()):
        self.fetched = []
        self.failing = set(failing)

    def __call__(self, url):
        self.fetched.append(url)
        if url.endswith('.zip'):
            raise AssertionError('must not fetch non-images')
        if any(url.endswith(f) for f in self.failing):
            raise RuntimeError('boom')
        return b'\x89PNG\r\n\x1a\n' + b'\x00' * 32, 'image/png'


def _service(bridge):
    svc = ConversationService.__new__(ConversationService)
    svc.media_service = MediaService(vision_bridge=bridge)
    return svc


def _trigger(*attachments):
    return SimpleNamespace(message_id='m1', channel_id='c1', account_name='bot', attachments=list(attachments))


def _caption_lane(bridge, urls):
    """What interpret_media does off-loop: fetch each attachment once."""
    for u in urls:
        try:
            bridge.fetch_bytes(u)
        except Exception:
            pass


def test_image_attachments_become_payload_images_gated_by_the_understanding_switch():
    raw = RawFetch()
    bridge = VisionBridge(fetch_bytes=raw)
    svc = _service(bridge)
    trigger = _trigger(
        {'url': 'https://cdn/a.png', 'filename': 'a.png', 'content_type': 'image/png'},
        {'url': 'https://cdn/b.zip', 'filename': 'b.zip', 'content_type': 'application/zip'},
    )
    _caption_lane(bridge, ['https://cdn/a.png'])
    on = SimpleNamespace(media=SimpleNamespace(enabled=True, image_understanding_enabled=True))
    images = svc._payload_images(trigger, on)
    assert len(images) == 1 and images[0]['media_type'] == 'image/png'
    assert base64.b64decode(images[0]['data']).startswith(b'\x89PNG')
    assert raw.fetched == ['https://cdn/a.png'], 'the payload lane fetched nothing itself'
    off = SimpleNamespace(media=SimpleNamespace(enabled=True, image_understanding_enabled=False))
    assert svc._payload_images(trigger, off) == []


def test_payload_images_are_capped_and_uncached_ones_are_skipped():
    raw = RawFetch(failing=('3.png',))
    bridge = VisionBridge(fetch_bytes=raw)
    svc = _service(bridge)
    urls = [f'https://cdn/{i}.png' for i in range(7)]
    trigger = _trigger(*[{'url': u, 'filename': u.rsplit('/', 1)[1], 'content_type': 'image/png'} for u in urls])
    _caption_lane(bridge, urls)          # 3.png fails → never cached → skipped below
    on = SimpleNamespace(media=SimpleNamespace(enabled=True, image_understanding_enabled=True))
    before = list(raw.fetched)
    images = svc._payload_images(trigger, on)
    assert len(images) == ConversationService._PAYLOAD_IMAGE_MAX
    assert raw.fetched == before, 'the payload lane never fetches (it runs on the daemon loop)'


def test_payload_lane_never_fetches_what_the_caption_lane_did_not():
    raw = RawFetch()
    bridge = VisionBridge(fetch_bytes=raw)
    svc = _service(bridge)
    trigger = _trigger({'url': 'https://cdn/cold.png', 'filename': 'cold.png', 'content_type': 'image/png'})
    on = SimpleNamespace(media=SimpleNamespace(enabled=True, image_understanding_enabled=True))
    assert svc._payload_images(trigger, on) == []
    assert raw.fetched == []
