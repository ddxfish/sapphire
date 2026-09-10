"""web_view_images (born web_search_images 2026-09-09; renamed + upgraded
2026-09-10, tmp/image-upgrade.md §11): Bing-backed image search through
core.net with page + safesearch, every hit's thumbnail STASHED as an img:
handle (core.images.stash — ours, vault-aware, offline), GALLERY marker v3
({title, items[{handle, full, title, page}]}) for the user's numbered row,
view=true (the default) = pixels for the model from the SAME bytes (the full
image for one result, else one numbered contact sheet), url= views one image.
Plus get_website show_image_urls. Parser runs on
tests/fixtures/bing_images_sample.html — 5 real anchors plus a duplicate and
two broken ones.
"""
import base64
import io
import itertools
import json
import re
import types
from pathlib import Path

import pytest
import requests
from PIL import Image

import core.images as ci
import functions.web as web

FIXTURE = Path(__file__).parent / 'fixtures' / 'bing_images_sample.html'
MARKER = re.compile(r'<!--GALLERY:(\{.*\})-->', re.S)


def _png(w=64, h=32):
    buf = io.BytesIO()
    Image.new('RGB', (w, h), (30, 200, 60)).save(buf, 'PNG')
    return buf.getvalue()


def _results(n=3):
    return [{'full': f'https://host{i}.example/{i}.jpg', 'thumb': f'https://ts.bing.example/th?id={i}',
             'page': f'https://host{i}.example/page', 'title': f'Pic {i}', 'dims': '800x600'}
            for i in range(1, n + 1)]


@pytest.fixture
def stashed(monkeypatch):
    """core.images.stash → deterministic handles; no store is touched in tests."""
    n = itertools.count(1)
    calls = []

    def fake(raw, media_type=None, *, visible=False, chat_name=None):
        calls.append((raw, visible))
        return f"img:h{next(n)}.jpg"
    monkeypatch.setattr(ci, 'stash', fake)
    return calls


def _resolver(monkeypatch, fetched=None, size=(64, 32)):
    def fake_resolve(src, **kw):
        if fetched is not None:
            fetched.append(src)
        return ci.Resolved(_png(*size), 'image/png', 'x', 'web')
    monkeypatch.setattr(ci, 'resolve', fake_resolve)


# ── parser ───────────────────────────────────────────────────────────────────

def test_parser_dedupes_and_skips_broken():
    html = FIXTURE.read_text()
    out = web._parse_bing_images(html, 12)
    assert len(out) == 5                                   # 6 anchors incl. 1 dup, 2 broken skipped
    assert len({r['full'] for r in out}) == 5
    for r in out:
        assert r['full'].startswith('http') and r['thumb'].startswith('https://')
        assert set(r) == {'full', 'thumb', 'page', 'title', 'dims'}
    assert out[0]['dims'] == '2400x2021'                   # card markup carries dims
    assert out[1]['dims'] == ''                            # bare anchor → no dims, still a result


def test_parser_honors_limit():
    assert len(web._parse_bing_images(FIXTURE.read_text(), 2)) == 2


def test_search_rides_wan_session(monkeypatch):
    seen = {}

    class Sess:
        def get(self, url, timeout=None):
            seen['url'], seen['timeout'] = url, timeout

            class R:
                status_code = 200
                text = FIXTURE.read_text()
            return R()

    monkeypatch.setattr('core.net.wan_session', lambda profile='browser': Sess())
    out = web.search_bing_images('red apple', 4)
    assert len(out) == 4 and 'q=red+apple' in seen['url'] and 'bing.com/images/search' in seen['url']


def test_search_bad_status_is_empty(monkeypatch):
    class Sess:
        def get(self, url, timeout=None):
            class R:
                status_code = 503
                text = 'nope'
            return R()
    monkeypatch.setattr('core.net.wan_session', lambda profile='browser': Sess())
    assert web.search_bing_images('x', 3) == []


def test_search_page_and_safesearch_ride_the_url(monkeypatch):
    seen = []

    class Sess:
        def get(self, url, timeout=None):
            seen.append(url)
            return types.SimpleNamespace(status_code=200, text=FIXTURE.read_text())
    monkeypatch.setattr('core.net.wan_session', lambda profile='browser': Sess())
    assert len(web.search_bing_images('cats', 3, page=2, safesearch='moderate')) == 3
    assert 'first=4' in seen[0] and 'adlt=moderate' in seen[0]
    web.search_bing_images('cats', 6, page=1, safesearch='bogus')
    assert 'first=1' in seen[1] and 'adlt=off' in seen[1]      # unknown value → off


# ── result shape ─────────────────────────────────────────────────────────────

def test_tiles_marker_v3_handles_are_stashed_thumbs(monkeypatch, stashed):
    fetched = []
    _resolver(monkeypatch, fetched)
    out = web.image_search_result('apples', _results(3), view=False)
    assert isinstance(out, str)
    assert fetched == [r['thumb'] for r in _results(3)]          # thumbs, through the facade
    assert [v for _, v in stashed] == [False] * 3                # hidden handles
    assert ('1. Pic 1 — host1.example — 800x600 — img:h1.jpg\n'
            '   full: https://host1.example/1.jpg') in out
    m = json.loads(MARKER.search(out).group(1))
    assert m['title'] == 'apples' and len(m['items']) == 3
    e = m['items'][0]
    assert set(e) == {'handle', 'full', 'title', 'page'}
    assert e['handle'] == 'img:h1.jpg' and e['full'] == ci.proxied('https://host1.example/1.jpg')
    assert e['page'] == 'https://host1.example/page' and e['title'] == 'Pic 1'
    assert 'bing.example' not in out.split('<!--GALLERY')[1]     # browser never sees a bare Bing host


def test_view_many_is_one_sheet_from_the_stashed_bytes(monkeypatch, stashed):
    _resolver(monkeypatch)
    out = web.image_search_result('apples', _results(5), view=True, page=2)
    assert isinstance(out, dict) and len(out['images']) == 1 and len(stashed) == 5
    assert 'contact sheet numbered 1-5' in out['text'] and '(page 2)' in out['text']
    assert json.loads(MARKER.search(out['text']).group(1))['title'] == 'apples (page 2)'
    sheet = Image.open(io.BytesIO(base64.b64decode(out['images'][0]['data'])))
    assert sheet.size == (1200, 800) and out['images'][0]['display_only'] is False


def test_view_one_is_the_full_image(monkeypatch, stashed):
    fetched = []
    _resolver(monkeypatch, fetched, size=(3000, 1000))
    out = web.image_search_result('apples', _results(1), view=True)
    assert fetched == ['https://ts.bing.example/th?id=1', 'https://host1.example/1.jpg']
    img = Image.open(io.BytesIO(base64.b64decode(out['images'][0]['data'])))
    assert max(img.size) == 1536 and "looking at it" in out['text']


def test_thumb_fetch_failure_keeps_numbering(monkeypatch, stashed):
    def flaky(src, **kw):
        if 'id=2' in src:
            raise ci.ImageError('walled')
        return ci.Resolved(_png(), 'image/png', 'x', 'web')
    monkeypatch.setattr(ci, 'resolve', flaky)
    out = web.image_search_result('apples', _results(3), view=True)
    assert len(stashed) == 2
    assert '2. Pic 2 — host2.example — 800x600 — (thumbnail unavailable)' in out['text']
    items = json.loads(MARKER.search(out['text']).group(1))['items']
    assert 'handle' not in items[1] and items[1]['thumb'] == ci.proxied('https://ts.bing.example/th?id=2')
    assert items[0]['handle'] == 'img:h1.jpg' and items[2]['handle'] == 'img:h2.jpg'


def test_view_falls_back_honestly_when_pixels_fail(monkeypatch, stashed):
    def boom(src, **kw):
        raise ci.ImageError('walled')
    monkeypatch.setattr(ci, 'resolve', boom)
    out = web.image_search_result('apples', _results(2), view=True)
    assert isinstance(out, str) and "couldn't fetch the pixels" in out and MARKER.search(out)
    assert stashed == []


def test_view_url_lane(monkeypatch):
    monkeypatch.setattr(ci, 'resolve',
                        lambda src, **kw: ci.Resolved(_png(3000, 1000), 'image/png', 'pic.png', 'web'))
    out, ok = web.execute('web_view_images', {'url': 'https://h/pic.png'}, {})
    assert ok and isinstance(out, dict) and 'pic.png — 3000x1000' in out['text']
    monkeypatch.setattr(ci, 'resolve', lambda src, **kw: (_ for _ in ()).throw(ci.ImageError('nope')))
    assert web.execute('web_view_images', {'url': 'https://h/x'}, {}) == ('nope', False)


# ── execute ──────────────────────────────────────────────────────────────────

def test_execute_clamps_count_page_view_and_safesearch(monkeypatch):
    seen = []
    monkeypatch.setattr(web, 'search_bing_images', lambda q, c, p, s: seen.append((c, p, s)) or _results(1))
    monkeypatch.setattr(web, 'image_search_result', lambda q, r, v, p: f'ok view={v}')
    monkeypatch.setattr(web, '_safesearch', lambda: 'strict')
    assert web.execute('web_view_images', {'query': 'a', 'count': 50, 'page': 3}, {}) == ('ok view=True', True)
    assert web.execute('web_view_images', {'query': 'a', 'count': 'x', 'view': False}, {}) == ('ok view=False', True)
    assert web.execute('web_view_images', {'query': 'a', 'count': 0, 'page': 0}, {})[1] is True
    assert seen == [(12, 3, 'strict'), (6, 1, 'strict'), (6, 1, 'strict')]   # 0 = "not given" → default


def test_execute_no_query_and_no_results(monkeypatch):
    assert web.execute('web_view_images', {}, {})[1] is False
    monkeypatch.setattr(web, 'search_bing_images', lambda q, c, p, s: [])
    text, ok = web.execute('web_view_images', {'query': 'zzz', 'page': 2}, {})
    assert ok and 'No images found' in text and 'page 2' in text


def test_execute_network_posture(monkeypatch):
    def down(q, c, p, s):
        raise requests.exceptions.ConnectionError('no route')
    monkeypatch.setattr(web, 'search_bing_images', down)
    text, ok = web.execute('web_view_images', {'query': 'a'}, {})
    assert not ok and 'proxy' in text.lower()


def test_tool_registered_old_names_gone():
    names = [t['function']['name'] for t in web.TOOLS]
    assert 'web_view_images' in names
    assert 'web_search_images' not in names and 'get_images' not in names
    assert 'web_view_images' in web.AVAILABLE_FUNCTIONS and 'web_search_images' not in web.AVAILABLE_FUNCTIONS
    tool = next(t for t in web.TOOLS if t['function']['name'] == 'web_view_images')
    assert tool['network'] is True and tool['is_local'] is False
    assert set(tool['function']['parameters']['properties']) == {'query', 'url', 'count', 'page', 'view'}
    site = next(t for t in web.TOOLS if t['function']['name'] == 'get_website')
    assert site['function']['parameters']['properties']['show_image_urls']['enum'] == ['false', 'true', 'only']


# ── get_website show_image_urls ──────────────────────────────────────────────

PAGE = ('<html><head><meta property="og:image" content="/og.jpg"></head><body>'
        '<img src="/a.jpg" alt="A cat" width="600px" height="400">'
        '<img src="data:image/gif;base64,xx"><img src="/logo.svg">'
        '<img src="/pixel.gif" width="1" height="1">'
        '<img data-src="https://cdn.example/b.png"><img src="/a.jpg">'
        '<p>hello world text</p></body></html>')


def test_extract_image_urls():
    imgs = web.extract_image_urls(PAGE, 'https://site.example/post')
    assert [i['url'] for i in imgs] == ['https://site.example/og.jpg', 'https://site.example/a.jpg',
                                        'https://cdn.example/b.png']
    assert imgs[1] == {'url': 'https://site.example/a.jpg', 'alt': 'A cat', 'dims': '600x400'}
    assert web.image_urls_text([]) == 'No content images found on the page.'


def test_get_website_show_image_urls(monkeypatch):
    resp = types.SimpleNamespace(status_code=200, text=PAGE)
    monkeypatch.setattr(web, 'get_session', lambda: types.SimpleNamespace(get=lambda url, timeout=None: resp))
    only, ok = web.execute('get_website', {'url': 'https://site.example/post', 'show_image_urls': 'only'}, {})
    assert ok and only.startswith('Images on https://site.example/post (3)') and 'hello world' not in only
    both, ok = web.execute('get_website', {'url': 'https://site.example/post', 'show_image_urls': 'true'}, {})
    assert ok and 'hello world' in both and 'Images on the page (3):' in both
    assert '1. og:image — https://site.example/og.jpg' in both and '2. A cat — 600x400 — https://site.example/a.jpg' in both
    plain, ok = web.execute('get_website', {'url': 'https://site.example/post'}, {})
    assert ok and 'Images on the page' not in plain


# ── the junk guard ───────────────────────────────────────────────────────────

def _titled(*titles):
    return [{'full': f'https://h/{i}.jpg', 'thumb': 'https://t/x', 'page': '', 'title': t, 'dims': ''}
            for i, t in enumerate(titles)]


def test_looks_answered_scores_content_words():
    assert web.looks_answered('Eiffel Tower', _titled('Eiffel Tower | History', 'Paris at night'))
    assert web.looks_answered('red panda closeup', _titled('Red Panda Facts', 'Red panda cub'))   # 2 of 3
    assert not web.looks_answered('Plumbus rick and morty', _titled('About Taj Mahal', 'Taj Mahal tips'))
    assert not web.looks_answered('great horned owl', _titled('Great Value Wheat Bread', 'Great Word Logo'))  # 1 of 3
    assert not web.looks_answered('plumbus', _titled('Rap10 tortilla'))
    assert web.looks_answered('plumbus', [{'full': 'https://h/plumbus.png', 'thumb': '', 'page': '', 'title': '', 'dims': ''}])
    assert web.looks_answered('the of', _titled('anything'))          # no content words → nothing to judge
    assert web.looks_answered('x', [])


def test_execute_drops_bing_filler(monkeypatch):
    monkeypatch.setattr(web, 'search_bing_images', lambda q, c, p, s: _titled('Mahindra Thar LXT', 'Mahindra Thar 4x4'))
    monkeypatch.setattr(web, 'image_search_result', lambda q, r, v, p: 'SHOULD NOT RUN')
    text, ok = web.execute('web_view_images', {'query': 'Plumbus rick and morty'}, {})
    assert ok and "didn't answer" in text and 'Mahindra' not in text
    monkeypatch.setattr(web, 'search_bing_images', lambda q, c, p, s: _titled('Rick and Morty (TV Series)'))
    assert web.execute('web_view_images', {'query': 'rick and morty'}, {}) == ('SHOULD NOT RUN', True)
