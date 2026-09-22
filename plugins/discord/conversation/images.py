"""Inbound images ride the reply payload — the one image lane (S5, 2026-09-22).

No captions, no vision side lane: the daemon task's own model sees the bytes
(core's plugin-event image contract) when it has vision and ignores them when
it does not. The transport fetches OFF the gateway loop right after a message
is adapted (to_thread → fetch); process_batch, ON the loop, only reads the
cache (broadsword H5: a fetch there froze every account). Images only, 10 MB
each, no redirects.
"""
from __future__ import annotations

import base64
import logging
from collections import OrderedDict

logger = logging.getLogger(__name__)

IMAGE_TYPES = {'image/png', 'image/jpeg', 'image/webp', 'image/gif'}
IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.webp', '.gif')
FETCH_MAX_BYTES = 10 * 1024 * 1024
CACHE_SIZE = 16
DEFAULT_MAX_IMAGES = 4
_FETCH_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
    'Accept': 'image/avif,image/webp,image/png,image/jpeg,image/gif,*/*;q=0.8',
}


def image_urls(attachments) -> list[str]:
    """The image attachments of a message, by content type or extension."""
    out = []
    for item in attachments or []:
        url = str(item.get('url') or '').strip()
        content_type = str(item.get('content_type') or '').lower().split(';')[0].strip()
        name = str(item.get('filename') or '').lower()
        if url and (content_type in IMAGE_TYPES or name.endswith(IMAGE_EXTS)):
            out.append(url)
    return out


def _lane_settings(settings) -> tuple[bool, int]:
    media = getattr(settings, 'media', None) if settings else None
    enabled = bool(getattr(media, 'images_in_enabled', False))
    try:
        limit = int(getattr(media, 'max_images', DEFAULT_MAX_IMAGES) or DEFAULT_MAX_IMAGES)
    except (TypeError, ValueError):
        limit = DEFAULT_MAX_IMAGES
    return enabled, max(1, min(10, limit))


def _type_from_url(url: str) -> str:
    lowered = url.lower().split('?')[0]
    if lowered.endswith(('.jpg', '.jpeg')):
        return 'image/jpeg'
    if lowered.endswith('.webp'):
        return 'image/webp'
    if lowered.endswith('.gif'):
        return 'image/gif'
    return 'image/png'


def fetch_bytes(url: str) -> tuple[bytes, str]:
    """One attachment, streamed with a hard cap and no redirects (a stranger's
    bytes; a redirect is how a link reaches a LAN host). core.net = the one egress door."""
    from core import net

    response = net.get(url, headers=_FETCH_HEADERS, timeout=30, stream=True, allow_redirects=False)
    if response.status_code != 200:
        raise RuntimeError(f'HTTP {response.status_code}')
    buf = bytearray()
    for chunk in response.iter_content(65536):
        buf += chunk
        if len(buf) > FETCH_MAX_BYTES:
            raise ValueError(f'attachment over {FETCH_MAX_BYTES // (1024 * 1024)}MB — not fetched')
    if not buf:
        raise RuntimeError('empty body')
    media_type = str(response.headers.get('Content-Type') or '').split(';')[0].strip().lower()
    return bytes(buf), media_type if media_type in IMAGE_TYPES else _type_from_url(url)


class ImageLane:
    def __init__(self, *, fetch=None):
        self._fetch = fetch or fetch_bytes
        self._cache: OrderedDict[str, tuple[bytes, str]] = OrderedDict()

    def fetch(self, observation, settings) -> int:
        """OFF the loop: pull this message's images into the cache. Returns how many are cached."""
        enabled, limit = _lane_settings(settings)
        if not enabled:
            return 0
        cached = 0
        for url in image_urls(getattr(observation, 'attachments', None))[:limit]:
            if url not in self._cache:
                try:
                    self._cache[url] = self._fetch(url)
                except Exception as exc:
                    logger.info('[DISCORD] image not fetched (%s): %s', url[:80], exc)
                    continue
                while len(self._cache) > CACHE_SIZE:
                    self._cache.popitem(last=False)
            cached += 1
        return cached

    def cached(self, url: str):
        """(bytes, media_type) or None. NEVER fetches — safe on the daemon loop."""
        return self._cache.get(str(url or '').strip())

    def payload_images(self, trigger, settings) -> list[dict]:
        """ON the loop: the cached bytes for this message, base64, capped by max_images."""
        enabled, limit = _lane_settings(settings)
        if not enabled:
            return []
        out: list[dict] = []
        for url in image_urls(getattr(trigger, 'attachments', None)):
            hit = self._cache.get(url)
            if not hit or not hit[0]:
                continue
            data, media_type = hit
            out.append({'data': base64.b64encode(data).decode('ascii'), 'media_type': str(media_type or 'image/png')})
            if len(out) >= limit:
                break
        return out
