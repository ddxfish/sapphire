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
import threading
import time

from plugins.email import accounts, mailbox, message
from plugins.email.mailbox import Mailbox

logger = logging.getLogger(__name__)

MAX_PER_POLL = 20   # after a long outage, only the newest N fire
MAX_AUTO_PER_SENDER = 10    # auto-replies to one sender in 24 hours, per account
MAX_BACKOFF = 3600          # seconds: the longest an unreachable account waits between tries
_fails: dict = {}           # account -> (failures in a row, do not try before)

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


def on_settings_saved(settings):
    """Called by core when the plugin's settings are saved: the poll interval applies at once."""
    global _poll_interval
    _settings.clear()
    _settings.update(settings or {})
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
            for acct in (credentials.list_email_accounts() if active else []):
                if _stop_event.is_set():
                    return
                scope = acct["scope"]
                if scope not in active:
                    continue
                fails, wait_until = _fails.get(scope, (0, 0))
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
                    _fails[scope] = (fails + 1, time.time() + wait)
                    if not fails:
                        logger.warning(f"[EMAIL] Poll failed for '{scope}': {e}. Trying again less often until it works.")
        except Exception as e:
            logger.error(f"[EMAIL] Poll loop error: {e}", exc_info=True)

        for _ in range(_poll_interval):  # small steps so stop is responsive
            if _stop_event.is_set():
                return
            time.sleep(1)


def _mark_get(scope):
    return (mailbox._state().get('daemon') or {}).get(scope)


def _mark_set(scope, validity, last_uid, address=''):
    mailbox._state().update_with_lock(
        'daemon', lambda cur: {**(cur or {}), scope: {'validity': validity, 'last_uid': last_uid,
                                                     'address': str(address or '').lower()}}, {})


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
            _mark_set(scope, mb.validity, uid, own)
        _mark_set(scope, mb.validity, top, own)
        if new:
            logger.info(f"[EMAIL] '{scope}': {len(new)} new message(s)")


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


def _pass_mark():
    try:
        from core.rooms import PASS_MARK
        return PASS_MARK
    except Exception:
        return '<<PASS>>'


def _is_contact(task, address):
    """True when the address belongs to someone in the task's People."""
    scope = (task or {}).get('people_scope') or 'default'
    if str(scope).lower() == 'none':
        return False
    try:
        from core.contacts import get_people
        return address.strip().lower() in {(p.get('email') or '').strip().lower() for p in get_people(scope)}
    except Exception as e:
        logger.warning(f"[EMAIL] People not read for the auto-reply check, so no reply is sent: {e}")
        return False


def _say_not_a_contact(scope):
    """Once, so a person whose task used to answer everyone learns why it stopped."""
    try:
        from core import notify
        notify.notify("An email was not auto-answered", level='info', source='email',
                      body=("The sender is not in People, and automatic replies only go to your contacts. "
                            "To answer anyone, turn on 'Auto-reply to anyone' in Settings > Plugins > Email."),
                      key="email-autoreply-contacts", go={'view': 'settings', 'tab': 'plugins'})
    except Exception as e:
        logger.debug(f"[EMAIL] notice not shown: {e}")


def _may_auto_reply(scope, address):
    """True while this sender has had fewer than MAX_AUTO_PER_SENDER auto-replies
    from this account in the last 24 hours, and counts this one. The backstop
    against a responder that never says it is automatic. Senders are kept as a
    hash, never as an address."""
    who = hashlib.sha256(f"{scope}\n{address.strip().lower()}".encode()).hexdigest()[:20]
    now, day = time.time(), 86400
    allowed = []

    def step(cur):
        table = {k: [t for t in v if now - t < day] for k, v in (cur or {}).items()}
        table = {k: v for k, v in table.items() if v}
        sent = table.get(who, [])
        allowed.append(len(sent) < MAX_AUTO_PER_SENDER)
        if allowed[-1]:
            table[who] = sent + [now]
        return table
    mailbox._state().update_with_lock('auto_replies', step, {})
    return allowed[-1]


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
    if _pass_mark() in clean:
        # Her choice: this mail gets no answer. The same mark she uses to pass a turn in a chat room.
        logger.info(f"[EMAIL] No auto-reply to {handle}: she passed ({_pass_mark()})")
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

        why = message.is_automated(original)
        if why:
            logger.info(f"[EMAIL] No auto-reply to {handle} ({why}): automated mail is never answered automatically")
            return
        if b'\\answered' in flags:
            # Answered already: by her own send_email in this same run, by another task
            # on this account, or by this handler before a restart. Never twice.
            logger.info(f"[EMAIL] No auto-reply to {handle}: it has been answered already")
            return
        to, _ = message.reply_targets(original, creds['address'])
        if not to:
            logger.warning(f"[EMAIL] Reply handler: no reply address on {handle}")
            return
        # ONE address. A mail may name any number of Reply-To addresses, and
        # an automatic answer to all of them makes this account a relay.
        to = to[:1]
        if not _settings.get('auto_reply_anyone') and not _is_contact(task, to[0][1]):
            # Nobody is in the loop here, and anyone can write in. Unless the
            # person opened it up, an automatic answer only goes to people they know.
            logger.info(f"[EMAIL] No auto-reply to {handle}: the sender is not in People "
                        "(Settings > Plugins > Email, 'Auto-reply to anyone', allows it)")
            _say_not_a_contact(account)
            return
        if not _may_auto_reply(account, to[0][1]):
            logger.warning(f"[EMAIL] No auto-reply to {handle}: this sender already got {MAX_AUTO_PER_SENDER} "
                           "automatic replies in the last 24 hours (a loop guard)")
            return
        subject = message.prefixed(message.header(original, 'Subject'), 'Re')
        msg = message.build(creds['address'], to, (), subject, clean, original, 'reply', auto=True)
        note = mailbox.send(account, creds, msg, [a for _, a in to])
        try:
            with Mailbox(account, creds, loose=True) as mb:
                mb.flag([handle], b'\\Answered')
        except Exception as e:
            logger.warning(f"[EMAIL] auto-reply sent, but {handle} could not be marked answered: {e}")
        logger.info(f"[EMAIL] Auto-reply delivered for {handle} via {account}{note}")
    except Exception as e:
        logger.error(f"[EMAIL] Auto-reply failed: {e}", exc_info=True)
