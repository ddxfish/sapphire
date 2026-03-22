# plugins/telegram/daemon.py — Background Telethon client manager
#
# Manages one asyncio event loop on a daemon thread.
# Each authenticated account gets a TelethonClient that listens for messages
# and emits daemon events into Sapphire's trigger system.

import asyncio
import json
import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

SESSION_DIR = Path(__file__).parent.parent.parent / "user" / "plugin_state" / "telegram_sessions"

# Module-level state
_loop: asyncio.AbstractEventLoop = None
_thread: threading.Thread = None
_clients: dict = {}  # {account_name: TelegramClient}
_stop_event = threading.Event()
_plugin_loader = None
_api_id: int = 0
_api_hash: str = ""


def start(plugin_loader, settings):
    """Called by plugin_loader on load. Starts the daemon thread."""
    global _loop, _thread, _plugin_loader, _api_id, _api_hash

    _plugin_loader = plugin_loader
    api_id = settings.get("api_id", "")
    api_hash = settings.get("api_hash", "")
    if not api_id or not api_hash:
        logger.info("[TELEGRAM] No API credentials configured — daemon idle")
        return

    _api_id = int(api_id)
    _api_hash = api_hash

    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    _stop_event.clear()

    _loop = asyncio.new_event_loop()
    _thread = threading.Thread(target=_run_loop, args=(_plugin_loader, _api_id, _api_hash), daemon=True, name="telegram-daemon")
    _thread.start()

    # Register reply handler so daemon responses route back to Telegram
    plugin_loader.register_reply_handler("telegram", _reply_handler)
    logger.info("[TELEGRAM] Daemon thread started")


def stop():
    """Called by plugin_loader on unload. Stops all clients."""
    global _loop, _thread
    _stop_event.set()

    if _loop and _loop.is_running():
        # Schedule disconnect of all clients
        async def _shutdown():
            for name, client in list(_clients.items()):
                try:
                    await client.disconnect()
                except Exception:
                    pass
            _clients.clear()
            _loop.stop()

        asyncio.run_coroutine_threadsafe(_shutdown(), _loop)

    if _thread and _thread.is_alive():
        _thread.join(timeout=5)

    _loop = None
    _thread = None
    logger.info("[TELEGRAM] Daemon stopped")


def get_client(account_name: str):
    """Get a connected TelethonClient by account name (for tools/routes)."""
    return _clients.get(account_name)


def get_loop():
    """Get the daemon's event loop (for scheduling coroutines from sync code)."""
    return _loop


def list_connected():
    """Return list of connected account names."""
    return list(_clients.keys())


# ── Internal ──

def _run_loop(plugin_loader, api_id: int, api_hash: str):
    """Main daemon thread — runs asyncio event loop."""
    asyncio.set_event_loop(_loop)

    async def _main():
        # Load all session files and connect
        await _connect_accounts(plugin_loader, api_id, api_hash)

        # Keep running until stop
        while not _stop_event.is_set():
            await asyncio.sleep(1)

    try:
        _loop.run_until_complete(_main())
    except Exception as e:
        if not _stop_event.is_set():
            logger.error(f"[TELEGRAM] Daemon loop crashed: {e}", exc_info=True)
    finally:
        try:
            _loop.run_until_complete(_loop.shutdown_asyncgens())
        except Exception:
            pass


async def _connect_accounts(plugin_loader, api_id: int, api_hash: str):
    """Find all session files and connect each account."""
    from telethon import TelegramClient, events

    if not SESSION_DIR.exists():
        return

    # Each .session file = one account
    for session_file in SESSION_DIR.glob("*.session"):
        account_name = session_file.stem
        if account_name.startswith("_"):
            continue  # skip temp auth sessions

        session_path = str(SESSION_DIR / account_name)
        try:
            client = TelegramClient(session_path, api_id, api_hash)
            await client.connect()

            if not await client.is_user_authorized():
                logger.warning(f"[TELEGRAM] Account '{account_name}' session expired — skipping")
                await client.disconnect()
                continue

            me = await client.get_me()
            logger.info(f"[TELEGRAM] Connected: {account_name} (@{me.username or me.first_name})")

            # Register message handler
            @client.on(events.NewMessage(incoming=True))
            async def _on_message(event, _name=account_name):
                await _handle_message(plugin_loader, _name, event)

            _clients[account_name] = client

        except Exception as e:
            logger.error(f"[TELEGRAM] Failed to connect '{account_name}': {e}")


async def _handle_message(plugin_loader, account_name: str, event):
    """Handle incoming Telegram message — emit daemon event."""
    try:
        sender = await event.get_sender()
        chat = await event.get_chat()

        # Determine chat type
        from telethon.tl.types import User, Chat, Channel
        if isinstance(chat, User):
            chat_type = "private"
        elif isinstance(chat, Channel):
            chat_type = "channel" if chat.broadcast else "supergroup"
        else:
            chat_type = "group"

        payload = {
            "account": account_name,
            "chat_id": event.chat_id,
            "message_id": event.id,
            "text": event.raw_text or "",
            "username": getattr(sender, "username", "") or "",
            "first_name": getattr(sender, "first_name", "") or "",
            "sender_id": sender.id if sender else None,
            "chat_type": chat_type,
            "chat_title": getattr(chat, "title", "") or "",
        }

        logger.debug(f"[TELEGRAM] Message from {payload['username'] or payload['first_name']} in {chat_type}")
        plugin_loader.emit_daemon_event("telegram_message", json.dumps(payload))

    except Exception as e:
        logger.error(f"[TELEGRAM] Error handling message: {e}", exc_info=True)


async def send_message(account_name: str, chat_id: int, text: str, parse_mode: str = None):
    """Send a message via a specific account. Used by reply handler and tools."""
    client = _clients.get(account_name)
    if not client:
        raise RuntimeError(f"Account '{account_name}' not connected")

    await client.send_message(chat_id, text, parse_mode=parse_mode)


async def _connect_single(account_name: str):
    """Connect a single account (called after successful auth)."""
    from telethon import TelegramClient, events

    if not _api_id or not _api_hash:
        return

    session_path = str(SESSION_DIR / account_name)
    try:
        client = TelegramClient(session_path, _api_id, _api_hash)
        await client.connect()

        if not await client.is_user_authorized():
            logger.warning(f"[TELEGRAM] Account '{account_name}' not authorized after auth")
            await client.disconnect()
            return

        me = await client.get_me()

        @client.on(events.NewMessage(incoming=True))
        async def _on_message(event, _name=account_name):
            await _handle_message(_plugin_loader, _name, event)

        _clients[account_name] = client
        logger.info(f"[TELEGRAM] Hot-connected: {account_name} (@{me.username or me.first_name})")

    except Exception as e:
        logger.error(f"[TELEGRAM] Failed to hot-connect '{account_name}': {e}")


def _reply_handler(task, event_data: dict, response_text: str):
    """Route LLM response back to the Telegram chat that triggered the daemon."""
    chat_id = event_data.get("chat_id")
    account = event_data.get("account") or task.get("trigger_config", {}).get("account", "")

    if not chat_id or not account:
        logger.warning("[TELEGRAM] Reply handler missing chat_id or account")
        return

    # Strip think tags — greedy to last close tag (handles nested/malformed)
    import re
    clean = re.sub(r'<(?:seed:)?think[^>]*>[\s\S]*</(?:seed:think|seed:cot_budget_reflect|think)>', '', response_text, flags=re.IGNORECASE)
    clean = re.sub(r'<(?:seed:)?think[^>]*>.*$', '', clean, flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r'^[\s\S]*</(?:seed:think|seed:cot_budget_reflect|think)>', '', clean, flags=re.IGNORECASE)
    clean = clean.strip()
    if not clean:
        return

    # Determine parse mode from task config
    reply_format = task.get("trigger_config", {}).get("reply_format", "text")
    parse_mode = None
    if reply_format == "markdown":
        parse_mode = "md"
    elif reply_format == "html":
        parse_mode = "html"

    if not _loop or not _loop.is_running():
        logger.warning("[TELEGRAM] Reply handler: daemon loop not running")
        return

    try:
        future = asyncio.run_coroutine_threadsafe(
            send_message(account, chat_id, clean, parse_mode=parse_mode),
            _loop
        )
        future.result(timeout=15)
        logger.info(f"[TELEGRAM] Reply sent to chat {chat_id} via {account}")
    except Exception as e:
        logger.error(f"[TELEGRAM] Reply failed: {e}")
