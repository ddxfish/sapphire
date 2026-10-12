# plugins/kdeconnect/tests/test_kdeconnect.py - the protocol on the loopback.
#
# The Link under test is Sapphire's side. The other side is a FakePhone that
# does what the Android app does: hears an identity, connects over TCP, writes
# its connection packet, is the TLS SERVER (wanting the client's certificate),
# exchanges identities, then pairs, pings, shares. Roles are the real ones.
import json
import socket
import ssl
import threading
import time

import pytest

from plugins.kdeconnect import link as L
from plugins.kdeconnect import protocol as P


def free_udp_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def identity(tmp_path, name, incoming=('kdeconnect.ping', 'kdeconnect.share.request', 'kdeconnect.battery'),
             outgoing=('kdeconnect.ping', 'kdeconnect.share.request', 'kdeconnect.battery.request')):
    key_pem, cert_pem = P.make_identity(P.new_id(), days=30)
    (tmp_path / f'{name}.key').write_bytes(key_pem)
    (tmp_path / f'{name}.crt').write_bytes(cert_pem)
    return L.Identity(tmp_path / f'{name}.crt', tmp_path / f'{name}.key', name, incoming, outgoing)


class Events:
    def __init__(self):
        self.items, self.cv = [], threading.Condition()

    def __call__(self, kind, peer_id, **info):
        with self.cv:
            self.items.append((kind, peer_id, info))
            self.cv.notify_all()

    def wait(self, kind, timeout=8):
        end = time.monotonic() + timeout
        with self.cv:
            while True:
                hit = next((i for i in self.items if i[0] == kind), None)
                if hit:
                    self.items.remove(hit)
                    return hit
                left = end - time.monotonic()
                if left <= 0:
                    raise AssertionError(f"no '{kind}' event; had {[i[0] for i in self.items]}")
                self.cv.wait(left)


class FakePhone:
    """The Android side, as far as the handshake, pairing and payloads go.
    `trusts` is the Sapphire certificate it will take (a phone that is not
    paired takes any; Python's TLS server cannot, so the test tells it)."""

    def __init__(self, ident, udp_port, trusts_pem):
        self.ident, self.udp_port, self.trusts = ident, udp_port, trusts_pem
        self.packets = Events()                 # ('packet', sapphire id, pkt=)
        self.sock = None
        self.paired = False
        self.pair_ts = None
        self.peer_der = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.udp.bind(('127.0.0.1', udp_port))
        self.udp.settimeout(0.5)
        threading.Thread(target=self._udp_loop, daemon=True).start()

    def _ctx(self):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.maximum_version = ssl.TLSVersion.TLSv1_2      # Android caps at 1.2
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
        ctx.load_verify_locations(cadata=self.trusts.decode('ascii'))
        ctx.load_cert_chain(self.ident.cert_file, self.ident.key_file)
        return ctx

    def _udp_loop(self):
        while not self._stop.is_set():
            try:
                data, (ip, _) = self.udp.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                return
            pkt = P.parse(data.rstrip(b'\n'))
            if pkt['body'].get('deviceId') == self.ident.id or self.sock is not None:
                continue
            threading.Thread(target=self._connect, args=(ip, pkt['body']), daemon=True).start()

    def _connect(self, ip, body):
        raw = socket.create_connection((ip, body['tcpPort']), timeout=5)
        try:
            raw.sendall(P.connection_packet(self.ident.id, self.ident.name, body['deviceId'], body['protocolVersion']))
            tls = self._ctx().wrap_socket(raw, server_side=True)
            self.peer_der = tls.getpeercert(True)
            tls.sendall(P.full_identity(self.ident.id, self.ident.name, 'phone', self.ident.incoming, self.ident.outgoing))
            told = P.parse(P.read_line(tls, P.MAX_IDENTITY, 5))
            assert told['body']['deviceId'] == body['deviceId']
        except (OSError, P.ProtocolError, ssl.SSLError):
            raw.close()                      # refused by Sapphire (a stranger's certificate, an old protocol)
            return
        self.sapphire_id = body['deviceId']
        tls.settimeout(None)
        self.sock = tls
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        f = self.sock.makefile('rb')
        try:
            for line in iter(lambda: f.readline(P.MAX_LINE), b''):
                pkt = P.parse(line)
                if pkt['type'] == P.PAIR:
                    if pkt['body'].get('pair'):
                        if self.pair_ts is None:                   # a request from Sapphire
                            self.pair_ts = pkt['body']['timestamp']
                        self.packets('pair', self.sapphire_id, pkt=pkt)
                    else:
                        self.paired = False
                        self.packets('unpair', self.sapphire_id, pkt=pkt)
                    continue
                self.packets('packet', self.sapphire_id, pkt=pkt)
        except (OSError, ValueError):
            pass
        finally:
            with self._lock:
                self.sock = None

    def key(self):
        return P.verification_key(P.cert_der(open(self.ident.cert_file, 'rb').read()), self.peer_der, self.pair_ts)

    def send(self, kind, body=None):
        with self._lock:
            self.sock.sendall(P.packet(kind, body))

    def request_pair(self):
        self.pair_ts = int(time.time())
        self.send(P.PAIR, {'pair': True, 'timestamp': self.pair_ts})

    def accept_pair(self):
        self.send(P.PAIR, {'pair': True})
        self.paired = True

    def share(self, filename, data):
        """A file to Sapphire: listen, announce, serve over TLS when it comes."""
        srv = socket.socket()
        srv.bind(('127.0.0.1', 0))
        srv.listen(1)
        srv.settimeout(10)
        with self._lock:
            self.sock.sendall(P.packet('kdeconnect.share.request', {'filename': filename}, len(data), srv.getsockname()[1]))
        conn, _ = srv.accept()
        tls = self._ctx().wrap_socket(conn, server_side=True)
        tls.sendall(data)
        tls.close()
        srv.close()

    def fetch(self, pkt):
        """A file from Sapphire: connect to the port it named, TLS client."""
        size, port = P.payload_of(pkt)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        ctx.load_cert_chain(self.ident.cert_file, self.ident.key_file)
        tls = ctx.wrap_socket(socket.create_connection(('127.0.0.1', port), timeout=5))
        out = b''
        while len(out) < size:
            chunk = tls.recv(65536)
            if not chunk:
                break
            out += chunk
        tls.close()
        return out

    def drop(self):
        """Drop the link the way a phone does: a FIN, not a silent close."""
        s = self.sock
        if s:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            s.close()

    def close(self):
        self._stop.set()
        self.udp.close()
        self.drop()


@pytest.fixture
def rig(tmp_path):
    ev = Events()
    sapph_port, phone_port = free_udp_port(), free_udp_port()
    sapphire = L.Link(identity(tmp_path, 'Sapphire test'), ev, udp_port=sapph_port, tcp_ports=(20100, 20160),
                      bind='127.0.0.1', reply_port=phone_port)
    phone = FakePhone(identity(tmp_path, 'pixel'), phone_port, open(sapphire.identity.cert_file, 'rb').read())
    sapphire.start()
    try:
        yield sapphire, ev, phone
    finally:
        phone.close()
        sapphire.stop()


def linked(sapphire, ev, phone):
    sapphire.broadcast(('127.0.0.1', phone.udp_port))
    ev.wait('linked')
    end = time.monotonic() + 3                 # the phone's side of the handshake ends a moment later
    while phone.sock is None and time.monotonic() < end:
        time.sleep(0.05)
    assert phone.sock is not None
    peer = sapphire.peers[phone.ident.id]
    assert peer.name == 'pixel' and peer.kind == 'phone' and 'kdeconnect.ping' in peer.caps_in
    return peer


def test_packet_round_trip():
    raw = P.packet('kdeconnect.ping', {'message': 'hi'}, 12, 1740)
    assert raw.endswith(b'\n')
    pkt = P.parse(raw)
    assert pkt['type'] == 'kdeconnect.ping' and pkt['body'] == {'message': 'hi'}
    assert P.payload_of(pkt) == (12, 1740)
    assert P.payload_of(P.parse(P.packet('kdeconnect.ping'))) is None
    with pytest.raises(P.ProtocolError):
        P.parse(b'{"type": "http.get"}')
    assert json.loads(P.udp_identity('a' * 32, 'x', 1716))['body']['tcpPort'] == 1716


def test_names_and_ids():
    assert P.clean_name('Sapphire (Smokey)!') == 'Sapphire Smokey'
    assert P.clean_name('') == 'Sapphire'
    assert len(P.clean_name('x' * 50)) == 32
    assert P.valid_id(P.new_id()) and not P.valid_id('short') and not P.valid_id('a' * 40)


def test_certificate_and_key():
    key, cert = P.make_identity('abcdef0123456789abcdef0123456789', days=5)
    der = P.cert_der(cert)
    assert P.cert_cn(der) == 'abcdef0123456789abcdef0123456789'
    other = P.cert_der(P.make_identity(P.new_id(), days=5)[1])
    k1 = P.verification_key(der, other, 1700000000)
    assert k1 == P.verification_key(other, der, 1700000000), 'the same key from either side'
    assert len(k1) == 8 and k1 == k1.upper()
    assert k1 != P.verification_key(der, other, 1700000001), 'the timestamp is in the hash'


def test_link_sapphire_asks_to_pair(rig):
    sapphire, ev, phone = rig
    peer = linked(sapphire, ev, phone)
    assert not peer.paired
    # an unpaired phone's packets are ignored
    phone.send('kdeconnect.ping', {'message': 'too early'})
    time.sleep(0.3)
    assert not any(i[0] == 'packet' for i in ev.items)
    key = sapphire.request_pair(phone.ident.id)
    phone.packets.wait('pair')
    assert phone.key() == key, 'both screens show the same key'
    assert sapphire.pending_key(phone.ident.id) == ('requested', key)
    phone.accept_pair()
    _, who, info = ev.wait('paired')
    assert who == phone.ident.id and info['name'] == 'pixel'
    assert peer.paired and sapphire.trust[phone.ident.id] == open(phone.ident.cert_file, 'rb').read()
    # packets flow now
    phone.send('kdeconnect.battery', {'currentCharge': 73, 'isCharging': True})
    _, who, info = ev.wait('packet')
    assert info['pkt']['body']['currentCharge'] == 73
    # ask and answer
    def answer():
        _, _, got = phone.packets.wait('packet')
        assert got['pkt']['type'] == 'kdeconnect.battery.request'
        phone.send('kdeconnect.battery', {'currentCharge': 74, 'isCharging': False})
    threading.Thread(target=answer, daemon=True).start()
    got = peer.ask('kdeconnect.battery.request', {'request': True}, 'kdeconnect.battery', timeout=5)
    assert got and got[0]['body']['currentCharge'] == 74
    # unpair from Sapphire: the phone is told
    sapphire.unpair(phone.ident.id)
    phone.packets.wait('unpair')
    ev.wait('unpaired')
    assert phone.ident.id not in sapphire.trust


def test_link_phone_asks_to_pair_and_shares(rig):
    sapphire, ev, phone = rig
    peer = linked(sapphire, ev, phone)
    phone.request_pair()
    _, who, info = ev.wait('pair_request')
    assert who == phone.ident.id and info['key'] == phone.key()
    assert sapphire.pending_key(phone.ident.id) == ('by_peer', info['key'])
    assert sapphire.accept_pair(phone.ident.id) == info['key']
    phone.packets.wait('pair')               # the accept
    phone.paired = True
    ev.wait('paired')
    assert peer.paired
    # the phone shares a picture: Sapphire downloads it over a second TLS connection
    data = bytes(range(256)) * 300
    threading.Thread(target=phone.share, args=('pic.jpg', data), daemon=True).start()
    _, _, info = ev.wait('packet')
    assert info['pkt']['body']['filename'] == 'pic.jpg'
    assert peer.download(info['pkt']) == data
    # Sapphire sends a file to the phone
    got = {}

    def take():
        _, _, info = phone.packets.wait('packet')
        got['data'] = phone.fetch(info['pkt'])
    t = threading.Thread(target=take, daemon=True)
    t.start()
    peer.send_payload('kdeconnect.share.request', {'filename': 'note.txt'}, b'hello phone')
    t.join(10)
    assert got.get('data') == b'hello phone'


def test_paired_phone_relinks_and_a_stranger_is_refused(rig):
    sapphire, ev, phone = rig
    peer = linked(sapphire, ev, phone)
    sapphire.request_pair(phone.ident.id)
    phone.packets.wait('pair')
    phone.accept_pair()
    ev.wait('paired')
    # the link drops; the phone comes back and is paired at once
    peer.close()
    ev.wait('unlinked')
    time.sleep(0.3)
    sapphire.broadcast(('127.0.0.1', phone.udp_port))
    ev.wait('linked')
    assert sapphire.peers[phone.ident.id].paired
    # a stranger with the phone's id but another certificate is refused
    sapphire.trust[phone.ident.id] = P.make_identity(phone.ident.id, days=5)[1]
    sapphire.peers[phone.ident.id].close()
    ev.wait('unlinked')
    time.sleep(0.3)
    sapphire.broadcast(('127.0.0.1', phone.udp_port))
    time.sleep(1.5)
    assert phone.ident.id not in sapphire.peers


def test_old_protocol_is_refused(rig):
    sapphire, ev, phone = rig
    raw = socket.create_connection(('127.0.0.1', sapphire.tcp_port), timeout=5)
    raw.sendall(P.packet(P.IDENTITY, {'deviceId': 'b' * 32, 'deviceName': 'old', 'protocolVersion': 7}))
    time.sleep(0.5)
    raw.settimeout(2)
    assert raw.recv(10) == b''          # closed on us
    assert 'b' * 32 not in sapphire.peers


def test_driver_helpers():
    from plugins.kdeconnect import device_driver as d
    assert d._vcard('BEGIN:VCARD\r\nFN:Dana Scully\r\nTEL;TYPE=CELL:+1 (555) 123-4567\r\nitem1.TEL:555-0000\r\nEND:VCARD') \
        == ('Dana Scully', ['+1 (555) 123-4567', '555-0000'])
    assert d._digits('+1 (555) 123-4567') == '5551234567'
    assert d._who('+15551234567', {'5551234567': 'Dana'}) == 'Dana (+15551234567)'
    # loose numbers: Dad's number saved without its area code still matches
    assert d._same_number('555-1234', '+1 (555) 555-1234') and d._same_number('15555551234', '5555551234')
    assert not d._same_number('1234', '+15555551234') and not d._same_number('5551234', '5559999')
    assert d._name_of('5551234', {'5555551234': 'Dad'}) == 'Dad'
    assert d._name_of('5551234', {'5559999999': 'Dana'}) == ''
    assert d._count_and_who('Dad 30', 20) == ('Dad', 30) and d._count_and_who('5551234', 20) == ('5551234', 20)
    assert d._count_and_who('+1 555 123 4567 5', 20) == ('+1 555 123 4567', 5)
    from datetime import datetime
    now = datetime(2026, 10, 12, 18, 0)
    assert d._ago(datetime(2026, 10, 12, 14, 2).timestamp() * 1000, now) == 'today 14:02'
    assert d._ago(datetime(2026, 10, 11, 9, 10).timestamp() * 1000, now) == 'yesterday 09:10'
    assert d._ago(datetime(2026, 10, 3, 18, 40).timestamp() * 1000, now).startswith('Oct 3')
    assert d._ago(datetime(2025, 12, 1, 8, 0).timestamp() * 1000, now) == '2025-12-01'
    assert d._ago(None) == 'some time'
    # send targets: a number minus punctuation and the country prefix; a name only whole
    class Fake:
        caps_in = {'kdeconnect.contacts.request_all_uids_timestamps'}
        extra = {'contacts': {'5555551234': 'Dad'}, 'contacts_names': {'dad': ['+1 (555) 555-1234'], 'dana scully': ['555-0000']},
                 'contacts_ts': time.time()}
    assert d._number_of(Fake(), '+1 (555) 555-1234') == ('5555551234', '')
    assert d._number_of(Fake(), '555.555.1234') == ('5555551234', '')
    assert d._number_of(Fake(), 'Dad') == ('15555551234', '')
    assert d._number_of(Fake(), 'dana')[0] == '' and 'Dana Scully' in d._number_of(Fake(), 'dana')[1]
    assert d._number_of(Fake(), 'nobody')[0] == ''
    caps = d.describe({'id': 'p'}, {})
    assert caps['link']['tab'] == 'Status' and set(caps['link']['actions']) == {'pair', 'unpair'}
    assert set(caps['notify']['actions']) == {'ping', 'find'} and 'say' not in caps['notify']['actions']
    assert d._safe_name('../../etc/passwd') == 'passwd'
    assert d._document_text('.txt', b'hello') == 'hello'
    assert d._document_text('.bin', b'\x00\x01') is None
    assert d._peer_id({'phone': {'all': False, 'only': [{'id': 'abc', 'name': 'x'}]}}) == 'abc'
    assert d._peer_id({'phone': {'all': True, 'only': [{'id': 'abc'}]}}) == ''
    cfg, err = d.validate({'phone': {'all': True, 'only': []}})
    assert err


def test_a_pairing_survives_the_phone_relinking(rig):
    """The Galaxy S22 asked to pair and 50 ms later dropped the link and came
    back (2026-10-12). The request has to be waiting on the new link, and
    accepting has to reach the phone."""
    sapphire, ev, phone = rig
    peer = linked(sapphire, ev, phone)
    phone.request_pair()
    _, _, info = ev.wait('pair_request')
    key = info['key']
    # the phone drops the link and links again, same certificate
    phone.drop()
    ev.wait('unlinked')
    end = time.monotonic() + 3
    while phone.sock is not None and time.monotonic() < end:
        time.sleep(0.05)
    sapphire.broadcast(('127.0.0.1', phone.udp_port))
    ev.wait('linked')
    end = time.monotonic() + 3
    while phone.sock is None and time.monotonic() < end:
        time.sleep(0.05)
    assert sapphire.peers[phone.ident.id] is not peer
    assert sapphire.pending_key(phone.ident.id) == ('by_peer', key), 'the request is still waiting'
    assert sapphire.accept_pair(phone.ident.id) == key
    phone.packets.wait('pair')
    ev.wait('paired')
    assert sapphire.peers[phone.ident.id].paired and not sapphire._pending


def test_pairing_happens_in_the_pick_list(rig, monkeypatch):
    """The Add window: the phone's line says its state and carries the button
    for the next step; setup() does it; validate refuses an unpaired phone."""
    from plugins.kdeconnect import device_driver as d
    sapphire, ev, phone = rig
    monkeypatch.setattr(d, '_link', sapphire)              # the driver's one Link is the test's
    monkeypatch.setattr(d, '_trusted', lambda: {})
    peer = linked(sapphire, ev, phone)
    pid = phone.ident.id
    item = d._found_item(pid, 'pixel', peer, sapphire.pending_key(pid), False)
    assert item['kind'] == 'NOT paired' and item['actions'] == [{'do': 'pair', 'label': 'Pair'}]
    cfg, err = d.validate({'phone': {'all': False, 'only': [{'id': pid, 'name': 'pixel'}]}})
    assert 'not paired yet' in err
    # Sapphire asks; the phone's side shows the same key; the line says to accept on the phone
    text, ok = d.setup(pid, 'pair', {})
    assert ok and 'check that the key reads' in text
    phone.packets.wait('pair')
    item = d._found_item(pid, 'pixel', peer, sapphire.pending_key(pid), False)
    assert item['kind'] == 'pairing' and phone.key() in item['note'] and item['actions'][0]['do'] == 'pair'
    phone.accept_pair()
    ev.wait('paired')
    item = d._found_item(pid, 'pixel', peer, sapphire.pending_key(pid), True)
    assert item['kind'] == 'paired' and 'actions' not in item
    cfg, err = d.validate({'phone': {'all': False, 'only': [{'id': pid, 'name': 'pixel'}]}})
    assert err == '' and cfg['phone']['only'][0]['id'] == pid
    assert d.setup(pid, 'pair', {}) == ('pixel is paired. Add it.', True)
    assert d.setup('nobody', 'pair', {})[1] is False
    # "whichever phone is here" with one paired phone linked: that one (Krem never chose it in the list, 2026-10-12)
    cfg, err = d.validate({'phone': {'all': True, 'only': []}})
    assert err == '' and cfg['phone'] == {'all': False, 'only': [{'id': pid, 'name': 'pixel'}]}


def test_the_phone_asks_first_and_the_pick_list_accepts(rig, monkeypatch):
    from plugins.kdeconnect import device_driver as d
    sapphire, ev, phone = rig
    monkeypatch.setattr(d, '_link', sapphire)
    peer = linked(sapphire, ev, phone)
    pid = phone.ident.id
    assert d.setup(pid, 'accept', {}) == ('The phone has not asked to pair. Press Pair instead.', False)
    phone.request_pair()
    _, _, info = ev.wait('pair_request')
    item = d._found_item(pid, 'pixel', peer, sapphire.pending_key(pid), False)
    assert item['kind'] == 'asks to pair' and info['key'] in item['note']
    assert item['actions'] == [{'do': 'accept', 'label': 'Accept pairing'}]
    text, ok = d.setup(pid, 'accept', {})
    assert ok and 'Paired with pixel' in text and info['key'] in text
    phone.packets.wait('pair')
    ev.wait('paired')


def test_conversations_come_slowly_and_are_kept(rig, monkeypatch):
    """The Galaxy S22 sends one packet per conversation with gaps, and works
    requests off one at a time. The list waits them out, merges across
    re-links, answers from the cache, and never sends two requests at once."""
    from plugins.kdeconnect import device_driver as d
    monkeypatch.setattr(d, 'CONV_FIRST', 4)
    monkeypatch.setattr(d, 'CONV_QUIET', 1.2)
    sapphire, ev, phone = rig
    peer = linked(sapphire, ev, phone)
    sapphire.request_pair(phone.ident.id)
    phone.packets.wait('pair')
    phone.accept_pair()
    ev.wait('paired')
    asked = []

    def answer_slowly():
        while True:
            _, _, info = phone.packets.wait('packet', timeout=20)
            if info['pkt']['type'] != 'kdeconnect.sms.request_conversations':
                continue
            asked.append(time.monotonic())
            for i in range(3):
                time.sleep(0.5)
                phone.send('kdeconnect.sms.messages', {'version': 2, 'messages': [
                    {'_id': 100 + i, 'thread_id': i + 1, 'addresses': [{'address': f'555000{i}'}],
                     'body': f'hello {i}', 'date': 1700000000000 + i, 'type': 1, 'read': 1}]})
    threading.Thread(target=answer_slowly, daemon=True).start()
    d._threads.pop(phone.ident.id, None)
    t0 = time.monotonic()
    threads = d._conversations(peer)
    assert sorted(threads) == [1, 2, 3], 'all three, though they came half a second apart'
    assert time.monotonic() - t0 >= 1.5 + 1.2 - 0.2
    assert d._conversations(peer) is threads and len(asked) == 1, 'the second call is the cache'
    text, ok = d._sms_list(peer, '2')
    assert ok and text.splitlines()[0].startswith('Texts on the phone: 3 conversations, the 2 newest')
    assert '5550002' in text and 'hello 2' in text
    # a number without its area code finds the conversation
    tid, latest, why = d._thread_of(peer, '000-2')
    assert tid is None and 'no conversation' in why, 'four digits is too loose'
    tid, latest, why = d._thread_of(peer, '555-0002')
    assert tid == 3
    # a plain call while a refresh is in flight (the arming on a re-link) waits for it: one request on the wire
    results = []
    first = threading.Thread(target=lambda: results.append(len(d._conversations(peer, refresh=True))))
    first.start()
    time.sleep(0.2)
    second = threading.Thread(target=lambda: results.append(len(d._conversations(peer))))
    second.start()
    first.join(15); second.join(15)
    assert results == [3, 3] and len(asked) == 2
