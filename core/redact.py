# core/redact.py - keep secrets out of text bound for a log, the model, a UI or a URL
#
# One scrubber for the whole app (devices' Secrets.scrub calls it too). Two
# layers: secret-named `name=value` pairs and Bearer tokens are blanked wherever
# they appear, and every secret value the caller knows is blanked by value.
# describe() is what anyone is told about an UNEXPECTED exception: requests'
# network errors carry the full request URL, query string and all (Meta's
# Graph API takes access_token in the query), so they are reduced to kind +
# host and their text is never used. LogFilter sits on the root handlers.
import logging
import re
from urllib.parse import urlsplit

MARK = '[secret]'
_PARAMS = ('access_token', 'refresh_token', 'id_token', 'client_secret', 'fb_exchange_token',
           'code', 'api_key', 'apikey', 'key', 'token', 'secret', 'password', 'passwd',
           'sig', 'signature', 'appsecret_proof')
_QUERY = re.compile(r'(?i)((?<![\w-])(?:%s)=)[^&#\s\'"<>]+' % '|'.join(_PARAMS))
_BEARER = re.compile(r'(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{8,}')


def scrub(text, secrets=()):
    """The text with secret query values, Bearer tokens and every given secret
    value (and each line of a multi-line one) replaced by [secret]."""
    text = _BEARER.sub(r'\1' + MARK, _QUERY.sub(r'\1' + MARK, str(text)))
    parts = set()
    for value in secrets or ():
        value = str(value or '')
        parts.update(p for p in [value] + [ln.strip() for ln in value.splitlines()] if len(p) >= 4)
    for part in sorted(parts, key=len, reverse=True):
        text = text.replace(part, MARK)
    return text


def is_network(e):
    """A requests network error: its text and traceback hold the request URL,
    so log it without exc_info."""
    try:
        import requests
    except ImportError:
        return False
    return isinstance(e, requests.RequestException)


def describe(e, secrets=()):
    """'Kind: message' for an unexpected exception, safe to show anyone. A
    network error says only its kind and host - its message holds the URL."""
    if is_network(e):
        url = getattr(getattr(e, 'request', None), 'url', '') or ''
        try:
            host = urlsplit(url).hostname
        except ValueError:
            host = None
        return f"network error reaching {host or 'the server'} ({type(e).__name__})"
    return scrub(f"{type(e).__name__}: {e}", secrets)


_fmt = logging.Formatter()


class LogFilter(logging.Filter):
    """Handler filter: no log line or traceback carries a secret query value
    or a Bearer token (urllib3 and httpx print request URLs at DEBUG)."""

    def filter(self, record):
        try:
            msg = record.getMessage()
            clean = scrub(msg)
            if clean != msg:
                record.msg, record.args = clean, None
            if record.exc_info and not record.exc_text:
                record.exc_text = _fmt.formatException(record.exc_info)
            if record.exc_text:
                record.exc_text = scrub(record.exc_text)
        except Exception:
            pass
        return True
