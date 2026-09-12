"""Bridge Discord voice to Sapphire core STT/TTS."""

from __future__ import annotations

import logging
import os
import tempfile
import time

from plugins.discord.sapphire.discord_stt_quality import (
    is_likely_decrypt_noise,
    reject_discord_transcript,
    segments_to_transcript,
)
from plugins.discord.transport.discord_audio import pcm_stereo_to_whisper_wav_bytes, prepare_discord_wav_for_stt

logger = logging.getLogger(__name__)


class SapphireSpeechBridge:
    def __init__(self, plugin_loader):
        self.plugin_loader = plugin_loader

    def available(self) -> bool:
        system = self._system()
        if not system:
            return False
        whisper = getattr(system, 'whisper_client', None)
        tts = getattr(system, 'tts', None)
        return bool(
            (whisper and getattr(whisper, 'is_available', lambda: False)())
            or (tts and hasattr(tts, 'generate_audio_data'))
        )

    def transcribe_audio(self, audio_bytes, *, speaker_hint=''):
        system = self._system()
        whisper = getattr(system, 'whisper_client', None) if system else None
        if not whisper or not hasattr(whisper, 'transcribe_file'):
            return {'text': '', 'confidence': 0.0}

        wav_bytes = audio_bytes
        if audio_bytes[:4] != b'RIFF':
            wav_bytes = pcm_stereo_to_whisper_wav_bytes(audio_bytes)

        temp_path = None
        try:
            fd, temp_path = tempfile.mkstemp(suffix='.wav')
            os.close(fd)
            with open(temp_path, 'wb') as handle:
                handle.write(wav_bytes)
            text, confidence = self._transcribe_discord_wav(
                whisper,
                temp_path,
                speaker_hint=str(speaker_hint or '').strip(),
            )
            return {'text': text, 'confidence': confidence}
        except Exception as exc:
            logger.warning('Speech bridge transcription failed: %s', exc)
            return {'text': '', 'confidence': 0.0}
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

    def _transcribe_discord_wav(self, whisper, audio_path: str, *, speaker_hint: str = '') -> tuple[str, float]:
        """Run Discord audio through DAVE quality gates, then the shared STT provider."""
        try:
            import soundfile as sf
            import numpy as np
        except ImportError:
            text = str(whisper.transcribe_file(audio_path) or '').strip()
            rejected, reason = reject_discord_transcript(text)
            if rejected:
                logger.info('Discord voice STT rejected (%s): %r', reason, text[:120])
                return '', 0.0
            return text, (0.75 if text else 0.0)

        audio_data, sample_rate = sf.read(audio_path)
        if len(audio_data.shape) > 1:
            audio_data = audio_data.mean(axis=1)
        duration = len(audio_data) / sample_rate if sample_rate > 0 else 0.0
        rms = float(np.sqrt(np.mean(audio_data ** 2))) if len(audio_data) else 0.0
        peak = float(np.max(np.abs(audio_data))) if len(audio_data) else 0.0

        if is_likely_decrypt_noise(peak=peak, rms=rms, duration=duration):
            logger.info(
                'Discord voice audio likely DAVE decrypt noise (%.2fs rms=%.6f peak=%.6f) — skipping STT',
                duration,
                rms,
                peak,
            )
            return '', 0.0

        if rms < 0.0005:
            logger.info(
                'Discord voice audio near-silent (%.2fs rms=%.6f peak=%.6f) — check DAVE decrypt',
                duration,
                rms,
                peak,
            )
            return '', 0.0

        prepared_path = None
        try:
            prepared_path = prepare_discord_wav_for_stt(audio_path)
            if os.environ.get('DISCORD_VOICE_DEBUG_WAV'):
                debug_dir = os.environ.get('DISCORD_VOICE_DEBUG_WAV') or '/tmp'
                os.makedirs(debug_dir, exist_ok=True)
                import wave

                stamp = int(time.time() * 1000)
                with wave.open(audio_path, 'rb') as wav_file:
                    stt_rate = wav_file.getframerate()
                    stt_channels = wav_file.getnchannels()
                debug_copy = os.path.join(
                    debug_dir,
                    f'discord_stt_{stt_rate}hz_{stt_channels}ch_{stamp}.wav',
                )
                with open(audio_path, 'rb') as src, open(debug_copy, 'wb') as dst:
                    dst.write(src.read())
                prepared_copy = os.path.join(debug_dir, f'discord_prepared_{stamp}.wav')
                with open(prepared_path, 'rb') as src, open(prepared_copy, 'wb') as dst:
                    dst.write(src.read())
                logger.info(
                    'Discord voice debug saved: capture=%s prepared=%s',
                    debug_copy,
                    prepared_copy,
                )

            text, segment_list = self._transcribe_prepared_wav(
                whisper,
                prepared_path,
                duration=duration,
            )
            rejected, reason = reject_discord_transcript(
                text,
                peak=peak,
                duration=duration,
                segment_list=segment_list,
            )
            if rejected and text:
                logger.info('Discord voice STT rejected (%s): %r', reason, text[:200])
                text = ''
        finally:
            if prepared_path and os.path.exists(prepared_path):
                try:
                    os.unlink(prepared_path)
                except OSError:
                    pass

        if text:
            logger.info(
                'Discord voice transcribed (%.2fs rms=%.4f peak=%.4f): %r',
                duration,
                rms,
                peak,
                text[:200],
            )
        else:
            clip_hint = ' (clipped — likely DAVE decrypt noise)' if peak >= 0.98 else ''
            logger.info(
                'Discord voice STT empty (%.2fs rms=%.6f peak=%.6f)%s',
                duration,
                rms,
                peak,
                clip_hint,
            )
        return text, (0.75 if text else 0.0)

    def _transcribe_prepared_wav(
        self,
        whisper,
        prepared_path: str,
        *,
        duration: float,
    ) -> tuple[str, list]:
        """Transcribe preprocessed Discord audio with web-mic quality filters."""
        import config
        from contextlib import nullcontext
        from core.stt.hallucination import is_whisper_hallucination

        model = getattr(whisper, 'model', None)
        lock = getattr(whisper, '_lock', None)
        if model is None:
            return str(whisper.transcribe_file(prepared_path) or '').strip(), []

        params = {
            'language': getattr(config, 'STT_LANGUAGE', None),
            'beam_size': getattr(config, 'FASTER_WHISPER_BEAM_SIZE', 3),
            'vad_filter': False,
            'condition_on_previous_text': False,
            'temperature': 0.0,
        }
        ctx = lock if lock is not None else nullcontext()
        with ctx:
            segments, _info = model.transcribe(prepared_path, **params)
            segment_list = list(segments)
            text, _kept = segments_to_transcript(segment_list)
            if segment_list and not text:
                logger.info(
                    'Discord voice whisper dropped all segments: %s',
                    [
                        (
                            segment.text[:32],
                            round(float(getattr(segment, 'no_speech_prob', 0.0)), 3),
                            round(float(getattr(segment, 'avg_logprob', 0.0)), 3),
                        )
                        for segment in segment_list[:6]
                    ],
                )
        if is_whisper_hallucination(text):
            return '', segment_list
        return text, segment_list

    def synthesize_speech(self, text: str):
        system = self._system()
        tts = getattr(system, 'tts', None) if system else None
        if not tts or not hasattr(tts, 'generate_audio_data'):
            return {'audio_bytes': b'', 'format': 'wav'}
        try:
            audio_bytes = tts.generate_audio_data(str(text or '').strip())
        except Exception as exc:
            logger.warning('Speech bridge synthesis failed: %s', exc)
            audio_bytes = None
        if not audio_bytes:
            return {'audio_bytes': b'', 'format': 'wav'}
        return {'audio_bytes': audio_bytes, 'format': 'wav'}

    def _system(self):
        speech = getattr(self.plugin_loader, 'speech', None)
        if speech and hasattr(speech, 'system'):
            return speech.system
        try:
            from core.api_fastapi import get_system
            return get_system()
        except Exception:
            return None
