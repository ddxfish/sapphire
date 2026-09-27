"""Describe an image for a brain that cannot see it (2026-09-26).

ONE door for every lane that hands a text-only model an image: tool images
(core/chat/chat_tool_calling.py), daemon-event images
(core/continuity/execution_context.py — Discord, Telegram, any plugin payload)
and, through caption_blind(), the images a user pastes plus the vision
window's replays (core/chat/chat_streaming.py).
Settings › Images › Image describer (IMAGE_DESCRIBE_ENGINE) picks the engine:

    'clip'          core.vibes — CLIP atmospheric read, local, no LLM (default)
    <provider key>  that model line captions the image literally, through the
                    ONE resolver (the turn's privacy honoured — a private turn
                    refuses a provider not marked local). Picking a line here
                    IS the statement that it can see: vision is forced on for
                    this one call. Refused, down, rejected or empty → CLIP, logged.

The caption lives ON THE IMAGE (tool_images.caption): described once, read
back ever after, deleted with the image. Images with no row (perception
frames, history-less lanes) are described without being kept, or left blind.

An animated GIF is cut to its first frame (PNG) before a provider sees it;
anything that is not JPEG / PNG / WEBP is re-encoded to PNG the same way.
"""
from __future__ import annotations

import base64
import logging
from io import BytesIO

logger = logging.getLogger(__name__)

CLIP = 'clip'
MAX_TOKENS = 600          # room for a describer that reasons anyway; the caption itself is short
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
    cfg = providers_config()
    if key in cfg:
        # The vision checkbox describes the entry's everyday use; being picked as
        # the describer is the owner saying this one sees.
        cfg = {**cfg, key: {**cfg[key], 'supports_images': True}}
    try:
        sel = resolve(key, '', private=private, prompt_name=None, timeout=TIMEOUT,
                      health='skip', require_images=True, cfg=cfg)
    except ProviderRefused as e:
        logger.warning(f"[DESCRIBE] {display_name(key)} refused ({e}) — CLIP instead")
        return ''
    data, media_type = prepare(image_bytes)
    messages = [{'role': 'user', 'content': [
        {'type': 'text', 'text': PROMPT},
        {'type': 'image', 'data': base64.b64encode(data).decode('ascii'), 'media_type': media_type},
    ]}]
    params = {**get_generation_params(sel.key, sel.effective_model, cfg), 'max_tokens': MAX_TOKENS,
              'disable_thinking': True}
    try:
        response = sel.provider.chat_completion(messages, None, params)
    except Exception as e:
        logger.warning(f"[DESCRIBE] {sel.display_name} failed ({e}) — CLIP instead")
        return ''
    # A describer that reasons first must not hand its scratch work over as the
    # caption (2026-09-27: GLM Flash's think block rode into the chat): thinking
    # is asked off, stripped if it comes anyway, and a reply that was ALL
    # reasoning counts as nothing.
    from core import think
    text = '' if getattr(response, 'content_is_reasoning', False) is True \
        else think.strip(getattr(response, 'content', '') or '')
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


# ── the caption lives on the image ───────────────────────────────────────────

def _store():
    """The chat image store (session manager), or None outside a running Sapphire."""
    try:
        from core import images as ci
        return ci._session_manager()
    except Exception:
        return None


def for_image(image_id, image_bytes=None, *, private: bool | None = None, store=None) -> str:
    """The description of a STORED image: read from its row, or made once and
    written there. image_bytes may be raw bytes or base64 text; without them the
    store supplies the pixels. A lane with no store just describes."""
    image_id = str(image_id or '').strip()
    if store is None and image_id:
        store = _store()
    read = getattr(store, 'image_caption', None)
    if image_id and callable(read):
        try:
            cached = read(image_id)
        except Exception:
            cached = None
        if isinstance(cached, str) and cached.strip():
            return cached
    raw = image_bytes
    if isinstance(raw, str):
        raw = base64.b64decode(raw) if raw else None
    if not raw and image_id:
        fetch = getattr(store, 'get_tool_image', None)
        got = fetch(image_id) if callable(fetch) else None
        raw = got[0] if got else None
    if not raw:
        return ''
    text = describe(raw, private=private)
    write = getattr(store, 'set_image_caption', None)
    if text and image_id and callable(write):
        try:
            write(image_id, text)
        except Exception as e:
            logger.debug(f"[DESCRIBE] caption for {image_id} not stored: {e}")
    return text


def _text_of(messages) -> str:
    parts = []
    for m in messages:
        c = m.get('content') if isinstance(m, dict) else None
        if isinstance(c, str):
            parts.append(c)
        elif isinstance(c, list):
            parts.extend(str(b.get('text') or '') for b in c if isinstance(b, dict) and b.get('type') == 'text')
    return '\n'.join(parts)


def caption_blind(messages, provider, *, private: bool | None = None, store=None) -> int:
    """A provider with no vision reads a description in place of every STORED
    image on the wire (a block that names its row: block['id']) — this turn's
    pasted images and the vision window's replays alike. A caption the wire
    already carries is not repeated. Blocks with no id (perception frames,
    legacy inline rows) are left alone: the provider says it cannot see them.
    Mutates `messages`; returns how many images were answered in words."""
    from core.chat.llm_providers.resolve import provider_sees
    if provider is None or not messages or provider_sees(provider):
        return 0
    try:
        from core.chat.history import _REPLAY_NOTE
    except Exception:
        _REPLAY_NOTE = ''
    wire, done = None, 0
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        content = msg.get('content') if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        out, touched = [], False
        for block in content:
            is_image = isinstance(block, dict) and block.get('type') == 'image'
            image_id = str(block.get('id') or '') if is_image else ''
            if not image_id:
                out.append(block)
                continue
            try:
                text = for_image(image_id, block.get('data'), private=private, store=store)
            except Exception as e:
                logger.warning(f"[DESCRIBE] image {image_id} not described: {e}")
                text = ''
            if not text:
                out.append(block)
                continue
            touched, done = True, done + 1
            if wire is None:
                wire = _text_of(messages)
            if text not in wire:
                out.append({'type': 'text', 'text': text})
                wire += '\n' + text
        if not touched:
            continue
        if all(isinstance(b, dict) and b.get('type') == 'text' for b in out):
            texts = [str(b.get('text') or '') for b in out if str(b.get('text') or '').strip()]
            if not texts or texts == [_REPLAY_NOTE]:
                del messages[i]          # a replay note with nothing left to introduce
                continue
            msg['content'] = '\n\n'.join(texts)
        else:
            msg['content'] = out
    if done:
        logger.info(f"[DESCRIBE] {done} image(s) answered in words for a model with no vision")
    return done

