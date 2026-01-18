import requests
import os
import tempfile
import time
import threading
import logging
import config
import re
import gc
import numpy as np
import sounddevice as sd
import soundfile as sf
from core.event_bus import publish, Events

logger = logging.getLogger(__name__)


def get_temp_dir():
    """Get optimal temp directory. Prefers /dev/shm (Linux RAM disk) for speed."""
    shm = '/dev/shm'
    if os.path.exists(shm) and os.access(shm, os.W_OK):
        return shm
    return tempfile.gettempdir()


class TTSClient:
    """Generic HTTP-based TTS client with server fallback and cross-platform audio playback"""
    
    def __init__(self):
        """Initialize TTS client with fallback capability"""
        self.primary_server = config.TTS_PRIMARY_SERVER
        self.fallback_server = config.TTS_FALLBACK_SERVER
        self.fallback_timeout = config.TTS_FALLBACK_TIMEOUT
        # Hardcoded fallbacks - chat settings override these on chat load
        self.pitch_shift = 0.98
        self.speed = 1.3
        self.voice_name = "af_heart"
        self.temp_dir = get_temp_dir()
        
        self.lock = threading.Lock()
        self.should_stop = threading.Event()
        self._is_playing = False
        
        # Audio output device setup
        self.output_device = None
        self.output_rate = None
        self.audio_available = False
        self._init_output_device()
        
        logger.info(f"TTS client initialized: {self.primary_server}")
        logger.info(f"Voice: {self.voice_name}, Speed: {self.speed}, Pitch: {self.pitch_shift}")
        logger.info(f"Temp directory: {self.temp_dir}")
        
        if self.audio_available:
            logger.info(f"Audio playback: device={self.output_device}, rate={self.output_rate}Hz")
        else:
            logger.warning("Audio playback unavailable - TTS will be silent")
    
    def _init_output_device(self):
        """Find a working output device and compatible sample rate."""
        try:
            devices = sd.query_devices()
        except Exception as e:
            logger.error(f"Failed to query audio devices: {e}")
            return
        
        # Build list of output devices
        output_devices = []
        for i, dev in enumerate(devices):
            if dev['max_output_channels'] > 0:
                logger.debug(f"Found output device {i}: {dev['name']} "
                           f"(default_rate={dev['default_samplerate']})")
                output_devices.append((i, dev))
        
        if not output_devices:
            logger.error("No output devices found")
            return
        
        # Try default device first
        try:
            default_out = sd.default.device[1]
            if default_out is not None:
                for idx, dev_info in output_devices:
                    if idx == default_out:
                        if self._try_output_device(idx, dev_info):
                            return
                        break
        except Exception:
            pass
        
        # Fall back to any available device
        for idx, dev_info in output_devices:
            if self._try_output_device(idx, dev_info):
                return
        
        logger.error("No compatible output device found")

    def _try_output_device(self, device_index, dev_info):
        """Try to use an output device, testing sample rates.
        
        Returns True if device is usable.
        """
        device_name = dev_info['name']
        default_rate = int(dev_info['default_samplerate'])
        
        logger.info(f"Testing output device '{device_name}' (default_rate={default_rate})")
        
        # Common TTS output rates to test
        test_rates = [default_rate, 48000, 44100, 32000, 24000, 22050, 16000, 96000]
        # Remove duplicates while preserving order
        seen = set()
        test_rates = [r for r in test_rates if not (r in seen or seen.add(r))]
        
        for rate in test_rates:
            if self._test_output_rate(device_index, rate):
                self.output_device = device_index
                self.output_rate = rate
                self.audio_available = True
                logger.info(f"Output device '{device_name}' OK at {rate}Hz")
                return True
        
        logger.debug(f"Output device '{device_name}' failed all sample rate tests")
        return False

    def _test_output_rate(self, device_index, sample_rate):
        """Test if output device supports a given sample rate."""
        try:
            stream = sd.OutputStream(
                device=device_index,
                samplerate=sample_rate,
                channels=1,
                dtype=np.float32
            )
            stream.close()
            logger.info(f"  -> {sample_rate}Hz: OK")
            return True
        except Exception as e:
            logger.debug(f"  -> {sample_rate}Hz: FAIL ({e})")
            return False

    def _resample(self, audio_data, from_rate, to_rate):
        """Resample audio from one rate to another using linear interpolation."""
        if from_rate == to_rate:
            return audio_data
        
        ratio = to_rate / from_rate
        old_length = len(audio_data)
        new_length = int(old_length * ratio)
        
        if new_length == 0:
            return np.array([], dtype=audio_data.dtype)
        
        old_indices = np.arange(old_length)
        new_indices = np.linspace(0, old_length - 1, new_length)
        resampled = np.interp(new_indices, old_indices, audio_data.astype(np.float64))
        
        return resampled.astype(audio_data.dtype)

    def set_voice(self, voice_name):
        """Set the voice for TTS"""
        self.voice_name = voice_name
        logger.info(f"Voice set to: {self.voice_name}")
        return True
    
    def set_speed(self, speed):
        """Set the speech speed"""
        self.speed = float(speed)
        logger.info(f"Speed set to: {self.speed}")
        return True
    
    def set_pitch(self, pitch):
        """Set the pitch shift"""
        self.pitch_shift = float(pitch)
        logger.info(f"Pitch set to: {self.pitch_shift}")
        return True

    def check_server_health(self, server_url, timeout=None):
        """Check if TTS server is available"""
        try:
            response = requests.get(f"{server_url}/health", timeout=timeout)
            return response.status_code == 200
        except:
            return False
            
    def get_server_url(self):
        """Get available server URL with fallback logic"""
        if self.check_server_health(self.primary_server, timeout=self.fallback_timeout):
            return self.primary_server
        logger.info(f"Primary unavailable, using fallback: {self.fallback_server}")
        return self.fallback_server

    def speak(self, text):
        """Send text to TTS server and play audio"""
        if not self.audio_available:
            logger.warning("Audio playback unavailable - skipping TTS")
            return False
        
        # Strip content that shouldn't be spoken (order matters)
        processed_text = text
        
        # Remove block-level content entirely
        block_patterns = [
            r'<think>.*?</think>',           # Think tags
            r'<reasoning>.*?</reasoning>',   # Reasoning tags
            r'<tools>.*?</tools>',           # Tools tags
            r'```[\s\S]*?```',               # Code blocks (fenced)
            r'`[^`]+`',                      # Inline code
            r'!\[.*?\]\(.*?\)',              # Image markdown ![alt](url)
            r'\|.*?\|(?:\n\|.*?\|)*',        # Markdown tables
            r'<[^>]+>',                      # HTML tags
        ]
        for pattern in block_patterns:
            processed_text = re.sub(pattern, ' ', processed_text, flags=re.DOTALL)
        
        # Transform markdown to speech-friendly punctuation
        # Bold **text** or __text__ → period before and after for emphasis pause
        processed_text = re.sub(r'\*\*([^*]+)\*\*', r'. \1. ', processed_text)
        processed_text = re.sub(r'__([^_]+)__', r'. \1. ', processed_text)
        
        # Italic *text* or _text_ → comma for slight pause (but not mid-word apostrophes)
        processed_text = re.sub(r'(?<!\w)\*([^*]+)\*(?!\w)', r', \1, ', processed_text)
        processed_text = re.sub(r'(?<!\w)_([^_]+)_(?!\w)', r', \1, ', processed_text)
        
        # Links [anchor](url) → keep anchor text only
        processed_text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', processed_text)
        
        # Headers # Title → Title with period at end of line
        processed_text = re.sub(r'^#+\s*(.+)$', r'\1.', processed_text, flags=re.MULTILINE)
        
        # Clean up remaining markdown artifacts
        processed_text = re.sub(r'[*_#]', '', processed_text)  # Stray markers
        processed_text = re.sub(r'\n', ' ', processed_text)    # Newlines to space
        
        # Remove common UI text that shouldn't be spoken
        ui_words = ['Copy', 'Copied!', 'Failed', 'Loading...', '...']
        for word in ui_words:
            processed_text = processed_text.replace(word, '')
        
        # Normalize punctuation and whitespace
        processed_text = re.sub(r'[,\.]\s*[,\.]', '.', processed_text)  # Collapse ., or ..
        processed_text = re.sub(r'\s+', ' ', processed_text).strip()
        
        self.stop()
        self.should_stop.clear()
        
        threading.Thread(
            target=self._generate_and_play_audio,
            args=(processed_text,),
            daemon=True
        ).start()
        
        return True
        
    def _apply_pitch_shift(self, audio_data, samplerate):
        """Apply pitch shifting to audio data in memory"""
        if self.pitch_shift == 1.0:
            return audio_data, samplerate
            
        try:
            # Convert to mono if stereo for pitch processing
            if len(audio_data.shape) > 1:
                mono_data = audio_data.mean(axis=1)
            else:
                mono_data = audio_data
            
            # Resample to shift pitch
            original_length = len(mono_data)
            new_length = int(original_length / self.pitch_shift)
            indices = np.linspace(0, original_length - 1, new_length)
            shifted_data = np.interp(indices, np.arange(original_length), mono_data)
            
            return shifted_data.astype(audio_data.dtype), samplerate
            
        except Exception as e:
            logger.error(f"Error applying pitch shift: {e}")
            return audio_data, samplerate

    def _fetch_audio(self, text):
        """Fetch audio from server. Returns (audio_data, samplerate) or (None, None)."""
        temp_path = None
        try:
            server_url = self.get_server_url()
            tts_url = f"{server_url}/tts"
            
            response = requests.post(tts_url, data={
                'text': text.replace("*", ""),
                'voice': self.voice_name,
                'speed': self.speed
            })
            
            if response.status_code != 200:
                logger.error(f"TTS server error: {response.status_code}")
                return None, None
            
            # Save to temp file (soundfile needs a file to read WAV properly)
            fd, temp_path = tempfile.mkstemp(suffix='.flac', dir=self.temp_dir)
            os.close(fd)
            
            with open(temp_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if self.should_stop.is_set():
                        break
                    f.write(chunk)
            
            if self.should_stop.is_set():
                return None, None
            
            # Load audio data
            audio_data, samplerate = sf.read(temp_path)
            
            # Apply pitch shift if needed
            if self.pitch_shift != 1.0:
                audio_data, samplerate = self._apply_pitch_shift(audio_data, samplerate)
            
            return audio_data, samplerate
            
        except Exception as e:
            logger.error(f"Error fetching audio: {e}")
            return None, None
        finally:
            # Clean up temp file
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except:
                    pass
        
    def _generate_and_play_audio(self, text):
        """Generate audio from server and play it using sounddevice"""
        if not self.audio_available:
            return
        
        try:
            audio_data, samplerate = self._fetch_audio(text)
            if audio_data is None or self.should_stop.is_set():
                return
            
            with self.lock:
                if self.should_stop.is_set():
                    return
                self._is_playing = True
                publish(Events.TTS_PLAYING)
            
            # Convert stereo to mono if needed
            if len(audio_data.shape) > 1:
                audio_data = audio_data.mean(axis=1)
            
            # Resample to output device rate if different
            if samplerate != self.output_rate:
                logger.debug(f"Resampling audio from {samplerate}Hz to {self.output_rate}Hz")
                audio_data = self._resample(audio_data, samplerate, self.output_rate)
                samplerate = self.output_rate
            
            # Play audio
            sd.play(audio_data, samplerate, device=self.output_device)
            
            # Wait for playback to complete or stop signal
            while sd.get_stream() and sd.get_stream().active and not self.should_stop.is_set():
                time.sleep(0.05)
            
            # If stopped early, halt playback
            if self.should_stop.is_set():
                sd.stop()
                
        except Exception as e:
            logger.error(f"Error in TTS playback: {e}")
        finally:
            with self.lock:
                self._is_playing = False
                publish(Events.TTS_STOPPED)
            gc.collect()

    def stop(self):
        """Stop currently playing audio"""
        self.should_stop.set()
        with self.lock:
            if self._is_playing:
                try:
                    sd.stop()
                except:
                    pass
                self._is_playing = False

    def generate_audio_data(self, text):
        """Generate audio and return raw bytes for file download"""
        temp_path = None
        try:
            server_url = self.get_server_url()
            tts_url = f"{server_url}/tts"
            
            response = requests.post(tts_url, data={
                'text': text.replace("*", ""),
                'voice': self.voice_name,
                'speed': self.speed
            })
            
            if response.status_code != 200:
                return None
            
            # Save to temp, apply pitch, return bytes
            fd, temp_path = tempfile.mkstemp(suffix='.flac', dir=self.temp_dir)
            os.close(fd)
            
            with open(temp_path, 'wb') as f:
                f.write(response.content)
            
            # Apply pitch shift if needed
            if self.pitch_shift != 1.0:
                audio_data, samplerate = sf.read(temp_path)
                audio_data, samplerate = self._apply_pitch_shift(audio_data, samplerate)
                sf.write(temp_path, audio_data, samplerate)
            
            with open(temp_path, 'rb') as f:
                return f.read()
                
        except Exception as e:
            logger.error(f"Error generating audio data: {e}")
            return None
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except:
                    pass