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
import logging
import re
import smtplib
import time
import urllib.parse

from plugins.email import accounts, mailbox, message, search
from plugins.email.mailbox import Mailbox, MailError

logger = logging.getLogger(__name__)

PER_PAGE = 50
MAX_IDS = 200                     # per move / mark request
MAX_STATUS = 40                   # folders counted per listing
READ_WAIT = 8                     # seconds a list or a mail waits for the request in front of it
SENT_TOKENS: dict = {}            # a Send's one-time token -> when; a mail goes out once per token
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

def _refused(words, status=400):
    return {'success': False, 'detail': str(words)}, status


def _door(fn):
    """A handler whose every failure is words, never a traceback."""
    def run(**kw):
        try:
            return fn(**kw)
        except MailError as e:
            return _refused(e)
        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"[EMAIL] page: SMTP login refused: {e}")
            return _refused("The mail server refused the login for sending. Check the account's "
                            "password in Settings > Plugins > Email.")
        except Exception as e:
            if type(e).__name__ == 'LoginError':
                logger.error(f"[EMAIL] page: IMAP login refused: {e}")
                return _refused("The mail server refused the login. Check the account's password "
                                "in Settings > Plugins > Email.")
            logger.error(f"[EMAIL] page: {fn.__name__} failed: {e}", exc_info=True)
            return _refused(f"Email error: {e}", 500)
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
            if not idx.empty():
                idx.forget()
                logger.info("[EMAIL] page: the mail index is off; what it held was deleted")
            return None
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
                if own_host and host == own_host:
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
    # upload does, and the person then presses Send again: the token is the
    # same, so the second press is answered without sending.
    token = str(b.get('token') or '')[:80]
    now = time.time()
    for old in [t for t, when in SENT_TOKENS.items() if now - when > TOKEN_KEEP]:
        SENT_TOKENS.pop(old, None)
    if token:
        if token in SENT_TOKENS:
            return {'success': True, 'draft': False, 'duplicate': True,
                    'note': 'This email was already sent (or is on its way). It was not sent again.'}
        SENT_TOKENS[token] = now

    def answered():
        if mode == 'reply':
            with Mailbox(scope, creds, keep=True) as mb:
                mb.flag([handle], b'\\Answered')
    try:
        note = mailbox.send(scope, creds, msg, [a for _n, a in to + cc + bcc], after=answered, wait=False)
    except smtplib.SMTPRecipientsRefused as e:
        SENT_TOKENS.pop(token, None)                 # nothing went out: the same Send may be tried again
        raise MailError(f"The mail server refused the address: {', '.join(e.recipients) or e}")
    except Exception:
        SENT_TOKENS.pop(token, None)
        raise
    logger.info(f"[EMAIL] page: sent ({mode}) from '{scope}' to {len(to + cc + bcc)} recipient(s)")
    return {'success': True, 'draft': False, 'note': ' '.join(x for x in (note.strip(' ()'), left_out) if x)}
