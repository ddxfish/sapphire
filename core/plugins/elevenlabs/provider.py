"""ElevenLabs TTS provider — cloud text-to-speech."""
import os
import logging
from typing import Optional

import httpx
import config

from core.tts.providers.base import BaseTTSProvider

logger = logging.getLogger(__name__)

ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech"

# Default voice: Rachel (premade, clear female voice)
DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"


def _parse_error(response) -> str:
    """Extract human-readable error from ElevenLabs API response."""
    try:
        data = response.json()
        detail = data.get('detail', {})
        if isinstance(detail, dict):
            msg = detail.get('message', '')
            status = detail.get('status', '')
            if 'missing_permission' in status or 'missing_permission' in msg:
                return f"API key missing permissions — check key permissions on ElevenLabs dashboard"
            if msg:
                return msg
        if isinstance(detail, str):
            return detail
    except Exception:
        pass
    return f"HTTP {response.status_code}: {response.text[:200]}"


class ElevenLabsTTSProvider(BaseTTSProvider):
    """Generates audio via the ElevenLabs cloud API."""

    audio_content_type = 'audio/ogg'

    def __init__(self):
        self._last_error = None
        self._validated = None  # None=unchecked, True/False=cached result
        logger.info("ElevenLabs TTS provider initialized")

    @property
    def _api_key(self):
        return self._resolve_api_key()

    @property
    def _model(self):
        try:
            from core.plugin_loader import plugin_loader
            ps = plugin_loader.get_plugin_settings('elevenlabs')
            if ps and ps.get('model', '').strip():
                return ps['model'].strip()
        except Exception:
            pass
        return getattr(config, 'TTS_ELEVENLABS_MODEL', 'eleven_flash_v2_5')

    @property
    def _voice_id(self):
        try:
            from core.plugin_loader import plugin_loader
            ps = plugin_loader.get_plugin_settings('elevenlabs')
            if ps and ps.get('voice_id', '').strip():
                return ps['voice_id'].strip()
        except Exception:
            pass
        return getattr(config, 'TTS_ELEVENLABS_VOICE_ID', '') or DEFAULT_VOICE_ID

    # ElevenLabs speed range (their API rejects anything outside)
    SPEED_MIN = 0.7
    SPEED_MAX = 1.2

    def generate(self, text: str, voice: str, speed: float, **kwargs) -> Optional[bytes]:
        """POST to ElevenLabs streaming endpoint, return OGG/Opus bytes.

        If voice looks like an ElevenLabs voice_id (20+ alphanumeric chars),
        use it directly. Otherwise fall back to the configured default.
        """
        if not self._api_key:
            logger.error("ElevenLabs API key not configured")
            return None

        # Per-chat voice override: use if it looks like an ElevenLabs ID
        from core.tts.utils import is_elevenlabs_voice
        voice_id = voice if is_elevenlabs_voice(voice) else self._voice_id
        url = f"{ELEVENLABS_TTS_URL}/{voice_id}/stream"

        # Clamp speed to ElevenLabs range (Kokoro allows up to 2.0, ElevenLabs only 0.7-1.2)
        clamped_speed = max(self.SPEED_MIN, min(self.SPEED_MAX, speed))
        if clamped_speed != speed:
            logger.warning(f"ElevenLabs: clamped speed {speed} -> {clamped_speed} (range {self.SPEED_MIN}-{self.SPEED_MAX})")

        try:
            with httpx.Client(timeout=60.0) as client:
                response = client.post(url, headers={
                    'xi-api-key': self._api_key,
                    'Content-Type': 'application/json',
                }, params={
                    'output_format': 'opus_48000_192',
                }, json={
                    'text': text,
                    'model_id': self._model,
                    'voice_settings': {
                        'speed': clamped_speed,
                    },
                })

                if response.status_code != 200:
                    logger.error(f"ElevenLabs TTS error: {_parse_error(response)}")
                    return None

                return response.content

        except Exception as e:
            logger.error(f"ElevenLabs generate failed: {e}")
            return None

    def is_available(self) -> bool:
        """Quick check — uses cached result after first validation."""
        if not self._api_key:
            return False
        if self._validated is None:
            self._validated = self._validate_key()
        return self._validated

    def _validate_key(self) -> bool:
        """Validate API key by hitting a lightweight endpoint."""
        try:
            with httpx.Client(timeout=10.0) as client:
                r = client.get("https://api.elevenlabs.io/v1/voices",
                               headers={'xi-api-key': self._api_key}, params={'page_size': 1})
                if r.status_code != 200:
                    err = _parse_error(r)
                    logger.error(f"ElevenLabs availability check failed: {err}")
                    self._last_error = err
                    return False
                self._last_error = None
                return True
        except Exception as e:
            self._last_error = str(e)
            return False

    def list_voices(self) -> list:
        """Fetch available voices from ElevenLabs API."""
        return self.list_voices_with_key(self._api_key)

    @staticmethod
    def list_voices_with_key(api_key: str) -> list:
        """Fetch voices using a specific API key (for pre-save browsing)."""
        if not api_key:
            return []
        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.get("https://api.elevenlabs.io/v1/voices", headers={
                    'xi-api-key': api_key,
                }, params={'page_size': 100})

                if response.status_code != 200:
                    logger.error(f"ElevenLabs voices error: {_parse_error(response)}")
                    return []

                data = response.json()
                return [
                    {
                        'voice_id': v['voice_id'],
                        'name': v['name'],
                        'category': v.get('category', ''),
                        'description': v.get('description', ''),
                    }
                    for v in data.get('voices', [])
                ]
        except Exception as e:
            logger.error(f"ElevenLabs list_voices failed: {e}")
            return []

    def _resolve_api_key(self) -> str:
        """Resolve API key: plugin settings > credentials > config > env var."""
        # Check plugin settings first (saved via Settings UI)
        try:
            from core.plugin_loader import plugin_loader
            ps = plugin_loader.get_plugin_settings('elevenlabs')
            if ps and ps.get('api_key', '').strip():
                return ps['api_key'].strip()
        except Exception:
            pass
        # Credentials manager
        try:
            from core.credentials_manager import credentials
            key = credentials.get_service_api_key('tts_elevenlabs')
            if key:
                return key
        except Exception:
            pass
        # Legacy config
        key = getattr(config, 'TTS_ELEVENLABS_API_KEY', '') or ''
        if key:
            return key
        return os.environ.get('ELEVENLABS_API_KEY', '')
