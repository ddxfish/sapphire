# plugins/telegram/routes/auth.py — Multi-step Telethon auth + account management
#
# Auth flow: start_auth(phone) → Telegram sends code → submit_code(code) → done
# If 2FA enabled: submit_code returns needs_2fa → submit_2fa(password) → done
#
# Route handlers receive kwargs from plugin dispatcher:
#   body={}, settings={}, query={}, request=Request, plus path_params as keys

import asyncio
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

SESSION_DIR = Path(__file__).parent.parent.parent.parent / "user" / "plugin_state" / "telegram_sessions"

# Pending auth state: {account_name: {client, phone_hash}}
_pending_auth = {}


def _get_settings():
    from core.plugin_loader import plugin_loader
    return plugin_loader.get_plugin_settings("telegram")


def _get_state():
    from core.plugin_loader import plugin_loader
    return plugin_loader.get_plugin_state("telegram")


async def list_accounts(**kwargs):
    """GET /api/plugin/telegram/accounts — list all accounts."""
    SESSION_DIR.mkdir(parents=True, exist_ok=True)

    state = _get_state()
    accounts_meta = state.get("accounts", {})

    accounts = []
    for session_file in SESSION_DIR.glob("*.session"):
        name = session_file.stem
        if name.startswith("_"):
            continue
        meta = accounts_meta.get(name, {})
        from plugins.telegram.daemon import list_connected
        accounts.append({
            "name": name,
            "value": name,  # for dynamic select compatibility
            "label": meta.get("display_name", name),
            "phone": meta.get("phone", ""),
            "username": meta.get("username", ""),
            "connected": name in list_connected(),
        })

    return {"accounts": accounts}


async def start_auth(**kwargs):
    """POST /api/plugin/telegram/accounts — start auth with phone number."""
    body = kwargs.get("body", {})
    phone = body.get("phone", "").strip()
    account_name = body.get("account_name", "").strip()

    if not phone:
        return {"error": "Phone number required"}
    if not account_name:
        return {"error": "Account name required"}

    # Sanitize account name
    account_name = "".join(c for c in account_name if c.isalnum() or c in "-_").lower()
    if not account_name:
        return {"error": "Invalid account name"}

    settings = _get_settings()
    api_id = settings.get("api_id", "")
    api_hash = settings.get("api_hash", "")
    if not api_id or not api_hash:
        return {"error": "API ID and API Hash must be configured in plugin settings first"}

    SESSION_DIR.mkdir(parents=True, exist_ok=True)

    try:
        from telethon import TelegramClient

        session_path = str(SESSION_DIR / f"_{account_name}")  # temp session with _ prefix
        client = TelegramClient(session_path, int(api_id), api_hash)
        await client.connect()

        result = await client.send_code_request(phone)
        _pending_auth[account_name] = {
            "client": client,
            "phone": phone,
            "phone_hash": result.phone_code_hash,
        }

        logger.info(f"[TELEGRAM] Auth code sent to {phone} for account '{account_name}'")
        return {"status": "code_sent", "account_name": account_name}

    except Exception as e:
        logger.error(f"[TELEGRAM] Auth start failed: {e}")
        return {"error": str(e)}


async def submit_code(**kwargs):
    """POST /api/plugin/telegram/auth/code — submit the verification code."""
    body = kwargs.get("body", {})
    account_name = body.get("account_name", "").strip()
    code = body.get("code", "").strip()

    if not account_name or not code:
        return {"error": "Account name and code required"}

    pending = _pending_auth.get(account_name)
    if not pending:
        return {"error": "No pending auth for this account. Start auth first."}

    client = pending["client"]
    phone = pending["phone"]
    phone_hash = pending["phone_hash"]

    try:
        await client.sign_in(phone, code, phone_code_hash=phone_hash)
        return await _finalize_auth(account_name, client, phone)

    except Exception as e:
        err_str = str(e).lower()
        if "two-steps verification" in err_str or "2fa" in err_str or "password" in err_str:
            logger.info(f"[TELEGRAM] Account '{account_name}' requires 2FA")
            return {"status": "needs_2fa", "account_name": account_name}
        else:
            _cleanup_pending(account_name)
            logger.error(f"[TELEGRAM] Code verification failed: {e}")
            return {"error": str(e)}


async def submit_2fa(**kwargs):
    """POST /api/plugin/telegram/auth/2fa — submit 2FA password."""
    body = kwargs.get("body", {})
    account_name = body.get("account_name", "").strip()
    password = body.get("password", "")

    if not account_name or not password:
        return {"error": "Account name and password required"}

    pending = _pending_auth.get(account_name)
    if not pending:
        return {"error": "No pending auth for this account"}

    client = pending["client"]
    phone = pending["phone"]

    try:
        await client.sign_in(password=password)
        return await _finalize_auth(account_name, client, phone)

    except Exception as e:
        _cleanup_pending(account_name)
        logger.error(f"[TELEGRAM] 2FA failed: {e}")
        return {"error": str(e)}


async def delete_account(**kwargs):
    """DELETE /api/plugin/telegram/accounts/{name} — remove an account."""
    account_name = kwargs.get("name", "")
    if not account_name:
        return {"error": "Account name required"}

    # Disconnect if running
    from plugins.telegram.daemon import get_client, _clients
    client = get_client(account_name)
    if client:
        try:
            await client.disconnect()
        except Exception:
            pass
        _clients.pop(account_name, None)

    # Remove session file
    session_path = SESSION_DIR / f"{account_name}.session"
    if session_path.exists():
        session_path.unlink()

    # Remove metadata
    state = _get_state()
    accounts_meta = state.get("accounts", {})
    accounts_meta.pop(account_name, None)
    state.save("accounts", accounts_meta)

    logger.info(f"[TELEGRAM] Deleted account '{account_name}'")
    return {"status": "deleted", "account_name": account_name}


# ── Helpers ──

async def _finalize_auth(account_name, client, phone):
    """Move temp session to permanent, save metadata, clean up."""
    import shutil

    me = await client.get_me()
    await client.disconnect()

    # Move temp session to permanent
    temp_path = SESSION_DIR / f"_{account_name}.session"
    perm_path = SESSION_DIR / f"{account_name}.session"
    if temp_path.exists():
        shutil.move(str(temp_path), str(perm_path))

    # Save account metadata
    state = _get_state()
    accounts_meta = state.get("accounts", {})
    accounts_meta[account_name] = {
        "phone": phone,
        "username": me.username or "",
        "display_name": me.first_name or account_name,
        "user_id": me.id,
    }
    state.save("accounts", accounts_meta)

    # Clean up pending auth
    _pending_auth.pop(account_name, None)

    logger.info(f"[TELEGRAM] Account '{account_name}' authenticated as @{me.username or me.first_name}")

    # Try to connect in the running daemon
    try:
        from plugins.telegram.daemon import _loop, _connect_single
        if _loop and _loop.is_running():
            asyncio.run_coroutine_threadsafe(_connect_single(account_name), _loop)
    except Exception:
        pass  # daemon not running, will connect on next restart

    return {
        "status": "authenticated",
        "account_name": account_name,
        "username": me.username or "",
        "display_name": me.first_name or account_name,
    }


def _cleanup_pending(account_name):
    """Clean up a failed auth attempt."""
    pending = _pending_auth.pop(account_name, None)
    if pending:
        client = pending.get("client")
        if client:
            try:
                asyncio.ensure_future(client.disconnect())
            except Exception:
                pass
    # Remove temp session
    temp_path = SESSION_DIR / f"_{account_name}.session"
    if temp_path.exists():
        try:
            temp_path.unlink()
        except Exception:
            pass
