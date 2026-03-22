# plugins/discord/routes/accounts.py — Bot account management
#
# Discord uses bot tokens (simple strings) — no multi-step auth like Telegram.
# Tokens stored in PluginState (encrypted via settings scramble).

import logging

logger = logging.getLogger(__name__)


def _get_state():
    from core.plugin_loader import plugin_loader
    return plugin_loader.get_plugin_state("discord")


async def list_accounts(**kwargs):
    """GET /api/plugin/discord/accounts — list all bot accounts."""
    state = _get_state()
    accounts_meta = state.get("accounts", {})

    from plugins.discord.daemon import list_connected
    connected = list_connected()

    accounts = []
    for name, meta in accounts_meta.items():
        accounts.append({
            "name": name,
            "value": name,  # for dynamic select compatibility
            "label": meta.get("bot_name", name),
            "bot_name": meta.get("bot_name", ""),
            "bot_id": meta.get("bot_id", ""),
            "connected": name in connected,
        })

    return {"accounts": accounts}


async def add_account(**kwargs):
    """POST /api/plugin/discord/accounts — add a bot account with token."""
    body = kwargs.get("body", {})
    account_name = body.get("account_name", "").strip()
    token = body.get("token", "").strip()

    if not account_name:
        return {"error": "Account name required"}
    if not token:
        return {"error": "Bot token required"}

    # Sanitize name
    account_name = "".join(c for c in account_name if c.isalnum() or c in "-_").lower()
    if not account_name:
        return {"error": "Invalid account name"}

    # Store in plugin state
    state = _get_state()
    accounts = state.get("accounts", {})
    accounts[account_name] = {
        "token": token,
        "bot_name": "",
        "bot_id": "",
    }
    state.save("accounts", accounts)

    # Try to connect in the running daemon
    try:
        from plugins.discord.daemon import _loop, _connect_single
        if _loop and _loop.is_running():
            import asyncio
            asyncio.run_coroutine_threadsafe(_connect_single(account_name, token), _loop)
    except Exception:
        pass

    logger.info(f"[DISCORD] Added account '{account_name}'")
    return {"status": "added", "account_name": account_name}


async def delete_account(**kwargs):
    """DELETE /api/plugin/discord/accounts/{name} — remove a bot account."""
    account_name = kwargs.get("name", "")
    if not account_name:
        return {"error": "Account name required"}

    # Disconnect if running
    from plugins.discord.daemon import get_client, _clients
    client = get_client(account_name)
    if client:
        try:
            await client.close()
        except Exception:
            pass
        _clients.pop(account_name, None)

    # Remove from state
    state = _get_state()
    accounts = state.get("accounts", {})
    accounts.pop(account_name, None)
    state.save("accounts", accounts)

    logger.info(f"[DISCORD] Deleted account '{account_name}'")
    return {"status": "deleted", "account_name": account_name}


async def test_account(**kwargs):
    """POST /api/plugin/discord/accounts/{name}/test — test bot connection."""
    account_name = kwargs.get("name", "")
    if not account_name:
        return {"error": "Account name required"}

    state = _get_state()
    accounts = state.get("accounts", {})
    meta = accounts.get(account_name)
    if not meta:
        return {"error": f"Account '{account_name}' not found"}

    token = meta.get("token", "")
    if not token:
        return {"error": "No token configured"}

    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://discord.com/api/v10/users/@me",
                headers={"Authorization": f"Bot {token}"}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    bot_name = data.get("username", "Unknown")
                    # Update metadata
                    accounts[account_name]["bot_name"] = bot_name
                    accounts[account_name]["bot_id"] = data.get("id", "")
                    state.save("accounts", accounts)
                    return {"success": True, "bot_name": bot_name, "bot_id": data.get("id", "")}
                elif resp.status == 401:
                    return {"success": False, "error": "Invalid bot token"}
                else:
                    return {"success": False, "error": f"Discord API returned {resp.status}"}
    except ImportError:
        # Fallback without aiohttp
        import urllib.request
        import json as json_mod
        req = urllib.request.Request(
            "https://discord.com/api/v10/users/@me",
            headers={"Authorization": f"Bot {token}"}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json_mod.loads(resp.read())
                bot_name = data.get("username", "Unknown")
                accounts[account_name]["bot_name"] = bot_name
                accounts[account_name]["bot_id"] = data.get("id", "")
                state.save("accounts", accounts)
                return {"success": True, "bot_name": bot_name, "bot_id": data.get("id", "")}
        except Exception as e:
            return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": str(e)}
