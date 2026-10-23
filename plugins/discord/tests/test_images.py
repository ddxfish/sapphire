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


def test_h5_payload_lane_reads_the_cache_and_never_fetches():
    """Broadsword H5: process_batch runs on the daemon loop — the payload lane is cache-only."""
    calls = []
    lane = im.ImageLane(fetch=lambda url: (calls.append(url) or (b'PNGDATA', 'image/png')))
    settings = SimpleNamespace(media=SimpleNamespace(images_in_enabled=True, max_images=4))
    obs = SimpleNamespace(attachments=[{'url': 'https://cdn/x.png', 'content_type': 'image/png', 'filename': 'x.png'},
                                       {'url': 'https://cdn/never.png', 'content_type': 'image/png', 'filename': 'n.png'}])
    assert lane.payload_images(obs, settings) == [] and calls == []          # nothing cached → nothing, no fetch
    assert lane.fetch(SimpleNamespace(attachments=obs.attachments[:1]), settings) == 1
    assert lane.fetch(SimpleNamespace(attachments=obs.attachments[:1]), settings) == 1   # cached: no refetch
    out = lane.payload_images(obs, settings)
    assert len(out) == 1 and out[0]['media_type'] == 'image/png' and calls == ['https://cdn/x.png']


def test_fetch_streams_with_a_cap_and_never_follows_redirects(monkeypatch):
    """Wave E: a body past the cap is refused, redirects are never followed."""
    from core import net
    calls = {}

    class Resp:
        status_code = 200
        headers = {'Content-Type': 'image/png'}

        def iter_content(self, n):
            for _ in range(200):
                yield b'\x89PNG\r\n\x1a\n' + b'\x00' * (n - 8)

    def fake_get(url, **kw):
        calls.update(kw)
        return Resp()
    monkeypatch.setattr(net, 'get', fake_get)
    try:
        im.fetch_bytes('https://cdn.example/x.png')
    except ValueError as exc:
        assert 'over' in str(exc)
    else:
        raise AssertionError('a body past the cap must be refused')
    assert calls['stream'] is True and calls['allow_redirects'] is False
    assert im.FETCH_MAX_BYTES == 10 * 1024 * 1024
