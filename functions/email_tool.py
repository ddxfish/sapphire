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
import re
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
    'archive_emails',
    'get_recipients',
    'send_email',
]

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "get_inbox",
            "description": "Fetch the latest emails from a mail folder. Returns names, subjects, and dates. Use read_email(index) to read full content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "description": "Number of recent emails to fetch (default 20, max 50)"
                    },
                    "folder": {
                        "type": "string",
                        "enum": ["inbox", "sent", "archive"],
                        "description": "Which mail folder to view (default: inbox)"
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
            "name": "archive_emails",
            "description": "Archive emails by their index numbers from the last get_inbox() call. Moves them to an Archive folder (not deleted — recoverable). Clears inbox cache so next get_inbox() reflects changes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "indices": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "List of email indices to archive (1-based, from get_inbox)"
                    }
                },
                "required": ["indices"]
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
            "description": "Send an email to a whitelisted contact, or reply to an inbox message. For new emails use recipient_id. For replies use reply_to_index (from get_inbox) — the recipient is resolved from the original message automatically.",
            "parameters": {
                "type": "object",
                "properties": {
                    "recipient_id": {
                        "type": "integer",
                        "description": "Contact ID from get_recipients() — required for new emails, omit when replying"
                    },
                    "reply_to_index": {
                        "type": "integer",
                        "description": "Email index from get_inbox() to reply to — sets recipient, subject, and threading headers automatically"
                    },
                    "subject": {
                        "type": "string",
                        "description": "Email subject (auto-set to 'Re: ...' when replying)"
                    },
                    "body": {
                        "type": "string",
                        "description": "Email body text"
                    }
                },
                "required": ["body"]
            }
        }
    }
]

# ─── Inbox Cache (per-scope) ──────────────────────────────────────────────────

_inbox_cache = {}  # scope -> {folder, messages, raw, msg_ids, timestamp}

CACHE_TTL = 60  # seconds

def _empty_cache():
    return {"folder": "inbox", "messages": [], "raw": [], "msg_ids": [], "timestamp": 0}

def _get_cache():
    scope = _get_current_email_scope() or 'default'
    if scope not in _inbox_cache:
        _inbox_cache[scope] = _empty_cache()
    return _inbox_cache[scope]

def _reset_cache():
    scope = _get_current_email_scope() or 'default'
    _inbox_cache[scope] = _empty_cache()

# IMAP folder name candidates (tried in order, first success wins)
_FOLDER_CANDIDATES = {
    "inbox": ["INBOX"],
    "sent": ["[Gmail]/Sent Mail", "Sent", "Sent Items"],
    "archive": ["Archive", "[Gmail]/All Mail"],
}
_resolved_folders = {}  # (scope, folder_key) -> resolved IMAP folder name


def _imap_quote(name):
    """Workaround for Python imaplib bug #90378 — select() breaks on spaces."""
    if ' ' in name and not name.startswith('"'):
        return f'"{name}"'
    return name


def _resolve_folder(imap, folder_key):
    """Resolve logical folder name to IMAP folder. LIST discovery first, then candidates."""
    scope = _get_current_email_scope() or 'default'
    cache_key = (scope, folder_key)

    if cache_key in _resolved_folders:
        name = _resolved_folders[cache_key]
        try:
            imap.select(_imap_quote(name), readonly=True)
            return name
        except imaplib.IMAP4.error:
            del _resolved_folders[cache_key]

    if folder_key == "inbox":
        imap.select("INBOX", readonly=True)
        _resolved_folders[cache_key] = "INBOX"
        return "INBOX"

    # Discover via LIST first (no select = no BAD errors to corrupt state)
    name = _discover_folder(imap, folder_key)
    if name:
        try:
            status, _ = imap.select(_imap_quote(name), readonly=True)
            if status == 'OK':
                _resolved_folders[cache_key] = name
                return name
        except imaplib.IMAP4.error:
            pass

    # Fallback: try hardcoded candidates
    for name in _FOLDER_CANDIDATES.get(folder_key, []):
        try:
            status, _ = imap.select(_imap_quote(name), readonly=True)
            if status == 'OK':
                _resolved_folders[cache_key] = name
                return name
        except imaplib.IMAP4.error:
            continue

    return None


def _discover_folder(imap, folder_key):
    """Find IMAP folder by special-use flag (RFC 6154), then by name pattern."""
    _FLAGS = {"sent": b"\\Sent", "archive": b"\\All"}
    _HINTS = {"sent": [b"sent"], "archive": [b"archive", b"all mail"]}

    flag = _FLAGS.get(folder_key)
    hints = _HINTS.get(folder_key, [])

    try:
        _, folders = imap.list()
        if not folders:
            return None

        # Pass 1: match by special-use flag
        for entry in folders:
            if not isinstance(entry, bytes):
                continue
            if flag and flag in entry:
                match = re.search(rb'"([^"]+)"\s*$', entry)
                if match:
                    return match.group(1).decode()

        # Pass 2: match by folder name pattern
        for entry in folders:
            if not isinstance(entry, bytes):
                continue
            lower = entry.lower()
            for hint in hints:
                if hint in lower:
                    match = re.search(rb'"([^"]+)"\s*$', entry)
                    if match:
                        return match.group(1).decode()
    except Exception:
        pass
    return None


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


def _get_current_email_scope():
    try:
        from core.chat.function_manager import scope_email
        return scope_email.get()
    except Exception:
        return None

def _get_email_creds():
    """Get email credentials for current scope."""
    from core.credentials_manager import credentials
    scope = _get_current_email_scope()
    if scope is None:
        return None
    creds = credentials.get_email_account(scope)
    if not creds['address'] or not creds['app_password']:
        return None
    return creds


# ─── Tool Implementations ────────────────────────────────────────────────────

def _get_inbox(count=20, folder="inbox"):
    count = min(max(1, count), 50)
    folder = folder if folder in _FOLDER_CANDIDATES else "inbox"

    cache = _get_cache()

    # Cache hit — same folder and fresh
    if (cache["folder"] == folder and cache["messages"]
            and (time.time() - cache["timestamp"]) < CACHE_TTL):
        cached = cache["messages"][:count]
        logger.info(f"Email {folder}: returning {len(cached)} cached messages")
        return _format_inbox(cached, folder), True

    creds = _get_email_creds()
    if not creds:
        scope = _get_current_email_scope()
        if scope is None:
            return "Email is disabled for this chat.", False
        return "Email not configured. Set up email credentials in Settings → Plugins → Email.", False

    try:
        imap = imaplib.IMAP4_SSL(creds['imap_server'], int(creds.get('imap_port', 993)))
        imap.login(creds['address'], creds['app_password'])

        # Resolve IMAP folder name (also selects it)
        imap_folder = _resolve_folder(imap, folder)
        if not imap_folder:
            imap.logout()
            return f"Could not find {folder} folder on mail server.", False

        # Use UIDs — stable across sessions (unlike sequence numbers)
        _, data = imap.uid('search', None, 'ALL')
        uids = data[0].split()
        if not uids:
            imap.logout()
            cache.update({"folder": folder, "messages": [], "raw": [], "msg_ids": [], "timestamp": time.time()})
            return f"{folder.title()} is empty.", True

        # Get unseen UIDs (only meaningful for inbox)
        unseen_uids = set()
        if folder == "inbox":
            _, unseen_data = imap.uid('search', None, 'UNSEEN')
            unseen_uids = set(unseen_data[0].split())

        # Fetch latest N
        latest = uids[-count:]
        latest.reverse()  # Newest first

        messages = []
        raw_messages = []

        for i, uid in enumerate(latest, 1):
            _, msg_data = imap.uid('fetch', uid, '(RFC822)')
            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)
            raw_messages.append(msg)

            date_str = msg.get('Date', '')
            try:
                parsed_date = email.utils.parsedate_to_datetime(date_str)
                date_display = parsed_date.strftime('%b %d, %H:%M')
            except Exception:
                date_display = date_str[:20] if date_str else '?'

            # Show recipient for sent, sender for inbox/archive
            if folder == "sent":
                display_name = _extract_sender_name(msg.get('To', ''))
            else:
                display_name = _extract_sender_name(msg.get('From', ''))

            messages.append({
                "index": i,
                "name": display_name,
                "subject": _decode_header_value(msg.get('Subject', '(no subject)')),
                "date": date_display,
                "unread": uid in unseen_uids,
            })

        imap.logout()

        cache.update({
            "folder": folder,
            "messages": messages,
            "raw": raw_messages,
            "msg_ids": latest,
            "timestamp": time.time(),
        })

        logger.info(f"Email {folder}: fetched {len(messages)} messages")
        return _format_inbox(messages, folder), True

    except imaplib.IMAP4.error as e:
        logger.error(f"IMAP error: {e}")
        return f"Email login failed — check credentials. Error: {e}", False
    except Exception as e:
        logger.error(f"Email {folder} error: {e}", exc_info=True)
        return f"Failed to fetch {folder}: {e}", False


def _format_inbox(messages, folder="inbox"):
    if not messages:
        return f"{folder.title()} is empty."
    creds = _get_email_creds()
    account = creds['address'] if creds else 'unknown'
    label = "To" if folder == "sent" else "From"
    total = len(messages)
    if folder == "inbox":
        unread = sum(1 for m in messages if m.get('unread'))
        read = total - unread
        header = f"Account: {account}\n{folder.title()} — {unread} unread, {read} read ({total} total)"
    else:
        header = f"Account: {account}\n{folder.title()} ({total} messages)"
    lines = [header]
    for m in messages:
        tag = " *NEW*" if folder == "inbox" and m.get('unread') else ""
        lines.append(f"  [{m['index']}] {m['date']} — {m['name']}: {m['subject']}{tag}")
    lines.append("\nUse read_email(index) to read full content.")
    return '\n'.join(lines)


def _read_email(index):
    cache = _get_cache()
    if not cache["raw"]:
        return "No inbox loaded. Call get_inbox() first.", False

    if index < 1 or index > len(cache["raw"]):
        return f"Invalid index {index}. Range: 1-{len(cache['raw'])}.", False

    msg = cache["raw"][index - 1]
    sender = msg.get('From', 'Unknown')
    subject = _decode_header_value(msg.get('Subject', '(no subject)'))
    date_str = msg.get('Date', '?')
    body = _extract_body(msg)

    # Truncate very long bodies
    if len(body) > 4000:
        body = body[:4000] + '\n\n... (truncated)'

    # Mark as read in IMAP
    _mark_as_read(index)

    return f"From: {sender}\nSubject: {subject}\nDate: {date_str}\n\n{body}", True


def _mark_as_read(index):
    """Mark a message as read (\\Seen) in IMAP using UID."""
    cache = _get_cache()
    if cache["folder"] != "inbox":
        return  # Only mark read in inbox
    if not cache["msg_ids"] or index < 1 or index > len(cache["msg_ids"]):
        return
    creds = _get_email_creds()
    if not creds:
        return
    try:
        imap = imaplib.IMAP4_SSL(creds['imap_server'], int(creds.get('imap_port', 993)))
        imap.login(creds['address'], creds['app_password'])
        imap.select('INBOX')  # read-write
        imap.uid('store', cache["msg_ids"][index - 1], '+FLAGS', '\\Seen')
        imap.logout()
        # Update cache
        if index <= len(cache["messages"]):
            cache["messages"][index - 1]["unread"] = False
        logger.info(f"Email [{index}] marked as read")
    except Exception as e:
        logger.warning(f"Failed to mark email as read: {e}")


def _archive_emails(indices):
    """Archive emails by moving to Archive folder."""
    cache = _get_cache()

    if cache["folder"] != "inbox":
        return "Can only archive from inbox view. Use get_inbox() first.", False

    if not cache["msg_ids"]:
        return "No inbox loaded. Call get_inbox() first.", False

    max_idx = len(cache["msg_ids"])
    bad = [i for i in indices if i < 1 or i > max_idx]
    if bad:
        return f"Invalid indices: {bad}. Range: 1-{max_idx}.", False

    creds = _get_email_creds()
    if not creds:
        return "Email not configured.", False

    try:
        imap = imaplib.IMAP4_SSL(creds['imap_server'], int(creds.get('imap_port', 993)))
        imap.login(creds['address'], creds['app_password'])

        # Create Archive folder (no-op if exists)
        imap.create('Archive')

        imap.select('INBOX')  # read-write

        archived = []
        for idx in sorted(set(indices)):
            uid = cache["msg_ids"][idx - 1]
            subject = cache["messages"][idx - 1]["subject"] if idx <= len(cache["messages"]) else "?"
            imap.uid('copy', uid, 'Archive')
            imap.uid('store', uid, '+FLAGS', '\\Deleted')
            archived.append(f"[{idx}] {subject}")

        imap.expunge()
        imap.logout()

        # Invalidate cache so next get_inbox() is fresh
        _reset_cache()

        logger.info(f"Archived {len(archived)} emails")
        lines = [f"Archived {len(archived)} emails:"]
        lines.extend(f"  {a}" for a in archived)
        return '\n'.join(lines), True

    except Exception as e:
        logger.error(f"Archive error: {e}", exc_info=True)
        return f"Failed to archive: {e}", False


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


def _send_email(recipient_id=None, subject=None, body='', reply_to_index=None):
    creds = _get_email_creds()
    if not creds:
        scope = _get_current_email_scope()
        if scope is None:
            return "Email is disabled for this chat.", False
        return "Email not configured. Set up email credentials in Settings → Plugins → Email.", False

    cache = _get_cache()
    reply_headers = {}
    to_addr = None
    to_name = None

    # Reply mode — resolve recipient + headers from cached message
    if reply_to_index is not None:
        if not cache["raw"]:
            return "No inbox loaded. Call get_inbox() first.", False
        if reply_to_index < 1 or reply_to_index > len(cache["raw"]):
            return f"Invalid index {reply_to_index}. Range: 1-{len(cache['raw'])}.", False

        original = cache["raw"][reply_to_index - 1]
        # Reply-to address: use Reply-To header if set, otherwise From
        reply_addr = original.get('Reply-To') or original.get('From', '')
        _, to_addr = email.utils.parseaddr(reply_addr)
        to_name = _extract_sender_name(original.get('From', ''))

        if not to_addr:
            return "Could not determine reply address from original message.", False

        # Threading headers
        orig_msg_id = original.get('Message-ID', '')
        orig_refs = original.get('References', '')
        if orig_msg_id:
            reply_headers['In-Reply-To'] = orig_msg_id
            reply_headers['References'] = f"{orig_refs} {orig_msg_id}".strip()

        # Auto-subject
        if not subject:
            orig_subject = _decode_header_value(original.get('Subject', ''))
            subject = orig_subject if orig_subject.lower().startswith('re:') else f"Re: {orig_subject}"

        # Quote original body
        orig_body = _extract_body(original)
        if len(orig_body) > 2000:
            orig_body = orig_body[:2000] + '\n...'
        orig_date = original.get('Date', '')
        body = f"{body}\n\nOn {orig_date}, {to_name} wrote:\n> " + '\n> '.join(orig_body.splitlines())

    # New email mode — resolve from whitelisted contacts
    elif recipient_id is not None:
        from functions.knowledge import get_people

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
        if not subject:
            return "subject is required for new emails.", False
    else:
        return "FAILED: No recipient specified. Use recipient_id (integer from get_recipients) for new emails, or reply_to_index (integer from get_inbox) for replies. Do NOT pass email addresses directly.", False

    try:
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = creds['address']
        msg['To'] = to_addr
        msg['Date'] = email.utils.formatdate(localtime=True)
        msg['Message-ID'] = email.utils.make_msgid(domain=creds['smtp_server'])
        for k, v in reply_headers.items():
            msg[k] = v

        smtp_port = int(creds.get('smtp_port', 465))
        if smtp_port == 465:
            smtp = smtplib.SMTP_SSL(creds['smtp_server'], smtp_port)
        else:
            smtp = smtplib.SMTP(creds['smtp_server'], smtp_port)
            smtp.starttls()
        with smtp:
            smtp.login(creds['address'], creds['app_password'])
            smtp.send_message(msg)

        logger.info(f"Email sent to {to_name}: {subject}")
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
        from core.chat.function_manager import scope_people
        return scope_people.get()
    except Exception:
        return 'default'


# ─── Executor ────────────────────────────────────────────────────────────────

def execute(function_name, arguments, config):
    try:
        if function_name == "get_inbox":
            return _get_inbox(count=arguments.get('count', 20), folder=arguments.get('folder', 'inbox'))
        elif function_name == "read_email":
            index = arguments.get('index')
            if index is None:
                return "index is required.", False
            return _read_email(index)
        elif function_name == "archive_emails":
            indices = arguments.get('indices')
            if not indices:
                return "indices list is required.", False
            return _archive_emails(indices)
        elif function_name == "get_recipients":
            return _get_recipients()
        elif function_name == "send_email":
            body = arguments.get('body', '')
            if not body:
                return "body is required.", False
            return _send_email(
                recipient_id=arguments.get('recipient_id'),
                subject=arguments.get('subject'),
                body=body,
                reply_to_index=arguments.get('reply_to_index'),
            )
        else:
            return f"Unknown email function '{function_name}'.", False
    except Exception as e:
        logger.error(f"Email tool error in {function_name}: {e}", exc_info=True)
        return f"Email error: {e}", False
