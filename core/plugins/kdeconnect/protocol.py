# core/plugins/kdeconnect/protocol.py - the KDE Connect wire, protocol version 8.
#
# Verified against kdeconnect-kde and kdeconnect-android master, 2026-10-12
# (tmp/kdeconnect-plan.md section 2). Nothing here knows about Sapphire: a
# packet is one line of compact JSON; identity, TLS and pairing are plain
# functions, so a test can run two peers on the loopback.
#
# The shape of a packet:
#   {"id": <epoch ms>, "type": "kdeconnect.x", "body": {...}}
#   + "payloadSize" and "payloadTransferInfo": {"port": n} when bytes ride along.
import hashlib
import json
import re
import ssl
import time
import uuid
from datetime import datetime, timedelta, timezone

PROTOCOL = 8
UDP_PORT = 1716
TCP_PORTS = (1716, 1764)          # the control link listens on the first free one
PAYLOAD_PORTS = (1739, 1764)      # a payload's sender listens on the first free one
MAX_IDENTITY = 8192               # bytes of an identity line
MAX_LINE = 32 * 1024 * 1024       # bytes of any other line (Android's cap)
PAIR_SKEW = 1800                  # seconds the two clocks may differ for a pairing
PAIR_WAIT = 30                    # seconds a pairing request stands
NAME_MAX = 32
TYPES = ('desktop', 'laptop', 'phone', 'tablet', 'tv')
ID_RE = re.compile(r'^[a-zA-Z0-9_-]{32,38}$')
_NAME_BAD = re.compile(r'["\',;:.!?()\[\]<>]')

IDENTITY = 'kdeconnect.identity'
PAIR = 'kdeconnect.pair'


class ProtocolError(Exception):
    pass


# --- packets ----------------------------------------------------------------------

def packet(kind, body=None, payload_size=None, port=None):
    """One packet as the bytes that go on the wire: compact JSON and a newline."""
    out = {'id': int(time.time() * 1000), 'type': kind, 'body': dict(body or {})}
    if payload_size is not None:
        out['payloadSize'] = int(payload_size)
        out['payloadTransferInfo'] = {'port': int(port)}
    return (json.dumps(out, separators=(',', ':'), ensure_ascii=False) + '\n').encode('utf-8')


def parse(line):
    """A packet from one line. ProtocolError when it is not one."""
    try:
        pkt = json.loads(line.decode('utf-8') if isinstance(line, (bytes, bytearray)) else line)
    except (ValueError, UnicodeDecodeError) as e:
        raise ProtocolError(f'not JSON: {e}')
    if not isinstance(pkt, dict) or not isinstance(pkt.get('type'), str) or not pkt['type'].startswith('kdeconnect.'):
        raise ProtocolError('not a kdeconnect packet')
    if not isinstance(pkt.get('body'), dict):
        pkt['body'] = {}
    return pkt


def payload_of(pkt):
    """(size, port) when the packet announces bytes to fetch, else None."""
    size, info = pkt.get('payloadSize'), pkt.get('payloadTransferInfo')
    if isinstance(size, int) and size > 0 and isinstance(info, dict) and isinstance(info.get('port'), int):
        return size, info['port']
    return None


# --- who we are -----------------------------------------------------------------

def new_id():
    """A device id the way the Android app makes one: a UUID without hyphens."""
    return uuid.uuid4().hex


def clean_name(text, fallback='Sapphire'):
    """A device name the protocol allows: 1-32 characters, none of the forbidden ones."""
    name = ' '.join(_NAME_BAD.sub('', str(text or '')).split())[:NAME_MAX].strip()
    return name or fallback


def udp_identity(device_id, name, tcp_port):
    """What is broadcast: enough to be found, no more (v8)."""
    return packet(IDENTITY, {'deviceId': device_id, 'deviceName': name,
                             'protocolVersion': PROTOCOL, 'tcpPort': int(tcp_port)})


def connection_packet(device_id, name, target_id, target_version=PROTOCOL):
    """The one plaintext line the TCP client writes before TLS (v8)."""
    return packet(IDENTITY, {'deviceId': device_id, 'deviceName': name, 'protocolVersion': PROTOCOL,
                             'targetDeviceId': target_id, 'targetProtocolVersion': int(target_version)})


def full_identity(device_id, name, kind, incoming, outgoing):
    """The identity exchanged inside TLS: who, what kind, what it takes and sends."""
    return packet(IDENTITY, {'deviceId': device_id, 'deviceName': name, 'deviceType': kind,
                             'protocolVersion': PROTOCOL, 'incomingCapabilities': list(incoming),
                             'outgoingCapabilities': list(outgoing)})


def valid_id(text):
    return isinstance(text, str) and bool(ID_RE.match(text))


# --- certificates ----------------------------------------------------------------

def make_identity(device_id, days=3650):
    """(key_pem, cert_pem): a self-signed certificate the way KDE Connect makes
    one. CN is the device id (the desktop checks it against the id it was
    told), O and OU as theirs, valid from a year ago (clocks) for ten years."""
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, device_id),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'KDE'),
                      x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, 'KDE Connect')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=365)).not_valid_after(now + timedelta(days=days))
            .sign(key, hashes.SHA512()))
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    return key_pem, cert.public_bytes(serialization.Encoding.PEM)


def cert_der(pem):
    return ssl.PEM_cert_to_DER_cert(pem.decode('ascii') if isinstance(pem, bytes) else pem)


def cert_pem(der):
    return ssl.DER_cert_to_PEM_cert(der)


def cert_cn(der):
    """The certificate's common name, or ''."""
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    try:
        names = x509.load_der_x509_certificate(der).subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        return str(names[0].value) if names else ''
    except Exception:
        return ''


def public_key_der(der):
    """The certificate's public key as SubjectPublicKeyInfo DER: what both
    apps hash for the pairing key (Java's PublicKey.getEncoded, Qt's toDer)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    return x509.load_der_x509_certificate(der).public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)


def verification_key(our_der, their_der, timestamp):
    """The 8 characters both screens show during a pairing. SHA-256 over the
    two public keys, the larger first (unsigned bytes), then the request's
    timestamp in seconds as decimal text; the first 8 hex digits, upper case."""
    a, b = public_key_der(our_der), public_key_der(their_der)
    if a < b:
        a, b = b, a
    return hashlib.sha256(a + b + str(int(timestamp)).encode('ascii')).hexdigest()[:8].upper()


# --- TLS ------------------------------------------------------------------------------

def client_context(cert_file, key_file):
    """We are the TLS client (the other side opened TLS as server): we show
    our certificate when asked and take theirs as it is. The caller pins it
    by comparing bytes with the one it trusts; an unpaired peer has none yet."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.load_cert_chain(cert_file, key_file)
    return ctx


def server_context(cert_file, key_file, peer_pem):
    """We are the TLS server, for a PAIRED peer only: its certificate is the
    one trust anchor and it has to present it. (As a server Python cannot ask
    for a certificate it will not check, which is why an unpaired peer always
    connects to us and we are the client.)"""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    ctx.load_verify_locations(cadata=peer_pem.decode('ascii') if isinstance(peer_pem, bytes) else peer_pem)
    ctx.load_cert_chain(cert_file, key_file)
    return ctx


def read_line(sock, limit, timeout):
    """One newline-terminated line from a socket, at most `limit` bytes, within
    `timeout` seconds. ProtocolError on anything else. Reads byte by byte: the
    line is short and the socket is about to change hands (TLS)."""
    sock.settimeout(timeout)
    buf = bytearray()
    try:
        while True:
            b = sock.recv(1)
            if not b:
                raise ProtocolError('closed before a whole line')
            if b == b'\n':
                return bytes(buf)
            buf += b
            if len(buf) > limit:
                raise ProtocolError('line too long')
    except (OSError, ssl.SSLError) as e:
        raise ProtocolError(f'{type(e).__name__}: {e}')


def read_exactly(sock, size, limit, timeout):
    """`size` bytes from a socket (a payload). ProtocolError when it ends early
    or is larger than `limit`."""
    if size > limit:
        raise ProtocolError(f'payload of {size} bytes is over the {limit} byte limit')
    sock.settimeout(timeout)
    out, left = bytearray(), size
    try:
        while left > 0:
            chunk = sock.recv(min(left, 256 * 1024))
            if not chunk:
                raise ProtocolError(f'payload ended after {len(out)} of {size} bytes')
            out += chunk
            left -= len(chunk)
    except (OSError, ssl.SSLError) as e:
        raise ProtocolError(f'{type(e).__name__}: {e}')
    return bytes(out)
