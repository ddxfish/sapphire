"""DuplexConversationSource — open-speaker conversation front-door (v3 Rollout 2 full).

ONE duplex sd.Stream that is BOTH the cleaned-mic SOURCE and the TTS SINK. Because
input and output share a single stream/callback, whatever we write to `outdata` IS
the loopback reference for the same time slice — perfect sample-alignment for free,
which is the whole reason duplex beats two separate streams (no echo-delay estimation).

Signal path, per audio callback (device rate, e.g. 44.1k):
    outdata <- her TTS pull-buffer            full-rate playback (preserves voice quality)
    lpb      = outdata                         exactly what's playing = the echo reference
    mic      = indata
    mic,lpb  -> decimate to 16k -> DTLN(onnx)(mic,lpb) -> cleaned 16k mic (echo removed)
    cleaned  -> 512-sample blocks -> VAD worker (off the audio thread) -> driver.push_frame

DTLN runs IN the callback (numpy + onnxruntime, ~0.4ms/hop for the 256 model — proven
realtime, warmed up at start so no cold-start spike lands live). silero VAD runs OFF the
audio thread. The callback must stay fast and must NEVER raise — cffi swallows callback
exceptions, which silently mutes the whole stream (a real bug we hit before).

Dual role / lifecycle:
    SOURCE: start() opens the stream + VAD worker (raises on failure -> the fail-safe
            handoff restores wakeword); close() tears it down on mode exit.
    SINK:   start() also (idempotently) arms a turn; feed_chunk() enqueues TTS; finish()
            + wait() drain; stop() (barge-in) flushes queued audio but KEEPS the stream
            open (it's also the live mic).
"""
import base64
import io
import logging
import queue
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np
from core.audio.backend import sd
import soundfile as sf

from core.event_bus import publish, Events

logger = logging.getLogger(__name__)

BLOCK_LEN, BLOCK_SHIFT = 512, 128     # DTLN block + hop @16k
DTLN_RATE = 16000
VAD_BLOCK = 512                       # silero wants 512-sample 16k frames


class _OnnxDTLN:
    """2-stage DTLN-aec on onnxruntime (CPU). State tensors are explicit I/O."""

    def __init__(self, models_dir, size):
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.intra_op_num_threads = 1
        so.inter_op_num_threads = 1
        p = Path(models_dir)
        self.o1 = ort.InferenceSession(str(p / f"dtln_aec_{size}_1.onnx"), sess_options=so, providers=["CPUExecutionProvider"])
        self.o2 = ort.InferenceSession(str(p / f"dtln_aec_{size}_2.onnx"), sess_options=so, providers=["CPUExecutionProvider"])
        self.in1 = [i.name for i in self.o1.get_inputs()]; self.out1 = [o.name for o in self.o1.get_outputs()]
        self.in2 = [i.name for i in self.o2.get_inputs()]; self.out2 = [o.name for o in self.o2.get_outputs()]
        self.s1 = np.zeros([int(d) for d in self.o1.get_inputs()[1].shape], np.float32)
        self.s2 = np.zeros([int(d) for d in self.o2.get_inputs()[1].shape], np.float32)

    def stage1(self, mag, lmag):
        r = self.o1.run(self.out1, {self.in1[0]: mag, self.in1[1]: self.s1, self.in1[2]: lmag}); self.s1 = r[1]; return r[0]

    def stage2(self, est, lpb):
        r = self.o2.run(self.out2, {self.in2[0]: est, self.in2[1]: self.s2, self.in2[2]: lpb}); self.s2 = r[1]; return r[0]

    def reset(self):
        self.s1 = np.zeros_like(self.s1)
        self.s2 = np.zeros_like(self.s2)


def _resample(x, src, dst):
    if src == dst or len(x) == 0:
        return np.asarray(x, np.float32)
    n = int(len(x) * dst / src)
    if n <= 0:
        return np.zeros(0, np.float32)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


class DuplexConversationSource:
    def __init__(self, driver, gate, dtln_model="256", models_dir=None,
                 device=None, dev_rate=None, blocksize=512, aec_delay_ms=0,
                 barge_guard_ms=300, barge_rms_floor=0.03, prebuffer_ms=400):
        self._driver = driver
        self._gate = gate
        self._size = str(dtln_model)
        self._blocksize = int(blocksize)
        self._delay_ms = float(aec_delay_ms)   # 0 off; <0 auto from stream.latency; >0 manual
        self._delay_16k = 0
        self._guard_s = float(barge_guard_ms) / 1000.0   # ignore barge while DTLN locks onto her echo onset
        self._barge_floor = float(barge_rms_floor)       # reject quiet residual echo as a false barge
        self._play_start = 0.0
        self._last_rms_log = 0.0
        self._prebuffer_ms = float(prebuffer_ms)         # cushion before draining playback (anti-chop)
        self._prebuffer = 0
        self._released = False
        self._finishing = False
        self._underflows = 0
        root = Path(__file__).absolute().parents[2]      # .absolute() not .resolve() (symlink trap)
        self._models_dir = models_dir or (root / "user" / "models" / "dtln")

        sys_ = getattr(driver, "system", None)
        tts = getattr(sys_, "tts", None)
        self._dev_rate = int(dev_rate or getattr(tts, "output_rate", None) or 48000)
        if device is not None:
            self._device = device
        else:
            in_idx = None
            try:
                from core.audio import get_device_manager
                cfg = get_device_manager().find_input_device(target_rate=self._dev_rate)
                if cfg is not None:                      # DeviceConfig -> sd wants the int index, not the object
                    in_idx = cfg.device_index
            except Exception:
                in_idx = None
            self._device = (in_idx, getattr(tts, "output_device", None))

        self._eng = None
        self._stream = None
        self._vad_q = queue.Queue(maxsize=64)
        self._worker = None
        self._closing = threading.Event()
        self._cb_err = False

        # callback-thread-only DTLN buffers
        self._inb = np.zeros(BLOCK_LEN, np.float32)
        self._inl = np.zeros(BLOCK_LEN, np.float32)
        self._outb = np.zeros(BLOCK_LEN, np.float32)
        self._mic16 = np.zeros(0, np.float32)
        self._lpb16 = np.zeros(0, np.float32)
        self._cleaned = np.zeros(0, np.float32)

        # sink (output) state
        self._out_lock = threading.Lock()
        self._out_chunks = deque()
        self._stop_flag = threading.Event()
        self._playing = False

    # ── DTLN one hop (128 cleaned samples @16k) ───────────────────────────────
    def _hop(self, mic_hop, lpb_hop):
        inb, inl, outb = self._inb, self._inl, self._outb
        inb[:-BLOCK_SHIFT] = inb[BLOCK_SHIFT:]; inb[-BLOCK_SHIFT:] = mic_hop
        inl[:-BLOCK_SHIFT] = inl[BLOCK_SHIFT:]; inl[-BLOCK_SHIFT:] = lpb_hop
        fft = np.fft.rfft(inb).astype("complex64")
        mag = np.abs(fft).reshape(1, 1, -1).astype("float32")
        lmag = np.abs(np.fft.rfft(inl).astype("complex64")).reshape(1, 1, -1).astype("float32")
        mask = self._eng.stage1(mag, lmag)
        est = np.fft.irfft(fft * mask).reshape(1, 1, -1).astype("float32")
        ob = self._eng.stage2(est, inl.reshape(1, 1, -1).astype("float32"))
        outb[:-BLOCK_SHIFT] = outb[BLOCK_SHIFT:]; outb[-BLOCK_SHIFT:] = 0.0
        outb += np.squeeze(ob)
        return outb[:BLOCK_SHIFT].copy()

    # ── audio callback (REAL-TIME — fast, never raises) ───────────────────────
    def _callback(self, indata, outdata, frames, time_info, status):
        try:
            if status and getattr(status, "output_underflow", False):
                self._underflows += 1          # callback ran late -> PortAudio gap (vs our buffer drain)
            out = self._pull_output(frames)
            outdata[:, 0] = out
            mic = indata[:, 0].astype(np.float32)

            self._mic16 = np.concatenate((self._mic16, _resample(mic, self._dev_rate, DTLN_RATE)))
            self._lpb16 = np.concatenate((self._lpb16, _resample(out, self._dev_rate, DTLN_RATE)))
            blocks = []
            while len(self._mic16) >= BLOCK_SHIFT and len(self._lpb16) >= BLOCK_SHIFT:
                mh = self._mic16[:BLOCK_SHIFT]; self._mic16 = self._mic16[BLOCK_SHIFT:]
                lh = self._lpb16[:BLOCK_SHIFT]; self._lpb16 = self._lpb16[BLOCK_SHIFT:]
                blocks.append(self._hop(mh, lh))
            if blocks:
                self._cleaned = np.concatenate([self._cleaned] + blocks)
                while len(self._cleaned) >= VAD_BLOCK:
                    blk = self._cleaned[:VAD_BLOCK]; self._cleaned = self._cleaned[VAD_BLOCK:]
                    i16 = np.clip(blk * 32768.0, -32768, 32767).astype(np.int16)
                    try:
                        self._vad_q.put_nowait(i16)
                    except queue.Full:
                        pass  # VAD fell behind; drop a block rather than grow unbounded
        except Exception as e:
            try:
                outdata.fill(0.0)
            except Exception:
                pass
            if not self._cb_err:
                self._cb_err = True
                logger.error(f"[CONV] duplex callback error (silencing further logs): {e}")

    def _pull_output(self, n):
        out = np.zeros(n, np.float32)
        with self._out_lock:
            if not self._released:                    # pre-roll: build a cushion before draining
                buffered = sum(len(c) for c in self._out_chunks)
                if buffered < self._prebuffer and not self._finishing:
                    return out                        # hold (silence) until the cushion fills
                self._released = True
            filled = 0
            while filled < n and self._out_chunks:
                head = self._out_chunks[0]
                take = min(n - filled, len(head))
                out[filled:filled + take] = head[:take]
                if take == len(head):
                    self._out_chunks.popleft()
                else:
                    self._out_chunks[0] = head[take:]
                filled += take
        return out

    # ── VAD worker (off the audio thread) ─────────────────────────────────────
    def _barge_ok(self, is_sp, rms):
        """While her audio plays, reject false barges from her own residual echo:
          (a) onset guard — DTLN needs a few hundred ms to lock onto a new echo;
          (b) energy floor — post-AEC residual is quiet, a real barge is louder.
        When she's NOT playing, speech passes straight through (normal endpointing)."""
        if not is_sp:
            return False
        if self._playing:
            if self._guard_s > 0 and (time.time() - self._play_start) < self._guard_s:
                return False
            if rms < self._barge_floor:
                return False
        return is_sp

    def _maybe_log_playback(self, rms):
        """1/sec choppiness telemetry during her playback (independent of speech detection):
        out_buf draining -> TTS below realtime/CPU-starved; underflows climbing -> callback late."""
        if not self._playing:
            return
        now = time.time()
        if now - self._last_rms_log > 1.0:
            self._last_rms_log = now
            with self._out_lock:
                buf_ms = int(sum(len(c) for c in self._out_chunks) / self._dev_rate * 1000)
            logger.info(f"[CONV] playback: out_buf={buf_ms}ms underflows={self._underflows} "
                        f"released={self._released} residual_rms={rms:.4f}")

    def _vad_loop(self):
        while not self._closing.is_set():
            try:
                blk = self._vad_q.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                rms = float(np.sqrt(np.mean((blk.astype(np.float64) / 32768.0) ** 2)))
                self._maybe_log_playback(rms)
                is_sp = self._barge_ok(self._gate.is_speech(blk), rms)
                self._driver.push_frame(blk.tobytes(), is_sp)
            except Exception as e:
                logger.debug(f"[CONV] vad/push failed: {e}")

    # ── SOURCE lifecycle ──────────────────────────────────────────────────────
    def start(self):
        """Open the duplex stream on first call; re-arm output on later calls (idempotent)."""
        self._stop_flag.clear()
        self._released = False
        self._finishing = False
        self._prebuffer = int(self._prebuffer_ms / 1000.0 * self._dev_rate)
        with self._out_lock:
            self._out_chunks.clear()
        if self._stream is not None:
            return
        self._eng = _OnnxDTLN(self._models_dir, self._size)
        self._warmup()
        self._worker = threading.Thread(target=self._vad_loop, daemon=True, name="conv-duplex-vad")
        self._worker.start()
        self._stream = sd.Stream(
            samplerate=self._dev_rate, blocksize=self._blocksize, device=self._device,
            channels=(1, 1), dtype="float32", latency="high", callback=self._callback,
        )
        self._stream.start()
        self._apply_aec_delay()        # align loopback to the mic echo (round-trip latency)
        logger.info(f"[CONV] duplex source up: DTLN-{self._size} @ {self._dev_rate}Hz->16k, "
                    f"device={self._device}, block={self._blocksize}")

    def _apply_aec_delay(self):
        """Delay the loopback reference to line up with the mic echo (output+input round-trip).
        DTLN can't cancel an echo it sees too early — misalignment is the #1 open-speaker leak."""
        delay_ms = self._delay_ms
        if delay_ms < 0:                       # auto from the stream's reported latency
            try:
                lat = self._stream.latency
                in_lat, out_lat = lat if isinstance(lat, (tuple, list)) else (lat, lat)
                delay_ms = (float(in_lat) + float(out_lat)) * 1000.0
            except Exception:
                delay_ms = 0.0
        delay_ms = max(0.0, min(delay_ms, 150.0))
        self._delay_16k = int(delay_ms / 1000.0 * DTLN_RATE)
        if self._delay_16k:
            self._lpb16 = np.zeros(self._delay_16k, np.float32)   # prepend silence = delay the ref
        logger.info(f"[CONV] AEC loopback delay {self._delay_16k} samples "
                    f"({self._delay_16k / DTLN_RATE * 1000:.0f}ms) | stream.latency="
                    f"{getattr(self._stream, 'latency', None)}")

    def _warmup(self):
        """JIT the onnx graph so the cold first-inference never lands in a live callback."""
        z = np.zeros(BLOCK_SHIFT, np.float32)
        for _ in range(8):
            self._hop(z, z)
        self._inb[:] = 0; self._inl[:] = 0; self._outb[:] = 0
        self._eng.reset()

    def close(self):
        self._closing.set()
        self._stop_flag.set()
        w = self._worker
        if w is not None and w.is_alive() and w is not threading.current_thread():
            w.join(timeout=2.0)
        s = self._stream
        self._stream = None
        if s is not None:
            try:
                s.stop(); s.close()
            except Exception as e:
                logger.debug(f"[CONV] duplex stream close error: {e}")

    # ── SINK role (TTS output) ────────────────────────────────────────────────
    def feed_chunk(self, chunk):
        if self._stop_flag.is_set():
            return
        if not (chunk and chunk.get("audio_b64")):
            return
        try:
            pcm = self._decode(chunk)
        except Exception as e:
            logger.warning(f"[CONV] tts chunk decode failed: {e}")
            return
        if pcm is None or len(pcm) == 0:
            return
        pause = chunk.get("pause_after_ms", 0) or 0
        with self._out_lock:
            self._out_chunks.append(pcm)
            if pause > 0:
                self._out_chunks.append(np.zeros(int(self._dev_rate * pause / 1000.0), np.float32))
        if not self._playing:
            self._playing = True
            self._play_start = time.time()       # starts the barge-guard window for this turn
            publish(Events.TTS_PLAYING, {"surface": "web"})

    def finish(self):
        """No more chunks — release the pre-roll even if the cushion isn't full (short replies)."""
        self._finishing = True

    def stop(self):
        """Barge-in / abort: drop queued audio (cut her off now), keep the stream open."""
        self._stop_flag.set()
        with self._out_lock:
            self._out_chunks.clear()
        if self._playing:
            self._playing = False
            publish(Events.TTS_STOPPED, {"surface": "web"})

    def wait(self, timeout=180):
        """Block until the output buffer drains (turn audio finished) or a barge-in stops us."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._stop_flag.is_set():
                return
            with self._out_lock:
                empty = not self._out_chunks
            if empty:
                break
            time.sleep(0.02)
        time.sleep(0.12)  # grace for the device buffer tail
        if self._playing:
            self._playing = False
            publish(Events.TTS_STOPPED, {"surface": "web"})

    def _decode(self, chunk):
        b = base64.b64decode(chunk["audio_b64"])
        data, sr = sf.read(io.BytesIO(b))
        if data.ndim > 1:
            data = data.mean(axis=1)
        return _resample(data.astype(np.float32), sr, self._dev_rate)
