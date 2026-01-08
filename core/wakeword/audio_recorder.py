# core/wakeword/audio_recorder.py - Audio recorder for wake word detection
"""
Audio recorder for wake word detection using sounddevice.
Uses the unified audio subsystem for device management.

OpenWakeWord expects 16kHz audio, so this module handles
resampling if the device doesn't support native 16kHz.
"""

import numpy as np
import sounddevice as sd
import array
import logging

from core.audio import (
    get_device_manager,
    classify_audio_error,
    convert_to_mono,
    resample_audio
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
        self.chunk_size = config.CHUNK_SIZE
        self.actual_blocksize = self.chunk_size
        self.channels = 1
        self.stream = None
        self.available = False
        self._resample_ratio = 1.0
        self._needs_stereo_downmix = False
        
        # Frame skipping parameters
        self.frame_skip = config.FRAME_SKIP
        self.frame_counter = 0
        self.previous_result = np.array([], dtype=np.int16)
        
        # Pre-allocate buffer
        self.buffer = array.array('h', [0] * int(config.BUFFER_DURATION * self.target_rate))
        self.buffer_index = 0
        
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
            
            self.device_index = device_config.device_index
            self.actual_rate = device_config.sample_rate
            self.channels = device_config.channels
            self.actual_blocksize = device_config.blocksize
            self._resample_ratio = device_config.resample_ratio
            self._needs_stereo_downmix = device_config.needs_stereo_downmix
            self.available = True
            
            if self.actual_rate == self.target_rate:
                logger.info(f"Wakeword: Device supports native {self.target_rate}Hz")
            else:
                logger.info(f"Wakeword: Device using {self.actual_rate}Hz "
                           f"(will resample to {self.target_rate}Hz)")
                
        except Exception as e:
            logger.error(f"Wakeword device init failed: {classify_audio_error(e)}")
            logger.error(dm.get_device_help())
            self.available = False

    def start_recording(self):
        """Open audio input stream."""
        if not self.available:
            logger.warning("Cannot start recording - no audio device available")
            return
        
        if self.stream is not None:
            logger.debug("Stream already open")
            return
        
        try:
            # Calculate actual chunk size based on device rate
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
        except Exception as e:
            logger.error(f"Failed to open wakeword audio stream: {classify_audio_error(e)}")
            self.stream = None
            # Don't raise - allow app to continue without wake word

    def stop_recording(self):
        """Close audio stream."""
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception as e:
                logger.debug(f"Error closing wakeword stream: {e}")
            self.stream = None
            logger.info("Wakeword audio stream closed")

    def get_stream(self):
        """Return the underlying stream (for compatibility)."""
        return self.stream

    def get_latest_chunk(self, duration):
        """
        Get latest audio chunk with frame skipping optimization.
        
        Returns audio resampled to 16kHz for OWW compatibility.
        Returns cached result on skipped frames for performance.
        """
        # Frame skipping logic
        self.frame_counter = (self.frame_counter + 1) % self.frame_skip
        if self.frame_counter != 0:
            return self.previous_result
        
        if self.stream is None:
            return self.previous_result
        
        # Calculate how many samples to read at actual device rate
        actual_samples = int(duration * self.actual_rate)
        
        try:
            data, overflowed = self.stream.read(actual_samples)
            if overflowed:
                logger.debug("Wakeword audio buffer overflow (non-fatal)")
            
            # Convert stereo to mono if needed
            if self._needs_stereo_downmix:
                audio = convert_to_mono(data)
            else:
                audio = data.flatten().astype(np.int16)
            
            # Resample to target rate if needed
            if self._resample_ratio != 1.0:
                audio = resample_audio(audio, self.actual_rate, self.target_rate)
            
            self.previous_result = audio
            
        except Exception as e:
            logger.warning(f"Error reading wakeword audio chunk: {classify_audio_error(e)}")
            # Return previous result to avoid breaking detection loop
        
        return self.previous_result