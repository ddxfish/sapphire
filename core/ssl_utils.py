# ssl_utils.py - Persistent self-signed SSL certificate management
import os
import sys
import ipaddress
import logging
from pathlib import Path
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Default paths relative to project root
PROJECT_ROOT = Path(__file__).parent.parent
SSL_DIR = PROJECT_ROOT / "user" / "ssl"
CERT_FILE = SSL_DIR / "cert.pem"
KEY_FILE = SSL_DIR / "key.pem"


def get_ssl_context():
    """
    Get SSL context with persistent self-signed certificate.

    Generates cert on first run, reuses on subsequent runs.
    Returns None if SSL is disabled in config.
    """
    import config

    if not getattr(config, 'WEB_UI_SSL_ADHOC', False):
        return None

    # Ensure cert exists
    if not CERT_FILE.exists() or not KEY_FILE.exists():
        logger.info("SSL certificate not found, generating new one...")
        _generate_self_signed_cert()
    else:
        # Check if cert is still valid (regenerate if expired)
        if _is_cert_expired():
            logger.warning("SSL certificate expired, regenerating...")
            _generate_self_signed_cert()
        else:
            logger.info(f"Using existing SSL certificate: {CERT_FILE}")

    return (str(CERT_FILE), str(KEY_FILE))


def _generate_self_signed_cert():
    """Generate a self-signed certificate valid for 10 years."""
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    # Ensure directory exists
    SSL_DIR.mkdir(parents=True, exist_ok=True)

    # Generate private key
    key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )

    # Certificate subject/issuer
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "Sapphire Local"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Sapphire"),
    ])

    # Build certificate
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=3650))  # 10 years
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.DNSName("127.0.0.1"),
                x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
            ]),
            critical=False,
        )
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )

    # Write key file with 0600 mode from the first byte — not write-then-chmod.
    # The write-then-chmod pattern left a micro-window where another local UID
    # could open the key for read before the mode restricted it. Mirrors the
    # MCP-key fix (2026-04-20) and matches the finding from the 2026-04-22
    # auth audit. On Windows, os.open ignores mode bits — behavior falls back
    # to default ACLs (same as previous path).
    key_bytes = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    if sys.platform != 'win32':
        # O_EXCL would block overwriting an existing key; we DO want to
        # overwrite on regen, so drop EXCL but keep the mode = 0o600 on create.
        # If the file exists, open() below will still land on 0o600 because
        # we delete first.
        try:
            if os.path.exists(KEY_FILE):
                os.remove(KEY_FILE)
            fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(fd, key_bytes)
            finally:
                os.close(fd)
        except Exception:
            # Fallback to write-then-chmod if anything in the atomic path
            # fails (NFS mounts, AppArmor, etc). Least-bad recovery.
            with open(KEY_FILE, "wb") as f:
                f.write(key_bytes)
            os.chmod(KEY_FILE, 0o600)
    else:
        with open(KEY_FILE, "wb") as f:
            f.write(key_bytes)

    # Write cert file
    with open(CERT_FILE, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    logger.info(f"Generated new SSL certificate: {CERT_FILE}")
    logger.info("First time: Browser will show security warning - click Advanced > Proceed")


def _is_cert_expired():
    """Check if the existing certificate is expired or expiring soon."""
    from cryptography import x509

    try:
        with open(CERT_FILE, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())

        now = datetime.now(timezone.utc)
        # Consider expired if less than 30 days remaining
        if cert.not_valid_after_utc < now + timedelta(days=30):
            return True
        return False
    except Exception as e:
        logger.warning(f"Could not check cert expiry: {e}")
        return True  # Regenerate if we can't read it


def cert_pem():
    """The certificate as PEM text, for a device that pins it. '' when
    SSL is off or there is no certificate yet."""
    import config
    if not getattr(config, 'WEB_UI_SSL_ADHOC', False):
        return ''
    try:
        return CERT_FILE.read_text(encoding='utf-8')
    except OSError:
        return ''


def get_cert_info():
    """Get info about the current certificate for display."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes

    if not CERT_FILE.exists():
        return None

    try:
        with open(CERT_FILE, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())

        return {
            "path": str(CERT_FILE),
            "expires": cert.not_valid_after_utc.isoformat(),
            "fingerprint": cert.fingerprint(hashes.SHA256()).hex(),
        }
    except Exception as e:
        logger.warning(f"Could not read cert info: {e}")
        return None


