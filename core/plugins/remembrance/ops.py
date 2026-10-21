# core/plugins/remembrance/ops.py — offsite backup operations, shared by the tool, the
# settings routes, and the cron handler. The gate (password → sentinel → cap →
# tar → encrypt → VERIFY ciphertext → unlink plaintext) is CORE's
# `backup_manager.export_encrypted` since 2026-10-06 — one copy for every
# shipper. This file owns only what is Remembrance's: the vault account, the
# cadence, the upload, and the restore download.
import logging
import shutil
import tempfile
import time
from pathlib import Path

import requests

from core import backup_crypto
from core import restore as restore_mod
from core.backup import backup_manager, BackupRefused
from core.credentials_manager import credentials
from core.plugin_loader import plugin_loader
from plugins.remembrance import client

logger = logging.getLogger(__name__)
PLUGIN = "remembrance"
CADENCES = ("daily", "weekly", "monthly", "manual")
ROW_GO = {"view": "settings", "tab": "plugins"}      # the row's click target: this plugin's panel


def _state():
    return plugin_loader.get_plugin_state(PLUGIN)


PREF_DEFAULTS = {"offsite_extra_patterns": [], "offsite_max_mb": 2048,
                 "offsite_cron_hour": None, "auto_enabled": False}


def get_prefs():
    """Plugin prefs (stored in PluginState, written by the settings panel)."""
    try:
        cfg = _state().get("config", {}) or {}
    except Exception:
        cfg = {}
    return {**PREF_DEFAULTS, **cfg}


def _set_last_result(ok, message):
    try:
        _state().save("last_result", {"ok": bool(ok), "message": message,
                                      "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
    except Exception:
        pass


def last_result():
    try:
        return _state().get("last_result", None)
    except Exception:
        return None


def _account():
    """Fully-configured vault account, or None."""
    acct = credentials.get_offsite_account()
    if acct.get("server_url") and acct.get("tenant_id") and acct.get("api_key"):
        return acct
    return None


def _offsite_password():
    """THE backup password (Settings > Backup; stored machine-bound in
    ~/.config/sapphire — never inside user/). Encrypt-or-refuse: empty means
    no upload, ever (core's export_encrypted enforces it; restore reads it)."""
    return credentials.get_backup_password() or ""


def _extra_patterns():
    raw = get_prefs().get("offsite_extra_patterns") or []
    if isinstance(raw, str):
        raw = [p.strip() for p in raw.splitlines() if p.strip()]
    return [str(p).strip() for p in raw if str(p).strip()]


def _err_from_http(e):
    code = getattr(getattr(e, "response", None), "status_code", None)
    return {
        400: "Bad request (cadence?)",
        401: "Bad tenant ID or API key",
        404: "Not found on the vault",
        410: "Vault lost the file (corruption)",
        413: "Vault is full / backup exceeds your quota",
        422: "Upload integrity check failed",
    }.get(code, f"Vault error ({code})" if code else f"Network error: {e}")


def perform_offsite_backup(cadence="daily", comment=""):
    """Sealed blob (core gate: page + offsite excludes, cap, verify) → upload,
    under ONE row in the notification center: the build, then the upload
    byte by byte, then uploaded / failed with the reason (2026-10-21: an
    11:00 run crawled for an hour at 25 KB/s and nothing in the app said so).
    Returns {ok, id, size_bytes, usage_bytes, quota_bytes, ...} or {ok:False, error}."""
    if cadence not in CADENCES:
        return {"ok": False, "error": f"Invalid cadence '{cadence}'"}
    acct = _account()
    if not acct:
        return {"ok": False, "error": "Remembrance is not configured (set server URL, tenant ID, API key)"}

    from core import notify
    rid = notify.begin(f"Offsite backup ({cadence})", source=PLUGIN, key="remembrance-offsite",
                       go=ROW_GO, body="building the encrypted blob")

    def _fail(msg):
        _set_last_result(False, msg)
        notify.finish(rid, failed=True, body=msg, title=f"Offsite backup ({cadence}) failed")
        return {"ok": False, "error": msg}

    # Build the blob in a temp dir UNDER the backup folder (a disk sibling of
    # user/, never itself backed up) — never inside user/ (would loop) and never
    # /tmp (could be RAM/tmpfs for a big blob).
    tmp = Path(tempfile.mkdtemp(prefix="remembrance_", dir=str(backup_manager.backup_dir)))
    try:
        try:
            enc_path = backup_manager.export_encrypted(
                backup_type="offsite", dest_dir=tmp, extra_patterns=_extra_patterns(),
                cap_mb=int(get_prefs().get("offsite_max_mb", 2048) or 0))
        except BackupRefused as e:
            return _fail(str(e))
        size = enc_path.stat().st_size
        mb = size // (1024 * 1024)
        notify.tick(rid, done=0, total=size, text=f"uploading {mb} MB to the vault")
        try:
            res = client.upload(acct, enc_path, cadence, comment=comment,
                                progress=lambda done, total: notify.tick(rid, done=done, total=total))
        except requests.HTTPError as e:
            return _fail(_err_from_http(e))
        except requests.RequestException as e:
            return _fail(f"Network error: {e}")
        kb = res.get("size_bytes", 0) // 1024
        _set_last_result(True, f"Uploaded {cadence} backup ({kb} KB)" + (f" — {comment}" if comment else ""))
        notify.finish(rid, title=f"Offsite backup uploaded ({cadence}, {mb} MB)")
        return {"ok": True, **res}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        # A row still active here means something above raised past us
        # (the executor logs it): never leave a bar spinning. No-op otherwise.
        notify.finish(rid, failed=True, body="ended unexpectedly; see the log")


def get_status():
    """Vault usage/quota + backup list, or {ok:False, error}."""
    acct = _account()
    if not acct:
        return {"ok": False, "configured": False, "error": "not configured"}
    try:
        data = client.list_backups(acct)
    except requests.HTTPError as e:
        return {"ok": False, "configured": True, "error": _err_from_http(e)}
    except requests.RequestException as e:
        return {"ok": False, "configured": True, "error": f"Network error: {e}"}
    return {"ok": True, "configured": True, **data}


def stage_restore(backup_id=None, cadence=None, password=None):
    """Download (latest or by id) → verify sha → decrypt → validate → stage for the
    watchdog swap. Returns the archive roots. The CALLER fires the restart.
    `password` lets a user restore on a NEW machine (the stored one won't be there)."""
    acct = _account()
    if not acct:
        raise ValueError("Remembrance is not configured")
    pw = password or _offsite_password()
    if not pw:
        raise ValueError("Backup password required to decrypt the offsite backup")
    restore_mod.RESTORE_DIR.mkdir(parents=True, exist_ok=True)
    enc = restore_mod.RESTORE_DIR / "offsite_download.sapphirebak"
    dec = restore_mod.RESTORE_DIR / "offsite_decrypted.tar.gz"
    try:
        client.download(acct, enc, backup_id=backup_id, cadence=cadence)   # verifies sha256
        if backup_crypto.is_encrypted_backup(enc):
            backup_crypto.decrypt_file(enc, dec, pw)                       # ValueError on wrong pw
        else:
            shutil.copy2(enc, dec)
        roots = restore_mod.validate_tar(dec)
        restore_mod.request_restore(dec, source=f"remembrance:{backup_id or 'latest'}", trusted=True)
        return roots
    finally:
        enc.unlink(missing_ok=True)
        if dec.exists():       # success moved it to STAGED; this cleans the failure case
            dec.unlink()


def delete_backup(backup_id):
    acct = _account()
    if not acct:
        return {"ok": False, "error": "not configured"}
    try:
        return {"ok": True, **client.delete(acct, backup_id)}
    except requests.HTTPError as e:
        return {"ok": False, "error": _err_from_http(e)}
    except requests.RequestException as e:
        return {"ok": False, "error": f"Network error: {e}"}


def test_connection():
    acct = _account()
    if not acct:
        return {"ok": False, "error": "not configured"}
    try:
        client.health(acct["server_url"])
        client.list_backups(acct)          # also exercises auth
        return {"ok": True}
    except requests.HTTPError as e:
        return {"ok": False, "error": _err_from_http(e)}
    except requests.RequestException as e:
        return {"ok": False, "error": f"Network error: {e}"}
