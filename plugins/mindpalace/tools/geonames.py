# plugins/mindpalace/tools/geonames.py
# Offline reverse geocoding for Library images (Arc 2, I2).
#
# GPS decimal degrees → "Cancún, MX" with zero API calls and zero new deps:
# the GeoNames cities1000 dataset (every city with population ≥ 1000,
# ~140k rows) is DOWNLOADED ON FIRST USE (the nomic-model pattern — never
# bundled in the repo), compacted to an npz, and scanned with a numpy
# haversine (137k rows = sub-millisecond; no scipy, no k-d tree needed).
#
# LICENSE: GeoNames data is CC BY 4.0 — attribution REQUIRED and shipped
# beside the data (ATTRIBUTION.txt, written at build time). The repo itself
# contains no GeoNames data.
#
# Storage follows the model-cache contract (Krem 2026-07-18):
#   Linux/macOS → ~/.cache/sapphire/geonames/   (XDG cache, never backed up)
#   Windows     → user/models/geonames/          (backup cache-floor excluded)

import logging
import sys
import threading
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

DUMP_URL = 'https://download.geonames.org/export/dump/cities1000.zip'
ATTRIBUTION = ('This directory contains data derived from GeoNames '
               '(https://www.geonames.org), licensed under CC BY 4.0 '
               '(https://creativecommons.org/licenses/by/4.0/).')
MAX_KM = 150.0          # farther than this from any city = honest None
_EARTH_KM = 6371.0

_data = None            # (lats, lons, names) once loaded
_data_lock = threading.Lock()
_download_started = False
_dir_override = None    # tests monkeypatch


def data_dir() -> Path:
    if _dir_override is not None:
        return Path(_dir_override)
    if sys.platform == 'win32':
        # user/models/ is rebuildable-cache territory by contract — walk up
        # from this file with .absolute() (never .resolve(): symlinked
        # plugin dirs would escape the project) until the repo root (the
        # dir holding core/) — a fixed parent COUNT breaks when the plugin
        # moves between user/plugins/ and plugins/ (2026-07-19).
        root = Path(__file__).absolute().parent
        while root != root.parent and not (root / 'core').is_dir():
            root = root.parent
        return root / 'user' / 'models' / 'geonames'
    return Path.home() / '.cache' / 'sapphire' / 'geonames'


def _npz_path() -> Path:
    return data_dir() / 'cities.npz'


def available() -> bool:
    return _npz_path().exists()


def _build_npz(txt_bytes: bytes):
    """cities1000.txt (TSV) → compact npz: name, country, lat, lon."""
    names, lats, lons = [], [], []
    for line in txt_bytes.decode('utf-8', 'replace').splitlines():
        f = line.split('\t')
        if len(f) < 9:
            continue
        try:
            lat, lon = float(f[4]), float(f[5])
        except ValueError:
            continue
        name = f[1].strip() or f[2].strip()
        if not name:
            continue
        names.append(f'{name}, {f[8].strip()}' if f[8].strip() else name)
        lats.append(lat)
        lons.append(lon)
    if not names:
        raise ValueError('no rows parsed from GeoNames dump')
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / 'ATTRIBUTION.txt').write_text(ATTRIBUTION + '\n', encoding='utf-8')
    tmp = _npz_path().with_suffix('.npz.tmp')
    # File handle, not path: savez would silently append '.npz' to the name.
    with open(tmp, 'wb') as f:
        np.savez_compressed(f, names=np.array(names),
                            lats=np.array(lats, dtype=np.float32),
                            lons=np.array(lons, dtype=np.float32))
    tmp.replace(_npz_path())
    logger.info(f'[GEONAMES] dataset built: {len(names)} places → {_npz_path()}')


def ensure_dataset() -> bool:
    """Download + build if missing. Blocking (call from a worker thread for
    the first-use path). True = dataset is ready."""
    if available():
        return True
    import io
    import zipfile
    try:
        import requests
        logger.info(f'[GEONAMES] downloading {DUMP_URL} (first use)')
        r = requests.get(DUMP_URL, timeout=120)
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            _build_npz(z.read('cities1000.txt'))
        return True
    except Exception as e:
        logger.warning(f'[GEONAMES] dataset fetch failed (will retry on next '
                       f'GPS import): {e}')
        return False


def ensure_dataset_async(on_ready=None):
    """First-use trigger: fires the download once per process, in the
    background. on_ready runs after a successful build (backfill hook)."""
    global _download_started
    if available():
        return
    with _data_lock:
        if _download_started:
            return
        _download_started = True

    def _go():
        global _download_started
        try:
            if ensure_dataset() and on_ready:
                on_ready()
        finally:
            with _data_lock:
                _download_started = False

    threading.Thread(target=_go, name='geonames-fetch', daemon=True).start()


def _load():
    global _data
    if _data is not None:
        return _data
    with _data_lock:
        if _data is not None:
            return _data
        if not available():
            return None
        try:
            z = np.load(_npz_path(), allow_pickle=False)
            _data = (np.radians(z['lats'].astype(np.float32)),
                     np.radians(z['lons'].astype(np.float32)), z['names'])
        except Exception as e:
            logger.warning(f'[GEONAMES] dataset unreadable ({e}) — rebuilding '
                           f'on next fetch')
            try:
                _npz_path().unlink()
            except OSError:
                pass
            return None
    return _data


def reverse(lat, lon):
    """Decimal degrees → 'Cancún, MX', or None (no dataset / open ocean).
    Full haversine over every city — numpy makes brute force the simple,
    fast, and index-free answer at this scale."""
    d = _load()
    if d is None:
        return None
    rlats, rlons, names = d
    la, lo = np.radians(float(lat)), np.radians(float(lon))
    dlat = rlats - la
    dlon = rlons - lo
    a = (np.sin(dlat / 2.0) ** 2
         + np.cos(la) * np.cos(rlats) * np.sin(dlon / 2.0) ** 2)
    km = 2.0 * _EARTH_KM * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))
    i = int(np.argmin(km))
    if km[i] > MAX_KM:
        return None
    return str(names[i])
