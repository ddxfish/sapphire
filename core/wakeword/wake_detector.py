import numpy as np
from precise_runner import PreciseRunner, PreciseEngine
import sounddevice as sd
import threading
import os
import time
import logging
from concurrent.futures import ThreadPoolExecutor
import config

logger = logging.getLogger(__name__)

class WakeWordDetector:
    def __init__(self, model_path):
        # Validate paths before attempting initialization
        if not os.path.exists(config.PRECISE_ENGINE_PATH):
            logger.error(f"precise-engine not found at: {config.PRECISE_ENGINE_PATH}")
            raise FileNotFoundError(f"precise-engine missing: {config.PRECISE_ENGINE_PATH}")
        
        if not os.path.exists(model_path):
            logger.error(f"Wake word model not found at: {model_path}")
            raise FileNotFoundError(f"Model missing: {model_path}")
        
        logger.info(f"Initializing PreciseEngine: {config.PRECISE_ENGINE_PATH}")
        logger.info(f"Model: {model_path}, chunk_size={config.CHUNK_SIZE}")
        
        try:
            self.engine = PreciseEngine(
                exe_file=config.PRECISE_ENGINE_PATH,
                model_file=model_path,
                chunk_size=config.CHUNK_SIZE
            )
            logger.info("PreciseEngine initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize PreciseEngine: {e}")
            raise
        
        self.audio_recorder = None
        self.runner = None
        self.callbacks = []
        self.system = None  # Reference to the main system
        
        # Simplified tone generation
        samples = np.linspace(0, config.WAKE_TONE_DURATION, 
                              int(config.PLAYBACK_SAMPLE_RATE * config.WAKE_TONE_DURATION))
        self.tone_data = (0.5 * np.sin(2 * np.pi * config.WAKE_TONE_FREQUENCY * samples)
                        ).astype(np.float32)
        
        # Audio output stream
        self.audio_stream = sd.OutputStream(
            samplerate=config.PLAYBACK_SAMPLE_RATE,
            channels=1,
            dtype=np.float32,
            latency=0.001,
            blocksize=128
        )
        self.audio_stream.start()
        
        self.callback_pool = ThreadPoolExecutor(max_workers=config.CALLBACK_THREAD_POOL_SIZE)
        self.playback_lock = threading.Lock()

    def set_audio_recorder(self, audio_recorder):
        self.audio_recorder = audio_recorder

    def add_detection_callback(self, callback):
        self.callbacks.append(callback)
        
    def set_system(self, system):
        """Set reference to the main system."""
        self.system = system

    def _play_tone(self):
        with self.playback_lock:
            self.audio_stream.write(self.tone_data)

    def _on_activation(self):
        # Simplified callback handling - removed timing code
        self.callback_pool.submit(self._play_tone)
        
        # Process wake word detection if system is set
        if self.system:
            self.wake_word_detected()
        else:
            # Execute callbacks (for backward compatibility)
            for callback in self.callbacks:
                callback()
                
    def wake_word_detected(self):
        """Handle wake word detection by recording and processing user speech."""
        start_time = threading.local()
        start_time.value = time.time()
        logger.info("Wake word detected! Starting to listen...")
        
        try:
            # Record user's speech
            logger.info("Recording your message...")
            audio_file = self.system.whisper_recorder.record_audio()
            
            if not audio_file or not os.path.exists(audio_file):
                logger.warning("No audio file produced")
                self.system.speak_error('file')
                return
                
            # Process audio
            process_time = time.time()
            text = self.system.whisper_client.transcribe_file(audio_file)
            logger.info(f"Processing took: {(time.time() - process_time)*1000:.1f}ms")
            
            if not text or not text.strip():
                logger.warning("No speech detected")
                self.system.speak_error('speech')
                return
                
            # Process transcribed text
            logger.info(f"Transcribed: user text hidden")
            self.system.process_llm_query(text)
                    
        except Exception as e:
            logger.error(f"Error during recording: {e}")
            self.system.speak_error('recording')
        finally:
            logger.info(f"Total wake word handling took: {(time.time() - start_time.value)*1000:.1f}ms")

    def start_listening(self):
        if not self.audio_recorder:
            logger.error("No audio recorder set")
            raise ValueError("No audio recorder set")
        
        stream = self.audio_recorder.get_stream()
        if stream is None:
            logger.error("Audio stream is None - cannot start wake word detection")
            raise ValueError("Audio stream not available")
        
        logger.info(f"Starting PreciseRunner: trigger_level={config.WAKE_WORD_TRIGGER_LEVEL}, sensitivity={config.WAKE_WORD_SENSITIVITY}")
        
        try:
            self.runner = PreciseRunner(
                engine=self.engine,
                on_activation=self._on_activation,
                trigger_level=config.WAKE_WORD_TRIGGER_LEVEL,
                sensitivity=config.WAKE_WORD_SENSITIVITY,
                stream=stream
            )
            
            self.runner.start()
            logger.info("Wake word detection started successfully")
        except Exception as e:
            logger.error(f"Failed to start PreciseRunner: {e}")
            raise

    def stop_listening(self):
        if self.runner:
            self.runner.stop()
            logger.info("PreciseRunner stopped")
        if self.audio_stream:
            self.audio_stream.stop()
            self.audio_stream.close()
            logger.info("Audio output stream closed")
        self.callback_pool.shutdown()