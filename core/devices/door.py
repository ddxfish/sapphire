# core/devices/door.py - a paired device opens one of a plugin's sockets
#
# A plugin's socket normally needs a login session (core/auth.py
# require_login_ws). A socket whose manifest entry says "door": "device" is
# for a device instead - a browser extension, a board: no cookie, a proof made
# with the key stored for that device. Nothing else in core takes this path.
#
# The proof rides the handshake as a WebSocket subprotocol, because a browser
# cannot set a header on a socket. The caller offers two:
#
#   sapphire-device
#   sapphire.v1.<key id>.<unix seconds>.<nonce>.<hex HMAC-SHA256>
#
# signed with the key over "<plugin>/<path>\n<key id>\n<seconds>\n<nonce>".
# The device made the key id and the key itself; the person pasted
# "<key id>.<key>" into the device's `socket_key` field when adding it. So the
# key never crosses the wire, and a proof opens this one socket, once, within
# a few minutes of when it was made.
import hashlib
import hmac
import logging
import re
import threading
import time
from collections import defaultdict

logger = logging.getLogger(__name__)

NAME = 'sapphire-device'       # the subprotocol the socket is accepted with
FIELD = 'socket_key'           # the secret field a driver declares for it
WINDOW = 300                   # seconds a proof's clock may be off, either way
PER_MIN = 30                   # proofs a minute for one key id from one address, counted before any key is looked at
KEY_MIN = 32                   # characters: a shorter stored key opens nothing (one proof seen on the wire could be guessed at)
RECHECK = 20                   # seconds between looks at an OPEN socket's device: disabled, removed or re-keyed, it closes
_PROOF = re.compile(r'sapphire\.v1\.([A-Za-z0-9_-]{4,32})\.(\d{9,11})\.([A-Za-z0-9_-]{8,64})\.([0-9a-f]{64})$')

_lock = threading.Lock()
_tries = defaultdict(list)     # (caller address, key id) -> when it tried
_used = {}                     # (key id, nonce) of a proof that opened a socket -> when it expires


def _engine():
    from core.devices import engine
    return engine


def signed(plugin, path, key_id, seconds, nonce, key):
    """The signature a proof must carry."""
    text = f"{plugin}/{path.strip('/')}\n{key_id}\n{seconds}\n{nonce}"
    return hmac.new(str(key).encode(), text.encode(), hashlib.sha256).hexdigest()


def _counted(who, now):
    """True when this address may try this key id again; every try counts,
    good or bad. Per key id, so a stranger at the same address (a web page in
    that very browser) cannot use up the real device's tries: it would have to
    know the key id."""
    with _lock:
        for a in [a for a, ts in _tries.items() if not ts or now - ts[-1] > 60]:
            del _tries[a]
        recent = _tries[who] = [t for t in _tries[who] if now - t < 60]
        if len(recent) >= PER_MIN:
            return False
        recent.append(now)
        return True


def holder(plugin, key_id):
    """The id of the device that holds this key id for one of this plugin's
    drivers, switched on or not, or None: for a plugin about to add a device
    for a key (is it one already?). Blocking."""
    return _holder(plugin, str(key_id or ''), enabled_only=False)[0]


def _holder(plugin, key_id, enabled_only=True):
    """(device id, key) of the enabled device that holds this key id for one
    of this plugin's drivers, or (None, '')."""
    e = _engine()
    for row in e.rows().values():
        if enabled_only and not row.get('enabled', True):
            continue
        for part in row.get('parts', []):
            if part.get('plugin') != plugin:
                continue
            try:
                stored = e._part_secrets(row['id'], part['driver']).get(FIELD) or ''
            except Exception:
                continue
            kid, _, key = str(stored).strip().partition('.')
            if len(key) >= KEY_MIN and hmac.compare_digest(kid.encode(), key_id.encode()):
                return row['id'], key
    return None, ''


def still(plugin, device, key_id):
    """True while the device that opened a socket would still be let in: it
    exists, is enabled, and holds the same key id for this plugin. The route
    asks every RECHECK seconds, so switching a device off, removing it or
    giving it a new code ends its open socket too. Blocking, like prove."""
    try:
        return not _engine().refusal() and _holder(plugin, key_id)[0] == device
    except Exception as e:
        logger.warning(f"[DEVICES] socket door {plugin}: {e!r}")
        return False


def prove(plugin, path, offered, address):
    """(device id, key id) of the device that proved itself for this socket,
    or None. Blocking (it reads the secret store): call it off the event
    loop. One answer for every refusal, so names, keys and plugins cannot
    be probed."""
    now = time.time()
    try:
        if _engine().refusal():
            return None
        m = next((m for m in map(_PROOF.match, [str(o) for o in (offered or [])][:4]) if m), None)
        if not m:
            return None                           # nothing that could be a proof: nothing was looked up
        key_id, seconds, nonce, sig = m.groups()
        if abs(now - int(seconds)) > WINDOW or not _counted((str(address), key_id), now):
            return None
        device, key = _holder(plugin, key_id)
        if not device or not hmac.compare_digest(signed(plugin, path, key_id, seconds, nonce, key), sig):
            logger.info(f"[DEVICES] socket door {plugin}/{path.strip('/')}: a proof from {address} was refused")
            return None
        with _lock:                               # a proof opens one socket: a copy of it opens none
            for k in [k for k, until in _used.items() if until < now]:
                del _used[k]
            if (key_id, nonce) in _used:
                return None
            _used[(key_id, nonce)] = now + 2 * WINDOW
        from core.devices import health
        health.seen(device)                       # it spoke with its own key: it is online
        return device, key_id
    except Exception as e:
        logger.warning(f"[DEVICES] socket door {plugin}/{path}: {e!r}")
        return None
