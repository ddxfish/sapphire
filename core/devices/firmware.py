# core/devices/firmware.py - the programs boards run, for the Devices page's
# flasher (tmp/board-flash-web-plan.md)
#
# A board is written over USB from the browser (Web Serial, esptool-js). The
# program comes from here. DEVICE_FIRMWARE_SOURCE names an index: a URL
# (the firmware repository's release) or, for development, a folder on this
# machine. The index lists boards; each board has a manifest in the ESP Web
# Tools form, so the same files serve a standalone flasher too:
#
#   index.json     {"boards": [{"id": "pocket", "name": "...", "manifest": "pocket/manifest.json"}]}
#   manifest.json  {"name": "...", "version": "0.2.0",
#                   "builds": [{"chipFamily": "ESP32",
#                               "flash": {"mode": "dio", "size": "4MB", "freq": "40m"},   (ours; optional)
#                               "parts": [{"path": "bootloader.bin", "offset": 4096},
#                                         {"path": "app.bin", "offset": 131072, "app": true}, ...]}]}
#   ("app": true marks the program itself, the one part an update over the air sends)
#
# Parts fetched from a URL are kept under user/firmware_cache/<board>/<version>/
# so a board can be flashed again without the network; older versions of
# that board are dropped. Nothing is fetched on its own: only when the
# flasher asks. The page never fetches from the internet itself (the CSP's
# connect-src is 'self'): it asks these doors.
import json
import logging
import re
import shutil
import threading
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from core import net

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
CACHE = ROOT / 'user' / 'firmware_cache'
TEXT_MAX = 64 * 1024              # an index or a manifest
PART_MAX = 8 * 1024 * 1024        # one part: an app is 1.5-2.5 MB, the speech models 0.6 MB
FETCH_WAIT = 30                   # seconds, per request
REMEMBER = 60                     # seconds the index is kept: the flasher asks for it, then each part
_PATH = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,80}(/[A-Za-z0-9][A-Za-z0-9._-]{0,80}){0,4}$')
_ID = re.compile(r'^[a-z0-9][a-z0-9_-]{0,32}$')
_VERSION = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,40}$')
_REDIRECT_OK = ('.githubusercontent.com',)     # a release asset is a 302 to objects.githubusercontent.com
HOPS = 2                          # releases/latest/download/x: 302 to the tagged URL on the same host, then to the asset host
_lock = threading.Lock()
_recent = (0.0, None)             # (when, {id: board})


class FirmwareError(Exception):
    """A reason fit to show as it is."""


def source():
    import config
    s = str(getattr(config, 'DEVICE_FIRMWARE_SOURCE', '') or '').strip()
    return s + '/' if _is_url(s) and not s.endswith('/') else s      # a folder: urljoin keeps its last segment


def _is_url(s):
    return s.startswith(('http://', 'https://'))


def _safe_path(path):
    path = str(path or '').strip()
    if not _PATH.fullmatch(path):
        raise FirmwareError(f"'{path[:60]}' is not a usable file name.")
    return path


# --- reading from the source -------------------------------------------------

def _fetch(url, limit, into=None):
    """Bytes from a URL on the wan lane, at most `limit`, or written to
    `into`. Redirects are followed HOPS deep, each one https and either on
    the source's own host or to a GitHub asset host."""
    r = net.get(url, stream=True, timeout=FETCH_WAIT, allow_redirects=False)
    mine = (urlsplit(url).hostname or '').lower()
    for _ in range(HOPS):
        if r.status_code not in (301, 302, 303, 307, 308):
            break
        to = urljoin(url, r.headers.get('Location', ''))
        host = (urlsplit(to).hostname or '').lower()
        if not to.startswith('https://') or not (host == mine or host.endswith(_REDIRECT_OK)):
            raise FirmwareError(f"{url} redirects somewhere I do not follow ({host or 'nowhere'}).")
        r = net.get(to, stream=True, timeout=FETCH_WAIT, allow_redirects=False)
    if r.status_code != 200:
        raise FirmwareError(f"{url} answered HTTP {r.status_code}.")
    got, size = bytearray(), 0
    for piece in r.iter_content(64 * 1024):
        size += len(piece)
        if size > limit:
            raise FirmwareError(f"{url} is larger than {limit // 1024} KB.")
        (into.write if into else got.extend)(piece)
    return bytes(got)


def _local(base, rel):
    """The file `rel` under a source folder on this machine; never outside it."""
    folder = Path(base).expanduser().resolve()
    where = (folder / rel).resolve()
    if folder not in where.parents:
        raise FirmwareError(f"'{rel}' is outside the firmware folder.")
    if not where.is_file():
        raise FirmwareError(f"{where.name} is not there.")
    return where


def _read(base, rel, limit):
    if _is_url(base):
        return _fetch(urljoin(base, rel), limit)
    where = _local(base, rel)
    if where.stat().st_size > limit:
        raise FirmwareError(f"{where.name} is larger than {limit // 1024} KB.")
    return where.read_bytes()


def _json_of(data, what):
    try:
        out = json.loads(data.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        raise FirmwareError(f"The {what} is not JSON.")
    if not isinstance(out, dict):
        raise FirmwareError(f"The {what} is not an object.")
    return out


def _board(entry, base):
    """One board of the index, with its manifest read and checked."""
    board_id = str(entry.get('id') or '').strip().lower()
    if not _ID.fullmatch(board_id):
        raise FirmwareError(f"A board id in the index is not usable: '{board_id[:40]}'.")
    rel = _safe_path(entry.get('manifest') or f'{board_id}/manifest.json')
    m = _json_of(_read(base, rel, TEXT_MAX), f'{board_id} manifest')
    version = str(m.get('version') or '').strip()
    if not _VERSION.fullmatch(version):
        raise FirmwareError(f"The {board_id} manifest has no usable version.")
    builds = [b for b in (m.get('builds') or []) if isinstance(b, dict)]
    if not builds:
        raise FirmwareError(f"The {board_id} manifest has no builds.")
    b = builds[0]
    parts = []
    for p in (b.get('parts') or []):
        try:
            parts.append({'path': _safe_path(p.get('path')), 'offset': int(p.get('offset')), 'app': p.get('app') is True})
        except (TypeError, ValueError, AttributeError):
            raise FirmwareError(f"A part of {board_id} has no offset.")
    if not parts:
        raise FirmwareError(f"The {board_id} manifest has no parts.")
    flash = b.get('flash') if isinstance(b.get('flash'), dict) else {}
    return {'id': board_id, 'name': str(entry.get('name') or m.get('name') or board_id)[:80],
            'chipFamily': str(b.get('chipFamily') or '')[:20], 'version': version,
            'flash': {k: str(flash.get(k) or 'keep')[:8] for k in ('mode', 'size', 'freq')},
            'parts': parts,
            'folder': rel.rsplit('/', 1)[0] + '/' if '/' in rel else '', 'manifest': m}


def _boards(fresh=False):
    """({id: board}, error) from the source, remembered a minute. With the
    source unreachable, boards already in the cache are offered from there,
    so a board can be flashed again offline."""
    global _recent
    when, boards = _recent
    if not fresh and boards is not None and time.monotonic() - when < REMEMBER:
        return boards, None
    base = source()
    if not base:
        return {}, "No firmware source is set (DEVICE_FIRMWARE_SOURCE in Settings)."
    try:
        entries = _json_of(_read(base, 'index.json', TEXT_MAX), 'index').get('boards') or []
        boards = {b['id']: b for b in (_board(e, base) for e in entries if isinstance(e, dict))}
        error = None
    except Exception as e:
        logger.warning(f"[DEVICES] firmware index from {base[:80]}: {e}")
        boards, error = _cached_boards(), f"Could not read the firmware source: {e}"
    if boards:
        _recent = (time.monotonic(), boards)
    return boards, error


def _cached_boards():
    found = {}
    for mf in sorted(CACHE.glob('*/*/manifest.json')) if CACHE.is_dir() else []:
        board_id, version = mf.parent.parent.name, mf.parent.name
        try:
            b = _board({'id': board_id, 'manifest': f'{board_id}/{version}/manifest.json'}, str(CACHE))
        except FirmwareError:
            continue
        if all((mf.parent / p['path']).is_file() for p in b['parts']):
            found[board_id] = b
    return found


# --- what the flasher asks ---------------------------------------------------

def index():
    """The boards the flasher can offer: {'source', 'boards', 'error'}."""
    boards, error = _boards(fresh=True)
    return {'source': source(), 'error': error,
            'boards': [{k: b[k] for k in ('id', 'name', 'chipFamily', 'version', 'flash', 'parts')}
                       for b in boards.values()]}


def board(board_id, fresh=False):
    """One board of the source, or FirmwareError saying why not."""
    boards, error = _boards(fresh=fresh)
    b = boards.get(str(board_id or '').strip().lower())
    if not b:
        raise FirmwareError(error or f"No firmware for a '{board_id}' in the firmware source.")
    return b


def app_part(board_id):
    """The program itself, as a local file: what an update over the air sends."""
    b = board(board_id, fresh=True)
    app = next((p['path'] for p in b['parts'] if p['app']), None)
    if not app:
        raise FirmwareError(f"The {b['id']} manifest does not say which part is the program (\"app\": true).")
    return part(board_id, app), b['version']


def known_version(board_id):
    """The version the source offers for this board, from what is already
    known (the index read lately, or the cache): never the network, so a
    status probe can ask without reaching out. '' when nothing is known."""
    board_id = str(board_id or '').strip().lower()
    _, boards = _recent
    if boards is None:
        boards = _cached_boards()
    b = (boards or {}).get(board_id)
    return b['version'] if b else ''


def part(board_id, path):
    """The local file of one part, fetched into the cache when it is not
    there yet. Raises FirmwareError."""
    board_id = str(board_id or '').strip().lower()
    path = _safe_path(path)
    boards, error = _boards()
    b = boards.get(board_id)
    if not b or path not in [p['path'] for p in b['parts']]:
        raise FirmwareError(error or f"No such part: {board_id}/{path}.")
    base = source()
    if not _is_url(base):
        return _local(base, b['folder'] + path)
    with _lock:
        home = CACHE / board_id / b['version']
        want = home / path
        if want.is_file() and want.stat().st_size > 0:
            return want
        home.mkdir(parents=True, exist_ok=True)
        for old in (CACHE / board_id).iterdir():
            if old.is_dir() and old.name != b['version']:
                shutil.rmtree(old, ignore_errors=True)
        want.parent.mkdir(parents=True, exist_ok=True)
        tmp = want.with_name(want.name + '.partial')
        try:
            with open(tmp, 'wb') as f:
                _fetch(urljoin(base, b['folder'] + path), PART_MAX, into=f)
            tmp.replace(want)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        (home / 'manifest.json').write_text(json.dumps(b['manifest']), encoding='utf-8')
        logger.info(f"[DEVICES] firmware cached: {board_id} {b['version']} {path}")
        return want
