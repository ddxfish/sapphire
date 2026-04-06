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
_self_ids: dict = {}  # {account_name: user_id} — to filter own messages
_typing_tasks: dict = {}  # {chat_id: asyncio.Task} — active typing indicators
_stop_event = threading.Event()
_plugin_loader = None
_api_id: int = 0
_api_hash: str = ""
_lifecycle_lock = threading.Lock()


def start(plugin_loader, settings):
    """Called by plugin_loader on load. Starts the daemon thread."""
    global _loop, _thread, _plugin_loader, _api_id, _api_hash

    with _lifecycle_lock:
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

    with _lifecycle_lock:
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
    """Find session files and connect only accounts with active daemon tasks."""
    from telethon import TelegramClient, events

    if not SESSION_DIR.exists():
        return

    # Only connect accounts that have active daemon tasks
    active = plugin_loader.active_daemon_accounts("telegram_message")
    if not active:
        logger.info("[TELEGRAM] No active daemon tasks — not connecting any accounts")
        return

    # Load account metadata for type detection
    state = plugin_loader.get_plugin_state("telegram")
    accounts_meta = state.get("accounts", {}) if state else {}

    # Each .session file = one account (bots may not have one yet)
    known_accounts = set()
    for session_file in SESSION_DIR.glob("*.session"):
        name = session_file.stem
        if not name.startswith("_"):
            known_accounts.add(name)
    # Bot accounts might not have a session file yet — include them from metadata
    for name, meta in accounts_meta.items():
        if meta.get("type") == "bot":
            known_accounts.add(name)

    for account_name in known_accounts:
        if account_name not in active:
            logger.debug(f"[TELEGRAM] Skipping '{account_name}' — no active daemon task")
            continue

        meta = accounts_meta.get(account_name, {})
        acct_type = meta.get("type", "client")
        session_path = str(SESSION_DIR / account_name)

        try:
            client = TelegramClient(session_path, api_id, api_hash)

            if acct_type == "bot":
                bot_token = meta.get("bot_token", "")
                if not bot_token:
                    logger.warning(f"[TELEGRAM] Bot '{account_name}' has no token — skipping")
                    continue
                await client.start(bot_token=bot_token)
            else:
                await client.connect()
                if not await client.is_user_authorized():
                    logger.warning(f"[TELEGRAM] Account '{account_name}' session expired — skipping")
                    await client.disconnect()
                    continue

            me = await client.get_me()
            _self_ids[account_name] = me.id
            label = f"@{me.username}" if me.username else me.first_name
            logger.info(f"[TELEGRAM] Connected {acct_type}: {account_name} ({label})")

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
        # Ignore our own messages (bots see their own replies as incoming)
        self_id = _self_ids.get(account_name)
        if self_id and event.sender_id == self_id:
            return

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

        _start_typing(account_name, event.chat_id)
        await asyncio.sleep(0.1)  # Yield to let typing task send first request
        accepted = plugin_loader.emit_daemon_event("telegram_message", json.dumps(payload))
        if not accepted:
            _stop_typing(event.chat_id)

    except Exception as e:
        logger.error(f"[TELEGRAM] Error handling message: {e}", exc_info=True)


def _start_typing(account_name: str, chat_id: int):
    """Start a typing indicator loop for a chat. Auto-stops after 120s.

    Telegram typing indicators expire after ~5s, so we re-send every 4s.
    Uses raw SetTypingRequest which works for both bots and user accounts.
    """
    async def _typing_loop():
        client = _clients.get(account_name)
        if not client:
            return
        try:
            from telethon.tl.functions.messages import SetTypingRequest
            from telethon.tl.types import SendMessageTypingAction, SendMessageCancelAction
            await client(SetTypingRequest(peer=chat_id, action=SendMessageTypingAction()))
            for _ in range(30):  # 30 * 4s = 120s safety timeout
                await asyncio.sleep(4)
                await client(SetTypingRequest(peer=chat_id, action=SendMessageTypingAction()))
            # Cancel typing on timeout
            try:
                await client(SetTypingRequest(peer=chat_id, action=SendMessageCancelAction()))
            except Exception:
                pass
        except asyncio.CancelledError:
            # Cancel the typing indicator cleanly
            try:
                await client(SetTypingRequest(peer=chat_id, action=SendMessageCancelAction()))
            except Exception:
                pass
        except Exception:
            pass
        finally:
            _typing_tasks.pop(chat_id, None)

    # Cancel any existing typing task for this chat
    old = _typing_tasks.pop(chat_id, None)
    if old and not old.done():
        old.cancel()

    if _loop and _loop.is_running():
        _typing_tasks[chat_id] = _loop.create_task(_typing_loop())


def _stop_typing(chat_id: int):
    """Stop the typing indicator for a chat."""
    task = _typing_tasks.pop(chat_id, None)
    if task and not task.done():
        loop = _loop
        if loop and loop.is_running():
            loop.call_soon_threadsafe(task.cancel)
        else:
            task.cancel()


async def send_message(account_name: str, chat_id: int, text: str, parse_mode: str = None):
    """Send a message via a specific account. Used by reply handler and tools."""
    client = _clients.get(account_name)
    if not client:
        raise RuntimeError(f"Account '{account_name}' not connected")

    await client.send_message(chat_id, text, parse_mode=parse_mode)


async def send_voice_note(account_name: str, chat_id: int, audio_bytes: bytes):
    """Send a voice note via a specific account."""
    import io
    client = _clients.get(account_name)
    if not client:
        raise RuntimeError(f"Account '{account_name}' not connected")

    voice_file = io.BytesIO(audio_bytes)
    voice_file.name = "voice.ogg"
    await client.send_file(chat_id, voice_file, voice_note=True)


async def send_photo(account_name: str, chat_id: int, image_bytes: bytes, caption: str = None):
    """Send a photo via a specific account."""
    import io
    client = _clients.get(account_name)
    if not client:
        raise RuntimeError(f"Account '{account_name}' not connected")

    photo_file = io.BytesIO(image_bytes)
    photo_file.name = "image.jpg"
    await client.send_file(chat_id, photo_file, caption=caption)


async def _connect_single(account_name: str):
    """Connect a single account (called after successful auth)."""
    from telethon import TelegramClient, events

    if not _api_id or not _api_hash:
        return

    # Load account metadata for type detection
    from core.plugin_loader import plugin_loader as pl
    state = pl.get_plugin_state("telegram")
    meta = (state.get("accounts", {}) if state else {}).get(account_name, {})
    acct_type = meta.get("type", "client")

    session_path = str(SESSION_DIR / account_name)
    try:
        client = TelegramClient(session_path, _api_id, _api_hash)

        if acct_type == "bot":
            bot_token = meta.get("bot_token", "")
            if not bot_token:
                logger.warning(f"[TELEGRAM] Bot '{account_name}' has no token")
                return
            await client.start(bot_token=bot_token)
        else:
            await client.connect()
            if not await client.is_user_authorized():
                logger.warning(f"[TELEGRAM] Account '{account_name}' not authorized after auth")
                await client.disconnect()
                return

        me = await client.get_me()
        _self_ids[account_name] = me.id

        @client.on(events.NewMessage(incoming=True))
        async def _on_message(event, _name=account_name):
            await _handle_message(_plugin_loader, _name, event)

        _clients[account_name] = client
        label = f"@{me.username}" if me.username else me.first_name
        logger.info(f"[TELEGRAM] Hot-connected {acct_type}: {account_name} ({label})")

    except Exception as e:
        logger.error(f"[TELEGRAM] Failed to hot-connect '{account_name}': {e}")


def _generate_voice(text: str) -> bytes:
    """Generate TTS audio bytes from text. Returns bytes or None."""
    try:
        from core.api_fastapi import get_system
        system = get_system()
        if not system or not hasattr(system, 'tts') or not system.tts:
            logger.warning("[TELEGRAM] TTS not available for voice generation")
            return None
        # Strip avatar tags and other markup
        import re
        clean = re.sub(r'<<avatar:\s*[a-zA-Z0-9_]+(?:\s+\d+(?:\.\d+)?s)?>>', '', text)
        clean = clean.strip()
        if not clean:
            return None
        audio_data = system.tts.generate_audio_data(clean)
        return audio_data
    except Exception as e:
        logger.error(f"[TELEGRAM] TTS generation failed: {e}")
        return None


def _reply_handler(task, event_data: dict, response_text: str):
    """Route LLM response back to the Telegram chat that triggered the daemon."""
    chat_id = event_data.get("chat_id")
    account = event_data.get("account") or task.get("trigger_config", {}).get("account", "")

    # Stop typing indicator regardless of outcome
    if chat_id:
        try:
            _stop_typing(int(chat_id))
        except (ValueError, TypeError):
            pass

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

    # Determine parse mode and voice from task config
    reply_format = task.get("trigger_config", {}).get("reply_format", "text")
    send_text = reply_format not in ("voice",)
    send_voice = reply_format in ("voice", "text+voice")
    parse_mode = None
    if reply_format in ("markdown",):
        parse_mode = "md"
    elif reply_format in ("html",):
        parse_mode = "html"

    loop = _loop  # Snapshot — stop() may set _loop = None concurrently
    if not loop or not loop.is_running():
        logger.warning("[TELEGRAM] Reply handler: daemon loop not running")
        return

    try:
        # Send text message
        if send_text:
            future = asyncio.run_coroutine_threadsafe(
                send_message(account, chat_id, clean, parse_mode=parse_mode),
                loop
            )
            future.result(timeout=15)

        # Generate and send voice note
        if send_voice:
            try:
                audio_bytes = _generate_voice(clean)
                if audio_bytes:
                    future = asyncio.run_coroutine_threadsafe(
                        send_voice_note(account, chat_id, audio_bytes),
                        loop
                    )
                    future.result(timeout=30)
                    logger.info(f"[TELEGRAM] Voice note sent to chat {chat_id}")
                else:
                    logger.warning("[TELEGRAM] TTS generation returned no audio")
            except Exception as ve:
                logger.error(f"[TELEGRAM] Voice note failed: {ve}")

        logger.info(f"[TELEGRAM] Reply sent to chat {chat_id} via {account} (format={reply_format})")
    except Exception as e:
        logger.error(f"[TELEGRAM] Reply failed: {e}")
