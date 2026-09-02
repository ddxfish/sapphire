"""TTS provider — forwards to Sapphire Router."""

import os
import logging
from typing import Optional

import requests

from core import net

from core.tts.providers.base import BaseTTSProvider

logger = logging.getLogger(__name__)


class SapphireRouterTTSProvider(BaseTTSProvider):
    """Forwards text to a Sapphire Router for speech generation."""

    audio_content_type = 'audio/ogg'
    SPEED_MIN = 0.5
    SPEED_MAX = 2.0

    def _get_url(self):
        import config
        url = os.environ.get('SAPPHIRE_ROUTER_URL') or getattr(config, 'SAPPHIRE_ROUTER_URL', '')
        return url.rstrip('/')

    def _get_tenant_id(self):
        import config
        return os.environ.get('SAPPHIRE_TENANT_ID') or getattr(config, 'SAPPHIRE_ROUTER_TENANT_ID', '')

    def list_voices(self) -> list:
        """Return Kokoro voices available on the router's orchestrator."""
        from core.tts.providers.kokoro import KOKORO_VOICES
        return KOKORO_VOICES

    def generate(self, text: str, voice: str, speed: float, **kwargs) -> Optional[bytes]:
        url = self._get_url()
        if not url:
            return None
        try:
            headers = {'Content-Type': 'application/json'}
            tenant_id = self._get_tenant_id()
            if tenant_id:
                headers['X-Tenant-ID'] = tenant_id
            resp = net.request(
                'POST',
                f'{url}/v1/tts/generate',
                json={'text': text, 'voice': voice, 'speed': speed},
                headers=headers,
                timeout=30.0,
            )
            if 300 <= resp.status_code < 400:
                logger.error(f"Sapphire Router TTS: router redirected ({resp.status_code}) — "
                             f"LAN redirects are refused by policy; check SAPPHIRE_ROUTER_URL "
                             f"for a trailing-slash/path mismatch")
                return None
            resp.raise_for_status()
            if resp.headers.get('content-type', '').startswith('audio/'):
                return resp.content
            logger.error(f"Sapphire Router TTS: unexpected response type")
            return None
        except requests.exceptions.ConnectionError:
            logger.error(f"Sapphire Router TTS: cannot reach router at {url}")
            raise RuntimeError("TTS service unavailable — router is down")
        except Exception as e:
            logger.error(f"Sapphire Router TTS failed: {e}")
            return None

    def is_available(self) -> bool:
        return bool(self._get_url())
