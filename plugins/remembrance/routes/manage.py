# plugins/remembrance/routes/manage.py — settings-page backend for the Remembrance
# offsite vault. Handlers receive `body` (JSON) + path params as kwargs, return dict.
import logging

logger = logging.getLogger(__name__)


def get_config(**_):
    from core.credentials_manager import credentials
    from plugins.remembrance import ops
    acct = credentials.get_offsite_account()
    prefs = ops.get_prefs()
    return {
        "server_url": acct.get("server_url", ""),
        "tenant_id": acct.get("tenant_id", ""),
        "has_api_key": bool(acct.get("api_key")),
        "offsite_extra_patterns": prefs.get("offsite_extra_patterns", []),
        "offsite_max_mb": prefs.get("offsite_max_mb", 2048),
        "offsite_cron_hour": prefs.get("offsite_cron_hour"),
        "auto_enabled": prefs.get("auto_enabled", False),
        "backup_password_status": credentials.backup_password_status(),
        "last_result": ops.last_result(),
    }


def put_password(body=None, **_):
    """Set (or clear with empty) the offsite-encryption password. Stored scrambled
    in ~/.config/sapphire — never inside user/, which gets backed up."""
    from core.credentials_manager import credentials
    pw = (body or {}).get("password", "")
    if not isinstance(pw, str):
        return {"ok": False, "error": "password must be a string"}
    if not credentials.set_backup_password(pw):
        return {"ok": False, "error": "Failed to store the password"}
    return {"ok": True, "status": credentials.backup_password_status()}


def put_account(body=None, **_):
    from core.credentials_manager import credentials
    body = body or {}
    ok = credentials.set_offsite_account(
        server_url=body.get("server_url", ""),
        tenant_id=body.get("tenant_id", ""),
        api_key=body.get("api_key", ""),   # empty preserves the existing key
    )
    return {"ok": bool(ok)}


def put_config(body=None, **_):
    from plugins.remembrance import ops
    body = body or {}
    st = ops._state()
    cfg = dict(st.get("config", {}) or {})
    if "offsite_extra_patterns" in body:
        raw = body.get("offsite_extra_patterns") or []
        if isinstance(raw, str):
            raw = [p.strip() for p in raw.splitlines() if p.strip()]
        cfg["offsite_extra_patterns"] = [str(p).strip() for p in raw if str(p).strip()]
    if "offsite_max_mb" in body:
        try:
            cfg["offsite_max_mb"] = max(0, int(body.get("offsite_max_mb")))
        except (TypeError, ValueError):
            pass
    if "offsite_cron_hour" in body:
        h = body.get("offsite_cron_hour")
        try:
            cfg["offsite_cron_hour"] = None if h in (None, "") else max(0, min(23, int(h)))
        except (TypeError, ValueError):
            pass
    if "auto_enabled" in body:
        cfg["auto_enabled"] = bool(body.get("auto_enabled"))
    st.save("config", cfg)
    return {"ok": True, **cfg}


def status(**_):
    from plugins.remembrance import ops
    return ops.get_status()


def do_backup(body=None, **_):
    from plugins.remembrance import ops
    body = body or {}
    return ops.perform_offsite_backup(cadence=body.get("cadence", "manual"),
                                      comment=body.get("comment", ""))


def del_backup(backup_id=None, **_):
    from plugins.remembrance import ops
    if not backup_id:
        return {"ok": False, "error": "missing backup id"}
    return ops.delete_backup(backup_id)


def restore(body=None, **_):
    from plugins.remembrance import ops
    body = body or {}
    try:
        roots = ops.stage_restore(backup_id=(body.get("backup_id") or None),
                                  password=(body.get("password") or None))
    except (ValueError, IOError) as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        logger.error(f"[remembrance] restore staging failed: {e}")
        return {"ok": False, "error": f"Restore failed: {e}"}
    # Fire the restart after the response flushes (sync-safe; mirrors the local delay).
    try:
        from core.api_fastapi import get_restart_callback
        cb = get_restart_callback()
        if cb:
            import threading
            threading.Timer(0.8, cb).start()
    except Exception as e:
        logger.error(f"[remembrance] could not schedule restart: {e}")
        return {"ok": False, "error": "Staged, but couldn't auto-restart — restart Sapphire manually."}
    return {"ok": True, "roots": roots}


def test(**_):
    from plugins.remembrance import ops
    return ops.test_connection()


def download_blob(backup_id=None, **_):
    """Stream a stored backup's encrypted bytes straight to the browser — NO disk
    write, so it never lands in user/ and can't cause a backup loop. The user
    decrypts locally with tools/decrypt_backup.py."""
    import requests
    from core import net
    from fastapi.responses import JSONResponse, StreamingResponse
    from plugins.remembrance import ops
    if not backup_id:
        return JSONResponse({"ok": False, "error": "missing backup id"}, status_code=400)
    acct = ops._account()
    if not acct:
        return JSONResponse({"ok": False, "error": "not configured"}, status_code=400)
    try:
        r = net.get(f"{acct['server_url']}/v1/backup/{backup_id}",
                         headers={"X-Tenant-Id": acct["tenant_id"], "X-Api-Key": acct["api_key"]},
                         stream=True, timeout=600)
        r.raise_for_status()
    except requests.HTTPError as e:
        return JSONResponse({"ok": False, "error": ops._err_from_http(e)}, status_code=502)
    except requests.RequestException as e:
        return JSONResponse({"ok": False, "error": f"Network error: {e}"}, status_code=502)

    def _gen():
        try:
            for chunk in r.iter_content(1024 * 1024):
                if chunk:
                    yield chunk
        finally:
            r.close()

    safe = "".join(c for c in str(backup_id) if c.isalnum())[:16] or "backup"
    return StreamingResponse(_gen(), media_type="application/octet-stream",
                             headers={"Content-Disposition": f'attachment; filename="remembrance_{safe}.sapphirebak"'})
