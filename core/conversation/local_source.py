"""Local mic source for conversation mode — headphone tier (v3 Rollout 2b).

Input-only capture through the SHARED device manager: it honors the configured
input device and negotiates a sample rate the hardware actually supports (many
mics can't open native 16k), then resamples to 16k, scores VAD, and pushes
(pcm, is_speech) 512-sample 16k frames to the driver. Her TTS plays through the
normal output path, so use HEADPHONES to avoid her voice echoing into the mic
(the open-speaker case needs DTLN — the duplex source — which swaps THIS source
for a duplex one; the driver/engine are unchanged).

This object is the `acquire_audio` session for the fail-safe handoff: `start()`
opens the stream (raising on failure -> handoff restores wakeword), `close()`
stops cleanly.
"""
import logging
import threading

import numpy as np
from core.audio.backend import sd

from core.audio import get_device_manager, convert_to_mono, resample_audio

logger = logging.getLogger(__name__)

_RATE_16K = 16000
_FRAME_16K = 512      # silero wants 512-sample 16k frames


class LocalMicSource:
    def __init__(self, driver, gate, device=None):
        self.driver = driver
        self.gate = gate
        self._device = device       # explicit override (tests); else the device manager picks
        self._rate = _RATE_16K
        self._channels = 1
        self._blocksize = _FRAME_16K
        self._stream = None
        self._thread = None
        self._running = False

    def start(self):
        """Open the mic stream and start the capture->resample->VAD->driver loop.
        Raises on failure so the handoff can restore wakeword. Routes through the
        device manager so it uses the CONFIGURED device + a SUPPORTED rate — the
        old path hardcoded 16k on the default device, which failed on any mic that
        can't capture native 16k (and silently ignored the user's device pick)."""
        self.gate.reset()
        self.driver.reset()

        if self._device is None:
            cfg = get_device_manager().find_input_device(target_rate=_RATE_16K)
            if cfg is None:
                raise RuntimeError("no input device available for conversation mode")
            self._device = cfg.device_index
            self._rate = cfg.sample_rate
            self._channels = cfg.channels
            self._blocksize = cfg.blocksize or _FRAME_16K

        self._stream = sd.InputStream(
            samplerate=self._rate, channels=self._channels, dtype="int16",
            blocksize=self._blocksize, device=self._device,
        )
        self._stream.start()
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="conv-mic")
        self._thread.start()
        logger.info(f"[CONV] local mic source started (headphone tier): device={self._device}, "
                    f"rate={self._rate}Hz->16k, channels={self._channels}, block={self._blocksize}")

    def _loop(self):
        buf16 = np.zeros(0, dtype=np.int16)     # accumulates 16k samples -> 512-frame VAD blocks
        while self._running:
            try:
                data, _ = self._stream.read(self._blocksize)
            except Exception as e:
                logger.error(f"[CONV] mic read failed: {e}")
                break
            chunk = convert_to_mono(np.asarray(data))       # -> 1D int16 (downmix if stereo)
            if self._rate != _RATE_16K:
                chunk = resample_audio(chunk, self._rate, _RATE_16K)
            buf16 = np.concatenate((buf16, chunk))
            while len(buf16) >= _FRAME_16K:
                frame = buf16[:_FRAME_16K]
                buf16 = buf16[_FRAME_16K:]
                try:
                    is_sp = self.gate.is_speech(frame)
                    self.driver.push_frame(frame.tobytes(), is_sp)
                except Exception as e:
                    logger.error(f"[CONV] frame processing failed: {e}")

    def close(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as e:
                logger.debug(f"[CONV] mic stream close error: {e}")
            self._stream = None
        logger.info("[CONV] local mic source closed")
