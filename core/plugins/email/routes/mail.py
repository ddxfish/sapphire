# core/plugins/email/routes/mail.py - the Email page's doors
#
# Mounted at /api/plugin/email/... (login, CSRF and the rate meter are core's).
# Plain `def` on purpose: core threadpools sync handlers, so a slow mail
# server never blocks the web loop.
#
# The page is the PERSON, not the AI: it sees every account and every
# address, and the People whitelist does not apply to what it sends. Her
# tools (tools/email_tool.py) keep their own gates.
#
# One page action = one request. Ids are the same `<folder>:<uid>~<check>` ids
# her tools use: each carries its own check code (mailbox.py), so an id from
# another account or from before a server rebuild is refused instead of
# opening a different mail, whichever folder the page happens to be showing.
import base64
import email.utils
import imaplib
import ipaddress
import logging
import re
import smtplib
import threading
import time
import urllib.parse

from plugins.email import accounts, mailbox, message, search
from plugins.email.mailbox import Mailbox, MailError

logger = logging.getLogger(__name__)

PER_PAGE = 50
MAX_IDS = 200                     # per move / mark request
MAX_STATUS = 40                   # folders counted per listing
READ_WAIT = 8                     # seconds a list or a mail waits for the request in front of it
SENT_TOKENS: dict = {}            # a Send's one-time token -> {'at', 'state', 'done', 'note', 'error'}
_tokens_lock = threading.Lock()
SEND_WAIT = 50                    # seconds a repeated Send waits for the first one to end
TOKEN_KEEP = 900
MAX_TEXT = 500_000                # characters of body text sent to the page
MAX_HTML = 2_000_000              # characters of HTML sent to the page
MAX_INLINE = 6 * 1024 * 1024      # bytes of inline pictures embedded in the HTML
MAX_ATTACH = 20 * 1024 * 1024     # bytes of attachments carried by a forward
_ADDR_RE = re.compile(r'^[^@\s<>"]+@[^@\s<>"]+\.[^@\s<>"]+$')
_LABELS = {'inbox': 'Inbox', 'sent': 'Sent', 'drafts': 'Drafts', 'archive': 'Archive',
           'spam': 'Spam', 'trash': 'Trash'}
_MARKS = {'read': (b'\\Seen', True), 'unread': (b'\\Seen', False),
          'starred': (b'\\Flagged', True), 'unstarred': (b'\\Flagged', False)}
# What an HTML mail may not bring into the page. The frame it is shown in
# runs no script and loads nothing remote; this is the second wall.
_DROP_TAGS = ['script', 'head', 'title', 'meta', 'link', 'base', 'iframe', 'frame', 'frameset',
              'object', 'embed', 'applet', 'form', 'input', 'button', 'select', 'textarea', 'noscript']
_URL_ATTRS = ('src', 'href', 'background', 'poster', 'action', 'formaction', 'xlink:href')
_CSS_URL_RE = re.compile(r'''url\(\s*(["']?)\s*([^)"']*)''', re.I)
_REMOTE_RE = re.compile(r'''(?:src|background|poster|srcset)\s*=\s*["']?\s*(?:https?:)?//'''
                        r'''|url\(\s*["']?\s*(?:https?:)?//''', re.I)


# -- plumbing -------------------------------------------------------------------

def _inside(host):
    """True for a host name that means this machine or its own network. A
    mail's picture must never be fetched from there: the request would come
    from the person's own browser, to their own Sapphire or router."""
    host = str(host or '').strip('[]').lower()
    if not host or host == 'localhost' or host.endswith(('.localhost', '.local', '.lan', '.internal')):
        return True
    if '.' not in host and ':' not in host:
        return True                              # a bare name: something on the local network
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_reserved


def _refused(words, status=400):
    return {'success': False, 'detail': str(words)}, status


def _door(fn):
    """A handler whose every failure is words, never a traceback."""
    def run(**kw):
        try:
            return fn(**kw)
        except MailError as e:
            return _refused(e)
        except accounts.CertUntrusted as e:
            logger.warning(f"[EMAIL] page: {e.info.get('host')}:{e.info.get('port')} certificate not trusted "
                           f"({e.info.get('reason')})")
            return {'success': False, 'detail': str(e), 'cert': e.info}, 400
        except Exception as e:
            # accounts.py says why a CONNECTION failed (wrong name, port, password,
            # provider rule) and hangs that on the error. An error without it
            # happened later, on a line that was up: it is not a setup problem.
            words = getattr(e, 'words', '')
            if words:
                logger.error(f"[EMAIL] page: {fn.__name__}: {type(e).__name__}")
                return _refused(words)
            if isinstance(e, (OSError, imaplib.IMAP4.error, smtplib.SMTPException)):
                # The exception's text may be the mail server's own words: the log keeps it, the screen does not.
                logger.error(f"[EMAIL] page: {fn.__name__}: {type(e).__name__}: {e}")
                return _refused("The mail server stopped answering in the middle of that. Try again.", 502)
            logger.error(f"[EMAIL] page: {fn.__name__} failed: {e}", exc_info=True)
            return _refused(f"Email error: {e}", 500)             # a fault of our own: its text is ours
    run.__name__ = fn.__name__
    return run


def _open(scope):
    """(scope, creds) for an account the person picked, or MailError."""
    scope = str(scope or '').strip()
    if not scope:
        raise MailError("Pick an email account.")
    creds, why = accounts.get_creds(scope)
    if not creds:
        raise MailError(why or "Email not configured. Set it up in Settings > Plugins > Email.")
    return scope, creds


def _mail_index(settings):
    """The lists-as-last-seen file (index.py), or None when the person turned
    it off - what it held is dropped then. It is a convenience: no failure in
    it may ever break reading mail."""
    try:
        from plugins.email import index
        idx = index.get()
        if (settings or {}).get('keep_mail_index', True) in (False, 0, 'false', 'False', '0', 'off'):
            idx.enabled = False
            if not idx.empty():
                idx.forget()
                logger.info("[EMAIL] page: the mail index is off; what it held was deleted")
            return None
        idx.enabled = True
        return idx
    except Exception as e:
        logger.warning(f"[EMAIL] page: mail index unavailable: {e}")
        return None


def _kept(what, fn):
    try:
        return fn()
    except Exception as e:
        logger.warning(f"[EMAIL] page: mail index, {what}: {e}")
        return None


def _reading(scope, creds):
    """The kept login for a read. It waits only a few seconds for a request in
    front of it: the page asks again by itself, and a worker thread parked
    for long is one fewer for the rest of Sapphire."""
    mb = Mailbox(scope, creds, keep=True)
    mb.wait = READ_WAIT
    return mb


def _flag(value):
    if isinstance(value, str):
        return value.strip().lower() in ('1', 'true', 'yes', 'on')
    return bool(value)


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _ids(value):
    if value in (None, ''):
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    out = [str(x).strip() for x in items if str(x).strip()]
    if len(out) > MAX_IDS:
        raise MailError(f"At most {MAX_IDS} emails at a time.")
    return out


def _people(addrs):
    """ENVELOPE addresses as [{name, addr}]."""
    out = []
    for a in addrs or ():
        if a.mailbox and a.host:
            addr = f"{mailbox._dec(a.mailbox)}@{mailbox._dec(a.host)}"
            out.append({'name': message.display(mailbox._dec(a.name), addr), 'addr': addr})
    return out


def _pairs(pairs):
    return [{'name': message.display(n, a), 'addr': a} for n, a in pairs]


def _stamp(dt):
    """A server time as epoch seconds, or None."""
    try:
        return int(dt.timestamp())
    except Exception:
        return None


def _safe_name(name):
    name = re.sub(r'[\x00-\x1f\x7f/\\:*?"<>|]+', '_', str(name or '')).strip(' .')
    return name[-120:] or 'attachment'


# -- listing ----------------------------------------------------------------------

def _folders(mb, counts=True):
    """Every folder, roles first: key, label, role, and message counts."""
    roles, _ = mb.folders()
    out = []
    for n, key in enumerate(mb.folder_keys()):
        real = roles.get(key, key)
        row = {'key': key, 'label': _LABELS.get(key, key) if key in roles else key,
               'role': key if key in roles else '', 'total': None, 'unread': None}
        if counts and n < MAX_STATUS:
            try:
                got = mb.c.folder_status(real, ['MESSAGES', 'UNSEEN'])
                row['total'], row['unread'] = got.get(b'MESSAGES'), got.get(b'UNSEEN')
            except Exception as e:
                mb.swallowed(e)
                logger.debug(f"[EMAIL] page: no counts for {real}: {e}")
                if mb.broken:
                    raise
        out.append(row)
    return out


def _rows(mb, key, uids):
    """Listing rows for `uids` of the selected folder, in the order given."""
    if not uids:
        return []
    data = mb.listing(uids, extra=['INTERNALDATE'])
    rows = []
    for uid in uids:
        item = data.get(uid)
        env = item.get(b'ENVELOPE') if item else None
        if env is None:
            continue
        flags = tuple(f.lower() for f in item.get(b'FLAGS', ()) if isinstance(f, bytes))
        rows.append({
            'id': mb.id_for(key, uid),
            'from': _people(env.from_), 'to': _people(env.to),
            'subject': mailbox._dec(env.subject) or '(no subject)',
            'ts': _stamp(env.date) or _stamp(item.get(b'INTERNALDATE')),
            'unread': b'\\seen' not in flags,
            'starred': b'\\flagged' in flags,
            'answered': b'\\answered' in flags,
            'attach': mailbox._has_attachment(item.get(b'BODYSTRUCTURE')),
        })
    return rows


@_door
def folder_view(query=None, settings=None, **_):
    """GET folder?scope=&folder=&page=&q=&unread=&counts=&cached= - one
    folder's page of mail, newest first, with the folder list and its counts.

    cached=1 answers from the mail index without asking the server: the page
    as it was last seen ({'cached': true, 'as_of': when}), or {'miss': true}.
    The page shows that at once and then asks again without it."""
    q = query or {}
    scope, creds = _open(q.get('scope'))
    page = max(1, _int(q.get('page'), 1))
    text = ' '.join(str(q.get('q') or '').split())[:200]
    unread_only = _flag(q.get('unread'))
    idx = _mail_index(settings)
    plain = not text and not unread_only                # a search is never kept
    if _flag(q.get('cached')):
        wanted = str(q.get('folder') or 'inbox').strip()
        seen = _kept('reading', lambda: idx.view(scope, wanted, page, PER_PAGE, creds['address'])) if idx is not None and plain else None
        if seen is None:
            return {'cached': True, 'miss': True}
        return {'cached': True, 'as_of': seen['as_of'], 'account': {'scope': scope, 'address': creds['address']},
                'folder': wanted, 'folders': _kept('reading', lambda: idx.folders(scope)) or [],
                'rows': seen['rows'], 'total': seen['total'], 'page': page,
                'pages': max(1, -(-seen['total'] // PER_PAGE)), 'per_page': PER_PAGE,
                'validity': seen['validity'], 'q': '', 'unread_only': False}
    want_counts = q.get('counts') is None or _flag(q.get('counts'))
    with _reading(scope, creds) as mb:
        # No counts on this line: it keeps a folder selected between requests,
        # and STATUS belongs on a line that has none. Counts ride the second one.
        folders = _folders(mb, counts=False)
        if want_counts:
            with Mailbox(scope, creds, keep='counts') as counting:
                folders = _folders(counting, counts=True)
        if text:
            # The search box: the words of search.py. Everywhere when the page
            # asks (all=1) or the words say in:all; `in:sent` picks a folder.
            try:
                terms, place = search.where(search.parse(text))
            except ValueError as e:
                raise MailError(str(e))
            everywhere = place == 'all' or (place is None and _flag(q.get('all')))
            spot = place if place not in (None, 'all') else (q.get('folder') or 'inbox')
            if unread_only:
                terms.append((False, 'is', 'unread'))

            def make(found_in, uids):
                return [dict(r, folder=found_in) for r in _rows(mb, found_in, uids)]
            rows, total = mb.find(terms, spot, everywhere, PER_PAGE, (page - 1) * PER_PAGE,
                                  make=make, stamp=lambda r: r.get('ts') or 0)
            key = str(q.get('folder') or 'inbox').strip() if everywhere else mb.key_for(mb.resolve(spot))
            return {'account': {'scope': scope, 'address': creds['address']}, 'folder': key, 'folders': folders,
                    'rows': rows, 'total': total, 'page': 1 if everywhere else page,
                    'pages': 1 if everywhere else max(1, -(-total // PER_PAGE)), 'per_page': PER_PAGE,
                    # ids from several folders: no one folder's validity stands for them
                    'validity': 0 if everywhere else (mb.validity or 0),
                    'q': text, 'unread_only': unread_only, 'everywhere': everywhere}
        key = mb.key_for(mb.select(q.get('folder') or 'inbox'))
        if unread_only:
            found = sorted(mb.c.search(['UNSEEN']), reverse=True)
            total = len(found)
            uids = found[(page - 1) * PER_PAGE: page * PER_PAGE]
        else:
            # The newest 50 by position: no need to ask for every uid in a big folder.
            total = mb.exists or 0
            hi = total - (page - 1) * PER_PAGE
            uids = sorted(mb.c.search(f"{max(1, hi - PER_PAGE + 1)}:{hi}"), reverse=True) if hi >= 1 else []
        rows = _rows(mb, key, uids)
        validity = mb.validity or 0
    if idx is not None and plain:
        _kept('saving', lambda: idx.save_view(scope, key, validity, total, page, PER_PAGE, rows, creds['address']))
    return {'account': {'scope': scope, 'address': creds['address']}, 'folder': key, 'folders': folders,
            'rows': rows, 'total': total, 'page': page, 'pages': max(1, -(-total // PER_PAGE)),
            'per_page': PER_PAGE, 'validity': validity, 'q': text, 'unread_only': unread_only}


@_door
def folder_counts(query=None, settings=None, **_):
    """GET counts?scope= - the folder list with its message counts. One
    STATUS per folder is the slow part of a folder view, so the page asks for
    it apart, and it runs on a second kept login: a click never waits for it."""
    scope, creds = _open((query or {}).get('scope'))
    with Mailbox(scope, creds, keep='counts') as mb:
        folders = _folders(mb, counts=True)
    idx = _mail_index(settings)
    if idx is not None:
        _kept('saving', lambda: idx.save_folders(scope, folders))
        _kept('pruning', lambda: idx.prune(_account_scopes()))
    return {'scope': scope, 'folders': folders}


def _account_scopes():
    """Every email account that is set up, for dropping the kept lists of removed ones."""
    from core.credentials_manager import credentials
    return [a['scope'] for a in credentials.list_email_accounts()]


# -- reading ----------------------------------------------------------------------

def _own_host(request):
    try:
        return (request.url.hostname or '').lower()
    except Exception:
        return ''


def _css(text, own_host=''):
    """A style attribute or <style> body with every url() that is not an
    absolute outside address (or an embedded picture) pointed at nothing.
    The frame inherits the app's address as its base, so a relative url()
    would otherwise be a request to this Sapphire."""
    if '\\' in text:
        return ''                    # CSS escapes can spell url( in ways no pattern sees
    def fix(m):
        target = m.group(2).strip()
        low = target.lower()
        if low.startswith('data:image/') and not low.startswith('data:image/svg'):
            return m.group(0)
        if low.startswith(('http://', 'https://')):
            host = (urllib.parse.urlsplit(target).hostname or '').lower()
            if not own_host or host != own_host:
                return m.group(0)
        return f"url({m.group(1)}about:invalid"
    return _CSS_URL_RE.sub(fix, re.sub(r'image-set', 'x-image-set', text, flags=re.I))


def _page_html(msg, own_host=''):
    """(markup or None, has remote content) for the page's sandboxed frame.

    Scripts, forms, frames and event handlers are removed. Pictures attached
    to the mail itself (cid:) are embedded, so they show without a remote
    load. Any URL that is not absolute is dropped, and so is one naming this
    Sapphire: a mail must not be able to point the browser at the app."""
    part = message._body_part(msg, 'html')
    if part is None:
        return None, False
    raw = message._part_text(part)
    if len(raw) > MAX_HTML:
        return None, False
    from bs4 import BeautifulSoup, Comment
    soup = BeautifulSoup(raw, 'html.parser')
    for tag in soup(_DROP_TAGS):
        tag.decompose()
    # Comments are written back as they came, and a browser does not always
    # end a comment where this parser does: markup hidden in one could come
    # alive in the frame. A mail needs no comments.
    for note in soup.find_all(string=lambda t: isinstance(t, Comment)):
        note.extract()

    inline, budget = {}, MAX_INLINE
    for p in msg.walk():
        cid = str(p.get('Content-ID') or '').strip().strip('<>')
        ctype = p.get_content_type()
        if not cid or not ctype.startswith('image/') or ctype == 'image/svg+xml':
            continue
        data = p.get_payload(decode=True) or b''
        if data and len(data) <= budget:
            budget -= len(data)
            inline[cid] = f"data:{ctype};base64,{base64.b64encode(data).decode('ascii')}"

    for tag in soup.find_all('style'):
        css = _css(tag.get_text(), own_host)
        # Style text is not escaped on the way out: anything that looks like a
        # closing tag inside it would end the sheet early in a browser.
        tag.string = '' if '<' in css else css
    for tag in soup.find_all(True):
        for attr in list(tag.attrs):
            name = attr.lower()
            if name == 'style':
                tag.attrs[attr] = _css(str(tag.attrs[attr]), own_host)
                continue
            if name.startswith('on') or name in ('srcdoc', 'srcset', 'ping'):
                del tag.attrs[attr]
                continue
            if name not in _URL_ATTRS:
                continue
            value = str(tag.attrs[attr]).strip()
            low = value.lower()
            if low.startswith('cid:'):
                data = inline.get(value[4:])
                if data and tag.name == 'img':
                    tag.attrs[attr] = data
                else:
                    del tag.attrs[attr]
            elif low.startswith(('http://', 'https://')):
                host = (urllib.parse.urlsplit(value).hostname or '').lower()
                if (own_host and host == own_host) or _inside(host):
                    del tag.attrs[attr]
            elif low.startswith('data:image/') and tag.name == 'img' and not low.startswith('data:image/svg'):
                pass
            elif name == 'href' and low.startswith(('mailto:', 'tel:', '#')):
                pass
            else:
                del tag.attrs[attr]            # relative, //host, javascript:, data:text/html, ...
        if tag.name in ('a', 'area'):
            tag.attrs['target'] = '_blank'
            tag.attrs['rel'] = 'noopener noreferrer'
    markup = (soup.body or soup).decode_contents().strip()
    if not markup:
        return None, False
    return markup, bool(_REMOTE_RE.search(markup))


@_door
def get_message(query=None, request=None, **_):
    """GET message?scope=&id=&read=1 - one mail, for reading."""
    q = query or {}
    scope, creds = _open(q.get('scope'))
    handle = str(q.get('id') or '').strip()
    read = _flag(q.get('read'))
    with _reading(scope, creds) as mb:
        msg, flags = mb.message(handle, mark_read=read)
    plain = message._body_part(msg, 'plain')
    has_plain = plain is not None and bool(message._part_text(plain).strip())
    text = message.body_text(msg)
    markup, remote = _page_html(msg, _own_host(request))
    files = message.attachments(msg)
    to_one, _cc = message.reply_targets(msg, creds['address'], False)
    to_all, cc_all = message.reply_targets(msg, creds['address'], True)
    when = None
    try:
        when = int(email.utils.parsedate_to_datetime(message.header(msg, 'Date')).timestamp())
    except Exception:
        pass
    return {
        'id': handle, 'folder': mailbox.split_id(handle)[0],
        'subject': message.header(msg, 'Subject', '(no subject)') or '(no subject)',
        'from': _pairs(message.addresses(msg, 'From')),
        'to': _pairs(message.addresses(msg, 'To')),
        'cc': _pairs(message.addresses(msg, 'Cc')),
        'ts': when, 'date': message.header(msg, 'Date'),
        'unread': b'\\seen' not in flags and not read,
        'starred': b'\\flagged' in flags, 'answered': b'\\answered' in flags,
        'text': text[:MAX_TEXT], 'text_cut': len(text) > MAX_TEXT, 'has_plain': has_plain,
        'html': markup, 'remote': remote,
        'attachments': [{'n': i, 'name': _safe_name(f['name']), 'type': f['type'], 'size': len(f['data'])}
                        for i, f in enumerate(files, 1)],
        'reply': {'to': _pairs(to_one), 'to_all': _pairs(to_all), 'cc_all': _pairs(cc_all)},
        'automated': message.is_automated(msg),
    }


@_door
def get_attachment(query=None, **_):
    """GET attachment?scope=&id=&n= - one attachment, as a download.
    Always a download: never a type the browser would open in the app."""
    q = query or {}
    scope, creds = _open(q.get('scope'))
    handle = str(q.get('id') or '').strip()
    with _reading(scope, creds) as mb:
        msg, _flags = mb.message(handle)
    files = message.attachments(msg)
    n = _int(q.get('n'))
    if n < 1 or n > len(files):
        raise MailError(f"That email has no attachment {q.get('n')}." if files else "That email has no attachments.")
    f = files[n - 1]
    name = _safe_name(f['name'])
    plain = name.encode('ascii', 'replace').decode('ascii').replace('?', '_')
    from starlette.responses import Response
    return Response(content=f['data'], media_type='application/octet-stream', headers={
        'Content-Disposition': f"attachment; filename=\"{plain}\"; filename*=UTF-8''{urllib.parse.quote(name)}",
        'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store'})


# -- changing ---------------------------------------------------------------------

@_door
def move(body=None, **_):
    """POST move {scope, ids, to} - to a role (archive, spam, trash,
    inbox...) or a folder name. Trash and spam are folders: nothing here
    deletes for good."""
    b = body or {}
    scope, creds = _open(b.get('scope'))
    ids, dest = _ids(b.get('ids')), str(b.get('to') or '').strip()
    if not ids or not dest:
        raise MailError("Pick at least one email and where to move it.")
    with Mailbox(scope, creds, keep=True) as mb:
        moved, skipped = mb.move(ids, dest, create='Archive' if dest.lower() == 'archive' else None)
    return {'success': bool(moved) or not skipped, 'moved': [h for h, _s in moved],
            'skipped': [{'id': h, 'why': why} for h, why in skipped]}


@_door
def mark(body=None, **_):
    """POST mark {scope, ids, mark} - read, unread, starred, unstarred."""
    b = body or {}
    scope, creds = _open(b.get('scope'))
    ids, what = _ids(b.get('ids')), str(b.get('mark') or '').strip().lower()
    if not ids:
        raise MailError("Pick at least one email.")
    if what not in _MARKS:
        raise MailError(f"mark must be one of: {', '.join(_MARKS)}.")
    flag, on = _MARKS[what]
    with Mailbox(scope, creds, keep=True) as mb:
        changed, skipped = mb.flag(ids, flag, on)
    return {'success': bool(changed) or not skipped, 'changed': changed,
            'skipped': [{'id': h, 'why': why} for h, why in skipped]}


# -- sending ----------------------------------------------------------------------

def _addresses(value, label):
    """'Ann <a@x.com>, b@y.org' (or a list of those) -> [(name, addr)].

    Each comma-separated piece must be one address. A piece that is not is
    refused by name: the parser alone would drop a typo without a word, and
    the mail would go out to fewer people than the person typed."""
    items = value if isinstance(value, (list, tuple)) else [value]
    out, seen = [], set()
    for item in items:
        text = str(item or '').replace('\r', ' ').replace('\n', ' ').replace(';', ',')
        for piece in re.findall(r'(?:[^,"]|"[^"]*")+', text):
            piece = piece.strip()
            if not piece:
                continue
            found = email.utils.getaddresses([piece])
            name, addr = found[0] if len(found) == 1 else ('', '')
            if not _ADDR_RE.match(addr or ''):
                raise MailError(f"{label}: '{piece}' is not an email address.")
            if addr.lower() not in seen:
                seen.add(addr.lower())
                out.append((name.strip(), addr))
    return out


def _without(pairs, taken):
    have = {a.lower() for _n, a in taken}
    return [(n, a) for n, a in pairs if a.lower() not in have]


@_door
def send(body=None, **_):
    """POST send {scope, mode, id, to, cc, bcc, subject, body, draft}

    mode: new | reply | forward. A reply or forward names the mail (`id`) it
    answers; the recipients are the ones in the form, which the page fills in
    and the person may change. Built by message.build and sent by
    mailbox.send, the same path her tools use."""
    b = body or {}
    scope, creds = _open(b.get('scope'))
    mode = str(b.get('mode') or 'new').strip().lower()
    if mode == 'reply_all':
        mode = 'reply'
    if mode not in ('new', 'reply', 'forward'):
        raise MailError("mode must be new, reply or forward.")
    draft = _flag(b.get('draft'))
    to = _addresses(b.get('to'), 'To')
    cc = _without(_addresses(b.get('cc'), 'Cc'), to)
    bcc = _without(_addresses(b.get('bcc'), 'Bcc'), to + cc)
    subject = ' '.join(str(b.get('subject') or '').split())[:500]
    text = str(b.get('body') or '').replace('\r\n', '\n').replace('\r', '\n')
    if not to and not draft:
        raise MailError("Add at least one address in To.")

    handle, original, files, left_out = str(b.get('id') or '').strip(), None, [], ''
    if mode != 'new':
        if not handle:
            raise MailError("Which email is this a reply to? Open it and try again.")
        with Mailbox(scope, creds, keep=True) as mb:
            original, _flags = mb.message(handle)
        if mode == 'forward':
            files = message.attachments(original)
            if sum(len(f['data']) for f in files) > MAX_ATTACH:
                files, left_out = [], f"Its attachments are over {MAX_ATTACH // 1048576} MB and were left out."
        subject = subject or message.prefixed(message.header(original, 'Subject', '(no subject)'),
                                               'Fwd' if mode == 'forward' else 'Re')
    elif not draft:
        if not subject:
            raise MailError("Give the email a subject.")
    if mode != 'forward' and not draft and not text.strip():
        raise MailError("The email is empty. Write something first.")

    msg = message.build(creds['address'], to, cc, subject, text, original,
                        None if mode == 'new' else mode, files)
    if draft:
        if bcc:              # a draft is a file, not a delivery: its Bcc line is how the mail program knows
            msg['Bcc'] = ', '.join(email.utils.formataddr(p) for p in bcc)
        with Mailbox(scope, creds, keep=True) as mb:
            mb.append('drafts', msg, flags=(b'\\Draft', b'\\Seen'))
        return {'success': True, 'draft': True, 'note': left_out}

    # One mail per Send. The browser gives up waiting long before a slow
    # upload does, and the person then presses Send again. The token is the
    # same, so the second press never sends: it waits for the first one and
    # reports how THAT ended. Telling it "already sent" while the first was
    # still going closed the dialog on a mail that then failed.
    token = str(b.get('token') or '')[:80]
    entry = None
    if token:
        now = time.time()
        with _tokens_lock:
            for old in [t for t, e in SENT_TOKENS.items() if now - e['at'] > TOKEN_KEEP]:
                SENT_TOKENS.pop(old, None)
            earlier = SENT_TOKENS.get(token)
            if earlier is None:
                entry = SENT_TOKENS[token] = {'at': now, 'state': 'sending', 'done': threading.Event(), 'note': '', 'error': ''}
        if earlier is not None:
            earlier['done'].wait(SEND_WAIT)
            if earlier['state'] == 'sent':
                return {'success': True, 'draft': False, 'duplicate': True,
                        'note': ' '.join(x for x in ('This email was already sent. It was not sent again.', earlier['note']) if x)}
            if earlier['state'] == 'sending':
                return _refused("That email is still going out. Give it a moment, then press Send again to see how it ended.", 409)
            raise MailError(earlier['error'] or "That didn't send. Try again.")

    def ended(state, words=''):
        """How this Send ended, for a repeated press that is waiting on it."""
        if entry is None:
            return
        with _tokens_lock:
            entry['state'] = state
            entry['note' if state == 'sent' else 'error'] = words
            if state == 'failed':
                SENT_TOKENS.pop(token, None)         # nothing went out: the same Send may be tried again
        entry['done'].set()

    def answered():
        if mode == 'reply':
            with Mailbox(scope, creds, keep=True) as mb:
                mb.flag([handle], b'\\Answered')
    try:
        note = mailbox.send(scope, creds, msg, [a for _n, a in to + cc + bcc], after=answered, wait=False,
                            name_refused=True)
    except smtplib.SMTPRecipientsRefused as e:
        words = f"The mail server refused the address: {', '.join(e.recipients) or 'every recipient'}. Nothing was sent."
        ended('failed', words)
        raise MailError(words)
    except smtplib.SMTPResponseException as e:
        logger.error(f"[EMAIL] page: send: {type(e).__name__} {e.smtp_code}")
        words = getattr(e, 'words', '') or (
            accounts.explain(e, creds, sending=True) if isinstance(e, smtplib.SMTPAuthenticationError) else
            f"The mail server did not take the email (code {e.smtp_code}"
            + (": it is too big for this server" if e.smtp_code == 552 else '') + "). Nothing was sent.")
        ended('failed', words)
        raise MailError(words)
    except Exception as e:
        if getattr(e, 'unclear', False):
            logger.error(f"[EMAIL] page: send: the line failed mid-send ({type(e).__name__})")
            words = ("The connection dropped while the email was going out, so it is not certain whether it was "
                     "sent. Look in the Sent folder (or ask the recipient) before sending it again.")
            ended('unclear', words)                  # the token stays: pressing Send again must not send twice
            raise MailError(words)
        ended('failed', getattr(e, 'words', '') or (str(e) if isinstance(e, MailError) else "That didn't send. Try again."))
        raise
    clean = ' '.join(x for x in (note.strip(' ()'), left_out) if x)
    ended('sent', clean)
    logger.info(f"[EMAIL] page: sent ({mode}) from '{scope}' to {len(to + cc + bcc)} recipient(s)")
    return {'success': True, 'draft': False, 'note': clean}


# -- setting an account up: the Test button and certificates ------------------------

TEST_TIMEOUT = 12
_FIELDS = ('address', 'app_password', 'imap_server', 'imap_port', 'smtp_server', 'smtp_port')
_SAME_SERVER = (('imap_server', 'imap_port', 993), ('smtp_server', 'smtp_port', 465))


def _port(value, default, what):
    try:
        port = int(value) if str(value if value is not None else '').strip() else default
    except (TypeError, ValueError, OverflowError):
        raise MailError(f"The {what} port is a number: 993 for reading mail, 465 or 587 for sending.")
    if not 1 <= port <= 65535:
        raise MailError(f"{port} is not a port. Reading mail is 993; sending is 465 or 587.")
    return port


def _server(value, what):
    """A server name as typed, tidied; MailError when it cannot be one."""
    text = str(value or '').strip()
    text = re.sub(r'^[a-z][a-z0-9+.-]*://', '', text, flags=re.I).rstrip('/').strip('[]')
    if '@' in text:
        raise MailError(f"'{text}' is an email address, not a server name. The {what} server looks like mail.example.com.")
    if not text or re.search(r'[\s/]', text) or accounts._CONTROL_HOST.search(text):
        raise MailError(f"Fill in the {what} server first. It looks like mail.example.com.")
    return text


def _typed_account(b):
    """(scope, creds, stored) from what is in the form. A stored password (or
    Microsoft sign-in) is used ONLY with the servers it was stored for: a
    typed server name gets a typed password, or nothing. Otherwise anything
    able to post here could have the stored secret sent to a server of its
    choosing, and it would keep that password for good."""
    scope = str(b.get('scope') or '').strip()
    from core.credentials_manager import credentials
    stored = dict(credentials.get_email_account(scope) or {}) if scope else {}
    typed = {k: b[k] for k in _FIELDS if str(b.get(k) if b.get(k) is not None else '').strip()}
    creds = dict(stored)
    creds.update({k: (str(v).strip() if isinstance(v, str) else v) for k, v in typed.items()})
    missing = [label for key, label in (('address', 'the email address'), ('imap_server', 'the IMAP server'),
                                         ('smtp_server', 'the SMTP server')) if not str(creds.get(key) or '').strip()]
    if missing:
        raise MailError(f"Fill in {', '.join(missing)} first.")
    creds['imap_server'], creds['smtp_server'] = _server(creds['imap_server'], 'IMAP'), _server(creds['smtp_server'], 'SMTP')
    creds['imap_port'], creds['smtp_port'] = _port(creds.get('imap_port'), 993, 'IMAP'), _port(creds.get('smtp_port'), 465, 'SMTP')

    def same(key):
        return str(creds.get(key) or '').strip().lower() == str(stored.get(key) or '').strip().lower()
    as_stored = same('address') and all(
        same(host) and creds[port] == _port(stored.get(port), default, 'stored') for host, port, default in _SAME_SERVER)
    if typed.get('app_password'):
        creds['auth_type'] = 'password'
        for key in [k for k in creds if k.startswith('oauth_')]:
            creds.pop(key)
    elif not as_stored:
        if stored.get('app_password') or stored.get('auth_type') == 'oauth2':
            raise MailError("Type the password to test a changed address or server. The one that is stored is only "
                            "ever sent to the server it was stored for.")
        raise MailError("Fill in the password first.")
    elif stored.get('auth_type') == 'oauth2':
        fresh, why = accounts.get_creds(scope)             # a current token, refreshed if it ran out
        if not fresh:
            raise MailError(why)
        creds = dict(fresh)
    elif not creds.get('app_password'):
        raise MailError("Fill in the password first.")
    return scope, creds, as_stored


def _plain(error, creds, sending):
    """A failed step as words for the screen. Never the exception's own text:
    that can be whatever the server on the other end chose to send."""
    words = getattr(error, 'words', '') or accounts.explain(error, creds, sending)
    return words or f"That did not work ({type(error).__name__}). The details are in Sapphire's log."


def _daemon_again():
    """A watched account that had failed is tried at its next poll, not after its back-off."""
    try:
        from plugins.email import daemon
        daemon.retry_now()
    except Exception as e:
        logger.debug(f"[EMAIL] daemon not nudged: {e}")


@_door
def check_account(body=None, **_):
    """POST test: log in for reading AND for sending with what is in the
    form, the way the plugin itself connects (certificates checked). No mail
    is sent. Each half answers for itself: reading can work while sending is
    set up wrong, and that used to show only at the first send.

    A person pressing Test is not held back by the refused-login hold, but a
    refusal here is recorded like any other, so the daemon and her tools
    stop sending a password the server just refused."""
    scope, creds, as_stored = _typed_account(body or {})
    out = {'success': True, 'certs': []}

    def half(name, sending, work):
        try:
            out[name] = {'ok': True, **(work() or {})}
        except accounts.CertUntrusted as e:
            out[name] = {'ok': False, 'words': str(e)}
            out['certs'].append(e.info)
        except Exception as e:
            logger.info(f"[EMAIL] test, {name}: {type(e).__name__}: {e}")
            out[name] = {'ok': False, 'words': _plain(e, creds, sending)}

    def reading():
        client = accounts.connect(scope or '(new account)', creds, TEST_TIMEOUT, force=True)
        try:
            got = client.select_folder('INBOX', readonly=True)
            return {'count': int(got.get(b'EXISTS') or 0)}
        finally:
            try:
                client.logout()
            except Exception:
                pass

    def sending():
        smtp = accounts.smtp_connect(creds, TEST_TIMEOUT, force=True)
        try:
            smtp.quit()
        except Exception:
            pass

    half('reading', False, reading)
    half('sending', True, sending)
    out['works'] = out['reading']['ok'] and out['sending']['ok']
    if out['works'] and scope and as_stored:
        mailbox.close_kept(scope)
        _daemon_again()
    return out


@_door
def certs(query=None, **_):
    """GET certs: the certificates the person has trusted. With ?scope=, also
    where that account's two servers stand right now (no login is made)."""
    out = {'success': True, 'trusted': accounts.trusted_list()}
    scope = str((query or {}).get('scope') or '').strip()
    if scope:
        from core.credentials_manager import credentials
        creds = credentials.get_email_account(scope) or {}
        out['servers'] = []
        for which, host, port, default in (('imap', creds.get('imap_server'), creds.get('imap_port'), 993),
                                           ('smtp', creds.get('smtp_server'), creds.get('smtp_port'), 465)):
            if not str(host or '').strip():
                continue
            try:
                port = _port(port, default, which.upper())
                out['servers'].append(accounts.cert_status(host, port, which))
            except MailError as e:
                out['servers'].append({'host': host, 'port': 0, 'which': which, 'state': 'unreachable', 'words': str(e)})
            except Exception as e:
                logger.info(f"[EMAIL] certs, {which}: {type(e).__name__}: {e}")
                out['servers'].append({'host': host, 'port': port, 'which': which, 'state': 'unreachable',
                                       'words': _plain(e, creds, which == 'smtp')})
    return out


@_door
def trust_cert(body=None, **_):
    """POST trust: {host, port, which, fingerprint}. The server is asked for
    its certificate again here, and it is trusted only if it is the one the
    person was shown: what they agreed to is that fingerprint, nothing else.
    Never for a well-known provider: its certificate always verifies, so one
    that does not is somebody in between."""
    b = body or {}
    which = 'smtp' if b.get('which') == 'smtp' else 'imap'
    shown = re.sub(r'[^0-9a-f]', '', str(b.get('fingerprint') or '').lower())
    if not str(b.get('host') or '').strip() or len(shown) != 64:
        raise MailError("Which certificate? Run the test again.")
    host, port = _server(b.get('host'), which.upper()), _port(b.get('port'), 0, which.upper())
    if accounts.well_known(host):
        raise MailError(f"{host} is a well-known mail provider, and its certificate always checks out. One that does "
                        "not means the connection is being intercepted, so it cannot be trusted here.")
    try:
        now = accounts.cert_status(host, port, which)
    except Exception as e:
        logger.info(f"[EMAIL] trust: {type(e).__name__}: {e}")
        raise MailError(_plain(e, {'imap_server': host, 'imap_port': port, 'smtp_server': host, 'smtp_port': port},
                               which == 'smtp'))
    if now.get('state') == 'verified':
        accounts.untrust(host, port)
        return {'success': True, 'note': f"{host} has a certificate that checks out now. Nothing needs trusting."}
    if now.get('fingerprint') != shown:
        raise MailError(f"{host} is showing a different certificate from the one on your screen. "
                        "Nothing was trusted. Run the test again and look at the new one.")
    accounts.trust(host, port, shown)
    mailbox.close_kept()
    _daemon_again()
    return {'success': True}


@_door
def untrust_cert(body=None, **_):
    """POST untrust: {host, port}. The next connection checks that server the normal way again."""
    b = body or {}
    accounts.untrust(str(b.get('host') or '').strip(), _port(b.get('port'), 0, 'server'))
    mailbox.close_kept()
    return {'success': True, 'trusted': accounts.trusted_list()}
