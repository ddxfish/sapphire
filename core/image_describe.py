"""Describe an image for a brain that cannot see it (2026-09-26).

ONE door for every lane that hands a text-only model an image: chat tool
images (core/chat/chat_tool_calling.py) and daemon-event images
(core/continuity/execution_context.py — Discord, Telegram, any plugin payload).
Settings › Images › Image describer (IMAGE_DESCRIBE_ENGINE) picks the engine:

    'clip'          core.vibes — CLIP atmospheric read, local, no LLM (default)
    <provider key>  that vision-capable provider captions the image literally,
                    through the ONE resolver (require_images; the turn's privacy
                    honoured — a private turn refuses a provider not marked
                    local). Blind, refused, down or empty → CLIP, logged.

An animated GIF is cut to its first frame (PNG) before a provider sees it;
anything that is not JPEG / PNG / WEBP is re-encoded to PNG the same way.
"""
from __future__ import annotations

import base64
import logging
from io import BytesIO

logger = logging.getLogger(__name__)

CLIP = 'clip'
MAX_TOKENS = 300
TIMEOUT = 60.0
PROMPT = ("Describe this image for someone who cannot see it: what it shows, any text in it, "
          "the setting and the mood. Two to four sentences, plain and literal, no preamble.")
_PASS_THROUGH = {'JPEG': 'image/jpeg', 'PNG': 'image/png', 'WEBP': 'image/webp'}


def engine() -> str:
    """The configured engine: 'clip' or a provider key. Read per call (hot setting)."""
    try:
        from core.settings_manager import settings
        return str(settings.get('IMAGE_DESCRIBE_ENGINE', CLIP) or CLIP).strip() or CLIP
    except Exception:
        return CLIP


def _turn_is_private() -> bool:
    """The running turn's privacy (core scope contextvar). Unknown = private —
    a non-local describer is then refused rather than guessed at."""
    try:
        from core.chat.function_manager import scope_private
        return bool(scope_private.get())
    except Exception:
        return True


def prepare(image_bytes: bytes) -> tuple[bytes, str]:
    """(bytes, media_type) a vision API accepts: JPEG / PNG / WEBP pass through,
    an animated GIF becomes its first frame as PNG, anything else PNG too."""
    from PIL import Image
    im = Image.open(BytesIO(image_bytes))
    fmt = str(im.format or '').upper()
    if fmt in _PASS_THROUGH and not getattr(im, 'is_animated', False):
        return image_bytes, _PASS_THROUGH[fmt]
    im.seek(0)
    out = BytesIO()
    im.convert('RGBA').save(out, format='PNG')
    return out.getvalue(), 'image/png'


def _clip(image_bytes: bytes) -> str:
    from core import vibes
    return vibes.describe(image_bytes)


def _vlm(key: str, image_bytes: bytes, private: bool) -> str:
    """The provider's literal caption, or '' when it cannot (every reason logged)."""
    from core.chat.llm_providers import get_generation_params
    from core.chat.llm_providers.resolve import ProviderRefused, display_name, providers_config, resolve
    try:
        sel = resolve(key, '', private=private, prompt_name=None, timeout=TIMEOUT,
                      health='skip', require_images=True)
    except ProviderRefused as e:
        logger.warning(f"[DESCRIBE] {display_name(key)} refused ({e}) — CLIP instead")
        return ''
    data, media_type = prepare(image_bytes)
    messages = [{'role': 'user', 'content': [
        {'type': 'text', 'text': PROMPT},
        {'type': 'image', 'data': base64.b64encode(data).decode('ascii'), 'media_type': media_type},
    ]}]
    params = {**get_generation_params(sel.key, sel.effective_model, providers_config()), 'max_tokens': MAX_TOKENS}
    try:
        response = sel.provider.chat_completion(messages, None, params)
    except Exception as e:
        logger.warning(f"[DESCRIBE] {sel.display_name} failed ({e}) — CLIP instead")
        return ''
    text = str(getattr(response, 'content', '') or '').strip()
    if not text:
        logger.warning(f"[DESCRIBE] {sel.display_name} returned nothing — CLIP instead")
        return ''
    return f"Image (described by {sel.display_name}; the replying model could not see it): {text}"


def describe(image_bytes: bytes, *, private: bool | None = None) -> str:
    """One string ready to ride a tool result or a user turn. Falls back to
    CLIP on every failure of a provider engine; the caller wraps CLIP itself."""
    key = engine()
    if key != CLIP:
        try:
            text = _vlm(key, image_bytes, _turn_is_private() if private is None else bool(private))
        except Exception as e:
            logger.warning(f"[DESCRIBE] engine {key!r} blew up ({e}) — CLIP instead", exc_info=True)
            text = ''
        if text:
            return text
    return _clip(image_bytes)
