# core/plugins/email/unattended.py - mail sent with nobody in the loop
"""The rules for a reply that goes out while no person is watching: the
auto-reply the daemon sends after a "New Email" task, and a reply she sends
herself with send_email inside such a run. ONE set of rules for both doors,
because the task's prompt (and anything a stranger wrote in the mail) can
steer her to the second door as easily as the first.

  - never to automated mail (message.is_automated)
  - never to a mail that has been answered already
  - one address, the sender's
  - only to people in People with "Allow email" ticked, unless "Auto-reply
    to anyone" is on (the tick is the person's "she may write to them")
  - at most MAX_PER_SENDER a day to one sender, and MAX_PER_HOUR an hour
    from one account (a help desk that answers from a new address each time
    walks straight past a per-sender count)
  - one answer per mail, claimed before it is sent: two tasks on one
    account, or the handler and her own tool, cannot both send
"""
import hashlib
import logging
import time

from plugins.email import mailbox, message

logger = logging.getLogger(__name__)

MAX_PER_SENDER = 10      # automatic replies to one sender in 24 hours, per account
MAX_PER_HOUR = 30        # automatic replies from one account in an hour, to anyone
CLAIM_DAYS = 30          # how long "this mail was answered automatically" is remembered


def pass_mark():
    try:
        from core.rooms import PASS_MARK
        return PASS_MARK
    except Exception:
        return '<<PASS>>'


def passed(text):
    """True when her answer means "send nothing": the mark anywhere in it,
    in any case, or an answer that is only (pass). The same reading her
    chat rooms give it, so a habit from there does not get mailed out."""
    t = str(text or '').strip()
    if not t:
        return False
    return pass_mark().lower() in t.lower() or t.strip('() .<>').lower() == 'pass'


def people_scope_of(task):
    """The People book a task's run uses, or None when it has none. As the
    task runner decides it: the task's own choice, else its persona's; a
    task that names neither runs with People off, and so does 'none'."""
    task = task or {}
    value = task.get('people_scope')
    if value in (None, '', 'auto') and task.get('persona'):
        try:
            from core.personas import persona_manager
            persona = persona_manager.get(task['persona']) or {}
            value = (persona.get('settings') or {}).get('people_scope') or value
        except Exception as e:
            logger.warning(f"[EMAIL] persona not read for the contact check: {e}")
    if value in (None, '', 'auto') or str(value).strip().lower() == 'none':
        return None
    return value


def is_allowed(people_scope, address):
    """True when the address belongs to someone in that People book who has
    "Allow email" ticked: the same permission her send_email needs for a
    contact. None (People is off for the run) is nobody: never the default
    book instead."""
    if people_scope is None:
        return False
    try:
        from core.contacts import get_people
        return address.strip().lower() in {(p.get('email') or '').strip().lower() for p in get_people(people_scope)
                                           if p.get('email_whitelisted')} - {''}
    except Exception as e:
        logger.warning(f"[EMAIL] People not read for the auto-reply check, so no reply is sent: {e}")
        return False


def _key(*parts):
    return hashlib.sha256('\n'.join(str(p).strip().lower() for p in parts).encode()).hexdigest()[:20]


def under_cap(scope, address):
    """True while both counts have room, and counts this reply. Senders are
    kept as a hash, never as an address."""
    who, everyone = _key(scope, address), '*' + _key(scope)
    now = time.time()
    ok = []

    def step(cur):
        table = {k: [t for t in v if now - t < 86400] for k, v in (cur or {}).items() if isinstance(v, list)}
        table = {k: v for k, v in table.items() if v}
        last_hour = [t for t in table.get(everyone, []) if now - t < 3600]
        ok.append(len(table.get(who, [])) < MAX_PER_SENDER and len(last_hour) < MAX_PER_HOUR)
        if ok[-1]:
            table[who] = table.get(who, []) + [now]
            table[everyone] = table.get(everyone, []) + [now]
        return table
    mailbox._state().update_with_lock('auto_replies', step, {})
    return ok[-1]


def tidy():
    """Drop counts and claims that have aged out (also when no reply is going out any more)."""
    now = time.time()
    state = mailbox._state()
    if state.get('auto_replies'):
        state.update_with_lock('auto_replies', lambda cur: {
            k: kept for k, v in (cur or {}).items() if isinstance(v, list)
            for kept in [[t for t in v if now - t < 86400]] if kept}, {})
    if state.get('replied'):
        state.update_with_lock('replied', lambda cur: {
            k: t for k, t in (cur or {}).items() if now - float(t or 0) < CLAIM_DAYS * 86400}, {})


def claim(address, handle):
    """Take the one automatic answer this mail gets. False when it is taken."""
    key, now = _key(address, handle), time.time()
    got = []

    def step(cur):
        table = dict(cur or {})
        got.append(key not in table)
        if got[-1]:
            table[key] = now
        return table
    mailbox._state().update_with_lock('replied', step, {})
    return got[-1]


def release(address, handle):
    """The answer did not go out after all: the mail may be answered again."""
    key = _key(address, handle)
    mailbox._state().update_with_lock('replied', lambda cur: {k: v for k, v in (cur or {}).items() if k != key}, {})


CAP_WORDS = (f"automatic replies have hit their limit ({MAX_PER_SENDER} a day to one sender, "
             f"{MAX_PER_HOUR} an hour from one account), a guard against two machines mailing each other")


def refusal(scope, original, flags, address, people_scope, anyone):
    """Why this mail gets no automatic answer: (kind, words), or None when it
    may have one. kind: 'automated' | 'answered' | 'stranger'. The caller
    then takes the claim, and last of all counts the reply (under_cap): a
    reply that is refused or already taken must not use up the count."""
    why = message.is_automated(original)
    if why:
        return 'automated', (f"that email is automated ({why}), and automated mail is never answered automatically "
                             "(two machines would mail each other forever)")
    if b'\\answered' in tuple(f.lower() for f in flags or () if isinstance(f, bytes)):
        return 'answered', "that email has been answered already"
    if not anyone and not is_allowed(people_scope, address):
        return 'stranger', ("the sender is not someone in People with 'Allow email' ticked, and an automatic reply "
                            "only goes to those people (Settings > Plugins > Email, 'Auto-reply to anyone', opens that up)")
    return None
