# core/plugins/remembrance/client.py — thin HTTP client for the Remembrance vault.
# Contract: slipgate-remembrance-sapphires-future/INTEGRATION.md. Streams blobs so
# a large backup never sits fully in memory.
import hashlib
import os

import requests

from core import net

CHUNK = 1024 * 1024


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(CHUNK), b""):
            h.update(c)
    return h.hexdigest()


def _headers(acct, extra=None):
    h = {"X-Tenant-Id": acct["tenant_id"], "X-Api-Key": acct["api_key"]}
    if extra:
        h.update(extra)
    return h


def health(server_url, timeout=10):
    r = net.get(f"{server_url}/health", timeout=timeout)
    r.raise_for_status()
    return r.json()


class _Counted:
    """The blob as requests reads it, 8 KB at a time; each read reports
    (bytes so far, total) to `progress`. __len__ keeps the Content-Length
    header the vault expects (requests' super_len reads it first)."""

    def __init__(self, f, size, progress):
        self._f, self._size, self._progress, self._done = f, size, progress, 0

    def __len__(self):
        return self._size

    def read(self, n=-1):
        chunk = self._f.read(n)
        self._done += len(chunk)
        if self._progress and chunk:
            self._progress(self._done, self._size)
        return chunk


def upload(acct, blob_path, cadence, comment="", timeout=600, progress=None):
    """POST the sealed blob. `progress(done, total)` is called as the bytes go
    out (the notification center's bar; 2026-10-21, the hour-long crawl)."""
    headers = _headers(acct, {"X-Content-SHA256": sha256_file(blob_path),
                              "Content-Type": "application/octet-stream"})
    if comment:
        headers["X-Comment"] = comment
    size = os.path.getsize(blob_path)
    with open(blob_path, "rb") as f:
        r = net.post(f"{acct['server_url']}/v1/backup", params={"cadence": cadence},
                          headers=headers, data=_Counted(f, size, progress), timeout=timeout)
    r.raise_for_status()
    return r.json()


def list_backups(acct, timeout=30):
    r = net.get(f"{acct['server_url']}/v1/backups", headers=_headers(acct), timeout=timeout)
    r.raise_for_status()
    return r.json()


def download(acct, out_path, backup_id=None, cadence=None, timeout=600):
    """Stream a backup to out_path and verify X-Content-SHA256 before returning."""
    if backup_id:
        url, params = f"{acct['server_url']}/v1/backup/{backup_id}", {}
    else:
        url, params = f"{acct['server_url']}/v1/backup/latest", ({"cadence": cadence} if cadence else {})
    with net.get(url, params=params, headers=_headers(acct), stream=True, timeout=timeout) as r:
        r.raise_for_status()
        expected = r.headers.get("X-Content-SHA256")
        h = hashlib.sha256()
        with open(out_path, "wb") as f:
            for c in r.iter_content(CHUNK):
                if c:
                    h.update(c)
                    f.write(c)
    if expected and h.hexdigest() != expected:
        raise IOError("integrity check failed — downloaded blob does not match server hash")
    return out_path


def delete(acct, backup_id, timeout=30):
    r = net.delete(f"{acct['server_url']}/v1/backup/{backup_id}", headers=_headers(acct), timeout=timeout)
    r.raise_for_status()
    return r.json()
