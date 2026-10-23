# plugins/remembrance/client.py — thin HTTP client for the Remembrance vault.
# Contract: slipgate-remembrance-sapphires-future/INTEGRATION.md. Streams blobs so
# a large backup never sits fully in memory.
import hashlib

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


def upload(acct, blob_path, cadence, comment="", timeout=600):
    headers = _headers(acct, {"X-Content-SHA256": sha256_file(blob_path),
                              "Content-Type": "application/octet-stream"})
    if comment:
        headers["X-Comment"] = comment
    with open(blob_path, "rb") as body:
        r = net.post(f"{acct['server_url']}/v1/backup", params={"cadence": cadence},
                          headers=headers, data=body, timeout=timeout)
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
