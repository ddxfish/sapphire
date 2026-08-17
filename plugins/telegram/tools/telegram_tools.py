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

TOOLS = [
    {
        "type": "function",
        "network": True,
        "is_local": False,
        "function": {
            "name": "telegram_send",
            "description": "Send a Telegram message. Uses sidebar-scoped account.",
            "parameters": {
                "type": "object",
                "properties": {
                    "chat_id": {
                        "type": ["string", "integer"],
                        "description": "Chat id (number) or @username"
                    },
                    "text": {
                        "type": "string",
                        "description": "Message text"
                    }
                },
                "required": ["chat_id", "text"]
            }
        }
    },
    {
        "type": "function",
        "network": True,
        "is_local": False,
        "function": {
            "name": "telegram_get_chats",
            "description": "Recent Telegram chats with last-message preview + unread count.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Max chats (default 15)",
                        "default": 15
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "network": True,
        "is_local": False,
        "function": {
            "name": "telegram_read_messages",
            "description": "Read recent messages from a Telegram chat. Get chat_id from telegram_get_chats.",
            "parameters": {
                "type": "object",
                "properties": {
                    "chat_id": {
                        "type": ["string", "integer"],
                        "description": "Chat id (number) or @username"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max messages (default 20)",
                        "default": 20
                    }
                },
                "required": ["chat_id"]
            }
        }
    },
    {
        "type": "function",
        "network": True,
        "is_local": False,
        "function": {
            "name": "telegram_send_image",
            "description": "Send the most recently generated image to a Telegram chat. You see it too and can comment.",
            "parameters": {
                "type": "object",
                "properties": {
                    "chat_id": {
                        "type": ["string", "integer"],
                        "description": "Chat id (from daemon event context)"
                    },
                    "caption": {
                        "type": "string",
                        "description": "Image caption"
                    }
                },
                "required": ["chat_id"]
            }
        }
    },
    {
        "type": "function",
        "network": True,
        "is_local": False,
        "function": {
            "name": "telegram_send_voice",
            "description": "Send a TTS voice note to a Telegram chat (playable voice bubble).",
            "parameters": {
                "type": "object",
                "properties": {
                    "chat_id": {
                        "type": ["string", "integer"],
                        "description": "Chat id (number) or @username"
                    },
                    "text": {
                        "type": "string",
                        "description": "What to say"
                    }
                },
                "required": ["chat_id", "text"]
            }
        }
    },
    {
        "type": "function",
        "network": True,
        "is_local": False,
        "function": {
            "name": "telegram_add_contact",
            "description": "Add a Telegram contact (client mode only). Required to message someone new.",
            "parameters": {
                "type": "object",
                "properties": {
                    "phone": {
                        "type": "string",
                        "description": "Phone with country code (e.g. +15551234567)"
                    },
                    "first_name": {
                        "type": "string",
                        "description": "First name"
                    },
                    "last_name": {
                        "type": "string",
                        "description": "Last name",
                        "default": ""
                    }
                },
                "required": ["phone", "first_name"]
            }
        }
    }
]

AVAILABLE_FUNCTIONS = [t["function"]["name"] for t in TOOLS]


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
            result = telegram_send(arguments, config)
        elif function_name == "telegram_get_chats":
            result = telegram_get_chats(arguments, config)
        elif function_name == "telegram_read_messages":
            result = telegram_read_messages(arguments, config)
        elif function_name == "telegram_send_voice":
            result = telegram_send_voice(arguments, config)
        elif function_name == "telegram_send_image":
            result = telegram_send_image(arguments, config)
        elif function_name == "telegram_add_contact":
            result = telegram_add_contact(arguments, config)
        else:
            return f"Unknown function: {function_name}", False
        # Inner functions return error strings on failure
        if isinstance(result, str) and result.startswith(("Failed", "Missing", "Error", "Not connected", "No ", "Account ", "Telegram daemon", "chat_id")):
            return result, False
        return result, True
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


def telegram_send_image(args, config):
    ready = _check_ready()
    if isinstance(ready, str):
        return ready
    client, loop = ready

    chat_id = args.get("chat_id")
    caption = args.get("caption")

    if not chat_id:
        return "Missing chat_id — check the daemon event context"

    try:
        try:
            chat_id = int(chat_id)
        except (ValueError, TypeError):
            pass

        # Grab most recent tool image
        from pathlib import Path
        import base64
        img_dir = Path(__file__).parent.parent.parent.parent / "user" / "tool_images"
        image_bytes = None
        if img_dir.exists():
            images = sorted(img_dir.glob("*.*"), key=lambda f: f.stat().st_mtime, reverse=True)
            if images:
                image_bytes = images[0].read_bytes()

        if not image_bytes:
            return "No recent images found — generate an image first with comfy_generate or flux_generate"

        # Send to Telegram
        from plugins.telegram.daemon import send_photo
        future = asyncio.run_coroutine_threadsafe(
            send_photo(_get_account(), chat_id, image_bytes, caption=caption),
            loop
        )
        future.result(timeout=30)

        # Return the image so Sapphire can see it and comment
        b64 = base64.b64encode(image_bytes).decode()
        return {
            "text": f"Image sent to {chat_id}" + (f" — {caption}" if caption else ""),
            "images": [{"data": b64, "media_type": "image/jpeg"}]
        }
    except Exception as e:
        logger.error(f"[TELEGRAM] Image send failed: {e}")
        return f"Failed to send image: {e}"


def telegram_send_voice(args, config):
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

        # Generate TTS audio
        from plugins.telegram.daemon import _generate_voice, send_voice_note
        audio_bytes = _generate_voice(text)
        if not audio_bytes:
            return "Failed to generate voice — TTS may not be available"

        future = asyncio.run_coroutine_threadsafe(
            send_voice_note(_get_account(), chat_id, audio_bytes),
            loop
        )
        future.result(timeout=30)
        return f"Voice note sent to {chat_id}"
    except Exception as e:
        logger.error(f"[TELEGRAM] Voice send failed: {e}")
        return f"Failed to send voice note: {e}"


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


def telegram_add_contact(args, config):
    ready = _check_ready()
    if isinstance(ready, str):
        return ready
    client, loop = ready

    # Bots can't add contacts — client accounts only
    account = _get_account()
    if account:
        from core.plugin_loader import plugin_loader
        state = plugin_loader.get_plugin_state("telegram")
        meta = (state.get("accounts", {}) if state else {}).get(account, {})
        if meta.get("type") == "bot":
            return "Cannot add contacts — this is a bot account. Only client accounts can manage contacts."

    phone = args.get("phone", "").strip()
    first_name = args.get("first_name", "").strip()
    last_name = args.get("last_name", "").strip()
    if not phone or not first_name:
        return "Missing required fields: phone, first_name"

    try:
        async def _add():
            from telethon.tl.functions.contacts import ImportContactsRequest
            from telethon.tl.types import InputPhoneContact
            result = await client(ImportContactsRequest(contacts=[
                InputPhoneContact(
                    client_id=0,
                    phone=phone,
                    first_name=first_name,
                    last_name=last_name
                )
            ]))
            if result.imported:
                user = result.users[0] if result.users else None
                if user:
                    label = f"@{user.username}" if user.username else f"{user.first_name}"
                    return f"Contact added: {label} (ID: {user.id}). You can now message them."
                return f"Contact imported for {phone}."
            elif result.retry_contacts:
                return f"Failed — {phone} is not registered on Telegram."
            else:
                return f"Contact already exists or could not be imported for {phone}."

        future = asyncio.run_coroutine_threadsafe(_add(), loop)
        return future.result(timeout=15)

    except Exception as e:
        logger.error(f"[TELEGRAM] Add contact failed: {e}")
        return f"Failed to add contact: {e}"
