import sys
import os
import io
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
    if sys.platform == 'linux':
        shm = '/dev/shm'
        if os.path.exists(shm) and os.access(shm, os.W_OK):
            return shm
    return tempfile.gettempdir()


class TTSClient:
    """TTS orchestrator — text processing, playback, hooks. Delegates audio generation to a provider."""

    def __init__(self, provider=None):
        """Initialize TTS client with an audio generation provider."""
        # Hardcoded fallbacks - chat settings override these on chat load
        self.pitch_shift = 0.98
        self.speed = 1.3
        self.voice_name = "af_heart"
        self.temp_dir = get_temp_dir()

        # Provider handles audio generation (Kokoro, ElevenLabs, etc.)
        if provider is None:
            from core.tts.providers.kokoro import KokoroTTSProvider
            provider = KokoroTTSProvider()
        self._provider = provider
        
        self.lock = threading.Lock()
        self.should_stop = threading.Event()
        self._is_playing = False
        self._generation = 0  # epoch counter — prevents stale threads from playing
        
        # Audio output device setup
        self.output_device = None
        self.output_device_name = None
        self.output_rate = None
        self.audio_available = False
        self._init_output_device()
        
        logger.info(f"TTS client initialized: {self._provider.__class__.__name__}")
        logger.info(f"Voice: {self.voice_name}, Speed: {self.speed}, Pitch: {self.pitch_shift}")
        logger.info(f"Temp directory: {self.temp_dir}")
        
        if self.audio_available:
            logger.info(f"Audio playback: device={self.output_device}, rate={self.output_rate}Hz")
        else:
            logger.warning("Audio playback unavailable - TTS will be silent")
    
    def _init_output_device(self):
        """Find a working output device via DeviceManager (respects AUDIO_OUTPUT_DEVICE setting)."""
        self.audio_available = False
        try:
            from core.audio import get_device_manager
            dm = get_device_manager()
            dev_idx, default_rate, dev_name = dm.find_output_device()
            if dev_idx is None:
                logger.error("No output devices found")
                return

            # Test sample rates on the resolved device
            dev_info = {'name': dev_name, 'default_samplerate': default_rate}
            if self._try_output_device(dev_idx, dev_info):
                self.output_device_name = dev_name
                return

            # If configured device fails, fall back to any working output
            logger.warning(f"Output device '{dev_name}' failed, trying all outputs")
            for dev in dm.get_output_devices():
                info = {'name': dev.name, 'default_samplerate': dev.default_samplerate}
                if self._try_output_device(dev.index, info):
                    self.output_device_name = dev.name
                    return

            logger.error("No compatible output device found")
        except Exception as e:
            logger.error(f"Output device init failed: {e}")

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
        if not voice_name:
            return True  # Keep current voice (default af_heart)
        self.voice_name = voice_name
        logger.info(f"Voice set to: {self.voice_name}")
        # Notify the provider so it can react to a voice change — e.g. pre-fetch a
        # model for a newly-selected voice off the hot path (Piper). Opt-in +
        # isolated: only providers that define on_voice_selected react. 2026-06-16.
        prov = self._provider
        if prov is not None and hasattr(prov, "on_voice_selected"):
            try:
                prov.on_voice_selected(voice_name)
            except Exception as e:
                logger.warning(f"provider on_voice_selected failed: {e}")
        return True
    
    def set_speed(self, speed):
        """Set the speech speed, clamped to provider's valid range."""
        speed = float(speed)
        lo, hi = self._provider.SPEED_MIN, self._provider.SPEED_MAX
        if speed < lo or speed > hi:
            clamped = max(lo, min(hi, speed))
            logger.warning(f"Speed {speed} outside range [{lo}-{hi}], clamped to {clamped}")
            speed = clamped
        self.speed = speed
        logger.info(f"Speed set to: {self.speed}")
        return True
    
    def set_pitch(self, pitch):
        """Set the pitch shift"""
        self.pitch_shift = float(pitch)
        logger.info(f"Pitch set to: {self.pitch_shift}")
        return True

    @property
    def provider(self):
        """The active TTS provider instance."""
        return self._provider

    @property
    def audio_content_type(self):
        """Content type of audio produced by the current provider."""
        return self._provider.audio_content_type

    def _process_text_for_tts(self, text):
        """Strip markdown/tags and normalize text for speech."""
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
        processed_text = re.sub(r'\*\*([^*]+)\*\*', r'. \1. ', processed_text)
        processed_text = re.sub(r'__([^_]+)__', r'. \1. ', processed_text)
        processed_text = re.sub(r'(?<!\w)\*([^*]+)\*(?!\w)', r', \1, ', processed_text)
        processed_text = re.sub(r'(?<!\w)_([^_]+)_(?!\w)', r', \1, ', processed_text)
        processed_text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', processed_text)
        processed_text = re.sub(r'^#+\s*(.+)$', r'\1.', processed_text, flags=re.MULTILINE)
        processed_text = re.sub(r'^[\-\*]\s+(.+)$', r'\1.', processed_text, flags=re.MULTILINE)
        processed_text = re.sub(r'^\d+[\.\)]\s+(.+)$', r'\1.', processed_text, flags=re.MULTILINE)
        processed_text = re.sub(r'[*_#]', '', processed_text)
        processed_text = re.sub(r'\n{2,}', '. ', processed_text)
        processed_text = re.sub(r'\n', '. ', processed_text)

        ui_words = ['Copy', 'Copied!', 'Failed', 'Loading...', '...']
        for word in ui_words:
            processed_text = processed_text.replace(word, '')

        processed_text = re.sub(r'[.!?,]+\s*[.!?,]+', '. ', processed_text)
        processed_text = re.sub(r'\s+', ' ', processed_text).strip()
        return processed_text

    def speak(self, text):
        """Send text to TTS server and play audio (non-blocking)."""
        if not self.audio_available:
            logger.warning("Audio playback unavailable - skipping TTS")
            return False

        processed_text = self._process_text_for_tts(text)
        if not processed_text or len(processed_text) < 3:
            logger.warning(f"[TTS] speak: too short after processing ({len(processed_text) if processed_text else 0} chars), skipping")
            return False

        # pre_tts hook — plugins can alter or cancel TTS
        from core.hooks import hook_runner, HookEvent
        if hook_runner.has_handlers("pre_tts"):
            tts_event = HookEvent(tts_text=processed_text, config=config,
                                  metadata={'tts_client': self})
            hook_runner.fire("pre_tts", tts_event)
            if tts_event.skip_tts:
                return False
            processed_text = tts_event.tts_text

        self.stop()
        self._generation += 1
        gen = self._generation
        self.should_stop.clear()

        # Route to streaming variant when the provider supports it AND
        # the user has opted in via TTS_STREAMING_ENABLED. Falls back to
        # the existing blob path transparently otherwise. 2026-05-17.
        target = self._select_play_target()
        threading.Thread(
            target=target,
            args=(processed_text, gen),
            daemon=True
        ).start()

        return True

    def _select_play_target(self):
        """Pick streaming vs blob playback path. Streaming only when the
        provider declares supports_streaming AND the setting is enabled AND
        the provider self-reports available. Without the is_available check
        a dead Kokoro subprocess accepted a 200 OK queue, returned no audio,
        no error toast — user thinks Sapphire is mute. 2026-05-20."""
        if not getattr(config, 'TTS_STREAMING_ENABLED', False):
            return self._generate_and_play_audio
        if not getattr(self._provider, 'supports_streaming', False):
            return self._generate_and_play_audio
        # is_available() can be expensive (subprocess probe) — only call
        # when the streaming path would otherwise be selected. Fallback to
        # blob path is graceful: blob fetch will surface its own errors
        # cleanly via _fetch_audio's logging.
        try:
            if not self._provider.is_available():
                logger.warning(
                    "TTS streaming requested but provider is_available()=False; "
                    "falling back to blob path"
                )
                return self._generate_and_play_audio
        except Exception as e:
            logger.warning(f"provider is_available() raised: {e!r}; falling back to blob path")
            return self._generate_and_play_audio
        return self._generate_and_play_audio_stream

    def speak_sync(self, text):
        """Send text to TTS server, play audio, and block until playback finishes."""
        if not self.audio_available:
            logger.warning("Audio playback unavailable - skipping TTS")
            return False

        processed_text = self._process_text_for_tts(text)
        if not processed_text or len(processed_text) < 3:
            logger.warning(f"[TTS] speak_sync: too short after processing ({len(processed_text) if processed_text else 0} chars), skipping")
            return False

        # pre_tts hook — plugins can alter or cancel TTS
        from core.hooks import hook_runner, HookEvent
        if hook_runner.has_handlers("pre_tts"):
            tts_event = HookEvent(tts_text=processed_text, config=config,
                                  metadata={'tts_client': self})
            hook_runner.fire("pre_tts", tts_event)
            if tts_event.skip_tts:
                return False
            processed_text = tts_event.tts_text

        logger.debug(f"[TTS] speak_sync: {len(text)} chars raw -> {len(processed_text)} chars processed")
        self.stop()
        self.should_stop.clear()

        # Run synchronously on calling thread — no daemon, no race.
        # Same streaming-vs-blob dispatch as speak().
        target = self._select_play_target()
        target(processed_text)
        return True
        
    def _apply_pitch_shift(self, audio_data, samplerate, pitch=None):
        """Apply pitch shifting to audio data in memory"""
        pitch = pitch if pitch is not None else self.pitch_shift
        if pitch == 1.0:
            return audio_data, samplerate

        try:
            # Convert to mono if stereo for pitch processing
            if len(audio_data.shape) > 1:
                mono_data = audio_data.mean(axis=1)
            else:
                mono_data = audio_data

            # Resample to shift pitch
            original_length = len(mono_data)
            new_length = int(original_length / pitch)
            indices = np.linspace(0, original_length - 1, new_length)
            shifted_data = np.interp(indices, np.arange(original_length), mono_data)
            
            return shifted_data.astype(audio_data.dtype), samplerate
            
        except Exception as e:
            logger.error(f"Error applying pitch shift: {e}")
            return audio_data, samplerate

    def _fetch_audio(self, text):
        """Fetch audio from provider. Returns (audio_data, samplerate) or (None, None)."""
        temp_path = None
        try:
            kw = {"pitch": self.pitch_shift} if getattr(self._provider, "supports_pitch", False) else {}
            audio_bytes = self._provider.generate(text, self.voice_name, self.speed, **kw)
            if not audio_bytes:
                return None, None

            # Save to temp file for soundfile to read
            ext_map = {'audio/mp3': '.mp3', 'audio/mpeg': '.mp3', 'audio/wav': '.wav', 'audio/ogg': '.ogg'}
            ext = ext_map.get(self._provider.audio_content_type, '.ogg')
            fd, temp_path = tempfile.mkstemp(suffix=ext, dir=self.temp_dir)
            os.close(fd)

            with open(temp_path, 'wb') as f:
                f.write(audio_bytes)

            if self.should_stop.is_set():
                return None, None

            # Load audio data
            audio_data, samplerate = sf.read(temp_path)

            # Legacy client-side pitch — ONLY for providers that can't shift at
            # synthesis time; supports_pitch providers already applied it
            # server-side (double-shift guard, 2026-08-08).
            if self.pitch_shift != 1.0 and not getattr(self._provider, "supports_pitch", False):
                audio_data, samplerate = self._apply_pitch_shift(audio_data, samplerate)

            return audio_data, samplerate

        except Exception as e:
            logger.error(f"Error fetching audio: {e}")
            return None, None
        finally:
            if temp_path and os.path.exists(temp_path):
                for _attempt in range(3):
                    try:
                        os.unlink(temp_path)
                        break
                    except PermissionError:
                        time.sleep(0.1)
                    except Exception:
                        break
        
    def _generate_and_play_audio(self, text, gen=None):
        """Generate audio from server and play it using sounddevice OutputStream"""
        if not self.audio_available:
            return

        def _stale():
            return gen is not None and gen != self._generation

        try:
            # Mark busy BEFORE fetch so wait() blocks across the whole
            # generate-and-play span. Pre-fix, _is_playing only became True
            # after fetch returned non-None — if fetch failed (Kokoro down,
            # 5xx, etc), _is_playing stayed False and any concurrent wait()
            # returned instantly. Wakeword's finally then re-opened the
            # InputStream while OutputStream might still be tearing down →
            # PortAudio device contention → 10 strikes → wakeword silently
            # dies. Chaos scout 2026-05-07 #2. The finally below clears
            # _is_playing on every exit path, including early-return.
            with self.lock:
                if self.should_stop.is_set() or _stale():
                    return
                self._is_playing = True

            audio_data, samplerate = self._fetch_audio(text)
            if _stale():
                logger.debug(f"[TTS] Stale generation {gen} (current {self._generation}), discarding")
                return
            if audio_data is None or self.should_stop.is_set():
                if audio_data is None and not self.should_stop.is_set():
                    logger.warning("[TTS] speak_sync: provider returned no audio (check provider logs)")
                else:
                    logger.debug(f"[TTS] Fetch stopped={self.should_stop.is_set()}")
                return

            # _is_playing was already set True before fetch. TTS_PLAYING fires
            # here so the UI signals real playback start, not the fetch span.
            with self.lock:
                if self.should_stop.is_set() or _stale():
                    return
                publish(Events.TTS_PLAYING, {"surface": "local"})

            # Convert stereo to mono if needed
            if len(audio_data.shape) > 1:
                audio_data = audio_data.mean(axis=1)

            # Resample to output device rate if different
            if samplerate != self.output_rate:
                logger.debug(f"Resampling audio from {samplerate}Hz to {self.output_rate}Hz")
                audio_data = self._resample(audio_data, samplerate, self.output_rate)
                samplerate = self.output_rate

            # Ensure float32 for sounddevice
            audio_data = audio_data.astype(np.float32)
            duration = len(audio_data) / samplerate
            logger.debug(f"[TTS] Playing {duration:.1f}s audio ({len(audio_data)} samples @ {samplerate}Hz) on device {self.output_device}")

            # Use OutputStream directly — avoids sd.play() global state
            # that can be stomped by other sd.play()/sd.stop() calls
            chunk_dur = 0.1  # 100ms chunks for interruptibility
            chunk_size = int(samplerate * chunk_dur)
            chunks_written = 0
            stopped_early = False

            def _play_stream():
                nonlocal chunks_written, stopped_early
                with sd.OutputStream(samplerate=samplerate, device=self.output_device,
                                     channels=1, dtype='float32') as stream:
                    for i in range(0, len(audio_data), chunk_size):
                        if self.should_stop.is_set() or _stale():
                            stopped_early = True
                            break
                        chunk = audio_data[i:i + chunk_size].reshape(-1, 1)
                        stream.write(chunk)
                        chunks_written += 1

            try:
                _play_stream()
            except sd.PortAudioError as pa_err:
                logger.warning(f"[TTS] Output device {self.output_device} failed: {pa_err} — re-probing")
                self._init_output_device()
                if self.audio_available:
                    if samplerate != self.output_rate:
                        audio_data = self._resample(audio_data, samplerate, self.output_rate)
                        samplerate = self.output_rate
                        chunk_size = int(samplerate * chunk_dur)
                    _play_stream()
                else:
                    raise

            if stopped_early:
                logger.info(f"[TTS] Stopped early at {chunks_written * chunk_dur:.1f}s / {duration:.1f}s")
            else:
                logger.debug(f"[TTS] Playback complete: {duration:.1f}s")

            # post_tts hook — plugins can react to completed/stopped playback
            from core.hooks import hook_runner, HookEvent
            if hook_runner.has_handlers("post_tts"):
                hook_runner.fire("post_tts", HookEvent(
                    tts_text=text, config=config,
                    metadata={"duration": duration, "stopped_early": stopped_early}
                ))

        except Exception as e:
            logger.error(f"Error in TTS playback: {e}", exc_info=True)
        finally:
            with self.lock:
                was_playing = self._is_playing
                self._is_playing = False
            if was_playing:
                publish(Events.TTS_STOPPED, {"surface": "local"})
            gc.collect()

    def _generate_and_play_audio_stream(self, text, gen=None):
        """Streaming variant — pulls bytes from `provider.generate_stream()`,
        decodes each chunk as an independent OGG, plays in order through a
        single long-lived OutputStream. First chunk plays while later chunks
        are still being generated server-side.

        Same lifecycle as _generate_and_play_audio (lock, _is_playing,
        TTS_PLAYING/TTS_STOPPED, post_tts hook, stop responsiveness). Falls
        back transparently if the provider's generate_stream() yields just
        one blob (base-class default).
        """
        if not self.audio_available:
            return

        def _stale():
            return gen is not None and gen != self._generation

        chunks_played = 0
        duration_total = 0.0
        stopped_early = False
        output_stream = None
        playing_started = False
        first_chunk_at = None
        t_start = time.time()

        try:
            with self.lock:
                if self.should_stop.is_set() or _stale():
                    return
                self._is_playing = True

            chunk_iter = self._provider.generate_stream(text, self.voice_name, self.speed)

            for chunk_bytes in chunk_iter:
                if self.should_stop.is_set() or _stale():
                    stopped_early = True
                    break
                if not chunk_bytes:
                    continue

                try:
                    audio_data, samplerate = sf.read(io.BytesIO(chunk_bytes))
                except Exception as e:
                    logger.warning(f"[TTS-stream] decode failed: {e}")
                    continue

                if len(audio_data.shape) > 1:
                    audio_data = audio_data.mean(axis=1)
                if samplerate != self.output_rate:
                    audio_data = self._resample(audio_data, samplerate, self.output_rate)
                    samplerate = self.output_rate
                audio_data = audio_data.astype(np.float32)
                duration_total += len(audio_data) / samplerate

                # Open OutputStream lazily on first decodable chunk so we
                # know the sample rate. Single stream across all chunks =
                # no gaps between sentences.
                # WASAPI on Win can refuse the first chunk's native rate
                # if the device endpoint is configured to a different rate
                # (Sound Control Panel) or in exclusive mode. Fall back to
                # the boot-validated self.output_rate and resample subsequent
                # chunks instead of silently failing the whole turn.
                # 2026-05-18 herring-table #19.
                if output_stream is None:
                    try:
                        output_stream = sd.OutputStream(
                            samplerate=samplerate, device=self.output_device,
                            channels=1, dtype='float32',
                        )
                        output_stream.start()
                    except sd.PortAudioError as pa_err:
                        logger.warning(
                            f"[TTS-stream] OutputStream open failed at {samplerate}Hz "
                            f"({pa_err}); falling back to {self.output_rate}Hz"
                        )
                        try:
                            output_stream = sd.OutputStream(
                                samplerate=self.output_rate, device=self.output_device,
                                channels=1, dtype='float32',
                            )
                            output_stream.start()
                            # Resample THIS chunk now; subsequent chunks at this
                            # samplerate will hit the same resample branch above.
                            audio_data = self._resample(audio_data, samplerate, self.output_rate)
                            samplerate = self.output_rate
                        except Exception as fallback_err:
                            logger.error(
                                f"[TTS-stream] Fallback OutputStream open also failed: "
                                f"{fallback_err}; aborting streaming playback"
                            )
                            stopped_early = True
                            break
                    with self.lock:
                        if self.should_stop.is_set() or _stale():
                            stopped_early = True
                            break
                        publish(Events.TTS_PLAYING, {"surface": "local"})
                        playing_started = True
                    first_chunk_at = time.time() - t_start
                    logger.debug(f"[TTS-stream] first chunk playing at +{first_chunk_at*1000:.0f}ms")

                # 100ms slices for interruptibility — matches the non-stream path
                slice_size = int(samplerate * 0.1)
                for i in range(0, len(audio_data), slice_size):
                    if self.should_stop.is_set() or _stale():
                        stopped_early = True
                        break
                    slice_audio = audio_data[i:i + slice_size].reshape(-1, 1)
                    try:
                        output_stream.write(slice_audio)
                    except sd.PortAudioError as pa_err:
                        logger.warning(f"[TTS-stream] output device error mid-stream: {pa_err}")
                        stopped_early = True
                        break

                chunks_played += 1
                if stopped_early:
                    break

            if output_stream is not None:
                try:
                    output_stream.stop()
                    output_stream.close()
                except Exception as e:
                    logger.debug(f"[TTS-stream] output close error: {e}")

            if stopped_early:
                logger.info(f"[TTS-stream] Stopped early after {chunks_played} chunks ({duration_total:.1f}s)")
            else:
                logger.debug(f"[TTS-stream] Complete: {chunks_played} chunks, {duration_total:.1f}s")

            from core.hooks import hook_runner, HookEvent
            if hook_runner.has_handlers("post_tts"):
                hook_runner.fire("post_tts", HookEvent(
                    tts_text=text, config=config,
                    metadata={
                        "duration": duration_total,
                        "stopped_early": stopped_early,
                        "chunks": chunks_played,
                        "first_chunk_ms": int(first_chunk_at * 1000) if first_chunk_at else None,
                        "streaming": True,
                    },
                ))
        except Exception as e:
            logger.error(f"Error in streaming TTS playback: {e}", exc_info=True)
        finally:
            with self.lock:
                was_playing = self._is_playing
                self._is_playing = False
            if was_playing and playing_started:
                publish(Events.TTS_STOPPED, {"surface": "local"})
            gc.collect()

    def stop(self):
        """Stop currently playing audio"""
        self.should_stop.set()
        was_playing = False
        with self.lock:
            if self._is_playing:
                try:
                    sd.stop()
                except Exception:
                    pass
                self._is_playing = False
                was_playing = True
        if was_playing:
            publish(Events.TTS_STOPPED, {"surface": "local"})

    def wait(self, timeout=300):
        """Block until TTS playback finishes or timeout (seconds)."""
        import time as _time
        deadline = _time.monotonic() + timeout
        while self._is_playing and _time.monotonic() < deadline:
            _time.sleep(0.1)
        return not self._is_playing

    def generate_audio_data(self, text, voice=None, speed=None, pitch=None):
        """Generate audio and return raw bytes for file download.

        Optional voice/speed/pitch override without mutating shared state (used by preview).
        """
        use_voice = voice or self.voice_name
        use_speed = speed if speed is not None else self.speed
        use_pitch = pitch if pitch is not None else self.pitch_shift
        temp_path = None
        try:
            provider_pitch = getattr(self._provider, "supports_pitch", False)
            kw = {"pitch": use_pitch} if provider_pitch else {}
            audio_bytes = self._provider.generate(text, use_voice, use_speed, **kw)
            if not audio_bytes:
                return None

            # Legacy decode → shift → re-encode — only when the provider
            # couldn't apply pitch at synthesis time (double-shift guard).
            if use_pitch != 1.0 and not provider_pitch:
                ext_map = {'audio/mp3': '.mp3', 'audio/mpeg': '.mp3', 'audio/wav': '.wav', 'audio/ogg': '.ogg'}
                ext = ext_map.get(self._provider.audio_content_type, '.ogg')
                fd, temp_path = tempfile.mkstemp(suffix=ext, dir=self.temp_dir)
                os.close(fd)

                with open(temp_path, 'wb') as f:
                    f.write(audio_bytes)

                audio_data, samplerate = sf.read(temp_path)
                audio_data, samplerate = self._apply_pitch_shift(audio_data, samplerate, pitch=use_pitch)
                # Re-encode as OGG regardless of input format (consistent output)
                sf.write(temp_path, audio_data, samplerate, format='OGG', subtype='OPUS')

                with open(temp_path, 'rb') as f:
                    return f.read()

            return audio_bytes

        except Exception as e:
            logger.error(f"Error generating audio data: {e}")
            return None
        finally:
            if temp_path and os.path.exists(temp_path):
                for _attempt in range(3):
                    try:
                        os.unlink(temp_path)
                        break
                    except PermissionError:
                        time.sleep(0.1)
                    except Exception:
                        break