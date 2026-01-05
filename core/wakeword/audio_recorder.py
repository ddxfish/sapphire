import numpy as np
import sounddevice as sd
import array
import logging
import config

logger = logging.getLogger(__name__)

# Target rate for OpenWakeWord (expects 16kHz)
OWW_SAMPLE_RATE = 16000


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
    """Audio recorder for wake word detection using sounddevice.
    
    Handles device capability detection, sample rate negotiation,
    channel fallback, blocksize fallback, and resampling to 16kHz
    for OpenWakeWord compatibility.
    """
    
    def __init__(self):
        self.target_rate = OWW_SAMPLE_RATE
        self.actual_rate = None
        self.device_index = None
        self.chunk_size = config.CHUNK_SIZE
        self.actual_blocksize = self.chunk_size  # May differ after fallback
        self.channels = 1  # Will be updated if stereo fallback needed
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
        
        # Find working device and rate
        self._init_device()
        
        if self.available:
            logger.info(f"AudioRecorder ready: device={self.device_index}, "
                       f"actual_rate={self.actual_rate}Hz, target_rate={self.target_rate}Hz, "
                       f"channels={self.channels}, blocksize={self.actual_blocksize}, "
                       f"resample={self._resample_ratio != 1.0}, "
                       f"stereo_downmix={self._needs_stereo_downmix}")
        else:
            logger.warning("AudioRecorder unavailable - wake word detection disabled")

    def _init_device(self):
        """Find a working input device and compatible settings."""
        try:
            devices = sd.query_devices()
        except Exception as e:
            logger.error(f"Failed to query audio devices: {classify_audio_error(e)}")
            return
        
        # Build list of input devices
        input_devices = []
        for i, dev in enumerate(devices):
            if dev['max_input_channels'] > 0:
                logger.debug(f"Found input device {i}: {dev['name']} "
                           f"(max_channels={dev['max_input_channels']}, "
                           f"default_rate={dev['default_samplerate']})")
                input_devices.append((i, dev))
        
        if not input_devices:
            logger.error("No input devices found. Check microphone connection.")
            return
        
        # Try preferred devices first
        preferred = getattr(config, 'RECORDER_PREFERRED_DEVICES', ['default'])
        
        for pref in preferred:
            for idx, dev_info in input_devices:
                if pref.lower() in dev_info['name'].lower():
                    if self._try_device_with_fallbacks(idx, dev_info):
                        return
        
        # Fall back to any available device
        for idx, dev_info in input_devices:
            if self._try_device_with_fallbacks(idx, dev_info):
                return
        
        logger.error("No compatible audio device found. " + self._get_device_help())

    def _get_device_help(self) -> str:
        """Generate helpful message about available devices."""
        try:
            devices = sd.query_devices()
            input_devs = [f"  [{i}] {d['name']}" for i, d in enumerate(devices) 
                         if d['max_input_channels'] > 0]
            if input_devs:
                return "Available input devices:\n" + "\n".join(input_devs)
            return "No input devices detected."
        except Exception:
            return "Could not query audio devices."

    def _try_device_with_fallbacks(self, device_index: int, dev_info: dict) -> bool:
        """Try device with all fallback combinations.
        
        Priority order:
        1. Native 16kHz (no resampling needed for OWW)
        2. Device default rate
        3. Fallback rates from config
        
        For each rate, tries:
        1. Mono + configured blocksize
        2. Mono + fallback blocksizes  
        3. Stereo + all blocksizes (with downmix)
        
        Returns True if device is usable.
        """
        device_name = dev_info['name']
        default_rate = int(dev_info['default_samplerate'])
        max_channels = dev_info['max_input_channels']
        
        # Build rate list: prefer 16kHz (native OWW), then default, then fallbacks
        fallback_rates = getattr(config, 'RECORDER_SAMPLE_RATES', [44100, 48000])
        sample_rates = [self.target_rate, default_rate] + [r for r in fallback_rates 
                                                            if r not in (self.target_rate, default_rate)]
        
        # Get blocksize fallbacks
        blocksizes = getattr(config, 'RECORDER_BLOCKSIZE_FALLBACKS', [1024, 512, 2048, 4096])
        # Ensure configured chunk size is tried first
        if self.chunk_size not in blocksizes:
            blocksizes = [self.chunk_size] + blocksizes
        
        # Channel options: prefer mono, fall back to stereo if available
        channel_options = [1]
        if max_channels >= 2:
            channel_options.append(2)
        
        for rate in sample_rates:
            for channels in channel_options:
                for blocksize in blocksizes:
                    if self._test_config(device_index, rate, channels, blocksize):
                        self.device_index = device_index
                        self.actual_rate = rate
                        self.channels = channels
                        self.actual_blocksize = blocksize
                        self._resample_ratio = rate / self.target_rate
                        self._needs_stereo_downmix = (channels == 2)
                        self.available = True
                        
                        if rate == self.target_rate:
                            logger.info(f"Device '{device_name}' supports native {self.target_rate}Hz")
                        else:
                            logger.info(f"Device '{device_name}' using {rate}Hz "
                                       f"(will resample to {self.target_rate}Hz)")
                        return True
        
        logger.debug(f"Device '{device_name}' failed all configuration attempts")
        return False

    def _test_config(self, device_index: int, sample_rate: int, channels: int, blocksize: int) -> bool:
        """Test if device supports given configuration."""
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

    def _resample(self, audio_data, from_rate, to_rate):
        """Resample audio from one rate to another.
        
        Uses linear interpolation - simple and fast enough for wake word.
        """
        if from_rate == to_rate:
            return audio_data
        
        ratio = to_rate / from_rate
        new_length = int(len(audio_data) * ratio)
        
        if new_length == 0:
            return np.array([], dtype=np.int16)
        
        # Linear interpolation
        old_indices = np.arange(len(audio_data))
        new_indices = np.linspace(0, len(audio_data) - 1, new_length)
        resampled = np.interp(new_indices, old_indices, audio_data.astype(np.float32))
        
        return resampled.astype(np.int16)

    def _convert_to_mono(self, audio_data: np.ndarray) -> np.ndarray:
        """Convert stereo audio to mono by averaging channels."""
        if len(audio_data.shape) > 1 and audio_data.shape[1] == 2:
            return (audio_data[:, 0].astype(np.int32) + audio_data[:, 1].astype(np.int32)) // 2
        return audio_data.flatten()

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
            logger.info(f"Audio stream opened: device={self.device_index}, "
                       f"rate={self.actual_rate}, channels={self.channels}, chunk={actual_chunk}")
        except Exception as e:
            logger.error(f"Failed to open audio stream: {classify_audio_error(e)}")
            self.stream = None
            # Don't raise - allow app to continue without wake word

    def stop_recording(self):
        """Close audio stream."""
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception as e:
                logger.debug(f"Error closing stream: {e}")
            self.stream = None
            logger.info("Audio stream closed")

    def get_stream(self):
        """Return the underlying stream (for compatibility)."""
        return self.stream

    def get_latest_chunk(self, duration):
        """Get latest audio chunk with frame skipping optimization.
        
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
                logger.debug("Audio buffer overflow (non-fatal)")
            
            # Convert stereo to mono if needed
            if self._needs_stereo_downmix:
                audio = self._convert_to_mono(data).astype(np.int16)
            else:
                audio = data.flatten().astype(np.int16)
            
            # Resample to target rate if needed
            if self._resample_ratio != 1.0:
                audio = self._resample(audio, self.actual_rate, self.target_rate)
            
            self.previous_result = audio
            
        except Exception as e:
            logger.warning(f"Error reading audio chunk: {classify_audio_error(e)}")
            # Return previous result to avoid breaking detection loop
        
        return self.previous_result