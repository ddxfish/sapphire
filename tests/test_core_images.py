"""core.images — the one image primitive (2026-09-09 image-tools rebuild).

for_chat = the one resize, contact_sheet = the one grid, result = the one
contract, resolve = the one door (img: / doc: / abs path / URL). Net is
mocked at core.net (rule 2: server fetches ride the facade).
"""
import base64
import io

import pytest
from PIL import Image

import core.images as ci


def _png(w=64, h=32, color=(255, 0, 0)):
    buf = io.BytesIO()
    Image.new('RGB', (w, h), color).save(buf, 'PNG')
    return buf.getvalue()


def _jpeg_exif6(w=100, h=50):
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90° CW on display
    buf = io.BytesIO()
    Image.new('RGB', (w, h), (0, 255, 0)).save(buf, 'JPEG', exif=exif)
    return buf.getvalue()


def _im(raw):
    return Image.open(io.BytesIO(raw))


# ── shaping ──────────────────────────────────────────────────────────────────

def test_for_chat_bounds_and_jpeg_rgb():
    im = _im(ci.for_chat(_png(4000, 1000), max_px=1536))
    assert im.format == 'JPEG' and im.mode == 'RGB' and max(im.size) == 1536


def test_for_chat_exif_upright():
    assert _im(ci.for_chat(_jpeg_exif6(100, 50))).size == (50, 100)


def test_for_chat_rejects_junk():
    with pytest.raises(ci.ImageError, match='not an image'):
        ci.for_chat(b'<html>nope</html>')


def test_contact_sheet_dims():
    im = _im(ci.contact_sheet([_png()] * 5, cell=100))
    assert im.size == (300, 200) and im.format == 'JPEG'   # ceil(sqrt(5))=3 cols, 2 rows


def test_contact_sheet_keeps_numbering_on_bad_entry():
    assert _im(ci.contact_sheet([_png(), b'junk', _png()], cell=100)).size == (200, 200)


def test_result_contract():
    r = ci.result('hi', [_png()], display_only=True)
    img = r['images'][0]
    assert r['text'] == 'hi'
    assert img['media_type'] == 'image/png' and img['display_only'] is True
    assert base64.b64decode(img['data']) == _png()
    assert ci.result('x') == {'text': 'x', 'images': []}


def test_proxied_encodes_whole_url():
    u = ci.proxied('https://ts1.mm.bing.net/th?id=OIP.abc&pid=Api')
    assert u.startswith('https://external-content.duckduckgo.com/iu/?u='
                        'https%3A%2F%2Fts1.mm.bing.net%2Fth%3Fid%3DOIP.abc%26pid%3DApi')
    assert u.endswith('&f=1')


# ── resolve: file lane ───────────────────────────────────────────────────────

def test_resolve_file(tmp_path):
    p = tmp_path / 'a.png'
    p.write_bytes(_png())
    r = ci.resolve(str(p))
    assert (r.origin, r.media_type, r.label, r.size) == ('file', 'image/png', 'a.png', (64, 32))


def test_resolve_file_errors(tmp_path):
    with pytest.raises(ci.ImageError, match='not a handle'):
        ci.resolve('relative/x.png')
    with pytest.raises(ci.ImageError, match='no file'):
        ci.resolve(str(tmp_path / 'missing.png'))
    t = tmp_path / 't.txt'
    t.write_text('hello')
    with pytest.raises(ci.ImageError, match='not an image'):
        ci.resolve(str(t))
    with pytest.raises(ci.ImageError, match='no image given'):
        ci.resolve('')
    with pytest.raises(ci.ImageError):
        ci.resolve(None)


# ── resolve: URL lane (core.net mocked) ──────────────────────────────────────

class _Resp:
    def __init__(self, body, status=200, ct='image/png'):
        self.body, self.status, self.headers = body, status, {'content-type': ct}

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f'HTTP {self.status}')

    def iter_content(self, n):
        for i in range(0, len(self.body), n):
            yield self.body[i:i + n]


def test_resolve_url_rides_facade_browser_profile(monkeypatch):
    calls = []

    def fake_get(url, **kw):
        calls.append((url, kw))
        return _Resp(_png())

    monkeypatch.setattr('core.net.get', fake_get)
    r = ci.resolve('https://x.y/pic.png')
    assert r.origin == 'web' and r.label == 'pic.png' and r.media_type == 'image/png'
    assert calls[0][0] == 'https://x.y/pic.png'
    assert calls[0][1]['profile'] == 'browser' and calls[0][1]['stream'] is True


def test_resolve_url_cap(monkeypatch):
    monkeypatch.setattr('core.net.get', lambda url, **kw: _Resp(b'x' * (ci.MAX_FETCH + 1)))
    with pytest.raises(ci.ImageError, match='over 20MB'):
        ci.resolve('https://x/big')


def test_resolve_url_not_an_image(monkeypatch):
    monkeypatch.setattr('core.net.get', lambda url, **kw: _Resp(b'<html>', ct='text/html'))
    with pytest.raises(ci.ImageError, match='text/html'):
        ci.resolve('https://x/page')


def test_resolve_url_http_error(monkeypatch):
    monkeypatch.setattr('core.net.get', lambda url, **kw: _Resp(b'', status=403))
    with pytest.raises(ci.ImageError, match="couldn't fetch"):
        ci.resolve('https://x/forbidden.jpg')


# ── resolve: img: lane ───────────────────────────────────────────────────────

class _SM:
    def __init__(self, rows):
        self.rows = rows

    def get_tool_image(self, i):
        return self.rows.get(i)

    def last_tool_image_id(self, chat_name=None):
        return max(self.rows) if self.rows else None


def test_resolve_img_from_db(monkeypatch):
    monkeypatch.setattr(ci, '_session_manager', lambda: _SM({'ab12.png': (_png(), 'image/png')}))
    r = ci.resolve('img:ab12.png')
    assert r.origin == 'chat' and r.label == 'img:ab12.png' and r.media_type == 'image/png'
    assert ci.last_image_id() == 'ab12.png'


def test_resolve_img_disk_fallback_and_traversal(monkeypatch, tmp_path):
    monkeypatch.setattr(ci, '_session_manager', lambda: _SM({}))
    monkeypatch.setattr(ci, '_DISK', tmp_path)
    (tmp_path / 'cd34.jpg').write_bytes(_png())
    assert ci.resolve('img:cd34.jpg').origin == 'chat'
    with pytest.raises(ci.ImageError, match='no image img:zz'):
        ci.resolve('img:zz')
    (tmp_path.parent / 'secret.png').write_bytes(_png())
    with pytest.raises(ci.ImageError):
        ci.resolve('img:../secret.png')


def test_resolve_img_without_system(monkeypatch, tmp_path):
    def boom():
        raise RuntimeError('no system')
    monkeypatch.setattr(ci, '_session_manager', boom)
    monkeypatch.setattr(ci, '_DISK', tmp_path)
    with pytest.raises(ci.ImageError, match='no image img:'):
        ci.resolve('img:none.png')
    assert ci.last_image_id() is None


# ── resolve: doc: lane ───────────────────────────────────────────────────────

def test_resolve_doc(monkeypatch, tmp_path):
    p = tmp_path / 'cat.png'
    p.write_bytes(_png())
    seen = {}

    class Lib:
        @staticmethod
        def image_source(scope, n, private_key=None):
            seen.update(scope=scope, n=n, pk=private_key)
            return p, '[doc 7] Cat photo'

    monkeypatch.setattr(ci, '_scope', lambda: 'krem')
    monkeypatch.setattr(ci, '_library', lambda: Lib)
    r = ci.resolve('doc:7', private_key='word')
    assert r.origin == 'library' and r.label == '[doc 7] Cat photo'
    assert seen == dict(scope='krem', n=7, pk='word')


def test_resolve_doc_errors(monkeypatch):
    monkeypatch.setattr(ci, '_scope', lambda: None)
    with pytest.raises(ci.ImageError, match='memory is disabled'):
        ci.resolve('doc:7')
    with pytest.raises(ci.ImageError, match='not a library id'):
        ci.resolve('doc:abc')
