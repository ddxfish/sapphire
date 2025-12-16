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
        self.pitch_shift = config.TTS_PITCH_SHIFT
        self.speed = config.TTS_SPEED
        self.voice_name = config.TTS_VOICE_NAME
        self.temp_dir = get_temp_dir()
        
        self.lock = threading.Lock()
        self.should_stop = threading.Event()
        self._is_playing = False
        
        logger.info(f"TTS client initialized: {self.primary_server}")
        logger.info(f"Voice: {self.voice_name}, Speed: {self.speed}, Pitch: {self.pitch_shift}")
        logger.info(f"Temp directory: {self.temp_dir}")
        logger.info("Audio playback: sounddevice (cross-platform)")
    
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
        processed_text = re.sub(
            r'<think>.*?</think>|<reasoning>.*?</reasoning>|<tools>.*?</tools>|\[.*?\]|\*|\n',
            '', text, flags=re.DOTALL
        )
        
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
        try:
            audio_data, samplerate = self._fetch_audio(text)
            if audio_data is None or self.should_stop.is_set():
                return
            
            with self.lock:
                if self.should_stop.is_set():
                    return
                self._is_playing = True
            
            # Play audio (blocking call, but we check should_stop in a loop)
            sd.play(audio_data, samplerate)
            
            # Wait for playback to complete or stop signal
            while sd.get_stream() and sd.get_stream().active and not self.should_stop.is_set():
                time.sleep(0.05)
            
            # If stopped early, halt playback
            if self.should_stop.is_set():
                sd.stop()
                
        except Exception as e:
            logger.error(f"Error in TTS client: {e}")
        finally:
            with self.lock:
                self._is_playing = False
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