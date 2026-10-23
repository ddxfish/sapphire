#!/usr/bin/env python3
"""Standalone decryptor for Sapphire encrypted backups (.sapphirebak).

DISASTER RECOVERY: works WITHOUT a running Sapphire. You only need this one file,
your backup password, and the `cryptography` package (pip install cryptography).

Usage:
    python decrypt_backup.py  sapphire_2026-06-24_030000_daily.sapphirebak
    python decrypt_backup.py  backup.sapphirebak  restored.tar.gz

Then:  tar -xzf restored.tar.gz   →  gives you the `user/` folder back.

Format (mirrors core/backup_crypto.py — keep in sync):
    MAGIC(12) | header_len(4 BE) | header_json(UTF-8) | frames...
    frame = ct_len(4 BE) | nonce(12) | ciphertext(+16B tag);  ct_len==0 => EOF
    AAD per frame = frame_index as 8-byte big-endian (blocks reorder/truncation)
"""
import getpass
import json
import struct
import sys

try:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
except ImportError:
    sys.exit("This needs the 'cryptography' package:  pip install cryptography")

MAGIC = b"SAPPHIREBAK\x01"


def decrypt(src, dst, password):
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        if fin.read(len(MAGIC)) != MAGIC:
            sys.exit("Not a Sapphire encrypted backup (bad magic header).")
        hlen = struct.unpack(">I", fin.read(4))[0]
        hdr = json.loads(fin.read(hlen).decode("utf-8"))
        key = Scrypt(salt=bytes.fromhex(hdr["salt"]), length=32,
                     n=hdr["n"], r=hdr["r"], p=hdr["p"]).derive(password.encode("utf-8"))
        aes = AESGCM(key)
        i = 0
        while True:
            lenb = fin.read(4)
            if len(lenb) < 4:
                sys.exit("Truncated backup (missing EOF marker).")
            n = struct.unpack(">I", lenb)[0]
            if n == 0:
                break
            nonce, ct = fin.read(12), fin.read(n)
            if len(nonce) < 12 or len(ct) < n:
                sys.exit("Truncated backup (incomplete frame).")
            try:
                fout.write(aes.decrypt(nonce, ct, struct.pack(">Q", i)))
            except InvalidTag:
                sys.exit("Wrong password or corrupted backup.")
            i += 1


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        sys.exit(__doc__)
    src = sys.argv[1]
    if len(sys.argv) > 2:
        dst = sys.argv[2]
    elif src.endswith(".sapphirebak"):
        dst = src[:-len(".sapphirebak")] + ".tar.gz"
    else:
        dst = src + ".tar.gz"
    password = getpass.getpass("Backup password: ")
    decrypt(src, dst, password)
    print(f"Decrypted -> {dst}")
    print(f"Now run:  tar -xzf {dst}")


if __name__ == "__main__":
    main()
