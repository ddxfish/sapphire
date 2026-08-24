"""Queue-based streaming PCM playback for Discord voice channels."""

from __future__ import annotations

import logging
import threading
import time

from plugins.discord.transport.discord_audio import (
    DISCORD_CHANNELS,
    DISCORD_SAMPLE_RATE,
    DISCORD_SAMPLE_WIDTH,
)

logger = logging.getLogger(__name__)

# 20 ms @ 48 kHz stereo s16
DISCORD_FRAME_BYTES = int(DISCORD_SAMPLE_RATE * 0.02) * DISCORD_CHANNELS * DISCORD_SAMPLE_WIDTH
_SILENCE_FRAME = b'\x00' * DISCORD_FRAME_BYTES


class StreamingVoicePlayback:
    """Thread-safe PCM queue with the conversation-driver sink contract."""

    def __init__(self):
        self._lock = threading.Lock()
        self._pending = bytearray()
        self._finished = False
        self._stopped = False
        self._playing = False

    @property
    def is_playing(self) -> bool:
        with self._lock:
            return self._playing and not self._stopped

    def feed(self, pcm_stereo: bytes) -> None:
        if not pcm_stereo:
            return
        with self._lock:
            if self._stopped:
                return
            self._pending.extend(pcm_stereo)
            self._playing = True

    def read_frame(self) -> bytes:
        """Called from py-cord's audio thread via QueuedPCMSource.read()."""
        with self._lock:
            if self._stopped:
                self._playing = False
                return b''
            if len(self._pending) >= DISCORD_FRAME_BYTES:
                frame = bytes(self._pending[:DISCORD_FRAME_BYTES])
                del self._pending[:DISCORD_FRAME_BYTES]
                return frame
            if self._finished and not self._pending:
                self._playing = False
                return b''
        return _SILENCE_FRAME

    # ── Driver sink contract (Phase 2) ───────────────────────────────────────
    def start(self) -> None:
        with self._lock:
            self._finished = False
            self._stopped = False

    def begin_turn(self) -> None:
        """Reset per-turn state without tearing down the Discord play session."""
        with self._lock:
            self._finished = False
            self._stopped = False
            self._pending.clear()
            self._playing = False

    def feed_chunk(self, chunk: dict) -> None:
        from plugins.discord.transport.discord_tts_chunks import decode_tts_chunk

        pcm = decode_tts_chunk(chunk)
        self.feed(pcm)

    def finish(self) -> None:
        with self._lock:
            self._finished = True

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            self._finished = True
            self._pending.clear()
            self._playing = False

    def wait(self, timeout: float = 180.0) -> None:
        deadline = time.time() + max(0.0, float(timeout))
        while time.time() < deadline:
            with self._lock:
                if self._stopped:
                    return
                if self._finished and not self._pending:
                    return
            time.sleep(0.02)
        logger.debug('StreamingVoicePlayback.wait timed out after %.1fs', timeout)

    def close(self) -> None:
        self.stop()

    def pending_bytes(self) -> int:
        with self._lock:
            return len(self._pending)


def build_pcm_audio_source(playback: StreamingVoicePlayback):
    """Build a py-cord/discord AudioSource for the given playback queue."""
    import discord

    class QueuedPCMSource(discord.AudioSource):
        def __init__(self, sink: StreamingVoicePlayback):
            self._sink = sink

        def read(self) -> bytes:
            return self._sink.read_frame()

        def is_opus(self) -> bool:
            return False

        def cleanup(self) -> None:
            # Playback teardown is owned by DiscordExecution.stop/finish — cleanup
            # runs when py-cord replaces the source and must not close a reused sink.
            pass

    return QueuedPCMSource(playback)
