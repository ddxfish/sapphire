# core/plugins/social/providers/_common.py - what every built-in provider shares
from datetime import datetime, timezone

from core.redact import scrub
from plugins.social.errors import SocialError


def _message(data):
    """The platform's own words, wherever it keeps them."""
    if not isinstance(data, dict):
        return ''
    err = data.get('error')
    if isinstance(err, dict):
        return str(err.get('message') or err.get('error_user_msg') or err.get('type') or '')
    if isinstance(err, str):
        return err + (f": {data['error_description']}" if data.get('error_description') else '')
    errors = data.get('errors')
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        return str(errors[0].get('message') or errors[0].get('detail') or errors[0].get('title') or '')
    for k in ('error_description', 'detail', 'message', 'title'):
        if data.get(k):
            return str(data[k])
    return ''


def check(resp, what):
    """The JSON body, or SocialError naming the platform's complaint."""
    try:
        data = resp.json() if resp.content else {}
    except ValueError:
        data = {}
    if resp.status_code >= 400:
        msg = scrub(_message(data) or (resp.text or '').strip()[:200])
        raise SocialError(f"{what}: HTTP {resp.status_code}{(' ' + msg) if msg else ''}")
    return data if isinstance(data, dict) else {'data': data}


def iso_ts(s):
    """ISO 8601 (Z, +00:00 or +0000) → epoch seconds; 0 when unreadable."""
    s = str(s or '').strip()
    if not s:
        return 0.0
    if s.endswith('Z'):
        s = s[:-1] + '+00:00'
    if len(s) > 5 and s[-5] in '+-' and s[-3] != ':':
        s = s[:-2] + ':' + s[-2:]
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def iso_utc(ts):
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def need(conn, where, *keys):
    """The config/secret/meta values named, or SocialError saying which is missing."""
    out = []
    for k in keys:
        v = where.get(k)
        if not v:
            raise SocialError(f"'{conn.name}' has no {k.replace('_', ' ')} yet (Settings > Plugins > Social)")
        out.append(v)
    return out
