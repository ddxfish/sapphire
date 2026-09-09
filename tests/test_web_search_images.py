"""web_search_images (2026-09-09): Bing-backed image search through core.net,
GALLERY marker v2 (objects, DDG-proxied URLs) for the user's tiles, and
view=true = pixels for the model (single image, or one numbered contact
sheet). Parser runs on tests/fixtures/bing_images_sample.html — 5 real anchors
plus a duplicate and two broken ones.
"""
import io
import json
import re
from pathlib import Path

import pytest
import requests
from PIL import Image

import core.images as ci
import functions.web as web

FIXTURE = Path(__file__).parent / 'fixtures' / 'bing_images_sample.html'
MARKER = re.compile(r'<!--GALLERY:(\[.*\])-->', re.S)


def _png(w=64, h=32):
    buf = io.BytesIO()
    Image.new('RGB', (w, h), (30, 200, 60)).save(buf, 'PNG')
    return buf.getvalue()


def _results(n=3):
    return [{'full': f'https://host{i}.example/{i}.jpg', 'thumb': f'https://ts.bing.example/th?id={i}',
             'page': f'https://host{i}.example/page', 'title': f'Pic {i}', 'dims': '800x600'}
            for i in range(1, n + 1)]


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


# ── result shape ─────────────────────────────────────────────────────────────

def test_tiles_only_marker_v2_is_proxied():
    out = web.image_search_result('apples', _results(3), view=False)
    assert isinstance(out, str)
    assert '1. Pic 1 — host1.example — 800x600' in out and 'https://host1.example/1.jpg' in out
    entries = json.loads(MARKER.search(out).group(1))
    assert len(entries) == 3
    e = entries[0]
    assert set(e) == {'thumb', 'full', 'title', 'page'}
    assert e['thumb'].startswith('https://external-content.duckduckgo.com/iu/?u=')
    assert e['full'] == ci.proxied('https://host1.example/1.jpg')
    assert e['page'] == 'https://host1.example/page' and e['title'] == 'Pic 1'
    assert 'bing.example' not in e['thumb'].split('u=')[0]   # browser never sees a bare Bing host


def test_view_many_is_one_contact_sheet(monkeypatch):
    fetched = []

    def fake_resolve(src, **kw):
        fetched.append(src)
        return ci.Resolved(_png(), 'image/png', 'x', 'web')

    monkeypatch.setattr(ci, 'resolve', fake_resolve)
    out = web.image_search_result('apples', _results(5), view=True)
    assert isinstance(out, dict) and len(out['images']) == 1
    assert fetched == [r['thumb'] for r in _results(5)]      # thumbs, not full-size, for the sheet
    assert 'contact sheet numbered 1-5' in out['text'] and MARKER.search(out['text'])
    sheet = Image.open(io.BytesIO(__import__('base64').b64decode(out['images'][0]['data'])))
    assert sheet.size == (1200, 800) and out['images'][0]['display_only'] is False


def test_view_one_is_the_full_image(monkeypatch):
    fetched = []

    def fake_resolve(src, **kw):
        fetched.append(src)
        return ci.Resolved(_png(3000, 1000), 'image/png', 'x', 'web')

    monkeypatch.setattr(ci, 'resolve', fake_resolve)
    out = web.image_search_result('apples', _results(1), view=True)
    assert fetched == ['https://host1.example/1.jpg']
    img = Image.open(io.BytesIO(__import__('base64').b64decode(out['images'][0]['data'])))
    assert max(img.size) == 1536 and "looking at it" in out['text']


def test_view_falls_back_honestly_when_pixels_fail(monkeypatch):
    def boom(src, **kw):
        raise ci.ImageError('walled')
    monkeypatch.setattr(ci, 'resolve', boom)
    out = web.image_search_result('apples', _results(2), view=True)
    assert isinstance(out, str) and "couldn't fetch the pixels" in out and MARKER.search(out)


# ── execute ──────────────────────────────────────────────────────────────────

def test_execute_clamps_count_and_passes_view(monkeypatch):
    seen = []
    monkeypatch.setattr(web, 'search_bing_images', lambda q, c: seen.append(c) or _results(1))
    monkeypatch.setattr(web, 'image_search_result', lambda q, r, v: f'ok view={v}')
    assert web.execute('web_search_images', {'query': 'a', 'count': 50}, {}) == ('ok view=False', True)
    assert web.execute('web_search_images', {'query': 'a', 'count': 'x', 'view': True}, {}) == ('ok view=True', True)
    assert web.execute('web_search_images', {'query': 'a', 'count': 0}, {})[1] is True
    assert seen == [12, 6, 6]                                    # 0 = "not given" → default


def test_execute_no_query_and_no_results(monkeypatch):
    assert web.execute('web_search_images', {}, {})[1] is False
    monkeypatch.setattr(web, 'search_bing_images', lambda q, c: [])
    text, ok = web.execute('web_search_images', {'query': 'zzz'}, {})
    assert ok and 'No images found' in text


def test_execute_network_posture(monkeypatch):
    def down(q, c):
        raise requests.exceptions.ConnectionError('no route')
    monkeypatch.setattr(web, 'search_bing_images', down)
    text, ok = web.execute('web_search_images', {'query': 'a'}, {})
    assert not ok and 'proxy' in text.lower()


def test_tool_registered_get_images_gone():
    names = [t['function']['name'] for t in web.TOOLS]
    assert 'web_search_images' in names and 'get_images' not in names
    assert 'web_search_images' in web.AVAILABLE_FUNCTIONS and 'get_images' not in web.AVAILABLE_FUNCTIONS
    tool = next(t for t in web.TOOLS if t['function']['name'] == 'web_search_images')
    assert tool['network'] is True and tool['is_local'] is False
    assert not hasattr(web, 'extract_images') and not hasattr(web, '_JUNK_PATTERNS')
