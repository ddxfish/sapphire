import pyaudio
import wave
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
    def __init__(self):
        self.format = pyaudio.paInt16
        self.audio = None
        self.level_history = deque(maxlen=config.RECORDER_LEVEL_HISTORY_SIZE)
        self.adaptive_threshold = config.RECORDER_SILENCE_THRESHOLD
        self._stream = None
        self._recording = False
        self.device_index = None
        self.rate = None
        self.temp_dir = get_temp_dir()
        
        # Initialize PyAudio with robust error handling
        self._init_pyaudio()
        
        # Find input device (only if PyAudio was initialized successfully)
        if self.audio:
            self.device_index, self.rate = self._find_input_device()
            if self.device_index is None:
                logger.error("No suitable input device found")
                self._cleanup_pyaudio()
                self._init_pyaudio()  # Try to reinitialize
                self.device_index, self.rate = self._find_input_device()
                if self.device_index is None:
                    raise RuntimeError("No suitable input device found after retry")
            
            logger.info(f"Selected device {self.device_index} with sample rate {self.rate}")
            logger.info(f"Temp directory: {self.temp_dir}")

    def _init_pyaudio(self):
        """Initialize PyAudio with error handling."""
        try:
            if self.audio is not None:
                try:
                    self.audio.terminate()
                except:
                    pass
            self.audio = pyaudio.PyAudio()
        except Exception as e:
            logger.error(f"Failed to initialize PyAudio: {e}")
            self.audio = None

    def _cleanup_pyaudio(self):
        """Clean up PyAudio resources."""
        if self._stream:
            try:
                if self._stream.is_active():
                    self._stream.stop_stream()
                self._stream.close()
            except Exception as e:
                logger.debug(f"Error closing stream: {e}")
            self._stream = None
            
        if self.audio:
            try:
                self.audio.terminate()
            except Exception as e:
                logger.debug(f"Error terminating PyAudio: {e}")
            self.audio = None

    def _find_input_device(self) -> Tuple[Optional[int], int]:
        """Find preferred input device and compatible sample rate with robust error handling."""
        if not self.audio:
            return None, 44100
            
        logger.info("Searching for input devices...")
        
        # Get all input devices
        input_devices = []
        try:
            device_count = self.audio.get_device_count()
            for i in range(device_count):
                try:
                    dev_info = self.audio.get_device_info_by_index(i)
                    if dev_info['maxInputChannels'] > 0:
                        logger.info(f"Found device {i}: {dev_info['name']}")
                        input_devices.append((i, dev_info))
                except Exception as e:
                    logger.debug(f"Error querying device {i}: {e}")
        except Exception as e:
            logger.error(f"Error enumerating devices: {e}")
            
        # Find first device matching preferred names
        for preferred in config.RECORDER_PREFERRED_DEVICES:
            for idx, dev_info in input_devices:
                if preferred in dev_info['name'].lower():
                    logger.info(f"Selected preferred device: {dev_info['name']}")
                    default_rate = int(dev_info['defaultSampleRate'])
                    
                    # Try default rate first, then fallback rates
                    if self._test_device(idx, default_rate):
                        return idx, default_rate
                    
                    for rate in config.RECORDER_SAMPLE_RATES:
                        if self._test_device(idx, rate):
                            return idx, rate
        
        # If no preferred device, try any available device
        if input_devices:
            idx, dev_info = input_devices[0]
            logger.info(f"Using default device: {dev_info['name']}")
            default_rate = int(dev_info['defaultSampleRate'])
            
            if self._test_device(idx, default_rate):
                return idx, default_rate
                
            for rate in config.RECORDER_SAMPLE_RATES:
                if self._test_device(idx, rate):
                    return idx, rate
                    
        return None, 44100
        
    def _test_device(self, device_index: int, sample_rate: int) -> bool:
        """Test if device works with given sample rate with robust error handling."""
        if not self.audio:
            return False
            
        try:
            stream = self.audio.open(
                format=self.format,
                channels=config.RECORDER_CHANNELS,
                rate=sample_rate,
                input=True,
                input_device_index=device_index,
                frames_per_buffer=config.RECORDER_CHUNK_SIZE,
                start=False
            )
            stream.close()
            return True
        except Exception as e:
            logger.debug(f"Device test failed: {e}")
            return False

    def _update_threshold(self, level: float) -> None:
        """Update adaptive silence threshold."""
        self.level_history.append(level)
        background = np.percentile(list(self.level_history), config.RECORDER_BACKGROUND_PERCENTILE)
        self.adaptive_threshold = max(
            config.RECORDER_SILENCE_THRESHOLD, 
            background * config.RECORDER_NOISE_MULTIPLIER
        )

    def _is_silent(self, audio_data: np.ndarray) -> bool:
        """Check if audio chunk is silent."""
        level = np.max(np.abs(audio_data.astype(np.float32) / 32768.0))
        self._update_threshold(level)
        print(f"Level: {level:.4f} | Threshold: {self.adaptive_threshold:.4f}", end='\r')
        return level < self.adaptive_threshold

    def _open_stream(self) -> bool:
        """Open the audio stream with error handling."""
        if not self.audio:
            self._init_pyaudio()
            if not self.audio:
                return False
                
        if self._stream:
            try:
                if self._stream.is_active():
                    self._stream.stop_stream()
                self._stream.close()
            except:
                pass
            self._stream = None
        
        try:
            self._stream = self.audio.open(
                format=self.format,
                channels=config.RECORDER_CHANNELS,
                rate=self.rate,
                input=True,
                input_device_index=self.device_index,
                frames_per_buffer=config.RECORDER_CHUNK_SIZE
            )
            return True
        except Exception as e:
            logger.error(f"Error opening audio stream: {e}")
            return False

    def record_audio(self) -> Optional[str]:
        """Record audio until silence is detected with improved reliability."""
        logger.debug(f"Recording state before: {self._recording}")
        
        # Clean up previous recording if needed
        if self._recording:
            self.stop()
            self._recording = False
            # Brief sleep to let audio system stabilize
            time.sleep(0.1)
        
        # Lower system volume - non-blocking
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
        
        # Main recording loop with robust error handling
        while self._recording:
            try:
                data = self._stream.read(config.RECORDER_CHUNK_SIZE, exception_on_overflow=False)
                audio_data = np.frombuffer(data, dtype=np.int16)
                
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
                
                frames.append(data)
                
                if time.time() - start_time > config.RECORDER_MAX_SECONDS:
                    if has_speech:
                        break
                    return None
                    
            except IOError as e:
                # Handle ALSA errors like underruns specifically
                logger.warning(f"Audio read error (continuing): {e}")
                time.sleep(0.01)  # Add a small pause
                continue
                
            except Exception as e:
                logger.error(f"Recording error: {e}")
                break

        # Restore system volume - non-blocking
        system_audio.restore_system_volume()
        
        # Close stream and reset state
        self.stop()

        if not has_speech:
            return None
            
        try:
            # Use cross-platform temp directory
            timestamp = int(time.time())
            temp_path = os.path.join(self.temp_dir, f"voice_assistant_{timestamp}.wav")
            
            with wave.open(temp_path, 'wb') as wf:
                wf.setnchannels(config.RECORDER_CHANNELS)
                wf.setsampwidth(self.audio.get_sample_size(self.format))
                wf.setframerate(self.rate)
                wf.writeframes(b''.join(frames))
            return temp_path
            
        except Exception as e:
            logger.error(f"Error saving audio: {e}")
            return None

    def stop(self) -> None:
        """Stop recording and clean up audio resources with error handling."""
        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception as e:
                logger.debug(f"Error stopping stream: {e}")
            self._stream = None
        self._recording = False

    def __del__(self):
        """Clean up all resources when object is destroyed."""
        self.stop()
        self._cleanup_pyaudio()