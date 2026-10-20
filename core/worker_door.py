"""The worker door: a worker (Kokoro, a FlashHead GPU) shared on the network, safely.

One file, stdlib + `cryptography` (server half) + `requests` (client
half), no Sapphire imports: a plugin's standalone server carries a copy
and must speak exactly this.

Server half: <folder>/ holds a self-signed cert (cert.pem, key.pem,
made once) and one key per client (clients.json, {name: key}). serve()
opens a TLS listener; client_of() names the client a request's key
belongs to, None means refuse. The client's name is its session.

Client half: a pairing string carries everything,
    https://<host>:<port>/#key=<key>&pin=<sha256 of the worker's cert>
context() fetches the worker's cert once, checks it against the pin and
from then on trusts that cert only (no CA, no hostname: an IP can change,
the cert can't). A worker that made a new cert fails loudly until its
new pairing string is pasted.

CLI (for a worker with no Sapphire around it):
    python worker_door.py add <folder> <name> <port|host:port>   prints a pairing string
    python worker_door.py remove <folder> <name>
    python worker_door.py list <folder>
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import ssl
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

_NAME = re.compile(r"[^a-z0-9_-]")


# ── server half ──────────────────────────────────────────────────────────

def ensure_cert(folder):
    """(cert.pem, key.pem) in folder, made on first use."""
    folder = Path(folder)
    cert, key = folder / "cert.pem", folder / "key.pem"
    if not (cert.exists() and key.exists()):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
        folder.mkdir(parents=True, exist_ok=True)
        k = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Sapphire worker")])
        now = datetime.now(timezone.utc)
        c = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
             .public_key(k.public_key()).serial_number(x509.random_serial_number())
             .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=3650))
             .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
             .sign(k, hashes.SHA256()))
        _write_private(key, k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                            serialization.NoEncryption()))
        cert.write_bytes(c.public_bytes(serialization.Encoding.PEM))
    return cert, key


def pin(folder):
    """The cert's fingerprint, the half of a pairing string that proves the worker."""
    cert, _ = ensure_cert(folder)
    return _fingerprint(cert.read_text(encoding="ascii"))


def _fingerprint(pem):
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()


def _write_private(path, data):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


_cache = {}                 # clients.json path -> (mtime, {name: key})
_lock = threading.Lock()


def clients(folder):
    """{name: key} of every client let in."""
    path = Path(folder) / "clients.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    with _lock:
        hit = _cache.get(path)
        if hit and hit[0] == mtime:
            return dict(hit[1])
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data = {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except (OSError, ValueError):
            data = {}
        _cache[path] = (mtime, data)
        return dict(data)


def slug(name):
    return _NAME.sub("", str(name or "").strip().lower().replace(" ", "-"))[:32]


def add_client(folder, name):
    """A new key for `name` (replacing any it had); returns the key."""
    name = slug(name)
    if not name:
        raise ValueError("a client needs a name (letters, numbers, - or _)")
    data = clients(folder)
    data[name] = secrets.token_urlsafe(24)
    Path(folder).mkdir(parents=True, exist_ok=True)
    _write_private(Path(folder) / "clients.json", json.dumps(data, indent=2).encode("utf-8"))
    return data[name]


def remove_client(folder, name):
    data = clients(folder)
    if data.pop(slug(name), None) is not None:
        _write_private(Path(folder) / "clients.json", json.dumps(data, indent=2).encode("utf-8"))


def client_of(folder, authorization):
    """The client name an `Authorization: Bearer <key>` header belongs to, else None."""
    auth = str(authorization or "")
    if not auth.startswith("Bearer "):
        return None
    given = auth[7:].strip().encode()
    found = None
    for name, key in clients(folder).items():          # every key compared: no early exit to time
        if hmac.compare_digest(given, key.encode()):
            found = name
    return found


def guess_host():
    """This computer's address as others on its network reach it: the
    interface the default route leaves by (a UDP connect sends nothing)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            return s.getsockname()[0]
    except OSError:
        return socket.gethostname()


def pairing(folder, name, address):
    """The string a client pastes: address is host:port as the client reaches it."""
    key = clients(folder).get(slug(name))
    if not key:
        raise KeyError(name)
    return f"https://{address}/#key={key}&pin={pin(folder)}"


def serve(server_cls, handler, host, port, folder):
    """A TLS listener for `handler`; call serve_forever() on what comes back.
    The handshake runs in the request's own thread (ThreadingMixIn), never in
    the accept loop, so one stalled caller can't hold the door shut."""
    cert, key = ensure_cert(folder)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert, key)

    class Door(server_cls):
        door = Path(folder)

        def finish_request(self, request, client_address):
            request.settimeout(10)
            try:
                tls = ctx.wrap_socket(request, server_side=True)
            except (ssl.SSLError, OSError):
                return                                      # not TLS, or gave up: nothing to answer
            tls.settimeout(None)
            self.RequestHandlerClass(tls, client_address, self)

        def handle_error(self, request, client_address):
            pass                                            # a dropped stranger is no traceback

    return Door((host, port), handler)


# ── client half ──────────────────────────────────────────────────────────

def is_pairing(text):
    return "#" in str(text or "") and "key=" in str(text) and "pin=" in str(text)


def parse(text):
    """(base_url, key, pin) from a pairing string; ValueError if it isn't one."""
    u = urlsplit(str(text or "").strip())
    f = parse_qs(u.fragment)
    key, fp = (f.get("key") or [""])[0], (f.get("pin") or [""])[0].lower()
    if u.scheme != "https" or not u.hostname or not key or not re.fullmatch(r"[0-9a-f]{64}", fp):
        raise ValueError("not a pairing string (https://host:port/#key=...&pin=...)")
    return f"https://{u.netloc}", key, fp


_contexts = {}


def context(base, fp, timeout=5):
    """An SSLContext trusting the worker's cert alone, once it matches the pin."""
    with _lock:
        hit = _contexts.get((base, fp))
    if hit:
        return hit
    u = urlsplit(base)
    pem = ssl.get_server_certificate((u.hostname, u.port or 443), timeout=timeout)
    if not hmac.compare_digest(_fingerprint(pem), fp):
        raise ssl.SSLError(f"{base} is not the worker this pairing names (its certificate changed: paste its new pairing string)")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False                              # the pin is the identity, not the address
    ctx.load_verify_locations(cadata=pem)
    with _lock:
        _contexts[(base, fp)] = ctx
    return ctx


def forget(base, fp):
    """Drop a cached context (a worker restarted with a new cert): the next call re-checks."""
    with _lock:
        _contexts.pop((base, fp), None)


def session(text, timeout=5):
    """(requests.Session, base_url) for a pairing string: pinned TLS and the key on every call."""
    import requests
    from requests.adapters import HTTPAdapter
    base, key, fp = parse(text)
    ctx = context(base, fp, timeout)

    class Pinned(HTTPAdapter):
        def init_poolmanager(self, *a, **kw):
            kw["ssl_context"] = ctx
            kw["assert_hostname"] = False
            return super().init_poolmanager(*a, **kw)

    s = requests.Session()
    s.trust_env = False                                     # a LAN worker never rides a proxy
    s.mount(base, Pinned())
    s.headers["Authorization"] = f"Bearer {key}"
    return s, base


def _main(argv):
    if len(argv) == 5 and argv[1] == "add":
        folder, name, address = argv[2], argv[3], argv[4]
        address = address if ":" in address else f"{guess_host()}:{address}"
        add_client(folder, name)
        print(pairing(folder, name, address))
    elif len(argv) == 4 and argv[1] == "remove":
        remove_client(argv[2], argv[3])
    elif len(argv) == 3 and argv[1] == "list":
        print("\n".join(clients(argv[2])) or "(no clients)")
    else:
        print(__doc__.split("CLI", 1)[1])
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
