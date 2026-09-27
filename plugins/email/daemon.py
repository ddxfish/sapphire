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
_lifecycle_lock = threading.Lock()


def start(plugin_loader, settings):
    """Called by plugin_loader on load."""
    global _thread, _plugin_loader, _poll_interval

    with _lifecycle_lock:
        _plugin_loader = plugin_loader
        _poll_interval = int(settings.get("poll_interval", 120))

        if _poll_interval < 30:
            _poll_interval = 30  # sanity floor

        if _thread and _thread.is_alive():
            logger.warning("[EMAIL] Daemon already running — skipping double-start")
            return

        _stop_event.clear()
        _thread = threading.Thread(target=_poll_loop, daemon=True, name="email-daemon")
        _thread.start()

        plugin_loader.register_reply_handler("email", _reply_handler)
    logger.info(f"[EMAIL] Daemon started (poll every {_poll_interval}s)")


def stop():
    """Called by plugin_loader on unload."""
    global _thread

    with _lifecycle_lock:
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
            # Only poll accounts that have active daemon tasks
            active = _plugin_loader.active_daemon_accounts("email_message")
            if not active:
                logger.debug("[EMAIL] No active daemon tasks — skipping poll")
            else:
                accounts = credentials.list_email_accounts()
                for acct in accounts:
                    if _stop_event.is_set():
                        return
                    scope = acct["scope"]
                    if scope not in active:
                        continue
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


def _imap_connect(account_scope, creds):
    """Connect and authenticate to IMAP. Handles both password and OAuth2.

    `account_scope` is the credentials-manager scope name for THIS account
    (e.g. 'work', 'personal'). Required so that OAuth refresh writes the
    rotated tokens to the correct scope's record. Pre-fix, refresh used
    address-matching to look up the scope, which wrote tokens to the
    wrong scope when two scopes legitimately shared an address (shared
    mailbox in work + personal). Day-ruiner scout 2026-05-07 #I.
    """
    imap = imaplib.IMAP4_SSL(creds["imap_server"], int(creds.get("imap_port", 993)))
    if creds.get("auth_type") == "oauth2":
        # Refresh token if needed
        oauth_expires = creds.get("oauth_expires_at", 0)
        if time.time() > oauth_expires - 60:
            creds = _refresh_oauth(account_scope, creds)
            if not creds:
                raise RuntimeError("OAuth token refresh failed")
        auth_string = f"user={creds['address']}\x01auth=Bearer {creds['oauth_access_token']}\x01\x01"
        imap.authenticate("XOAUTH2", lambda x: auth_string.encode())
    else:
        imap.login(creds["address"], creds["app_password"])
    return imap


def _refresh_oauth(account_scope, creds):
    """Refresh OAuth2 token. Returns updated creds or None.

    Writes rotated tokens directly to `account_scope` rather than looking
    up the scope by address-match (which wrote to the wrong scope's record
    when two scopes shared an address). Day-ruiner scout 2026-05-07 #I.

    Acquires the per-scope refresh lock (shared with the tool-call path
    via `email_tool._lock_for`) so daemon poll + tool call can't both
    refresh the same scope concurrently and clobber each other's rotated
    refresh_token. Day-ruiner scout 2026-05-07 #D.
    """
    import requests as http_requests
    from core.credentials_manager import credentials
    from plugins.email.tools.email_tool import _lock_for

    tenant = creds.get("oauth_tenant_id", "common")
    token_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    scope = creds.get("oauth_scope", "https://outlook.office365.com/IMAP.AccessAsUser.All offline_access")

    with _lock_for(account_scope):
        # Re-read creds inside the lock — another thread may have refreshed
        # while we waited. Skip the refresh if our token is now fresh.
        try:
            fresh = credentials.get_email_account(account_scope)
            if fresh and fresh.get("oauth_expires_at", 0) > time.time() + 60:
                creds["oauth_access_token"] = fresh["oauth_access_token"]
                creds["oauth_expires_at"] = fresh["oauth_expires_at"]
                if fresh.get("oauth_refresh_token"):
                    creds["oauth_refresh_token"] = fresh["oauth_refresh_token"]
                logger.debug(f"[EMAIL] Refresh skipped — another thread rotated for scope '{account_scope}'")
                return creds
        except Exception:
            pass

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

            credentials.update_email_oauth_tokens(account_scope, access_token, expires_at, new_refresh)

            creds["oauth_access_token"] = access_token
            creds["oauth_expires_at"] = expires_at
            if new_refresh:
                creds["oauth_refresh_token"] = new_refresh
            return creds
        except Exception as e:
            logger.error(f"[EMAIL] OAuth refresh failed: {e}")
            return None


def _check_account(scope: str, creds: dict):
    """Check one account for new UNSEEN mail. Emit events for each new message."""
    imap = _imap_connect(scope, creds)
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

                # Skip our own outbound mail (SMTP copies, sent-to-inbox)
                if from_addr and from_addr.lower() == creds.get("address", "").lower():
                    logger.debug(f"[EMAIL] Skipping own message from {from_addr}")
                    continue

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
        # Was a completely silent skip before — admin had no way to tell
        # why the email bot "went quiet." Task default is True as of
        # 2026-04-24; an OFF here is a deliberate user choice or legacy task.
        logger.info(
            f"[EMAIL] auto_reply OFF — skipping reply to {event_data.get('from_address', '?')} "
            f"(task '{task.get('name', '?')}'). Enable in Schedule if unintended."
        )
        return

    from_addr = event_data.get("from_address", "")
    from_name = event_data.get("from_name", "")
    subject = event_data.get("subject", "")
    account = event_data.get("account") or task.get("email_scope", "default")

    if not from_addr:
        logger.warning("[EMAIL] Reply handler: no from_address in event data")
        return

    # Thinking out (core.think: the one reader of those tags)
    from core import think
    clean = think.strip(response_text)
    if not clean:
        logger.warning(
            f"[EMAIL] Empty reply after think-tag strip — raw response was "
            f"{len(response_text)} chars. Raw: {response_text[:200]!r}"
        )
        return

    try:
        from core.credentials_manager import credentials
        creds = credentials.get_email_account(account)
        if not creds.get("address"):
            logger.warning(f"[EMAIL] Reply handler: no credentials for account '{account}'")
            return

        # Refresh OAuth if needed — `account` is the scope name passed in
        # by the daemon dispatch path, used directly for the refresh write.
        if creds.get("auth_type") == "oauth2":
            if time.time() > creds.get("oauth_expires_at", 0) - 60:
                creds = _refresh_oauth(account, creds)
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
