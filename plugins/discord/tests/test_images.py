"""S5: the one image lane — detection, the off-loop fetch cache, the on-loop payload."""
from types import SimpleNamespace

from plugins.discord.conversation import images as im


def _settings(enabled=True, limit=4):
    return SimpleNamespace(media=SimpleNamespace(images_in_enabled=enabled, max_images=limit))


def _att(url, ct='image/png', name='a.png'):
    return {'url': url, 'content_type': ct, 'filename': name}


def test_image_urls_by_type_or_extension():
    atts = [_att('https://cdn/a.png'), _att('https://cdn/b', ct='', name='b.JPG'), _att('https://cdn/c.zip', ct='application/zip', name='c.zip'),
            _att('', name='x.png'), _att('https://cdn/d.webp', ct='image/webp; charset=binary', name='d')]
    assert im.image_urls(atts) == ['https://cdn/a.png', 'https://cdn/b', 'https://cdn/d.webp']
    assert im.image_urls(None) == []


def test_fetch_is_gated_capped_and_tolerant():
    calls = []

    def fetch(url):
        calls.append(url)
        if url.endswith('bad.png'):
            raise RuntimeError('boom')
        return b'DATA', 'image/png'

    lane = im.ImageLane(fetch=fetch)
    obs = SimpleNamespace(attachments=[_att(f'https://cdn/{i}.png') for i in range(6)] + [_att('https://cdn/bad.png')])
    assert lane.fetch(obs, _settings(enabled=False)) == 0 and calls == []
    assert lane.fetch(obs, _settings(limit=2)) == 2 and calls == ['https://cdn/0.png', 'https://cdn/1.png']
    assert lane.fetch(SimpleNamespace(attachments=[_att('https://cdn/bad.png')]), _settings()) == 0
    assert lane.fetch(obs, None) == 0                                   # no settings = off


def test_payload_images_cache_only_and_capped():
    lane = im.ImageLane(fetch=lambda url: (b'DATA', 'image/jpeg'))
    obs = SimpleNamespace(attachments=[_att(f'https://cdn/{i}.jpg', ct='image/jpeg') for i in range(5)])
    assert lane.payload_images(obs, _settings()) == []
    lane.fetch(obs, _settings(limit=10))
    out = lane.payload_images(obs, _settings(limit=3))
    assert len(out) == 3 and out[0] == {'data': 'REFUQQ==', 'media_type': 'image/jpeg'}
    assert lane.payload_images(obs, _settings(enabled=False)) == []


def test_cache_is_bounded():
    lane = im.ImageLane(fetch=lambda url: (b'x', 'image/png'))
    for i in range(im.CACHE_SIZE + 5):
        lane.fetch(SimpleNamespace(attachments=[_att(f'https://cdn/{i}.png')]), _settings())
    assert len(lane._cache) == im.CACHE_SIZE and lane.cached('https://cdn/0.png') is None
