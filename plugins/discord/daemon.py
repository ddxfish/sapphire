# plugins/discord/daemon.py — Discord bot daemon
#
# Manages one asyncio event loop on a daemon thread.
# Each bot token gets a discord.py Client that listens for messages
# and emits daemon events into Sapphire's trigger system.

import asyncio
import json
import logging
import threading

logger = logging.getLogger(__name__)

# Module-level state
_loop: asyncio.AbstractEventLoop = None
_thread: threading.Thread = None
_clients: dict = {}  # {account_name: discord.Client}
_stop_event = threading.Event()
_plugin_loader = None


def start(plugin_loader, settings):
    """Called by plugin_loader on load. Starts the daemon thread."""
    global _loop, _thread, _plugin_loader

    _plugin_loader = plugin_loader
    _stop_event.clear()

    _loop = asyncio.new_event_loop()
    _thread = threading.Thread(target=_run_loop, daemon=True, name="discord-daemon")
    _thread.start()

    plugin_loader.register_reply_handler("discord", _reply_handler)
    logger.info("[DISCORD] Daemon thread started")


def stop():
    """Called by plugin_loader on unload. Stops all clients."""
    global _loop, _thread
    _stop_event.set()

    if _loop and _loop.is_running():
        async def _shutdown():
            for name, client in list(_clients.items()):
                try:
                    await client.close()
                except Exception:
                    pass
            _clients.clear()

        future = asyncio.run_coroutine_threadsafe(_shutdown(), _loop)
        try:
            future.result(timeout=5)
        except Exception:
            pass

        _loop.call_soon_threadsafe(_loop.stop)

    if _thread and _thread.is_alive():
        _thread.join(timeout=5)

    _loop = None
    _thread = None
    logger.info("[DISCORD] Daemon stopped")


def get_client(account_name: str):
    """Get a connected discord.Client by account name."""
    return _clients.get(account_name)


def get_loop():
    """Get the daemon's event loop."""
    return _loop


def list_connected():
    """Return list of connected account names."""
    return list(_clients.keys())


# ── Internal ──

def _run_loop():
    """Main daemon thread — runs asyncio event loop."""
    asyncio.set_event_loop(_loop)

    async def _main():
        await _connect_accounts()
        # Keep loop alive until stop
        while not _stop_event.is_set():
            await asyncio.sleep(1)

    try:
        _loop.run_until_complete(_main())
    except Exception as e:
        if not _stop_event.is_set():
            logger.error(f"[DISCORD] Daemon loop crashed: {e}", exc_info=True)
    finally:
        try:
            _loop.run_until_complete(_loop.shutdown_asyncgens())
        except Exception:
            pass


async def _connect_accounts():
    """Load all bot tokens from plugin state and connect."""
    from core.plugin_loader import plugin_loader
    state = plugin_loader.get_plugin_state("discord")
    accounts = state.get("accounts", {})

    if not accounts:
        logger.info("[DISCORD] No accounts configured — daemon idle")
        return

    for name, meta in accounts.items():
        token = meta.get("token", "")
        if not token:
            continue
        try:
            await _connect_single(name, token)
        except Exception as e:
            logger.error(f"[DISCORD] Failed to connect '{name}': {e}")


async def _connect_single(account_name: str, token: str = None):
    """Connect a single bot account."""
    import discord

    if not token:
        from core.plugin_loader import plugin_loader
        state = plugin_loader.get_plugin_state("discord")
        accounts = state.get("accounts", {})
        meta = accounts.get(account_name, {})
        token = meta.get("token", "")
        if not token:
            return

    intents = discord.Intents.default()
    intents.message_content = True
    intents.guilds = True

    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        logger.info(f"[DISCORD] Connected: {account_name} ({client.user.name}#{client.user.discriminator})")
        try:
            from core.plugin_loader import plugin_loader
            state = plugin_loader.get_plugin_state("discord")
            accounts = state.get("accounts", {})
            if account_name in accounts:
                accounts[account_name]["bot_name"] = client.user.name
                accounts[account_name]["bot_id"] = client.user.id
                state.save("accounts", accounts)
        except Exception:
            pass

    @client.event
    async def on_message(message):
        logger.info(f"[DISCORD] on_message fired: author={message.author} bot={message.author.bot} content={message.content[:50] if message.content else '(empty)'}")
        # Ignore own messages and other bots
        if message.author == client.user or message.author.bot:
            return

        # Check direct @user mention
        mentioned = client.user in message.mentions
        # Also check @role mentions — if bot has any of the mentioned roles
        if not mentioned and message.guild and message.role_mentions:
            bot_member = message.guild.get_member(client.user.id)
            if bot_member:
                mentioned = any(role in bot_member.roles for role in message.role_mentions)

        # Fetch recent history for context (last 10 messages before this one)
        recent_history = []
        try:
            async for msg in message.channel.history(limit=11, before=message):
                who = msg.author.display_name or msg.author.name
                recent_history.append(f"{who}: {msg.clean_content or '(no text)'}")
            recent_history.reverse()  # oldest first
        except Exception:
            pass

        payload = {
            "account": account_name,
            "guild_id": str(message.guild.id) if message.guild else "",
            "guild_name": message.guild.name if message.guild else "DM",
            "channel_id": str(message.channel.id),
            "channel_name": getattr(message.channel, "name", "DM"),
            "message_id": str(message.id),
            "content": message.clean_content or "",
            "username": message.author.name,
            "display_name": message.author.display_name,
            "author_id": str(message.author.id),
            "is_dm": message.guild is None,
            "mentioned": str(mentioned),
            "recent_history": recent_history,
        }

        logger.info(f"[DISCORD] Message from {payload['username']} in #{payload['channel_name']} (mentioned={mentioned})")
        _plugin_loader.emit_daemon_event("discord_message", json.dumps(payload))

    _clients[account_name] = client

    # Start client as a background task on the current loop
    # discord.py's start() handles login + gateway connection
    asyncio.ensure_future(client.start(token))


async def send_message(account_name: str, channel_id: int, text: str):
    """Send a message to a channel via a specific bot account."""
    client = _clients.get(account_name)
    if not client:
        raise RuntimeError(f"Account '{account_name}' not connected")
    if not client.is_ready():
        raise RuntimeError(f"Account '{account_name}' not ready yet")

    channel = client.get_channel(channel_id)
    if not channel:
        channel = await client.fetch_channel(channel_id)

    await channel.send(text)


def _reply_handler(task, event_data: dict, response_text: str):
    """Route LLM response back to the Discord channel that triggered the daemon."""
    import re

    trigger_config = task.get("trigger_config", {})
    if not trigger_config.get("auto_reply"):
        return

    channel_id = event_data.get("channel_id")
    account = event_data.get("account", "")

    if not channel_id or not account:
        logger.warning("[DISCORD] Reply handler missing channel_id or account")
        return

    # Strip think tags
    clean = re.sub(r'<(?:seed:)?think[^>]*>[\s\S]*</(?:seed:think|seed:cot_budget_reflect|think)>', '', response_text, flags=re.IGNORECASE)
    clean = re.sub(r'<(?:seed:)?think[^>]*>.*$', '', clean, flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r'^[\s\S]*</(?:seed:think|seed:cot_budget_reflect|think)>', '', clean, flags=re.IGNORECASE)
    clean = clean.strip()
    if not clean:
        return

    # Discord has a 2000 char limit
    if len(clean) > 2000:
        clean = clean[:1997] + "..."

    if not _loop or not _loop.is_running():
        logger.warning("[DISCORD] Reply handler: daemon loop not running")
        return

    try:
        future = asyncio.run_coroutine_threadsafe(
            send_message(account, int(channel_id), clean),
            _loop
        )
        future.result(timeout=15)
        logger.info(f"[DISCORD] Reply sent to #{event_data.get('channel_name', channel_id)} via {account}")
    except Exception as e:
        logger.error(f"[DISCORD] Reply failed: {e}")
