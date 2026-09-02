"""STT provider — forwards to Sapphire Router."""

import os
import logging
from typing import Optional

import requests

from core import net

from core.stt.providers.base import BaseSTTProvider

logger = logging.getLogger(__name__)


class SapphireRouterSTTProvider(BaseSTTProvider):
    """Forwards audio to a Sapphire Router for transcription."""

    def _get_url(self):
        import config
        url = os.environ.get('SAPPHIRE_ROUTER_URL') or getattr(config, 'SAPPHIRE_ROUTER_URL', '')
        return url.rstrip('/')

    def _get_tenant_id(self):
        import config
        return os.environ.get('SAPPHIRE_TENANT_ID') or getattr(config, 'SAPPHIRE_ROUTER_TENANT_ID', '')

    def _transcribe_impl(self, audio_path: str) -> Optional[str]:
        url = self._get_url()
        if not url:
            return None
        try:
            headers = {}
            tenant_id = self._get_tenant_id()
            if tenant_id:
                headers['X-Tenant-ID'] = tenant_id
            with open(audio_path, 'rb') as f:
                resp = net.request(
                    'POST',
                    f'{url}/v1/stt/transcribe',
                    files={'file': ('audio.wav', f, 'audio/wav')},
                    headers=headers,
                    timeout=30.0,
                )
            if 300 <= resp.status_code < 400:
                logger.error(f"Sapphire Router STT: router redirected ({resp.status_code}) — "
                             f"LAN redirects are refused by policy; check SAPPHIRE_ROUTER_URL "
                             f"for a trailing-slash/path mismatch")
                return None
            resp.raise_for_status()
            return resp.json().get('text', '').strip() or None
        except requests.exceptions.ConnectionError:
            logger.error(f"Sapphire Router STT: cannot reach router at {url}")
            raise RuntimeError("STT service unavailable — router is down")
        except Exception as e:
            logger.error(f"Sapphire Router STT failed: {e}")
            return None

    def is_available(self) -> bool:
        return bool(self._get_url())
