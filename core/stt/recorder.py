import sounddevice as sd
import soundfile as sf
import numpy as np
from typing import Optional, Tuple
import tempfile
import os
import time
from collections import deque
from . import system_audio
import logging
import config

logger = logging.getLogger(__name__)


def get_temp_dir():
    """Get optimal temp directory. Prefers /dev/shm (Linux RAM disk) for speed."""
    shm = '/dev/shm'
    if os.path.exists(shm) and os.access(shm, os.W_OK):
        return shm
    return tempfile.gettempdir()


class AudioRecorder:
    """
    Audio recorder with adaptive VAD for speech-to-text.
    Uses sounddevice for cross-platform microphone access.
    """
    
    def __init__(self):
        self.level_history = deque(maxlen=config.RECORDER_LEVEL_HISTORY_SIZE)
        self.adaptive_threshold = config.RECORDER_SILENCE_THRESHOLD
        self._stream = None
        self._recording = False
        self.device_index = None
        self.rate = None
        self.temp_dir = get_temp_dir()
        
        # Find input device
        self.device_index, self.rate = self._find_input_device()
        if self.device_index is None:
            # Retry once
            logger.warning("No device found, retrying...")
            time.sleep(0.5)
            self.device_index, self.rate = self._find_input_device()
            if self.device_index is None:
                raise RuntimeError("No suitable input device found after retry")
        
        logger.info(f"Selected device {self.device_index} with sample rate {self.rate}")
        logger.info(f"Temp directory: {self.temp_dir}")

    def _find_input_device(self) -> Tuple[Optional[int], int]:
        """Find preferred input device and compatible sample rate."""
        logger.info("Searching for input devices...")
        
        try:
            devices = sd.query_devices()
        except Exception as e:
            logger.error(f"Error querying devices: {e}")
            return None, 44100
        
        # Build list of input devices
        input_devices = []
        for i, dev in enumerate(devices):
            if dev['max_input_channels'] > 0:
                logger.info(f"Found device {i}: {dev['name']}")
                input_devices.append((i, dev))
        
        if not input_devices:
            logger.error("No input devices found")
            return None, 44100
        
        # Find first device matching preferred names
        for preferred in config.RECORDER_PREFERRED_DEVICES:
            for idx, dev_info in input_devices:
                if preferred in dev_info['name'].lower():
                    logger.info(f"Selected preferred device: {dev_info['name']}")
                    default_rate = int(dev_info['default_samplerate'])
                    
                    # Try default rate first, then fallback rates
                    if self._test_device(idx, default_rate):
                        return idx, default_rate
                    
                    for rate in config.RECORDER_SAMPLE_RATES:
                        if self._test_device(idx, rate):
                            return idx, rate
        
        # If no preferred device, try any available device
        idx, dev_info = input_devices[0]
        logger.info(f"Using default device: {dev_info['name']}")
        default_rate = int(dev_info['default_samplerate'])
        
        if self._test_device(idx, default_rate):
            return idx, default_rate
        
        for rate in config.RECORDER_SAMPLE_RATES:
            if self._test_device(idx, rate):
                return idx, rate
        
        return None, 44100
    
    def _test_device(self, device_index: int, sample_rate: int) -> bool:
        """Test if device works with given sample rate."""
        try:
            # Try to open stream briefly
            stream = sd.InputStream(
                device=device_index,
                samplerate=sample_rate,
                channels=config.RECORDER_CHANNELS,
                dtype=np.int16,
                blocksize=config.RECORDER_CHUNK_SIZE
            )
            stream.close()
            return True
        except Exception as e:
            logger.debug(f"Device test failed for device {device_index} at {sample_rate}Hz: {e}")
            return False

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
                channels=config.RECORDER_CHANNELS,
                dtype=np.int16,
                blocksize=config.RECORDER_CHUNK_SIZE
            )
            self._stream.start()
            return True
        except Exception as e:
            logger.error(f"Error opening audio stream: {e}")
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
                data, overflowed = self._stream.read(config.RECORDER_CHUNK_SIZE)
                if overflowed:
                    logger.debug("Audio buffer overflow (continuing)")
                
                # Flatten to 1D int16 array
                audio_data = data.flatten().astype(np.int16)
                
                is_silent = self._is_silent(audio_data)
                
                if is_silent:
                    silent_chunks += 1
                    speech_chunks = max(0, speech_chunks - 1)
                    if (silent_chunks > (self.rate / config.RECORDER_CHUNK_SIZE *
                                        config.RECORDER_SILENCE_DURATION) and has_speech):
                        break
                else:
                    speech_chunks += 1
                    silent_chunks = max(0, silent_chunks - 1)
                    if speech_chunks > (self.rate / config.RECORDER_CHUNK_SIZE *
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
                logger.error(f"Recording error: {e}")
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
            
            # Write WAV file using soundfile
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