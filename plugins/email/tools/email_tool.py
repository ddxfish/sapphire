# Email tool — plugin tool
"""
Email tool — AI can read inbox and send email to whitelisted contacts.
Privacy-first: AI never sees email addresses in get_inbox/get_recipients.
Uses IMAP for reading, SMTP for sending.
Supports password auth and OAuth2 (XOAUTH2) for O365/Exchange.
"""

import base64
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
    'search_emails',
    'archive_emails',
    'delete_emails',
    'forward_email',
    'get_recipients',
    'send_email',
]

def _allow_all_enabled():
    """Check if allow_all_recipients setting is on."""
    try:
        from core.plugin_loader import plugin_loader
        settings = plugin_loader.get_plugin_settings("email")
        return bool(settings.get("allow_all_recipients", False))
    except Exception:
        return False

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "get_inbox",
            "description": "Latest emails from a folder. Returns names, subjects, dates. Use read_email(index) for full content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "description": "How many (default 20, max 50)"
                    },
                    "folder": {
                        "type": "string",
                        "enum": ["inbox", "sent", "archive"],
                        "description": "Default inbox"
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
            "description": "Read full email by index from last get_inbox().",
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {
                        "type": "integer",
                        "description": "Index from get_inbox() (1-based)"
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
            "description": "Archive emails by index (from last get_inbox). Moves to Archive — recoverable.",
            "parameters": {
                "type": "object",
                "properties": {
                    "indices": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Indices to archive (1-based)"
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
            "name": "delete_emails",
            "description": "Delete emails by index (from last get_inbox). Moves to Trash — recoverable from your mail client.",
            "parameters": {
                "type": "object",
                "properties": {
                    "indices": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Indices to delete (1-based)"
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
            "name": "search_emails",
            "description": "Search a folder. Give at least one of sender / content / date. Results load like get_inbox — use read_email(index), reply, archive, delete on them.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sender": {
                        "type": "string",
                        "description": "Match the sender — name or address, partial ok (e.g. 'fish' matches ddxfish@gmail.com)"
                    },
                    "content": {
                        "type": "string",
                        "description": "Text to find in the subject or body"
                    },
                    "date": {
                        "type": "string",
                        "description": "Anchor date YYYY-MM-DD — returns ~10 emails on each side of that day"
                    },
                    "folder": {
                        "type": "string",
                        "enum": ["inbox", "sent", "archive"],
                        "description": "Default inbox"
                    },
                    "count": {
                        "type": "integer",
                        "description": "Max results for non-date searches (default 20, max 50)"
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
            "name": "forward_email",
            "description": "Forward an inbox email (by index from get_inbox) to a contact. Body text only — attachments are not carried.",
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {
                        "type": "integer",
                        "description": "Inbox index from get_inbox() (1-based)"
                    },
                    "recipient_id": {
                        "type": "integer",
                        "description": "Contact id from get_recipients()"
                    },
                    "address": {
                        "type": "string",
                        "description": "Direct email address. Requires allow-all-recipients setting. Not with recipient_id."
                    },
                    "note": {
                        "type": "string",
                        "description": "Optional message added above the forwarded content"
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
            "description": "Whitelisted email contacts (ids + names, no addresses). Use id with send_email.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
]

# Build send_email schema — always include address param, runtime check gates it
_send_props = {
    "recipient_id": {
        "type": "integer",
        "description": "Contact id from get_recipients()"
    },
    "reply_to_index": {
        "type": "integer",
        "description": "Inbox index from get_inbox() — auto-sets recipient / subject / thread headers"
    },
    "subject": {
        "type": "string",
        "description": "Subject (auto-'Re: ...' on reply)"
    },
    "body": {
        "type": "string",
        "description": "Body text"
    },
    "address": {
        "type": "string",
        "description": "Direct email address. Requires allow-all-recipients setting. Not with recipient_id."
    },
    "cc": {
        "type": "array",
        "items": {"type": "integer"},
        "description": "Optional CC — list of contact ids from get_recipients()"
    },
}
_send_desc = "Send an email. One of recipient_id / reply_to_index / address is required.\n  recipient_id=N — to a contact (from get_recipients)\n  reply_to_index=N — reply to inbox entry (from get_inbox)\n  address='x@y' — direct (requires allow-all)\n  cc=[ids] — optional CC to contacts"

TOOLS.append({
    "type": "function",
    "is_local": True,
    "function": {
        "name": "send_email",
        "description": _send_desc,
        "parameters": {
            "type": "object",
            "properties": _send_props,
            "required": ["body"]
        }
    }
})

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
    "trash": ["Trash", "[Gmail]/Trash", "Deleted Items", "Deleted"],
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
    """Find IMAP folder by special-use flag (RFC 6154), then by name pattern.

    Flags are tried in priority order. Archive prefers a real \\Archive folder
    (non-Gmail) and falls back to \\All (Gmail's All Mail) — moving a message
    there + expunging INBOX is Gmail's actual archive behavior."""
    _FLAGS = {"sent": [b"\\Sent"], "archive": [b"\\Archive", b"\\All"], "trash": [b"\\Trash"]}
    _HINTS = {"sent": [b"sent"], "archive": [b"archive", b"all mail"], "trash": [b"trash", b"deleted"]}

    flags = _FLAGS.get(folder_key, [])
    hints = _HINTS.get(folder_key, [])

    try:
        _, folders = imap.list()
        if not folders:
            return None

        # Pass 1: match by special-use flag (priority order)
        for flag in flags:
            for entry in folders:
                if not isinstance(entry, bytes):
                    continue
                if flag in entry:
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

def _get_email_creds_detailed():
    """Like _get_email_creds but returns (creds, error_detail).

    error_detail is None when creds were found, or a human-facing string
    explaining what's wrong. Used by send_email to distinguish "never set
    up" from "OAuth refresh just failed" — because user-facing retry of
    OAuth setup on a transient refresh failure would OVERWRITE a valid
    refresh_token with a fresh consent cycle. Day-ruiner H8 2026-04-22.
    """
    from core.credentials_manager import credentials
    scope = _get_current_email_scope()
    if scope is None:
        return None, "Email is disabled for this chat."
    creds = credentials.get_email_account(scope)
    if not creds.get('address'):
        return None, "Email not configured. Set up credentials in Settings → Plugins → Email."
    if creds.get('auth_type') == 'oauth2':
        if not creds.get('oauth_refresh_token'):
            return None, "OAuth refresh token missing. Re-authorize in Settings → Plugins → Email."
        if creds.get('oauth_expires_at', 0) < time.time() + 300:
            refreshed = _refresh_oauth_token(scope, creds)
            if not refreshed:
                return None, (
                    "OAuth token refresh FAILED. Your existing credentials MAY still be valid — "
                    "do NOT re-run OAuth setup yet (would overwrite the existing refresh token with "
                    "a fresh consent cycle). Check network connectivity or the provider's status and "
                    "try again in a moment. Check server logs for the provider's error response if "
                    "this repeats."
                )
            creds = refreshed
        return creds, None
    # Password auth
    if not creds.get('app_password'):
        return None, "App password not set. Configure it in Settings → Plugins → Email."
    return creds, None


def _get_email_creds():
    """Get email credentials for current scope. Refreshes OAuth tokens if needed."""
    from core.credentials_manager import credentials
    scope = _get_current_email_scope()
    if scope is None:
        return None
    creds = credentials.get_email_account(scope)
    if not creds['address']:
        return None
    if creds.get('auth_type') == 'oauth2':
        if not creds.get('oauth_refresh_token'):
            return None
        # Refresh token if expired or expiring within 5 minutes
        if creds.get('oauth_expires_at', 0) < time.time() + 300:
            creds = _refresh_oauth_token(scope, creds)
            if not creds:
                return None
        return creds
    # Password auth
    if not creds.get('app_password'):
        return None
    return creds


# Per-scope refresh lock — serializes concurrent refresh attempts for the
# same email account. Pre-fix, daemon poll thread could refresh while a
# user-initiated send_email tool call simultaneously refreshed the same
# scope. Both POST to Microsoft for the same scope; Microsoft v2 rotates
# refresh_token under conditional-access accounts; last-writer-wins on
# disk and the other rotated refresh_token is gone forever → next refresh
# = invalid_grant → permadeath of the email OAuth setup. Gcal had this
# pattern (calendar.py:18-31); email didn't. Day-ruiner scout 2026-05-07
# #D. Daemon imports `_lock_for` from this module so the daemon thread
# and the tool-call thread share the same per-scope lock.
import threading as _threading
_refresh_locks_guard = _threading.Lock()
_refresh_locks: dict = {}


def _lock_for(scope: str) -> _threading.Lock:
    """Per-scope refresh serialization. Mirrors gcal's pattern."""
    with _refresh_locks_guard:
        lock = _refresh_locks.get(scope)
        if lock is None:
            lock = _threading.Lock()
            _refresh_locks[scope] = lock
        return lock


def _refresh_oauth_token(scope, creds):
    """Refresh an OAuth2 access token inline. Returns updated creds or None."""
    import requests as http_requests
    from core.credentials_manager import credentials

    tenant = creds.get('oauth_tenant_id', 'common')
    token_url = f'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token'

    with _lock_for(scope):
        # Re-read creds INSIDE the lock so a refresh that completed while
        # we were waiting on the lock isn't redone with stale tokens.
        try:
            fresh = credentials.get_email_account(scope)
            if fresh and fresh.get('oauth_expires_at', 0) > time.time() + 60:
                # Another thread already refreshed; use the rotated tokens.
                creds = fresh
                logger.debug(f"[EMAIL] Refresh skipped — another thread rotated for scope '{scope}'")
                return creds
        except Exception:
            pass  # Fall through to do the refresh ourselves.

        try:
            resp = http_requests.post(token_url, data={
                'client_id': creds['oauth_client_id'],
                'client_secret': creds['oauth_client_secret'],
                'refresh_token': creds['oauth_refresh_token'],
                'grant_type': 'refresh_token',
                'scope': 'https://outlook.office365.com/IMAP.AccessAsUser.All https://outlook.office365.com/SMTP.Send offline_access',
            }, timeout=15)

            if resp.status_code != 200:
                logger.error(f"[EMAIL] OAuth token refresh failed: {resp.status_code} {resp.text[:200]}")
                return None

            tokens = resp.json()
            access_token = tokens['access_token']
            expires_at = time.time() + tokens.get('expires_in', 3600)
            new_refresh = tokens.get('refresh_token', '')

            credentials.update_email_oauth_tokens(scope, access_token, expires_at, new_refresh)

            creds['oauth_access_token'] = access_token
            creds['oauth_expires_at'] = expires_at
            if new_refresh:
                creds['oauth_refresh_token'] = new_refresh
            logger.info(f"[EMAIL] OAuth token refreshed for scope '{scope}'")
            return creds
        except Exception as e:
            logger.error(f"[EMAIL] OAuth token refresh error: {e}")
            return None


def _build_xoauth2(user, access_token):
    """Build XOAUTH2 authentication string."""
    return f"user={user}\x01auth=Bearer {access_token}\x01\x01"


def _imap_connect(creds):
    """Connect and authenticate to IMAP. Handles both password and OAuth2."""
    imap = imaplib.IMAP4_SSL(creds['imap_server'], int(creds.get('imap_port', 993)))
    if creds.get('auth_type') == 'oauth2':
        auth_string = _build_xoauth2(creds['address'], creds['oauth_access_token'])
        imap.authenticate('XOAUTH2', lambda x: auth_string.encode())
    else:
        imap.login(creds['address'], creds['app_password'])
    return imap


def _smtp_connect(creds):
    """Connect and authenticate to SMTP. Handles both password and OAuth2."""
    smtp_port = int(creds.get('smtp_port', 465))
    if smtp_port == 465:
        smtp = smtplib.SMTP_SSL(creds['smtp_server'], smtp_port)
    else:
        smtp = smtplib.SMTP(creds['smtp_server'], smtp_port)
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()

    if creds.get('auth_type') == 'oauth2':
        auth_string = _build_xoauth2(creds['address'], creds['oauth_access_token'])
        encoded = base64.b64encode(auth_string.encode()).decode()
        code, msg = smtp.docmd('AUTH', f'XOAUTH2 {encoded}')
        if code not in (235, 250):
            raise smtplib.SMTPAuthenticationError(code, msg)
    else:
        smtp.login(creds['address'], creds['app_password'])
    return smtp


# ─── Tool Implementations ────────────────────────────────────────────────────

def _fetch_and_cache(imap, uids_newest_first, folder, unseen_uids, cache):
    """Fetch the given UIDs (already ordered newest-first), build the message
    list, write the per-scope cache (so read_email/reply/archive/delete operate
    on them by index) and return the messages. Shared by get_inbox + search."""
    messages = []
    raw_messages = []
    kept_uids = []
    for uid in uids_newest_first:
        typ, msg_data = imap.uid('fetch', uid, '(RFC822)')
        # A UID can vanish between SEARCH and FETCH (expunged by another client,
        # or inside our own cache window). Skip it rather than crash the whole
        # batch — kept_uids keeps messages/raw/msg_ids index-aligned for the
        # downstream read/reply/archive/delete-by-index callers.
        if typ != 'OK' or not msg_data or not isinstance(msg_data[0], tuple):
            continue
        raw_email = msg_data[0][1]
        if not raw_email:
            continue
        msg = email.message_from_bytes(raw_email)
        raw_messages.append(msg)
        kept_uids.append(uid)
        i = len(kept_uids)

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

    cache.update({
        "folder": folder,
        "messages": messages,
        "raw": raw_messages,
        "msg_ids": kept_uids,
        "timestamp": time.time(),
    })
    return messages


def _imap_search_str(s):
    """Quote a SEARCH term. Returns bytes for non-ASCII (paired with CHARSET
    UTF-8), str otherwise — imaplib encodes str args as ASCII and crashes on
    non-ASCII, but passes bytes through verbatim."""
    quoted = '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return quoted.encode('utf-8') if not s.isascii() else quoted


def _uid_search(imap, charset, criteria):
    """Run a UID SEARCH. `criteria` is a list of IMAP keys/terms; empty = ALL.
    Returns a list of UID bytes (ascending), [] on no match / error."""
    args = list(criteria) if criteria else ['ALL']
    if charset:
        typ, data = imap.uid('search', 'CHARSET', charset, *args)
    else:
        typ, data = imap.uid('search', None, *args)
    if typ != 'OK' or not data or not data[0]:
        return []
    return data[0].split()


def _search_date_bracket(imap, criteria, charset, date, per_side):
    """~per_side emails on each side of `date` (YYYY-MM-DD), criteria ANDed in.
    Returns a UID list (ascending) or None if the date can't be parsed. UIDs are
    ascending (older→newer), so the closest mails are the tail of BEFORE and the
    head of SINCE."""
    try:
        d = datetime.strptime(date.strip(), '%Y-%m-%d')
    except Exception:
        return None
    imap_date = d.strftime('%d-%b-%Y')
    older = _uid_search(imap, charset, list(criteria) + ['BEFORE', imap_date])
    newer = _uid_search(imap, charset, list(criteria) + ['SINCE', imap_date])
    return older[-per_side:] + newer[:per_side]


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
        imap = _imap_connect(creds)

        # Resolve IMAP folder name (also selects it)
        imap_folder = _resolve_folder(imap, folder)
        if not imap_folder:
            imap.logout()
            return f"Could not find {folder} folder on mail server.", False

        # Use UIDs — stable across sessions (unlike sequence numbers).
        # _uid_search guards a non-OK / empty response instead of crashing on
        # data[0].split() when the server returns [None] / 'NO'.
        uids = _uid_search(imap, None, [])
        if not uids:
            imap.logout()
            cache.update({"folder": folder, "messages": [], "raw": [], "msg_ids": [], "timestamp": time.time()})
            return f"{folder.title()} is empty.", True

        # Get unseen UIDs (only meaningful for inbox)
        unseen_uids = set()
        if folder == "inbox":
            unseen_uids = set(_uid_search(imap, None, ['UNSEEN']))

        # Fetch latest N (newest first)
        latest = uids[-count:]
        latest.reverse()

        messages = _fetch_and_cache(imap, latest, folder, unseen_uids, cache)
        imap.logout()

        logger.info(f"Email {folder}: fetched {len(messages)} messages")
        return _format_inbox(messages, folder), True

    except imaplib.IMAP4.error as e:
        logger.error(f"IMAP error: {e}")
        try: imap.logout()
        except Exception: pass
        return f"Email login failed — check credentials. Error: {e}", False
    except Exception as e:
        logger.error(f"Email {folder} error: {e}", exc_info=True)
        try: imap.logout()
        except Exception: pass
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


def _search_emails(sender=None, content=None, date=None, folder="inbox", count=20):
    """Search a folder via IMAP SEARCH. Loads results into the same per-scope
    cache as get_inbox, so read_email/reply/archive/delete operate on them by
    index. With `date`, returns a ~count/2-per-side bracket around that day,
    ANDing in any sender/content terms."""
    if not any([sender, content, date]):
        return "Give at least one of: sender, content, date.", False

    folder = folder if folder in _FOLDER_CANDIDATES else "inbox"
    count = min(max(1, count), 50)

    creds = _get_email_creds()
    if not creds:
        scope = _get_current_email_scope()
        if scope is None:
            return "Email is disabled for this chat.", False
        return "Email not configured. Set up email credentials in Settings → Plugins → Email.", False

    # Build sender + content criteria. IMAP ANDs criteria; OR is prefix and binds
    # the next two keys, so FROM + (OR SUBJECT BODY) = FROM AND (SUBJECT OR BODY).
    criteria = []
    needs_utf8 = False
    if sender:
        criteria += ['FROM', _imap_search_str(sender)]
        needs_utf8 = needs_utf8 or not sender.isascii()
    if content:
        term = _imap_search_str(content)
        criteria += ['OR', 'SUBJECT', term, 'BODY', term]
        needs_utf8 = needs_utf8 or not content.isascii()
    charset = 'UTF-8' if needs_utf8 else None

    imap = None
    try:
        imap = _imap_connect(creds)
        imap_folder = _resolve_folder(imap, folder)  # read-only select
        if not imap_folder:
            imap.logout()
            return f"Could not find {folder} folder on mail server.", False

        if date:
            uids = _search_date_bracket(imap, criteria, charset, date, max(1, count // 2))
            if uids is None:
                imap.logout()
                return f"Invalid date {date!r}. Use YYYY-MM-DD.", False
        else:
            uids = _uid_search(imap, charset, criteria)[-count:]
        uids.reverse()  # newest first

        if not uids:
            imap.logout()
            return "No matching emails found.", True

        unseen_uids = set()
        if folder == "inbox":
            unseen_uids = set(_uid_search(imap, None, ['UNSEEN']))

        cache = _get_cache()
        messages = _fetch_and_cache(imap, uids, folder, unseen_uids, cache)
        # Search results are a filtered subset — don't let a later get_inbox()
        # serve them from cache as if they were the whole inbox. read_email /
        # archive / delete don't check TTL, so they still work by index.
        cache["timestamp"] = 0
        imap.logout()

        logger.info(f"Email search ({folder}): {len(messages)} result(s)")
        return _format_inbox(messages, folder), True

    except imaplib.IMAP4.error as e:
        logger.error(f"IMAP search error: {e}")
        try: imap.logout()
        except Exception: pass
        return f"Email search failed — check credentials. Error: {e}", False
    except Exception as e:
        logger.error(f"Email search error: {e}", exc_info=True)
        try: imap.logout()
        except Exception: pass
        return f"Failed to search: {e}", False


def _read_email(index):
    cache = _get_cache()
    if not cache["raw"]:
        return "No inbox loaded. Call get_inbox() first.", False

    if index < 1 or index > len(cache["raw"]):
        return f"Invalid index {index}. Range: 1-{len(cache['raw'])}.", False

    msg = cache["raw"][index - 1]
    sender = _extract_sender_name(msg.get('From', 'Unknown'))
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
        imap = _imap_connect(creds)
        imap.select('INBOX')  # read-write
        imap.uid('store', cache["msg_ids"][index - 1], '+FLAGS', '\\Seen')
        imap.logout()
        # Update cache
        if index <= len(cache["messages"]):
            cache["messages"][index - 1]["unread"] = False
        logger.info(f"Email [{index}] marked as read")
    except Exception as e:
        logger.warning(f"Failed to mark email as read: {e}")
        try: imap.logout()
        except Exception: pass


def _resolve_dest_folder(imap, folder_key, fallback=None, create=False):
    """Resolve a real destination folder for a move (archive/trash).

    Discovery first (special-use flag → name hint, via _discover_folder). When
    nothing is found: create + return `fallback` if create=True (archive's
    legacy 'Archive' behavior), else return None. Delete passes create=False so
    a server with no discoverable Trash REFUSES rather than risk a permanent
    \\Deleted+expunge with no recoverable copy."""
    name = _discover_folder(imap, folder_key)
    if not name and create and fallback:
        try:
            imap.create(_imap_quote(fallback))
        except Exception:
            pass
        name = fallback
    return name


def _move_to_folder(imap, indices, cache, dest_folder):
    """COPY each indexed message to dest_folder, flag \\Deleted, EXPUNGE INBOX.

    Returns (moved, skipped): moved = ['[i] subject', ...],
    skipped = [(i, subject, reason), ...]. Shared by archive + delete.

    Checks IMAP response codes per message — a server COPY of NO is reported as
    skipped, never silently claimed as success (day-ruiner H10: a false 'moved'
    that the user acts on can lose mail). Caller must have INBOX selected r/w."""
    moved, skipped = [], []
    for idx in sorted(set(indices)):
        uid = cache["msg_ids"][idx - 1]
        subject = cache["messages"][idx - 1]["subject"] if idx <= len(cache["messages"]) else "?"
        try:
            copy_status, _ = imap.uid('copy', uid, _imap_quote(dest_folder))
            if copy_status != 'OK':
                skipped.append((idx, subject, f"copy returned {copy_status!r} (message may already be moved externally)"))
                continue
            store_status, _ = imap.uid('store', uid, '+FLAGS', '\\Deleted')
            if store_status != 'OK':
                skipped.append((idx, subject, f"flag-delete returned {store_status!r}"))
                continue
            moved.append(f"[{idx}] {subject}")
        except Exception as e:
            skipped.append((idx, subject, f"exception: {type(e).__name__}: {e}"))
    imap.expunge()
    return moved, skipped


def _format_move_report(moved, skipped, verb, skip_hint):
    """Render a move result. (text, ok) — ok=True if anything moved (partial
    success is still ok, with the skipped class named); full failure = False."""
    lines = []
    if moved:
        lines.append(f"{verb} {len(moved)} email{'s' if len(moved) != 1 else ''}:")
        lines.extend(f"  {m}" for m in moved)
    if skipped:
        if moved:
            lines.append("")
        lines.append(f"Skipped {len(skipped)} email{'s' if len(skipped) != 1 else ''} ({skip_hint}):")
        lines.extend(f"  [{idx}] {subj} — {reason}" for idx, subj, reason in skipped)
    return '\n'.join(lines), bool(moved)


def _move_indices(indices, folder_key, verb, skip_hint, fallback=None, create=False):
    """Shared archive/delete flow: validate inbox cache, connect, resolve the
    real destination folder, move + expunge, report. `create`/`fallback` gate
    whether a missing destination is created (archive) or refused (delete)."""
    cache = _get_cache()
    if cache["folder"] != "inbox":
        return f"Can only {verb.lower().rstrip('d')} from inbox view. Use get_inbox() first.", False
    if not cache["msg_ids"]:
        return "No inbox loaded. Call get_inbox() first.", False
    max_idx = len(cache["msg_ids"])
    bad = [i for i in indices if i < 1 or i > max_idx]
    if bad:
        return f"Invalid indices: {bad}. Range: 1-{max_idx}.", False
    creds = _get_email_creds()
    if not creds:
        return "Email not configured.", False

    imap = None
    try:
        imap = _imap_connect(creds)
        dest = _resolve_dest_folder(imap, folder_key, fallback=fallback, create=create)
        if not dest:
            return (
                f"Could not find a {folder_key.title()} folder on this mail server, "
                f"so nothing was {verb.lower()} (refusing to permanently delete "
                f"without a recoverable copy). Check the account's folders.",
                False,
            )
        imap.select('INBOX')  # read-write
        moved, skipped = _move_to_folder(imap, indices, cache, dest)
        imap.logout()
        _reset_cache()  # next get_inbox() is fresh

        if not moved and not skipped:
            return f"No emails to {verb.lower().rstrip('d')}.", False
        if moved:
            logger.info(f"{verb} {len(moved)} emails to {dest!r}")
        if skipped:
            logger.warning(f"{verb}: {len(skipped)} skipped: {[s[0] for s in skipped]}")
        return _format_move_report(moved, skipped, verb, skip_hint)
    except Exception as e:
        logger.error(f"{verb} error: {e}", exc_info=True)
        try: imap.logout()
        except Exception: pass
        return f"Failed to {verb.lower().rstrip('d')}: {e}", False


def _archive_emails(indices):
    """Archive emails — move to the real Archive folder (Gmail = All Mail),
    falling back to creating a literal 'Archive' folder when none is found."""
    return _move_indices(
        indices, 'archive', 'Archived', 'likely already archived elsewhere',
        fallback='Archive', create=True,
    )


def _delete_emails(indices):
    """Delete emails — move to the server's real Trash (recoverable there).
    Refuses if no Trash folder can be found, rather than permanently expunge."""
    return _move_indices(
        indices, 'trash', 'Deleted', 'may already be gone',
        fallback=None, create=False,
    )


def _get_recipients():
    from core.contacts import get_people

    allow_all = _allow_all_enabled()

    people_scope = _get_current_people_scope()
    if people_scope is None and not allow_all:
        return "People contacts are disabled for this chat.", False

    lines = []

    if people_scope is not None:
        people = get_people(people_scope)
        if allow_all:
            contacts = [p for p in people if p.get('email')]
        else:
            contacts = [p for p in people if p.get('email_whitelisted') and p.get('email')]

        if contacts:
            lines.append("Available contacts:")
            for p in contacts:
                lines.append(f"  [{p['id']}] {p['name']}")

    if allow_all:
        lines.append("\nDirect addresses enabled — you can also use the 'address' parameter with any email address.")

    if not lines:
        return "No contacts are whitelisted for email. Add contacts in Mind → People and enable 'Allow email'.", False

    return '\n'.join(lines), True


def _resolve_recipient(recipient_id=None, address=None):
    """Resolve one recipient to (to_addr, to_name, error_text).

    address mode requires allow-all; recipient_id mode requires the contact be
    whitelisted. Shared by send_email, forward_email and CC resolution so the
    whitelist gate lives in exactly ONE place — no privacy backdoors. Day-ruiner
    H4 lineage: the gate must never be bypassable by a secondary path."""
    if address is not None:
        if not _allow_all_enabled():
            return None, None, "Direct email addresses are not allowed. Use recipient_id from get_recipients() instead."
        addr = address.strip()
        if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', addr):
            return None, None, f"Invalid email address: {addr}"
        return addr, addr, None
    if recipient_id is not None:
        from core.contacts import get_people
        people_scope = _get_current_people_scope()
        if people_scope is None:
            return None, None, "People contacts are disabled for this chat."
        people = get_people(people_scope)
        person = next((p for p in people if p['id'] == recipient_id), None)
        if not person:
            return None, None, f"Contact ID {recipient_id} not found."
        if not person.get('email_whitelisted'):
            return None, None, f"{person['name']} is not whitelisted for email."
        if not person.get('email'):
            return None, None, f"{person['name']} has no email address."
        return person['email'], person['name'], None
    return None, None, "No recipient specified."


def _resolve_cc(cc):
    """Resolve a CC list of contact ids → ([(addr, name), ...], error|None).
    Fails loud on the first bad id so a CC never silently drops or leaks."""
    out = []
    if not cc:
        return out, None
    for cid in cc:
        addr, name, err = _resolve_recipient(recipient_id=cid)
        if err:
            return None, f"CC {cid}: {err}"
        out.append((addr, name))
    return out, None


def _send_email(recipient_id=None, subject=None, body='', reply_to_index=None, address=None, cc=None):
    # Target-mode mutex. Pre-2026-04-22 the precedence was
    # `address > reply_to_index > recipient_id`, which meant if the AI
    # passed both `recipient_id` and `reply_to_index` (confused or trying
    # to combine intents), reply_to_index silently won and the email went
    # to whoever sent that inbox entry — NOT to the contact the caller
    # named. Day-ruiner H4 — wrong-recipient silent failure with
    # potentially sensitive body content quoted. Fix: require exactly one
    # target mode and refuse ambiguous calls loudly. (cc is additive — not
    # a target mode, so it does not count toward the mutex.)
    target_modes = sum([
        bool(address),
        reply_to_index is not None,
        recipient_id is not None,
    ])
    if target_modes > 1:
        return (
            "Multiple recipient modes passed. Use EXACTLY ONE of "
            "address, recipient_id, or reply_to_index.",
            False,
        )
    if target_modes == 0:
        return (
            "No recipient specified. Pass one of recipient_id (from "
            "get_recipients), reply_to_index (from get_inbox), or "
            "address (requires allow-all-recipients).",
            False,
        )

    creds, creds_error = _get_email_creds_detailed()
    if not creds:
        return creds_error or "Email not configured.", False

    cache = _get_cache()
    reply_headers = {}
    to_addr = None
    to_name = None

    # Direct address mode (allow-all only)
    if address is not None:
        to_addr, to_name, err = _resolve_recipient(address=address)
        if err:
            return err, False
        if not subject:
            return "subject is required for new emails.", False

    # Reply mode — resolve recipient + headers from cached message
    elif reply_to_index is not None:
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
        to_addr, to_name, err = _resolve_recipient(recipient_id=recipient_id)
        if err:
            return err, False
        if not subject:
            return "subject is required for new emails.", False

    # Resolve CC (whitelisted contacts only — same gate as the primary recipient)
    cc_pairs, cc_err = _resolve_cc(cc)
    if cc_err:
        return cc_err, False

    try:
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = creds['address']
        msg['To'] = to_addr
        if cc_pairs:
            msg['Cc'] = ', '.join(a for a, _ in cc_pairs)
        msg['Date'] = email.utils.formatdate(localtime=True)
        msg['Message-ID'] = email.utils.make_msgid(domain=creds['smtp_server'])
        for k, v in reply_headers.items():
            msg[k] = v

        smtp = _smtp_connect(creds)
        with smtp:
            smtp.send_message(msg)  # delivers To + Cc from the headers

        cc_note = f" (cc {len(cc_pairs)})" if cc_pairs else ""
        logger.info(f"Email sent to {to_name}{cc_note}: {subject}")
        return f"Email sent to {to_name}{cc_note}: \"{subject}\"", True

    except smtplib.SMTPAuthenticationError as e:
        logger.error(f"SMTP auth error: {e}")
        return "Email send failed — authentication error. Check app password.", False
    except Exception as e:
        logger.error(f"Email send error: {e}", exc_info=True)
        return f"Failed to send email: {e}", False


def _forward_email(index, recipient_id=None, address=None, note=''):
    """Forward an inbox email (by index from get_inbox) to a contact.

    Text-only — attachments are NOT carried (no MIME attachment handling). The
    recipient goes through _resolve_recipient, so it inherits the same whitelist
    gate as send_email. The original sender's NAME is shown in the forwarded
    header, never the address (privacy-first, like the rest of the plugin)."""
    cache = _get_cache()
    if not cache["raw"]:
        return "No inbox loaded. Call get_inbox() first.", False
    if index < 1 or index > len(cache["raw"]):
        return f"Invalid index {index}. Range: 1-{len(cache['raw'])}.", False

    to_addr, to_name, err = _resolve_recipient(recipient_id=recipient_id, address=address)
    if err:
        return err, False

    creds, creds_error = _get_email_creds_detailed()
    if not creds:
        return creds_error or "Email not configured.", False

    original = cache["raw"][index - 1]
    orig_from = _extract_sender_name(original.get('From', ''))
    orig_subject = _decode_header_value(original.get('Subject', '(no subject)'))
    orig_date = original.get('Date', '')
    orig_body = _extract_body(original)
    if len(orig_body) > 4000:
        orig_body = orig_body[:4000] + '\n\n... (truncated)'

    fwd_subject = orig_subject if orig_subject.lower().startswith('fwd:') else f"Fwd: {orig_subject}"
    parts = []
    if note:
        parts.extend([note, ""])
    parts.extend([
        "---------- Forwarded message ----------",
        f"From: {orig_from}",
        f"Date: {orig_date}",
        f"Subject: {orig_subject}",
        "",
        orig_body,
    ])
    fwd_body = '\n'.join(parts)

    try:
        msg = MIMEText(fwd_body)
        msg['Subject'] = fwd_subject
        msg['From'] = creds['address']
        msg['To'] = to_addr
        msg['Date'] = email.utils.formatdate(localtime=True)
        msg['Message-ID'] = email.utils.make_msgid(domain=creds['smtp_server'])

        smtp = _smtp_connect(creds)
        with smtp:
            smtp.send_message(msg)

        logger.info(f"Email forwarded to {to_name}: {fwd_subject}")
        return f"Forwarded to {to_name}: \"{fwd_subject}\"", True

    except smtplib.SMTPAuthenticationError as e:
        logger.error(f"SMTP auth error (forward): {e}")
        return "Forward failed — authentication error. Check app password.", False
    except Exception as e:
        logger.error(f"Email forward error: {e}", exc_info=True)
        return f"Failed to forward: {e}", False


# ─── Scope Access ────────────────────────────────────────────────────────────

def _get_current_people_scope():
    """Resolve the active people scope. Returns None when unset/disabled.

    Previously returned 'default' on exception — silent-default class regression
    (same pattern as the github fix shipped 2026-05-14). Caller (`_get_recipients`)
    must handle None and refuse rather than silently routing to the default
    scope's contacts. 2026-05-14.
    """
    try:
        from core.chat.function_manager import scope_people
        return scope_people.get()
    except Exception as e:
        logger.debug(f"email: people scope resolution failed: {e}")
        return None


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
        elif function_name == "search_emails":
            return _search_emails(
                sender=arguments.get('sender'),
                content=arguments.get('content'),
                date=arguments.get('date'),
                folder=arguments.get('folder', 'inbox'),
                count=arguments.get('count', 20),
            )
        elif function_name == "archive_emails":
            indices = arguments.get('indices')
            if not indices:
                return "indices list is required.", False
            return _archive_emails(indices)
        elif function_name == "delete_emails":
            indices = arguments.get('indices')
            if not indices:
                return "indices list is required.", False
            return _delete_emails(indices)
        elif function_name == "forward_email":
            index = arguments.get('index')
            if index is None:
                return "index is required.", False
            return _forward_email(
                index,
                recipient_id=arguments.get('recipient_id'),
                address=arguments.get('address'),
                note=arguments.get('note', ''),
            )
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
                address=arguments.get('address'),
                cc=arguments.get('cc'),
            )
        else:
            return f"Unknown email function '{function_name}'.", False
    except Exception as e:
        logger.error(f"Email tool error in {function_name}: {e}", exc_info=True)
        return f"Email error: {e}", False
