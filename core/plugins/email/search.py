# core/plugins/email/search.py - one search language for the page and her tool
"""
What a person types in a mail program's search box, and what her
search_emails tool takes as `query`:

    invoice                      words anywhere in the mail (all of them)
    "quarterly numbers"          an exact phrase
    from:ann  to:krem  cc:dan    who (a name or part of an address)
    subject:invoice  body:refund
    is:unread  is:read  is:starred  is:unstarred  is:answered  is:unanswered
    has:attachment
    after:2026-11-01  before:2026-12-01  on:2026-11-18      (also today, yesterday)
    newer_than:7d  older_than:2m                            (d days, w weeks, m months, y years)
    in:sent  in:spam  in:all                                where to look
    -from:newsletter  -word                                 leave these out

Every term must match. parse() turns the text into terms once; a mail
server then gets them in its own form:

  - imap():  standard IMAP SEARCH keys, for any server (Dovecot, Exchange...).
    IMAP cannot ask about attachments, so `has:attachment` comes back as a
    flag and mailbox.py checks the results' structure itself.
  - gmail(): Gmail's own search text (X-GM-RAW). Gmail is searched with its
    real engine, so everything Gmail's box understands works there as well
    (label:, filename:, larger:, OR...): a term this module does not know is
    passed to Gmail as it was typed.

A bad date or value is a ValueError whose text is fit to show.
"""
import re
from datetime import date, timedelta

KEYS = ('from', 'to', 'cc', 'subject', 'body', 'is', 'has', 'after', 'before', 'on',
        'newer_than', 'older_than', 'in')
_TOKEN = re.compile(r'''(-?)(?:([A-Za-z_]+):)?(?:"([^"]*)"|(\S+))''')
_IS = {'unread': 'UNSEEN', 'read': 'SEEN', 'starred': 'FLAGGED', 'unstarred': 'UNFLAGGED',
       'answered': 'ANSWERED', 'replied': 'ANSWERED', 'unanswered': 'UNANSWERED', 'unreplied': 'UNANSWERED'}
_FIELD = {'from': 'FROM', 'to': 'TO', 'cc': 'CC', 'subject': 'SUBJECT', 'body': 'BODY', 'text': 'TEXT'}
_SPAN = re.compile(r'(\d{1,4})([dwmy])', re.I)
_CONTROL = re.compile(r'[\x00-\x08\x0a-\x1f\x7f]')
HELP = ('Search words: from:  to:  cc:  subject:  body:  is:unread  is:starred  has:attachment  '
        'after:YYYY-MM-DD  before:  on:  newer_than:7d  in:sent  in:all  "exact phrase"  -word')


def parse(text):
    """The query as [(negated, key, value)]. key is one of KEYS, 'text' for a
    plain word or phrase, or 'raw' for a `word:value` this module does not
    know (kept as typed)."""
    text = str(text or '')
    if _CONTROL.search(text):
        # A line break inside a value would reach the mail server as a new command.
        raise ValueError("A search cannot contain line breaks or control characters.")
    terms = []
    for neg, key, quoted, bare in _TOKEN.findall(text):
        value = quoted if quoted or not bare else bare
        if not key:
            stem = value.strip()[:-1].lower()
            if not quoted and value.strip().endswith(':') and stem in KEYS:       # 'from:' with nothing after it
                raise ValueError(f"'{stem}:' needs something after it, like {stem}:{_EXAMPLE.get(stem, 'word')}.")
            if value.strip():
                terms.append((bool(neg), 'text', value.strip()))
            continue
        low = key.lower()
        if low in KEYS:
            if not value.strip():
                raise ValueError(f"'{low}:' needs something after it, like {low}:{_EXAMPLE.get(low, 'word')}.")
            terms.append((bool(neg), low, value.strip()))
        else:
            terms.append((bool(neg), 'raw', f'{key}:"{value}"' if quoted else f"{key}:{value}"))
    return terms


_EXAMPLE = {'from': 'ann', 'to': 'ann', 'cc': 'ann', 'is': 'unread', 'has': 'attachment', 'after': '2026-11-01',
            'before': '2026-11-01', 'on': '2026-11-01', 'newer_than': '7d', 'older_than': '2m', 'in': 'sent'}


def where(terms):
    """(the terms without `in:`, the place named by the last `in:` or None).
    'all' / 'anywhere' / 'everywhere' mean every folder."""
    place, rest = None, []
    for neg, key, value in terms:
        if key == 'in' and neg:
            raise ValueError(f"-in:{value} is not supported: name the folder to look in (in:inbox), or in:all. "
                             "A search everywhere already leaves out spam and trash.")
        if key == 'in':
            place = 'all' if value.lower() in ('all', 'anywhere', 'everywhere', 'any') else value
        else:
            rest.append((neg, key, value))
    return rest, place


def day(value, today=None):
    """A date a person typed: 2026-11-18, 2026/11/18, today, yesterday."""
    today = today or date.today()
    word = str(value).strip().lower()
    if word in ('today', 'yesterday', 'tomorrow'):
        return today + timedelta(days={'today': 0, 'yesterday': -1, 'tomorrow': 1}[word])
    m = re.fullmatch(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', word)
    if not m:
        raise ValueError(f"'{value}' is not a date. Use YYYY-MM-DD, today or yesterday.")
    try:
        got = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        raise ValueError(f"'{value}' is not a real date.")
    if not 1970 <= got.year <= 2200:
        raise ValueError(f"'{value}' is outside the years a search can use (1970 to 2200).")
    return got


def _ago(value, today):
    m = _SPAN.fullmatch(str(value).strip())
    if not m:
        raise ValueError(f"'{value}' is not a span of time. Use a number and d, w, m or y, like 7d or 2m.")
    n, unit = int(m.group(1)), m.group(2).lower()
    days = n * {'d': 1, 'w': 7, 'm': 30, 'y': 365}[unit]
    if days > 365 * 60:
        raise ValueError(f"'{value}' is further back than a search can go (60 years at most).")
    return today - timedelta(days=days)


def _is(value):
    key = _IS.get(value.lower())
    if not key:
        raise ValueError(f"is:{value} is not known. Use is:unread, is:read, is:starred, is:unstarred, is:answered.")
    return key


def _has(value):
    if value.lower() not in ('attachment', 'attachments', 'file', 'files'):
        raise ValueError(f"has:{value} is not known here. Use has:attachment.")


def imap(terms, today=None):
    """(IMAP SEARCH criteria, charset or None, wants_attachment). The
    criteria are the nested list IMAPClient takes; dates stay date objects."""
    today = today or date.today()
    out, attach = [], None
    for neg, key, value in terms:
        if key in _FIELD:
            one = [_FIELD[key], value]
        elif key == 'raw':
            one = ['TEXT', value]                      # not ours to read: look for it as words
        elif key == 'is':
            one = [_is(value)]
        elif key == 'has':
            _has(value)
            if attach is not None and attach != (not neg):
                raise ValueError("has:attachment and -has:attachment cannot both be asked for.")
            attach = not neg
            continue
        elif key == 'after':
            one = ['SINCE', day(value, today)]
        elif key == 'before':
            one = ['BEFORE', day(value, today)]
        elif key == 'on':
            one = ['ON', day(value, today)]
        elif key == 'newer_than':
            one = ['SINCE', _ago(value, today)]
        elif key == 'older_than':
            one = ['BEFORE', _ago(value, today)]
        else:
            continue
        out += ['NOT', one] if neg else one
    flat = [str(x) for part in out for x in (part if isinstance(part, list) else [part])]
    charset = None if all(s.isascii() for s in flat) else 'UTF-8'
    return (out or ['ALL']), charset, attach


def _quoted(value):
    return f'"{value}"' if re.search(r'\s', value) else value


def gmail(terms, today=None):
    """The terms as Gmail's own search text."""
    today = today or date.today()
    parts = []
    for neg, key, value in terms:
        if key == 'text':
            one = _quoted(value)
        elif key == 'raw':
            one = value
        elif key == 'is':
            _is(value)
            low = value.lower()
            if low in ('answered', 'replied', 'unanswered', 'unreplied'):
                # Gmail has no word for "answered": ask its IMAP side for the flag instead
                raise _NeedsImap()
            if low == 'unstarred':                  # Gmail has no such word: flip starred instead
                low, neg = 'starred', not neg
            one = f"is:{low}"
        elif key == 'has':
            _has(value)
            one = 'has:attachment'
        elif key in ('after', 'before'):
            one = f"{key}:{day(value, today).strftime('%Y/%m/%d')}"
        elif key == 'on':
            d = day(value, today)
            one = f"after:{d.strftime('%Y/%m/%d')} before:{(d + timedelta(days=1)).strftime('%Y/%m/%d')}"
            if neg:
                raise _NeedsImap()
        elif key in ('newer_than', 'older_than'):
            _ago(value, today)                      # the same refusals as the standard search
            n, unit = _SPAN.fullmatch(value.strip()).groups()
            if unit.lower() == 'w':
                n, unit = int(n) * 7, 'd'
            one = f"{key}:{n}{unit.lower()}"
        elif key == 'body':
            one = _quoted(value)                        # Gmail has no body-only search: anywhere in the mail
        else:                                           # from, to, cc, subject
            one = f"{key}:{_quoted(value)}"
        parts.append(f"-{one}" if neg else one)
    return ' '.join(parts)


class _NeedsImap(Exception):
    """This query has a term Gmail's search text cannot say; use imap() for it."""


def for_gmail(terms, today=None):
    """Gmail's search text for the terms, or None when one of them can only
    be asked through standard IMAP (mailbox.py then uses imap())."""
    try:
        return gmail(terms, today)
    except _NeedsImap:
        return None
