# plugins/telegram/tools/telegram_tools.py — Telegram tools for the LLM
#
# Account is read from scope_telegram ContextVar (set via sidebar dropdown).
# Output is human-readable text, not JSON — reduces LLM cognitive overhead.

import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '✈️'
AVAILABLE_FUNCTIONS = ['telegram_send', 'telegram_get_chats', 'telegram_read_messages']

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "telegram_send",
            "description": "Send a message to a Telegram chat. Uses the account selected in sidebar scope.",
            "parameters": {
                "type": "object",
                "properties": {
                    "chat_id": {
                        "type": ["string", "integer"],
                        "description": "Telegram chat ID (number) or @username"
                    },
                    "text": {
                        "type": "string",
                        "description": "Message text to send"
                    }
                },
                "required": ["chat_id", "text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "telegram_get_chats",
            "description": "List recent Telegram chats with last message preview and unread count. One call gives you the full picture.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Max chats to return (default 15)",
                        "default": 15
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "telegram_read_messages",
            "description": "Read recent messages from a specific Telegram chat. Use telegram_get_chats first to find chat IDs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "chat_id": {
                        "type": ["string", "integer"],
                        "description": "Telegram chat ID (number) or @username"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max messages to return (default 20)",
                        "default": 20
                    }
                },
                "required": ["chat_id"]
            }
        }
    }
]


def _get_account():
    from core.chat.function_manager import scope_telegram
    acct = scope_telegram.get()
    return acct if acct and acct != 'none' else None


def _check_ready():
    account = _get_account()
    if not account:
        return "No Telegram account selected. Set one in Chat > Sidebar > Mind > Telegram."
    from plugins.telegram.daemon import get_client, get_loop
    client = get_client(account)
    if not client:
        return f"Account '{account}' is not connected. Check Telegram plugin settings."
    loop = get_loop()
    if not loop:
        return "Telegram daemon is not running."
    return client, loop


def _header():
    from core.tool_context import context_header
    return context_header()


def _time_ago(dt):
    """Human-readable relative time from a datetime."""
    if not dt:
        return ""
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = now - dt
    secs = int(delta.total_seconds())
    if secs < 60:
        return "just now"
    elif secs < 3600:
        m = secs // 60
        return f"{m}m ago"
    elif secs < 86400:
        h = secs // 3600
        return f"{h}h ago"
    else:
        d = secs // 86400
        return f"{d}d ago"


def execute(function_name, arguments, config):
    try:
        if function_name == "telegram_send":
            return telegram_send(arguments, config), True
        elif function_name == "telegram_get_chats":
            return telegram_get_chats(arguments, config), True
        elif function_name == "telegram_read_messages":
            return telegram_read_messages(arguments, config), True
        return f"Unknown function: {function_name}", False
    except Exception as e:
        logger.error(f"[TELEGRAM] Tool error: {e}")
        return f"Error: {e}", False


def telegram_send(args, config):
    ready = _check_ready()
    if isinstance(ready, str):
        return ready
    client, loop = ready

    chat_id = args.get("chat_id")
    text = args.get("text", "")
    if not chat_id or not text:
        return "Missing required fields: chat_id, text"

    try:
        try:
            chat_id = int(chat_id)
        except (ValueError, TypeError):
            pass
        future = asyncio.run_coroutine_threadsafe(
            client.send_message(chat_id, text), loop
        )
        future.result(timeout=15)
        return f"Message sent to {chat_id}"
    except Exception as e:
        logger.error(f"[TELEGRAM] Send failed: {e}")
        return f"Failed to send message: {e}"


def telegram_get_chats(args, config):
    ready = _check_ready()
    if isinstance(ready, str):
        return ready
    client, loop = ready

    limit = args.get("limit", 15)

    try:
        async def _get():
            results = []
            async for d in client.iter_dialogs(limit=limit):
                # Chat type
                if d.is_user:
                    ctype = "private"
                elif d.is_group:
                    ctype = "group"
                else:
                    ctype = "channel"

                # Last message preview
                preview = ""
                msg_time = ""
                if d.message:
                    raw = d.message.text or ""
                    preview = (raw[:80] + "...") if len(raw) > 80 else raw
                    msg_time = _time_ago(d.message.date)

                results.append({
                    "name": d.name,
                    "chat_id": d.id,
                    "type": ctype,
                    "unread": d.unread_count,
                    "preview": preview,
                    "time": msg_time,
                })
            return results

        future = asyncio.run_coroutine_threadsafe(_get(), loop)
        chats = future.result(timeout=15)

        # Format as readable text
        account = _get_account()
        lines = [_header() + f"Telegram Chats ({account})\n"]
        for i, c in enumerate(chats, 1):
            unread = f" — {c['unread']} unread" if c['unread'] else ""
            lines.append(f"{i}. {c['name']} ({c['type']}, id:{c['chat_id']}){unread}")
            if c['preview']:
                time_str = f" · {c['time']}" if c['time'] else ""
                lines.append(f"   \"{c['preview']}\"{time_str}")
        return "\n".join(lines)

    except Exception as e:
        logger.error(f"[TELEGRAM] Get chats failed: {e}")
        return f"Failed to list chats: {e}"


def telegram_read_messages(args, config):
    ready = _check_ready()
    if isinstance(ready, str):
        return ready
    client, loop = ready

    chat_id = args.get("chat_id")
    limit = args.get("limit", 20)
    if not chat_id:
        return "chat_id is required"

    try:
        try:
            chat_id = int(chat_id)
        except (ValueError, TypeError):
            pass

        async def _read():
            messages = []
            async for msg in client.iter_messages(chat_id, limit=limit):
                sender = None
                if msg.sender:
                    sender = getattr(msg.sender, 'username', None) or \
                             getattr(msg.sender, 'first_name', None) or \
                             str(msg.sender_id)
                messages.append({
                    "sender": sender or "unknown",
                    "text": msg.text or "[media/no text]",
                    "date": msg.date,
                    "reply_to": msg.reply_to_msg_id,
                })
            return messages

        future = asyncio.run_coroutine_threadsafe(_read(), loop)
        msgs = future.result(timeout=15)

        # Format as readable conversation (newest first)
        lines = [_header() + f"Messages (showing {len(msgs)})\n"]
        for m in msgs:
            time_str = _time_ago(m['date'])
            reply = " (reply)" if m['reply_to'] else ""
            lines.append(f"[{time_str}] {m['sender']}{reply}: {m['text']}")
        return "\n".join(lines)

    except Exception as e:
        logger.error(f"[TELEGRAM] Read messages failed: {e}")
        return f"Failed to read messages: {e}"
