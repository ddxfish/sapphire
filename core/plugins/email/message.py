# core/plugins/email/message.py - reading a message, and writing one
"""Parse received mail and build outgoing mail.

Every message Sapphire sends is built by `build()`: a new mail, a reply, a
reply-all, a forward, a draft and the daemon's auto-reply. Replies carry a
plain part with `>` quotes AND an HTML part with a real blockquote, which is
what mail clients fold away ("..."); plain `>` lines alone never fold.
"""
import email
import email.policy
import email.utils
import html
import mimetypes
import re
from email.message import EmailMessage

MAX_QUOTE_HTML = 200_000   # past this the quote falls back to plain text
_QUOTE_STYLE = 'margin:0 0 0 .8ex;border-left:1px #ccc solid;padding-left:1ex'
_NO_REPLY = re.compile(r'^(mailer-daemon|postmaster|no-?reply|do-?not-?reply|bounces?|microsoftexchange[0-9a-f]*)'
                       r'([+\-._].*)?$', re.I)
_CONTROL = re.compile(r'[\x00-\x1f\x7f]+')


# -- reading ------------------------------------------------------------------

def parse(raw: bytes) -> EmailMessage:
    return email.message_from_bytes(raw, policy=email.policy.default)


def header(msg, name, default='') -> str:
    """One header as text. A malformed header never raises."""
    try:
        v = msg.get(name)
    except Exception:
        return default
    return default if v is None else str(v).replace('\r', ' ').replace('\n', ' ').strip()


def addresses(msg, *names) -> list:
    """[(name, addr), ...] from the named address headers, in order."""
    out = []
    for n in names:
        try:
            values = [str(v) for v in (msg.get_all(n) or [])]
        except Exception:
            continue
        for value in values:
            # One header value at a time: a newer Python's strict parser throws
            # away EVERY address when one value (an empty group) does not add up.
            out += [(_CONTROL.sub(' ', nm).strip(), ad.strip()) for nm, ad in email.utils.getaddresses([value])
                    if ad and '@' in ad]
    return out


def display(name, addr) -> str:
    """What Sapphire sees for a person: the name, else the part before the @.
    Never the full address."""
    name = (name or '').strip().strip('"')
    if name and '@' not in name:
        return name
    return (addr or '').split('@')[0] or 'Unknown'


def names(pairs) -> str:
    return ', '.join(display(n, a) for n, a in pairs)


def _part_text(part) -> str:
    """A text part, decoded, with plain \n line ends (mail arrives as CRLF)."""
    try:
        text = part.get_content()
    except Exception:
        payload = part.get_payload(decode=True) or b''
        text = payload.decode(part.get_content_charset() or 'utf-8', errors='replace')
    return str(text).replace('\r\n', '\n').replace('\r', '\n')


def _body_part(msg, kind):
    try:
        return msg.get_body(preferencelist=(kind,))
    except Exception:
        for part in msg.walk():
            if part.get_content_type() == f'text/{kind}' and part.get_content_disposition() != 'attachment':
                return part
    return None


def html_to_text(markup: str) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(markup or '', 'html.parser')
    for tag in soup(['script', 'style', 'head', 'title', 'meta', 'link']):
        tag.decompose()
    for a in soup.find_all('a', href=True):
        href = a['href'].strip()
        if href.startswith('http') and len(href) <= 300 and href not in a.get_text():
            a.append(f' ({href})')
    for br in soup.find_all('br'):
        br.replace_with('\n')
    for block in soup.find_all(['p', 'div', 'tr', 'li', 'h1', 'h2', 'h3', 'h4', 'blockquote']):
        block.append('\n')
    lines = [ln.strip() for ln in soup.get_text().replace('\xa0', ' ').splitlines()]
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


def body_text(msg) -> str:
    """The message as plain text: the text part, else the HTML part made text."""
    part = _body_part(msg, 'plain')
    if part is not None:
        text = _part_text(part).strip()
        if text:
            return text
    part = _body_part(msg, 'html')
    if part is not None:
        return html_to_text(_part_text(part))
    return ''


def body_html(msg):
    """The HTML part's body, scripts and head removed, or None."""
    part = _body_part(msg, 'html')
    if part is None:
        return None
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(_part_text(part), 'html.parser')
    for tag in soup(['script', 'head', 'title', 'meta', 'link', 'base']):
        tag.decompose()
    return (soup.body or soup).decode_contents().strip() or None


def attachments(msg) -> list:
    """[{'name', 'type', 'data'}, ...]: every part that is not the body text.
    Inline pictures count: a person sees those too."""
    out = []
    for part in _leaves(msg):
        fn = part.get_filename()
        ctype = part.get_content_type()
        if ctype == 'message/rfc822':
            # A mail attached to this mail ("forward as attachment", an abuse
            # report). One attachment, never taken apart: its own body and
            # files belong to it, not to the mail that carries it.
            try:
                inner = part.get_payload()[0]
                data = inner.as_bytes()
                fn = fn or (_CONTROL.sub(' ', str(inner.get('Subject') or 'attached email')).strip()[:80] + '.eml')
            except Exception:
                continue
            out.append({'name': str(fn), 'type': ctype, 'data': data})
            continue
        # The plain and HTML bodies are not attachments. A calendar invite
        # (text/calendar, usually nameless) is: she can open it.
        if not fn and part.get_content_disposition() != 'attachment' \
                and ctype in ('text/plain', 'text/html'):
            continue
        data = part.get_payload(decode=True) or b''
        if not data:
            continue
        if not fn:
            fn = f"attachment-{len(out) + 1}{mimetypes.guess_extension(ctype) or ''}"
        out.append({'name': str(fn), 'type': ctype, 'data': data})
    return out


def _leaves(msg):
    """The parts of a mail that carry content. Unlike walk(), a mail attached
    to the mail (message/rfc822) is one leaf and is not entered. A part that
    cannot be read is skipped, never the whole mail."""
    try:
        if msg.get_content_type() == 'message/rfc822' or not msg.is_multipart():
            yield msg
            return
        subs = list(msg.iter_parts())
    except Exception:
        return
    for sub in subs:
        yield from _leaves(sub)


def is_automated(msg) -> str:
    """Why this mail must never get an automatic reply, or '' (RFC 3834).
    Bulk mail, lists, bounces and other autoresponders: answering them is how
    two machines end up mailing each other forever."""
    auto = header(msg, 'Auto-Submitted').lower()
    if auto and auto != 'no':
        return f"Auto-Submitted: {auto}"
    prec = header(msg, 'Precedence').lower()
    if prec in ('bulk', 'junk', 'list', 'auto_reply'):
        return f"Precedence: {prec}"
    for h in ('List-Id', 'List-Unsubscribe', 'X-Auto-Response-Suppress', 'X-Autoreply', 'X-Autorespond',
              'X-Loop', 'X-Failed-Recipients'):
        if header(msg, h):
            return h
    try:
        if msg.get_content_type() == 'multipart/report':
            return "a delivery report"
    except Exception:
        pass
    if header(msg, 'Return-Path').strip() == '<>':
        return "empty Return-Path (a bounce)"
    for _, addr in addresses(msg, 'From'):
        if _NO_REPLY.match(addr.split('@')[0]):
            return "a no-reply sender"
    return ''


def when(msg) -> str:
    raw = header(msg, 'Date')
    try:
        return email.utils.parsedate_to_datetime(raw).strftime('%a, %b %d, %Y at %H:%M')
    except Exception:
        return raw


def reply_targets(original, own_addr, reply_all=False):
    """(to, cc) as [(name, addr)] for a reply. Reply-To wins over From; a
    reply to our own sent mail goes to the people it was sent to. Reply-all
    adds everyone else on To and Cc, never ourselves, never twice."""
    own = (own_addr or '').lower()
    to = addresses(original, 'Reply-To') or addresses(original, 'From')
    if to and all(a.lower() == own for _, a in to):
        to = addresses(original, 'To')
    cc = []
    if reply_all:
        seen = {own} | {a.lower() for _, a in to}
        for n, a in addresses(original, 'To', 'Cc'):
            if a.lower() not in seen:
                seen.add(a.lower())
                cc.append((n, a))
    return to, cc


def prefixed(subject, prefix) -> str:
    """'Re: x' / 'Fwd: x', never doubled."""
    subject = (subject or '').strip()
    return subject if subject.lower().startswith(prefix.lower() + ':') else f"{prefix}: {subject}"


# -- writing ------------------------------------------------------------------

def _text_to_html(text) -> str:
    return html.escape(text or '').replace('\n', '<br>\n')


def _quote(original):
    """(plain, html) of the original under a reply."""
    who = display(*(addresses(original, 'From') or [('', '')])[0])
    line = f"On {when(original)}, {who} wrote:"
    text = body_text(original)
    plain = f"\n\n{line}\n" + '\n'.join('> ' + ln if ln else '>' for ln in text.splitlines())
    inner = body_html(original)
    if not inner or len(inner) > MAX_QUOTE_HTML:
        inner = _text_to_html(text)
    markup = (f'<br><div class="gmail_quote"><div dir="ltr" class="gmail_attr">{html.escape(line)}<br></div>'
              f'<blockquote class="gmail_quote" style="{_QUOTE_STYLE}">{inner}</blockquote></div>')
    return plain, markup


def _forwarded(original):
    """(plain, html) of the original under a forward. Names, not addresses:
    a forward can go to someone who was never on the thread."""
    head = ["---------- Forwarded message ---------",
            f"From: {names(addresses(original, 'From')) or 'Unknown'}",
            f"Date: {when(original)}",
            f"Subject: {header(original, 'Subject', '(no subject)')}"]
    to = names(addresses(original, 'To'))
    if to:
        head.append(f"To: {to}")
    text = body_text(original)
    plain = '\n'.join(head) + '\n\n' + text
    inner = body_html(original)
    if not inner or len(inner) > MAX_QUOTE_HTML:
        inner = _text_to_html(text)
    markup = ('<div class="gmail_quote"><div dir="ltr" class="gmail_attr">'
              + '<br>'.join(html.escape(h) for h in head) + '</div><br>' + inner + '</div>')
    return plain, markup


def build(sender, to, cc=(), subject='', text='', original=None, mode=None,
          files=(), auto=False) -> EmailMessage:
    """One outgoing message.

    to / cc: [(name, addr)]. Bcc never appears in the message: the caller
    hands those addresses to SMTP only.
    mode: None (new mail), 'reply' or 'forward', both with `original`.
    files: [{'name', 'type', 'data'}]. auto=True marks a machine reply.
    """
    msg = EmailMessage(policy=email.policy.SMTP)
    msg['From'] = sender
    msg['To'] = ', '.join(email.utils.formataddr((n if n != a else '', a)) for n, a in to)
    if cc:
        msg['Cc'] = ', '.join(email.utils.formataddr((n if n != a else '', a)) for n, a in cc)
    msg['Subject'] = subject or ''
    msg['Date'] = email.utils.formatdate(localtime=True)
    msg['Message-ID'] = email.utils.make_msgid(domain=sender.split('@')[-1] or None)
    if auto:
        msg['Auto-Submitted'] = 'auto-replied'
        msg['X-Loop'] = sender

    plain, markup = text or '', f'<div dir="ltr">{_text_to_html(text)}</div>'
    if mode == 'reply' and original is not None:
        orig_id = header(original, 'Message-ID')
        if orig_id:
            msg['In-Reply-To'] = orig_id
            msg['References'] = f"{header(original, 'References')} {orig_id}".strip()
        q_plain, q_html = _quote(original)
        plain, markup = plain + q_plain, markup + q_html
    elif mode == 'forward' and original is not None:
        f_plain, f_html = _forwarded(original)
        plain = f"{plain}\n\n{f_plain}" if plain else f_plain
        markup = markup + '<br>' + f_html

    msg.set_content(plain)
    msg.add_alternative(markup, subtype='html')
    for f in files:
        # A line break in a file name would be refused as a header and make the mail unsendable.
        name = _CONTROL.sub('_', str(f.get('name') or 'attachment')).strip() or 'attachment'
        maintype, _, subtype = (f.get('type') or 'application/octet-stream').partition('/')
        if maintype == 'message':
            try:
                msg.add_attachment(parse(f['data']), filename=name)      # an attached mail stays a mail
                continue
            except Exception:
                maintype, subtype = 'application', 'octet-stream'
        msg.add_attachment(f['data'], maintype=maintype, subtype=subtype or 'octet-stream', filename=name)
    return msg
