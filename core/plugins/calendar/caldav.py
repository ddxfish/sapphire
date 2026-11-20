# core/plugins/calendar/caldav.py - a CalDAV server, on the wire
"""
CalDAV is how iCloud, Fastmail, Nextcloud, Yahoo and most self-hosted
calendars are reached: plain HTTP with a login, a few WebDAV verbs, and one
calendar file (.ics) per event. This is only the wire: find the calendars of a
login, read the events of one in a range of days, write one event file, delete
one. What an event means is dav.py's (through ics.py and icsw.py).

Built on `requests` and the XML reader Python ships; no CalDAV package.

Where the login may go. It goes out as HTTP Basic (Digest when that is all a
server offers). A server names other addresses all the time: a redirect, the
account's own address, the folder of its calendars, each event file. Every
one of those is checked by _may_follow() before the login is sent there:
  - never from https down to http;
  - to another host only over https, and never from an outside server to an
    address on this machine's own network;
  - a calendar must be on its folder's server, an event file on its calendar's.
A server's own text never reaches a CalError: the words are ours, the status
code is the only thing quoted.
"""
import base64
import hashlib
import logging
import re
from collections import namedtuple
from urllib.parse import unquote, urljoin, urlsplit
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

import requests
from core import net

from .errors import CalError

logger = logging.getLogger(__name__)

TIMEOUT = 25
MAX_BYTES = 25 * 1024 * 1024
MAX_HOPS = 4
D, C, CS, APPLE = 'DAV:', 'urn:ietf:params:xml:ns:caldav', 'http://calendarserver.org/ns/', 'http://apple.com/ns/ical/'
_NS = f'xmlns:d="{D}" xmlns:c="{C}" xmlns:cs="{CS}"'
_XML = {'Content-Type': 'application/xml; charset=utf-8'}
_digest_hosts = set()         # servers that only take Digest: asked that way from the first request on

Login = namedtuple('Login', 'url user password')


class Refused(CalError):
    """The server turned the login down."""


class Gone(CalError):
    """The address is not there (any more)."""


class Changed(CalError):
    """The event was changed somewhere else since it was read."""


class Odd(CalError):
    """The address answered, but not the way a calendar server does."""


class Unreachable(CalError):
    """Nothing answered at all (no connection, no trusted certificate, too slow)."""


def clean_url(url):
    text = str(url or '').strip()
    if text and not re.match(r'[a-z][a-z0-9+.\-]*://', text, re.I):
        text = 'https://' + text
    wrong = CalError("A calendar server address looks like https://server.example.com (no login inside it).")
    if not re.match(r'https?://[^\s/]+', text, re.I) or len(text) > 2000 or re.search(r'\s', text):
        raise wrong
    try:
        parts = urlsplit(text)
        if '@' in parts.netloc or not parts.hostname:
            raise wrong
        parts.port                                    # a port that is not a number raises here
    except ValueError:
        raise wrong
    if not parts.path:
        text += '/'                                   # 'https://host' and 'https://host/' are one address
    return text


def host_of(url):
    try:
        return urlsplit(url).hostname or ''
    except ValueError:
        return ''


def _origin(url):
    """(scheme, host, port) of an address; None when it is not a usable one."""
    try:
        p = urlsplit(url)
        scheme = (p.scheme or '').lower()
        if scheme not in ('http', 'https') or not p.hostname:
            return None
        return scheme, p.hostname.lower(), p.port or (443 if scheme == 'https' else 80)
    except ValueError:
        return None


def _may_follow(frm, to):
    """May the login that was sent to `frm` also be sent to `to`, an address `frm` named?"""
    a, b = _origin(frm), _origin(to)
    if a is None or b is None:
        return False
    if a == b:
        return True
    if b[0] != 'https':
        return False                                  # never down to http, and another host only over https
    if a[1] == b[1]:
        return True
    try:
        return not (net.classify(a[1]) == 'wan' and net.classify(b[1]) == 'lan')
    except Exception:
        return False


def _join(base, href):
    try:
        return urljoin(base, href)
    except ValueError:
        return ''


def key_of(ref):
    """A short name for a calendar's address, for a page that is not handed the address itself."""
    return hashlib.sha256(str(ref).encode('utf-8')).hexdigest()[:16]


def _basic(login):
    raw = f"{login.user}:{login.password}".encode('utf-8')
    return 'Basic ' + base64.b64encode(raw).decode('ascii')


def _read(resp):
    data = bytearray()
    try:
        for chunk in resp.iter_content(65536):
            data += chunk
            if len(data) > MAX_BYTES:
                raise CalError("The calendar server sent more than this can take in one answer.")
    except requests.RequestException as e:
        raise Unreachable(f"The calendar server's answer broke off ({type(e).__name__}).")
    finally:
        resp.close()
    return bytes(data)


_NOT_ALLOWED = ("{host} tried to send the login on to an address it may not go to (an unencrypted one, or one on "
                "this machine's own network). Nothing was sent there.")


def _send(login, method, url, body=None, headers=None, expect=(207,)):
    """One request -> (status, headers, the body as bytes, the address that
    answered). Raises a CalError in our own words for anything outside `expect`."""
    headers = dict(headers or {})
    headers.setdefault('User-Agent', 'Sapphire-Calendar')
    data = body.encode('utf-8') if isinstance(body, str) else body
    host = host_of(url) or 'the calendar server'
    switched = False
    for hop in range(MAX_HOPS + 3):
        digest = host in _digest_hosts
        kw = dict(headers=dict(headers), data=data, timeout=TIMEOUT, allow_redirects=False, stream=True)
        try:
            if digest:
                kw['auth'] = requests.auth.HTTPDigestAuth(login.user, login.password)
            else:
                kw['headers']['Authorization'] = _basic(login)
            resp = net.request(method, url, **kw)
        except requests.exceptions.SSLError:
            raise Unreachable(f"{host} did not show a certificate this machine trusts, so nothing was sent to it.")
        except requests.exceptions.Timeout:
            raise Unreachable(f"{host} did not answer in time.")
        except (requests.exceptions.InvalidURL, UnicodeError, ValueError):
            raise Odd("That calendar server address, or the login, has characters that cannot be sent.")
        except requests.RequestException as e:
            raise Unreachable(f"Couldn't reach {host} ({type(e).__name__}).")
        status = resp.status_code
        if status in (301, 302, 307, 308) and resp.headers.get('Location'):
            target = _join(url, resp.headers['Location'])
            resp.close()
            if not _may_follow(url, target):
                raise Odd(_NOT_ALLOWED.format(host=host))
            if hop >= MAX_HOPS:
                break
            url, host = target, host_of(target) or host
            continue
        if status == 401 and not switched:            # the other way of logging in, tried once
            offered = resp.headers.get('WWW-Authenticate', '').lower()
            if (not digest and 'digest' in offered and 'basic' not in offered) or (digest and 'basic' in offered):
                resp.close()
                (_digest_hosts.discard if digest else _digest_hosts.add)(host)
                switched = True
                continue
        if status in expect:
            return status, resp.headers, _read(resp), url
        resp.close()
        if status == 401:
            raise Refused(f"{host} refused the login. Check the user name, and that the password is an app password "
                          "where the provider asks for one.")
        if status == 403:
            raise Odd(f"{host} does not allow that for this login.")
        if status in (404, 410):
            raise Gone(f"{host} has nothing at that address (any more).")
        if status == 412:
            raise Changed("That event was changed somewhere else a moment ago. Look at it again, then repeat the change.")
        if status == 507:
            raise CalError(f"{host} is out of space for this account.")
        if status >= 500:
            raise Odd(f"{host} is not answering right now (HTTP {status}). Try again in a few minutes.")
        if status == 405 or 200 <= status < 300:
            raise Odd(f"{host} did not answer like a calendar server at that address.")
        raise Odd(f"{host} answered with an error (HTTP {status}).")
    raise Odd(f"{host} kept sending the request somewhere else.")


def _multistatus(data, base):
    """A 207 answer -> [(absolute address, {tag: element})], the properties
    that were found (status 200) of each entry. An entry at an address the
    login may not be sent to (_may_follow) is left out."""
    if re.search(rb'<!\s*(DOCTYPE|ENTITY)', data, re.I):
        raise Odd("The calendar server sent an answer this will not read.")
    try:
        root = ET.fromstring(data)
    except (ET.ParseError, ValueError, LookupError):
        raise Odd(f"{host_of(base) or 'That address'} did not answer like a calendar server.")
    out = []
    for resp in root.iter(f'{{{D}}}response'):
        href = _join(base, (resp.findtext(f'{{{D}}}href') or '').strip())
        if not href or not _may_follow(base, href):
            continue
        props = {}
        for stat in resp.findall(f'{{{D}}}propstat'):
            if ' 200' not in (stat.findtext(f'{{{D}}}status') or ' 200'):
                continue
            for prop in stat.findall(f'{{{D}}}prop'):
                for el in prop:
                    props[el.tag] = el
        out.append((href, props))
    return out


def _same(a, b):
    try:
        pa, pb = urlsplit(a), urlsplit(b)
    except ValueError:
        return False
    return unquote(pa.path).rstrip('/') == unquote(pb.path).rstrip('/') and (pa.hostname or '') == (pb.hostname or '')


def _propfind(login, url, props, depth='0'):
    body = f'<?xml version="1.0" encoding="utf-8"?><d:propfind {_NS} xmlns:a="{APPLE}"><d:prop>{props}</d:prop></d:propfind>'
    _s, _h, data, final = _send(login, 'PROPFIND', url, body, {'Depth': depth, **_XML}, expect=(207, 200))
    return _multistatus(data, final), final


def _href_in(el):
    return (el.findtext(f'{{{D}}}href') or '').strip() if el is not None else ''


def _text_of(el):
    return (el.text or '').strip() if el is not None else ''


def _is_calendar(props):
    kind = props.get(f'{{{D}}}resourcetype')
    return kind is not None and kind.find(f'{{{C}}}calendar') is not None


def _calendar(url, props):
    kinds = props.get(f'{{{C}}}supported-calendar-component-set')
    if kinds is not None:
        names = {(c.get('name') or '').upper() for c in kinds.iter(f'{{{C}}}comp')}
        if names and 'VEVENT' not in names:
            return None                               # a task list or a notes folder
    rights = props.get(f'{{{D}}}current-user-privilege-set')
    writable = True
    if rights is not None:
        have = {el.tag.split('}')[-1] for priv in rights.iter(f'{{{D}}}privilege') for el in priv}
        writable = bool(have & {'write', 'write-content', 'all', 'bind'})
    name = ' '.join(_text_of(props.get(f'{{{D}}}displayname')).split())[:60]
    if not name:
        name = unquote(urlsplit(url).path.rstrip('/').rsplit('/', 1)[-1])[:60] or 'Calendar'
    return {'ref': url if url.endswith('/') else url + '/', 'name': name, 'writable': writable}


_FIND = '<d:current-user-principal/><c:calendar-home-set/><d:resourcetype/><d:displayname/>' \
        '<c:supported-calendar-component-set/><d:current-user-privilege-set/>'
_LIST = '<d:resourcetype/><d:displayname/><c:supported-calendar-component-set/><d:current-user-privilege-set/>'


def _look(login, url):
    """What one address says about itself: (its properties or None, where it really is)."""
    entries, final = _propfind(login, url, _FIND)
    for href, props in entries:
        if _same(href, final):
            return props, final
    return (entries[0][1], final) if entries else (None, final)


def _named(at, href):
    """An address a server named (the account's own, the folder of its calendars), made whole and checked."""
    url = _join(at, href)
    if not url or not _may_follow(at, url):
        raise Odd(_NOT_ALLOWED.format(host=host_of(at) or 'That server'))
    return url


def discover(login):
    """The calendars this login can reach: {'home': address, 'calendars':
    [{'ref', 'name', 'writable'}]}. `login.url` may be the server, the
    account's own address, the folder of calendars, or one calendar."""
    start = clean_url(login.url)
    origin = '{0.scheme}://{0.netloc}'.format(urlsplit(start))
    tried, props, at, plain, trouble = [], None, start, None, None
    for url in (start, origin + '/.well-known/caldav', origin + '/'):
        if url in tried:
            continue
        tried.append(url)
        try:
            got, where = _look(login, url)
        except (Refused, Unreachable):
            raise                                     # a wrong login or a dead server is the answer: no more knocking
        except CalError as e:
            if not isinstance(e, Gone):
                trouble = trouble or e                # this address is no use; the next may be
            continue
        if got is not None and (f'{{{D}}}current-user-principal' in got or f'{{{C}}}calendar-home-set' in got
                                 or _is_calendar(got)):
            props, at = got, where
            break
        if got is not None and plain is None and url == start:
            plain = (got, where)                      # it answered, and may itself be the folder of calendars
    if props is None and plain is not None:
        props, at = plain
    if props is None:
        if trouble is not None:
            raise trouble
        raise Odd(f"{host_of(start) or 'That address'} did not answer like a calendar server. "
                  "Check the address your provider gives for CalDAV.")
    if _is_calendar(props):                           # the address IS one calendar
        one = _calendar(at, props)
        return {'home': at, 'calendars': [one] if one else []}
    home = _href_in(props.get(f'{{{C}}}calendar-home-set'))
    if not home:
        principal = _href_in(props.get(f'{{{D}}}current-user-principal'))
        if principal:
            inner, at2 = _look(login, _named(at, principal))
            if inner is not None:
                home, at = _href_in(inner.get(f'{{{C}}}calendar-home-set')), at2
    home = _named(at, home) if home else at           # no home named: the address itself may hold the calendars
    entries, final = _propfind(login, home, _LIST, depth='1')
    found = []
    for href, entry in entries:
        if _is_calendar(entry) and _origin(href) == _origin(final):       # a calendar lives on its folder's server
            one = _calendar(href, entry)
            if one and not any(_same(one['ref'], c['ref']) for c in found):
                found.append(one)
    if not found and not _href_in(props.get(f'{{{C}}}calendar-home-set')) \
            and f'{{{D}}}current-user-principal' not in props:
        raise Odd(f"{host_of(start) or 'That address'} answered, but no calendars were found there. "
                  "Check the address your provider gives for CalDAV.")
    return {'home': final, 'calendars': found}


def ctag(login, calendar):
    """A mark that changes whenever anything in the calendar does; '' when the server has none."""
    try:
        entries, _final = _propfind(login, calendar, '<cs:getctag/><d:sync-token/>')
    except (Gone, Refused, Unreachable):
        raise
    except CalError as e:
        logger.debug(f"[calendar] no change mark from {host_of(calendar)}: {e}")
        return ''
    for _href, props in entries:
        for tag in (f'{{{CS}}}getctag', f'{{{D}}}sync-token'):
            if _text_of(props.get(tag)):
                return _text_of(props.get(tag))[:400]
    return ''


def _files(entries, calendar):
    """Event files out of a listing: ([(address, etag, text)], [addresses that came without their text])."""
    out, bare = [], []
    for href, props in entries:
        if _origin(href) != _origin(calendar):
            continue                                  # an event file is on its calendar's own server
        data = props.get(f'{{{C}}}calendar-data')
        etag = _text_of(props.get(f'{{{D}}}getetag'))
        if _text_of(data):
            out.append((href, etag, data.text))
        elif href.lower().endswith('.ics') or etag:
            bare.append(href)
    return out, bare


def events(login, calendar, first_utc, last_utc):
    """Every event file with something in [first_utc, last_utc) (aware UTC
    datetimes): [(address, etag, text)]. A repeating event comes whole, with
    its changed occurrences; nothing is expanded by the server."""
    span = f'start="{first_utc:%Y%m%dT%H%M%SZ}" end="{last_utc:%Y%m%dT%H%M%SZ}"'
    body = (f'<?xml version="1.0" encoding="utf-8"?><c:calendar-query {_NS}>'
            '<d:prop><d:getetag/><c:calendar-data/></d:prop>'
            '<c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT">'
            f'<c:time-range {span}/></c:comp-filter></c:comp-filter></c:filter></c:calendar-query>')
    _s, _h, data, final = _send(login, 'REPORT', calendar, body, {'Depth': '1', **_XML}, expect=(207, 200))
    out, bare = _files(_multistatus(data, final), final)
    for i in range(0, len(bare), 50):                 # a server that listed them without their text
        hrefs = ''.join(f'<d:href>{escape(urlsplit(h).path)}</d:href>' for h in bare[i:i + 50])
        body = (f'<?xml version="1.0" encoding="utf-8"?><c:calendar-multiget {_NS}>'
                f'<d:prop><d:getetag/><c:calendar-data/></d:prop>{hrefs}</c:calendar-multiget>')
        try:
            _s, _h, data, final = _send(login, 'REPORT', calendar, body, {'Depth': '1', **_XML}, expect=(207, 200))
            out.extend(_files(_multistatus(data, final), final)[0])
        except (Refused, Unreachable):
            raise
        except CalError as e:                         # what the first answer held is still worth having
            logger.debug(f"[calendar] {host_of(calendar)} did not hand over some events by name: {e}")
    return out


def _strong(etag):
    etag = (etag or '').strip()
    return '' if etag[:2].upper() == 'W/' else etag


def get(login, href):
    """One event file -> (text, etag). The etag is the version mark a later
    put() is made conditional on; '' only when the server has none at all."""
    # identity: a proxy that compresses the answer also rewrites its ETag, and the server would then never match it
    _s, headers, data, final = _send(login, 'GET', href, headers={'Accept': 'text/calendar', 'Accept-Encoding': 'identity'},
                                     expect=(200,))
    try:
        text = data.decode('utf-8').lstrip('\ufeff')
    except UnicodeDecodeError:                        # it goes back whole after a change: a guess would damage it
        raise Odd("That event is stored in a way this cannot read exactly, so it can only be changed where it is kept.")
    if 'BEGIN:VCALENDAR' not in text[:2000].upper():
        raise Odd("The calendar server sent something that is not an event.")
    etag = _strong(headers.get('ETag'))
    if not etag:                                      # no mark on the answer: ask for the file's own
        try:
            entries, _f = _propfind(login, final, '<d:getetag/>')
            etag = next((_strong(_text_of(p.get(f'{{{D}}}getetag'))) for _h, p in entries), '')
        except (Refused, Unreachable):
            raise
        except CalError:
            etag = ''
    return text, etag


def put(login, href, text, etag='', new=False):
    """Write one event file; returns its new etag ('' when the server does
    not say). `new`: only if nothing is there yet. `etag`: only if it is
    still the version that was read (Changed otherwise)."""
    headers = {'Content-Type': 'text/calendar; charset=utf-8'}
    if new:
        headers['If-None-Match'] = '*'
    elif etag:
        headers['If-Match'] = etag
    _s, got, _data, _final = _send(login, 'PUT', href, text, headers, expect=(200, 201, 204))
    return (got.get('ETag') or '').strip()


def delete(login, href):
    """Remove one event file. One that is already gone counts as removed."""
    try:
        _send(login, 'DELETE', href, expect=(200, 202, 204))
    except Gone:
        pass
