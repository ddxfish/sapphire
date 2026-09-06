"""PumpkinChunker — local streaming TTS sink.

Consumes the StreamingTTSPump's `tts_chunk` events and plays them on local speakers
via sounddevice: streaming (one persistent OutputStream across chunks, no gaps) and
cancellable (barge-in). Decode/play is lifted from the proven
`tts_client._generate_and_play_audio_stream` path.

Non-blocking by design: `feed_chunk` enqueues and returns immediately; a background
worker plays the queue. That keeps the LLM/UI token stream flowing while audio plays.

Lifecycle per turn:  start() -> feed_chunk()xN -> finish()   (worker drains, then closes)
Barge-in:            stop()  (abort: drop queue, cut audio, close)
"""
import base64
import io
import logging
import queue
import threading

import numpy as np
from core.audio.backend import sd
import soundfile as sf

from core.event_bus import publish, Events

logger = logging.getLogger(__name__)


class PumpkinChunker:
    def __init__(self, output_device=None, output_rate=48000, stream_factory=None):
        # output_device / output_rate come from the existing TTSClient (already
        # boot-resolved to a working device); resample every chunk to output_rate.
        self.output_device = output_device
        self.output_rate = int(output_rate or 48000)
        self._stream_factory = stream_factory or self._default_stream_factory

        self.lock = threading.Lock()
        self.should_stop = threading.Event()
        self._is_playing = False
        self._finished = False
        self._stream = None
        self._queue = queue.Queue()
        self._worker = None

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self):
        """Begin a turn: clear state and spin up the playback worker.

        Each turn gets a FRESH stop Event, captured by its worker: a zombie
        worker that outlived its join timeout (wedged PortAudio write) keeps
        its own already-set Event, so the next turn's start() can't revive
        its loop by clearing a shared flag (2026-07-06 herring hunt)."""
        self.should_stop = threading.Event()
        self._finished = False
        self._drain_queue()
        self._worker = threading.Thread(target=self._run, args=(self.should_stop,),
                                        daemon=True, name="pumpkin-chunker")
        self._worker.start()

    def feed_chunk(self, chunk):
        """Non-blocking: enqueue a tts_chunk event dict for playback."""
        if self.should_stop.is_set():
            return
        if chunk and chunk.get("audio_b64"):
            self._queue.put(chunk)

    def finish(self):
        """No more chunks coming: worker drains the queue, then closes the stream."""
        self._finished = True

    def stop(self):
        """Barge-in / abort: drop the queue, cut audio now, close the stream."""
        self.should_stop.set()
        self._drain_queue()
        w = self._worker
        if w and w.is_alive() and w is not threading.current_thread():
            w.join(timeout=2.0)
        self._close_stream()

    # ── worker ───────────────────────────────────────────────────────────────
    def _run(self, stop_evt):
        try:
            while not stop_evt.is_set():
                try:
                    chunk = self._queue.get(timeout=0.1)
                except queue.Empty:
                    if self._finished:
                        break
                    continue
                self._play_one(chunk, stop_evt)
        finally:
            # Fence: only the CURRENT worker may close the stream. A zombie
            # that outlived its join must not tear down the next turn's
            # stream (or fire TTS_STOPPED) on its way out.
            if self._worker is threading.current_thread():
                self._close_stream()

    def _play_one(self, chunk, stop_evt):
        try:
            pcm = self._decode(chunk)
        except Exception as e:
            logger.warning(f"[PUMPKIN] chunk decode failed: {e}")
            return
        if pcm is None or len(pcm) == 0:
            return

        with self.lock:
            if stop_evt.is_set():
                return
            if self._stream is None:
                try:
                    self._stream = self._stream_factory()
                    self._is_playing = True
                    publish(Events.TTS_PLAYING, {"surface": "local"})
                except Exception as e:
                    logger.error(f"[PUMPKIN] OutputStream open failed: {e}")
                    stop_evt.set()
                    return

        # 100ms slices for interruptibility (matches tts_client streaming path)
        slice_size = max(1, int(self.output_rate * 0.1))
        for i in range(0, len(pcm), slice_size):
            if stop_evt.is_set():
                return
            try:
                self._stream.write(pcm[i:i + slice_size].reshape(-1, 1))
            except Exception as e:
                logger.warning(f"[PUMPKIN] write error: {e}")
                stop_evt.set()  # own turn's event only — a zombie can't poison the next turn
                return

        pause = chunk.get("pause_after_ms", 0) or 0
        if pause > 0:
            # cancellable: wakes immediately on stop()
            stop_evt.wait(timeout=pause / 1000.0)

    # ── decode (testable without a device) ───────────────────────────────────
    def _decode(self, chunk):
        b = base64.b64decode(chunk["audio_b64"])
        data, sr = sf.read(io.BytesIO(b))
        if data.ndim > 1:
            data = data.mean(axis=1)
        if sr != self.output_rate:
            data = self._resample(data, sr, self.output_rate)
        return data.astype(np.float32)

    @staticmethod
    def _resample(audio_data, from_rate, to_rate):
        if from_rate == to_rate:
            return audio_data
        old_length = len(audio_data)
        new_length = int(old_length * (to_rate / from_rate))
        if new_length == 0:
            return np.array([], dtype=audio_data.dtype)
        old_indices = np.arange(old_length)
        new_indices = np.linspace(0, old_length - 1, new_length)
        resampled = np.interp(new_indices, old_indices, audio_data.astype(np.float64))
        return resampled.astype(audio_data.dtype)

    # ── helpers ──────────────────────────────────────────────────────────────
    def _default_stream_factory(self):
        s = sd.OutputStream(samplerate=self.output_rate, device=self.output_device,
                            channels=1, dtype="float32")
        s.start()
        return s

    def _close_stream(self):
        with self.lock:
            was = self._is_playing
            self._is_playing = False
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception as e:
                    logger.debug(f"[PUMPKIN] stream close error: {e}")
                self._stream = None
        if was:
            publish(Events.TTS_STOPPED, {"surface": "local"})

    def _drain_queue(self):
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass
