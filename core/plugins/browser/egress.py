# core/plugins/browser/egress.py - the one door her lane's traffic goes through
#
# Request interception never sees a redirect's next hop or a page's WebSocket,
# and Chromium resolves a name again after any check of ours, so a public page
# could still reach the LAN (probe 2026-10-24). Her lane talks only to this
# proxy on 127.0.0.1. It resolves each host ONCE, asks the rails, and connects
# to the very addresses it checked, one after another (a box without IPv6
# must not die on a v6-first answer). Plain http is one request per
# connection, both ways, so a kept-alive socket can't carry a request for
# another host. A host it can't reach is reported in words: Chromium itself
# only ever says ERR_TUNNEL_CONNECTION_FAILED (her NASA click, 2026-10-25).
#
# SOCKS (2026-10-27): when Sapphire's SOCKS proxy is on, every public address
# is dialed THROUGH it (SOCKS5, username/password), so her browsing leaves by
# its own exit, not this machine's. Fail-closed like core/socks_proxy.py: a
# dead or refusing proxy is reported, never quietly dialed around. This
# machine and the LAN stay direct - a remote proxy can't reach them, and only
# her own UI (or an unrailed lane) may go there at all.
import asyncio
import ipaddress
import logging
import weakref
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

HEAD_WAIT = 30             # seconds for a request or response head
DIAL_WAIT = 8              # seconds to reach one checked address
_HOP = (b'connection:', b'proxy-connection:', b'keep-alive:', b'proxy-authorization:')
_live = weakref.WeakKeyDictionary()   # server -> its open writers (drop)
# This machine and the LAN, named outright: anything else is the internet and
# goes through SOCKS when it's on. ("Not is_global" also covers 6to4/Teredo
# relays and test ranges, which are internet-routable: privacy scout 2026-10-27.)
_LAN = [ipaddress.ip_network(n) for n in (
    '127.0.0.0/8', '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '169.254.0.0/16',
    '100.64.0.0/10',                                   # CGNAT / tailnets: a remote exit can't reach them either
    '::1/128', 'fc00::/7', 'fe80::/10')]


async def start(check, report=None, upstream=None):
    """Serve on a free 127.0.0.1 port. `await check(host, port)` -> (why, ips):
    a refusal's words, or '' and the addresses to dial in order ([] = the name
    doesn't resolve; None = let the SOCKS proxy resolve the name).
    `report(host, port, words)` hears what couldn't be reached. `upstream()`
    -> None (dial directly), {host, port, user, password} (SOCKS5), or
    {error: words} (SOCKS is on but unusable: refuse, never go direct)."""
    live = set()
    server = await asyncio.start_server(lambda r, w: _client(r, w, check, report, upstream, live), '127.0.0.1', 0)
    _live[server] = live
    return server


def port(server):
    return server.sockets[0].getsockname()[1]


def drop(server):
    """Hang up every connection open through `server` (the rails or the
    SOCKS route changed): Chromium reconnects, by the new rules."""
    for w in list(_live.get(server, ())):
        try:
            w.close()
        except Exception:
            pass


class SocksError(Exception):
    def __init__(self, words, fatal=True):
        super().__init__(words)
        self.fatal = fatal                 # a login refusal ends the dial; a per-address refusal tries the next


_SOCKS_REPLY = {1: 'general failure', 2: 'not allowed by its rules', 3: 'network unreachable',
                4: 'host unreachable', 5: 'connection refused', 6: 'TTL expired',
                7: 'command not supported', 8: 'address type not supported'}


async def _socks(cfg, target, num):
    """A SOCKS5 CONNECT to `target` (an IP, or a name the proxy resolves)."""
    r, w = await asyncio.wait_for(asyncio.open_connection(cfg['host'], int(cfg['port'])), DIAL_WAIT)
    try:
        user, pw = (cfg.get('user') or '').encode(), (cfg.get('password') or '').encode()
        w.write(b'\x05\x02\x00\x02' if user else b'\x05\x01\x00')
        await w.drain()
        ver, method = await asyncio.wait_for(r.readexactly(2), DIAL_WAIT)
        if ver != 5 or method == 0xFF:
            raise SocksError("refused every login method we offered")
        if method == 2:
            w.write(b'\x01' + bytes([len(user)]) + user + bytes([len(pw)]) + pw)
            await w.drain()
            if (await asyncio.wait_for(r.readexactly(2), DIAL_WAIT))[1] != 0:
                raise SocksError("refused the username or password")
        try:
            addr = ipaddress.ip_address(target)
            dest = (b'\x01' if addr.version == 4 else b'\x04') + addr.packed
        except ValueError:
            name = target.encode('idna')
            dest = b'\x03' + bytes([len(name)]) + name
        w.write(b'\x05\x01\x00' + dest + int(num).to_bytes(2, 'big'))
        await w.drain()
        head = await asyncio.wait_for(r.readexactly(4), DIAL_WAIT)
        if head[1] != 0:
            raise SocksError(f"couldn't connect ({_SOCKS_REPLY.get(head[1], f'code {head[1]}')})", fatal=False)
        size = {1: 4, 4: 16}.get(head[3]) or (await r.readexactly(1))[0]
        await r.readexactly(size + 2)                  # the proxy's bound address: unused
        return r, w
    except Exception:
        w.close()
        raise


def _direct_only(target):
    """This machine and the LAN: a remote proxy can't reach them."""
    try:
        ip = ipaddress.ip_address(target)
    except ValueError:
        return False
    ip = getattr(ip, 'ipv4_mapped', None) or ip
    return any(ip in net for net in _LAN if net.version == ip.version)


async def _dial(ips, num, via=None):
    """(reader, writer) or (None, why). `via` = the SOCKS config, if any."""
    why = ''
    for ip in ips:
        try:
            if via and not _direct_only(ip):
                return await _socks(via, ip, num), ''
            return await asyncio.wait_for(asyncio.open_connection(ip, num), DIAL_WAIT), ''
        except SocksError as e:
            why = f"the SOCKS proxy at {via['host']}:{via['port']} {e}"
            if e.fatal:
                return None, why
            continue
        except (OSError, EOFError, ValueError, asyncio.TimeoutError) as e:   # EOF: the proxy hung up mid-handshake
            if via and not _direct_only(ip) and not why:
                why = f"the SOCKS proxy at {via['host']}:{via['port']} didn't answer"
            continue
    return None, why


def _say(report, host, num, words):
    if report:
        try:
            report(host, num, words)
        except Exception as e:
            logger.debug(f"[BROWSER] egress report: {e}")


async def _client(reader, writer, check, report=None, upstream=None, live=None):
    live = set() if live is None else live
    live.add(writer)
    try:
        await _serve(reader, writer, check, report, upstream, live)
    finally:
        live.discard(writer)


async def _serve(reader, writer, check, report, upstream, live):
    try:
        head = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), HEAD_WAIT)
        line, _, rest = head.partition(b'\r\n')
        method, target, version = line.decode('latin-1').split(' ', 2)
        if method == 'CONNECT':
            host, _, num = target.rpartition(':')
            out = b''
        else:
            u = urlsplit(target)
            if u.scheme != 'http' or not u.hostname:
                return await _refuse(writer, 400, 'Bad Request')
            host, num = u.hostname, u.port or 80
            path = (u.path or '/') + (f'?{u.query}' if u.query else '')
            out = f'{method} {path} {version}\r\n'.encode('latin-1') + _close_head(rest)
        host, num = host.strip('[]'), int(num)
    except Exception:
        writer.close()
        return
    why, ips = await check(host, num)
    if why:
        return await _refuse(writer, 403, 'Forbidden')
    via = upstream() if upstream else None
    if via and via.get('error'):
        _say(report, host, num, via['error'])               # SOCKS is on but unusable: fail closed
        return await _refuse(writer, 502, 'Bad Gateway')
    if ips is None:                                         # the SOCKS proxy resolves the name
        ips = [host] if via else []
    ips = [ips] if isinstance(ips, str) else list(ips or [])
    if not ips:
        _say(report, host, num, "its name doesn't resolve (DNS)")
        return await _refuse(writer, 502, 'Bad Gateway')
    up, fail = await _dial(ips, num, via)
    if up is None:
        _say(report, host, num, fail or f"no answer from {'its address' if len(ips) == 1 else f'any of its {len(ips)} addresses'}")
        return await _refuse(writer, 502, 'Bad Gateway')
    up_r, up_w = up
    live.add(up_w)
    try:
        if method == 'CONNECT':
            writer.write(b'HTTP/1.1 200 Connection established\r\n\r\n')
            await asyncio.gather(_pipe(reader, up_w), _pipe(up_r, writer))
            return
        up_w.write(out)
        # the request's body (a POST) goes up while the answer is awaited:
        # a server waits for the whole body before it says anything
        body = asyncio.ensure_future(_pipe(reader, up_w))
        try:
            resp = await asyncio.wait_for(up_r.readuntil(b'\r\n\r\n'), HEAD_WAIT)
        except Exception:
            body.cancel()
            up_w.close()
            return await _refuse(writer, 502, 'Bad Gateway')
        status, _, rest = resp.partition(b'\r\n')
        writer.write(status + b'\r\n' + _close_head(rest))
        await asyncio.gather(body, _pipe(up_r, writer))
    finally:
        live.discard(up_w)


def _close_head(rest):
    """Header block with hop-by-hop lines swapped for `Connection: close`."""
    keep = [h for h in rest.split(b'\r\n') if h and not h.lower().startswith(_HOP)]
    return b'\r\n'.join(keep + [b'Connection: close']) + b'\r\n\r\n'


async def _refuse(writer, code, words):
    # a body, or full Chromium swaps in its own error page a beat later and
    # that late navigation cuts off whatever she opens next
    body = f'<!doctype html><title>{code} {words}</title><p>{code} {words}: refused by her browser\'s proxy.</p>'.encode()
    try:
        writer.write(f'HTTP/1.1 {code} {words}\r\nContent-Type: text/html\r\nContent-Length: {len(body)}\r\n'
                     f'Connection: close\r\n\r\n'.encode() + body)
        await writer.drain()
    except Exception:
        pass
    writer.close()


async def _pipe(src, dst):
    try:
        while data := await src.read(65536):
            dst.write(data)
            await dst.drain()
    except Exception:
        pass
    finally:
        try:
            dst.close()
        except Exception:
            pass
