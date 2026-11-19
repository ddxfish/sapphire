"""A mail server in memory, and the fixture that puts the plugin on it.

No test here reaches a network, a credential file or plugin state on disk:
`mail` swaps the IMAP login, the SMTP login, the account lookup and the
state store for the fakes below.
"""
import email
import imaplib
import email.policy
import email.utils
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def _sapphire_root(start: Path) -> Path:
    root = start.absolute()
    while not (root / 'sapphire.py').is_file():
        if root.parent == root:
            raise RuntimeError(f'Sapphire root (sapphire.py) not found above {start}')
        root = root.parent
    return root


_ROOT = str(_sapphire_root(Path(__file__).parent))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

DOVECOT = [((b'\\HasNoChildren',), b'.', 'INBOX'),
           ((b'\\HasNoChildren', b'\\Sent'), b'.', 'Sent'),
           ((b'\\HasNoChildren', b'\\Drafts'), b'.', 'Drafts'),
           ((b'\\HasNoChildren', b'\\Junk'), b'.', 'Junk'),
           ((b'\\HasNoChildren', b'\\Trash'), b'.', 'Trash'),
           ((b'\\HasNoChildren',), b'.', 'Receipts')]

GMAIL = [((b'\\HasNoChildren',), b'/', 'INBOX'),
         ((b'\\HasChildren', b'\\Noselect'), b'/', '[Gmail]'),
         ((b'\\All', b'\\HasNoChildren'), b'/', '[Gmail]/All Mail'),
         ((b'\\HasNoChildren', b'\\Sent'), b'/', '[Gmail]/Sent Mail'),
         ((b'\\HasNoChildren', b'\\Drafts'), b'/', '[Gmail]/Drafts'),
         ((b'\\HasNoChildren', b'\\Junk'), b'/', '[Gmail]/Spam'),
         ((b'\\HasNoChildren', b'\\Trash'), b'/', '[Gmail]/Trash')]


def raw_mail(frm='Alice <alice@x.com>', to='me@x.com', subject='Hello', body='Hi there',
             html=None, cc=None, headers=None, files=()):
    msg = email.message.EmailMessage(policy=email.policy.SMTP)
    msg['From'], msg['To'], msg['Subject'] = frm, to, subject
    if cc:
        msg['Cc'] = cc
    msg['Date'] = 'Tue, 17 Nov 2026 10:00:00 -0500'
    msg['Message-ID'] = email.utils.make_msgid(domain='x.com')
    for k, v in (headers or {}).items():
        if k in ('Date', 'Message-ID'):
            del msg[k]                       # one of each: the caller's replaces the default
        msg[k] = v
    if body is not None:
        msg.set_content(body)
        if html:
            msg.add_alternative(html, subtype='html')
    elif html:
        msg.set_content(html, subtype='html')
    for name, ctype, data in files:
        maintype, _, subtype = ctype.partition('/')
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return msg.as_bytes()


def _addrs(msg, name):
    out = []
    for nm, ad in email.utils.getaddresses([str(v) for v in (msg.get_all(name) or [])]):
        box, _, host = ad.partition('@')
        out.append(SimpleNamespace(name=nm.encode() or None, route=None,
                                   mailbox=box.encode(), host=host.encode()))
    return tuple(out) or None


class FakeIMAP:
    """The slice of IMAPClient the plugin uses, over dicts."""

    def __init__(self, listing=None, caps=('MOVE', 'UIDPLUS')):
        self.listing = list(listing or DOVECOT)
        self.caps = set(caps)
        self.boxes = {name: {} for _, _, name in self.listing}
        self.validity = {}
        self.next_uid = 1
        self.selected = None
        self.readonly = True
        self.calls = []
        self.logouts = 0
        self.dead = False             # True: the line has dropped, the next NOOP fails
        self.gm_hits = None           # uids a Gmail raw search answers with (None: all)
        self.top_level_refused = False

    # test helpers
    def add(self, folder, raw, flags=()):
        uid, self.next_uid = self.next_uid, self.next_uid + 1
        self.boxes[folder][uid] = {'raw': raw, 'flags': set(flags)}
        return uid

    def subjects(self, folder):
        return [str(email.message_from_bytes(m['raw'], policy=email.policy.default)['Subject'])
                for m in self.boxes[folder].values()]

    # IMAPClient surface
    def has_capability(self, cap):
        return cap in self.caps

    def list_folders(self):
        return list(self.listing)

    def create_folder(self, name):
        self.calls.append(('create', name))
        if self.top_level_refused and not name.upper().startswith('INBOX'):
            raise imaplib.IMAP4.error('NO Invalid mailbox name')       # a server that keeps folders under INBOX
        self.listing.append(((b'\\HasNoChildren',), b'.', name))
        self.boxes[name] = {}

    def subscribe_folder(self, name):
        self.calls.append(('subscribe', name))

    def remove_gmail_labels(self, uids, labels, silent=False):
        self.calls.append(('remove_labels', list(uids), list(labels)))
        for u in uids:
            m = self.boxes[self.selected][u]
            m['labels'] = tuple(x for x in m.get('labels', ()) if x not in labels)

    def select_folder(self, name, readonly=False):
        if name not in self.boxes:
            raise RuntimeError(f"no such folder {name}")
        self.selected, self.readonly = name, readonly
        return {b'EXISTS': len(self.boxes[name]), b'UIDNEXT': self.next_uid,
                b'UIDVALIDITY': self.validity.get(name, 1)}

    def folder_status(self, folder, what=None):
        self.calls.append(('status', folder))
        box = self.boxes[folder]
        return {b'MESSAGES': len(box),
                b'UNSEEN': sum(1 for m in box.values() if b'\\Seen' not in m['flags'])}

    def search(self, criteria='ALL', charset=None):
        # A bare string goes to the server as written: '3:5' = positions 3 to 5.
        crit = [criteria] if isinstance(criteria, str) else list(criteria)
        self.calls.append(('search', list(crit), charset))
        box = self.boxes[self.selected]
        uids = sorted(box)
        if len(crit) == 1 and ':' in str(crit[0]):
            low, high = (int(x) for x in str(crit[0]).split(':'))
            return uids[low - 1:high]
        return [u for u in uids if self._matches(box[u], list(crit))]

    def gmail_search(self, query, charset='UTF-8'):
        """Gmail's own search text. The fake only records it and answers with
        `gm_hits` (uids), or every mail of the selected folder."""
        assert 'X-GM-EXT-1' in self.caps, "X-GM-RAW on a server that is not Gmail"
        self.calls.append(('gmail_search', query))
        box = self.boxes[self.selected]
        return [u for u in sorted(box) if self.gm_hits is None or u in self.gm_hits]

    def _matches(self, m, crit):
        """Standard IMAP SEARCH over one mail: every key in `crit` must hold."""
        msg = email.message_from_bytes(m['raw'], policy=email.policy.default)
        body = ''
        part = msg.get_body(preferencelist=('plain', 'html'))
        if part is not None:
            body = part.get_content()
        sent = email.utils.parsedate_to_datetime(str(msg['Date'])).date()
        flags = m['flags']

        def has(header, want):
            return str(want).lower() in str(msg.get(header, '')).lower()

        def one(i):
            """(does key at crit[i] hold, index after it)"""
            k = crit[i]
            if isinstance(k, list):
                return self._matches(m, k), i + 1
            k = str(k).upper()
            if k == 'ALL':
                return True, i + 1
            if k == 'NOT':
                ok, j = one(i + 1)
                return not ok, j
            if k == 'OR':
                a, j = one(i + 1)
                b2, j = one(j)
                return a or b2, j
            simple = {'UNSEEN': b'\\Seen' not in flags, 'SEEN': b'\\Seen' in flags,
                      'FLAGGED': b'\\Flagged' in flags, 'UNFLAGGED': b'\\Flagged' not in flags,
                      'ANSWERED': b'\\Answered' in flags, 'UNANSWERED': b'\\Answered' not in flags,
                      'DELETED': b'\\Deleted' in flags, 'UNDELETED': b'\\Deleted' not in flags}
            if k in simple:
                return simple[k], i + 1
            v = crit[i + 1]
            if k in ('FROM', 'TO', 'CC', 'SUBJECT'):
                return has(k.title(), v), i + 2
            if k == 'BODY':
                return str(v).lower() in body.lower(), i + 2
            if k == 'TEXT':
                return str(v).lower() in m['raw'].decode('utf-8', 'replace').lower(), i + 2
            if k == 'SINCE':
                return sent >= v, i + 2
            if k == 'BEFORE':
                return sent < v, i + 2
            if k == 'ON':
                return sent == v, i + 2
            if k == 'UID':
                low = int(str(v).split(':')[0])
                return self._uid_of(m) >= low or self._uid_of(m) == max(self.boxes[self.selected]), i + 2
            raise AssertionError(f"the fake does not know SEARCH {k}")
        i = 0
        while i < len(crit):
            ok, i = one(i)
            if not ok:
                return False
        return True

    def _uid_of(self, m):
        return next(u for u, x in self.boxes[self.selected].items() if x is m)

    def fetch(self, uids, data, modifiers=None):
        self._need_selected()
        box, out = self.boxes[self.selected], {}
        if 'ENVELOPE' in data and any((box.get(u) or {}).get('bad_date') for u in uids):
            # what IMAPClient does with a date in year 9999: the whole fetch fails
            raise OverflowError('date value out of range')
        for uid in uids:
            m = box.get(uid)
            if not m:
                continue
            msg = email.message_from_bytes(m['raw'], policy=email.policy.default)
            has_file = any(p.get_content_disposition() == 'attachment' for p in msg.walk())
            out[uid] = {
                b'FLAGS': tuple(m['flags']),
                b'ENVELOPE': SimpleNamespace(
                    date=email.utils.parsedate_to_datetime(str(msg['Date'])).replace(tzinfo=None),
                    subject=str(msg['Subject']).encode(), from_=_addrs(msg, 'From'),
                    to=_addrs(msg, 'To'), cc=_addrs(msg, 'Cc')),
                b'BODYSTRUCTURE': ((b'text', b'plain'), (b'attachment',)) if has_file else (b'text', b'plain'),
                b'BODY[]': m['raw'],
                b'BODY[HEADER]': m['raw'].split(b'\r\n\r\n')[0].split(b'\n\n')[0] + b'\r\n\r\n',
            }
            if 'X-GM-LABELS' in data:
                out[uid] = {b'X-GM-LABELS': tuple(m.get('labels', ()))}
        return out

    def _need_selected(self):
        assert self.selected, "no folder selected"

    def _need_rw(self):
        assert not self.readonly, "write on a read-only selected folder"

    def add_flags(self, uids, flags, silent=False):
        self._need_rw()
        for u in uids:
            self.boxes[self.selected][u]['flags'] |= set(flags)

    def remove_flags(self, uids, flags, silent=False):
        self._need_rw()
        for u in uids:
            self.boxes[self.selected][u]['flags'] -= set(flags)

    def move(self, uids, folder):
        self._need_rw()
        assert 'MOVE' in self.caps
        self.calls.append(('move', list(uids), folder))
        for u in uids:
            self.boxes[folder][u] = self.boxes[self.selected].pop(u)

    def copy(self, uids, folder):
        self.calls.append(('copy', list(uids), folder))
        for u in uids:
            src = self.boxes[self.selected][u]
            self.boxes[folder][u + 1000] = {'raw': src['raw'], 'flags': set(src['flags'])}

    def uid_expunge(self, uids):
        self._need_rw()
        self.calls.append(('uid_expunge', list(uids)))
        for u in uids:
            if b'\\Deleted' in self.boxes[self.selected][u]['flags']:
                del self.boxes[self.selected][u]

    def expunge(self, messages=None):
        self._need_rw()
        self.calls.append(('expunge',))
        box = self.boxes[self.selected]
        for u in [u for u, m in box.items() if b'\\Deleted' in m['flags']]:
            del box[u]

    def append(self, folder, msg, flags=(), msg_time=None):
        self.add(folder, msg, flags)

    def noop(self):
        self.calls.append(('noop',))
        if self.dead:
            raise OSError('connection reset')

    def logout(self):
        self.logouts += 1
        self.selected = None        # a new login starts with no folder selected


class FakeState:
    def __init__(self):
        self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def update_with_lock(self, key, mutator, default=None):
        self.data[key] = mutator(self.data.get(key, default))
        return self.data[key]


CREDS = {'address': 'me@x.com', 'app_password': 'pw', 'auth_type': 'password',
         'imap_server': 'imap.x.com', 'smtp_server': 'smtp.x.com', 'imap_port': 993, 'smtp_port': 465}


@pytest.fixture
def mail(monkeypatch, tmp_path):
    """The plugin wired to a FakeIMAP. Yields a namespace:
    imap, smtp (a MagicMock), state, tool, mailbox, message, accounts, daemon.
    The mail index is a file in tmp_path: no test may open the real one."""
    from plugins.email import accounts, index, mailbox, message
    test_index = index.Index(tmp_path / 'email_index.db')
    monkeypatch.setattr(index, 'DB_PATH', tmp_path / 'not-the-real-index.db')
    monkeypatch.setattr(index, '_index', test_index)
    import plugins.email.routes.mail as mail_routes
    monkeypatch.setattr(mail_routes, '_account_scopes', lambda: ['work'])     # never the real credentials file
    from plugins.email.tools import email_tool

    imap, smtp, state = FakeIMAP(), MagicMock(), FakeState()
    mailbox._folder_cache.clear()
    mailbox.close_kept()
    monkeypatch.setattr(mailbox, '_kept', {})          # the Email page's kept logins start empty
    monkeypatch.setattr(accounts, '_refused', {})
    monkeypatch.setattr(mailbox, '_spawn', lambda work: work())     # the Sent copy is filed at once, not behind
    monkeypatch.setattr(mail_routes, 'SENT_TOKENS', {})
    # Auto-reply to anyone, as it was when these tests were written; the contacts rule has its own test.
    from plugins.email import daemon as _daemon
    monkeypatch.setattr(_daemon, '_settings', {'auto_reply_anyone': True})
    # Plain 'inbox:5' ids in these tests: the check code has its own file (test_email_ids.py).
    monkeypatch.setattr(mailbox, '_TAGS', False)
    logins = []

    def connect(creds):
        logins.append(dict(creds))
        return ns.imap
    monkeypatch.setattr(accounts, 'imap_connect', connect)
    monkeypatch.setattr(accounts, 'smtp_connect', lambda creds: smtp)
    monkeypatch.setattr(accounts, 'get_creds', lambda scope: (dict(CREDS), None) if scope else (None, "Email is disabled for this chat."))
    monkeypatch.setattr(mailbox, '_state', lambda: state)
    monkeypatch.setattr(email_tool, '_get_current_email_scope', lambda: 'work')
    monkeypatch.setattr(email_tool, '_get_current_people_scope', lambda: 'default')
    settings = {}
    monkeypatch.setattr(email_tool, '_setting', lambda key, default=False: settings.get(key, default))
    def sent():
        """(message, to_addrs) of the last mail handed to SMTP."""
        call = smtp.send_message.call_args
        return call.args[0], call.kwargs['to_addrs']

    def use(fake):
        """Swap in another FakeIMAP (a Gmail-shaped one, one without MOVE)."""
        ns.imap = fake
        mailbox._folder_cache.clear()
        mailbox.close_kept()
        return fake

    ns = SimpleNamespace(imap=imap, smtp=smtp, state=state, tool=email_tool, mailbox=mailbox,
                         message=message, accounts=accounts, settings=settings, creds=dict(CREDS),
                         raw=raw_mail, sent=sent, use=use, FakeIMAP=FakeIMAP, GMAIL=GMAIL, logins=logins,
                         index=test_index)
    yield ns
    test_index.close()
    mailbox.close_kept()                                # also stops their idle timers
    mailbox._folder_cache.clear()
