# core/wakeword/audio_recorder.py - Audio recorder for wake word detection
"""
Audio recorder for wake word detection using sounddevice.
Uses the unified audio subsystem for device management.

OpenWakeWord expects 16kHz audio, so this module handles
resampling if the device doesn't support native 16kHz.
"""

import numpy as np
import sounddevice as sd
import logging
import threading

from core.audio import (
    get_device_manager,
    classify_audio_error
)
import config

logger = logging.getLogger(__name__)

# Target rate for OpenWakeWord (expects 16kHz)
OWW_SAMPLE_RATE = 16000


class AudioRecorder:
    """
    Audio recorder for wake word detection using sounddevice.
    
    Handles device capability detection via DeviceManager,
    and resampling to 16kHz for OpenWakeWord compatibility.
    """
    
    def __init__(self):
        self.target_rate = OWW_SAMPLE_RATE
        self.actual_rate = None
        self.device_index = None
        self.device_name = ''
        self.chunk_size = config.CHUNK_SIZE
        self.actual_blocksize = self.chunk_size
        self.channels = 1
        self.stream = None
        self.available = False
        self._resample_ratio = 1.0
        self._needs_stereo_downmix = False
        # Guards open/close/pause/resume — see start_recording's comment.
        self._stream_lock = threading.Lock()
        
        # Find working device via DeviceManager
        self._init_device()
        
        if self.available:
            logger.info(f"Wakeword AudioRecorder ready: device={self.device_index}, "
                       f"actual_rate={self.actual_rate}Hz, target_rate={self.target_rate}Hz, "
                       f"channels={self.channels}, blocksize={self.actual_blocksize}, "
                       f"resample={self._resample_ratio != 1.0}, "
                       f"stereo_downmix={self._needs_stereo_downmix}")
        else:
            logger.warning("Wakeword AudioRecorder unavailable - wake word detection disabled")

    def _init_device(self):
        """Find a working input device using DeviceManager."""
        dm = get_device_manager()

        try:
            device_config = dm.find_input_device(
                target_rate=self.target_rate,  # Prefer 16kHz for wakeword
                preferred_blocksize=self.chunk_size
            )

            self._apply_device_config(device_config)
            self.available = True

            if self.actual_rate == self.target_rate:
                logger.info(f"Wakeword: Device supports native {self.target_rate}Hz")
            else:
                logger.info(f"Wakeword: Device using {self.actual_rate}Hz "
                           f"(wake listen loop resamples to {self.target_rate}Hz)")

        except Exception as e:
            logger.error(f"Wakeword device init failed: {classify_audio_error(e)}")
            logger.error(dm.get_device_help())
            self.available = False

    def _apply_device_config(self, device_config):
        """Apply a DeviceConfig to this recorder's state."""
        self.device_index = device_config.device_index
        self.device_name = device_config.device_name
        self.actual_rate = device_config.sample_rate
        self.channels = device_config.channels
        self.actual_blocksize = device_config.blocksize
        self._resample_ratio = device_config.resample_ratio
        self._needs_stereo_downmix = device_config.needs_stereo_downmix

    def start_recording(self):
        """Open audio input stream. Retries once with device re-resolution on failure."""
        if not self.available:
            logger.warning("Cannot start recording - no audio device available")
            return

        # _stream_lock: two unlocked concurrent opens both passed the None
        # check, both opened an InputStream, and the attribute kept only the
        # second — the first held the mic unreferenced until process exit
        # (every later STT recording then failed mic_busy). Same lock covers
        # stop/pause/resume so a double Pa_CloseStream (check-to-call gap =
        # double free → unexplained SIGABRT) can't happen. Hunt 2026-08-17.
        with self._stream_lock:
            if self.stream is not None:
                logger.debug("Stream already open")
                return

            try:
                self._open_audio_stream()
            except Exception as e:
                logger.warning(f"Wakeword stream open failed: {classify_audio_error(e)}")
                # Retry once — re-resolve device by name in case index shifted
                if getattr(self, 'device_name', ''):
                    logger.info(f"Retrying with device re-resolution for '{self.device_name}'")
                    dm = get_device_manager()
                    new_config = dm.reopen_device(
                        self.device_name,
                        target_rate=self.target_rate,
                        preferred_blocksize=self.chunk_size
                    )
                    if new_config:
                        self._apply_device_config(new_config)
                        try:
                            self._open_audio_stream()
                            return
                        except Exception as e2:
                            logger.error(f"Wakeword retry also failed: {classify_audio_error(e2)}")
                self.stream = None

    def _open_audio_stream(self):
        """Open the underlying sd.InputStream. Raises on failure."""
        actual_chunk = int(self.actual_blocksize * self._resample_ratio)
        self.stream = sd.InputStream(
            device=self.device_index,
            samplerate=self.actual_rate,
            channels=self.channels,
            dtype=np.int16,
            blocksize=actual_chunk
        )
        self.stream.start()
        logger.info(f"Wakeword audio stream opened: device={self.device_index}, "
                   f"rate={self.actual_rate}, channels={self.channels}, chunk={actual_chunk}")

    def stop_recording(self):
        """Close audio stream."""
        with self._stream_lock:
            if self.stream:
                try:
                    self.stream.stop()
                    self.stream.close()
                except Exception as e:
                    logger.debug(f"Error closing wakeword stream: {e}")
                self.stream = None
                logger.info("Wakeword audio stream closed")

    def pause_recording(self):
        """Pause audio stream without closing (safer for PipeWire)."""
        with self._stream_lock:
            if self.stream:
                try:
                    self.stream.stop()
                    logger.debug("Wakeword audio stream paused")
                    return True
                except Exception as e:
                    logger.debug(f"Error pausing wakeword stream: {e}")
            return False

    def resume_recording(self):
        """Resume paused audio stream."""
        with self._stream_lock:
            if self.stream:
                try:
                    self.stream.start()
                    logger.debug("Wakeword audio stream resumed")
                    return True
                except Exception as e:
                    logger.debug(f"Error resuming wakeword stream: {e}")
            return False

    def get_stream(self):
        """Return the underlying stream (for compatibility)."""
        return self.stream

    # get_latest_chunk removed (F4 2026-08-17): it was the CORRECT
    # downmix+resample read path, but it had zero callers — the listen loop
    # read the raw stream directly. That conversion now lives in
    # wake_detector._listen_loop, the one place that reads this stream.