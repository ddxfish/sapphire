"""Discord voice sink that emits per-user utterances after silence."""

from __future__ import annotations

import logging
import os
import time
from collections import defaultdict

from plugins.discord.transport.discord_audio import (
    DISCORD_CHANNELS,
    DISCORD_SAMPLE_RATE,
    DISCORD_SAMPLE_WIDTH,
    LOW_ENERGY_DISCARD_PEAK,
    PREROLL_SECONDS,
    SATURATED_PCM_PEAK,
    SPEECH_RMS_THRESHOLD,
    SPEECH_CONTINUE_RATIO,
    SPEECH_WEAK_FLOOR,
    pcm_stereo_has_repetitive_glitch,
    pcm_stereo_mostly_silent,
    pcm_stereo_normalized_peak,
    pcm_stereo_peak,
    pcm_stereo_rms,
    pcm_stereo_to_wav_bytes,
    pcm_stereo_to_whisper_wav_bytes,
)
from plugins.discord.voice.dave_voice_patches import is_ssrc_decrypt_ready, note_ssrc_packet
from plugins.discord.voice.voice_workers import VOICE_WORKER_POOL

logger = logging.getLogger(__name__)

try:
    from discord.sinks import Sink as DiscordSink
except ImportError:
    DiscordSink = object


def _min_pcm_bytes(min_duration_seconds: float) -> int:
    return int(DISCORD_SAMPLE_RATE * DISCORD_SAMPLE_WIDTH * DISCORD_CHANNELS * min_duration_seconds)


def _preroll_pcm_bytes() -> int:
    return _min_pcm_bytes(PREROLL_SECONDS)


def _extract_pcm_and_user(data, user):
    """Normalize py-cord 2.7+ VoiceData vs legacy raw bytes."""
    if hasattr(data, 'pcm'):
        pcm = bytes(data.pcm or b'')
        speaker = getattr(data, 'source', None) or user
        packet = getattr(data, 'packet', None)
        ssrc = getattr(packet, 'ssrc', None) if packet is not None else None
        return pcm, speaker, ssrc
    if isinstance(data, (bytes, bytearray)):
        return bytes(data), user, None
    return b'', user, None


if DiscordSink is not object:

    class UtteranceVoiceSink(DiscordSink):
        """Decode Opus to PCM and finalize utterances after a silence gap."""

        __sink_listeners__: list[tuple[str, str]] = []

        def __init__(
            self,
            *,
            on_utterance,
            loop=None,
            silence_seconds: float = 0.9,
            min_duration_seconds: float = 0.35,
            filters=None,
            speech_rms_threshold: float = SPEECH_RMS_THRESHOLD,
            on_pcm_frame=None,
        ):
            super().__init__(filters=filters)
            self.on_utterance = on_utterance
            self.on_pcm_frame = on_pcm_frame
            self.loop = loop
            self.silence_seconds = max(0.3, float(silence_seconds))
            self.min_duration_seconds = max(0.1, float(min_duration_seconds))
            self.speech_rms_threshold = max(50.0, float(speech_rms_threshold))
            self._buffers = defaultdict(bytearray)
            self._preroll = defaultdict(bytearray)
            self._carry = defaultdict(bytearray)
            self._last_voice = {}
            self._pending = {}
            self._user_names = {}
            self._in_speech = {}
            self._finalize_deferred = {}
            self._voice_client = None
            self._last_passthrough_refresh = 0.0
            self._debug_last_log = 0.0
            self._debug_max_rms = 0.0
            self._debug_frames = 0
            self._active_ssrc = {}

        def is_opus(self) -> bool:
            """Receive decoded PCM frames from py-cord's voice reader."""
            return False

        def walk_children(self):
            """py-cord 2.8 SinkEventRouter expects this on custom sinks."""
            return iter(())

        def _reset_capture_history(self, user_id: int) -> None:
            """Clear preroll after an utterance so the next one cannot replay it."""
            self._preroll[user_id] = bytearray()

        def _canonical_ssrc(self, user_id: int) -> int | None:
            voice_client = self._voice_client or getattr(self, 'vc', None)
            if voice_client is not None:
                mapped = getattr(voice_client, '_id_to_ssrc', {}).get(user_id)
                if mapped is not None:
                    return int(mapped)
            return self._active_ssrc.get(user_id)

        def _accept_ssrc(self, user_id: int, ssrc: int | None) -> bool:
            if ssrc is None:
                return True
            canonical = self._canonical_ssrc(user_id)
            if canonical is None:
                self._active_ssrc[user_id] = int(ssrc)
                return True
            if int(ssrc) != int(canonical):
                return False
            if self._active_ssrc.get(user_id) != canonical:
                logger.info('Voice SSRC aligned for user %s: %s', user_id, canonical)
                self._buffers[user_id].clear()
                self._reset_capture_history(user_id)
                self._in_speech.pop(user_id, None)
                self._carry.pop(user_id, None)
                self._active_ssrc[user_id] = canonical
            return True

        def _update_preroll(self, user_id: int, pcm: bytes) -> None:
            preroll = self._preroll[user_id]
            preroll.extend(pcm)
            max_bytes = _preroll_pcm_bytes()
            if len(preroll) > max_bytes:
                del preroll[:len(preroll) - max_bytes]

        def _prepend_history(self, user_id: int, buffer: bytearray, *, current_pcm: bytes = b'') -> None:
            carry = self._carry.pop(user_id, None)
            if carry:
                buffer.extend(carry)
            preroll = self._preroll.get(user_id)
            if not preroll:
                return
            preroll_bytes = bytes(preroll)
            if (
                current_pcm
                and len(current_pcm) <= len(preroll_bytes)
                and preroll_bytes.endswith(current_pcm)
            ):
                preroll_bytes = preroll_bytes[:-len(current_pcm)]
            if preroll_bytes:
                buffer.extend(preroll_bytes)

        def init(self, voice_client):
            self._voice_client = voice_client
            if hasattr(super(), 'init'):
                super().init(voice_client)

        def _refresh_dave_passthrough(self) -> None:
            voice_client = self._voice_client or getattr(self, 'vc', None)
            if not voice_client:
                return
            now = time.monotonic()
            if now - self._last_passthrough_refresh < 10.0:
                return
            try:
                from plugins.discord.voice.dave_session import enable_dave_passthrough_mode
                if enable_dave_passthrough_mode(voice_client):
                    self._last_passthrough_refresh = now
            except Exception:
                logger.debug('DAVE passthrough refresh failed', exc_info=True)

        def write(self, data, user):
            self._refresh_dave_passthrough()
            pcm, speaker, ssrc = _extract_pcm_and_user(data, user)
            user_id = int(getattr(speaker, 'id', 0) or 0)
            if speaker and user_id:
                self._user_names[user_id] = (
                    getattr(speaker, 'display_name', '')
                    or getattr(speaker, 'name', '')
                    or str(user_id)
                )
            if not pcm:
                return
            if pcm_stereo_peak(pcm) >= SATURATED_PCM_PEAK:
                return
            if user_id and not self._accept_ssrc(user_id, ssrc):
                return
            if ssrc is not None:
                note_ssrc_packet(int(ssrc))
                if not is_ssrc_decrypt_ready(int(ssrc)):
                    self._update_preroll(user_id, pcm)
                    return

            rms = pcm_stereo_rms(pcm)
            if os.environ.get('DISCORD_VOICE_DEBUG_WAV'):
                self._debug_frames += 1
                self._debug_max_rms = max(self._debug_max_rms, rms)
                now = time.monotonic()
                if now - self._debug_last_log >= 15.0:
                    logger.info(
                        'Voice receive stats: frames=%s max_rms=%.1f threshold=%.1f in_speech=%s',
                        self._debug_frames,
                        self._debug_max_rms,
                        self.speech_rms_threshold,
                        bool(self._in_speech.get(user_id)),
                    )
                    self._debug_last_log = now
                    self._debug_frames = 0
                    self._debug_max_rms = 0.0
            continuing = self._in_speech.get(user_id)
            threshold = self.speech_rms_threshold * (
                SPEECH_CONTINUE_RATIO if continuing else 1.0
            )
            is_speech = rms >= threshold
            weak_speech = rms >= SPEECH_WEAK_FLOOR
            self._update_preroll(user_id, pcm)

            if is_speech or (continuing and weak_speech):
                buffer = self._buffers[user_id]
                if not buffer and not continuing:
                    self._prepend_history(user_id, buffer, current_pcm=pcm)
                self._in_speech[user_id] = True
                buffer.extend(pcm)
                self._last_voice[user_id] = time.monotonic()
                if self.on_pcm_frame:
                    try:
                        frame_is_speech = bool(is_speech or (continuing and weak_speech))
                        self.on_pcm_frame(user_id, pcm, rms, frame_is_speech)
                    except TypeError:
                        try:
                            self.on_pcm_frame(user_id, pcm, rms)
                        except Exception:
                            logger.debug('on_pcm_frame callback failed', exc_info=True)
                    except Exception:
                        logger.debug('on_pcm_frame callback failed', exc_info=True)
                self._schedule_finalize(user_id)
                return

            if not continuing:
                if self.on_pcm_frame and weak_speech:
                    try:
                        self.on_pcm_frame(user_id, pcm, rms, bool(weak_speech))
                    except TypeError:
                        try:
                            self.on_pcm_frame(user_id, pcm, rms)
                        except Exception:
                            logger.debug('on_pcm_frame callback failed', exc_info=True)
                    except Exception:
                        logger.debug('on_pcm_frame callback failed', exc_info=True)
                return

            self._buffers[user_id].extend(pcm)
            if self.on_pcm_frame:
                try:
                    frame_is_speech = bool(is_speech or weak_speech)
                    self.on_pcm_frame(user_id, pcm, rms, frame_is_speech)
                except TypeError:
                    try:
                        self.on_pcm_frame(user_id, pcm, rms)
                    except Exception:
                        logger.debug('on_pcm_frame callback failed', exc_info=True)
                except Exception:
                    logger.debug('on_pcm_frame callback failed', exc_info=True)
            if weak_speech:
                self._last_voice[user_id] = time.monotonic() - self.silence_seconds * 0.15

        def _schedule_finalize(self, user_id: int) -> None:
            if self.loop is None:
                return
            pending = self._pending.get(user_id)
            if pending is not None and hasattr(pending, 'cancel'):
                pending.cancel()
            self._pending[user_id] = self.loop.call_later(
                self.silence_seconds,
                lambda uid=user_id: self._finalize_user(uid),
            )

        def _finalize_user(self, user_id: int) -> None:
            last = self._last_voice.get(user_id, 0.0)
            if time.monotonic() - last < self.silence_seconds * 0.85:
                return
            pcm = bytes(self._buffers.pop(user_id, b''))
            self._pending.pop(user_id, None)
            if not pcm:
                return
            duration_s = len(pcm) / (
                DISCORD_SAMPLE_RATE * DISCORD_SAMPLE_WIDTH * DISCORD_CHANNELS
            )
            speaker_name = self._user_names.get(user_id, str(user_id))
            min_bytes = _min_pcm_bytes(self.min_duration_seconds)
            if len(pcm) < min_bytes:
                self._carry[user_id].extend(pcm)
                self._reset_capture_history(user_id)
                logger.info(
                    'Voice clip too short for %s (%.2fs) — carrying into next utterance',
                    speaker_name,
                    duration_s,
                )
                return
            if duration_s < 1.8 and not self._finalize_deferred.get(user_id):
                self._buffers[user_id] = bytearray(pcm)
                self._in_speech[user_id] = True
                self._finalize_deferred[user_id] = True
                if self.loop is not None:
                    self.loop.call_later(
                        1.0,
                        lambda uid=user_id: self._finalize_user(uid),
                    )
                logger.debug(
                    'Voice clip short for %s (%.2fs) — deferring finalize',
                    speaker_name,
                    duration_s,
                )
                return
            self._in_speech[user_id] = False
            self._finalize_deferred.pop(user_id, None)
            VOICE_WORKER_POOL.submit(
                self._emit_finalized_utterance,
                user_id,
                speaker_name,
                pcm,
                duration_s,
            )

        def _emit_finalized_utterance(
            self,
            user_id: int,
            speaker_name: str,
            pcm: bytes,
            duration_s: float,
        ) -> None:
            if pcm_stereo_mostly_silent(pcm):
                self._reset_capture_history(user_id)
                logger.info(
                    'Voice clip mostly silent for %s (%.2fs) — discarding',
                    speaker_name,
                    duration_s,
                )
                return
            norm_peak = pcm_stereo_normalized_peak(pcm)
            if norm_peak < LOW_ENERGY_DISCARD_PEAK and duration_s < 1.2:
                self._reset_capture_history(user_id)
                logger.info(
                    'Voice clip low energy for %s (%.2fs peak=%.3f) — discarding',
                    speaker_name,
                    duration_s,
                    norm_peak,
                )
                return
            if pcm_stereo_has_repetitive_glitch(pcm):
                self._reset_capture_history(user_id)
                logger.info(
                    'Voice clip repetitive glitch for %s (%.2fs) — discarding',
                    speaker_name,
                    duration_s,
                )
                return
            debug_dir = os.environ.get('DISCORD_VOICE_DEBUG_WAV')
            if debug_dir:
                os.makedirs(debug_dir, exist_ok=True)
                stamp = int(time.time() * 1000)
                capture_path = os.path.join(debug_dir, f'discord_capture_48kstereo_{stamp}.wav')
                with open(capture_path, 'wb') as handle:
                    handle.write(pcm_stereo_to_wav_bytes(pcm))
                logger.info('Discord voice debug capture saved: %s', capture_path)
            wav_bytes = pcm_stereo_to_whisper_wav_bytes(pcm)
            logger.info(
                'Voice utterance finalized for %s (%.2fs, %s bytes pcm)',
                speaker_name,
                duration_s,
                len(pcm),
            )
            try:
                self.on_utterance(user_id, speaker_name, wav_bytes)
            except Exception:
                logger.exception('Voice utterance callback failed for user %s', user_id)
            else:
                self._reset_capture_history(user_id)

        def cleanup(self):
            self.finished = True
            for pending in self._pending.values():
                if hasattr(pending, 'cancel'):
                    pending.cancel()
            self._pending.clear()
            self._buffers.clear()
            self._carry.clear()
            self._preroll.clear()
            self._in_speech.clear()
            self._finalize_deferred.clear()
            self._active_ssrc.clear()

else:


    class UtteranceVoiceSink:
        def __init__(self, **kwargs):
            del kwargs
            raise RuntimeError(
                'discord.sinks is unavailable — see plugins/discord/voice/voice_deps.py INSTALL_HINT'
            )
