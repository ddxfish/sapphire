"""Why can't we reach a download host? A few seconds, the standard library only.

A first Whisper download sat at 0% for 19 minutes on a network whose router
handed out addresses for Hugging Face's download server that had been dead
for months (2026-11-24). The library's own error was a URL and "error sending
request". This asks the questions a person would: does this network's DNS
answer, do the addresses it gives answer AS that host, and do the addresses a
public DNS server gives do better. It runs only when a download has stalled
or failed (core/notify.py watch, core/hf_fetch.py), never on a healthy one.

The public question goes to 1.1.1.1 over HTTPS by address (no DNS needed to
ask it) and names the one download host and nothing else: docs/NETWORK.md.
Behind a proxy (Sapphire's SOCKS mode included) it does nothing at all.
"""
import json
import logging
import socket
import ssl
import urllib.request
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

WAIT = 4                # seconds for any one connection
PUBLIC_DNS = '1.1.1.1'
# Most telling first: when several hosts are asked about, the row says this one.
ORDER = ('clock', 'stale-dns', 'no-dns', 'offline', 'cut-off', 'host-down', 'unknown', 'ok')


def _context():
    try:
        import certifi                      # the roots the downloaders use; a fresh Windows store can be short
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _try(host, ip, wait=WAIT):
    """One address, asked to be `host`: 'ok', 'timeout', 'refused', 'wrong-server'
    (it answers as somebody else), 'clock' (its certificate reads as expired or
    not yet valid), or 'tls' / 'net' for anything else."""
    try:
        with socket.create_connection((ip, 443), timeout=wait) as raw:
            with _context().wrap_socket(raw, server_hostname=host):
                return 'ok'
    except ssl.SSLCertVerificationError as e:
        if e.verify_code in (9, 10):
            return 'clock'
        return 'wrong-server' if e.verify_code == 62 or 'match' in str(e).lower() else 'tls'
    except (socket.timeout, TimeoutError):
        return 'timeout'
    except ConnectionRefusedError:
        return 'refused'
    except ssl.SSLError:
        return 'tls'
    except OSError:
        return 'net'


def _local(host):
    """The addresses this computer's DNS gives; [] when it has no answer."""
    try:
        return sorted({a[4][0] for a in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
    except (socket.gaierror, UnicodeError):
        return []


def _public(host, wait=WAIT):
    """The addresses a public DNS server gives; None when it could not be asked."""
    req = urllib.request.Request(f'https://{PUBLIC_DNS}/dns-query?name={host}&type=A',
                                 headers={'accept': 'application/dns-json'})
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                             urllib.request.HTTPSHandler(context=_context()))
        with opener.open(req, timeout=wait) as r:
            return sorted(a['data'] for a in json.load(r).get('Answer', []) if a.get('type') == 1)
    except Exception as e:
        logger.debug(f"[NET] public DNS not asked: {e}")
        return None


def _many(host, ips):
    if not ips:
        return {}
    with ThreadPoolExecutor(min(16, len(ips))) as pool:
        return dict(zip(ips, pool.map(lambda ip: _try(host, ip), ips)))


def _proxied():
    """True when outbound traffic rides a proxy: Sapphire's SOCKS mode (it
    stamps the environment, core/socks_proxy.py) or the system's own. A direct
    look would then say nothing true, and it would hand the host's name to the
    local resolver, which SOCKS mode promises never happens (core/net.py). So
    nothing below runs: no lookup, no connection, no public question."""
    try:
        import config
        if getattr(config, 'SOCKS_ENABLED', False):
            return True
    except Exception:
        pass
    found = urllib.request.getproxies()
    return any(found.get(k) for k in ('https', 'all', 'socks'))


def diagnose(host):
    """{'host', 'verdict', 'detail'}. Verdicts: 'ok' (it answers), 'stale-dns'
    (our addresses are dead, a public server's work), 'no-dns' (our DNS has no
    answer, a public server has), 'offline' (nothing can be looked up or
    reached), 'cut-off' (our addresses are dead and the public server cannot be
    asked), 'host-down' (dead on every address anyone gives), 'clock' (this
    computer's date is wrong), 'unknown' (behind a proxy: a direct look says
    nothing)."""
    def out(verdict, detail=''):
        return {'host': host, 'verdict': verdict, 'detail': detail}
    if _proxied():
        return out('unknown', 'a proxy is set')
    mine = _local(host)
    got = _many(host, mine)
    kinds = sorted(set(got.values()))
    if 'ok' in kinds:
        return out('ok', f"{sum(v == 'ok' for v in got.values())} of {len(mine)} addresses answer")
    if 'clock' in kinds:
        return out('clock')
    theirs = _public(host)
    if theirs is None:
        return out('cut-off' if mine else 'offline', ', '.join(kinds))
    fresh = [ip for ip in theirs if ip not in got][:4]
    works = sum(v == 'ok' for v in _many(host, fresh).values())
    if not mine:
        return out('no-dns' if theirs else 'host-down', 'no address from either' if not theirs else '')
    if works:
        return out('stale-dns', f"none of {len(mine)} addresses work ({', '.join(kinds)}); {works} of {len(fresh)} public ones do")
    return out('host-down', ', '.join(kinds))


_WORDS = {
    'stale-dns': ("This network's DNS is giving out-of-date addresses for {host}: none of them answer, while the "
                  "addresses a public DNS server gives do. Restarting the router usually clears it; setting this "
                  "computer's DNS to 1.1.1.1 or 9.9.9.9 also works."),
    'no-dns': ("This network's DNS has no answer for {host}, though a public DNS server does. Restart the router, "
               "or set this computer's DNS to 1.1.1.1 or 9.9.9.9."),
    'offline': "This computer does not seem to be online: {host} cannot be looked up and nothing outside answers.",
    'cut-off': ("{host} is not answering, and neither is a public DNS server: the internet connection looks down "
                "or filtered."),
    'host-down': "{host} is not answering from any address, so the trouble is likely on their side. Try again later.",
    'clock': ("This computer's date or time looks wrong: the security certificate of {host} reads as expired or "
              "not yet valid. Set the clock, then try again."),
    'ok': "The servers answer, so the connection is likely slow or being filtered.",
    'unknown': '',
}


def check(hosts):
    """(verdict, words) for the most telling of these hosts. ('ok', ...) when all answer."""
    hosts = [h for h in dict.fromkeys(hosts or ()) if h]
    if not hosts:
        return 'unknown', ''
    with ThreadPoolExecutor(len(hosts)) as pool:
        found = list(pool.map(diagnose, hosts))
    worst = min(found, key=lambda d: ORDER.index(d['verdict']))
    logger.info(f"[NET] {', '.join(d['host'] + '=' + d['verdict'] + (' (' + d['detail'] + ')' if d['detail'] else '') for d in found)}")
    return worst['verdict'], _WORDS[worst['verdict']].format(host=worst['host'])


def why(hosts):
    """The words alone, for a row."""
    return check(hosts)[1]
