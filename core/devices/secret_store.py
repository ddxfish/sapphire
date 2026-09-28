# core/devices/secret_store.py - Secrets that belong to devices (tmp/device-manager-plan.md)
#
# Not named secrets.py on purpose: that would shadow the standard library.
#
# SSH keys, passwords, device tokens. Own file beside credentials.json, so new
# code here can never damage the app's own credentials (that file is rewritten
# whole on every save). Same folder, same contract: outside user/, never in
# backups, scrambled with the machine-bound key - the scramble itself is the
# credentials manager's, not a copy.
#
# Plaintext leaves this module through ONE door: resolve(), which a transport
# calls at connect time. Everything else answers 'set' or 'not set'.
#
# File shape: {"devices": {device_id: {field: "enc:..."}}}

import json
import logging
import os
import re
import sys
import threading
from datetime import datetime

from core.setup import CONFIG_DIR
from core.settings_manager import _fsync_file, _fsync_dir
from core.fs_utils import replace_with_retry
from core.credentials_manager import DecryptionError

logger = logging.getLogger(__name__)

SECRETS_FILE = CONFIG_DIR / 'device_secrets.json'
_ID_RE = re.compile(r'[a-z0-9][a-z0-9_-]{0,32}$')
_FIELD_RE = re.compile(r'[a-z0-9][a-z0-9_.-]{0,80}$')   # 'driver.field'

_lock = threading.RLock()
_data = None


class Secrets:
    """One device's secrets, decrypted. Printing it never shows a value."""

    def __init__(self, device_id, values):
        self._device_id = device_id
        self._values = dict(values)

    def get(self, field, default=''):
        return self._values.get(field, default)

    def __contains__(self, field):
        return field in self._values

    def __bool__(self):
        return bool(self._values)

    def fields(self):
        return sorted(self._values)

    def scrub(self, text):
        """The text with every secret value (and each of its lines) replaced."""
        text = str(text)
        for value in self._values.values():
            for part in [value] + [ln.strip() for ln in value.splitlines()]:
                if len(part) >= 4:
                    text = text.replace(part, '[secret]')
        return text

    def __repr__(self):
        return f"<Secrets {self._device_id} fields={self.fields()} redacted>"

    __str__ = __repr__


def _crypto():
    from core.credentials_manager import credentials
    return credentials


def _load():
    global _data
    if _data is not None:
        return _data
    _data = {'devices': {}}
    if SECRETS_FILE.exists():
        try:
            loaded = json.loads(SECRETS_FILE.read_text(encoding='utf-8'))
            if not isinstance(loaded.get('devices'), dict):
                raise ValueError("no 'devices' table")
            _data = loaded
        except Exception as e:
            logger.critical(f"[DEVICE-SECRETS] {SECRETS_FILE} is unreadable: {e}")
            try:
                import shutil
                stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                kept = SECRETS_FILE.with_suffix(f'.json.corrupt.{stamp}')
                shutil.copy2(SECRETS_FILE, kept)
                logger.critical(f"[DEVICE-SECRETS] unreadable file kept as {kept}")
            except Exception as keep_err:
                logger.error(f"[DEVICE-SECRETS] could not keep a copy: {keep_err}")
    return _data


def _save():
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = SECRETS_FILE.with_suffix('.tmp')
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(_data, f, indent=2)
            _fsync_file(f)
        if sys.platform != 'win32':
            os.chmod(tmp, 0o600)
        replace_with_retry(tmp, SECRETS_FILE)
        _fsync_dir(SECRETS_FILE.parent)
        return True
    except Exception as e:
        logger.error(f"[DEVICE-SECRETS] save failed: {e}")
        return False


def _ok(device_id, field=None):
    if not _ID_RE.fullmatch(str(device_id or '')):
        logger.warning(f"[DEVICE-SECRETS] bad device id {device_id!r}")
        return False
    if field is not None and not _FIELD_RE.fullmatch(str(field or '')):
        logger.warning(f"[DEVICE-SECRETS] bad field name {field!r}")
        return False
    return True


def put(device_id, field, value):
    """Store one secret, scrambled. An empty value is refused - use clear()."""
    if not _ok(device_id, field) or not isinstance(value, str) or not value:
        return False
    try:
        scrambled = _crypto().scramble(value)
    except Exception as e:
        logger.error(f"[DEVICE-SECRETS] cannot scramble {device_id}.{field}: {e}")
        return False
    if not scrambled.startswith('enc:'):
        logger.error(f"[DEVICE-SECRETS] refusing to store {device_id}.{field} unscrambled")
        return False
    with _lock:
        devices = _load()['devices']
        before = devices.get(device_id, {}).get(field)
        devices.setdefault(device_id, {})[field] = scrambled
        if _save():
            return True
        if before is None:      # the save failed: memory must match the disk
            devices[device_id].pop(field, None)
        else:
            devices[device_id][field] = before
        return False


def clear(device_id, field):
    """Forget one secret. True when it is gone (also when it never existed)."""
    if not _ok(device_id, field):
        return False
    with _lock:
        devices = _load()['devices']
        row = devices.get(device_id)
        if not row or field not in row:
            return True
        before = row.pop(field)
        if not row:
            devices.pop(device_id, None)
        if _save():
            return True
        devices.setdefault(device_id, {})[field] = before
        return False


def delete(device_id):
    """Forget every secret of a device (the device was deleted)."""
    if not _ok(device_id):
        return False
    with _lock:
        devices = _load()['devices']
        before = devices.pop(device_id, None)
        if before is None or _save():
            return True
        devices[device_id] = before
        return False


def rename(old_id, new_id):
    """Move a device's secrets to its new name. Never overwrites."""
    if not _ok(old_id) or not _ok(new_id):
        return False
    with _lock:
        devices = _load()['devices']
        if old_id == new_id or old_id not in devices:
            return True
        if new_id in devices:
            logger.warning(f"[DEVICE-SECRETS] rename {old_id} -> {new_id}: target has secrets")
            return False
        devices[new_id] = devices.pop(old_id)
        if _save():
            return True
        devices[old_id] = devices.pop(new_id)
        return False


def status(device_id):
    """{field: 'set' | 'undecryptable'} - never a value."""
    if not _ok(device_id):
        return {}
    with _lock:
        row = dict(_load()['devices'].get(device_id, {}))
    out = {}
    for field, scrambled in row.items():
        try:
            _crypto().unscramble_strict(scrambled)
            out[field] = 'set'
        except Exception:
            out[field] = 'undecryptable'
    return out


def resolve(device_id):
    """The device's secrets in the clear, for a transport about to connect.
    Raises DecryptionError naming the field when one cannot be read."""
    if not _ok(device_id):
        return Secrets(str(device_id), {})
    with _lock:
        row = dict(_load()['devices'].get(device_id, {}))
    values = {}
    for field, scrambled in row.items():
        try:
            values[field] = _crypto().unscramble_strict(scrambled)
        except Exception as e:
            raise DecryptionError(
                f"The stored {field} of device '{device_id}' cannot be read on this "
                f"machine. Enter it again in Settings > Devices.") from e
    return Secrets(device_id, values)


def reload():
    """Drop the in-memory copy; the next call reads the file again."""
    global _data
    with _lock:
        _data = None
