"""TwilioConversationSource — conversation source+sink over an RtpSession.

Clone of core/conversation/browser_source.py with a telephony codec skin. The
conversation engine is unchanged: this just moves audio between the driver and a
phone call's RTP.

  SOURCE: RtpSession.read() -> μ-law 8k -> int16 16k -> 512-frame -> SpeechGate ->
          driver.push_frame  (same loop shape as LocalMicSource / BrowserSource).
  SINK:   feed_chunk(tts_chunk) -> decode OGG/Opus -> resample -> 8k μ-law 20ms
          frames -> RtpSession.write() (a paced thread in the session sends them).

Drain is simpler than the browser: WE pace the RTP send, so "done talking" = the
session's outbound queue emptied — no playback_done round-trip needed. barge (stop)
flushes the outbound queue immediately.
"""
import base64
import io
import logging
import threading

import numpy as np
import soundfile as sf

from core.event_bus import publish, Events
from .codec import phone_to_engine, engine_to_phone_frames, FRAME_SAMPLES

logger = logging.getLogger(__name__)

_ENGINE_FRAME = 512          # silero wants 512-sample 16k frames

# ── Turn cues (v2.9 soundscape, phone surface) ───────────────────────────────
# Tiny pre-encoded chimes written STRAIGHT to RTP (bypassing the sink, whose
# barge-in stop flag would swallow them). Encoded once per process.
_CUE_FILES = {"think": "think_pulse.wav",             # still working (1/s pulse)
              "barge": "respond_ding.wav",            # you cut in — floor's yours
              "hangup": "hangup_bye.wav",             # goodbye chime before BYE
              "error": "error_glitch.wav"}            # turn failed — spoken "sorry, I glitched"
# (respond_lift retired as barge cue 2026-07-15 — two notes/410ms stomped the
# caller's first words; the single short ding stays out of their way.)
_cue_frames = {}
_cue_lock = threading.Lock()


def _frames_for(name):
    with _cue_lock:
        if name in _cue_frames:
            return _cue_frames[name]
        frames = []
        try:
            from pathlib import Path
            path = Path(__file__).parent / "sounds" / _CUE_FILES[name]
            data, sr = sf.read(str(path))
            if data.ndim > 1:
                data = data.mean(axis=1)
            pcm16 = (np.clip(data, -1, 1) * 32767).astype(np.int16)
            frames = engine_to_phone_frames(pcm16, sr)
        except Exception as e:
            logger.warning(f"[TWILIO] cue '{name}' load failed: {e}")
        _cue_frames[name] = frames
        return frames


class TwilioConversationSource:
    def __init__(self, driver, gate, session):
        self.driver = driver
        self.gate = gate
        self.session = session
        self._buf16 = np.zeros(0, dtype=np.int16)
        self._running = False
        self._thread = None
        self._stop_flag = threading.Event()
        self._playing = False
        self.cues_enabled = False       # daemon wires driver.set_cues + flips this

    def play_cue(self, name):
        """Play a turn cue on the wire. Direct session.write — cues must survive
        the sink's post-barge stop flag and never touch engine/turn state."""
        if not self.session._alive.is_set():
            return
        for frame in _frames_for(name):
            self.session.write(frame)

    # ── SOURCE lifecycle (driver re-calls start() each turn to re-arm) ────────
    def start(self):
        self._stop_flag.clear()
        if self._running:
            return
        self.gate.reset()
        self.driver.reset()
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="twilio-src")
        self._thread.start()
        logger.info("[TWILIO] conversation source started")

    def close(self):
        self._running = False
        self._stop_flag.set()
        try:
            self.session.stop()
        except Exception:
            pass
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        logger.info("[TWILIO] conversation source closed")

    def _loop(self):
        while self._running and self.session._alive.is_set():
            ulaw = self.session.read(timeout=0.5)
            if ulaw is None:
                continue
            pcm16 = phone_to_engine(ulaw)                 # -> int16 16k
            self._buf16 = np.concatenate((self._buf16, pcm16))
            while len(self._buf16) >= _ENGINE_FRAME:
                frame = self._buf16[:_ENGINE_FRAME]
                self._buf16 = self._buf16[_ENGINE_FRAME:]
                try:
                    is_sp = self.gate.is_speech(frame)
                    self.driver.push_frame(frame.tobytes(), is_sp)
                except Exception as e:
                    logger.error(f"[TWILIO] frame processing failed: {e}")
        if not self.session._alive.is_set():
            self._running = False

    def _ev_payload(self):
        """Identity for TTS events — the web UI ignores surface='phone' (Phase I:
        a call's playback must not light/drive the browser's TTS controls)."""
        return {"surface": "phone",
                "chat": getattr(self.driver, "_chat_name", None)}

    # ── SINK role (driver contract) ──────────────────────────────────────────
    def feed_chunk(self, chunk):
        if self._stop_flag.is_set() or not (chunk and chunk.get("audio_b64")):
            return
        try:
            raw = base64.b64decode(chunk["audio_b64"])
            data, sr = sf.read(io.BytesIO(raw))
            if data.ndim > 1:
                data = data.mean(axis=1)
            pcm16 = (np.clip(data, -1, 1) * 32767).astype(np.int16) if data.dtype.kind == "f" \
                else data.astype(np.int16)
        except Exception as e:
            logger.warning(f"[TWILIO] tts chunk decode failed: {e}")
            return
        if not self._playing:
            self._playing = True
            publish(Events.TTS_PLAYING, self._ev_payload())
        for frame in engine_to_phone_frames(pcm16, sr):
            self.session.write(frame)

    def finish(self):
        pass                                              # nothing to flush; wait() drains

    def stop(self):
        """Barge-in: drop queued outbound audio so she cuts off immediately."""
        self._stop_flag.set()
        self.session.flush()
        if self._playing:
            self._playing = False
            publish(Events.TTS_STOPPED, self._ev_payload())

    def wait(self, timeout=180):
        """Block until the outbound RTP queue drains (she finished speaking)."""
        import time
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._stop_flag.is_set() or not self.session._alive.is_set():
                break
            if self.session.outbound_idle():
                break
            time.sleep(0.02)
        time.sleep(0.1)                                   # tail so the last frames send
        if self._playing:
            self._playing = False
            publish(Events.TTS_STOPPED, self._ev_payload())
        # <<HANG UP>> sentinel: her goodbye has fully drained — end the call now.
        # The io loop sees the session die and sends the (Route-correct) BYE.
        if getattr(self.session, "_hangup_after_drain", False):
            if self.cues_enabled:
                frames = _frames_for("hangup")
                if frames:
                    # The goodbye chime plays IN FULL before the BYE — pace is
                    # 20ms/frame, so wait it out plus a small network tail.
                    for f in frames:
                        self.session.write(f)
                    time.sleep(len(frames) * 0.02 + 0.15)
            logger.info("[TWILIO] goodbye drained — hanging up (sentinel)")
            self.session.stop()
