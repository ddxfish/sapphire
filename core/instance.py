"""This Sapphire's identity: an id nobody types, and a name somebody chose.

The id is minted once and never changes. It lives in the config dir
(core/setup.py CONFIG_DIR), outside user/, so a backup restored onto another
machine does not carry it along: two Sapphires never share one. It is keyed
by the data directory, so a second install on this machine, or a second
user/ directory one day, is a different Sapphire. Moving the install folder
mints a new id; rows already written keep the old one.

The name is a label and may change: INSTANCE_NAME when set, else the
dashboard's display name when someone changed it, else the hostname.
"""
import hashlib
import json
import logging
import os
import socket
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# .absolute(), never .resolve(): a symlinked tree is its own install.
DATA_DIR = Path(__file__).absolute().parent.parent / 'user'
DEFAULT_DISPLAY_NAME = 'Sapphire'     # settings_defaults.json: every fresh install shows this

_store_dir = None     # tests point this at a tmp dir
_lock = threading.Lock()
_cache = {}           # data dir -> id


def _dir():
    if _store_dir is not None:
        return Path(_store_dir)
    if 'pytest' in sys.modules:
        return None                   # a test never mints into the real config dir
    from core.setup import CONFIG_DIR
    return CONFIG_DIR / 'instances'


def _read(path):
    try:
        iid = json.loads(path.read_text(encoding='utf-8')).get('id')
        return iid if isinstance(iid, str) and len(iid) == 32 else None
    except (OSError, ValueError, AttributeError):
        return None


def _mint(path, key):
    """Write a new id unless one is already there. The hard link is atomic
    and never overwrites: of two processes minting at once exactly one wins,
    and both then read the winner's."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps({'id': uuid.uuid4().hex, 'data_dir': key,
                       'created': datetime.now(timezone.utc).isoformat(timespec='seconds')})
    tmp = path.with_name(f'{path.stem}.{os.getpid()}.{uuid.uuid4().hex[:6]}.tmp')
    try:
        tmp.write_text(body, encoding='utf-8')
        try:
            os.link(tmp, path)
        except FileExistsError:
            pass
        except OSError:               # no hard links on this filesystem
            try:
                with open(path, 'x', encoding='utf-8') as f:
                    f.write(body)
            except FileExistsError:
                pass
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def instance_id(data_dir=None):
    """The id of the Sapphire whose data lives in data_dir (default: this
    install's user/): 32 hex characters, read, or minted the first time
    anyone asks. None when it can be neither read nor stored. A caller then
    leaves its rows unstamped and asks again later; nobody invents one, and
    a file that is there but unreadable is never replaced."""
    key = str(Path(data_dir).absolute() if data_dir else DATA_DIR)
    with _lock:
        if key in _cache:
            return _cache[key]
        try:
            base = _dir()
            if base is None:
                return None
            path = base / (hashlib.sha256(key.encode('utf-8')).hexdigest()[:16] + '.json')
            if not path.exists():
                _mint(path, key)
                logger.info(f"[INSTANCE] this Sapphire has no id yet for {key}: minting one")
            iid = _read(path)
        except Exception as e:
            logger.error(f"[INSTANCE] id unavailable ({e}); rows stay unstamped until it is")
            return None
        if iid is None:
            logger.error(f"[INSTANCE] {path.name} is unreadable; it is left as it is, "
                         f"rows stay unstamped until it reads")
            return None
        _cache[key] = iid
        return iid


def instance_name():
    """What this Sapphire is called, 40 characters at most."""
    name = ''
    try:
        import config
        name = str(getattr(config, 'INSTANCE_NAME', '') or '').strip()
        if not name:
            shown = str(getattr(config, 'DASHBOARD_DISPLAY_NAME', '') or '').strip()
            if shown and shown != DEFAULT_DISPLAY_NAME:
                name = shown
    except Exception:
        pass
    return (name or socket.gethostname())[:40]
