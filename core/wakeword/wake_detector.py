import numpy as np
import sounddevice as sd
import threading
import os
import time
import logging
from concurrent.futures import ThreadPoolExecutor
import config
from core.audio import convert_to_mono, resample_audio
from core.event_bus import publish, Events

logger = logging.getLogger(__name__)


# Hallucination filter moved to core.stt.hallucination and applied at the
# STT provider boundary (BaseSTTProvider.transcribe_file). Wake detector
# now sees None for hallucinated transcripts — same downstream treatment
# as empty speech. Browser STT and future continuous-listen consumers
# inherit the filter for free. Chaos scout 2026-05-07 #1.


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
        # Serializes start/stop_listening — the band had zero locks and an
        # unlocked start-after-timed-out-stop spawned a second reader.
        self._lifecycle_lock = threading.Lock()
        
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

        # Voice privacy gate (vault v1.1): the wake path transcribes into the
        # ACTIVE chat — gated BEFORE recording (don't capture what we refuse
        # to transcribe).
        try:
            from core.voice_privacy import stt_gate_reason
            gate = stt_gate_reason(self.system.llm_chat.session_manager.get_chat_settings())
        except Exception:
            gate = ''
        if gate:
            logger.warning(f"[WAKE] {gate} — utterance not recorded")
            publish(Events.STT_ERROR, {"message": f"Wakeword heard — {gate}"})
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
                # Pick the right TTS message based on the actual failure
                # reason set by the recorder. Saying "File creation error"
                # when the mic was busy or no speech was captured is
                # technically wrong and not actionable for users —
                # particularly on Windows where mic-busy is common after
                # wakeword closes its stream. 2026-04-28.
                reason = getattr(self.system.whisper_recorder, 'last_failure_reason', '') or 'file'
                logger.warning(f"No audio file produced (reason={reason})")
                self.system.speak_error(reason)
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

            # `transcribe_file` now applies the hallucination filter at the
            # provider boundary — None means "no usable speech" (silence,
            # noise, OR a known canned phrase). Single check covers both.
            if not text or not text.strip():
                logger.warning("No speech detected (empty or hallucination)")
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

            # Restart wakeword audio stream after TTS is done (only if still
            # enabled AND this thread is still THE listen thread — a
            # superseded thread re-opening the mic is how two readers end up
            # on one stream, hunt 2026-08-17 K1)
            if (self.audio_recorder and self.running
                    and threading.current_thread() is self.listen_thread):
                logger.debug("Restarting wakeword audio stream after STT/TTS")
                self.audio_recorder.start_recording()

    def _listen_loop(self):
        """Main listening loop - polls OWW for predictions."""
        # OWW works best with 80ms frames (1280 samples at 16kHz)
        frame_samples = 1280
        target_rate = 16000
        consecutive_errors = 0
        max_consecutive = 10  # After 10 rapid errors, back off hard
        # 16kHz-mono accumulator between device reads and OWW. The device may
        # capture at any rate/channel count; OWW only ever sees exact
        # 1280-sample 16k mono frames drained from here. (F4 2026-08-17: the
        # old loop read 1280 RAW device frames — at 48k that's 26.7ms of audio
        # fed as "80ms" = 3x time-stretch, −1.58 octave, detector deaf; and
        # .flatten() on stereo interleaved L/R samples — deaf even at native
        # 16k. The resample path existed but had zero callers.)
        buf16 = np.zeros(0, dtype=np.int16)

        logger.info(f"Listen loop started: frame_samples={frame_samples}, threshold={self.threshold}")

        # Identity check beside the flag: after a toggle OFF (join timed out
        # mid-turn) → ON, `running` is True again — the flag alone would let
        # a SUPERSEDED thread fall back into the loop beside its replacement:
        # two stream.read()ers + two OWW predict()ers on shared state.
        while self.running and threading.current_thread() is self.listen_thread:
            try:
                # Pause processing when disabled at runtime (save CPU)
                if not config.WAKE_WORD_ENABLED:
                    time.sleep(0.5)
                    continue

                stream = self.audio_recorder.get_stream()
                if stream is None:
                    # Stream teardown (STT handoff) — any partial frame is
                    # stale audio from before the handoff.
                    buf16 = np.zeros(0, dtype=np.int16)
                    time.sleep(0.1)
                    continue

                # Read ~80ms of DEVICE frames (recomputed every pass — the
                # error-recovery path can re-resolve to a different rate).
                actual_rate = int(getattr(self.audio_recorder, 'actual_rate', None)
                                  or target_rate)
                read_frames = int(round(frame_samples * actual_rate / target_rate)) \
                    or frame_samples
                audio_data, overflowed = stream.read(read_frames)
                if overflowed:
                    logger.debug("Audio buffer overflow in wake detection")
                chunk = convert_to_mono(np.asarray(audio_data))  # downmix, never interleave
                if actual_rate != target_rate:
                    chunk = resample_audio(chunk, actual_rate, target_rate)
                buf16 = np.concatenate((buf16, chunk))

                # Drain exact 1280-sample frames; score INSIDE the drain so a
                # detection can't hide in a multi-frame backlog.
                while len(buf16) >= frame_samples:
                    frame = buf16[:frame_samples]
                    buf16 = buf16[frame_samples:]

                    predictions = self.model.predict(frame)

                    # Check if wake word detected. OWW keys predictions by file
                    # stem when loaded by path (e.g. 'hey_mycroft_v0.1' for the
                    # bundled v0.1.onnx files) and by bare name when loaded as a
                    # builtin. Try exact match first, then prefix match against
                    # versioned stems, finally fall back to whatever the only
                    # loaded model returned. Single-model detector — safe to
                    # inspect all keys.
                    score = predictions.get(self.model_name, 0)
                    if score < self.threshold and predictions:
                        for k, v in predictions.items():
                            if k.startswith(self.model_name):
                                score = v
                                break
                    if score >= self.threshold:
                        logger.info(f"Wake word '{self.model_name}' detected with score {score:.3f}")
                        # Pre-activation audio is stale by the time the voice
                        # turn ends — start the next listen fresh.
                        buf16 = np.zeros(0, dtype=np.int16)
                        self._on_activation()
                        # Note: _on_activation resets state, minimal cooldown needed
                        time.sleep(0.5)
                        break

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
                        # Teardown may have landed during the backoff — a
                        # re-open here would leave an unowned InputStream
                        # holding the mic with no thread to close it (K3).
                        if not self.running or threading.current_thread() is not self.listen_thread:
                            break
                        self.audio_recorder.start_recording()
                        # Verify recovery actually succeeded — start_recording
                        # swallows exceptions and leaves stream=None on failure.
                        # Without this check, the loop keeps polling a None
                        # stream forever and the user thinks wakeword is up
                        # when it's silently dead. Scout 4 finding (2026-04-19).
                        if self.audio_recorder.get_stream() is None:
                            raise RuntimeError("start_recording returned but stream is None")
                        logger.info("Attempted stream recovery after persistent errors")
                        consecutive_errors = 0
                    except Exception as recovery_err:
                        logger.error(f"Stream recovery failed: {recovery_err}")
                        # Publish a CONTINUITY_TASK_ERROR so the UI surfaces
                        # "wakeword is silently dead." Otherwise the UI toggle
                        # still reads on, but Sapphire can't hear.
                        try:
                            publish(Events.CONTINUITY_TASK_ERROR, {
                                "task": "Wake Word",
                                "error": f"Wake word stream recovery failed ({type(recovery_err).__name__}: "
                                         f"{recovery_err}). Audio input is dead — check mic, restart "
                                         f"Sapphire, or toggle wake word off/on.",
                            })
                        except Exception:
                            pass
                        # Stop the loop rather than spin forever on a dead
                        # stream. UI state will follow once the loop exits.
                        self.running = False
                        break

    def start_listening(self):
        with self._lifecycle_lock:
            return self._start_listening_locked()

    def _start_listening_locked(self):
        if self.running:
            logger.warning("Wake detector already listening — skipping duplicate start")
            return

        # Previous listen thread still mid-turn (stop_listening's 2s join
        # can't outlast a 10-90s voice turn): spawning a second thread here
        # put TWO readers on one InputStream and one OWW model (hunt
        # 2026-08-17 K1 — garbage scores, then double turns). Re-arm the
        # flag instead; the surviving thread resumes when its turn ends.
        if self.listen_thread and self.listen_thread.is_alive():
            self.running = True
            logger.info("Wake listener re-armed — previous listen thread is "
                        "finishing a voice turn and will resume")
            return

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
        with self._lifecycle_lock:
            self._stop_listening_locked()

    def _stop_listening_locked(self):
        self.running = False
        if self.listen_thread:
            self.listen_thread.join(timeout=2.0)
            if self.listen_thread.is_alive():
                logger.warning("Listen thread still mid-turn after 2s join — "
                               "flag cleared; it will exit (or re-arm) when "
                               "the turn finishes")
            else:
                logger.info("Listen thread stopped")
        try:
            sd.stop()  # Stop any playing audio
        except Exception:
            pass
        self.callback_pool.shutdown()