# core/stt/recorder.py - Audio recorder for speech-to-text
"""
Audio recorder with adaptive VAD for speech-to-text.
Uses the unified audio subsystem for device management.
"""

import sounddevice as sd
import soundfile as sf
import numpy as np
from typing import Optional
import os
import time
from collections import deque
import logging

from core.audio import (
    get_device_manager, 
    classify_audio_error, 
    convert_to_mono,
    get_temp_dir
)
from . import system_audio
import config

logger = logging.getLogger(__name__)


class AudioRecorder:
    """
    Audio recorder with adaptive VAD for speech-to-text.
    Uses sounddevice for cross-platform microphone access.
    Device selection and fallback handled by DeviceManager.
    """
    
    def __init__(self):
        self.level_history = deque(maxlen=config.RECORDER_LEVEL_HISTORY_SIZE)
        self.adaptive_threshold = config.RECORDER_SILENCE_THRESHOLD
        self._stream = None
        self._recording = False
        self.temp_dir = get_temp_dir()
        
        # Get device configuration from unified manager
        dm = get_device_manager()
        preferred_blocksize = config.RECORDER_CHUNK_SIZE
        
        try:
            device_config = dm.find_input_device(
                target_rate=None,  # STT can handle any rate
                preferred_blocksize=preferred_blocksize
            )
            
            self.device_index = device_config.device_index
            self.rate = device_config.sample_rate
            self.channels = device_config.channels
            self.blocksize = device_config.blocksize
            self._needs_stereo_downmix = device_config.needs_stereo_downmix
            
        except Exception as e:
            # Retry once
            logger.warning(f"First device search failed: {e}, retrying...")
            time.sleep(0.5)
            try:
                device_config = dm.find_input_device(
                    target_rate=None,
                    preferred_blocksize=preferred_blocksize
                )
                self.device_index = device_config.device_index
                self.rate = device_config.sample_rate
                self.channels = device_config.channels
                self.blocksize = device_config.blocksize
                self._needs_stereo_downmix = device_config.needs_stereo_downmix
            except Exception as e2:
                raise RuntimeError(
                    f"No suitable input device found after retry. {e2}\n" +
                    dm.get_device_help()
                )
        
        logger.info(f"STT Recorder: device={self.device_index}, rate={self.rate}Hz, "
                   f"channels={self.channels}, blocksize={self.blocksize}, "
                   f"stereo_downmix={self._needs_stereo_downmix}")
        logger.info(f"Temp directory: {self.temp_dir}")

    def _update_threshold(self, level: float) -> None:
        """Update adaptive silence threshold based on background noise."""
        self.level_history.append(level)
        background = np.percentile(list(self.level_history), config.RECORDER_BACKGROUND_PERCENTILE)
        self.adaptive_threshold = max(
            config.RECORDER_SILENCE_THRESHOLD,
            background * config.RECORDER_NOISE_MULTIPLIER
        )

    def _is_silent(self, audio_data: np.ndarray) -> bool:
        """Check if audio chunk is silent using adaptive threshold."""
        level = np.max(np.abs(audio_data.astype(np.float32) / 32768.0))
        self._update_threshold(level)
        print(f"Level: {level:.4f} | Threshold: {self.adaptive_threshold:.4f}", end='\r')
        return level < self.adaptive_threshold

    def _open_stream(self) -> bool:
        """Open the audio stream."""
        # Close existing stream if any
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except:
                pass
            self._stream = None
        
        try:
            self._stream = sd.InputStream(
                device=self.device_index,
                samplerate=self.rate,
                channels=self.channels,
                dtype=np.int16,
                blocksize=self.blocksize
            )
            self._stream.start()
            return True
        except Exception as e:
            logger.error(f"Error opening audio stream: {classify_audio_error(e)}")
            return False

    def record_audio(self) -> Optional[str]:
        """
        Record audio until silence is detected.
        Returns path to WAV file, or None if no speech detected.
        """
        logger.debug(f"Recording state before: {self._recording}")
        
        # Clean up previous recording if needed
        if self._recording:
            self.stop()
            self._recording = False
            time.sleep(0.1)
        
        # Lower system volume during recording
        system_audio.lower_system_volume()
        
        # Try to open the audio stream
        if not self._open_stream():
            system_audio.restore_system_volume()
            return None
        
        self._recording = True
        
        frames = []
        silent_chunks = speech_chunks = 0
        has_speech = False
        start_time = time.time()
        
        # Wait for beep to finish
        time.sleep(config.RECORDER_BEEP_WAIT_TIME)
        
        print("\nListening...")
        
        # Main recording loop
        while self._recording:
            try:
                # sounddevice read returns (data, overflowed)
                data, overflowed = self._stream.read(self.blocksize)
                if overflowed:
                    logger.debug("Audio buffer overflow (continuing)")
                
                # Convert stereo to mono if needed
                if self._needs_stereo_downmix:
                    audio_data = convert_to_mono(data)
                else:
                    audio_data = data.flatten().astype(np.int16)
                
                is_silent = self._is_silent(audio_data)
                
                if is_silent:
                    silent_chunks += 1
                    speech_chunks = max(0, speech_chunks - 1)
                    if (silent_chunks > (self.rate / self.blocksize *
                                        config.RECORDER_SILENCE_DURATION) and has_speech):
                        break
                else:
                    speech_chunks += 1
                    silent_chunks = max(0, silent_chunks - 1)
                    if speech_chunks > (self.rate / self.blocksize *
                                       config.RECORDER_SPEECH_DURATION):
                        has_speech = True
                
                frames.append(audio_data)
                
                if time.time() - start_time > config.RECORDER_MAX_SECONDS:
                    if has_speech:
                        break
                    return None
                
            except sd.PortAudioError as e:
                # Handle audio system errors (like ALSA underruns)
                logger.warning(f"Audio read error (continuing): {e}")
                time.sleep(0.01)
                continue
                
            except Exception as e:
                logger.error(f"Recording error: {classify_audio_error(e)}")
                break
        
        # Restore system volume
        system_audio.restore_system_volume()
        
        # Close stream and reset state
        self.stop()
        
        if not has_speech:
            return None
        
        try:
            # Combine all frames into single array
            audio_data = np.concatenate(frames)
            
            # Write WAV file using soundfile (always mono output)
            timestamp = int(time.time())
            temp_path = os.path.join(self.temp_dir, f"voice_assistant_{timestamp}.wav")
            sf.write(temp_path, audio_data, self.rate)
            
            return temp_path
            
        except Exception as e:
            logger.error(f"Error saving audio: {e}")
            return None

    def stop(self) -> None:
        """Stop recording and clean up audio resources."""
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as e:
                logger.debug(f"Error stopping stream: {e}")
            self._stream = None
        self._recording = False

    def _init_pyaudio(self):
        """No-op for compatibility with stt_null.py interface."""
        pass

    def _cleanup_pyaudio(self):
        """No-op for compatibility with stt_null.py interface."""
        pass

    def __del__(self):
        """Clean up resources when object is destroyed."""
        self.stop()