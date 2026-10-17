"""Arc 2 I2 — offline reverse geocoding (GeoNames cities1000 → npz →
numpy haversine). No network in tests: the atlas fixture builds the npz
from a 3-row hand-made TSV. conftest's _library_isolation covers the
library.db side of the integration tests."""
import io

import pytest

from plugins.mindpalace.tools import geonames as g
from plugins.mindpalace.tools import library as lib

CANCUN = (21.162, -86.8514)          # the beach fixture's EXIF
OCEAN = (0.0, -140.0)


@pytest.fixture
def atlas(tmp_path, monkeypatch):
    monkeypatch.setattr(g, '_dir_override', tmp_path / 'geo', raising=False)
    monkeypatch.setattr(g, '_data', None, raising=False)
    monkeypatch.setattr(g, '_download_started', False, raising=False)
    rows = [
        '1\tCancún\tCancun\t\t21.17429\t-86.84656\tP\tPPL\tMX',
        '2\tWest Yellowstone\tWest Yellowstone\t\t44.66215\t-111.10411\tP\tPPL\tUS',
        '3\tTokyo\tTokyo\t\t35.6895\t139.69171\tP\tPPL\tJP',
    ]
    g._build_npz('\n'.join(rows).encode('utf-8'))
    return g


@pytest.fixture
def no_atlas(tmp_path, monkeypatch):
    monkeypatch.setattr(g, '_dir_override', tmp_path / 'geo-none', raising=False)
    monkeypatch.setattr(g, '_data', None, raising=False)
    monkeypatch.setattr(g, '_download_started', False, raising=False)
    return g


def _jpeg_gps():
    from PIL import Image
    img = Image.new('RGB', (32, 32), (10, 90, 160))
    exif = Image.Exif()
    gps = exif.get_ifd(0x8825)
    gps[1] = 'N'; gps[2] = (21.0, 9.0, 43.2)
    gps[3] = 'W'; gps[4] = (86.0, 51.0, 5.0)
    buf = io.BytesIO()
    img.save(buf, 'JPEG', exif=exif)
    return buf.getvalue()


def test_reverse_nearest_and_ocean(atlas):
    assert atlas.available()
    assert atlas.reverse(*CANCUN) == 'Cancún, MX'
    assert atlas.reverse(44.6, -111.0) == 'West Yellowstone, US'
    assert atlas.reverse(*OCEAN) is None            # > MAX_KM from any city
    assert (atlas.data_dir() / 'ATTRIBUTION.txt').exists()   # CC BY rides along


def test_no_dataset_is_graceful(no_atlas):
    assert not no_atlas.available()
    assert no_atlas.reverse(*CANCUN) is None


def test_corrupt_npz_self_heals(atlas):
    atlas._npz_path().write_bytes(b'garbage')
    atlas._data = None
    assert atlas.reverse(*CANCUN) is None
    assert not atlas._npz_path().exists()           # cleared for a re-fetch


def test_import_image_names_the_place(atlas):
    did, err = lib.import_image('default', 'beach.jpg', _jpeg_gps())
    assert err is None
    import json
    with lib.get_connection() as conn:
        meta = json.loads(conn.execute(
            'SELECT meta FROM documents WHERE id = ?', (did,)).fetchone()[0])
    assert meta['place'] == 'Cancún, MX'
    md = (lib.sources_dir() / str(did) / 'metadata.md').read_text('utf-8')
    assert 'place: Cancún, MX' in md
    text, found = lib.search_library('default', 'Cancun OR Cancún')
    # place names ride FTS — searching the city finds the photo
    text, found = lib.search_library('default', 'Cancún')
    assert found and f'[doc {did}]' in text


def test_backfill_places_after_download(no_atlas, monkeypatch):
    kicked = []
    monkeypatch.setattr(g, 'ensure_dataset_async',
                        lambda on_ready=None: kicked.append(1))
    did, err = lib.import_image('default', 'later.jpg', _jpeg_gps())
    assert err is None and kicked                   # download was triggered
    import json
    with lib.get_connection() as conn:
        meta = json.loads(conn.execute(
            'SELECT meta FROM documents WHERE id = ?', (did,)).fetchone()[0])
    assert 'place' not in meta                      # no atlas yet
    # the atlas "arrives" — same rows the atlas fixture uses
    monkeypatch.setattr(g, '_dir_override', no_atlas.data_dir(), raising=False)
    g._build_npz('1\tCancún\tCancun\t\t21.17429\t-86.84656\tP\tPPL\tMX'
                 .encode('utf-8'))
    g._data = None
    assert lib.backfill_places() == 1
    with lib.get_connection() as conn:
        meta = json.loads(conn.execute(
            'SELECT meta FROM documents WHERE id = ?', (did,)).fetchone()[0])
    assert meta['place'] == 'Cancún, MX'
    md = (lib.sources_dir() / str(did) / 'metadata.md').read_text('utf-8')
    assert 'place:' in md
    assert lib.backfill_places() == 0               # idempotent
