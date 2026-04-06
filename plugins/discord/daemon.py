# plugins/discord/daemon.py — Discord bot daemon
#
# Manages one asyncio event loop on a daemon thread.
# Each bot token gets a discord.py Client that listens for messages
# and emits daemon events into Sapphire's trigger system.

import asyncio
import json
import logging
import threading
import time

logger = logging.getLogger(__name__)

# Module-level state
_loop: asyncio.AbstractEventLoop = None
_thread: threading.Thread = None
_clients: dict = {}  # {account_name: discord.Client}
_stop_event = threading.Event()
_plugin_loader = None
_last_connect_time: float = 0  # prevent rapid reconnects
_CONNECT_COOLDOWN = 30  # seconds between full reconnect cycles
_lifecycle_lock = threading.Lock()
_typing_tasks: dict = {}  # {channel_id: asyncio.Task} — active typing indicators
_last_reply_time: dict = {}  # {channel_id: timestamp} — for cooldown


def start(plugin_loader, settings):
    """Called by plugin_loader on load. Starts the daemon thread."""
    global _loop, _thread, _plugin_loader

    with _lifecycle_lock:
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

    with _lifecycle_lock:
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
    """Load bot tokens from plugin state and connect only those with active daemon tasks."""
    global _last_connect_time

    # Cooldown — prevent rapid reconnects from hammering Discord's API
    elapsed = time.monotonic() - _last_connect_time
    if _last_connect_time > 0 and elapsed < _CONNECT_COOLDOWN:
        wait = _CONNECT_COOLDOWN - elapsed
        logger.info(f"[DISCORD] Cooldown: waiting {wait:.0f}s before reconnecting")
        await asyncio.sleep(wait)

    from core.plugin_loader import plugin_loader
    state = plugin_loader.get_plugin_state("discord")
    accounts = state.get("accounts", {})

    if not accounts:
        logger.info("[DISCORD] No accounts configured — daemon idle")
        return

    # Only connect bots that have active daemon tasks
    active = plugin_loader.active_daemon_accounts("discord_message")
    if not active:
        logger.info("[DISCORD] No active daemon tasks — not connecting any bots")
        return

    _last_connect_time = time.monotonic()

    for i, (name, meta) in enumerate(accounts.items()):
        if name not in active:
            logger.debug(f"[DISCORD] Skipping '{name}' — no active daemon task")
            continue
        token = meta.get("token", "")
        if not token:
            continue
        # Stagger connections — 5s between each bot to avoid rate limits
        if i > 0:
            logger.info(f"[DISCORD] Staggering connection for '{name}' (5s)")
            await asyncio.sleep(5)
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
        logger.debug(f"[DISCORD] on_message fired: author={message.author} bot={message.author.bot} content={message.content[:50] if message.content else '(empty)'}")
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

        # Pre-check cooldown — don't emit event (or run LLM) if channel is cooling down
        ch_id_str = str(message.channel.id)
        last = _last_reply_time.get(ch_id_str, 0)
        if last:
            # Find the cooldown from any matching task — use the max
            cooldown = _get_channel_cooldown(ch_id_str)
            elapsed = time.time() - last
            if cooldown and elapsed < cooldown:
                logger.info(f"[DISCORD] Cooldown: skipping message in #{payload['channel_name']} ({int(cooldown - elapsed)}s remaining)")
                return

        # Snapshot send count before processing — reply handler compares after LLM runs
        from plugins.discord.tools.discord_tools import get_send_count
        payload["_send_count_before"] = get_send_count(account_name)

        # Only start typing if this message will likely be processed
        # (i.e. if mentioned and tasks require it, don't type on every message)
        if mentioned:
            _start_typing(message.channel)

        _plugin_loader.emit_daemon_event("discord_message", json.dumps(payload))

    _clients[account_name] = client

    # Start client with retry on rate limit
    async def _start_with_retry():
        for attempt in range(3):
            try:
                await client.start(token)
                return
            except Exception as e:
                if '429' in str(e) and attempt < 2:
                    wait = 10 * (attempt + 1)
                    logger.warning(f"[DISCORD] Rate limited on connect for '{account_name}', retrying in {wait}s")
                    await asyncio.sleep(wait)
                else:
                    logger.error(f"[DISCORD] Failed to start '{account_name}': {e}")
                    _clients.pop(account_name, None)
                    return

    asyncio.ensure_future(_start_with_retry())


def _get_channel_cooldown(channel_id_str):
    """Get the effective cooldown for a channel from any matching task's trigger_config."""
    try:
        from core.api_fastapi import get_system
        system = get_system()
        if system and hasattr(system, 'continuity_scheduler') and system.continuity_scheduler:
            for task in system.continuity_scheduler.list_tasks():
                if not task.get("enabled"):
                    continue
                tc = task.get("trigger_config", {})
                source = tc.get("source", "") or tc.get("event_source", "")
                if "discord" in source:
                    cd = tc.get("cooldown", 0)
                    if cd:
                        return float(cd)
    except Exception:
        pass
    return 0


def _start_typing(channel):
    """Start a typing indicator loop for a channel. Auto-stops after 120s."""
    channel_id = channel.id

    async def _typing_loop():
        try:
            async with channel.typing():
                # Auto-timeout after 120s — safety net if reply handler never fires
                for _ in range(24):  # 24 * 5s = 120s
                    await asyncio.sleep(5)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        finally:
            _typing_tasks.pop(channel_id, None)

    # Cancel any existing typing task for this channel
    old = _typing_tasks.pop(channel_id, None)
    if old and not old.done():
        old.cancel()

    if _loop and _loop.is_running():
        _typing_tasks[channel_id] = _loop.create_task(_typing_loop())


def _stop_typing(channel_id):
    """Stop the typing indicator for a channel."""
    task = _typing_tasks.pop(channel_id, None)
    if task and not task.done():
        task.cancel()


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
    """Route LLM response back to the Discord channel that triggered the daemon.

    Always fires as a safety net — if the LLM already used discord_send_message
    (smart models), we skip to prevent double-posting.
    """
    import re
    from plugins.discord.tools.discord_tools import get_send_count

    channel_id = event_data.get("channel_id")
    account = event_data.get("account", "")

    # Stop typing indicator regardless of outcome
    if channel_id:
        try:
            _stop_typing(int(channel_id))
        except (ValueError, TypeError):
            pass

    # Smart model already used the tool — send count increased since we emitted
    count_before = event_data.get("_send_count_before", 0)
    if account and get_send_count(account) > count_before:
        logger.info(f"[DISCORD] Reply handler skipped for '{account}' — tool already sent message")
        return

    if not channel_id or not account:
        logger.warning("[DISCORD] Reply handler missing channel_id or account")
        return

    # Cooldown check — skip if replied to this channel too recently
    cooldown = task.get("trigger_config", {}).get("cooldown", 0)
    if cooldown and channel_id:
        now = time.time()
        last = _last_reply_time.get(channel_id, 0)
        if now - last < cooldown:
            logger.info(f"[DISCORD] Cooldown active for channel {channel_id} ({int(cooldown - (now - last))}s remaining)")
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

    loop = _loop  # Snapshot — stop() may set _loop = None concurrently
    if not loop or not loop.is_running():
        logger.warning("[DISCORD] Reply handler: daemon loop not running")
        return

    try:
        future = asyncio.run_coroutine_threadsafe(
            send_message(account, int(channel_id), clean),
            loop
        )
        future.result(timeout=15)
        _last_reply_time[channel_id] = time.time()
        logger.info(f"[DISCORD] Reply sent to #{event_data.get('channel_name', channel_id)} via {account}")
    except Exception as e:
        logger.error(f"[DISCORD] Reply failed: {e}")
