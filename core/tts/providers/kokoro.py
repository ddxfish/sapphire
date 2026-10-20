"""Kokoro TTS provider — local HTTP server on port 5012, or another
computer's Kokoro through its shared door: a server setting that holds a
pairing string (core/worker_door.py) is reached over pinned TLS with its key."""
import logging
import time
from typing import Iterator, List, Optional, Tuple

import requests
from core import net, worker_door
import config

from .base import BaseTTSProvider

logger = logging.getLogger(__name__)


def _split_complete_oggs(buf: bytes) -> Tuple[List[bytes], bytes]:
    """Split a buffer of streamed OGG bytes at FILE boundaries.

    Returns (complete_files, remaining). complete_files is zero or more
    independently-decodable OGG files in order. remaining is partial-page
    bytes that should be prepended to the next chunk.

    Why this exists: Kokoro's chunked-transfer endpoint emits one OGG per
    pipeline segment. But `requests.iter_content(chunk_size=None)` yields
    bytes in whatever shape the socket layer delivered — on Windows
    loopback (and to a lesser degree Linux), multiple OGGs can coalesce
    into one yield. Browser `<audio>` and `sf.read(BytesIO)` decode only
    the FIRST OGG of a concatenated blob, silently dropping the rest.
    This splitter detects file ends via the EOS flag (bit 2 of byte 5 in
    each OGG page header) and yields each file separately. 2026-05-18
    herring-table #3.
    """
    files: List[bytes] = []
    pos = 0
    file_start = 0
    n = len(buf)
    while pos < n:
        idx = buf.find(b"OggS", pos)
        if idx < 0:
            break
        # Need at least 27 bytes for the fixed page header
        if idx + 27 > n:
            break
        # OGG version byte must be 0 — skip stray "OggS" in payload
        if buf[idx + 4] != 0:
            pos = idx + 1
            continue
        header_type = buf[idx + 5]
        num_segments = buf[idx + 26]
        seg_table_end = idx + 27 + num_segments
        if seg_table_end > n:
            break  # partial segment_table
        page_data_len = sum(buf[idx + 27:seg_table_end])
        page_end = seg_table_end + page_data_len
        if page_end > n:
            break  # partial page data
        # Complete page. Bit 2 of header_type_flag = end-of-stream → file ends here.
        if header_type & 0x04:
            files.append(bytes(buf[file_start:page_end]))
            file_start = page_end
        pos = page_end
    remaining = bytes(buf[file_start:])
    return files, remaining


class KokoroTTSProvider(BaseTTSProvider):
    """Generates audio via the local Kokoro TTS server subprocess."""

    audio_content_type = 'audio/ogg'
    SPEED_MIN = 0.5
    SPEED_MAX = 2.0
    supports_streaming = True
    supports_pitch = True  # server resamples PCM pre-encode (pitch in payload)

    def __init__(self):
        self.primary_server = config.TTS_PRIMARY_SERVER
        self.fallback_server = config.TTS_FALLBACK_SERVER
        self.fallback_timeout = config.TTS_FALLBACK_TIMEOUT
        self._doors = {}            # pairing string -> (session, base)
        self.served_by = None       # the last call: {"lane": primary|fallback, "server", "why"}
        self._health_why = None
        logger.info(f"Kokoro TTS provider: {_shown(self.primary_server)}")

    def _call(self, method, server, path, timeout=None, **kw):
        """One request to a server setting: a plain URL through the net facade,
        a pairing string through its pinned door (the key rides the session)."""
        if not worker_door.is_pairing(server):
            return getattr(net, method)(f"{server}{path}", timeout=timeout, **kw)
        door = self._doors.get(server)
        try:
            if not door:
                door = self._doors[server] = worker_door.session(server, timeout=timeout or 5)
            s, base = door
            return s.request(method.upper(), f"{base}{path}", timeout=timeout, **kw)
        except requests.exceptions.SSLError:
            self._doors.pop(server, None)               # a new cert on the worker: checked afresh next time, and refused
            worker_door.forget(*worker_door.parse(server)[::2])
            raise

    def generate(self, text: str, voice: str, speed: float, **kwargs) -> Optional[bytes]:
        """POST to Kokoro server, return OGG bytes. Retries on transient failures."""
        clamped_speed = max(self.SPEED_MIN, min(self.SPEED_MAX, speed))
        if clamped_speed != speed:
            logger.warning(f"Kokoro: clamped speed {speed} -> {clamped_speed} (range {self.SPEED_MIN}-{self.SPEED_MAX})")

        delays = [0.5, 1.0, 2.0]  # 3 retries, 3.5s total backoff
        last_error = None

        for attempt in range(1 + len(delays)):
            try:
                server_url = self._get_server_url()
                response = self._call('post', server_url, '/tts', json={
                    'text': text.replace("*", ""),
                    'voice': voice,
                    'speed': clamped_speed,
                    'pitch': float(kwargs.get('pitch') or 1.0),
                }, timeout=60)
                if response.status_code == 200:
                    self._last_error = None
                    return response.content
                try:
                    detail = response.json().get('error') or ''
                except Exception:
                    detail = ''
                last_error = f"HTTP {response.status_code} {detail}".rstrip()
                logger.error(f"Kokoro server error: HTTP {response.status_code}")   # the detail can quote her words: Test button only
                if 400 <= response.status_code < 500:
                    break  # client error — retrying won't help
            except Exception as e:
                last_error = e

            if attempt < len(delays):
                logger.warning(f"Kokoro TTS attempt {attempt + 1} failed, retrying in {delays[attempt]}s...")
                time.sleep(delays[attempt])

        logger.error(f"Kokoro generate failed after {attempt + 1} attempt(s)"
                     + ("" if isinstance(last_error, str) else f": {last_error!r}"))
        self._last_error = str(last_error)   # the Test button shows it
        return None

    def generate_stream(self, text: str, voice: str, speed: float, **kwargs) -> Iterator[bytes]:
        """POST to /tts/stream, yield each OGG segment as it arrives.

        Falls back to non-streaming generate() on connect failure or non-200
        response — same observable contract from the caller's view, just no
        latency win.

        No retry loop here: we've committed to a chunked response once headers
        arrive, can't restart cleanly. The non-streaming /tts path keeps its
        retries for the fallback case.
        """
        clamped_speed = max(self.SPEED_MIN, min(self.SPEED_MAX, speed))
        if clamped_speed != speed:
            logger.warning(f"Kokoro: clamped speed {speed} -> {clamped_speed}")

        try:
            server_url = self._get_server_url()
            response = self._call(
                'post', server_url, '/tts/stream',
                json={
                    'text': text.replace("*", ""),
                    'voice': voice,
                    'speed': clamped_speed,
                    'pitch': float(kwargs.get('pitch') or 1.0),
                },
                stream=True,
                timeout=60,
            )
            if response.status_code != 200:
                if 400 <= response.status_code < 500:
                    # The caller's mistake (unknown voice, bad text): the
                    # whole-blob route would answer the same, so don't ask it.
                    logger.error(f"Kokoro /tts/stream returned {response.status_code}")
                    return
                logger.warning(
                    f"Kokoro /tts/stream returned {response.status_code} — "
                    f"falling back to non-streaming generate()"
                )
                audio = self.generate(text, voice, speed, **kwargs)
                if audio:
                    yield audio
                return

            # iter_content with chunk_size=None yields whatever the HTTP
            # client received per chunked-transfer frame. A yield can be:
            #   - one complete OGG (the happy case on Linux loopback)
            #   - partial OGG (split across yields under TCP fragmentation)
            #   - multiple concatenated OGGs (Win loopback Nagle coalesces)
            # Buffer + split-by-EOS-page so each yield from THIS function
            # is exactly one independently-decodable OGG file. Otherwise
            # the browser's <audio> element decodes only the first of a
            # concatenated blob and silently drops trailing audio.
            # 2026-05-18 herring-table #3.
            buffer = bytearray()
            oggs_split = 0
            for chunk in response.iter_content(chunk_size=None):
                if not chunk:
                    continue
                buffer.extend(chunk)
                complete, remaining = _split_complete_oggs(bytes(buffer))
                for ogg in complete:
                    oggs_split += 1
                    yield ogg
                buffer = bytearray(remaining)
            # Any final non-EOS-terminated remainder (shouldn't normally
            # happen, but if it does we yield it as best-effort).
            if buffer:
                logger.debug(f"[Kokoro] stream ended with {len(buffer)} bytes of un-EOS'd OGG data")
                yield bytes(buffer)
            # Diagnostic fingerprint: oggs_split=0 with a large trailing
            # remainder means the server's OGG/Opus pages carry no per-segment
            # EOS flag (libsndfile/libopus version skew), so the splitter never
            # fired and the browser receives one possibly-malformed blob — a
            # server-environment cause of "no audio". 2026-05-28.
            logger.info(f"[Kokoro] stream voice={voice} oggs_split={oggs_split} trailing={len(buffer)}")
        except Exception as e:
            logger.warning(f"Kokoro /tts/stream failed: {e!r} — falling back to non-streaming")
            audio = self.generate(text, voice, speed, **kwargs)
            if audio:
                yield audio

    def is_available(self) -> bool:
        """Check if Kokoro server is reachable."""
        return self._check_health(self.primary_server, timeout=self.fallback_timeout) or \
               self._check_health(self.fallback_server, timeout=1.0)

    def _get_server_url(self) -> str:
        """Get available server URL with fallback. Which one served, and why
        not the primary, is kept for the Test button (served_by)."""
        if self._check_health(self.primary_server, timeout=self.fallback_timeout):
            self.served_by = {"lane": "primary", "server": _shown(self.primary_server)}
            return self.primary_server
        why = self._health_why
        self.served_by = {"lane": "fallback", "server": _shown(self.fallback_server),
                          "primary": _shown(self.primary_server), "why": why}
        logger.info(f"Kokoro primary unavailable ({why}), using fallback: {_shown(self.fallback_server)}")
        return self.fallback_server

    def list_voices(self) -> list:
        """Return the built-in Kokoro voice list."""
        return KOKORO_VOICES

    def _check_health(self, server_url: str, timeout: float = None) -> bool:
        """True when it answers. Else _health_why says why, in words."""
        try:
            response = self._call('get', server_url, '/health', timeout=timeout)
        except requests.exceptions.SSLError as e:
            self._health_why = (str(e) if "certificate changed" in str(e)
                                else "its certificate is not the one paired (paste its new pairing string)")
            return False
        except requests.exceptions.Timeout:
            self._health_why = f"no answer within {timeout} s"
            return False
        except requests.exceptions.ConnectionError:
            self._health_why = "not reachable (off, wrong address or port, or a firewall)"
            return False
        except Exception as e:
            self._health_why = type(e).__name__
            return False
        if response.status_code == 401:
            self._health_why = "it refused the key (that computer was removed, or the pairing string is old)"
        elif response.status_code != 200:
            self._health_why = f"HTTP {response.status_code}"
        return response.status_code == 200


def _shown(server):
    """A server setting fit for a log: a pairing string's key and pin stay out."""
    return str(server).split("#", 1)[0] if worker_door.is_pairing(server) else server


KOKORO_VOICES = [
    {'voice_id': 'am_adam', 'name': 'Adam', 'category': 'American Male'},
    {'voice_id': 'am_echo', 'name': 'Echo', 'category': 'American Male'},
    {'voice_id': 'am_eric', 'name': 'Eric', 'category': 'American Male'},
    {'voice_id': 'am_fenrir', 'name': 'Fenrir', 'category': 'American Male'},
    {'voice_id': 'am_liam', 'name': 'Liam', 'category': 'American Male'},
    {'voice_id': 'am_michael', 'name': 'Michael', 'category': 'American Male'},
    {'voice_id': 'am_onyx', 'name': 'Onyx', 'category': 'American Male'},
    {'voice_id': 'am_puck', 'name': 'Puck', 'category': 'American Male'},
    {'voice_id': 'am_santa', 'name': 'Santa', 'category': 'American Male'},
    {'voice_id': 'af_alloy', 'name': 'Alloy', 'category': 'American Female'},
    {'voice_id': 'af_aoede', 'name': 'Aoede', 'category': 'American Female'},
    {'voice_id': 'af_bella', 'name': 'Bella', 'category': 'American Female'},
    {'voice_id': 'af_heart', 'name': 'Heart', 'category': 'American Female'},
    {'voice_id': 'af_jessica', 'name': 'Jessica', 'category': 'American Female'},
    {'voice_id': 'af_kore', 'name': 'Kore', 'category': 'American Female'},
    {'voice_id': 'af_nicole', 'name': 'Nicole', 'category': 'American Female'},
    {'voice_id': 'af_nova', 'name': 'Nova', 'category': 'American Female'},
    {'voice_id': 'af_river', 'name': 'River', 'category': 'American Female'},
    {'voice_id': 'af_sarah', 'name': 'Sarah', 'category': 'American Female'},
    {'voice_id': 'af_sky', 'name': 'Sky', 'category': 'American Female'},
    {'voice_id': 'bf_emma', 'name': 'Emma', 'category': 'British Female'},
    {'voice_id': 'bf_isabella', 'name': 'Isabella', 'category': 'British Female'},
    {'voice_id': 'bf_alice', 'name': 'Alice', 'category': 'British Female'},
    {'voice_id': 'bf_lily', 'name': 'Lily', 'category': 'British Female'},
    {'voice_id': 'bm_george', 'name': 'George', 'category': 'British Male'},
    {'voice_id': 'bm_daniel', 'name': 'Daniel', 'category': 'British Male'},
    {'voice_id': 'bm_lewis', 'name': 'Lewis', 'category': 'British Male'},
    {'voice_id': 'bm_fable', 'name': 'Fable', 'category': 'British Male'},
]
