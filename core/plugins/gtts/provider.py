# core/plugins/gtts/provider.py — Google Translate TTS provider
#
# Zero config, zero API key. Uses Google Translate's TTS endpoint.
# Quality is basic but it works out of the box.

import io
import logging
from core.tts.providers.base import BaseTTSProvider

logger = logging.getLogger(__name__)


class GoogleTranslateTTSProvider(BaseTTSProvider):
    """Free TTS via Google Translate. No API key needed."""

    audio_content_type = 'audio/mp3'
    SPEED_MIN = 0.5
    SPEED_MAX = 1.0  # gTTS only supports normal and slow

    def generate(self, text, voice=None, speed=1.0, **kwargs):
        """Generate MP3 audio from text."""
        if not text or not text.strip():
            return None
        try:
            from gtts import gTTS
            slow = speed < 0.75
            tts = gTTS(text=text, lang='en', slow=slow)
            buf = io.BytesIO()
            tts.write_to_fp(buf)
            buf.seek(0)
            return buf.read()
        except Exception as e:
            logger.error(f"[gTTS] Generation failed: {e}")
            return None

    def is_available(self):
        try:
            from gtts import gTTS
            return True
        except ImportError:
            return False
