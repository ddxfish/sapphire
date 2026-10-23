# core/backup_crypto.py — framed AES-256-GCM encryption for backups.
#
# Zero-knowledge: the key is derived from the USER's password (scrypt), so a
# backup restores on any machine with the password and nothing else can read it.
# Framed so a 1 GB backup encrypts/decrypts in flat ~4 MB memory.
#
# FILE FORMAT (also implemented standalone in tools/decrypt_backup.py — keep in sync):
#   MAGIC (12 bytes) | header_len (4 BE) | header_json (UTF-8) | frames...
#   header_json = {"v":1,"kdf":"scrypt","n":N,"r":R,"p":P,"salt":hex,"chunk":C}
#   frame       = ct_len (4 BE) | nonce (12) | ciphertext(+16B tag)
#   ct_len == 0 => EOF marker (guards against silent truncation)
#   AAD per frame = frame_index as 8-byte BE (prevents reorder/duplication)
import json
import os
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"SAPPHIREBAK\x01"      # 12 bytes (11 + version byte 0x01)
_N, _R, _P = 2 ** 14, 8, 1      # scrypt params (~16 MB work)
CHUNK = 4 * 1024 * 1024         # 4 MB plaintext frames


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(password.encode("utf-8"))


def encrypt_file(src_path, dst_path, password: str):
    """Encrypt src_path → dst_path with a scrypt(password)-derived AES-256-GCM key."""
    salt = os.urandom(16)
    aes = AESGCM(_derive(password, salt, _N, _R, _P))
    header = json.dumps({"v": 1, "kdf": "scrypt", "n": _N, "r": _R, "p": _P,
                         "salt": salt.hex(), "chunk": CHUNK}).encode("utf-8")
    with open(src_path, "rb") as fin, open(dst_path, "wb") as fout:
        fout.write(MAGIC)
        fout.write(struct.pack(">I", len(header)))
        fout.write(header)
        i = 0
        while True:
            chunk = fin.read(CHUNK)
            if not chunk:
                break
            nonce = os.urandom(12)
            ct = aes.encrypt(nonce, chunk, struct.pack(">Q", i))
            fout.write(struct.pack(">I", len(ct)))
            fout.write(nonce)
            fout.write(ct)
            i += 1
        fout.write(struct.pack(">I", 0))   # EOF marker


def decrypt_file(src_path, dst_path, password: str):
    """Decrypt src_path → dst_path. Raises ValueError on wrong password / tamper /
    truncation (the GCM tag + EOF marker make all three loud, not silent)."""
    with open(src_path, "rb") as fin, open(dst_path, "wb") as fout:
        if fin.read(len(MAGIC)) != MAGIC:
            raise ValueError("Not a Sapphire encrypted backup (bad magic header)")
        try:
            hlen = struct.unpack(">I", fin.read(4))[0]
            hdr = json.loads(fin.read(hlen).decode("utf-8"))
            aes = AESGCM(_derive(password, bytes.fromhex(hdr["salt"]), hdr["n"], hdr["r"], hdr["p"]))
        except (struct.error, ValueError, KeyError, TypeError, UnicodeDecodeError) as e:
            raise ValueError(f"Corrupt or unsupported backup header: {e}")
        i = 0
        while True:
            lenb = fin.read(4)
            if len(lenb) < 4:
                raise ValueError("Truncated backup (missing EOF marker)")
            n = struct.unpack(">I", lenb)[0]
            if n == 0:
                break
            nonce = fin.read(12)
            ct = fin.read(n)
            if len(nonce) < 12 or len(ct) < n:
                raise ValueError("Truncated backup (incomplete frame)")
            try:
                fout.write(aes.decrypt(nonce, ct, struct.pack(">Q", i)))
            except InvalidTag:
                raise ValueError("Wrong password or corrupted backup")
            i += 1


def is_encrypted_backup(path) -> bool:
    """Cheap magic check — does this file look like a Sapphire encrypted backup?"""
    try:
        with open(path, "rb") as f:
            return f.read(len(MAGIC)) == MAGIC
    except OSError:
        return False
