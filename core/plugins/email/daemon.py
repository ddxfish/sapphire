# core/plugins/email/daemon.py - new-mail watcher + auto-reply
#
# Polls the inbox of every account that has an active "New Email" task and
# emits one daemon event per new unread mail.
#
# What counts as new is a high-water mark (the highest uid already handled),
# kept in plugin state, so mail that arrives while Sapphire is down still
# fires when she comes back. Auto-replies go out through the same builder as
# every other mail she sends (threaded, quoted, Reply-To honored) and are
# never sent to bulk mail, lists, bounces or other autoresponders.
#
# Guards that hold whatever a task's prompt says:
#   - a mail is handed over once: the mark moves forward after every single
#     mail, so a stop or a crash in the middle of a batch repeats nothing;
#   - an auto-reply goes to ONE address, never to a mail already answered, and
#     at most MAX_AUTO_PER_SENDER times a day to the same sender (a responder
#     that sends no "I am automatic" header is otherwise a loop that never ends);
#   - an account that cannot be reached is tried less and less often, and a
#     refused password is not sent again at all for a while (accounts.connect).

import hashlib
import imaplib
import json
import logging
import smtplib
import threading
import time

from plugins.email import accounts, mailbox, message, unattended
from plugins.email.mailbox import Mailbox

logger = logging.getLogger(__name__)

MAX_PER_POLL = 20   # after a long outage, only the newest N fire
MAX_AUTO_PER_SENDER = unattended.MAX_PER_SENDER    # the rules themselves live in unattended.py
MAX_BACKOFF = 3600          # seconds: the longest an unreachable account waits between tries
_fails: dict = {}           # account -> (failures in a row, do not try before, what kind of failure)

_thread: threading.Thread = None
_stop_event = threading.Event()
_plugin_loader = None
_poll_interval: int = 120  # seconds, overridden by settings
_settings: dict = {}       # the plugin's settings as last loaded or saved
_lifecycle_lock = threading.Lock()


def start(plugin_loader, settings):
    """Called by plugin_loader on load."""
    global _thread, _plugin_loader, _poll_interval

    with _lifecycle_lock:
        _plugin_loader = plugin_loader
        _settings.clear()
        _settings.update(settings or {})
        _poll_interval = max(30, int(settings.get("poll_interval", 120)))

        if _thread and _thread.is_alive():
            logger.warning("[EMAIL] Daemon already running - skipping double-start")
            return

        _stop_event.clear()
        _thread = threading.Thread(target=_poll_loop, daemon=True, name="email-daemon")
        _thread.start()

        plugin_loader.register_reply_handler("email", _reply_handler)
    logger.info(f"[EMAIL] Daemon started (poll every {_poll_interval}s)")


def retry_now(scope=None):
    """Something was just fixed (a certificate trusted, a test passed): try
    the watched accounts at the next poll, not after their back-off."""
    if scope is None:
        _fails.clear()
    else:
        _fails.pop(scope, None)


def _index_off(settings):
    return (settings or {}).get('keep_mail_index', True) in (False, 0, 'false', 'False', '0', 'off')


def on_settings_saved(settings):
    """Called by core when the plugin's settings are saved: the poll interval
    applies at once, and "remember mail lists" turned off deletes what was
    kept NOW (the Email page may never be opened again to do it)."""
    global _poll_interval
    _settings.clear()
    _settings.update(settings or {})
    if _index_off(settings):
        try:
            from plugins.email import index
            idx = index.get()
            idx.enabled = False
            if not idx.empty():
                idx.forget()
                logger.info("[EMAIL] the mail index was turned off; what it held was deleted")
        except Exception as e:
            logger.warning(f"[EMAIL] mail index not emptied: {e}")
    try:
        _poll_interval = max(30, int((settings or {}).get("poll_interval", _poll_interval)))
    except (TypeError, ValueError):
        pass


def stop():
    """Called by plugin_loader on unload."""
    global _thread

    with _lifecycle_lock:
        _stop_event.set()
        if _thread and _thread.is_alive():
            _thread.join(timeout=5)
        _thread = None
    logger.info("[EMAIL] Daemon stopped")


def _poll_loop():
    from core.credentials_manager import credentials

    for _ in range(10):  # let the system boot
        if _stop_event.is_set():
            return
        time.sleep(1)

    while not _stop_event.is_set():
        try:
            # Only accounts with an active daemon task are polled.
            active = _plugin_loader.active_daemon_accounts("email_message")
            _marks_keep(active)
            listed = credentials.list_email_accounts()
            _tidy({a["scope"] for a in listed})
            for acct in (listed if active else []):
                if _stop_event.is_set():
                    return
                scope = acct["scope"]
                if scope not in active:
                    continue
                fails, wait_until, before = (tuple(_fails.get(scope, (0, 0, ''))) + ('',))[:3]
                if time.time() < wait_until:
                    continue
                try:
                    creds, why = accounts.get_creds(scope)
                    if not creds:
                        raise RuntimeError(why or "no credentials")
                    _check_account(scope, creds)
                    if fails:
                        logger.info(f"[EMAIL] '{scope}' is reachable again after {fails} failed poll(s)")
                    _fails.pop(scope, None)
                except Exception as e:
                    # Tried less and less often, and said once: a server that is down for a
                    # day must not be hammered, nor fill the log with one line per poll.
                    wait = min(MAX_BACKOFF, _poll_interval * 2 ** min(fails, 10))
                    kind = type(e).__name__
                    _fails[scope] = (fails + 1, time.time() + wait, kind)
                    if not fails or kind != before:
                        # Said when a run of failures starts AND when its cause changes: a
                        # network blip at boot must not hide the certificate problem behind it.
                        why = getattr(e, 'words', '') or (e if isinstance(e, RuntimeError) else kind)
                        logger.warning(f"[EMAIL] Poll failed for '{scope}': {str(why).rstrip('.')}. Trying again less often until it works.")
                        if isinstance(e, accounts.CertUntrusted):
                            _say_cert(scope, e)
        except Exception as e:
            logger.error(f"[EMAIL] Poll loop error: {e}", exc_info=True)

        for _ in range(_poll_interval):  # small steps so stop is responsive
            if _stop_event.is_set():
                return
            time.sleep(1)


def _mark_get(scope):
    return (mailbox._state().get('daemon') or {}).get(scope)


def _mark_set(scope, validity, last_uid, address=''):
    """Move the mark. Only forward, while it is the same inbox: a poll thread
    that was still finishing when the plugin reloaded must not pull the mark
    back under the new thread and fire the same mail again."""
    address = str(address or '').lower()

    def step(cur):
        table = dict(cur or {})
        old = table.get(scope) or {}
        uid = last_uid
        if old.get('validity') == validity and (old.get('address') or '') == address:
            uid = max(int(old.get('last_uid') or 0), int(last_uid or 0))
        table[scope] = {'validity': validity, 'last_uid': uid, 'address': address}
        return table
    mailbox._state().update_with_lock('daemon', step, {})


def _tidy(scopes):
    """Housekeeping once a poll: counts and claims that aged out, and the
    mail index of an account that was removed in Settings."""
    try:
        unattended.tidy()
        from plugins.email import index
        if index.DB_PATH.exists():
            index.get().prune(scopes)
    except Exception as e:
        logger.debug(f"[EMAIL] tidy: {e}")


def _marks_keep(active):
    """Forget the mark of an account nobody watches any more. A task that is
    switched off for three weeks and on again must start from now, not hand
    over (and auto-answer) three weeks of old mail."""
    marks = mailbox._state().get('daemon') or {}
    gone = [s for s in marks if s not in active]
    if gone:
        mailbox._state().update_with_lock('daemon', lambda cur: {k: v for k, v in (cur or {}).items() if k not in gone}, {})


def _line_lost(error):
    """True when the connection is gone (a timeout, a reset), as opposed to the server saying no to one mail."""
    return not isinstance(error, (mailbox.MailError, imaplib.IMAP4.error)) or isinstance(error, imaplib.IMAP4.abort)


def _check_account(scope: str, creds: dict):
    """Emit an event for each unread inbox mail newer than the high-water mark."""
    with Mailbox(scope, creds, loose=True) as mb:
        mb.select('inbox')
        validity = mb.validity
        if mb.uidnext is None:                    # a server that does not say: ask for the newest uid
            top = max(mb.c.search(['ALL']) or [0])
        else:
            top = mb.uidnext - 1
        mark = _mark_get(scope)
        # First sight of this inbox (or the server rebuilt it): start from
        # here, never fire for the mail that was already sitting there.
        own = str(creds.get('address') or '').lower()
        moved = bool(mark) and mark.get('address') and mark['address'] != own      # the name now means another mailbox
        if not mark or mark.get('validity') != mb.validity or moved:
            _mark_set(scope, mb.validity, top, own)
            logger.info(f"[EMAIL] '{scope}' watching from uid {top}")
            return
        last = int(mark.get('last_uid') or 0)
        if top <= last:
            return
        # 'UID n:*' always matches the newest mail, even below n: filter.
        new = sorted(u for u in mb.c.search(['UID', f'{last + 1}:*', 'UNSEEN']) if u > last)
        # A mail delivered between the two commands has a uid above `top`: the mark must cover it too.
        top = max([top] + new)
        if len(new) > MAX_PER_POLL:
            logger.warning(f"[EMAIL] '{scope}' has {len(new)} new mails; firing the newest {MAX_PER_POLL}")
            _say_skipped(scope, len(new) - MAX_PER_POLL)
            new = new[-MAX_PER_POLL:]
        for uid in new:
            if _stop_event.is_set():
                return                            # the mark is at the last mail handed over
            try:
                handle = mb.id_for('inbox', uid)
                msg, _ = mb.message(handle)
                if mb.validity != validity:
                    # The server rebuilt the inbox under us: the numbers left in this batch
                    # now mean other mails. Stop; the next poll starts from the new inbox.
                    logger.warning(f"[EMAIL] '{scope}': the inbox was rebuilt on the server mid-poll; starting over next time")
                    return
                frm = (message.addresses(msg, 'From') or [('', '')])[0]
                if frm[1] and frm[1].lower() == own:
                    continue   # our own outbound mail (a copy landed in the inbox)
                to = (message.addresses(msg, 'To') or [('', '')])[0]
                _plugin_loader.emit_daemon_event("email_message", json.dumps({
                    "account": scope,
                    "from_name": frm[0],
                    "from_address": frm[1],
                    "to_address": to[1],
                    "subject": message.header(msg, 'Subject'),
                    "snippet": ' '.join(message.body_text(msg).split())[:200],
                    "uid": handle,                  # the id every email tool takes
                    "bulk": bool(message.is_automated(msg)),
                }))
            except Exception as e:
                if _line_lost(e):
                    # The connection is gone: the rest of the batch would all fail the
                    # same way. Stop here; the next poll takes up from the mark.
                    raise
                logger.warning(f"[EMAIL] Failed to process inbox:{uid}: {e}")
            # After EACH mail, also one that could not be read (retrying it
            # forever would block everything behind it): a stop, a reload or
            # a crash from here on repeats nothing.
            _mark_set(scope, validity, uid, own)
        _mark_set(scope, validity, top, own)
        if new:
            logger.info(f"[EMAIL] '{scope}': {len(new)} new message(s)")


def _say_cert(scope, error):
    """A watched account that stopped over its server's certificate is said to the person, not only the log."""
    try:
        from core import notify
        notify.notify(f"Email is not checking '{scope}'", level='warning', source='email',
                      body=str(error), key=f"email-cert-{scope}")
    except Exception as e:
        logger.debug(f"[EMAIL] certificate notice not shown: {e}")


def _say_skipped(scope, count):
    """Tell the person (not only the log) that a flood was cut short."""
    try:
        from core import notify
        notify.notify(f"{count} new emails were not handed to your AI", level='warning', source='email',
                      body=(f"More than {MAX_PER_POLL} new emails arrived at once on '{scope}'. Only the newest "
                            f"{MAX_PER_POLL} were handed over; the older {count} are in the inbox, unread."),
                      key=f"email-flood-{scope}")
    except Exception as e:
        logger.debug(f"[EMAIL] flood notice not shown: {e}")


def _say_not_a_contact(scope):
    """Once, so a person whose task used to answer everyone learns why it stopped."""
    try:
        from core import notify
        notify.notify("An email was not auto-answered", level='info', source='email',
                      body=("Automatic replies only go to people in People with 'Allow email' ticked, and the "
                            "sender is not one. Tick it on their card, or to answer anyone turn on "
                            "'Auto-reply to anyone' in Settings > Plugins > Email."),
                      key="email-autoreply-contacts", go={'view': 'settings', 'tab': 'email'})
    except Exception as e:
        logger.debug(f"[EMAIL] notice not shown: {e}")


def _reply_handler(task, event_data: dict, response_text: str):
    """Send the LLM's response as a reply to the mail that triggered the task,
    when the task has auto_reply on."""
    if not task.get("trigger_config", {}).get("auto_reply"):
        logger.info(f"[EMAIL] auto_reply OFF for task '{task.get('name', '?')}' - no reply sent. "
                    "Enable in Schedule if unintended.")
        return

    handle = str(event_data.get("uid") or "")
    account = event_data.get("account") or task.get("email_scope", "default")
    if not handle:
        logger.warning("[EMAIL] Reply handler: no email id in event data")
        return

    from core import think   # the one reader of thinking tags
    clean = think.strip(response_text)
    if not clean:
        logger.warning(f"[EMAIL] Empty reply after think-tag strip - raw response was {len(response_text)} chars")
        return
    if unattended.passed(clean):
        # Her choice: this mail gets no answer. The same mark she uses to pass a turn in a chat room.
        logger.info(f"[EMAIL] No auto-reply to {handle}: she passed ({unattended.pass_mark()})")
        return

    try:
        creds, why = accounts.get_creds(account)
        if not creds:
            logger.warning(f"[EMAIL] Reply handler: account '{account}': {why}")
            return
        if ':' not in handle:
            handle = f"inbox:{handle}"   # an event queued before ids were folder:uid
        with Mailbox(account, creds, loose=True) as mb:
            original, flags = mb.message(handle)

        to, _ = message.reply_targets(original, creds['address'])
        if not to:
            logger.warning(f"[EMAIL] Reply handler: no reply address on {handle}")
            return
        # ONE address. A mail may name any number of Reply-To addresses, and
        # an automatic answer to all of them makes this account a relay.
        to = to[:1]
        # The rules for mail nobody is watching (unattended.py); her own
        # send_email in a task run goes through the same ones.
        no = unattended.refusal(account, original, flags, to[0][1], unattended.people_scope_of(task),
                                bool(_settings.get('auto_reply_anyone')))
        if no:
            logger.info(f"[EMAIL] No auto-reply to {handle}: {no[1]}")
            if no[0] == 'stranger':
                _say_not_a_contact(account)
            return
        if not unattended.claim(creds['address'], handle):
            logger.info(f"[EMAIL] No auto-reply to {handle}: another run is answering it, or has")
            return
        if not unattended.under_cap(account, to[0][1]):
            unattended.release(creds['address'], handle)
            logger.warning(f"[EMAIL] No auto-reply to {handle}: {unattended.CAP_WORDS}")
            return
        subject = message.prefixed(message.header(original, 'Subject'), 'Re')
        msg = message.build(creds['address'], to, (), subject, clean, original, 'reply', auto=True)
        try:
            note = mailbox.send(account, creds, msg, [a for _, a in to])
        except Exception as e:
            if not getattr(e, 'unclear', False):
                unattended.release(creds['address'], handle)      # nothing went out: it may be answered later
            raise
        try:
            with Mailbox(account, creds, loose=True) as mb:
                mb.flag([handle], b'\\Answered')
        except Exception as e:
            logger.warning(f"[EMAIL] auto-reply sent, but {handle} could not be marked answered: {e}")
        logger.info(f"[EMAIL] Auto-reply delivered for {handle} via {account}{note}")
    except smtplib.SMTPException as e:
        # The server's own words name addresses: the kind and the code are enough here.
        logger.error(f"[EMAIL] Auto-reply failed: {type(e).__name__} {getattr(e, 'smtp_code', '')}")
    except Exception as e:
        logger.error(f"[EMAIL] Auto-reply failed: {getattr(e, 'words', '') or e}", exc_info=not getattr(e, 'words', ''))
