# plugins/kdeconnect/link.py - the link layer: finding devices, keeping TLS links
# to them, pairing. One Link per Sapphire, however many phones.
#
# Shape (tmp/kdeconnect-plan.md section 2):
#   UDP 1716   we broadcast who we are; devices that hear it connect to us.
#              We hear theirs too: a device we know gets our identity back
#              by unicast, so it connects to us. We never connect out for the
#              control link: as the TLS client we can read any certificate,
#              paired or not, which Python's TLS server cannot (section 2.3).
#   TCP        one accepted socket per device: one plaintext identity line,
#              TLS (we are the client), both identities inside TLS, then
#              newline-delimited JSON packets both ways for as long as it holds.
#   payloads   a file rides on a second, short TLS connection: the sender
#              listens on 1739-1764, the receiver connects.
#
# The driver (device_driver.py) gets everything through on_event(kind, peer_id,
# **info): seen, linked, unlinked, packet, pair_request, paired, unpaired,
# pair_failed. Those calls come from this module's threads: keep them short.
import logging
import queue
import socket
import ssl
import threading
import time

from plugins.kdeconnect import protocol as P

logger = logging.getLogger(__name__)

SEEN_FOR = 600          # seconds a device that broadcast counts as here
RECALL = 30             # seconds between our calls to a paired device that is not linked
HANDSHAKE = 5           # seconds for each step of a handshake
RATE = 0.5              # seconds between two answers to the same device (KDE's 500 ms)
MAX_PEERS = 16
MAX_PAYLOAD = 64 * 1024 * 1024
PAYLOAD_WAIT = 30       # seconds we hold a payload port open for the receiver
TICK = 5                # seconds between the keeper's looks


class Identity:
    """Who this Sapphire is on the wire."""

    def __init__(self, cert_file, key_file, name, incoming, outgoing, kind='desktop'):
        self.cert_file, self.key_file = str(cert_file), str(key_file)
        with open(cert_file, 'rb') as f:
            self.cert_der = P.cert_der(f.read())
        self.id = P.cert_cn(self.cert_der)
        self.name = P.clean_name(name)
        self.kind = kind
        self.incoming, self.outgoing = list(incoming), list(outgoing)


class Peer:
    """One linked device and what we know of it. send() and ask() are the
    driver's doors; the rest is the Link's."""

    def __init__(self, link, peer_id, body, der, addr, sock):
        self.link, self.id, self.der, self.addr, self.sock = link, peer_id, der, addr, sock
        self.name = P.clean_name(body.get('deviceName'), peer_id[:8])
        self.kind = str(body.get('deviceType') or 'phone')[:12]
        self.version = int(body.get('protocolVersion') or 0)
        self.caps_in = {str(c) for c in (body.get('incomingCapabilities') or [])}
        self.caps_out = {str(c) for c in (body.get('outgoingCapabilities') or [])}
        self.paired = link.trusted(peer_id) is not None
        self.pair_state = None        # None | ('requested' | 'by_peer', timestamp, until)
        self.readings = {}            # battery and the like, for the driver's status
        self.extra = {}               # the driver's own notes (caches)
        self.since = time.time()
        self._waiters = {}            # packet type -> [Queue]
        self._lock = threading.Lock()
        self.closed = threading.Event()

    def __repr__(self):
        return f"<Peer {self.name} {self.id[:8]} {'paired' if self.paired else 'unpaired'}>"

    def send(self, kind, body=None):
        """One packet. ProtocolError when the link is gone."""
        self._write(P.packet(kind, body))

    def _write(self, data):
        with self._lock:
            try:
                self.sock.sendall(data)
            except (OSError, ssl.SSLError) as e:
                raise P.ProtocolError(f'the link to {self.name} is gone: {e}')

    def send_payload(self, kind, body, data):
        """A packet with bytes behind it: we listen on a payload port, say so
        in the packet, and hand the bytes to the device when it connects.
        Blocks until sent. ProtocolError when it never came."""
        pem = self.link.trusted(self.id)
        if pem is None:
            raise P.ProtocolError('only a paired device takes files')
        srv = self.link._listen(P.PAYLOAD_PORTS)
        try:
            port = srv.getsockname()[1]
            self._write(P.packet(kind, body, len(data), port))
            srv.settimeout(PAYLOAD_WAIT)
            try:
                conn, _ = srv.accept()
            except OSError:
                raise P.ProtocolError(f'{self.name} did not come for the file')
            try:
                ctx = P.server_context(self.link.identity.cert_file, self.link.identity.key_file, pem)
                conn.settimeout(HANDSHAKE)
                tls = ctx.wrap_socket(conn, server_side=True)
                tls.settimeout(120)
                tls.sendall(data)
                try:
                    tls.unwrap()
                except (OSError, ssl.SSLError):
                    pass
                tls.close()
            except (OSError, ssl.SSLError) as e:
                raise P.ProtocolError(f'the file did not reach {self.name}: {e}')
        finally:
            srv.close()

    def download(self, pkt):
        """The bytes a packet announced: connect to the device at the port it
        named, TLS, read exactly payloadSize bytes. ProtocolError on any slip."""
        told = P.payload_of(pkt)
        if not told:
            raise P.ProtocolError('the packet carries no payload')
        size, port = told
        try:
            raw = socket.create_connection((self.addr, port), timeout=10)
        except OSError as e:
            raise P.ProtocolError(f'could not reach {self.name} for the file: {e}')
        tls = None
        try:
            ctx = P.client_context(self.link.identity.cert_file, self.link.identity.key_file)
            raw.settimeout(HANDSHAKE)
            tls = ctx.wrap_socket(raw, server_side=False)
            if tls.getpeercert(True) != self.der:
                raise P.ProtocolError('the file came from a different certificate')
            return P.read_exactly(tls, size, MAX_PAYLOAD, 120)
        except (OSError, ssl.SSLError) as e:
            raise P.ProtocolError(f'the file from {self.name} broke off: {e}')
        finally:
            try:
                (tls or raw).close()
            except OSError:
                pass

    def ask(self, kind, body, want, timeout=8, quiet=None):
        """Send one packet and wait for the answer(s) of type `want`. With
        quiet=None: the first answer within `timeout` seconds, as a list of
        one. With quiet=N: every answer until N seconds pass with none (the
        phone answers one packet per conversation). [] when nothing came."""
        q = queue.Queue()
        with self._lock:
            self._waiters.setdefault(want, []).append(q)
        try:
            self.send(kind, body)
            out, deadline = [], time.monotonic() + timeout
            while True:
                wait = quiet if (quiet is not None and out) else deadline - time.monotonic()
                if wait <= 0:
                    break
                try:
                    out.append(q.get(timeout=wait))
                except queue.Empty:
                    break
                if quiet is None:
                    break
            return out
        finally:
            with self._lock:
                held = self._waiters.get(want, [])
                if q in held:
                    held.remove(q)
                if not held:
                    self._waiters.pop(want, None)

    def feed(self, pkt):
        """Hand a packet to whoever is waiting for its type. True if someone was."""
        with self._lock:
            held = list(self._waiters.get(pkt.get('type'), []))
        for q in held:
            q.put(pkt)
        return bool(held)

    def close(self):
        """End the link. shutdown() first: the reader holds a file on the
        socket, so close() alone would leave it blocked in recv for good."""
        if self.closed.is_set():
            return
        self.closed.set()
        for step in (lambda: self.sock.shutdown(socket.SHUT_RDWR), self.sock.close):
            try:
                step()
            except (OSError, ValueError):
                pass


class Link:
    """The daemon. start() binds and runs its threads; stop() ends them."""

    def __init__(self, identity, on_event, udp_port=P.UDP_PORT, tcp_ports=P.TCP_PORTS, bind='', allow=None,
                 reply_port=None):
        self.identity, self.on_event = identity, on_event
        self.udp_port, self.tcp_ports, self.bind = udp_port, tcp_ports, bind
        self.reply_port = reply_port or udp_port         # where devices listen (one port in life; two in a test)
        self.allow = allow or (lambda ip: True)       # the driver's LAN rule
        self.trust = {}         # peer id -> certificate PEM (bytes) of a paired device
        self.known = {}         # peer id -> the address it was last at
        self.seen = {}          # peer id -> {'name', 'kind', 'addr', 'port', 'ts', 'version'}
        self.peers = {}         # peer id -> Peer
        self.tcp_port = None
        self._udp = self._tcp = None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._answered = {}     # peer id -> monotonic of our last answer to its broadcast
        self._called = {}       # peer id -> monotonic of our last call to it
        self._pending = {}      # peer id -> (pair_state, cert der): a pairing in progress survives a re-link
        self._threads = []

    # --- life ------------------------------------------------------------------

    def trusted(self, peer_id):
        return self.trust.get(peer_id)

    def _listen(self, ports):
        """A TCP server socket on the first free port of the range."""
        for port in range(ports[0], ports[1] + 1):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)   # a restart keeps 1716 (links in TIME_WAIT)
            try:
                s.bind((self.bind, port))
                s.listen(8)
                return s
            except OSError:
                s.close()
        raise P.ProtocolError(f'no free port between {ports[0]} and {ports[1]}')

    def start(self):
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        udp.bind((self.bind, self.udp_port))
        udp.settimeout(1.0)
        self._udp = udp
        self._tcp = self._listen(self.tcp_ports)
        self._tcp.settimeout(1.0)
        self.tcp_port = self._tcp.getsockname()[1]
        for fn in (self._udp_loop, self._accept_loop, self._keeper):
            t = threading.Thread(target=fn, daemon=True, name=f'kdeconnect-{fn.__name__.strip("_")}')
            t.start()
            self._threads.append(t)
        logger.info(f"[kdeconnect] listening: UDP {self.udp_port}, TCP {self.tcp_port}, as "
                    f"'{self.identity.name}' ({self.identity.id[:8]}...)")
        self.broadcast(why='started')

    def stop(self):
        self._stop.set()
        with self._lock:
            peers = list(self.peers.values())
            self.peers.clear()
        for p in peers:
            p.close()
        for s in (self._udp, self._tcp):
            try:
                s.close()
            except (OSError, AttributeError):
                pass
        for t in self._threads:
            t.join(timeout=3)
        logger.info("[kdeconnect] stopped")

    def _emit(self, kind, peer_id, **info):
        try:
            self.on_event(kind, peer_id, **info)
        except Exception as e:
            logger.error(f"[kdeconnect] the driver failed on '{kind}' from {peer_id[:8]}: {e}", exc_info=True)

    # --- UDP: being found ------------------------------------------------------

    def _broadcasts(self):
        """Where a broadcast goes: the general address, and each interface's
        own (192.168.0.255) when they can be read; some networks pass one and
        not the other."""
        out = {'<broadcast>'}
        try:
            import psutil
            for addrs in psutil.net_if_addrs().values():
                for a in addrs:
                    if a.family == socket.AF_INET and a.broadcast and not str(a.address).startswith('127.'):
                        out.add(a.broadcast)
        except Exception:
            pass
        return sorted(out)

    def broadcast(self, to=None, why=''):
        """Our identity, to everyone (and to every device we know the address
        of, by name: a sleeping phone does not hear a broadcast every time).
        Every send is logged: a device links to us again whenever it hears
        us, so who said our name, and why, is worth knowing."""
        data = P.udp_identity(self.identity.id, self.identity.name, self.tcp_port)
        targets = [to] if to else [(b, self.reply_port) for b in self._broadcasts()] + \
            [(addr, self.reply_port) for addr in set(self.known.values())]
        logger.info(f"[kdeconnect] said our name to {', '.join(t[0] for t in targets)}" + (f" ({why})" if why else ''))
        for target in targets:
            try:
                self._udp.sendto(data, target)
            except OSError as e:
                logger.debug(f"[kdeconnect] broadcast to {target} failed: {e}")

    def call(self, peer_id):
        """Make a device we know link to us: our identity, straight to its last
        address. Rate limited. False when we have no address for it."""
        addr = self.known.get(peer_id)
        if not addr:
            return False
        now = time.monotonic()
        if now - self._called.get(peer_id, 0) < RECALL:
            return True
        self._called[peer_id] = now
        self.broadcast((addr, self.reply_port), why='called: paired and not linked')
        return True

    def _udp_loop(self):
        while not self._stop.is_set():
            try:
                data, (ip, _) = self._udp.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                if self._stop.is_set():
                    return
                time.sleep(0.5)
                continue
            try:
                self._heard(data, ip)
            except Exception as e:
                logger.debug(f"[kdeconnect] a broadcast from {ip} was not understood: {e}")

    def _heard(self, data, ip):
        pkt = P.parse(data.rstrip(b'\n'))
        body = pkt['body']
        peer_id = body.get('deviceId')
        if pkt['type'] != P.IDENTITY or not P.valid_id(peer_id) or peer_id == self.identity.id:
            return
        if not self.allow(ip):
            logger.debug(f"[kdeconnect] {ip} is not on the local network: ignored")
            return
        version = int(body.get('protocolVersion') or 0)
        with self._lock:
            new = peer_id not in self.seen
            self.seen[peer_id] = {'name': P.clean_name(body.get('deviceName'), peer_id[:8]), 'kind': '',
                                  'addr': ip, 'port': body.get('tcpPort'), 'ts': time.time(), 'version': version}
            self.known[peer_id] = ip
            linked = peer_id in self.peers
            answered = self._answered.get(peer_id, 0)
            self._answered[peer_id] = time.monotonic()
        if new:
            logger.info(f"[kdeconnect] found '{self.seen[peer_id]['name']}' at {ip} (protocol {version})")
            self._emit('seen', peer_id)
        if version < P.PROTOCOL:
            if new:
                logger.warning(f"[kdeconnect] '{self.seen[peer_id]['name']}' speaks protocol {version}; "
                               f"this needs 8. Update KDE Connect on it.")
            return
        # it connects to us when it hears us: answer by name, once in a while
        if not linked and time.monotonic() - answered > RATE:
            self.broadcast((ip, self.reply_port), why='answering its broadcast')
        elif linked:
            logger.info(f"[kdeconnect] '{self.seen[peer_id]['name']}' said its name again while linked (left alone)")

    # --- TCP: a device links to us ----------------------------------------------

    def _accept_loop(self):
        while not self._stop.is_set():
            try:
                sock, addr = self._tcp.accept()
            except socket.timeout:
                continue
            except OSError:
                if self._stop.is_set():
                    return
                time.sleep(0.5)
                continue
            threading.Thread(target=self._incoming, args=(sock, addr[0]), daemon=True,
                             name='kdeconnect-handshake').start()

    def _incoming(self, sock, ip):
        """The whole handshake with a device that connected to us (section 2.2)."""
        tls = None
        try:
            if not self.allow(ip):
                raise P.ProtocolError('not on the local network')
            body = P.parse(P.read_line(sock, P.MAX_IDENTITY, HANDSHAKE))
            if body['type'] != P.IDENTITY:
                raise P.ProtocolError('the first line was not an identity')
            body = body['body']
            peer_id = body.get('deviceId')
            version = int(body.get('protocolVersion') or 0)
            if version < P.PROTOCOL:
                raise P.ProtocolError(f"'{body.get('deviceName')}' speaks protocol {version}; this needs 8. "
                                      f"Update KDE Connect on it")
            if not P.valid_id(peer_id) or peer_id == self.identity.id:
                shown = repr(str(peer_id)[:12]) + f'... ({len(str(peer_id))} chars)' if peer_id else 'none'
                raise P.ProtocolError(f'no usable device id: {shown}, protocol {version}')
            if body.get('targetDeviceId') and body['targetDeviceId'] != self.identity.id:
                raise P.ProtocolError('meant for another device')
            with self._lock:
                if peer_id not in self.peers and len(self.peers) >= MAX_PEERS:
                    raise P.ProtocolError('too many devices are linked')
            ctx = P.client_context(self.identity.cert_file, self.identity.key_file)
            sock.settimeout(HANDSHAKE)
            tls = ctx.wrap_socket(sock, server_side=False)
            der = tls.getpeercert(True)
            pem = self.trusted(peer_id)
            if pem is not None and der != P.cert_der(pem):
                raise P.ProtocolError('its certificate is not the one it was paired with. Unpair, then pair again')
            tls.sendall(P.full_identity(self.identity.id, self.identity.name, self.identity.kind,
                                        self.identity.incoming, self.identity.outgoing))
            told = P.parse(P.read_line(tls, P.MAX_IDENTITY, HANDSHAKE))
            full = told['body']
            if told['type'] != P.IDENTITY or full.get('deviceId') != peer_id \
                    or int(full.get('protocolVersion') or 0) != version:
                raise P.ProtocolError('the identity changed halfway through the handshake')
            cn = P.cert_cn(der)
            if cn and cn != peer_id:
                raise P.ProtocolError(f"its certificate is for '{cn[:12]}...', not for the id it gave")
            peer = Peer(self, peer_id, full, der, ip, tls)
            held = self._pending.get(peer_id)
            if held and held[1] == der:          # the phone re-linked mid-pairing (it does, right after asking)
                peer.pair_state = held[0]
            tls.settimeout(None)
            try:
                tls.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                for opt, val in (('TCP_KEEPIDLE', 30), ('TCP_KEEPINTVL', 10), ('TCP_KEEPCNT', 3)):
                    if hasattr(socket, opt):
                        tls.setsockopt(socket.IPPROTO_TCP, getattr(socket, opt), val)
            except OSError:
                pass
            with self._lock:
                old = self.peers.get(peer_id)
                self.peers[peer_id] = peer
                self.seen[peer_id] = {'name': peer.name, 'kind': peer.kind, 'addr': ip, 'port': None,
                                      'ts': time.time(), 'version': version}
                self.known[peer_id] = ip
            if old is not None:
                logger.info(f"[kdeconnect] '{peer.name}' linked again while its link of "
                            f"{time.time() - old.since:.0f} s was still up: the old one is closed")
                old.close()               # it linked again: the old link is the stale one
            threading.Thread(target=self._reader, args=(peer,), daemon=True, name='kdeconnect-reader').start()
            logger.info(f"[kdeconnect] linked: '{peer.name}' ({peer.kind}) at {ip}, "
                        f"{'paired' if peer.paired else 'not paired'}")
            self._emit('linked', peer_id)
        except (P.ProtocolError, OSError, ssl.SSLError, ValueError) as e:
            logger.info(f"[kdeconnect] a connection from {ip} was dropped: {e}")
            try:
                (tls or sock).close()
            except OSError:
                pass

    def _reader(self, peer):
        f = peer.sock.makefile('rb')
        why = 'closed by the device'
        try:
            while not peer.closed.is_set():
                line = f.readline(P.MAX_LINE + 1)
                if not line:
                    break
                if not line.endswith(b'\n'):
                    why = 'a line over the size limit'
                    break
                if line.strip():
                    try:
                        self._dispatch(peer, P.parse(line))
                    except P.ProtocolError as e:
                        logger.debug(f"[kdeconnect] {peer.name}: a line was not a packet: {e}")
        except (OSError, ssl.SSLError, ValueError) as e:
            why = str(e) or type(e).__name__
        finally:
            was_open = not peer.closed.is_set()
            peer.close()
            with self._lock:
                mine = self.peers.get(peer.id) is peer
                if mine:
                    self.peers.pop(peer.id, None)
            if was_open and mine:
                logger.info(f"[kdeconnect] unlinked: '{peer.name}' ({why})")
            if mine:
                self._emit('unlinked', peer.id)

    def _dispatch(self, peer, pkt):
        if pkt['type'] == P.PAIR:
            self._pair_packet(peer, pkt['body'])
            return
        if not peer.paired:
            logger.debug(f"[kdeconnect] {peer.name}: {pkt['type']} while not paired: ignored")
            return
        fed = peer.feed(pkt)
        self._emit('packet', peer.id, pkt=pkt, peer=peer, fed=fed)

    # --- pairing -------------------------------------------------------------------

    def _set_pairing(self, peer, state):
        peer.pair_state = state
        if state is None:
            self._pending.pop(peer.id, None)
        else:
            self._pending[peer.id] = (state, peer.der)

    def request_pair(self, peer_id):
        """Ask the device to pair. Returns the key both screens show."""
        peer = self.peers.get(peer_id)
        if peer is None:
            raise P.ProtocolError('the device is not linked right now')
        if peer.paired:
            raise P.ProtocolError('already paired')
        if peer.pair_state and peer.pair_state[0] == 'by_peer':
            return self.accept_pair(peer_id)
        ts = int(time.time())
        peer.send(P.PAIR, {'pair': True, 'timestamp': ts})
        self._set_pairing(peer, ('requested', ts, time.monotonic() + P.PAIR_WAIT))
        return P.verification_key(self.identity.cert_der, peer.der, ts)

    def accept_pair(self, peer_id):
        """Accept the device's own request. Returns the key, for the record."""
        peer = self.peers.get(peer_id)
        if peer is None or not peer.pair_state or peer.pair_state[0] != 'by_peer':
            raise P.ProtocolError('the device has not asked to pair')
        ts = peer.pair_state[1]
        peer.send(P.PAIR, {'pair': True})
        self._paired(peer)
        return P.verification_key(self.identity.cert_der, peer.der, ts)

    def pending_key(self, peer_id):
        """(state, key) of a pairing in progress with this device, or None.
        Read from the link when it is up, else from what survives a re-link."""
        peer = self.peers.get(peer_id)
        if peer is not None and peer.pair_state:
            state, ts, _ = peer.pair_state
            return state, P.verification_key(self.identity.cert_der, peer.der, ts)
        held = self._pending.get(peer_id)
        if held:
            (state, ts, _), der = held
            return state, P.verification_key(self.identity.cert_der, der, ts)
        return None

    def unpair(self, peer_id):
        """Forget the device. It is told when it is linked; the link is closed
        (it comes back unpaired, as an available device)."""
        peer = self.peers.get(peer_id)
        self.trust.pop(peer_id, None)
        self._pending.pop(peer_id, None)
        if peer is not None:
            try:
                peer.send(P.PAIR, {'pair': False})
            except P.ProtocolError:
                pass
            peer.paired, peer.pair_state = False, None
            peer.close()
        self._emit('unpaired', peer_id)

    def _paired(self, peer):
        self.trust[peer.id] = P.cert_pem(peer.der).encode('ascii')
        peer.paired = True
        self._set_pairing(peer, None)
        logger.info(f"[kdeconnect] paired with '{peer.name}'")
        self._emit('paired', peer.id, name=peer.name, device_kind=peer.kind, cert=self.trust[peer.id])

    def _pair_packet(self, peer, body):
        now = int(time.time())
        if body.get('pair'):
            if peer.paired:
                return
            state = peer.pair_state[0] if peer.pair_state else None
            if state == 'requested':                  # the accept, or both asked at once
                self._paired(peer)
            elif state == 'by_peer':
                logger.debug(f"[kdeconnect] {peer.name} asked again: still waiting on the page")
            else:
                ts = body.get('timestamp')
                if not isinstance(ts, (int, float)):
                    logger.info(f"[kdeconnect] {peer.name} asked to pair without a timestamp (old app): refused")
                    peer.send(P.PAIR, {'pair': False})
                    return
                if abs(int(ts) - now) > P.PAIR_SKEW:
                    logger.warning(f"[kdeconnect] {peer.name} asked to pair but its clock is "
                                   f"{abs(int(ts) - now)} s off: refused")
                    peer.send(P.PAIR, {'pair': False})
                    self._emit('pair_failed', peer.id, why='the clocks of the two devices disagree by more than 30 minutes')
                    return
                self._set_pairing(peer, ('by_peer', int(ts), time.monotonic() + 25))
                key = P.verification_key(self.identity.cert_der, peer.der, int(ts))
                logger.info(f"[kdeconnect] '{peer.name}' asks to pair; key {key}. Accept in Settings > Devices.")
                self._emit('pair_request', peer.id, key=key, name=peer.name)
        else:
            if peer.paired:
                logger.info(f"[kdeconnect] '{peer.name}' unpaired from us")
                self.trust.pop(peer.id, None)
                peer.paired = False
                self._emit('unpaired', peer.id)
                peer.close()
            elif peer.pair_state:
                self._set_pairing(peer, None)
                logger.info(f"[kdeconnect] '{peer.name}' declined the pairing")
                self._emit('pair_failed', peer.id, why='declined on the device')

    # --- the keeper ----------------------------------------------------------------

    def _keeper(self):
        last_broadcast = time.monotonic()
        while not self._stop.wait(TICK):
            now = time.monotonic()
            try:
                for peer_id, (state, _) in list(self._pending.items()):
                    if now > state[2]:
                        self._pending.pop(peer_id, None)
                        peer = self.peers.get(peer_id)
                        if peer is not None:
                            peer.pair_state = None
                            if state[0] == 'requested':
                                try:
                                    peer.send(P.PAIR, {'pair': False})
                                except P.ProtocolError:
                                    pass
                        logger.info(f"[kdeconnect] the pairing with '{peer_id[:8]}...' timed out")
                        self._emit('pair_failed', peer_id, why='timed out: accept on the phone within 30 seconds of pressing Pair')
                with self._lock:
                    gone = [pid for pid, s in self.seen.items()
                            if pid not in self.peers and time.time() - s['ts'] > SEEN_FOR]
                    for pid in gone:
                        self.seen.pop(pid, None)
                for pid in gone:
                    logger.info(f"[kdeconnect] '{pid[:8]}...' has not been heard from for a while")
                    self._emit('seen', pid)
                missing = [pid for pid in self.trust if pid not in self.peers]
                if missing and now - last_broadcast > RECALL:
                    last_broadcast = now
                    self.broadcast(why='a paired device is not linked')
            except Exception as e:
                logger.error(f"[kdeconnect] keeper: {e}", exc_info=True)
