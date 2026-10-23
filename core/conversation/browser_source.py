"""Browser conversation source+sink (v3 browser endpoint) — the WS adapter.

SOURCE: the WS receive coroutine pushes raw 16k mono int16 PCM via push_pcm();
a worker thread reassembles 512-sample silero frames, scores VAD, and feeds
driver.push_frame — same loop shape as LocalMicSource, frames just arrive off
the network instead of sounddevice. Drop-oldest backpressure (the duplex VAD
queue's policy).

SINK (driver contract start/feed_chunk/finish/wait/stop — the duplex source's
dual role): tts_chunk events are forwarded verbatim to the browser through
send_fn (thread-safe; the WS route bridges onto its asyncio loop). Playback
happens in the BROWSER, so server queue-drain != audio finished: finish()
sends turn_audio_done, the client replies playback_done when ITS queue
empties, and wait() blocks on that event (pitfall 3,
tmp/v3-conversation-websocket.md). The client replies ONLY to turn_audio_done,
so a stale reply can't leak into the next turn; a lost reply means the
connection died, and close() unblocks the waiter — a dead WS must never wedge
a turn thread for the 180s timeout.

This object is the acquire_audio session for the handoff: start() spins the
worker (idempotent — the driver re-calls it each turn to re-arm, like the
duplex source), close() stops everything and unblocks any waiter.
"""
import logging
import queue
import threading

import numpy as np

from core.event_bus import publish, Events

logger = logging.getLogger(__name__)

_FRAME_SAMPLES = 512            # silero wants 512-sample 16k frames
_FRAME_BYTES = _FRAME_SAMPLES * 2


class BrowserConversationSource:
    def __init__(self, driver, gate, send_fn, inbound_max=64):
        self.driver = driver
        self.gate = gate
        self._send = send_fn            # thread-safe callable(dict); never raises
        self._q = queue.Queue(maxsize=inbound_max)
        self._pcm_buf = b""
        self._running = False
        self._thread = None
        self._stop_flag = threading.Event()      # barge/abort for the current turn
        self._playback_done = threading.Event()  # client drained its audio queue
        self._playing = False

    # ── SOURCE lifecycle (also the driver's per-turn sink re-arm; idempotent) ──
    def start(self):
        self._stop_flag.clear()
        self._playback_done.clear()
        if self._running:
            return
        self.gate.reset()
        self.driver.reset()
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="conv-ws-source")
        self._thread.start()
        logger.info("[CONV] browser source started")

    def close(self):
        self._running = False
        self._stop_flag.set()
        self._playback_done.set()       # unblock wait() — the WS is gone
        self._send({"type": "bye"})     # nudge a still-alive client to close its side
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        logger.info("[CONV] browser source closed")

    # ── SOURCE: WS coroutine -> worker thread ───────────────────────────────
    def push_pcm(self, data):
        """Called from the WS receive coroutine. Never blocks: drop-oldest on overflow."""
        try:
            self._q.put_nowait(data)
        except queue.Full:
            try:
                self._q.get_nowait()
            except queue.Empty:
                pass
            try:
                self._q.put_nowait(data)
            except queue.Full:
                pass

    def on_playback_done(self):
        """Client control frame: its playback queue drained (reply to turn_audio_done)."""
        self._playback_done.set()

    def _loop(self):
        while self._running:
            try:
                data = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            self._pcm_buf += data
            while len(self._pcm_buf) >= _FRAME_BYTES:
                frame = self._pcm_buf[:_FRAME_BYTES]
                self._pcm_buf = self._pcm_buf[_FRAME_BYTES:]
                try:
                    is_sp = self.gate.is_speech(np.frombuffer(frame, dtype=np.int16))
                    self.driver.push_frame(frame, is_sp)
                except Exception as e:
                    logger.error(f"[CONV] browser frame processing failed: {e}")

    # ── SINK role (driver contract) ──────────────────────────────────────────
    def feed_chunk(self, chunk):
        if self._stop_flag.is_set() or not (chunk and chunk.get("audio_b64")):
            return
        self._playback_done.clear()     # audio in flight again
        if not self._playing:
            self._playing = True
            publish(Events.TTS_PLAYING, {"surface": "web"})
        self._send({"type": "tts_chunk",
                    **{k: chunk.get(k) for k in
                       ("audio_b64", "content_type", "index", "boundary",
                        "pause_after_ms", "text", "stream_id")}})

    def finish(self):
        """No more chunks this turn — the client replies playback_done when it drains."""
        if not self._stop_flag.is_set():
            self._send({"type": "turn_audio_done"})

    def stop(self):
        """Barge-in / abort: halt browser playback now, drop in-flight stragglers."""
        self._stop_flag.set()
        self._playback_done.set()       # unblock wait()
        self._send({"type": "barge_stop"})
        if self._playing:
            self._playing = False
            publish(Events.TTS_STOPPED, {"surface": "web"})

    def wait(self, timeout=180):
        """Block until the CLIENT reports playback done (or barge / close / timeout)."""
        self._playback_done.wait(timeout=timeout)
        if self._playing:
            self._playing = False
            publish(Events.TTS_STOPPED, {"surface": "web"})
