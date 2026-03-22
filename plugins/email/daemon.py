# plugins/email/daemon.py — IMAP polling daemon
#
# Polls all configured email accounts for new (UNSEEN) messages.
# Emits daemon events into the trigger system when new mail arrives.
# Privacy-first: event payload has sender name + subject, never addresses.

import imaplib
import json
import logging
import threading
import time
import email
from email.header import decode_header

logger = logging.getLogger(__name__)

_thread: threading.Thread = None
_stop_event = threading.Event()
_plugin_loader = None
_poll_interval: int = 120  # seconds, overridden by settings


def start(plugin_loader, settings):
    """Called by plugin_loader on load."""
    global _thread, _plugin_loader, _poll_interval

    _plugin_loader = plugin_loader
    _poll_interval = int(settings.get("poll_interval", 120))

    if _poll_interval < 30:
        _poll_interval = 30  # sanity floor

    _stop_event.clear()
    _thread = threading.Thread(target=_poll_loop, daemon=True, name="email-daemon")
    _thread.start()

    plugin_loader.register_reply_handler("email", _reply_handler)
    logger.info(f"[EMAIL] Daemon started (poll every {_poll_interval}s)")


def stop():
    """Called by plugin_loader on unload."""
    global _thread
    _stop_event.set()
    if _thread and _thread.is_alive():
        _thread.join(timeout=5)
    _thread = None
    logger.info("[EMAIL] Daemon stopped")


# Track last seen UID per account to only fire on truly new mail
_last_seen: dict = {}  # {scope: set(uid_strings)}


def _poll_loop():
    """Main poll thread — checks all accounts on interval."""
    from core.credentials_manager import credentials

    # Initial delay to let system boot
    for _ in range(10):
        if _stop_event.is_set():
            return
        time.sleep(1)

    while not _stop_event.is_set():
        try:
            accounts = credentials.list_email_accounts()
            for acct in accounts:
                if _stop_event.is_set():
                    return
                scope = acct["scope"]
                try:
                    creds = credentials.get_email_account(scope)
                    if not creds.get("address"):
                        continue
                    _check_account(scope, creds)
                except Exception as e:
                    logger.warning(f"[EMAIL] Poll failed for '{scope}': {e}")
        except Exception as e:
            logger.error(f"[EMAIL] Poll loop error: {e}", exc_info=True)

        # Sleep in small increments so stop is responsive
        for _ in range(_poll_interval):
            if _stop_event.is_set():
                return
            time.sleep(1)


def _decode_header_value(raw):
    """Decode RFC 2047 encoded header into a string."""
    if not raw:
        return ""
    parts = decode_header(raw)
    decoded = []
    for data, charset in parts:
        if isinstance(data, bytes):
            decoded.append(data.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(data)
    return " ".join(decoded)


def _parse_address_header(header: str) -> tuple:
    """Parse an email header into (name, address). Returns both parts."""
    if not header:
        return ("", "")
    decoded = _decode_header_value(header)
    # "John Doe <john@example.com>" -> ("John Doe", "john@example.com")
    if "<" in decoded and ">" in decoded:
        name = decoded.split("<")[0].strip().strip('"').strip("'")
        addr = decoded.split("<")[1].split(">")[0].strip()
        return (name, addr)
    # Bare address "john@example.com"
    if "@" in decoded:
        return ("", decoded.strip())
    return (decoded.strip(), "")


def _get_snippet(msg) -> str:
    """Extract first ~200 chars of email body for event payload."""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                try:
                    charset = part.get_content_charset() or "utf-8"
                    text = part.get_payload(decode=True).decode(charset, errors="replace")
                    return text.strip()[:200]
                except Exception:
                    continue
    else:
        try:
            charset = msg.get_content_charset() or "utf-8"
            text = msg.get_payload(decode=True).decode(charset, errors="replace")
            return text.strip()[:200]
        except Exception:
            pass
    return ""


def _imap_connect(creds):
    """Connect and authenticate to IMAP. Handles both password and OAuth2."""
    imap = imaplib.IMAP4_SSL(creds["imap_server"], int(creds.get("imap_port", 993)))
    if creds.get("auth_type") == "oauth2":
        # Refresh token if needed
        oauth_expires = creds.get("oauth_expires_at", 0)
        if time.time() > oauth_expires - 60:
            creds = _refresh_oauth(creds)
            if not creds:
                raise RuntimeError("OAuth token refresh failed")
        auth_string = f"user={creds['address']}\x01auth=Bearer {creds['oauth_access_token']}\x01\x01"
        imap.authenticate("XOAUTH2", lambda x: auth_string.encode())
    else:
        imap.login(creds["address"], creds["app_password"])
    return imap


def _refresh_oauth(creds):
    """Refresh OAuth2 token. Returns updated creds or None."""
    import requests as http_requests
    from core.credentials_manager import credentials

    tenant = creds.get("oauth_tenant_id", "common")
    token_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    scope = creds.get("oauth_scope", "https://outlook.office365.com/IMAP.AccessAsUser.All offline_access")

    try:
        resp = http_requests.post(token_url, data={
            "client_id": creds["oauth_client_id"],
            "client_secret": creds.get("oauth_client_secret", ""),
            "refresh_token": creds["oauth_refresh_token"],
            "grant_type": "refresh_token",
            "scope": scope,
        }, timeout=15)
        resp.raise_for_status()
        tokens = resp.json()

        access_token = tokens["access_token"]
        expires_at = time.time() + tokens.get("expires_in", 3600)
        new_refresh = tokens.get("refresh_token", "")

        # Find scope name for this account
        for acct in credentials.list_email_accounts():
            if acct["address"] == creds["address"]:
                credentials.update_email_oauth_tokens(acct["scope"], access_token, expires_at, new_refresh)
                break

        creds["oauth_access_token"] = access_token
        creds["oauth_expires_at"] = expires_at
        return creds
    except Exception as e:
        logger.error(f"[EMAIL] OAuth refresh failed: {e}")
        return None


def _check_account(scope: str, creds: dict):
    """Check one account for new UNSEEN mail. Emit events for each new message."""
    imap = _imap_connect(creds)
    try:
        imap.select("INBOX", readonly=True)
        _, data = imap.uid("search", None, "UNSEEN")
        uids = data[0].split() if data[0] else []

        # First run: snapshot current UIDs, don't fire events
        if scope not in _last_seen:
            _last_seen[scope] = set(uid.decode() for uid in uids)
            logger.info(f"[EMAIL] '{scope}' initial snapshot: {len(uids)} unseen")
            return

        current_uids = set(uid.decode() for uid in uids)
        new_uids = current_uids - _last_seen[scope]
        _last_seen[scope] = current_uids

        if not new_uids:
            return

        logger.info(f"[EMAIL] '{scope}' has {len(new_uids)} new message(s)")

        # Fetch and emit each new message
        for uid in new_uids:
            if _stop_event.is_set():
                return
            try:
                _, msg_data = imap.uid("fetch", uid.encode(), "(RFC822)")
                if not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)

                from_name, from_addr = _parse_address_header(msg.get("From", ""))
                _, to_addr = _parse_address_header(msg.get("To", ""))
                subject = _decode_header_value(msg.get("Subject", ""))
                snippet = _get_snippet(msg)

                payload = {
                    "account": scope,
                    "from_name": from_name,
                    "from_address": from_addr,
                    "to_address": to_addr,
                    "subject": subject,
                    "snippet": snippet,
                    "uid": uid,
                }

                _plugin_loader.emit_daemon_event("email_message", json.dumps(payload))

            except Exception as e:
                logger.warning(f"[EMAIL] Failed to process UID {uid}: {e}")

    finally:
        try:
            imap.logout()
        except Exception:
            pass


def _reply_handler(task, event_data: dict, response_text: str):
    """Route LLM response back as an email reply if auto_reply is enabled."""
    import re
    import smtplib
    from email.mime.text import MIMEText
    import email.utils

    trigger_config = task.get("trigger_config", {})
    if not trigger_config.get("auto_reply"):
        return

    from_addr = event_data.get("from_address", "")
    from_name = event_data.get("from_name", "")
    subject = event_data.get("subject", "")
    account = event_data.get("account") or task.get("email_scope", "default")

    if not from_addr:
        logger.warning("[EMAIL] Reply handler: no from_address in event data")
        return

    # Strip think tags
    clean = re.sub(r'<(?:seed:)?think[^>]*>[\s\S]*</(?:seed:think|seed:cot_budget_reflect|think)>', '', response_text, flags=re.IGNORECASE)
    clean = re.sub(r'<(?:seed:)?think[^>]*>.*$', '', clean, flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r'^[\s\S]*</(?:seed:think|seed:cot_budget_reflect|think)>', '', clean, flags=re.IGNORECASE)
    clean = clean.strip()
    if not clean:
        return

    try:
        from core.credentials_manager import credentials
        creds = credentials.get_email_account(account)
        if not creds.get("address"):
            logger.warning(f"[EMAIL] Reply handler: no credentials for account '{account}'")
            return

        # Refresh OAuth if needed
        if creds.get("auth_type") == "oauth2":
            if time.time() > creds.get("oauth_expires_at", 0) - 60:
                creds = _refresh_oauth(creds)
                if not creds:
                    logger.error("[EMAIL] Reply handler: OAuth refresh failed")
                    return

        # Build reply — match format used by email tool's _send_email
        reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        msg = MIMEText(clean)
        msg["From"] = creds["address"]
        msg["To"] = from_addr
        msg["Subject"] = reply_subject
        msg["Date"] = email.utils.formatdate(localtime=True)
        msg["Message-ID"] = email.utils.make_msgid(domain=creds.get("smtp_server", "localhost"))

        logger.info(f"[EMAIL] Auto-reply: from={creds['address']} to={from_addr} subject={reply_subject}")

        # Connect and send
        from plugins.email.tools.email_tool import _smtp_connect
        smtp = _smtp_connect(creds)
        with smtp:
            smtp.send_message(msg)

        logger.info(f"[EMAIL] Auto-reply delivered to {from_name or from_addr} via {account}")

    except Exception as e:
        logger.error(f"[EMAIL] Auto-reply failed: {e}", exc_info=True)
