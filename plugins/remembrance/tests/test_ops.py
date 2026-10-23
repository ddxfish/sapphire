"""[REGRESSION_GUARD] Remembrance offsite plugin — ops + cron gating.

Locks the load-bearing rules with NO network and NO real state:
- encrypt-or-refuse (never upload without a password / when unconfigured),
- the runaway size cap, cadence validation, temp cleanup,
- cron fires ONLY at the configured hour when auto-backup is on,
- HTTP error → friendly message mapping.
"""
from datetime import datetime

import pytest

from plugins.remembrance import ops, schedule


@pytest.fixture
def env(monkeypatch, tmp_path):
    # No PluginState / user/ writes; backup temp lands in tmp_path.
    monkeypatch.setattr(ops, "_set_last_result", lambda ok, msg: None)
    monkeypatch.setattr(ops, "get_prefs", lambda: {
        "offsite_extra_patterns": [], "offsite_max_mb": 2048,
        "offsite_cron_hour": None, "auto_enabled": False})
    monkeypatch.setattr(ops.backup_manager, "backup_dir", tmp_path)
    return monkeypatch


def _account(mp, configured=True, password="pw"):
    acct = ({"server_url": "https://v", "tenant_id": "t", "api_key": "k"} if configured
            else {"server_url": "", "tenant_id": "", "api_key": ""})
    mp.setattr(ops.credentials, "get_offsite_account", lambda: acct)
    mp.setattr(ops.credentials, "get_backup_password", lambda: password)


def test_refuse_when_not_configured(env):
    _account(env, configured=False)
    r = ops.perform_offsite_backup("daily")
    assert not r["ok"] and "not configured" in r["error"].lower()


def test_encrypt_or_refuse_without_password(env):
    """The load-bearing rule: no backup password → refuse, never upload plaintext."""
    _account(env, password="")
    r = ops.perform_offsite_backup("daily")
    assert not r["ok"] and "password" in r["error"].lower()


def test_invalid_cadence(env):
    _account(env)
    r = ops.perform_offsite_backup("hourly")
    assert not r["ok"] and "cadence" in r["error"].lower()


def test_cap_exceeded_refuses_before_creating(env):
    _account(env)
    env.setattr(ops, "get_prefs", lambda: {"offsite_extra_patterns": [], "offsite_max_mb": 1,
                                            "offsite_cron_hour": None, "auto_enabled": False})
    env.setattr(ops.backup_manager, "estimate_size", lambda extra_patterns=None: {"total_bytes": 50 * 1024 * 1024})
    called = []
    env.setattr(ops.backup_manager, "create_backup", lambda *a, **k: called.append(1))
    r = ops.perform_offsite_backup("daily")
    assert not r["ok"] and "cap" in r["error"].lower()
    assert called == []   # refused BEFORE building a giant blob


def _fake_create(backup_type, extra_patterns=None, dest_dir=None):
    """Stand-in for core create_backup: writes a plain tar.gz (as core now
    always does — encryption moved into this plugin)."""
    import tarfile
    from pathlib import Path
    p = Path(dest_dir) / "sapphire_x_offsite.tar.gz"
    with tarfile.open(p, "w:gz"):
        pass
    return p.name


def test_happy_path_encrypts_verifies_uploads(env, tmp_path):
    """Plain tar from core → REAL encrypt in ops → ciphertext verify → upload.
    The plaintext tar must be gone by upload time; temp dir cleaned after."""
    _account(env)
    env.setattr(ops.backup_manager, "estimate_size", lambda extra_patterns=None: {"total_bytes": 1000})
    env.setattr(ops.backup_manager, "create_backup", _fake_create)

    seen = {}
    def fake_upload(acct, blob, cadence, comment=""):
        seen.update(cadence=cadence, comment=comment, blob_exists=blob.exists(),
                    blob_name=blob.name,
                    plaintext_gone=not (blob.parent / "sapphire_x_offsite.tar.gz").exists(),
                    is_ciphertext=ops.backup_crypto.is_encrypted_backup(blob))
        return {"id": "abc123", "size_bytes": 4, "usage_bytes": 4, "quota_bytes": 1000}
    env.setattr(ops.client, "upload", fake_upload)

    r = ops.perform_offsite_backup("daily", comment="before migration")
    assert r["ok"] and r["id"] == "abc123"
    assert seen["cadence"] == "daily" and seen["comment"] == "before migration" and seen["blob_exists"]
    assert seen["blob_name"].endswith(".sapphirebak")
    assert seen["is_ciphertext"]          # what went up carries the encrypted magic
    assert seen["plaintext_gone"]         # plaintext unlinked BEFORE the upload
    assert list(tmp_path.glob("remembrance_*")) == []   # temp dir cleaned up


def test_verify_blocks_plaintext_upload(env, tmp_path):
    """The pre-upload check: if 'encryption' silently passed plaintext through
    (blob still opens as tar), the upload MUST be refused — never trust
    encrypt_file, prove it."""
    import shutil as sh
    _account(env)
    env.setattr(ops.backup_manager, "estimate_size", lambda extra_patterns=None: {"total_bytes": 1000})
    env.setattr(ops.backup_manager, "create_backup", _fake_create)
    env.setattr(ops.backup_crypto, "encrypt_file", lambda src, dst, pw: sh.copy2(src, dst))

    uploads = []
    env.setattr(ops.client, "upload", lambda *a, **k: uploads.append(1))

    r = ops.perform_offsite_backup("daily")
    assert not r["ok"]
    assert "plaintext" in r["error"].lower()
    assert uploads == []                                # nothing left the machine
    assert list(tmp_path.glob("remembrance_*")) == []   # temp dir cleaned up


def test_err_from_http_maps_quota():
    import requests
    e = requests.HTTPError()
    e.response = type("R", (), {"status_code": 413})()
    msg = ops._err_from_http(e).lower()
    assert "quota" in msg or "full" in msg


# --- cron gating (schedule.run) -------------------------------------------------

def _no_real_backup(mp):
    hits = []
    mp.setattr(ops, "perform_offsite_backup", lambda **k: (hits.append(k), {"ok": True})[1])
    return hits


def test_cron_skips_when_auto_disabled(monkeypatch):
    monkeypatch.setattr(ops, "get_prefs", lambda: {"auto_enabled": False, "offsite_cron_hour": 4})
    hits = _no_real_backup(monkeypatch)
    assert schedule.run({"config": None}) is None and hits == []


def test_cron_skips_wrong_hour(monkeypatch):
    monkeypatch.setattr(ops, "get_prefs", lambda: {"auto_enabled": True,
                                                   "offsite_cron_hour": (datetime.now().hour + 1) % 24})
    hits = _no_real_backup(monkeypatch)
    assert schedule.run({"config": None}) is None and hits == []


def test_cron_fires_at_configured_hour(monkeypatch):
    monkeypatch.setattr(ops, "get_prefs", lambda: {"auto_enabled": True,
                                                   "offsite_cron_hour": datetime.now().hour})
    hits = _no_real_backup(monkeypatch)
    schedule.run({"config": None})
    assert len(hits) == 1
