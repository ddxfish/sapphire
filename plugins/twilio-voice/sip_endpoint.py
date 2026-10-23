"""SIP/RTP endpoint for Twilio inbound calls — proven core from tmp/spike_sip_twilio.py.

Registers OUTBOUND to a Twilio SIP domain (no open ports — the registration +
keepalive holds the NAT pinhole), answers incoming INVITEs, and hands each call to
an `on_call(caller, RtpSession)` callback. All the hard-won 2026-07-02 findings are
baked in: STUN public address in Contact/SDP, full Via-stack echo, Min-Expires 600,
407 Proxy-Auth, stale-binding wipe. Pure stdlib SIP + numpy-free here (codec lives
in codec.py).

Transport (Stage 2, 2026-07-05): TLS by default — one client-initiated flow to
domain:5061 carries ALL signaling (flow reuse proven by tmp/spike_sip_tls.py).
SNI by hostname (IP = invisible 403), Content-Length stream framing, CRLF
ping-pong keepalive, inline reconnect-on-EOF. A router's SIP ALG can't read the
stream, so it's irrelevant — no edge punching, no SIP STUN, no UA hole-punch.
UDP stays as the per-account legacy fallback (needs SIP ALG off). Media is
plain μ-law RTP over UDP on both (Twilio's supported default; SRTP not needed).

Serve-loop refactor (Stage 1, 2026-07-05): the serve thread is the sole SIP
reader and never blocks on a call — dispatch in _dispatch(), per-call media
pump + teardown in _media_loop(), conversation in _run_on_call(). See
tmp/twilio-serve-loop-refactor-plan.md.

RtpSession is the transport seam the TwilioConversationSource rides:
  read(timeout)  -> inbound μ-law payload (bytes) or None
  write(ulaw160) -> queue an outbound 20ms frame (a paced thread sends at 20ms)
  flush()        -> drop queued outbound audio (barge-in)
  hangup()       -> end the call (sends BYE)
"""
import logging
import os
import queue
import random
import re
import select
import socket
import ssl
import struct
import threading
import time

from .codec import SILENCE_FRAME, FRAME_SAMPLES

logger = logging.getLogger(__name__)

# Call-teardown guards. Inbound RTP flows continuously during a live call (Twilio
# sends silence frames too), so a gap means the caller is gone even if the BYE was
# lost. Without these the media thread pumps a dead call forever and the line
# stays busy until restart. Krem idles a while mid-call, so 60s not 30s.
_CALL_INACTIVITY_SEC = 60      # no inbound RTP this long -> assume hangup
_CALL_MAX_SEC = 7200           # hard 2h backstop against a runaway/wedged call

STUN_MAGIC = 0x2112A442
_PTIME = 0.02          # 20ms RTP frames

# Twilio US1 SIP signaling edges (docs: 54.172.60.0/23, gateways .0-.3) —
# UDP transport only. A restricted-cone NAT only passes inbound from addresses
# we've SENT to — the registrar pinhole covers one edge, but INVITEs arrive
# from ANY of them. CRLF keepalives (RFC 5626 convention) hold a pinhole to
# each. Found live 2026-07-04: router demoted the mapping to restricted after
# restart churn — green keepalives, Twilio 32011 on every INVITE.
_TWILIO_EDGES = [("54.172.60.0", 5060), ("54.172.60.1", 5060),
                 ("54.172.60.2", 5060), ("54.172.60.3", 5060)]
_EDGE_PUNCH_SEC = 30
_TLS_PING_SEC = 25             # CRLF ping cadence on the TLS flow (Twilio's NAT recipe)


def _rand_hex(n=16):
    return "".join(random.choice("0123456789abcdef") for _ in range(n))


def _md5(s):
    import hashlib
    return hashlib.md5(s.encode()).hexdigest()


def _local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def stun_public_addr(sock, host="stun.l.google.com", port=19302, timeout=3):
    """Discover this socket's public NAT mapping (IP, port) via STUN, or None."""
    old = sock.gettimeout()
    try:
        dest = (socket.gethostbyname(host), port)
    except Exception:
        return None
    txid = bytes(random.randint(0, 255) for _ in range(12))
    req = struct.pack("!HHI", 0x0001, 0x0000, STUN_MAGIC) + txid
    try:
        for _ in range(3):
            sock.settimeout(timeout)
            sock.sendto(req, dest)
            try:
                deadline = time.time() + timeout
                while time.time() < deadline:
                    data, _ = sock.recvfrom(2048)
                    if len(data) < 20:
                        continue
                    _, mlen, magic = struct.unpack("!HHI", data[:8])
                    if magic != STUN_MAGIC or data[8:20] != txid:
                        continue
                    off, end = 20, 20 + mlen
                    while off + 4 <= end:
                        atype, alen = struct.unpack("!HH", data[off:off + 4])
                        aval = data[off + 4:off + 4 + alen]
                        if atype == 0x0020 and len(aval) >= 8:
                            pport = struct.unpack("!H", aval[2:4])[0] ^ (STUN_MAGIC >> 16)
                            praw = bytes(b ^ m for b, m in zip(aval[4:8], struct.pack("!I", STUN_MAGIC)))
                            return socket.inet_ntoa(praw), pport
                        if atype == 0x0001 and len(aval) >= 8:
                            return socket.inet_ntoa(aval[4:8]), struct.unpack("!H", aval[2:4])[0]
                        off += 4 + alen + ((4 - alen % 4) % 4)
                    break
            except socket.timeout:
                continue
    finally:
        sock.settimeout(old)
    return None


def _parse(data):
    text = data.decode(errors="replace")
    head, _, body = text.partition("\r\n\r\n")
    lines = head.split("\r\n")
    headers, vias, rrs = {}, [], []
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            k, v = k.strip().lower(), v.strip()
            if k == "via":
                vias.append(v)
            if k == "record-route":
                rrs.append(v)
            if k not in headers:
                headers[k] = v
    headers["_vias"] = vias
    headers["_rrs"] = rrs
    return lines[0], headers, body


def _via_lines(h):
    return [f"Via: {v}" for v in (h.get("_vias") or [h.get("via", "")])]


def _ua_public_addr(h):
    """Far UA's public signaling addr, from the bottom Via's received=/rport=
    (stamped by Twilio's proxy). With no Record-Route in the INVITE, the ACK and
    the hangup BYE come DIRECT from this host — the NAT only passes them if we've
    sent to it first (the hole-punch)."""
    vias = h.get("_vias") or []
    if not vias:
        return None
    v = vias[-1]
    m_ip = re.search(r"received=([\d.]+)", v)
    m_pt = re.search(r"rport=(\d+)", v)
    if m_ip:
        return (m_ip.group(1), int(m_pt.group(1)) if m_pt else 5060)
    m = re.search(r"SIP/2\.0/UDP\s+([\d.]+)(?::(\d+))?", v)
    if m and not m.group(1).startswith(("10.", "192.168.", "172.")):
        return (m.group(1), int(m.group(2) or 5060))
    return None


def _challenge(value):
    value = value.split(" ", 1)[1] if value.lower().startswith("digest") else value
    out = {}
    for m in re.finditer(r'(\w+)=(?:"([^"]*)"|([^,\s]+))', value):
        out[m.group(1).lower()] = m.group(2) if m.group(2) is not None else m.group(3)
    return out


class RtpSession:
    """One live call's media. A paced thread sends 20ms frames (silence when idle,
    keeping RTP + the NAT pinhole alive); inbound frames land in a queue."""

    def __init__(self, sock, remote):
        self._sock = sock
        self._remote = remote
        self._seq = random.randint(0, 20000)
        self._ts = random.randint(0, 100000)
        self._ssrc = random.randint(0, 0xFFFFFFFF)
        self._out = queue.Queue()
        self._in = queue.Queue(maxsize=200)
        self._alive = threading.Event()
        self._alive.set()
        self._ended = threading.Event()
        self._sender = None
        self._hangup_after_drain = False   # armed by the <<HANG UP>> sentinel hook

    def start(self):
        self._sender = threading.Thread(target=self._send_loop, daemon=True, name="twilio-rtp-tx")
        self._sender.start()

    def feed_inbound(self, ulaw):
        try:
            self._in.put_nowait(ulaw)
        except queue.Full:
            try:
                self._in.get_nowait()
                self._in.put_nowait(ulaw)
            except queue.Empty:
                pass

    def read(self, timeout=0.5):
        try:
            return self._in.get(timeout=timeout)
        except queue.Empty:
            return None

    def write(self, ulaw160):
        if self._alive.is_set():
            self._out.put(ulaw160)

    def flush(self):
        while True:
            try:
                self._out.get_nowait()
            except queue.Empty:
                break

    def outbound_idle(self):
        return self._out.empty()

    def _send_loop(self):
        next_t = time.time()
        while self._alive.is_set():
            try:
                frame = self._out.get_nowait()
            except queue.Empty:
                frame = SILENCE_FRAME
            hdr = struct.pack("!BBHII", 0x80, 0x00, self._seq & 0xFFFF,
                              self._ts & 0xFFFFFFFF, self._ssrc)
            try:
                self._sock.sendto(hdr + frame, self._remote)
            except ConnectionResetError:
                # Windows surfaces ICMP port-unreachable as WSAECONNRESET on
                # UDP — transient, must not kill the outbound audio loop.
                continue
            except OSError:
                break
            self._seq += 1
            self._ts += FRAME_SAMPLES
            next_t += _PTIME
            time.sleep(max(0, next_t - time.time()))

    def stop(self):
        self._alive.clear()
        self._ended.set()

    def wait_ended(self, timeout=None):
        return self._ended.wait(timeout)


class SipEndpoint:
    def __init__(self, domain, user, password, on_call,
                 sip_port=5062, rtp_port=10080, expires=600, accept_call=None,
                 transport="tls"):
        self.domain = domain
        self.user = user
        self.password = password
        self.on_call = on_call
        # Optional pre-answer gate: accept_call(caller) -> bool. False = 603
        # Decline before any 180/200 — the caller a rule doesn't want never
        # reaches a session (no RTP, no chat, carrier plays the right tone).
        self.accept_call = accept_call
        self.sip_port = sip_port
        self.rtp_port = rtp_port
        self.expires = expires
        self.ip = _local_ip()
        self._stop = threading.Event()
        self._registered = False
        self.sip = None
        self.rtp = None
        self.call_id = f"{_rand_hex(20)}@{self.ip}"
        self.from_tag = _rand_hex(10)
        self.cseq = 0
        self.pub_sip = None
        self.contact = None
        self._active_call = None   # {"session", "dialog", "call_id", "caller"} or None
        self.tls = (transport == "tls")
        self._buf = b""                     # TLS stream reassembly buffer
        self._sip_lock = threading.Lock()   # SSL objects aren't safe for concurrent
        self._reconnecting = False          # read/write — serialize all flow I/O

    # ── lifecycle ────────────────────────────────────────────────────────────
    def serve_forever(self):
        """Bind, register, and loop: keepalive + dispatch. Blocks until stop().

        Serve-loop refactor (Stage 1, 2026-07-05): this thread is the SOLE
        reader of self.sip and NEVER blocks on a call — per-call work runs on
        two daemon threads (media pump reads self.rtp and owns teardown;
        conversation runs on_call). So re-registration keeps running during
        long calls and a second INVITE always gets a clean 486. Transport
        seam: SIP reads go through _poll_sip(), writes through _send() —
        Stage 2 (TLS) swaps their internals."""
        self.rtp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rtp.bind(("0.0.0.0", self.rtp_port))
        if self.tls:
            # Signaling rides one client-initiated TLS flow — no local SIP bind,
            # no SIP STUN (the flow itself holds the NAT path). Media stays
            # UDP + STUN below, unchanged.
            self.registrar = (self.domain, 5061)   # synthetic addr for logs; _send ignores it
            if not self._tls_connect():
                logger.error("[TWILIO] TLS connect failed — voice endpoint not listening")
                return                              # fast death -> daemon backoff ladder
        else:
            self.sip = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sip.bind(("0.0.0.0", self.sip_port))
            self.registrar = (socket.gethostbyname(self.domain), 5060)
            pub = stun_public_addr(self.sip)
            ip, port = pub if pub else (self.ip, self.sip_port)
            if not pub:
                logger.warning("[TWILIO] STUN failed — using LAN addr (calls will fail behind NAT)")
            self.pub_sip = (ip, port)
            self.contact = f"<sip:{self.user}@{ip}:{port}>"

        self._deregister_all()   # over TLS this also wipes stale UDP bindings (32209 guard)
        if not self._register():
            logger.error("[TWILIO] SIP registration failed — voice endpoint not listening")
            return
        logger.info(f"[TWILIO] registered as {self.user}@{self.domain} "
                    f"({'TLS flow' if self.tls else 'UDP public'} "
                    f"{self.pub_sip[0]}:{self.pub_sip[1]})")
        self._punch_edges()

        punch_sec = _TLS_PING_SEC if self.tls else _EDGE_PUNCH_SEC
        next_reg = time.time() + self.expires / 2
        next_punch = time.time() + punch_sec
        while not self._stop.is_set():
            try:
                if time.time() >= next_reg:
                    self._keepalive_register()
                    next_reg = time.time() + self.expires / 2
                if time.time() >= next_punch:
                    self._punch_edges()
                    next_punch = time.time() + punch_sec
                for start, h, body, addr in self._poll_sip(0.5):
                    self._dispatch(start, h, body, addr)
            except Exception as e:
                # A stray/malformed packet or transient socket error must NEVER kill
                # this thread — a dead endpoint silently drops ALL future calls until
                # a restart (the bug that made her stop answering ~10min after boot).
                # Log, back off briefly, keep serving.
                logger.error(f"[TWILIO] serve loop error (continuing): {e}", exc_info=True)
                time.sleep(0.2)
        call = self._active_call
        if call:
            call["session"].stop()   # media thread notices and tears down
        # Deregister HERE, on the serve thread — it must stay the socket's ONLY
        # reader. A second reader (the old stop()-side deregister) races it for
        # the reply datagram; the loser blocks forever in recvfrom and wedges
        # shutdown until systemd SIGKILLs (three wedges Jul 2-3, 2026).
        try:
            self._deregister_all()
        except Exception:
            pass
        for s in (self.sip, self.rtp):
            try:
                s.close()
            except Exception:
                pass

    def _keepalive_register(self):
        # NAT-drift guard (UDP only — a TLS flow is connection-oriented, drift
        # can't happen): routers re-map long-lived UDP bindings (proven live
        # 2026-07-03: green keepalives, unroutable INVITEs). Re-STUN each
        # keepalive; if the public mapping moved, wipe the stale binding and
        # register the new Contact. STUN failure -> keep the old contact
        # (never clobber with a LAN addr). NOTE: STUN reads self.sip on this
        # (serve) thread — a SIP message landing in that ~3s window is eaten;
        # rare, and the inactivity backstop covers a lost mid-call BYE.
        if not self.tls:
            try:
                pub = stun_public_addr(self.sip)
                if pub and pub != self.pub_sip:
                    logger.warning(f"[TWILIO] public mapping moved {self.pub_sip} -> {pub} — re-binding")
                    self.pub_sip = pub
                    self.contact = f"<sip:{self.user}@{pub[0]}:{pub[1]}>"
                    self._deregister_all()
            except Exception as e:
                logger.debug(f"[TWILIO] keepalive STUN check failed: {e}")
        ok = self._register()
        logger.info(f"[TWILIO] keepalive re-register {'ok' if ok else 'FAILED'} — {self.user}@{self.domain}")

    def _poll_sip(self, timeout):
        """Transport seam: return [(start, headers, body, addr)]. UDP: at most
        one datagram. TLS: every complete message framed out of the stream
        buffer; a dead flow triggers an inline reconnect (an active call's RTP
        keeps flowing meanwhile) — if that fails, the endpoint exits and the
        daemon's backoff ladder takes over."""
        if not self.tls:
            r, _, _ = select.select([self.sip], [], [], timeout)
            if not r:
                return []
            data, addr = self.sip.recvfrom(65535)
            start, h, body = _parse(data)
            return [(start, h, body, addr)]
        msgs = self._tls_read(timeout)
        if msgs is None:                     # flow died (EOF/reset)
            if not self._reconnecting:
                self._reconnecting = True
                try:
                    if not self._tls_reconnect():
                        self._stop.set()
                finally:
                    self._reconnecting = False
            else:
                # Reconnect's own register polls land here if the fresh flow
                # dies too — EOF is sticky, don't hot-spin _reg_wait.
                self._stop.wait(0.1)
            return []
        return msgs

    # ── TLS transport (Stage 2) ──────────────────────────────────────────────
    def _tls_connect(self):
        """Open the TLS flow to the SIP domain. Hostname (not IP) is
        load-bearing: no SNI = invisible 403 that never reaches the console."""
        try:
            ctx = ssl.create_default_context()
            raw = socket.create_connection((self.domain, 5061), timeout=10)
            self.sip = ctx.wrap_socket(raw, server_hostname=self.domain)
            self._buf = b""
            lip, lport = self.sip.getsockname()[:2]
            self.pub_sip = (lip, lport)
            self.contact = f"<sip:{self.user}@{lip}:{lport};transport=tls>"
            logger.info(f"[TWILIO] TLS flow up ({self.sip.version()}) to "
                        f"{self.domain}:5061, local {lip}:{lport}")
            return True
        except Exception as e:
            logger.error(f"[TWILIO] TLS connect to {self.domain}:5061 failed: {e}")
            return False

    def _tls_reconnect(self):
        """The flow died: reconnect + re-register inline on the serve thread.
        An active call's media is a separate UDP socket, so a SIP blip mid-call
        is invisible to the caller."""
        logger.warning("[TWILIO] TLS flow lost — reconnecting")
        try:
            self.sip.close()
        except Exception:
            pass
        for attempt in range(3):
            if self._stop.is_set():
                return False
            if self._tls_connect():
                self._deregister_all()   # wipe the dead flow's binding first (32209)
                if self._register():
                    logger.info("[TWILIO] TLS flow re-established + re-registered")
                    return True
            self._stop.wait(2 * (attempt + 1))
        logger.error("[TWILIO] TLS reconnect failed — endpoint exiting (daemon will retry)")
        return False

    def _tls_read(self, timeout):
        """Pull bytes off the flow, frame complete SIP messages by
        Content-Length, swallow CRLF keepalive pongs. Returns a list, or None
        when the flow is dead. recv is capped at 0.5s so cross-thread _send()
        callers never wait long on the I/O lock."""
        with self._sip_lock:
            self.sip.settimeout(min(timeout, 0.5))
            try:
                data = self.sip.recv(65535)
                if not data:
                    return None                       # EOF
                self._buf += data
                while self.sip.pending():             # drain decrypted leftovers
                    self._buf += self.sip.recv(65535)
            except (socket.timeout, ssl.SSLWantReadError):
                pass
            except OSError:
                return None                           # reset / SSL failure
        out = []
        while True:
            while self._buf.startswith(b"\r\n"):      # keepalive pong
                self._buf = self._buf[2:]
            if b"\r\n\r\n" not in self._buf:
                return out
            head, rest = self._buf.split(b"\r\n\r\n", 1)
            m = re.search(rb"content-length\s*:\s*(\d+)", head, re.I)
            cl = int(m.group(1)) if m else 0
            if len(rest) < cl:
                return out                            # body still in flight
            self._buf = rest[cl:]
            start, h, body = _parse(head + b"\r\n\r\n" + rest[:cl])
            out.append((start, h, body, self.registrar))

    def stop(self):
        """Signal the serve thread to exit; it deregisters on its way out.
        Never read the socket from here — single-reader rule (see serve_forever)."""
        self._stop.set()

    def _punch_edges(self):
        """UDP: hold NAT pinholes open to every Twilio signaling edge (see
        _TWILIO_EDGES). TLS: CRLF ping down the flow (Twilio's documented
        TCP/TLS keepalive; the pong is swallowed by _tls_read). Write-only —
        never reads the socket."""
        if self.tls:
            try:
                with self._sip_lock:
                    self.sip.sendall(b"\r\n\r\n")
            except OSError:
                pass
            return
        targets = list(_TWILIO_EDGES)
        if getattr(self, "registrar", None):
            targets.append(self.registrar)
        for addr in targets:
            try:
                self.sip.sendto(b"\r\n\r\n", addr)
            except OSError:
                pass

    # ── dispatch + one call ──────────────────────────────────────────────────
    def _dispatch(self, start, h, body, addr):
        """Route one inbound SIP message. Runs on the serve thread — must never
        block on a call. Also called from _reg_wait so INVITE/BYE keep working
        during the ~5s registration window (no drop window)."""
        call = self._active_call
        if call and not start.startswith("SIP/2.0"):
            # TEMP probe (BYE-loss investigation): every SIP request that
            # reaches the socket during a call, with its source addr.
            logger.info(f"[TWILIO-SIPCALL] {start} from {addr[0]}:{addr[1]}")
        if start.startswith("INVITE"):
            if call:
                if h.get("call-id") == call["call_id"]:
                    logger.info("[TWILIO] INVITE retransmit for the active call — ignoring (200 already sent)")
                else:
                    # Reject the newcomer cleanly so the carrier plays a busy
                    # tone instead of a routing failure. (One call per line —
                    # concurrent lines are separate endpoints.)
                    logger.info("[TWILIO] second call while busy — replying 486 Busy Here")
                    self._reply(486, "Busy Here", h, addr)
                return
            self._answer_invite(start, h, body, addr)
        elif start.startswith("BYE"):
            if call and h.get("call-id") == call["call_id"]:
                logger.info("[TWILIO] caller hung up (BYE)")
                self._reply(200, "OK", h, addr)
                call["dialog"]["remote_bye"] = True
                call["session"].stop()
            else:
                # Late BYE retransmit after teardown — a 481 stops the retransmits.
                logger.info(f"[TWILIO-SIPIDLE] stray BYE from {addr[0]}:{addr[1]} — replying 481")
                self._reply(481, "Call/Transaction Does Not Exist", h, addr)
        elif start.startswith("OPTIONS"):
            self._reply(200, "OK", h, addr)
        elif start.startswith("ACK"):
            pass
        elif start.startswith("SIP/2.0"):
            # Response outside _reg_wait (late REGISTER reply, our BYE's 200).
            logger.debug(f"[TWILIO] stray response: {start}")
        else:
            # TEMP probe: SIP arriving outside a call.
            logger.info(f"[TWILIO-SIPIDLE] {start} from {addr[0]}:{addr[1]}")

    def _answer_invite(self, start, h, sdp, addr):
        m = re.search(r"c=IN IP4 ([\d.]+)", sdp)
        p = re.search(r"m=audio (\d+)", sdp)
        if not (m and p):
            self._reply(488, "Not Acceptable Here", h, addr)
            return
        remote_rtp = (m.group(1), int(p.group(1)))
        caller = self._caller_number(h.get("from", ""))
        logger.info(f"[TWILIO] incoming call from {caller}")
        if self.accept_call is not None:
            try:
                accepted = bool(self.accept_call(caller))
            except Exception as e:
                logger.error(f"[TWILIO] accept_call check failed (declining): {e}")
                accepted = False
            if not accepted:
                logger.info(f"[TWILIO] no rule accepts {caller} — replying 603 Decline")
                self._reply(603, "Decline", h, addr)
                return
        # TEMP probe (BYE-loss investigation): does the INVITE carry Record-Route,
        # and where does the far UA live? Settles proxy-path vs direct-path routing.
        logger.info(f"[TWILIO-SIPCALL] INVITE Record-Route x{len(h.get('_rrs') or [])}: "
                    f"{h.get('_rrs')} | bottom Via: {(h.get('_vias') or [''])[-1]}")

        rpub = stun_public_addr(self.rtp)
        rip, rport = rpub if rpub else (self.ip, self.rtp_port)
        my_sdp = ("v=0\r\n" f"o=- 0 0 IN IP4 {rip}\r\n" "s=sapphire\r\n"
                  f"c=IN IP4 {rip}\r\n" "t=0 0\r\n"
                  f"m=audio {rport} RTP/AVP 0\r\n"
                  "a=rtpmap:0 PCMU/8000\r\na=ptime:20\r\na=sendrecv\r\n")
        self._reply(180, "Ringing", h, addr)
        self._reply(200, "OK", h, addr, sdp=my_sdp)

        session = RtpSession(self.rtp, remote_rtp)
        # Outbound-call correlation: <Dial><Sip> URI params arrive as X- headers
        # (if Twilio passes them through — the daemon logs which).
        session.x_header = h.get("x-sapphire-call")
        session.start()
        # TLS: ACK/BYE arrive down the flow — no direct UA path, no punch.
        ua = None if self.tls else _ua_public_addr(h)
        if ua:
            # NAT hole-punch: open our router's path from the far UA's host so its
            # direct end-to-end ACK/BYE can reach this socket. Re-punched by the
            # media thread to hold the mapping for the whole call.
            try:
                self.sip.sendto(b"\r\n\r\n", ua)
                logger.info(f"[TWILIO] hole-punched SIP path to UA at {ua[0]}:{ua[1]}")
            except OSError as e:
                logger.warning(f"[TWILIO] hole-punch failed: {e}")
        dialog = {"h": h, "addr": addr, "remote": remote_rtp, "ua": ua, "remote_bye": False}
        self._active_call = {"session": session, "dialog": dialog,
                             "call_id": h.get("call-id"), "caller": caller}
        # Conversation + media pump each get their own thread; the serve thread
        # goes straight back to dispatching (re-register never pauses).
        threading.Thread(target=self._run_on_call, args=(caller, session),
                         daemon=True, name="twilio-oncall").start()
        threading.Thread(target=self._media_loop, args=(session, dialog),
                         daemon=True, name="twilio-media").start()

    def _run_on_call(self, caller, session):
        try:
            self.on_call(caller, session)
        except Exception as e:
            logger.error(f"[TWILIO] on_call error: {e}", exc_info=True)
        finally:
            session.stop()

    def _media_loop(self, session, dialog):
        """Per-call media pump: reads self.rtp ONLY (the serve thread keeps
        self.sip). Owns the inactivity + max-duration teardown, the mid-call
        NAT punching, and the farewell BYE (unless the caller's BYE already
        ended the dialog). Clears _active_call on the way out."""
        started = last_rtp = last_punch = time.time()
        try:
            while session._alive.is_set() and not self._stop.is_set():
                now = time.time()
                if not self.tls and now - last_punch > 15:
                    if dialog.get("ua"):
                        try:
                            self.sip.sendto(b"\r\n\r\n", dialog["ua"])
                        except OSError:
                            pass
                    self._punch_edges()      # keep ALL edges open during calls too
                    last_punch = now
                if now - last_rtp > _CALL_INACTIVITY_SEC:
                    logger.info(f"[TWILIO] no inbound audio for {_CALL_INACTIVITY_SEC}s — ending call (assumed hangup, BYE not seen)")
                    break
                if now - started > _CALL_MAX_SEC:
                    logger.warning(f"[TWILIO] call exceeded {_CALL_MAX_SEC}s cap — force-ending")
                    break
                r, _, _ = select.select([self.rtp], [], [], 0.2)
                if not r:
                    continue
                try:
                    data, _addr = self.rtp.recvfrom(65535)
                except ConnectionResetError:
                    # Windows WSAECONNRESET on UDP (ICMP echo of a failed
                    # send) — transient, not a call-ending error.
                    continue
                if len(data) > 12 and (data[0] & 0xC0) == 0x80:
                    _gap = time.time() - last_rtp          # TEMP probe: is Twilio RTP
                    if _gap > 3:                            # continuous during silence?
                        logger.info(f"[TWILIO-RTPGAP] inbound RTP resumed after {_gap:.1f}s gap")
                    last_rtp = time.time()
                    session.feed_inbound(data[12:])
        except Exception as e:
            logger.error(f"[TWILIO] media loop error: {e}", exc_info=True)
        finally:
            session.stop()
            if not dialog.get("remote_bye"):
                # We (Sapphire) ended it -> send BYE to the caller leg.
                try:
                    self._send_bye(dialog)
                except Exception as e:
                    logger.error(f"[TWILIO] BYE send failed: {e}")
            self._active_call = None

    # ── SIP message helpers ──────────────────────────────────────────────────
    def _send(self, text, addr):
        if self.tls:
            with self._sip_lock:     # cross-thread writes (media thread's BYE)
                self.sip.sendall(text.encode())
        else:
            self.sip.sendto(text.encode(), addr)

    def _reply(self, code, reason, req_h, addr, sdp=None):
        to = req_h.get("to", "")
        if code != 100 and "tag=" not in to and not str(code).startswith("1"):
            to = f"{to};tag={self.from_tag}"
        elif code == 180 and "tag=" not in to:
            to = f"{to};tag={self.from_tag}"
        # RFC 3261 12.1.1: dialog-forming responses MUST echo Record-Route in
        # order — without it Twilio sends the 200's ACK and the hangup BYE
        # direct-to-Contact from a host the NAT blocks (the lost-BYE root cause).
        lines = [f"SIP/2.0 {code} {reason}", *_via_lines(req_h),
                 *[f"Record-Route: {r}" for r in (req_h.get("_rrs") or [])],
                 f"From: {req_h.get('from', '')}", f"To: {to}",
                 f"Call-ID: {req_h.get('call-id', '')}", f"CSeq: {req_h.get('cseq', '')}",
                 f"Contact: {self.contact}", "Allow: INVITE, ACK, BYE, CANCEL, OPTIONS"]
        body = sdp or ""
        if sdp:
            lines.append("Content-Type: application/sdp")
        lines.append(f"Content-Length: {len(body)}")
        self._send("\r\n".join(lines) + "\r\n\r\n" + body, addr)

    def _via(self):
        """Via line for requests WE originate (REGISTER/BYE), per transport."""
        if self.tls:
            return (f"Via: SIP/2.0/TLS {self.pub_sip[0]}:{self.pub_sip[1]}"
                    f";branch=z9hG4bK{_rand_hex(12)};rport")
        return (f"Via: SIP/2.0/UDP {self.ip}:{self.sip_port}"
                f";branch=z9hG4bK{_rand_hex(12)};rport")

    def _send_bye(self, dialog):
        h, addr = dialog["h"], dialog["addr"]
        target = self._contact_uri(h) or f"sip:{self.user}@{self.domain}"
        self.cseq += 1
        lines = [
            f"BYE {target} SIP/2.0",
            self._via(),
            # In-dialog requests ride the Record-Route set from the INVITE — without
            # it the proxy has no dialog context and 481s the BYE (spike run 2-3).
            *[f"Route: {r}" for r in (h.get("_rrs") or [])],
            "Max-Forwards: 70",
            f"From: {h.get('to', '')};tag={self.from_tag}",
            f"To: {h.get('from', '')}",
            f"Call-ID: {h.get('call-id', '')}", f"CSeq: {self.cseq} BYE",
            "Content-Length: 0",
        ]
        self._send("\r\n".join(lines) + "\r\n\r\n", addr)

    def _reg_msg(self, auth=None, auth_header="Authorization", wipe=False):
        self.cseq += 1
        uri = f"sip:{self.domain}"
        contact = "Contact: *" if wipe else f"Contact: {self.contact}"
        exp = 0 if wipe else self.expires
        lines = [
            f"REGISTER {uri} SIP/2.0",
            self._via(),
            "Max-Forwards: 70",
            f"From: <sip:{self.user}@{self.domain}>;tag={self.from_tag}",
            f"To: <sip:{self.user}@{self.domain}>",
            f"Call-ID: {self.call_id}", f"CSeq: {self.cseq} REGISTER",
            contact, f"Expires: {exp}", "Allow: INVITE, ACK, BYE, CANCEL, OPTIONS",
            "User-Agent: sapphire-twilio/1.0",
        ]
        if auth:
            lines.append(f"{auth_header}: {self._digest('REGISTER', uri, auth)}")
        lines.append("Content-Length: 0")
        return "\r\n".join(lines) + "\r\n\r\n"

    def _digest(self, method, uri, ch):
        realm, nonce = ch.get("realm", ""), ch.get("nonce", "")
        ha1 = _md5(f"{self.user}:{realm}:{self.password}")
        ha2 = _md5(f"{method}:{uri}")
        parts = [f'username="{self.user}"', f'realm="{realm}"', f'nonce="{nonce}"',
                 f'uri="{uri}"', 'algorithm=MD5']
        if "auth" in (ch.get("qop") or ""):
            cnonce, nc = _rand_hex(8), "00000001"
            parts += [f'response="{_md5(f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}")}"',
                      'qop=auth', f'nc={nc}', f'cnonce="{cnonce}"']
        else:
            parts.append(f'response="{_md5(f"{ha1}:{nonce}:{ha2}")}"')
        if ch.get("opaque"):
            parts.append(f'opaque="{ch["opaque"]}"')
        return "Digest " + ", ".join(parts)

    def _reg_wait(self):
        """Wait for a REGISTER response. Non-response traffic (INVITE/BYE/
        OPTIONS) is dispatched, not dropped — a call can arrive or hang up
        mid-registration without loss."""
        deadline = time.time() + 5
        while time.time() < deadline:
            for start, h, body, addr in self._poll_sip(max(0.05, deadline - time.time())):
                if start.startswith("SIP/2.0"):
                    if start.split(" ")[1] != "100":
                        return start, h
                else:
                    self._dispatch(start, h, body, addr)
        return None

    def _register(self):
        challenge, auth_header = None, "Authorization"
        for _ in range(4):
            self._send(self._reg_msg(challenge, auth_header), self.registrar)
            resp = self._reg_wait()
            if resp is None:
                logger.error(f"[TWILIO] ({self.user}) REGISTER got no response "
                             "(network/DNS, or the Twilio domain/credential hasn't propagated yet)")
                return False
            start, h = resp
            code = int(start.split(" ", 2)[1])
            if code in (401, 407):
                if challenge is not None:
                    logger.error(f"[TWILIO] ({self.user}) SIP credentials rejected")
                    return False
                key = "www-authenticate" if code == 401 else "proxy-authenticate"
                challenge = _challenge(h.get(key, ""))
                auth_header = "Authorization" if code == 401 else "Proxy-Authorization"
                continue
            if code == 423:
                self.expires = int(h.get("min-expires", "600"))
                challenge, auth_header = None, "Authorization"
                continue
            if 200 <= code < 300:
                self._registered = True
                return True
            logger.error(f"[TWILIO] ({self.user}) REGISTER rejected: {start.strip()} "
                         "(403 right after setup usually = credential still propagating, "
                         "or username/password mismatch)")
            return False
        return False

    def _deregister_all(self):
        challenge, auth_header = None, "Authorization"
        for _ in range(3):
            self._send(self._reg_msg(challenge, auth_header, wipe=True), self.registrar)
            resp = self._reg_wait()
            if resp is None:
                return
            start, h = resp
            code = int(start.split(" ", 2)[1])
            if code in (401, 407):
                key = "www-authenticate" if code == 401 else "proxy-authenticate"
                challenge = _challenge(h.get(key, ""))
                auth_header = "Authorization" if code == 401 else "Proxy-Authorization"
                continue
            return

    def _caller_number(self, from_hdr):
        m = re.search(r"sip:(\+?\d+)@", from_hdr) or re.search(r'"(\+?\d+)"', from_hdr)
        return m.group(1) if m else "unknown"

    def _contact_uri(self, h):
        m = re.search(r"<(sip:[^>]+)>", h.get("contact", ""))
        return m.group(1) if m else None
