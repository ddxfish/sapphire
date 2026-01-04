import numpy as np
import sounddevice as sd
import array
import logging
import config

logger = logging.getLogger(__name__)

# Target rate for OpenWakeWord (expects 16kHz)
OWW_SAMPLE_RATE = 16000


class AudioRecorder:
    """Audio recorder for wake word detection using sounddevice.
    
    Handles device capability detection, sample rate negotiation,
    and resampling to 16kHz for OpenWakeWord compatibility.
    """
    
    def __init__(self):
        self.target_rate = OWW_SAMPLE_RATE
        self.actual_rate = None
        self.device_index = None
        self.chunk_size = config.CHUNK_SIZE
        self.stream = None
        self.available = False
        self._resample_ratio = 1.0
        
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
                       f"resample={self._resample_ratio != 1.0}")
        else:
            logger.warning("AudioRecorder unavailable - wake word detection disabled")

    def _init_device(self):
        """Find a working input device and compatible sample rate."""
        try:
            devices = sd.query_devices()
        except Exception as e:
            logger.error(f"Failed to query audio devices: {e}")
            return
        
        # Build list of input devices
        input_devices = []
        for i, dev in enumerate(devices):
            if dev['max_input_channels'] > 0:
                logger.debug(f"Found input device {i}: {dev['name']} "
                           f"(default_rate={dev['default_samplerate']})")
                input_devices.append((i, dev))
        
        if not input_devices:
            logger.error("No input devices found")
            return
        
        # Try preferred devices first
        preferred = getattr(config, 'RECORDER_PREFERRED_DEVICES', ['default'])
        
        for pref in preferred:
            for idx, dev_info in input_devices:
                if pref.lower() in dev_info['name'].lower():
                    if self._try_device(idx, dev_info):
                        return
        
        # Fall back to any available device
        for idx, dev_info in input_devices:
            if self._try_device(idx, dev_info):
                return
        
        logger.error("No compatible audio device found")

    def _try_device(self, device_index, dev_info):
        """Try to initialize a device, testing sample rates.
        
        Returns True if device is usable.
        """
        device_name = dev_info['name']
        default_rate = int(dev_info['default_samplerate'])
        
        # Priority: try 16kHz first (native OWW rate, no resampling needed)
        if self._test_rate(device_index, self.target_rate):
            self.device_index = device_index
            self.actual_rate = self.target_rate
            self._resample_ratio = 1.0
            self.available = True
            logger.info(f"Device '{device_name}' supports native {self.target_rate}Hz")
            return True
        
        # Try device's default rate
        if self._test_rate(device_index, default_rate):
            self.device_index = device_index
            self.actual_rate = default_rate
            self._resample_ratio = default_rate / self.target_rate
            self.available = True
            logger.info(f"Device '{device_name}' using {default_rate}Hz "
                       f"(will resample to {self.target_rate}Hz)")
            return True
        
        # Try common fallback rates
        fallback_rates = getattr(config, 'RECORDER_SAMPLE_RATES', [44100, 48000])
        for rate in fallback_rates:
            if rate != default_rate and self._test_rate(device_index, rate):
                self.device_index = device_index
                self.actual_rate = rate
                self._resample_ratio = rate / self.target_rate
                self.available = True
                logger.info(f"Device '{device_name}' using fallback {rate}Hz "
                           f"(will resample to {self.target_rate}Hz)")
                return True
        
        logger.debug(f"Device '{device_name}' failed all sample rate tests")
        return False

    def _test_rate(self, device_index, sample_rate):
        """Test if device supports a given sample rate."""
        try:
            stream = sd.InputStream(
                device=device_index,
                samplerate=sample_rate,
                channels=1,
                dtype=np.int16,
                blocksize=self.chunk_size
            )
            stream.close()
            logger.debug(f"Device {device_index} OK at {sample_rate}Hz")
            return True
        except Exception as e:
            logger.debug(f"Device {device_index} failed at {sample_rate}Hz: {e}")
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
            actual_chunk = int(self.chunk_size * self._resample_ratio)
            
            self.stream = sd.InputStream(
                device=self.device_index,
                samplerate=self.actual_rate,
                channels=1,
                dtype=np.int16,
                blocksize=actual_chunk
            )
            self.stream.start()
            logger.info(f"Audio stream opened: device={self.device_index}, "
                       f"rate={self.actual_rate}, chunk={actual_chunk}")
        except Exception as e:
            logger.error(f"Failed to open audio stream: {e}")
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
            
            audio = data.flatten().astype(np.int16)
            
            # Resample to target rate if needed
            if self._resample_ratio != 1.0:
                audio = self._resample(audio, self.actual_rate, self.target_rate)
            
            self.previous_result = audio
            
        except Exception as e:
            logger.warning(f"Error reading audio chunk: {e}")
            # Return previous result to avoid breaking detection loop
        
        return self.previous_result