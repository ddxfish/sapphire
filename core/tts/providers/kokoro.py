"""Kokoro TTS provider — local HTTP server on port 5012."""
import logging
import time
from typing import Iterator, List, Optional, Tuple

import requests
from core import net
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
        logger.info(f"Kokoro TTS provider: {self.primary_server}")

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
                response = net.post(f"{server_url}/tts", json={
                    'text': text.replace("*", ""),
                    'voice': voice,
                    'speed': clamped_speed,
                    'pitch': float(kwargs.get('pitch') or 1.0),
                }, timeout=60)
                if response.status_code == 200:
                    return response.content
                logger.error(f"Kokoro server error: {response.status_code}")
                last_error = f"HTTP {response.status_code}"
                if 400 <= response.status_code < 500:
                    break  # client error — retrying won't help
            except Exception as e:
                last_error = e

            if attempt < len(delays):
                logger.warning(f"Kokoro TTS attempt {attempt + 1} failed, retrying in {delays[attempt]}s...")
                time.sleep(delays[attempt])

        logger.error(f"Kokoro generate failed after {1 + len(delays)} attempts: {last_error}")
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
            response = net.post(
                f"{server_url}/tts/stream",
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
        """Get available server URL with fallback."""
        if self._check_health(self.primary_server, timeout=self.fallback_timeout):
            return self.primary_server
        logger.info(f"Kokoro primary unavailable, using fallback: {self.fallback_server}")
        return self.fallback_server

    def list_voices(self) -> list:
        """Return the built-in Kokoro voice list."""
        return KOKORO_VOICES

    def _check_health(self, server_url: str, timeout: float = None) -> bool:
        try:
            response = net.get(f"{server_url}/health", timeout=timeout)
            return response.status_code == 200
        except Exception:
            return False


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
