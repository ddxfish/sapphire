"""Portable library paths (2026-09-09, the remembrance-to-VM broken images).

documents.source_path / working_path are stored ABSOLUTE by the install that
wrote them. Restored on a box with a different install path, every reader did
a bare Path(stored).exists() and gave up — text docs fell back to chunks,
images 404'd. `_local_path` is the one read-time fallback: stored path if it
exists, else sources_dir()/<doc_id>/<basename>. No migration, no schema.
"""
import io

import pytest
from PIL import Image

from plugins.mindpalace.tools import library as lib


def _jpeg():
    buf = io.BytesIO()
    Image.new('RGB', (48, 32), (200, 30, 60)).save(buf, 'JPEG')
    return buf.getvalue()


def _rehome(doc_id, other_root='/srv/old-box/user/mindpalace/library'):
    """Rewrite the stored paths as if another install had written them."""
    with lib.get_connection() as conn:
        row = conn.execute('SELECT source_path, working_path FROM documents '
                           'WHERE id = ?', (doc_id,)).fetchone()
        src = f"{other_root}/{doc_id}/{row[0].rsplit('/', 1)[-1]}" if row[0] else None
        wp = f"{other_root}/{doc_id}/working.md" if row[1] else None
        conn.execute('UPDATE documents SET source_path = ?, working_path = ? '
                     'WHERE id = ?', (src, wp, doc_id))
        conn.commit()


def test_local_path_falls_back_to_this_installs_sources(tmp_path):
    did, err = lib.import_image('default', 'pic.jpg', _jpeg(), title='Pic')
    assert did and not err
    _rehome(did)
    with lib.get_connection() as conn:
        stored = conn.execute('SELECT source_path FROM documents WHERE id = ?',
                              (did,)).fetchone()[0]
    assert stored.startswith('/srv/old-box/')
    got = lib._local_path(did, stored)
    assert got == lib.sources_dir() / str(did) / 'pic.jpg' and got.exists()
    assert lib._local_path(did, None) is None
    assert lib._local_path(did, '/srv/old-box/9999/nothing.jpg') is None


def test_image_readers_survive_a_moved_install():
    did, _ = lib.import_image('default', 'beach.jpg', _jpeg(), title='Beach')
    _rehome(did)
    src, thumb, render, ext = lib.image_paths('default', did)
    assert src and src.name == 'beach.jpg' and thumb and ext == '.jpg'
    path, label = lib.image_source('default', did)
    assert path == src and label.startswith(f'[doc {did}] Beach')
    data, fname, mime = lib.export_annotated('default', did)
    assert data and fname.endswith('.jpg')


def test_text_doc_reads_working_copy_after_move():
    did, err = lib.import_file('default', 'notes.txt', b'apple pie recipe\n' * 20)
    assert did and not err
    _rehome(did)
    title, text = lib.document_text('default', did)
    assert 'apple pie recipe' in text


def test_export_zip_packs_the_moved_file(tmp_path):
    did, _ = lib.import_image('default', 'zip.jpg', _jpeg(), title='Zipped')
    _rehome(did)
    out = tmp_path / 'scope.zip'
    report, err = lib.export_scope_zip('default', out)
    assert not err and report['documents'] == 1
    import zipfile
    assert f'sources/{did}/zip.jpg' in zipfile.ZipFile(out).namelist()


def test_image_source_walls():
    did, _ = lib.import_image('default', 'k.jpg', _jpeg(), title='Keyed')
    with pytest.raises(LookupError, match='No image'):
        lib.image_source('other', did)
    with lib.get_connection() as conn:
        conn.execute("UPDATE documents SET private_key = 'word' WHERE id = ?", (did,))
        conn.commit()
    with pytest.raises(LookupError, match='No image'):
        lib.image_source('default', did)
    assert lib.image_source('default', did, private_key='word')[1].startswith(f'[doc {did}] Keyed')
    nid, _ = lib.import_note('default', 'Note', 'text')
    with pytest.raises(LookupError, match='No image'):
        lib.image_source('default', nid)
    (lib.sources_dir() / str(did) / 'k.jpg').unlink()
    with pytest.raises(LookupError, match='no image file'):
        lib.image_source('default', did, private_key='word')
