# core/plugins/email/mailbox.py - the mail server, as folders and ids
"""One IMAP session over IMAPClient.

A message is addressed by a stable id, `<folder>:<uid>~<check>`
(inbox:4821~k7p, spam:17~m2x). The uid is the server's own and never shifts,
so an id read an hour ago, or before a restart, still points at the same
mail. The check code is worked out from the account's address, the folder
and the folder's UIDVALIDITY, so the id itself says which mailbox it belongs
to: used on another account (a chat whose email account was switched), or
after the server rebuilt the folder, the code no longer matches and the id
is refused instead of landing on whatever mail now has that number. Nothing
is stored for this and nothing here caches mail: every call asks the server.

Her tools and the daemon log in for each call and log out after it. The
Email page keeps its login instead (`keep=`): a person clicks many times a
minute, and every fresh login costs a handful of round trips to the server
before the first useful command. A kept login is used by one request at a
time, is checked with a NOOP when it has sat idle, and is closed after
KEEP_IDLE seconds without use or when the plugin unloads (close_kept).

Folders have roles (inbox, sent, drafts, archive, spam, trash) found by the
server's special-use flags first and by common names second; any other
folder is addressed by its own name.
"""
import hashlib
import imaplib
import logging
import re
import threading
import time
from datetime import datetime
from email.header import decode_header, make_header
from types import SimpleNamespace

from plugins.email import accounts, message, search as query_words

logger = logging.getLogger(__name__)

ROLES = ('inbox', 'sent', 'drafts', 'archive', 'spam', 'trash')
# Flags in priority order. Gmail has no \Archive: moving to \All (All Mail)
# and out of the inbox is what its own Archive button does.
_FLAGS = {'sent': (b'\\sent',), 'drafts': (b'\\drafts',), 'archive': (b'\\archive', b'\\all'),
          'spam': (b'\\junk',), 'trash': (b'\\trash',)}
# Names, for servers that send no special-use flags. English first, then the
# names German, French, Spanish, Italian, Dutch and Portuguese servers and mail
# programs use (written with escapes so this file stays plain ASCII).
_NAMES = {'sent': ('sent', 'sent items', 'sent mail', 'sent messages',
                   'gesendet', 'gesendete objekte', 'gesendete elemente', 'envoy\u00e9s', '\u00e9l\u00e9ments envoy\u00e9s',
                   'enviados', 'elementos enviados', 'posta inviata', 'inviata', 'verzonden', 'verzonden items',
                   'itens enviados'),
          'drafts': ('drafts', 'draft', 'entw\u00fcrfe', 'brouillons', 'borradores', 'bozze', 'concepten', 'rascunhos'),
          'archive': ('archive', 'all mail', 'archives', 'archiv', 'archivio', 'archivo', 'archief', 'arquivo'),
          'spam': ('junk', 'spam', 'junk email', 'junk e-mail', 'bulk mail', 'bulk', 'spamverdacht', 'junk-e-mail',
                   'ind\u00e9sirables', 'courrier ind\u00e9sirable', 'correo no deseado', 'posta indesiderata',
                   'ongewenste e-mail', 'lixo eletr\u00f4nico', 'lixo eletr\u00f3nico'),
          'trash': ('trash', 'deleted items', 'deleted messages', 'deleted',
                    'papierkorb', 'gel\u00f6schte elemente', 'gel\u00f6schte objekte', 'corbeille',
                    '\u00e9l\u00e9ments supprim\u00e9s', 'papelera', 'elementos eliminados', 'cestino', 'posta eliminata',
                    'prullenbak', 'verwijderde items', 'lixeira', 'itens exclu\u00eddos')}
_GMAIL_PREFIXES = ('[Gmail]/', '[Google Mail]/')
_CONTROL = re.compile(r'[\x00-\x1f\x7f\u2028\u2029]+')
MAX_HEADER = 300             # characters of a subject or a name shown in a list
MAX_SEARCH_FOLDERS = 25      # an everywhere-search looks in at most this many folders
MAX_ATTACH_CHECK = 400       # has:attachment is checked on at most this many newest matches per folder
FOLDER_TTL = 600
_folder_cache: dict = {}   # scope -> (timestamp, {role: name}, [all names])


class MailError(Exception):
    """A refusal whose text is the answer (bad id, no such folder, ...)."""


# -- kept logins (the Email page) ----------------------------------------------

KEEP_IDLE = 300        # seconds an unused login stays open
KEEP_CHECK = 25        # idle longer than this: a NOOP proves the line before it is used
KEEP_WAIT = 45         # seconds a request waits for the one in front of it


class _Kept:
    def __init__(self):
        self.lock = threading.Lock()       # one request at a time on a connection
        self.client = None
        self.mark = ''                     # which credentials it was opened with
        self.used = 0.0
        self.timer = None


_kept: dict = {}
_kept_guard = threading.Lock()


def _slot(key):
    with _kept_guard:
        return _kept.setdefault(key, _Kept())


def _login(scope, creds):
    """A logged-in client, through the refused-login hold every caller shares."""
    try:
        return accounts.connect(scope, creds)
    except accounts.LoginHeld as e:
        raise MailError(str(e))


def _drop(slot):
    """Close a kept login. The slot's lock is held by the caller."""
    client, slot.client, slot.mark = slot.client, None, ''
    if client is not None:
        try:
            client.logout()
        except Exception:
            pass


def _reap(key):
    slot = _kept.get(key)
    if slot is None or not slot.lock.acquire(blocking=False):
        return                             # in use right now: its release arms a new timer
    try:
        if slot.client is not None and time.time() - slot.used >= KEEP_IDLE:
            _drop(slot)
    finally:
        slot.lock.release()


def _arm(key, slot):
    if slot.timer is not None:
        slot.timer.cancel()
    slot.timer = threading.Timer(KEEP_IDLE + 2, _reap, [key])
    slot.timer.daemon = True
    slot.timer.start()


def close_kept(scope=None):
    """Log out the kept logins (all, or one account's): plugin unload, or the
    account was changed or removed."""
    with _kept_guard:
        keys = [k for k in _kept if scope is None or k == scope or k.startswith(f"{scope}\n")]
    for key in keys:
        slot = _kept.get(key)
        if slot is None:
            continue
        if slot.timer is not None:
            slot.timer.cancel()
        if slot.lock.acquire(timeout=5):
            try:
                _drop(slot)
            finally:
                slot.lock.release()


_STALE = ("That id is not for this mailbox as it is now: it came from another account, or the mail "
          "server rebuilt the folder. List the folder again for current ids.")
_TAG_ALPHABET = 'abcdefghijkmnpqrstuvwxyz23456789'
# The check code in every id. Off only in tests of everything else, so they
# can write plain 'inbox:5'; nothing in the app turns it off.
_TAGS = True


def _tag(address, real, validity):
    """Three characters that stand for (account, folder, UIDVALIDITY)."""
    digest = hashlib.sha256(f"{str(address or '').strip().lower()}\n{real}\n{validity}".encode()).digest()
    n = int.from_bytes(digest[:4], 'big')
    return ''.join(_TAG_ALPHABET[(n >> shift) & 31] for shift in (0, 5, 10))


def _state():
    from core.plugin_loader import plugin_loader
    return plugin_loader.get_plugin_state('email')


def _dec(raw) -> str:
    """An ENVELOPE string (bytes, maybe RFC 2047 encoded) as text."""
    if raw is None:
        return ''
    text = raw.decode('utf-8', errors='replace') if isinstance(raw, bytes) else str(raw)
    try:
        text = str(make_header(decode_header(text)))
    except Exception:
        pass
    # An encoded word can hide a line break: in a list that would be a forged row.
    return _CONTROL.sub(' ', text)[:MAX_HEADER].strip()


def _person(addrs) -> str:
    """First address of an ENVELOPE list as a display name (never the address)."""
    for a in addrs or ():
        if a.mailbox and a.host:
            return message.display(_dec(a.name), f"{_dec(a.mailbox)}@{_dec(a.host)}")
    return 'Unknown'


def _has_attachment(struct) -> bool:
    if isinstance(struct, (tuple, list)):
        return any(_has_attachment(x) for x in struct)
    return isinstance(struct, bytes) and struct.lower() == b'attachment'


def _when(dt) -> str:
    if not isinstance(dt, datetime):
        return '?'
    return dt.strftime('%b %d, %H:%M' if dt.year == datetime.now().year else '%b %d %Y')


def parse_id(handle):
    """'inbox:4821~k7p' -> ('inbox', 4821, 'k7p'); the code is '' when the id
    has none. Raises MailError on anything that is not an id."""
    text = str(handle or '').strip()
    if text.startswith('[') and text.endswith(']'):   # copied with the listing's brackets
        text = text[1:-1].strip()                     # ('[Gmail]/Old:9' keeps its own bracket)
    folder, sep, rest = text.rpartition(':')
    uid, _, tag = rest.partition('~')
    if not sep or not folder or not uid.strip().isdigit():
        raise MailError(f"'{handle}' is not an email id. Ids look like inbox:4821~k7p and come from "
                        f"get_inbox or search_emails.")
    return folder.strip(), int(uid), tag.strip().lower()


def split_id(handle):
    """'inbox:4821~k7p' -> ('inbox', 4821)."""
    return parse_id(handle)[:2]


class Mailbox:
    """`with Mailbox(scope, creds) as mb:` - logs in, logs out.

    `keep` is for the Email page only: True (or a name, for a second line
    beside the first) uses the account's kept login instead and leaves it
    open for the next request.

    `loose` is for the daemon only: an id with no check code is taken as it
    is (an event queued before ids carried one). A code that is there is
    always checked."""

    def __init__(self, scope, creds, keep=False, loose=False):
        self.scope, self.creds, self.c, self.loose = scope, creds, None, loose
        self.real = self.validity = self.exists = self.uidnext = None
        self._fkey = f"{scope}\n{str(creds.get('address') or '').lower()}\n{creds.get('imap_server')}"
        self.wait = KEEP_WAIT
        self._key = None if not keep else (scope if keep is True else f"{scope}\n{keep}")
        self._slot = None
        self.broken = False      # an error was swallowed that may have left the line mid-answer

    def swallowed(self, error):
        """Call where an exception is caught and work goes on. A plain "NO"
        from the server leaves the line fine; a timeout or a dropped socket
        does not, and a kept login in that state must not go to the next request."""
        if not isinstance(error, imaplib.IMAP4.error) or isinstance(error, imaplib.IMAP4.abort):
            self.broken = True

    def __enter__(self):
        if self._key is None:
            self.c = _login(self.scope, self.creds)
            return self
        slot = _slot(self._key)
        if not slot.lock.acquire(timeout=self.wait):
            raise MailError("That mailbox is still busy with the last request. Try again in a moment.")
        try:
            mark, idle = accounts.creds_mark(self.creds), time.time() - slot.used
            if slot.client is not None and (slot.mark != mark or idle >= KEEP_IDLE):
                _drop(slot)
            if slot.client is not None and idle > KEEP_CHECK:
                try:
                    slot.client.noop()
                except Exception as e:
                    logger.info(f"[EMAIL] kept login for '{self.scope}' had gone quiet ({type(e).__name__}); logging in again")
                    _drop(slot)
            if slot.client is None:              # a line that is up is never judged by the hold
                slot.client, slot.mark = _login(self.scope, self.creds), mark
            self.c, self._slot = slot.client, slot
        except BaseException:
            slot.lock.release()
            raise
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._slot is None:
            try:
                self.c.logout()
            except Exception:
                pass
            return False
        slot, self._slot = self._slot, None
        try:
            slot.used = time.time()
            # Our own refusals (no such folder, a stale id) leave the line fine.
            # Anything else may have left it mid-answer: do not hand it on.
            if self.broken or (exc_type is not None and not issubclass(exc_type, MailError)):
                _drop(slot)
            else:
                _arm(self._key, slot)
        finally:
            slot.lock.release()
        return False

    # -- folders --------------------------------------------------------------

    def folders(self):
        """({role: real name}, [every selectable folder name])."""
        hit = _folder_cache.get(self._fkey)
        if hit and time.time() - hit[0] < FOLDER_TTL:
            return hit[1], hit[2]
        listing = []
        for flags, delim, name in self.c.list_folders():
            flags = tuple(f.lower() for f in flags if isinstance(f, bytes))
            if b'\\noselect' in flags or b'\\nonexistent' in flags:
                continue
            if isinstance(delim, bytes):
                delim = delim.decode('utf-8', errors='replace')
            listing.append((flags, delim, name))
        roles = {'inbox': 'INBOX'}
        for role, wanted in _FLAGS.items():
            for flag in wanted:
                found = next((n for fl, _, n in listing if flag in fl), None)
                if found:
                    roles[role] = found
                    break
        for role, wanted in _NAMES.items():
            if role in roles:
                continue
            for _, delim, name in listing:
                leaf = name.split(delim)[-1] if delim else name
                if leaf.lower() in wanted:
                    roles[role] = name
                    break
        all_names = [n for _, _, n in listing]
        _folder_cache[self._fkey] = (time.time(), roles, all_names)
        return roles, all_names

    def resolve(self, key) -> str:
        """A role or a folder name -> the server's folder name."""
        k = str(key or 'inbox').strip()
        roles, all_names = self.folders()
        if k.lower() in roles:
            return roles[k.lower()]
        if k.lower() in ROLES:
            raise MailError(f"This mail server has no {k.lower()} folder.")
        for n in all_names:
            if n.lower() == k.lower():
                return n
        short = self._short_names()
        for n, nick in short.items():
            if nick.lower() == k.lower():
                return n
        raise MailError(f"No folder named '{k}'. Folders: {', '.join(self.folder_keys())}")

    def _short_names(self) -> dict:
        """{server name: the name shown} for Gmail's own folders, which the
        server spells '[Gmail]/Starred'. Shown as 'Starred' unless that would
        be taken for a role or for another folder."""
        roles, all_names = self.folders()
        taken = set(roles.values())
        seen = {n.lower() for n in all_names} | set(ROLES)
        out = {}
        for n in all_names:
            if n in taken:
                continue
            for prefix in _GMAIL_PREFIXES:
                nick = n[len(prefix):]
                if n.startswith(prefix) and nick and nick.lower() not in seen:
                    out[n] = nick
                    seen.add(nick.lower())
                    break
        return out

    def key_for(self, real) -> str:
        roles, _ = self.folders()
        role = next((role for role, n in roles.items() if n == real), None)
        return role or self._short_names().get(real, real)

    def folder_keys(self) -> list:
        """What to show her: the roles this server has, then its other folders."""
        roles, all_names = self.folders()
        taken = set(roles.values())
        short = self._short_names()
        return [r for r in ROLES if r in roles] + [short.get(n, n) for n in all_names if n not in taken]

    def select(self, key, readonly=True) -> str:
        """Select a folder; returns the server's name for it."""
        real = self.resolve(key)
        try:
            info = self.c.select_folder(real, readonly=readonly)
        except Exception:
            _folder_cache.pop(self._fkey, None)   # the folder map may be stale
            raise
        self.real = real
        self.exists = info.get(b'EXISTS')
        self.uidnext = info.get(b'UIDNEXT')
        self.validity = info.get(b'UIDVALIDITY')
        return real

    def id_for(self, key, uid) -> str:
        """The id of a mail in the folder that is selected now."""
        if not _TAGS:
            return f"{key}:{uid}"
        return f"{key}:{uid}~{_tag(self.creds.get('address'), self.real, self.validity)}"

    def why_not(self, handle, tag):
        """'' when an id's check code fits the folder that is selected now,
        else the refusal for it."""
        if not _TAGS:
            return ''
        if not tag:
            if self.loose:
                return ''
            return (f"'{handle}' is missing its check code. An id looks like inbox:4821~k7p, and the part "
                    "after ~ belongs to it: copy the id whole from get_inbox or search_emails.")
        return '' if tag == _tag(self.creds.get('address'), self.real, self.validity) else _STALE

    # -- reading --------------------------------------------------------------

    def headers(self, key, uids) -> list:
        """Listing rows for `uids` of the selected folder, in the order given.
        Uids that no longer exist are left out."""
        if not uids:
            return []
        data = self.listing(uids)
        sent = key == 'sent'
        rows = []
        for uid in uids:
            item = data.get(uid)
            env = item.get(b'ENVELOPE') if item else None
            if env is None:
                continue
            flags = tuple(f.lower() for f in item.get(b'FLAGS', ()) if isinstance(f, bytes))
            rows.append({
                'id': self.id_for(key, uid), 'uid': uid,
                'name': _person(env.to if sent else env.from_), 'to_name': _person(env.to),
                'subject': _dec(env.subject) or '(no subject)',
                'date': _when(env.date), 'when': env.date,
                'unread': b'\\seen' not in flags,
                'starred': b'\\flagged' in flags,
                'attach': _has_attachment(item.get(b'BODYSTRUCTURE')),
            })
        return rows

    def listing(self, uids, extra=()):
        """fetch() of what a list row needs (ENVELOPE, FLAGS, BODYSTRUCTURE
        and `extra`) for uids of the selected folder.

        One mail must not take the list down. The library turns each
        envelope's date into a datetime while it reads the answer, and a
        mail dated year 9999 overflows that for the WHOLE fetch. The answer
        was already read off the line when that happens, so the mails are
        asked for again one at a time, and a mail that still cannot be read
        gets an envelope rebuilt from its raw header (with no date)."""
        items = ['ENVELOPE', 'FLAGS', 'BODYSTRUCTURE', *extra]
        try:
            return self.c.fetch(uids, items)
        except (OverflowError, ValueError, TypeError) as e:
            logger.warning(f"[EMAIL] a mail in this list has a header the mail library cannot read ({e}); "
                           "reading the list one mail at a time")
        out = {}
        for uid in uids:
            try:
                out.update(self.c.fetch([uid], items))
            except (OverflowError, ValueError, TypeError):
                try:
                    got = dict(self.c.fetch([uid], ['FLAGS', *extra, 'BODY.PEEK[HEADER]']).get(uid) or {})
                    head = message.parse(got.get(b'BODY[HEADER]') or b'')
                    box = lambda name: tuple(SimpleNamespace(name=n.encode() or None, route=None,
                                                             mailbox=a.partition('@')[0].encode(),
                                                             host=a.partition('@')[2].encode())
                                             for n, a in message.addresses(head, name)) or None
                    got[b'ENVELOPE'] = SimpleNamespace(date=None, subject=message.header(head, 'Subject'),
                                                       from_=box('From'), to=box('To'), cc=box('Cc'))
                    out[uid] = got
                except Exception as e:
                    logger.warning(f"[EMAIL] uid {uid} left out of the list: {e}")
        return out

    def latest(self, folder, count, unread_only=False):
        """(folder key, newest-first rows) for the newest `count` mails."""
        key = self.key_for(self.select(folder))
        uids = sorted(self.c.search(['UNSEEN'] if unread_only else ['ALL']))[-count:]
        return key, self.headers(key, uids[::-1])

    def search(self, folder, sender=None, content=None, date=None, count=20):
        """(folder key, newest-first rows). `date` (a date) returns about
        count/2 mails on each side of that day, the other terms ANDed in."""
        for value in (sender, content):
            if value and query_words._CONTROL.search(str(value)):
                raise MailError("A search cannot contain line breaks or control characters.")
        key = self.key_for(self.select(folder))
        criteria = []
        if sender:
            criteria += ['TO' if key == 'sent' else 'FROM', sender]
        if content:
            criteria += ['OR', 'SUBJECT', content, 'BODY', content]
        charset = None if all(str(c).isascii() for c in criteria) else 'UTF-8'
        if date is not None:
            side = max(1, count // 2)
            older = sorted(self.c.search(criteria + ['BEFORE', date], charset))[-side:]
            newer = sorted(self.c.search(criteria + ['SINCE', date], charset))[:side]
            uids = older + newer
        else:
            uids = sorted(self.c.search(criteria or ['ALL'], charset))[-count:]
        return key, self.headers(key, uids[::-1])

    def find(self, terms, folder='inbox', everywhere=False, limit=50, offset=0, make=None, stamp=None):
        """Search with the terms of search.parse(). -> (rows newest first, how
        many matched). One folder, or with `everywhere` every folder but spam
        and trash (on Gmail that is one look in All Mail).

        `make(folder key, uids)` builds the rows while that folder is
        selected (default: headers()); `stamp(row)` is what rows from several
        folders are put in order by. `offset` pages through one folder."""
        make = make or self.headers
        stamp = stamp or (lambda r: r.get('when') or datetime.min)
        try:
            criteria, charset, attach = query_words.imap(terms)
            is_gmail = self.c.has_capability('X-GM-EXT-1')
            raw = query_words.for_gmail(terms) if is_gmail else None
        except ValueError as e:
            raise MailError(str(e))
        roles, _ = self.folders()
        if not everywhere:
            keys = [folder or 'inbox']
        elif is_gmail and 'archive' in roles:
            keys = ['archive']                       # Gmail's All Mail holds every mail but spam and trash
        else:
            keys = [k for k in self.folder_keys() if k not in ('spam', 'trash')][:MAX_SEARCH_FOLDERS]
        rows, total = [], 0
        for want in keys:
            try:
                key = self.key_for(self.select(want))
                if raw:
                    uids = self.c.gmail_search(raw)
                else:
                    uids = self.c.search(criteria, charset)
                    if attach is not None:
                        uids = self._by_attachment(uids, attach)
            except Exception as e:
                if not everywhere:
                    raise
                # a folder with no read right, a calendar folder, one removed a moment ago
                self.swallowed(e)
                logger.info(f"[EMAIL] search skipped the folder '{want}': {type(e).__name__}")
                if self.broken:
                    raise
                continue
            uids = sorted(uids, reverse=True)
            total += len(uids)
            page = uids[offset:offset + limit] if not everywhere else uids[:limit]
            if page:
                found = make(key, page)
                if raw and everywhere:
                    self._gmail_places(found)
                rows.extend(found)
        if everywhere and len(keys) > 1:
            rows.sort(key=stamp, reverse=True)
            rows = rows[:limit]
        return rows, total

    def _gmail_places(self, rows):
        """Gmail's All Mail holds every mail, so a hit there does not say
        where the mail is. Its labels do: each row gets `where` (inbox, sent,
        drafts, a label's name, or archive when it has none). One extra
        fetch; a failure leaves the rows as they are."""
        uids = {}
        for r in rows:
            try:
                uids[parse_id(r.get('id', ''))[1]] = r
            except MailError:
                continue
        if not uids:
            return
        try:
            data = self.c.fetch(list(uids), ['X-GM-LABELS'])
        except Exception as e:
            self.swallowed(e)
            logger.debug(f"[EMAIL] Gmail labels not read: {e}")
            return
        for uid, r in uids.items():
            labels = [_dec(x) if isinstance(x, bytes) else str(x) for x in (data.get(uid) or {}).get(b'X-GM-LABELS') or ()]
            low = [x.lower() for x in labels]
            place = next((role for mark, role in (('\\inbox', 'inbox'), ('\\sent', 'sent'), ('\\draft', 'drafts'))
                          if mark in low), None)
            if place is None:
                place = next((x for x in labels if not x.startswith('\\')), 'archive')
            r['where'] = place

    def _by_attachment(self, uids, want):
        """IMAP has no search key for attachments: look at the structure of
        the newest matches of the selected folder and keep the ones that have
        (or with want=False, have not) a file."""
        newest = sorted(uids, reverse=True)[:MAX_ATTACH_CHECK]
        if not newest:
            return []
        data = self.c.fetch(newest, ['BODYSTRUCTURE'])
        return [u for u in newest if _has_attachment((data.get(u) or {}).get(b'BODYSTRUCTURE')) == want]

    def message(self, handle, mark_read=False):
        """(parsed message, flags) for an id. PEEK: reading never marks a mail
        seen by accident; mark_read=True does it on purpose."""
        folder, uid, tag = parse_id(handle)
        self.select(folder, readonly=not mark_read)
        why = self.why_not(handle, tag)
        if why:
            raise MailError(why)
        data = self.c.fetch([uid], ['BODY.PEEK[]', 'FLAGS'])
        raw = (data.get(uid) or {}).get(b'BODY[]')
        if not raw:
            raise MailError(f"No email {handle} (moved or deleted since it was listed). "
                            f"List the folder again.")
        flags = tuple(f.lower() for f in data[uid].get(b'FLAGS', ()) if isinstance(f, bytes))
        if mark_read and b'\\seen' not in flags:
            try:
                self.c.add_flags([uid], [b'\\Seen'])
            except Exception as e:
                logger.warning(f"[EMAIL] could not mark {handle} read: {e}")
        return message.parse(raw), flags

    # -- changing -------------------------------------------------------------

    def _grouped(self, handles):
        """{server folder: {uid: (the id as given, its check code)}}"""
        groups = {}
        for h in handles:
            folder, uid, tag = parse_id(h)
            groups.setdefault(self.resolve(folder), {}).setdefault(uid, (str(h).strip(), tag))
        return groups

    def _checked(self, given, skipped):
        """The uids of the selected folder whose ids fit it; the others go to `skipped` with the reason."""
        ok = []
        for uid, (handle, tag) in given.items():
            why = self.why_not(handle, tag)
            if why:
                skipped.append((handle, why))
            else:
                ok.append(uid)
        return ok

    def move(self, handles, dest, create=None):
        """Move mails to the `dest` folder. Returns (moved, skipped):
        moved = [(id, subject), ...], skipped = [(id, why), ...]. The ids are
        the ones passed in: a moved mail gets a new id in its new folder.

        MOVE where the server has it (one atomic step). Otherwise copy, flag
        \\Deleted, expunge - and a copy the server refuses is reported, never
        claimed as moved. `create` names a folder to make when the server has
        no such role (archive only): spam and trash are refused instead, so a
        delete can never become a permanent one."""
        try:
            target = self.resolve(dest)
        except MailError:
            if not create:
                raise
            target = self._create(create)
        moved, skipped = [], []
        for real, given in self._grouped(handles).items():
            key = self.key_for(real)
            if real == target:
                if key == 'archive' and self.c.has_capability('X-GM-EXT-1'):
                    self._gmail_archive(real, given, moved, skipped)
                else:
                    skipped += [(h, f"already in {dest}") for h, _tag_ in given.values()]
                continue
            self.select(real, readonly=False)
            uids = self._checked(given, skipped)
            rows = {r['uid']: r for r in self.headers(key, uids)}
            skipped += [(given[u][0], "no longer there") for u in uids if u not in rows]
            present = [u for u in uids if u in rows]
            if not present:
                continue
            copied = False
            try:
                if self.c.has_capability('MOVE'):
                    self.c.move(present, target)
                else:
                    self.c.copy(present, target)
                    copied = True
                    self.c.add_flags(present, [b'\\Deleted'])
                    if self.c.has_capability('UIDPLUS'):
                        self.c.uid_expunge(present)
                    elif not set(self.c.search(['DELETED'])) - set(present):
                        self.c.expunge()
                    else:
                        # A plain EXPUNGE removes EVERY mail marked deleted in this folder,
                        # also ones another mail program only marked. Ours stay marked.
                        logger.info(f"[EMAIL] '{key}': moved mail left marked deleted (other mail here is marked too)")
            except Exception as e:
                self.swallowed(e)
                why = (f"copied to {dest}, but could not be taken out of {key}: it is in both now ({e})" if copied
                       else f"server refused: {e}")
                skipped += [(given[u][0], why) for u in present]
                continue
            moved += [(given[u][0], rows[u]['subject']) for u in present]
        return moved, skipped

    def _gmail_archive(self, real, given, moved, skipped):
        """Archive mail addressed by its All Mail id (what a search over all
        mail returns on Gmail). It already IS in All Mail, so a move there
        does nothing: what puts it in the inbox is the Inbox label, and
        archiving means taking that label off."""
        self.select(real, readonly=False)
        uids = self._checked(given, skipped)
        if not uids:
            return
        try:
            labels = self.c.fetch(uids, ['X-GM-LABELS'])
            in_inbox = [u for u in uids
                        if any((x.decode('utf-8', 'replace') if isinstance(x, bytes) else str(x)).lower() == '\\inbox'
                               for x in (labels.get(u) or {}).get(b'X-GM-LABELS') or ())]
            skipped += [(given[u][0], "already archived") for u in uids if u not in in_inbox]
            if not in_inbox:
                return
            rows = {r['uid']: r for r in self.headers('archive', in_inbox)}
            self.c.remove_gmail_labels(in_inbox, [b'\\Inbox'])
        except Exception as e:
            self.swallowed(e)
            skipped += [(given[u][0], f"server refused: {e}") for u in uids
                        if given[u][0] not in {h for h, _why in skipped}]
            return
        moved += [(given[u][0], (rows.get(u) or {}).get('subject', '')) for u in in_inbox]

    def _create(self, name):
        """Make a folder (the archive, when a server has none) where this
        server keeps its folders, and return its full name. Some servers put
        every folder under INBOX ('INBOX.Archive') and refuse one at the top."""
        _, all_names = self.folders()
        prefix = ''
        for n in all_names:
            m = re.match(r'(?i)(INBOX[./])', n)
            if m:
                prefix = m.group(1)
                break
        tries = [prefix + name, name] if prefix else [name]
        last = None
        for full in tries:
            try:
                self.c.create_folder(full)
                try:
                    self.c.subscribe_folder(full)      # other mail programs only show subscribed folders
                except Exception as e:
                    self.swallowed(e)
                _folder_cache.pop(self._fkey, None)
                return full
            except Exception as e:
                self.swallowed(e)
                last = e
                if self.broken:
                    break
        raise MailError(f"This mail server has no {name.lower()} folder and would not let one be made ({last}).")

    def flag(self, handles, flag, on=True):
        """Set or clear a flag. Returns (changed count, skipped [(id, why)])."""
        changed, skipped = 0, []
        for real, given in self._grouped(handles).items():
            self.select(real, readonly=False)
            uids = self._checked(given, skipped)
            there = self.c.fetch(uids, ['FLAGS']) if uids else {}
            present = [u for u in uids if u in there]
            skipped += [(given[u][0], "no longer there") for u in uids if u not in there]
            if present:
                try:
                    (self.c.add_flags if on else self.c.remove_flags)(present, [flag])
                except Exception as e:           # a read-only or shared folder
                    self.swallowed(e)
                    skipped += [(given[u][0], f"server refused: {e}") for u in present]
                    continue
                changed += len(present)
        return changed, skipped

    def append(self, folder, msg, flags=(b'\\Seen',)):
        self.c.append(self.resolve(folder), msg.as_bytes(), flags=flags)

    def keeps_sent_copy(self) -> bool:
        """Gmail and Microsoft file a copy in Sent themselves when mail goes
        out through their SMTP; saving another would show every mail twice."""
        host = str(self.creds.get('smtp_server', '')).lower()
        return self.c.has_capability('X-GM-EXT-1') or 'office365' in host or 'outlook' in host


def parse_day(text):
    """'YYYY-MM-DD' -> date, or None."""
    try:
        return datetime.strptime(str(text).strip(), '%Y-%m-%d').date()
    except Exception:
        return None


def _spawn(work):
    """Run `work` behind the request that asked for it."""
    threading.Thread(target=work, name='email-sent-copy', daemon=True).start()


def send(scope, creds, msg, rcpts, after=None, wait=True) -> str:
    """Hand `msg` to SMTP for `rcpts` (To + Cc + Bcc addresses), then keep a
    copy in Sent where the provider does not. Returns '' or a short note when
    the mail went out but the copy could not be saved. Raises if SMTP fails.

    `after()` runs once the copy is dealt with (marking the original
    answered). wait=False (the Email page): return as soon as the server has
    taken the mail and do the rest behind. Uploading the whole mail a second
    time for the Sent copy can take longer than a browser waits, and a person
    who is told "try again" after the mail already left sends it twice."""
    smtp = accounts.smtp_connect(creds)
    with smtp:
        refused = smtp.send_message(msg, from_addr=creds['address'], to_addrs=list(rcpts))
    refused = refused if isinstance(refused, dict) else {}
    if refused:
        logger.warning(f"[EMAIL] the server refused {len(refused)} of {len(list(rcpts))} recipient(s)")

    def rest():
        note = _file_sent(scope, creds, msg)
        if after:
            try:
                after()
            except Exception as e:
                logger.debug(f"[EMAIL] after-send step failed: {e}")
        return note
    said = f" ({len(refused)} address{'es' if len(refused) != 1 else ''} refused by the server: {', '.join(refused)})" if refused else ''
    if not wait:
        _spawn(rest)
        return said
    return said + rest()


def _file_sent(scope, creds, msg) -> str:
    try:
        with Mailbox(scope, creds) as mb:
            if not mb.keeps_sent_copy():
                mb.append('sent', msg)
    except MailError:
        return " (no Sent folder on this server, so no copy was kept)"
    except Exception as e:
        logger.warning(f"[EMAIL] sent, but could not save a copy to Sent: {e}")
        return " (sent, but a copy could not be saved to Sent)"
    return ''
