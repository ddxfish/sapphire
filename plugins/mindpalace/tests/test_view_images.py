"""memory_view_image + local_view_images (image upgrade 2026-09-10, record
tmp/image-upgrade.md §11): library pictures by pixel query → one numbered
contact sheet with [doc N] lines and tiles off the thumb route (no copies);
files/folders on this machine → one numbered sheet, individuals stashed as
img: thumbs, folders paged. Both follow core/images.py's one lane: the sheet
is the only image in the return; the GALLERY marker v3 carries the user's row.
"""
import base64
import io
import json
import re

import pytest
from PIL import Image

import core.images as ci
from plugins.mindpalace.tools import library as lib
from plugins.mindpalace.tools import library_tools as lt

MARKER = re.compile(r'<!--GALLERY:(\{.*\})-->', re.S)


def _png(color=(200, 30, 60), w=48, h=32):
    b = io.BytesIO()
    Image.new('RGB', (w, h), color).save(b, 'PNG')
    return b.getvalue()


def _sheet(out):
    return Image.open(io.BytesIO(base64.b64decode(out['images'][0]['data'])))


@pytest.fixture
def stashed(monkeypatch):
    calls = []

    def fake(raw, media_type=None, *, visible=False, chat_name=None):
        calls.append(visible)
        return f"img:h{len(calls)}.jpg"
    monkeypatch.setattr(ci, 'stash', fake)
    return calls


@pytest.fixture
def scope_default(monkeypatch):
    monkeypatch.setattr(lt, '_pt', lambda: type('P', (), {'_get_current_scope': staticmethod(lambda: 'default')}))


# ── local_view_images ────────────────────────────────────────────────────────

def test_folder_page_is_a_numbered_sheet(tmp_path, stashed):
    for i in range(8):
        (tmp_path / f"p{i:02d}.png").write_bytes(_png((i * 20, 90, 90)))
    (tmp_path / 'notes.txt').write_text('x')
    (tmp_path / 'sub').mkdir()
    out, ok = lt.execute('local_view_images', {'folder': str(tmp_path)}, {})
    assert ok and len(out['images']) == 1 and _sheet(out).size == (1200, 800)
    assert f"{tmp_path}: 8 image(s), page 1 of 2; folders: sub" in out['text']
    assert f"1. p00.png — 48x32 — img:h1.jpg\n   {tmp_path / 'p00.png'}" in out['text']
    m = json.loads(MARKER.search(out['text']).group(1))
    assert m['title'] == f"{tmp_path.name}: page 1 of 2"
    assert [i['handle'] for i in m['items']] == [f'img:h{n}.jpg' for n in range(1, 7)]
    assert stashed == [False] * 6                                  # hidden handles, 6 a page
    out2, ok = lt.execute('local_view_images', {'folder': str(tmp_path), 'page': 2}, {})
    assert ok and 'page 2 of 2' in out2['text'] and 'p06.png' in out2['text'] and 'p05.png' not in out2['text']
    out3, ok = lt.execute('local_view_images', {'folder': str(tmp_path), 'page': 9, 'count': 3}, {})
    assert ok and 'page 3 of 3' in out3['text']                    # past the end → the last page


def test_paths_sheet_keeps_numbering_on_a_bad_entry(tmp_path, stashed):
    a = tmp_path / 'a.png'
    a.write_bytes(_png())
    b = tmp_path / 'b.txt'
    b.write_text('nope')
    out, ok = lt.execute('local_view_images', {'paths': [str(a), str(b), str(a)]}, {})
    assert ok and f"2. {b} — not an image" in out['text'] and len(stashed) == 2
    assert '3. a.png — 48x32 — img:h2.jpg' in out['text']
    assert len(json.loads(MARKER.search(out['text']).group(1))['items']) == 2   # no tile for the bad one
    assert _sheet(out).size == (800, 800)                          # 3 cells → 2×2: the bad one keeps its slot


def test_single_path_is_the_image_itself(tmp_path, stashed):
    a = tmp_path / 'a.png'
    a.write_bytes(_png(w=3000, h=1000))
    out, ok = lt.execute('local_view_images', {'paths': [str(a)]}, {})
    assert ok and 'a.png — 3000x1000 — from disk' in out['text'] and stashed == []
    assert 'GALLERY' not in out['text']                            # the inline image is the user's view
    assert lt.execute('local_view_images', {'paths': str(a)}, {})[1]   # a bare string works too


def test_folder_edges(tmp_path):
    assert lt.execute('local_view_images', {}, {})[1] is False
    assert lt.execute('local_view_images', {'folder': 'relative/dir'}, {})[1] is False
    empty = tmp_path / 'e'
    empty.mkdir()
    text, ok = lt.execute('local_view_images', {'folder': str(empty)}, {})
    assert ok and 'No images in this folder' in text
    junk = tmp_path / 'j'
    junk.mkdir()
    (junk / 'x.png').write_bytes(b'junk')
    (junk / 'y.png').write_bytes(b'junk')
    text, ok = lt.execute('local_view_images', {'folder': str(junk)}, {})
    assert not ok and 'None of those opened' in text


# ── memory_view_image ────────────────────────────────────────────────────────

def _two_library_images(tmp_path):
    p, q = tmp_path / 'cat.png', tmp_path / 'dog.png'
    p.write_bytes(_png())
    q.write_bytes(_png((30, 200, 60)))
    t1, ok1 = lib.save_image('default', str(p), 'Pets', caption='a cat')
    t2, ok2 = lib.save_image('default', str(q), 'Pets', caption='a dog')
    assert ok1 and ok2, (t1, t2)
    return [int(re.search(r'\[doc (\d+)\]', t).group(1)) for t in (t1, t2)]


def test_query_sheet_from_the_library(tmp_path, monkeypatch, scope_default):
    cat, dog = _two_library_images(tmp_path)
    monkeypatch.setattr(lib, '_photo_hits', lambda scope, query, limit: [(dog, 0.9), (cat, 0.8), (999999, 0.5)])
    out, ok = lt.execute('memory_view_image', {'query': 'animals'}, {})
    assert ok and len(out['images']) == 1 and _sheet(out).size == (800, 400)
    assert f"1. [doc {dog}] a dog" in out['text'] and f"2. [doc {cat}] a cat" in out['text']
    assert 'contact sheet numbered 1-2' in out['text']
    m = json.loads(MARKER.search(out['text']).group(1))
    assert m['title'] == 'library: animals'
    assert m['items'][0] == {'thumb': f'/api/plugin/mindpalace/library/documents/{dog}/thumb?scope=default',
                             'full': f'/api/plugin/mindpalace/library/documents/{dog}/file?scope=default',
                             'title': 'a dog'}
    one, ok = lt.execute('memory_view_image', {'query': 'animals', 'count': 1}, {})
    assert ok and "looking at it now" in one['text'] and f"1. [doc {dog}]" in one['text']


def test_query_edges_and_document_lane(tmp_path, monkeypatch, scope_default):
    cat, _dog = _two_library_images(tmp_path)
    assert lt.execute('memory_view_image', {}, {})[1] is False
    assert lt.execute('memory_view_image', {'image_id': 'nope'}, {})[1] is False   # not an img: handle
    monkeypatch.setattr(lib, '_photo_hits', lambda scope, query, limit: [])
    text, ok = lt.execute('memory_view_image', {'query': 'zzz'}, {})
    assert ok and "No library images match 'zzz'" in text and 'embedder' in text
    monkeypatch.setattr(ci, '_scope', lambda: 'default')        # the doc: lane resolves scope itself
    out, ok = lt.execute('memory_view_image', {'document_id': cat}, {})
    assert ok and isinstance(out, dict) and 'from the library' in out['text']


def test_tools_registered():
    names = [t['function']['name'] for t in lt.TOOLS]
    for n in ('memory_view_image', 'local_view_images', 'memory_save_image'):
        assert n in names and n in lt.AVAILABLE_FUNCTIONS
    assert 'image_view' not in names and 'image_view' not in lt.AVAILABLE_FUNCTIONS   # retired 2026-09-10 (vote A)
    mv = next(t for t in lt.TOOLS if t['function']['name'] == 'memory_view_image')
    assert set(mv['function']['parameters']['properties']) == {'query', 'count', 'document_id', 'image_id', 'private_key'}
    local = next(t for t in lt.TOOLS if t['function']['name'] == 'local_view_images')
    assert local['is_local'] is True and set(local['function']['parameters']['properties']) == {'paths', 'folder', 'page', 'count'}
