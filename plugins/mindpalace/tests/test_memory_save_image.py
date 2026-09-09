"""memory_save_image (2026-09-09): any core.images handle → a library image
under a topic. Dedup by content hash, topic match either level (case-
insensitive) else a new Category, private_key gate, receipt carries the id,
and the doc: round-trip back through core.images / image_view.
"""
import io

import pytest
from PIL import Image

import core.images as ci
from plugins.mindpalace.tools import library as lib
from plugins.mindpalace.tools import library_tools as lt


def _png(color=(200, 30, 60), w=48, h=32):
    buf = io.BytesIO()
    Image.new('RGB', (w, h), color).save(buf, 'PNG')
    return buf.getvalue()


def _docs(scope='default'):
    with lib.get_connection() as conn:
        return conn.execute('SELECT id, title, collection_id, private_key, meta FROM documents '
                            "WHERE scope = ? AND kind = 'image'", (scope,)).fetchall()


def test_save_from_path_creates_the_topic(tmp_path):
    p = tmp_path / 'cat photo.png'
    p.write_bytes(_png())
    text, ok = lib.save_image('default', str(p), 'Pets', caption='Sudo on the porch')
    assert ok and 'Saved [doc ' in text and '▸ Pets' in text and 'image_view("doc:' in text
    (did, title, cid, pk, meta), = _docs()
    assert title == 'Sudo on the porch' and pk is None
    with lib.get_connection() as conn:
        name, parent = conn.execute('SELECT name, parent_id FROM collections WHERE id = ?', (cid,)).fetchone()
    assert name == 'Pets' and parent is None
    assert '"import_key": "img:' in meta and 'cat photo.png' in meta      # provenance
    src, thumb, _r, ext = lib.image_paths('default', did)
    assert src.name == 'cat photo.png' and thumb and ext == '.png'


def test_dedup_by_content_per_scope(tmp_path):
    p = tmp_path / 'a.png'
    p.write_bytes(_png())
    text1, ok1 = lib.save_image('default', str(p), 'Pets')
    text2, ok2 = lib.save_image('default', str(p), 'Other topic')
    assert ok1 and ok2 and text2.startswith('Already in the library as [doc ')
    assert len(_docs()) == 1
    text3, ok3 = lib.save_image('work', str(p), 'Pets')     # another scope: its own copy
    assert ok3 and 'Saved' in text3 and len(_docs('work')) == 1


def test_topic_matches_existing_category_or_topic_case_insensitive(tmp_path):
    cat, _ = lib.create_collection('default', 'Family')
    top, _ = lib.create_collection('default', 'Holidays', parent_id=cat)
    p = tmp_path / 'x.png'
    p.write_bytes(_png((1, 2, 3)))
    text, ok = lib.save_image('default', str(p), 'holidays')
    assert ok and '▸ Holidays' in text
    assert _docs()[0][2] == top
    with lib.get_connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM collections WHERE scope = ?', ('default',)).fetchone()[0] == 2


def test_private_key_gates_the_saved_image(tmp_path):
    p = tmp_path / 'k.png'
    p.write_bytes(_png((9, 9, 9)))
    text, ok = lib.save_image('default', str(p), 'Secrets', caption='keyed', private_key='word')
    assert ok and '(private)' in text
    did = _docs()[0][0]
    with pytest.raises(LookupError):
        lib.image_source('default', did)
    assert lib.image_source('default', did, private_key='word')[1].startswith(f'[doc {did}] keyed')


def test_bad_inputs_are_honest(tmp_path):
    assert lib.save_image('default', 'relative/x.png', 'Pets')[1] is False
    assert lib.save_image('default', str(tmp_path / 'none.png'), 'Pets')[1] is False
    p = tmp_path / 'y.png'
    p.write_bytes(_png())
    text, ok = lib.save_image('default', str(p), '   ')
    assert not ok and 'topic' in text.lower()
    assert len(_docs()) == 0


def test_doc_round_trip_through_core_images_and_image_view(tmp_path, monkeypatch):
    p = tmp_path / 'trip.png'
    p.write_bytes(_png((0, 200, 0), 300, 100))
    text, ok = lib.save_image('default', str(p), 'Trips', caption='lake at dusk')
    did = _docs()[0][0]
    monkeypatch.setattr(ci, '_scope', lambda: 'default')
    r = ci.resolve(f'doc:{did}')
    assert r.origin == 'library' and r.media_type == 'image/png' and 'lake at dusk' in r.label
    import functions.images as fi
    out, ok = fi.execute('image_view', {'source': f'doc:{did}'}, {})
    assert ok and out['images'][0]['media_type'] == 'image/jpeg'
    assert f'[doc {did}] lake at dusk' in out['text'] and '300x100' in out['text'] and 'library' in out['text']
    monkeypatch.setattr(ci, '_scope', lambda: 'other')
    out, ok = fi.execute('image_view', {'source': f'doc:{did}'}, {})
    assert not ok and 'No image' in out


def test_tool_execute_routes_to_save_image(monkeypatch, tmp_path):
    from plugins.mindpalace.tools import palace_tools as pt
    monkeypatch.setattr(pt, '_get_current_scope', lambda: 'default')
    p = tmp_path / 'z.png'
    p.write_bytes(_png((5, 6, 7)))
    text, ok = lt.execute('memory_save_image', {'source': str(p), 'topic': 'Zed', 'caption': 'zee'}, {})
    assert ok and 'Saved [doc' in text and '▸ Zed' in text
    monkeypatch.setattr(pt, '_get_current_scope', lambda: None)
    text, ok = lt.execute('memory_save_image', {'source': str(p), 'topic': 'Zed'}, {})
    assert not ok and 'unavailable' in text
