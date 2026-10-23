"""[REGRESSION_GUARD] Backup encryption (Remembrance Stage 2).

Framed AES-256-GCM via a scrypt(password) key. Round-trips exactly; wrong
password and truncation/tamper fail loudly (never silent). Same format the
standalone tools/decrypt_backup.py reads.
"""
import os

import pytest

from core.backup_crypto import encrypt_file, decrypt_file, is_encrypted_backup


def _paths(tmp_path):
    return (str(tmp_path / "src"), str(tmp_path / "enc.sapphirebak"), str(tmp_path / "out"))


def test_roundtrip_multiframe(tmp_path):
    src, enc, out = _paths(tmp_path)
    data = os.urandom(9 * 1024 * 1024) + b"tail"   # > 4 MB → multiple frames
    with open(src, "wb") as f:
        f.write(data)
    encrypt_file(src, enc, "pw123")
    assert is_encrypted_backup(enc)
    decrypt_file(enc, out, "pw123")
    with open(out, "rb") as f:
        assert f.read() == data


def test_wrong_password_raises(tmp_path):
    src, enc, out = _paths(tmp_path)
    with open(src, "wb") as f:
        f.write(b"hello world" * 1000)
    encrypt_file(src, enc, "right")
    with pytest.raises(ValueError):
        decrypt_file(enc, out, "wrong")


def test_truncation_detected(tmp_path):
    src, enc, out = _paths(tmp_path)
    with open(src, "wb") as f:
        f.write(os.urandom(5 * 1024 * 1024))
    encrypt_file(src, enc, "pw")
    raw = open(enc, "rb").read()
    with open(enc, "wb") as f:
        f.write(raw[:-100])          # drop the EOF marker / tail
    with pytest.raises(ValueError):
        decrypt_file(enc, out, "pw")


def test_plain_file_not_detected_as_encrypted(tmp_path):
    p = str(tmp_path / "plain.txt")
    with open(p, "wb") as f:
        f.write(b"not encrypted")
    assert not is_encrypted_backup(p)


def test_corrupt_header_raises_valueerror(tmp_path):
    """Bad magic-but-garbage header → ValueError (not struct.error/KeyError), so the
    route returns a clean 400 instead of a 500. War-campaign fix H."""
    import struct
    from core.backup_crypto import MAGIC
    src = str(tmp_path / "c.sapphirebak")
    out = str(tmp_path / "o")
    with open(src, "wb") as f:
        f.write(MAGIC + struct.pack(">I", 999999) + b"not json at all")
    with pytest.raises(ValueError):
        decrypt_file(src, out, "pw")
