import numpy as np
import sounddevice as sd
import threading
import os
import time
import logging
from concurrent.futures import ThreadPoolExecutor
import config
from core.event_bus import publish, Events

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
        
        # Output device setup for tone playback
        self.output_device = None
        self.output_device_name = None
        self.output_rate = None
        self.tone_available = False
        self._init_output_device()
        
        # Pre-generate tone for wake acknowledgment
        self.tone_data = None
        self.tone_sample_rate = None
        if self.tone_available:
            self._generate_tone()
        
        self.callback_pool = ThreadPoolExecutor(max_workers=1)
        self.playback_lock = threading.Lock()

    def _init_output_device(self):
        """Find a working output device via DeviceManager (respects AUDIO_OUTPUT_DEVICE setting)."""
        self.tone_available = False
        try:
            from core.audio import get_device_manager
            dm = get_device_manager()
            dev_idx, default_rate, dev_name = dm.find_output_device()
            if dev_idx is None:
                logger.warning("No output devices found - wake tone disabled")
                return

            dev_info = {'name': dev_name, 'default_samplerate': default_rate}
            if self._try_output_device(dev_idx, dev_info):
                self.output_device_name = dev_name
                return

            logger.warning(f"Output device '{dev_name}' failed, trying all outputs")
            for dev in dm.get_output_devices():
                info = {'name': dev.name, 'default_samplerate': dev.default_samplerate}
                if self._try_output_device(dev.index, info):
                    self.output_device_name = dev.name
                    return

            logger.warning("No compatible output device found - wake tone disabled")
        except Exception as e:
            logger.error(f"Output device init failed: {e}")

    def _try_output_device(self, device_index, dev_info):
        """Try to use an output device, testing sample rates."""
        device_name = dev_info['name']
        default_rate = int(dev_info['default_samplerate'])
        
        logger.info(f"Testing output device '{device_name}' (default_rate={default_rate})")
        
        # Preferred rate 48kHz, then common rates
        preferred_rate = 48000
        test_rates = [preferred_rate, default_rate, 48000, 44100, 32000, 24000, 22050, 16000, 96000]
        # Remove duplicates while preserving order
        seen = set()
        test_rates = [r for r in test_rates if not (r in seen or seen.add(r))]
        
        for rate in test_rates:
            if self._test_output_rate(device_index, rate):
                self.output_device = device_index
                self.output_rate = rate
                self.tone_available = True
                logger.info(f"Tone output device '{device_name}' OK at {rate}Hz")
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

    def _generate_tone(self):
        """Generate wake acknowledgment tone at detected output rate."""
        duration = getattr(config, 'WAKE_TONE_DURATION', 0.15)
        frequency = getattr(config, 'WAKE_TONE_FREQUENCY', 880)
        
        samples = np.linspace(0, duration, int(self.output_rate * duration), endpoint=False)
        self.tone_data = (0.5 * np.sin(2 * np.pi * frequency * samples)).astype(np.float32)
        self.tone_sample_rate = self.output_rate
        
        logger.debug(f"Generated wake tone: {frequency}Hz, {duration}s, {self.output_rate}Hz sample rate")

    def set_audio_recorder(self, audio_recorder):
        self.audio_recorder = audio_recorder

    def add_detection_callback(self, callback):
        self.callbacks.append(callback)
        
    def set_system(self, system):
        """Set reference to the main system."""
        self.system = system

    def _play_tone(self):
        """Play wake acknowledgment tone using sounddevice's built-in playback."""
        if not self.tone_available or self.tone_data is None:
            return

        with self.playback_lock:
            try:
                sd.play(self.tone_data, self.tone_sample_rate, device=self.output_device)
            except sd.PortAudioError as pa_err:
                logger.warning(f"Tone output device {self.output_device} failed: {pa_err} — re-probing")
                self._init_output_device()
                if self.tone_available:
                    self._generate_tone()
                    try:
                        sd.play(self.tone_data, self.tone_sample_rate, device=self.output_device)
                    except Exception as e2:
                        logger.debug(f"Tone playback retry failed: {e2}")
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
        # Runtime guard: skip if wakeword disabled via settings
        if not config.WAKE_WORD_ENABLED:
            logger.debug("Wakeword detected but WAKE_WORD_ENABLED=False, ignoring")
            return

        # Suppress during web UI activity (recording/chatting)
        if self.system and getattr(self.system, '_web_active', False):
            logger.info("Wakeword detected but web UI active, suppressing")
            self._reset_detection_state()
            return

        publish(Events.WAKEWORD_DETECTED)

        # on_wake hook — plugins can react to wakeword detection
        from core.hooks import hook_runner, HookEvent
        if hook_runner.has_handlers("on_wake"):
            hook_runner.fire("on_wake", HookEvent(config=config))

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
        from core.stt.utils import can_transcribe

        # Shared guard: skip if STT disabled or not initialized
        ok, reason = can_transcribe(self.system.whisper_client)
        if not ok:
            logger.info(f"Wakeword fired but STT unavailable: {reason}")
            publish(Events.STT_ERROR, {"message": f"Wakeword heard — {reason}"})
            return

        start_time = threading.local()
        start_time.value = time.time()
        logger.info("Wake word detected! Starting to listen...")

        # Stop wakeword audio stream to avoid conflict with STT recorder
        # Both use the same audio device - running simultaneously causes heap corruption
        if self.audio_recorder:
            logger.debug("Stopping wakeword audio stream for STT handoff")
            self.audio_recorder.stop_recording()

        # Play tone AFTER InputStream is closed to avoid device contention
        self._play_tone()

        try:
            logger.info("Recording your message...")
            audio_file = self.system.whisper_recorder.record_audio()

            if not audio_file or not os.path.exists(audio_file):
                logger.warning("No audio file produced")
                self.system.speak_error('file')
                return

            process_time = time.time()
            try:
                text = self.system.whisper_client.transcribe_file(audio_file)
            finally:
                try:
                    os.unlink(audio_file)
                except OSError:
                    pass
            logger.info(f"Processing took: {(time.time() - process_time)*1000:.1f}ms")

            if not text or not text.strip():
                logger.warning("No speech detected")
                self.system.speak_error('speech')
                return

            # post_stt hook — plugins can correct/translate/normalize transcription
            from core.hooks import hook_runner, HookEvent
            if hook_runner.has_handlers("post_stt"):
                stt_event = HookEvent(input=text, config=config,
                                      metadata={"system": self.system})
                hook_runner.fire("post_stt", stt_event)
                text = stt_event.input

            logger.info(f"Transcribed: user text hidden")
            self.system.process_llm_query(text)

        except Exception as e:
            logger.error(f"Error during recording: {e}")
            self.system.speak_error('recording')
        finally:
            logger.info(f"Total wake word handling took: {(time.time() - start_time.value)*1000:.1f}ms")

            # Wait for TTS to finish before restarting wakeword audio —
            # avoids PortAudio device contention between TTS OutputStream
            # and the wakeword InputStream on backends that don't support
            # simultaneous streams on the same device
            try:
                self.system.tts.wait(timeout=60)
            except Exception:
                pass

            # Restart wakeword audio stream after TTS is done
            if self.audio_recorder:
                logger.debug("Restarting wakeword audio stream after STT/TTS")
                self.audio_recorder.start_recording()

    def _listen_loop(self):
        """Main listening loop - polls OWW for predictions."""
        # OWW works best with 80ms frames (1280 samples at 16kHz)
        frame_samples = 1280
        consecutive_errors = 0
        max_consecutive = 10  # After 10 rapid errors, back off hard

        logger.info(f"Listen loop started: frame_samples={frame_samples}, threshold={self.threshold}")

        while self.running:
            try:
                # Pause processing when disabled at runtime (save CPU)
                if not config.WAKE_WORD_ENABLED:
                    time.sleep(0.5)
                    continue

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

                consecutive_errors = 0  # Reset on successful read

            except Exception as e:
                if not self.running:
                    break
                consecutive_errors += 1
                if consecutive_errors <= 3:
                    logger.warning(f"Audio stream hiccup ({consecutive_errors}/3): {e}")
                elif consecutive_errors == max_consecutive:
                    logger.warning(f"Audio stream error persisting ({consecutive_errors}x), attempting recovery: {e}")
                # Exponential backoff: 0.1, 0.2, 0.4, ... capped at 5s
                backoff = min(0.1 * (2 ** (consecutive_errors - 1)), 5.0)
                time.sleep(backoff)
                # Try to recover the stream after persistent errors
                if consecutive_errors >= max_consecutive:
                    try:
                        self.audio_recorder.stop_recording()
                        time.sleep(1)
                        self.audio_recorder.start_recording()
                        logger.info("Attempted stream recovery after persistent errors")
                        consecutive_errors = 0
                    except Exception as recovery_err:
                        logger.error(f"Stream recovery failed: {recovery_err}")
                        time.sleep(5)

    def start_listening(self):
        if not self.audio_recorder:
            logger.error("No audio recorder set")
            raise ValueError("No audio recorder set")
        
        # Check if audio recorder initialized successfully
        if not getattr(self.audio_recorder, 'available', True):
            logger.warning("Audio recorder unavailable - wake word detection disabled")
            return
        
        stream = self.audio_recorder.get_stream()
        if stream is None:
            logger.warning("Audio stream is None - wake word detection disabled")
            return
        
        logger.info(f"Starting OpenWakeWord detection: model={self.model_name}, threshold={self.threshold}")

        # Recreate callback pool if it was shut down (e.g. after stop_listening)
        if self.callback_pool._shutdown:
            self.callback_pool = ThreadPoolExecutor(max_workers=1)

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