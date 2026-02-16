# functions/email_tool.py
"""
Email tool — AI can read inbox and send email to whitelisted contacts.
Privacy-first: AI never sees email addresses in get_inbox/get_recipients.
Uses IMAP for reading, SMTP for sending. Gmail app passwords recommended.
"""

import imaplib
import smtplib
import email
import email.utils
import time
import logging
from email.mime.text import MIMEText
from email.header import decode_header
from datetime import datetime

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '📧'

AVAILABLE_FUNCTIONS = [
    'get_inbox',
    'read_email',
    'get_recipients',
    'send_email',
]

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "get_inbox",
            "description": "Fetch the latest emails from the inbox. Returns sender names (not addresses), subjects, and dates. Use read_email(index) to read full content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "description": "Number of recent emails to fetch (default 20, max 50)"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "read_email",
            "description": "Read the full content of an email by its index from the last get_inbox() call.",
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {
                        "type": "integer",
                        "description": "Email index from get_inbox() results (1-based)"
                    }
                },
                "required": ["index"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "get_recipients",
            "description": "List contacts who are whitelisted for email. Returns IDs and names only (no addresses). Use the ID with send_email().",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "send_email",
            "description": "Send an email to a whitelisted contact by their ID. The recipient's email address is resolved server-side — you only need their contact ID from get_recipients().",
            "parameters": {
                "type": "object",
                "properties": {
                    "recipient_id": {
                        "type": "integer",
                        "description": "Contact ID from get_recipients()"
                    },
                    "subject": {
                        "type": "string",
                        "description": "Email subject line"
                    },
                    "body": {
                        "type": "string",
                        "description": "Email body text"
                    }
                },
                "required": ["recipient_id", "subject", "body"]
            }
        }
    }
]

# ─── Inbox Cache ─────────────────────────────────────────────────────────────

_inbox_cache = {
    "messages": [],   # Parsed summaries for AI
    "raw": [],        # Full message objects for read_email
    "timestamp": 0,
}

CACHE_TTL = 60  # seconds


def _decode_header_value(value):
    """Decode RFC 2047 encoded header."""
    if not value:
        return ''
    decoded_parts = decode_header(value)
    result = []
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            result.append(part.decode(charset or 'utf-8', errors='replace'))
        else:
            result.append(part)
    return ''.join(result)


def _extract_sender_name(from_header):
    """Extract display name from 'Name <email>' format. Never expose address."""
    if not from_header:
        return 'Unknown'
    name, addr = email.utils.parseaddr(from_header)
    if name:
        return _decode_header_value(name)
    # No display name — show local part only
    if '@' in addr:
        return addr.split('@')[0]
    return addr or 'Unknown'


def _extract_body(msg):
    """Extract plain text body from email message."""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == 'text/plain' and part.get('Content-Disposition') != 'attachment':
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or 'utf-8'
                    return payload.decode(charset, errors='replace')
        # Fallback: try text/html
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == 'text/html' and part.get('Content-Disposition') != 'attachment':
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or 'utf-8'
                    return f"[HTML content]\n{payload.decode(charset, errors='replace')[:2000]}"
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or 'utf-8'
            return payload.decode(charset, errors='replace')
    return '(no text content)'


def _get_email_creds():
    """Get email credentials from credentials manager."""
    from core.credentials_manager import credentials
    creds = credentials.get_email_credentials()
    if not creds['address'] or not creds['app_password']:
        return None
    return creds


# ─── Tool Implementations ────────────────────────────────────────────────────

def _get_inbox(count=20):
    global _inbox_cache

    count = min(max(1, count), 50)

    # Check cache
    if _inbox_cache["messages"] and (time.time() - _inbox_cache["timestamp"]) < CACHE_TTL:
        cached = _inbox_cache["messages"][:count]
        logger.info(f"Email inbox: returning {len(cached)} cached messages")
        return _format_inbox(cached), True

    creds = _get_email_creds()
    if not creds:
        return "Email not configured. Set up email credentials in Settings → Plugins → Email.", False

    try:
        imap = imaplib.IMAP4_SSL(creds['imap_server'])
        imap.login(creds['address'], creds['app_password'])
        imap.select('INBOX', readonly=True)

        _, data = imap.search(None, 'ALL')
        msg_ids = data[0].split()
        if not msg_ids:
            imap.logout()
            _inbox_cache = {"messages": [], "raw": [], "timestamp": time.time()}
            return "Inbox is empty.", True

        # Fetch latest N
        latest = msg_ids[-count:]
        latest.reverse()  # Newest first

        messages = []
        raw_messages = []

        for i, msg_id in enumerate(latest, 1):
            _, msg_data = imap.fetch(msg_id, '(RFC822)')
            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)
            raw_messages.append(msg)

            date_str = msg.get('Date', '')
            try:
                parsed_date = email.utils.parsedate_to_datetime(date_str)
                date_display = parsed_date.strftime('%b %d, %H:%M')
            except Exception:
                date_display = date_str[:20] if date_str else '?'

            messages.append({
                "index": i,
                "sender_name": _extract_sender_name(msg.get('From', '')),
                "subject": _decode_header_value(msg.get('Subject', '(no subject)')),
                "date": date_display,
            })

        imap.logout()

        _inbox_cache = {
            "messages": messages,
            "raw": raw_messages,
            "timestamp": time.time(),
        }

        logger.info(f"Email inbox: fetched {len(messages)} messages")
        return _format_inbox(messages), True

    except imaplib.IMAP4.error as e:
        logger.error(f"IMAP error: {e}")
        return f"Email login failed — check credentials. Error: {e}", False
    except Exception as e:
        logger.error(f"Email inbox error: {e}", exc_info=True)
        return f"Failed to fetch inbox: {e}", False


def _format_inbox(messages):
    if not messages:
        return "Inbox is empty."
    lines = [f"Inbox ({len(messages)} messages):"]
    for m in messages:
        lines.append(f"  [{m['index']}] {m['date']} — {m['sender_name']}: {m['subject']}")
    lines.append("\nUse read_email(index) to read full content.")
    return '\n'.join(lines)


def _read_email(index):
    if not _inbox_cache["raw"]:
        return "No inbox loaded. Call get_inbox() first.", False

    if index < 1 or index > len(_inbox_cache["raw"]):
        return f"Invalid index {index}. Range: 1-{len(_inbox_cache['raw'])}.", False

    msg = _inbox_cache["raw"][index - 1]
    sender = msg.get('From', 'Unknown')
    subject = _decode_header_value(msg.get('Subject', '(no subject)'))
    date_str = msg.get('Date', '?')
    body = _extract_body(msg)

    # Truncate very long bodies
    if len(body) > 4000:
        body = body[:4000] + '\n\n... (truncated)'

    return f"From: {sender}\nSubject: {subject}\nDate: {date_str}\n\n{body}", True


def _get_recipients():
    from functions.knowledge import get_people

    people_scope = _get_current_people_scope()
    if people_scope is None:
        return "People contacts are disabled for this chat.", False

    people = get_people(people_scope)
    whitelisted = [p for p in people if p.get('email_whitelisted') and p.get('email')]

    if not whitelisted:
        return "No contacts are whitelisted for email. Add contacts in Mind → People and enable 'Allow email'.", False

    lines = ["Available email recipients:"]
    for p in whitelisted:
        lines.append(f"  [{p['id']}] {p['name']}")
    return '\n'.join(lines), True


def _send_email(recipient_id, subject, body):
    from functions.knowledge import get_people

    creds = _get_email_creds()
    if not creds:
        return "Email not configured. Set up email credentials in Settings → Plugins → Email.", False

    # Resolve recipient
    people_scope = _get_current_people_scope()
    if people_scope is None:
        return "People contacts are disabled for this chat.", False

    people = get_people(people_scope)
    person = next((p for p in people if p['id'] == recipient_id), None)

    if not person:
        return f"Contact ID {recipient_id} not found.", False
    if not person.get('email_whitelisted'):
        return f"{person['name']} is not whitelisted for email.", False
    if not person.get('email'):
        return f"{person['name']} has no email address.", False

    to_addr = person['email']
    to_name = person['name']

    try:
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = creds['address']
        msg['To'] = to_addr

        with smtplib.SMTP_SSL(creds['smtp_server'], 465) as smtp:
            smtp.login(creds['address'], creds['app_password'])
            smtp.send_message(msg)

        logger.info(f"Email sent to {to_name} (id:{recipient_id}): {subject}")
        return f"Email sent to {to_name}: \"{subject}\"", True

    except smtplib.SMTPAuthenticationError as e:
        logger.error(f"SMTP auth error: {e}")
        return "Email send failed — authentication error. Check app password.", False
    except Exception as e:
        logger.error(f"Email send error: {e}", exc_info=True)
        return f"Failed to send email: {e}", False


# ─── Scope Access ────────────────────────────────────────────────────────────

def _get_current_people_scope():
    try:
        from core.chat.function_manager import FunctionManager
        return FunctionManager._current_people_scope
    except Exception:
        return 'default'


# ─── Executor ────────────────────────────────────────────────────────────────

def execute(function_name, arguments, config):
    try:
        if function_name == "get_inbox":
            return _get_inbox(count=arguments.get('count', 20))
        elif function_name == "read_email":
            index = arguments.get('index')
            if index is None:
                return "index is required.", False
            return _read_email(index)
        elif function_name == "get_recipients":
            return _get_recipients()
        elif function_name == "send_email":
            recipient_id = arguments.get('recipient_id')
            subject = arguments.get('subject', '')
            body = arguments.get('body', '')
            if not recipient_id:
                return "recipient_id is required.", False
            if not subject:
                return "subject is required.", False
            if not body:
                return "body is required.", False
            return _send_email(recipient_id, subject, body)
        else:
            return f"Unknown email function '{function_name}'.", False
    except Exception as e:
        logger.error(f"Email tool error in {function_name}: {e}", exc_info=True)
        return f"Email error: {e}", False
