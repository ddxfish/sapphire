# plugins/discord/tools/discord_tools.py — Discord tools for the LLM
#
# Account is read from scope_discord ContextVar (set via sidebar dropdown).
# Channel can be specified by name (e.g. "general-bot-chat") or ID.
# In daemon context, channel defaults to the one that triggered the event.

import asyncio
import logging
from contextvars import ContextVar
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = "🎮"

# Set by executor when processing a daemon event — auto-reply target
_reply_channel_id = ContextVar('discord_reply_channel_id', default=None)
# Set to True when discord_send_message runs — prevents double-post from auto_reply
_message_sent = ContextVar('discord_message_sent', default=False)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "discord_get_servers",
            "description": "List Discord servers (guilds) the bot is in, with their channels.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "discord_read_messages",
            "description": "Read recent messages from a Discord channel.",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel": {
                        "type": "string",
                        "description": "Channel name (e.g. 'general-bot-chat') or channel ID. Omit to use the channel that triggered this message."
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of messages to fetch (default 20, max 50)",
                        "default": 20
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "discord_send_message",
            "description": "Send a message to a Discord channel. If no channel specified, replies to the channel that triggered this conversation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel": {
                        "type": "string",
                        "description": "Channel name (e.g. 'general-bot-chat') or channel ID. Omit to reply to the triggering channel."
                    },
                    "text": {
                        "type": "string",
                        "description": "Message text to send (max 2000 chars)"
                    }
                },
                "required": ["text"]
            }
        }
    }
]

AVAILABLE_FUNCTIONS = {t["function"]["name"] for t in TOOLS}


def _get_account():
    from core.chat.function_manager import scope_discord
    acct = scope_discord.get()
    return acct if acct and acct != 'none' else None


def _check_ready():
    account = _get_account()
    if not account:
        return "Discord is disabled for this chat. Select an account in the sidebar."
    from plugins.discord.daemon import get_client, get_loop
    client = get_client(account)
    loop = get_loop()
    if not client or not loop:
        return f"Discord bot '{account}' is not connected."
    return (client, loop)


def _header():
    from core.tool_context import context_header
    return context_header()


def _time_ago(dt):
    if not dt:
        return ""
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    diff = now - dt
    mins = int(diff.total_seconds() / 60)
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{mins}m ago"
    hours = mins // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


def _resolve_channel(client, loop, channel_ref):
    """Resolve a channel name or ID to a discord channel object.

    Accepts: channel name (e.g. 'general-bot-chat'), channel ID string, or None.
    If None, falls back to _reply_channel_id ContextVar (daemon context).
    """
    # No ref — try daemon reply channel
    if not channel_ref:
        fallback_id = _reply_channel_id.get()
        if not fallback_id:
            return None, "No channel specified and no triggering channel available."
        channel_ref = str(fallback_id)

    channel_ref = channel_ref.strip().lstrip('#')

    # If it's a pure number, treat as ID
    if channel_ref.isdigit():
        async def _by_id():
            ch = client.get_channel(int(channel_ref))
            if not ch:
                ch = await client.fetch_channel(int(channel_ref))
            return ch
        future = asyncio.run_coroutine_threadsafe(_by_id(), loop)
        return future.result(timeout=10), None

    # Otherwise resolve by name (case-insensitive)
    target = channel_ref.lower()
    for guild in client.guilds:
        for ch in guild.text_channels:
            if ch.name.lower() == target:
                return ch, None

    return None, f"Channel '{channel_ref}' not found. Use discord_get_servers to see available channels."


def _get_servers(client, loop):
    """List servers and their text channels."""
    async def _fetch():
        servers = []
        for guild in client.guilds:
            channels = []
            for ch in guild.text_channels:
                channels.append({"name": ch.name, "id": str(ch.id)})
            servers.append({
                "name": guild.name,
                "id": str(guild.id),
                "channels": channels,
                "members": guild.member_count,
            })
        return servers

    future = asyncio.run_coroutine_threadsafe(_fetch(), loop)
    servers = future.result(timeout=10)

    if not servers:
        return _header() + "Not in any servers.", True

    lines = [_header()]
    for s in servers:
        lines.append(f"{s['name']} ({s['members']} members)")
        for ch in s["channels"]:
            lines.append(f"  #{ch['name']}")
        lines.append("")

    return "\n".join(lines), True


def _read_messages(client, loop, channel_ref=None, count=20):
    """Read recent messages from a channel."""
    count = min(max(count, 1), 50)

    channel, err = _resolve_channel(client, loop, channel_ref)
    if err:
        return err, False

    async def _fetch():
        messages = []
        async for msg in channel.history(limit=count):
            messages.append({
                "author": msg.author.display_name,
                "content": msg.content or "(no text)",
                "time": msg.created_at,
                "attachments": len(msg.attachments),
            })
        return list(reversed(messages))  # oldest first

    future = asyncio.run_coroutine_threadsafe(_fetch(), loop)
    messages = future.result(timeout=15)

    if not messages:
        return _header() + f"#{channel.name}: No messages.", True

    lines = [_header() + f"#{channel.name} — {len(messages)} messages:\n"]
    for m in messages:
        ago = _time_ago(m["time"])
        attach = f" [{m['attachments']} file(s)]" if m["attachments"] else ""
        lines.append(f"  {m['author']} ({ago}): {m['content']}{attach}")

    return "\n".join(lines), True


def _send_message(client, loop, channel_ref=None, text=""):
    """Send a message to a channel."""
    if not text or not text.strip():
        return "Message text is required.", False

    if len(text) > 2000:
        return "Message exceeds Discord's 2000 character limit.", False

    channel, err = _resolve_channel(client, loop, channel_ref)
    if err:
        return err, False

    async def _send():
        await channel.send(text)
        return channel.name

    future = asyncio.run_coroutine_threadsafe(_send(), loop)
    channel_name = future.result(timeout=10)
    _message_sent.set(True)

    return f"Message sent to #{channel_name}.", True


def execute(function_name, arguments, config=None):
    """Dispatch tool calls. Returns (result_string, success_bool)."""
    ready = _check_ready()
    if isinstance(ready, str):
        return ready, False
    client, loop = ready

    try:
        if function_name == "discord_get_servers":
            return _get_servers(client, loop)
        elif function_name == "discord_read_messages":
            return _read_messages(
                client, loop,
                arguments.get("channel", arguments.get("channel_id", "")),
                arguments.get("count", 20)
            )
        elif function_name == "discord_send_message":
            return _send_message(
                client, loop,
                arguments.get("channel", arguments.get("channel_id", "")),
                arguments.get("text", "")
            )
        else:
            return f"Unknown function: {function_name}", False
    except Exception as e:
        logger.error(f"[DISCORD] Tool error: {e}", exc_info=True)
        return f"Discord error: {e}", False
