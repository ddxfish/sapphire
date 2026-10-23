"""Fireworks AI Whisper STT provider."""
import os
import logging
from typing import Optional

import config
from core.stt.providers.base import BaseSTTProvider

logger = logging.getLogger(__name__)

# Endpoint varies by model
FIREWORKS_ENDPOINTS = {
    'whisper-v3-turbo': 'https://audio-turbo.api.fireworks.ai/v1/audio/transcriptions',
}


class FireworksWhisperProvider(BaseSTTProvider):
    """Fireworks AI cloud Whisper — OpenAI-compatible transcription API."""

    def __init__(self):
        # Validate on init but read fresh from config on each call
        if self._resolve_api_key():
            logger.info(f"Fireworks Whisper ready (model: {self._get_model()})")
        else:
            logger.warning("Fireworks Whisper: no API key — check STT_FIREWORKS_API_KEY or FIREWORKS_API_KEY env")

    @staticmethod
    def _resolve_api_key() -> str:
        """Resolve API key: credentials > config setting > env var."""
        from core.credentials_manager import credentials
        key = credentials.get_service_api_key('stt_fireworks')
        if key:
            return key
        key = getattr(config, 'STT_FIREWORKS_API_KEY', '')
        if key:
            return key
        return os.environ.get('FIREWORKS_API_KEY', '')

    @staticmethod
    def _get_model() -> str:
        return getattr(config, 'STT_FIREWORKS_MODEL', 'whisper-v3-turbo')

    def _transcribe_impl(self, audio_path: str) -> Optional[str]:
        """Transcribe via Fireworks API (multipart POST).

        Hallucination filter is applied by `BaseSTTProvider.transcribe_file`.
        """
        api_key = self._resolve_api_key()
        if not api_key:
            logger.error("Fireworks Whisper: no API key configured")
            return None

        try:
            import httpx
        except ImportError:
            logger.error("httpx not installed — pip install httpx")
            return None

        model = self._get_model()
        endpoint = FIREWORKS_ENDPOINTS.get(model, FIREWORKS_ENDPOINTS['whisper-v3-turbo'])
        language = getattr(config, 'STT_LANGUAGE', 'en')

        try:
            with open(audio_path, 'rb') as f:
                response = httpx.post(
                    endpoint,
                    headers={'Authorization': f'Bearer {api_key}'},
                    files={'file': ('audio.wav', f, 'audio/wav')},
                    data={
                        'model': model,
                        'language': language,
                        'response_format': 'json',
                    },
                    timeout=30.0,
                )
            response.raise_for_status()
            text = response.json().get('text', '').strip()
            if text:
                logger.debug(f"Fireworks transcription ({len(text)} chars)")
            return text

        except Exception as e:
            logger.error(f"Fireworks transcription failed: {e}")
            return None

    def is_available(self) -> bool:
        return bool(self._resolve_api_key())
