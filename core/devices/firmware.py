# core/devices/firmware.py - the programs boards run, for the Devices page's
# flasher (tmp/board-flash-web-plan.md)
#
# A board is written over USB, from the browser (Web Serial, esptool-js) or
# from this machine (flasher.py). The program comes from here.
# DEVICE_FIRMWARE_SOURCE names Sapphire's own release, and DEVICE_FIRMWARE_SOURCES
# the sources the user added (read first). Each names where: a URL (the firmware repository's
# release) or, for development, a folder on this machine. There it finds an
# index and ONE file per board, every part at its own address:
#
#   index.json     {"boards": [{"id": "cyd35", "name": "...", "version": "0.3.0", "chipFamily": "ESP32",
#                               "flash": {"mode": "dio", "size": "4MB", "freq": "40m"},
#                               "file": "cyd35-0.3.0.bin", "size": 1596272, "sha256": "...",
#                               "parts": [{"name": "bootloader", "offset": 4096, "size": 26208, "sha256": "..."},
#                                         {"name": "app", "offset": 131072, "size": ..., "app": true}, ...]}]}
#   ("app": true marks the program itself, the one part an update over the air sends)
#
# The file is fetched once, checked against its sha256 and cut into its parts
# under user/firmware_cache/<board>/<version>/, so a board can be flashed
# again without the network; older versions of that board are dropped. The
# flasher writes the parts, not the megabytes of blanks between them.
# Nothing is fetched on its own: only when the flasher asks. The page never
# fetches from the internet itself (the CSP's connect-src is 'self'): it
# asks these doors.
import hashlib
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
TEXT_MAX = 64 * 1024              # the index
FILE_MAX = 32 * 1024 * 1024       # one board's file: as large as its chip at most (16 MB so far)
FETCH_WAIT = 30                   # seconds, per request
REMEMBER = 60                     # seconds the index is kept: the flasher asks for it, then each part
MARK = 'board.json'               # the board's index entry beside its parts, written last: the parts are whole
_SHA = re.compile(r'^[0-9a-f]{64}$')
_PATH = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,80}(/[A-Za-z0-9][A-Za-z0-9._-]{0,80}){0,4}$')
_ID = re.compile(r'^[a-z0-9][a-z0-9_-]{0,32}$')
_VERSION = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,40}$')
_REDIRECT_OK = ('.githubusercontent.com',)     # a release asset is a 302 to objects.githubusercontent.com
HOPS = 2                          # releases/latest/download/x: 302 to the tagged URL on the same host, then to the asset host
_lock = threading.Lock()
_recent = (0.0, None, ())         # (when, {id: board}, the sources they came from)
_errors = {}                      # {source: why it could not be read}, from the last reading
SOURCES_MAX = 12
OWN = 'own'                       # the board id of a file the user gave: never one of the source's
_own = None                       # that board, until the next file or a restart
_CHIP = {0: 'ESP32', 2: 'ESP32-S2', 5: 'ESP32-C3', 9: 'ESP32-S3', 12: 'ESP32-C2', 13: 'ESP32-C6', 16: 'ESP32-H2'}


class FirmwareError(Exception):
    """A reason fit to show as it is."""


def _tidy(s):
    s = str(s or '').strip()
    return s + '/' if _is_url(s) and not s.endswith('/') else s      # a folder: urljoin keeps its last segment


def release():
    """The source Sapphire ships with: her own firmware release."""
    from core.settings_manager import settings
    return _tidy(settings.get_defaults().get('DEVICE_FIRMWARE_SOURCE'))


def added():
    """The sources the user added, in their order: DEVICE_FIRMWARE_SOURCES,
    and before them a DEVICE_FIRMWARE_SOURCE that was pointed somewhere else
    (how one other source was set before there could be several)."""
    import config
    out = []
    one = _tidy(getattr(config, 'DEVICE_FIRMWARE_SOURCE', ''))
    many = getattr(config, 'DEVICE_FIRMWARE_SOURCES', None)
    for s in ([one] if one and one != release() else []) + (list(many) if isinstance(many, (list, tuple)) else []):
        s = _tidy(s) if isinstance(s, str) else ''
        if s and s != release() and s not in out:
            out.append(s)
    return out[:SOURCES_MAX]


def set_sources(wanted, off=None):
    """The user's sources, kept (DEVICE_FIRMWARE_SOURCES): release URLs or
    folders, in their order. Sapphire's own release is never one of them and
    never leaves the list. `off` names the listed sources that are not to be
    read (DEVICE_FIRMWARE_SOURCES_OFF); her release may be one. A source once
    set the old way (DEVICE_FIRMWARE_SOURCE) moves into the list with the
    rest. Returns index()."""
    global _recent
    import config
    from core.settings_manager import settings
    if not isinstance(wanted, (list, tuple)):
        raise FirmwareError("sources: a list of URLs or folders.")
    kept = []
    for s in wanted:
        s = _tidy(s) if isinstance(s, str) else ''
        if not s or s == release() or s in kept:
            continue
        if len(s) > 300 or not (_is_url(s) or Path(s).expanduser().is_absolute()):
            raise FirmwareError(f"'{s[:60]}' is not a release URL (http or https) or a full folder path.")
        kept.append(s)
    if len(kept) > SOURCES_MAX:
        raise FirmwareError(f"At most {SOURCES_MAX} sources.")
    settings.set('DEVICE_FIRMWARE_SOURCES', kept, persist=True)
    quiet = {_tidy(x) for x in off if isinstance(x, str)} if isinstance(off, (list, tuple)) else set(switched_off())
    settings.set('DEVICE_FIRMWARE_SOURCES_OFF', [x for x in kept + [release()] if x and x in quiet], persist=True)
    if _tidy(getattr(config, 'DEVICE_FIRMWARE_SOURCE', '')) != release():
        settings.remove_user_override('DEVICE_FIRMWARE_SOURCE')       # the one other source of before: in the list now, or dropped
    _recent = (0.0, None, ())
    logger.info(f"[DEVICES] firmware sources: {len(kept)} added, then Sapphire's release")
    return index()


def listed():
    """Every source there is, on or off: the user's own first, then Sapphire's release."""
    return added() + ([release()] if release() else [])


def switched_off():
    """The listed sources that are switched off (DEVICE_FIRMWARE_SOURCES_OFF). Her own release can be one."""
    import config
    off = getattr(config, 'DEVICE_FIRMWARE_SOURCES_OFF', None)
    off = {_tidy(s) for s in off if isinstance(s, str)} if isinstance(off, (list, tuple)) else set()
    return [s for s in listed() if s in off]


def sources():
    """The sources that are read, in order: the user's own first, then
    Sapphire's release, less the ones switched off. A board two sources have
    comes from the first."""
    off = switched_off()
    return [s for s in listed() if s not in off]


def source():
    """The first source: where a board comes from when nothing else says."""
    return (sources() or [''])[0]


def official(base=None):
    """True for the source Sapphire ships with. Anything else (another URL,
    a folder) is someone's own build, and the page says so."""
    return bool(release()) and _tidy(source() if base is None else base) == release()


def _is_url(s):
    return s.startswith(('http://', 'https://'))


def _safe_path(path):
    path = str(path or '').strip()
    if not _PATH.fullmatch(path):
        raise FirmwareError(f"'{path[:60]}' is not a usable file name.")
    return path


# --- reading from the source -------------------------------------------------

def _fetch(url, limit):
    """Bytes from a URL on the wan lane, at most `limit`. Redirects are followed HOPS deep, each one https and either on
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
        got.extend(piece)
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


def _board(entry):
    """One board of the index, checked."""
    board_id = str(entry.get('id') or '').strip().lower()
    if not _ID.fullmatch(board_id):
        raise FirmwareError(f"A board id in the index is not usable: '{board_id[:40]}'.")
    version = str(entry.get('version') or '').strip()
    if not _VERSION.fullmatch(version):
        raise FirmwareError(f"The index gives {board_id} no usable version.")
    sha = str(entry.get('sha256') or '').lower()
    if not entry.get('file') or not _SHA.fullmatch(sha):
        raise FirmwareError(f"The index gives {board_id} no file with a sha256.")
    parts = []
    try:
        size = int(entry.get('size'))
        for p in (entry.get('parts') or []):
            part = {'path': _safe_path(f"{p.get('name')}.bin"), 'offset': int(p.get('offset')), 'size': int(p.get('size')),
                    'sha256': str(p.get('sha256') or '').lower(), 'app': p.get('app') is True}
            if part['offset'] < 0 or part['size'] <= 0 or part['offset'] + part['size'] > size:
                raise ValueError
            parts.append(part)
    except (TypeError, ValueError, AttributeError):
        raise FirmwareError(f"The index does not say where the parts of {board_id} are in its file.")
    if not parts:
        raise FirmwareError(f"The index gives {board_id} no parts.")
    flash = entry.get('flash') if isinstance(entry.get('flash'), dict) else {}
    return {'id': board_id, 'name': str(entry.get('name') or board_id)[:80],
            'chipFamily': str(entry.get('chipFamily') or '')[:20], 'version': version,
            'flash': {k: str(flash.get(k) or 'keep')[:8] for k in ('mode', 'size', 'freq')},
            'parts': parts, 'file': _safe_path(entry.get('file')), 'size': size, 'sha256': sha, 'entry': entry}


def _boards(fresh=False):
    """({id: board}, error) from every source, remembered a minute. Each
    board says which source it is from (`source`); one that two sources have
    comes from the first. With a source unreachable, boards already in the
    cache are offered from there, so a board can be flashed again offline.
    `error` is set only when nothing could be read at all."""
    global _recent, _errors
    when, boards, of = _recent
    bases = tuple(sources())
    if not fresh and boards is not None and of == bases and time.monotonic() - when < REMEMBER:
        return boards, None
    if not bases:
        return {}, "No firmware source is switched on (Settings > Devices > Settings)."
    boards, errors = {}, {}
    for base in bases:
        try:
            entries = _json_of(_read(base, 'index.json', TEXT_MAX), 'index').get('boards') or []
            for b in (_board(e) for e in entries if isinstance(e, dict)):
                boards.setdefault(b['id'], dict(b, source=base))
        except Exception as e:
            logger.warning(f"[DEVICES] firmware index from {base[:80]}: {e}")
            errors[base] = str(e)
    if errors:
        for board_id, b in _cached_boards().items():       # what was fetched before, for what cannot be read now
            boards.setdefault(board_id, dict(b, source=''))
    _errors = errors
    if boards:
        _recent = (time.monotonic(), boards, bases)
    failed = len(errors) == len(bases)
    return boards, (f"Could not read the firmware source: {next(iter(errors.values()))}" if failed else None)


def _whole(home, b):
    """True when `home` holds every part of this very file."""
    try:
        kept = json.loads((home / MARK).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    return isinstance(kept, dict) and kept.get('sha256') == b['sha256'] and all((home / p['path']).is_file() for p in b['parts'])


def _cached_boards():
    found = {}
    for mark in sorted(CACHE.glob(f'*/*/{MARK}')) if CACHE.is_dir() else []:
        try:
            b = _board(_json_of(mark.read_bytes(), 'cached board'))
        except (OSError, FirmwareError):
            continue
        if (b['id'], b['version']) == (mark.parent.parent.name, mark.parent.name) and _whole(mark.parent, b):
            found[b['id']] = b
    return found


# --- what the flasher asks ---------------------------------------------------

def index():
    """The boards the flasher can offer, and where they come from: {'boards',
    'sources': [{'source', 'official', 'on', 'error', 'boards': how many}]
    (every source listed, also one switched off),
    'error'}. Each board says its `source` and whether that is Sapphire's
    release (`official`). 'source' and 'official' speak of the first source."""
    boards, error = _boards(fresh=True)
    return {'source': source(), 'official': official(), 'error': error,
            'sources': [{'source': s, 'official': official(s), 'on': s in sources(), 'error': _errors.get(s, ''),
                         'boards': sum(1 for b in boards.values() if b.get('source') == s)} for s in listed()],
            'boards': [dict({k: b[k] for k in ('id', 'name', 'chipFamily', 'version', 'flash', 'parts')},
                            source=b.get('source', ''), official=official(b['source']) if b.get('source') else False)
                       for b in boards.values()]}


def board(board_id, fresh=False):
    """One board of the source, or FirmwareError saying why not."""
    boards, error = _boards(fresh=fresh)
    b = boards.get(str(board_id or '').strip().lower())
    if not b:
        raise FirmwareError(error or f"No firmware for a '{board_id}' in the firmware source.")
    return b


def keep_own(data, name):
    """A firmware file of the user's own, kept as the board 'own' so either
    lane writes it like any other: one part, the whole file, at address 0.
    It must be a whole-board image, the kind the firmware repository's build
    makes: the bootloader first (at 0x1000 on an ESP32 or S2), the partition
    table at 0x8000. A lone program (an "app" .bin) is refused: at address 0
    it would leave a board that cannot start. Returns the board."""
    global _own
    name = re.sub(r'[^A-Za-z0-9._ -]', '_', Path(str(name or '')).name)[:80] or 'firmware.bin'
    if len(data) < 0x8002 or data[0x8000:0x8002] != b'\xaa\x50':
        raise FirmwareError(f"{name} is not a whole-board image: it has no partition table at 0x8000. A build's single "
                            f"program (the \"app\" .bin) cannot be written this way; use the merged file, the one a release has.")
    boot = 0 if data[0] == 0xE9 else 0x1000 if data[0x1000] == 0xE9 else None
    if boot is None:
        raise FirmwareError(f"{name} has no bootloader where one belongs.")
    b = {'id': OWN, 'name': name, 'version': '', 'chipFamily': _CHIP.get(int.from_bytes(data[boot + 12:boot + 14], 'little'), ''),
         'flash': {'mode': 'keep', 'size': 'keep', 'freq': 'keep'},
         'parts': [{'path': 'image.bin', 'offset': 0, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'app': False}]}
    with _lock:
        home = CACHE / OWN
        home.mkdir(parents=True, exist_ok=True)
        (home / 'image.bin').write_bytes(data)
        _own = b
    logger.info(f"[DEVICES] own firmware kept: {name}, {len(data)} bytes, {b['chipFamily'] or 'chip unknown'}")
    return b


def own():
    """The board of the last file given, or None."""
    return _own if _own and (CACHE / OWN / 'image.bin').is_file() else None


def app_part(board_id):
    """The program itself, as a local file: what an update over the air sends."""
    b = board(board_id, fresh=True)
    app = next((p['path'] for p in b['parts'] if p['app']), None)
    if not app:
        raise FirmwareError(f"The index does not say which part of {b['id']} is the program (\"app\": true).")
    return part(board_id, app), b['version']


def known_version(board_id):
    """The version the source offers for this board, from what is already
    known (the index read lately, or the cache): never the network, so a
    status probe can ask without reaching out. '' when nothing is known."""
    board_id = str(board_id or '').strip().lower()
    _, boards, of = _recent
    if boards is None or of != tuple(sources()):
        boards = _cached_boards()
    b = (boards or {}).get(board_id)
    return b['version'] if b else ''


def part(board_id, path):
    """The local file of one part, the board's file fetched and cut up when
    it is not in the cache yet. Raises FirmwareError."""
    board_id = str(board_id or '').strip().lower()
    path = _safe_path(path)
    if board_id == OWN:
        if not own() or path != 'image.bin':
            raise FirmwareError("No file of your own is here. Choose it again.")
        return CACHE / OWN / 'image.bin'
    boards, error = _boards()
    b = boards.get(board_id)
    if not b or path not in [p['path'] for p in b['parts']]:
        raise FirmwareError(error or f"No such part: {board_id}/{path}.")
    with _lock:
        home = CACHE / board_id / b['version']
        if _whole(home, b):
            return home / path
        if not b.get('source'):
            raise FirmwareError(f"The source {board_id} {b['version']} came from cannot be read now, and it is not whole in the cache.")
        image = _read(b['source'], b['file'], FILE_MAX)
        if len(image) != b['size'] or hashlib.sha256(image).hexdigest() != b['sha256']:
            raise FirmwareError(f"{b['file']} is not the file the index describes (its sha256 differs). Nothing was written.")
        cut = {p['path']: image[p['offset']:p['offset'] + p['size']] for p in b['parts']}
        for p in b['parts']:
            if p['sha256'] and hashlib.sha256(cut[p['path']]).hexdigest() != p['sha256']:
                raise FirmwareError(f"The {p['path'][:-4]} part of {b['file']} is not what the index describes.")
        shutil.rmtree(CACHE / board_id, ignore_errors=True)       # older versions, or half of this one
        home.mkdir(parents=True)
        for name, data in cut.items():
            (home / name).write_bytes(data)
        (home / MARK).write_text(json.dumps(b['entry']), encoding='utf-8')
        logger.info(f"[DEVICES] firmware cached: {board_id} {b['version']} ({len(image)} bytes, {len(cut)} parts)")
        return home / path
