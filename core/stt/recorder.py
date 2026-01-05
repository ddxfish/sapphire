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


def classify_audio_error(e: Exception) -> str:
    """Classify audio exception and return actionable user message."""
    err_str = str(e).lower()
    err_type = type(e).__name__
    
    # Permission denied
    if any(x in err_str for x in ['permission denied', 'eperm', 'eacces', 'access denied']):
        return (
            "Microphone access denied. "
            "Linux: run 'sudo usermod -aG audio $USER' then logout/login. "
            "Windows: check Settings > Privacy > Microphone."
        )
    
    # Device busy
    if any(x in err_str for x in ['device or resource busy', 'ebusy', 'already in use', 'exclusive']):
        return (
            "Microphone in use by another application. "
            "Close Discord, Zoom, Teams, or other audio apps and retry."
        )
    
    # Invalid sample rate (PortAudio error -9997)
    if any(x in err_str for x in ['invalid sample rate', '-9997', 'sample rate', 'samplerate']):
        return (
            "Device rejected all sample rates. "
            "Try adding your device's native rate to RECORDER_SAMPLE_RATES in user/settings.json"
        )
    
    # Invalid channel count
    if any(x in err_str for x in ['invalid number of channels', 'channel', '-9998']):
        return (
            "Device rejected channel configuration. "
            "This is unusual - check if device supports audio input."
        )
    
    # PortAudio not initialized / not found
    if any(x in err_str for x in ['portaudio', 'not initialized', 'pa_', 'libportaudio']):
        return (
            "Audio system not ready. "
            "Linux: run 'sudo apt install libportaudio2 portaudio19-dev'. "
            "Windows: reinstall Python audio packages."
        )
    
    # Device not found
    if any(x in err_str for x in ['no such device', 'device not found', 'invalid device']):
        return (
            "Audio device not found. "
            "Check USB connection or select a different device in settings."
        )
    
    # Generic with original error
    return f"Audio error: {err_type}: {e}"


class AudioRecorder:
    """
    Audio recorder with adaptive VAD for speech-to-text.
    Uses sounddevice for cross-platform microphone access.
    Includes fallback logic for sample rates, channels, and blocksizes.
    """
    
    def __init__(self):
        self.level_history = deque(maxlen=config.RECORDER_LEVEL_HISTORY_SIZE)
        self.adaptive_threshold = config.RECORDER_SILENCE_THRESHOLD
        self._stream = None
        self._recording = False
        self.device_index = None
        self.rate = None
        self.channels = config.RECORDER_CHANNELS  # Will be updated if stereo fallback needed
        self.blocksize = config.RECORDER_CHUNK_SIZE  # Will be updated if fallback needed
        self.temp_dir = get_temp_dir()
        self._needs_stereo_downmix = False  # Flag for stereo->mono conversion
        
        # Find input device with fallbacks
        self.device_index, self.rate, self.channels, self.blocksize = self._find_input_device()
        if self.device_index is None:
            # Retry once
            logger.warning("No device found, retrying...")
            time.sleep(0.5)
            self.device_index, self.rate, self.channels, self.blocksize = self._find_input_device()
            if self.device_index is None:
                raise RuntimeError("No suitable input device found after retry. " + 
                                   self._get_device_help())
        
        self._needs_stereo_downmix = (self.channels == 2)
        
        logger.info(f"Selected device {self.device_index}: rate={self.rate}Hz, "
                   f"channels={self.channels}, blocksize={self.blocksize}, "
                   f"stereo_downmix={self._needs_stereo_downmix}")
        logger.info(f"Temp directory: {self.temp_dir}")

    def _get_device_help(self) -> str:
        """Generate helpful message about available devices."""
        try:
            devices = sd.query_devices()
            input_devs = [f"  [{i}] {d['name']}" for i, d in enumerate(devices) 
                         if d['max_input_channels'] > 0]
            if input_devs:
                return "Available input devices:\n" + "\n".join(input_devs)
            return "No input devices detected. Check audio drivers."
        except Exception:
            return "Could not query audio devices."

    def _find_input_device(self) -> Tuple[Optional[int], int, int, int]:
        """Find preferred input device and compatible settings.
        
        Returns: (device_index, sample_rate, channels, blocksize) or (None, ...) on failure
        """
        logger.info("Searching for input devices...")
        
        try:
            devices = sd.query_devices()
        except Exception as e:
            logger.error(f"Error querying devices: {classify_audio_error(e)}")
            return None, 44100, 1, 1024
        
        # Build list of input devices
        input_devices = []
        for i, dev in enumerate(devices):
            if dev['max_input_channels'] > 0:
                logger.info(f"Found device {i}: {dev['name']} "
                          f"(max_channels={dev['max_input_channels']}, "
                          f"default_rate={dev['default_samplerate']})")
                input_devices.append((i, dev))
        
        if not input_devices:
            logger.error("No input devices found. Check microphone connection.")
            return None, 44100, 1, 1024
        
        # Find first device matching preferred names
        for preferred in config.RECORDER_PREFERRED_DEVICES:
            for idx, dev_info in input_devices:
                if preferred in dev_info['name'].lower():
                    logger.info(f"Trying preferred device: {dev_info['name']}")
                    result = self._try_device_with_fallbacks(idx, dev_info)
                    if result[0] is not None:
                        return result
        
        # If no preferred device, try any available device
        for idx, dev_info in input_devices:
            logger.info(f"Trying device: {dev_info['name']}")
            result = self._try_device_with_fallbacks(idx, dev_info)
            if result[0] is not None:
                return result
        
        logger.error("All devices failed configuration")
        return None, 44100, 1, 1024
    
    def _try_device_with_fallbacks(self, device_index: int, dev_info: dict) -> Tuple[Optional[int], int, int, int]:
        """Try device with all fallback combinations.
        
        Order of attempts:
        1. Default rate + mono + default blocksize
        2. Default rate + mono + fallback blocksizes
        3. Default rate + stereo + all blocksizes
        4. Fallback rates + mono + all blocksizes
        5. Fallback rates + stereo + all blocksizes
        
        Returns: (device_index, rate, channels, blocksize) or (None, ...) on failure
        """
        default_rate = int(dev_info['default_samplerate'])
        max_channels = dev_info['max_input_channels']
        
        # Get fallback lists from config
        sample_rates = [default_rate] + [r for r in config.RECORDER_SAMPLE_RATES if r != default_rate]
        blocksizes = getattr(config, 'RECORDER_BLOCKSIZE_FALLBACKS', [1024, 512, 2048, 4096])
        
        # Channels to try: prefer mono, fall back to stereo if device supports it
        channel_options = [1]
        if max_channels >= 2:
            channel_options.append(2)
        
        # Try all combinations
        for rate in sample_rates:
            for channels in channel_options:
                for blocksize in blocksizes:
                    if self._test_device(device_index, rate, channels, blocksize):
                        return (device_index, rate, channels, blocksize)
        
        return (None, 44100, 1, 1024)
    
    def _test_device(self, device_index: int, sample_rate: int, channels: int, blocksize: int) -> bool:
        """Test if device works with given parameters."""
        try:
            stream = sd.InputStream(
                device=device_index,
                samplerate=sample_rate,
                channels=channels,
                dtype=np.int16,
                blocksize=blocksize
            )
            stream.close()
            logger.debug(f"  OK: rate={sample_rate}, ch={channels}, block={blocksize}")
            return True
        except Exception as e:
            logger.debug(f"  FAIL: rate={sample_rate}, ch={channels}, block={blocksize}: {e}")
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
                channels=self.channels,
                dtype=np.int16,
                blocksize=self.blocksize
            )
            self._stream.start()
            return True
        except Exception as e:
            logger.error(f"Error opening audio stream: {classify_audio_error(e)}")
            return False

    def _convert_to_mono(self, audio_data: np.ndarray) -> np.ndarray:
        """Convert stereo audio to mono by averaging channels."""
        if len(audio_data.shape) > 1 and audio_data.shape[1] == 2:
            # Average left and right channels
            return (audio_data[:, 0].astype(np.int32) + audio_data[:, 1].astype(np.int32)) // 2
        return audio_data.flatten()

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
                    audio_data = self._convert_to_mono(data).astype(np.int16)
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