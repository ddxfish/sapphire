import numpy as np
import sounddevice as sd
import threading
import os
import time
import logging
from concurrent.futures import ThreadPoolExecutor
import config

logger = logging.getLogger(__name__)

class WakeWordDetector:
    def __init__(self, model_name=None):
        """Initialize OpenWakeWord detector.
        
        Args:
            model_name: Name of wakeword model (e.g., 'hey_mycroft', 'hey_jarvis', 'alexa')
                       or path to custom .onnx/.tflite file.
                       If None, uses config.WAKEWORD_MODEL
        """
        try:
            import openwakeword
            from openwakeword.model import Model
            self._oww_model_class = Model
        except ImportError as e:
            logger.error(f"OpenWakeWord not installed: {e}")
            raise ImportError("openwakeword package required. Install with: pip install openwakeword")
        
        # Resolve model name to path if it's a custom model
        from core.wakeword import resolve_model_path
        raw_model = model_name or config.WAKEWORD_MODEL
        self.model_name = raw_model  # Keep original for display/predictions
        self.model_path = resolve_model_path(raw_model)
        
        self.threshold = getattr(config, 'WAKEWORD_THRESHOLD', 0.5)
        
        logger.info(f"Initializing OpenWakeWord: model={self.model_name}, path={self.model_path}, threshold={self.threshold}")
        
        try:
            self.model = self._oww_model_class(
                wakeword_models=[self.model_path],
                inference_framework=getattr(config, 'WAKEWORD_FRAMEWORK', 'onnx')
            )
            logger.info("OpenWakeWord model initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize OpenWakeWord: {e}")
            raise
        
        self.audio_recorder = None
        self.callbacks = []
        self.system = None
        self.running = False
        self.listen_thread = None
        
        # Pre-generate tone for wake acknowledgment
        # sd.play() handles all buffering/latency cross-platform
        duration = getattr(config, 'WAKE_TONE_DURATION', 0.15)
        frequency = getattr(config, 'WAKE_TONE_FREQUENCY', 880)
        sample_rate = getattr(config, 'PLAYBACK_SAMPLE_RATE', 48000)
        
        samples = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
        self.tone_data = (0.5 * np.sin(2 * np.pi * frequency * samples)).astype(np.float32)
        self.tone_sample_rate = sample_rate
        
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
        """Play wake acknowledgment tone using sounddevice's built-in playback."""
        with self.playback_lock:
            try:
                sd.play(self.tone_data, self.tone_sample_rate)
                # Don't wait - let it play async
            except Exception as e:
                logger.debug(f"Tone playback error: {e}")

    def _flush_audio_buffer(self):
        """Discard any accumulated audio in the input buffer to prevent stale detections."""
        try:
            stream = self.audio_recorder.get_stream()
            if stream and stream.read_available > 0:
                available = stream.read_available
                stream.read(available)  # Discard the data
                logger.debug(f"Flushed {available} samples from audio buffer")
        except Exception as e:
            logger.debug(f"Buffer flush: {e}")

    def _reset_detection_state(self):
        """Reset OWW internal state and flush audio buffer for clean detection."""
        self._flush_audio_buffer()
        try:
            self.model.reset()
            logger.debug("OWW model state reset")
        except Exception as e:
            logger.debug(f"OWW reset: {e}")

    def _on_activation(self):
        """Handle wake word activation."""
        self.callback_pool.submit(self._play_tone)
        
        if self.system:
            self.wake_word_detected()
        else:
            for callback in self.callbacks:
                callback()
        
        # Critical: reset state after activation to prevent false re-triggers
        # Audio buffer accumulated during processing, OWW has stale feature state
        self._reset_detection_state()
                
    def wake_word_detected(self):
        """Handle wake word detection by recording and processing user speech."""
        start_time = threading.local()
        start_time.value = time.time()
        logger.info("Wake word detected! Starting to listen...")
        
        try:
            logger.info("Recording your message...")
            audio_file = self.system.whisper_recorder.record_audio()
            
            if not audio_file or not os.path.exists(audio_file):
                logger.warning("No audio file produced")
                self.system.speak_error('file')
                return
                
            process_time = time.time()
            text = self.system.whisper_client.transcribe_file(audio_file)
            logger.info(f"Processing took: {(time.time() - process_time)*1000:.1f}ms")
            
            if not text or not text.strip():
                logger.warning("No speech detected")
                self.system.speak_error('speech')
                return
                
            logger.info(f"Transcribed: user text hidden")
            self.system.process_llm_query(text)
                    
        except Exception as e:
            logger.error(f"Error during recording: {e}")
            self.system.speak_error('recording')
        finally:
            logger.info(f"Total wake word handling took: {(time.time() - start_time.value)*1000:.1f}ms")

    def _listen_loop(self):
        """Main listening loop - polls OWW for predictions."""
        # OWW works best with 80ms frames (1280 samples at 16kHz)
        frame_samples = 1280
        
        logger.info(f"Listen loop started: frame_samples={frame_samples}, threshold={self.threshold}")
        
        while self.running:
            try:
                stream = self.audio_recorder.get_stream()
                if stream is None:
                    time.sleep(0.1)
                    continue
                
                # Read audio frame (sounddevice returns numpy array directly)
                audio_data, overflowed = stream.read(frame_samples)
                if overflowed:
                    logger.debug("Audio buffer overflow in wake detection")
                audio_array = audio_data.flatten().astype(np.int16)
                
                # Get prediction from OWW
                predictions = self.model.predict(audio_array)
                
                # Check if wake word detected
                # OWW keys predictions by model name (stem), even for custom paths
                score = predictions.get(self.model_name, 0)
                if score >= self.threshold:
                    logger.info(f"Wake word '{self.model_name}' detected with score {score:.3f}")
                    self._on_activation()
                    # Note: _on_activation resets state, minimal cooldown needed
                    time.sleep(0.5)
                    
            except Exception as e:
                if self.running:
                    logger.error(f"Error in listen loop: {e}")
                    time.sleep(0.1)

    def start_listening(self):
        if not self.audio_recorder:
            logger.error("No audio recorder set")
            raise ValueError("No audio recorder set")
        
        stream = self.audio_recorder.get_stream()
        if stream is None:
            logger.error("Audio stream is None - cannot start wake word detection")
            raise ValueError("Audio stream not available")
        
        logger.info(f"Starting OpenWakeWord detection: model={self.model_name}, threshold={self.threshold}")
        
        # Start with clean state
        self._reset_detection_state()
        
        self.running = True
        self.listen_thread = threading.Thread(target=self._listen_loop, daemon=True)
        self.listen_thread.start()
        logger.info("Wake word detection started successfully")

    def stop_listening(self):
        self.running = False
        if self.listen_thread:
            self.listen_thread.join(timeout=2.0)
            logger.info("Listen thread stopped")
        try:
            sd.stop()  # Stop any playing audio
        except Exception:
            pass
        self.callback_pool.shutdown()