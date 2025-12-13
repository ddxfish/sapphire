import requests
import subprocess
import os
import tempfile
import time
import threading
import logging
import config
import re
import gc
import numpy as np
import wave

logger = logging.getLogger(__name__)

class TTSClient:
    """Generic HTTP-based TTS client with server fallback and audio processing"""
    
    def __init__(self):
        """Initialize TTS client with fallback capability"""
        self.primary_server = config.TTS_PRIMARY_SERVER
        self.fallback_server = config.TTS_FALLBACK_SERVER
        self.fallback_timeout = config.TTS_FALLBACK_TIMEOUT
        self.audio_player = config.TTS_AUDIO_PLAYER
        self.pitch_shift = config.TTS_PITCH_SHIFT
        self.speed = config.TTS_SPEED
        self.voice_name = config.TTS_VOICE_NAME
        
        self.play_process = None
        self.lock = threading.Lock()
        self.should_stop = threading.Event()
        
        logger.info(f"TTS client initialized: {self.primary_server}")
        logger.info(f"Voice: {self.voice_name}, Speed: {self.speed}, Pitch: {self.pitch_shift}")
    
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
        
    def _apply_pitch_shift(self, wav_path):
        """Apply pitch shifting to WAV file"""
        if self.pitch_shift == 1.0:
            return wav_path
            
        try:
            temp_dir = '/dev/shm' if os.path.exists('/dev/shm') else '/tmp'
            shifted_path = tempfile.mktemp(suffix='.wav', dir=temp_dir)
            
            with wave.open(wav_path, 'rb') as wf:
                params = wf.getparams()
                frames = wf.readframes(wf.getnframes())
                audio_data = np.frombuffer(frames, dtype=np.int16)
                
                original_length = len(audio_data)
                new_length = int(original_length / self.pitch_shift)
                indices = np.linspace(0, original_length-1, new_length)
                shifted_data = np.interp(indices, np.arange(original_length), audio_data).astype(np.int16)
                
                with wave.open(shifted_path, 'wb') as wf_out:
                    wf_out.setparams(params)
                    wf_out.setnframes(len(shifted_data))
                    wf_out.writeframes(shifted_data.tobytes())
            
            os.unlink(wav_path)
            return shifted_path
            
        except Exception as e:
            logger.error(f"Error applying pitch shift: {e}")
            return wav_path

    def _fetch_and_process_audio(self, text):
        """Fetch audio from server and apply pitch shift. Returns temp file path."""
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
                return None
            
            temp_dir = '/dev/shm' if os.path.exists('/dev/shm') else '/tmp'
            fd, temp_path = tempfile.mkstemp(suffix='.wav', dir=temp_dir)
            os.close(fd)
            
            with open(temp_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if hasattr(self, 'should_stop') and self.should_stop.is_set():
                        break
                    f.write(chunk)
            
            if hasattr(self, 'should_stop') and self.should_stop.is_set():
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
                return None
            
            if self.pitch_shift != 1.0:
                temp_path = self._apply_pitch_shift(temp_path)
            
            return temp_path
            
        except Exception as e:
            logger.error(f"Error fetching/processing audio: {e}")
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except:
                    pass
            return None
        
    def _generate_and_play_audio(self, text):
        """Generate audio from server and play it"""
        temp_path = None
        
        try:
            temp_path = self._fetch_and_process_audio(text)
            if not temp_path or self.should_stop.is_set():
                return
            
            with self.lock:
                if self.should_stop.is_set():
                    if os.path.exists(temp_path):
                        os.unlink(temp_path)
                    return
                
                self.play_process = subprocess.Popen(
                    [self.audio_player, temp_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            
            while self.play_process and self.play_process.poll() is None and not self.should_stop.is_set():
                time.sleep(0.1)
                
            with self.lock:
                if self.play_process and self.play_process.poll() is None:
                    self.play_process.terminate()
                    try:
                        self.play_process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        self.play_process.kill()
                self.play_process = None
            
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)
                
        except Exception as e:
            logger.error(f"Error in TTS client: {e}")
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except:
                    pass
        finally:
            gc.collect()

    def stop(self):
        """Stop currently playing audio"""
        self.should_stop.set()
        with self.lock:
            if self.play_process:
                try:
                    self.play_process.terminate()
                    try:
                        self.play_process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        self.play_process.kill()
                except:
                    pass
                self.play_process = None

    def generate_audio_data(self, text):
        """Generate audio and return raw bytes for file download"""
        temp_path = None
        try:
            temp_path = self._fetch_and_process_audio(text)
            if not temp_path:
                return None
            
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