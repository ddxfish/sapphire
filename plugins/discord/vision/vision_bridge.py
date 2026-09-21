"""Vision provider adapter for structured media descriptions."""

from __future__ import annotations

import base64
import io
import json
import logging
import time
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

CAPTION_MAX_CHARS = 600         # M12: caption prose cap before it enters her prompt
FETCH_MAX_BYTES = 10 * 1024 * 1024   # M13: attachment fetch cap

_FETCH_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/124.0 Safari/537.36'
    ),
    'Accept': 'image/avif,image/webp,image/png,image/jpeg,image/gif,video/*;q=0.9,*/*;q=0.8',
}


class VisionBridge:
    def __init__(self, provider_client=None, http_client=None, fetch_bytes=None, trace_recorder=None, debug_logger=None):
        self.provider_client = provider_client
        self.http_client = http_client or self._post_json
        self._raw_fetch = fetch_bytes or self._fetch_bytes
        self.fetch_bytes = self._fetch_cached
        # url -> (bytes, media_type), newest last, 8 deep. The caption lane
        # fetches an attachment OFF the daemon loop (transport to_thread →
        # interpret_media); seconds later the batch's payload lane wanted the
        # same bytes and fetched them AGAIN, synchronously, ON the loop —
        # 3 attempts with sleeps, per attachment (broadsword H5). Now the
        # payload lane reads this cache and never fetches.
        self._bytes_cache: dict = {}
        self.trace_recorder = trace_recorder
        self.debug_logger = debug_logger or logger

    def _fetch_cached(self, source_url: str) -> tuple[bytes, str]:
        key = str(source_url or '').strip()
        hit = self._bytes_cache.get(key)
        if hit is not None:
            return hit
        data, media_type = self._raw_fetch(key)
        self._bytes_cache[key] = (data, media_type)
        while len(self._bytes_cache) > 8:
            self._bytes_cache.pop(next(iter(self._bytes_cache)))
        return data, media_type

    def cached_bytes(self, source_url: str):
        """(bytes, media_type) if the caption lane already fetched this
        attachment, else None. NEVER fetches — safe on the daemon loop."""
        return self._bytes_cache.get(str(source_url or '').strip())

    def describe_media(
        self,
        source_url: str,
        *,
        media_kind: str,
        settings,
        filename: str = '',
        content_type: str = '',
        reply_llm_provider: str = '',
    ) -> dict:
        payload = {
            'source_url': source_url,
            'media_kind': media_kind,
            'llm_provider': getattr(settings, 'vision_llm_provider', 'auto'),
            'llm_model': getattr(settings, 'vision_llm_model', ''),
            'reply_llm_provider': reply_llm_provider,
            'provider': getattr(settings, 'vision_provider', ''),
            'model': getattr(settings, 'vision_model', ''),
            'base_url': getattr(settings, 'vision_base_url', ''),
            'api_key': getattr(settings, 'vision_api_key', ''),
            'timeout_seconds': getattr(settings, 'vision_timeout_seconds', 30),
            'gif_mode': getattr(settings, 'vision_gif_mode', 'first_frame'),
            'debug_enabled': bool(getattr(settings, 'vision_debug_enabled', False)),
            'filename': filename,
            'content_type': content_type,
        }
        try:
            raw = self._describe(payload)
        except Exception as exc:
            return self._fallback(
                source_url,
                filename=filename,
                content_type=content_type,
                error=exc,
            )
        return self._normalize(raw, source='vision')

    def _describe(self, payload: dict) -> dict:
        if hasattr(self.provider_client, 'describe'):
            return self.provider_client.describe(payload)
        if hasattr(self.provider_client, 'describe_image'):
            return self.provider_client.describe_image(payload.get('source_url'))

        # House path first: the vision call rides a Sapphire-registered LLM
        # provider (same credentials as everything else — no 4th key slot).
        # The legacy sidecar endpoint (hidden base_url/api_key settings) stays
        # as a fallback so pre-registry configs keep working.
        house_provider = self._resolve_house_provider(payload)
        base_url = str(payload.get('base_url') or '').strip()
        if house_provider is None and not base_url:
            raise AttributeError('no vision-capable LLM provider configured')

        provider = 'house' if house_provider is not None else self._detect_provider(
            str(payload.get('provider') or ''), base_url)
        self._debug(
            payload,
            'vision_provider_detected',
            'Vision provider detected',
            {
                'provider': provider,
                'base_url': base_url,
                'model': str(payload.get('model') or ''),
                'media_kind': str(payload.get('media_kind') or ''),
            },
            'Vision provider detected: %s model=%s url=%s media_kind=%s',
            provider,
            str(payload.get('model') or ''),
            base_url,
            str(payload.get('media_kind') or ''),
        )
        try:
            image_bytes, media_type = self.fetch_bytes(payload.get('source_url'))
        except Exception as exc:
            self._debug(
                payload,
                'vision_fetch_failed',
                'Vision image fetch failed',
                {
                    'source_url': str(payload.get('source_url') or ''),
                    'error_type': type(exc).__name__,
                    'error_message': str(exc),
                },
                'Vision image fetch failed: url=%s error=%s',
                str(payload.get('source_url') or ''),
                str(exc),
            )
            raise
        image_bytes, media_type = self._prepare_for_vision(
            image_bytes,
            media_type or str(payload.get('content_type') or '') or 'image/png',
            str(payload.get('media_kind') or ''),
            str(payload.get('gif_mode') or 'first_frame'),
        )

        if provider == 'house':
            return self._describe_via_house(house_provider, payload, image_bytes, media_type)
        if provider == 'ollama':
            return self._describe_via_ollama(payload, image_bytes)
        return self._describe_via_openai_compat(payload, image_bytes, media_type)

    def _resolve_house_provider(self, payload: dict):
        """Resolve a Sapphire-registered LLM provider for the vision call.

        Override semantics, not a competing default: an explicit selection is
        used as-is; 'auto' means "daemon chooses" — the same chain her replies
        ride (Reply LLM override if set, else the app's provider fallback
        order). If the daemon's provider can't see images, vision quietly
        degrades to filename descriptions — it never hops to some other
        provider the user didn't pick.
        Returns a provider instance, or None (legacy sidecar / fallback).
        """
        key = str(payload.get('llm_provider') or 'auto').strip().lower()
        model = str(payload.get('llm_model') or '').strip()
        timeout = float(payload.get('timeout_seconds') or 30)
        try:
            from core.chat.llm_providers.resolve import resolve, ProviderRefused
        except Exception:
            return None
        # ONE resolver (2026-09-21): no health probes (this runs per image),
        # sight is a hard requirement on every tier — a blind pick degrades
        # to filename captions, it never hops to a provider the user didn't
        # choose; auto honors the side-lanes local-only setting (M6).
        if key and key != 'auto':
            try:
                return resolve(key, model, timeout=timeout, health='skip', require_images=True).provider
            except ProviderRefused as exc:
                logger.warning('Vision LLM provider %r unavailable: %s', key, exc)
                return None
        reply_key = str(payload.get('reply_llm_provider') or '').strip().lower()
        if reply_key and reply_key != 'auto':
            try:
                return resolve(reply_key, timeout=timeout, health='skip', require_images=True).provider
            except ProviderRefused as exc:
                logger.info('Vision skipped: daemon-chosen provider %r: %s', reply_key, exc)
                return None
        try:
            return resolve('auto', timeout=timeout, health='skip', require_images=True,
                           private=self._local_only()).provider
        except ProviderRefused as exc:
            logger.info('Vision skipped: %s', exc)
            return None

    @staticmethod
    def _local_only() -> bool:
        try:
            from plugins.discord.sapphire.llm_settings import side_lanes_local_only
            return side_lanes_local_only()
        except Exception:
            return True

    @staticmethod
    def _provider_sees(provider) -> bool:
        """supports_images is a @property on BaseProvider — accessing it with
        a () call raised TypeError('bool' is not callable), which the old bare
        except swallowed into 'does not support images'. Vision-auto could
        never succeed for ANY provider. 2026-08-06."""
        try:
            supported = provider.supports_images
            if callable(supported):  # tolerate a plain-method provider impl
                supported = supported()
            return bool(supported)
        except Exception:
            return False

    def _describe_via_house(self, provider, payload: dict, image_bytes: bytes, media_type: str) -> dict:
        encoded = base64.b64encode(image_bytes).decode('ascii')
        messages = [{
            'role': 'user',
            'content': [
                {'type': 'text', 'text': self._prompt_for_media(str(payload.get('media_kind') or 'image'))},
                # Core's internal multimodal block shape — each provider class
                # converts to its own wire format (openai image_url, etc.).
                {'type': 'image', 'data': encoded, 'media_type': media_type or 'image/png'},
            ],
        }]
        self._debug(
            payload,
            'vision_request_started',
            'Vision request started',
            {'provider': 'house', 'model': str(payload.get('llm_model') or 'default'),
             'media_kind': str(payload.get('media_kind') or '')},
            'Vision request started via house provider (model=%s)',
            str(payload.get('llm_model') or 'default'),
        )
        response = provider.chat_completion(
            messages,
            generation_params={'max_tokens': 300, 'temperature': 0.2},
        )
        summary = str(getattr(response, 'content', '') or '').strip()
        if not summary:
            raise ValueError('house vision provider returned an empty description')
        return {'summary': summary}

    def _detect_provider(self, configured: str, base_url: str) -> str:
        configured = configured.strip().lower()
        if configured in {'ollama', 'openai_compat'}:
            return configured
        parsed = urlparse(base_url)
        path = (parsed.path or '').rstrip('/')
        if path.endswith('/v1'):
            return 'openai_compat'
        return 'ollama'

    def _describe_via_openai_compat(self, payload: dict, image_bytes: bytes, media_type: str) -> dict:
        model = str(payload.get('model') or '').strip()
        if not model:
            raise ValueError('vision model is required for openai-compatible vision')
        encoded = base64.b64encode(image_bytes).decode('ascii')
        request_payload = {
            'model': model,
            'messages': [{
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': self._prompt_for_media(str(payload.get('media_kind') or 'image'))},
                    {'type': 'image_url', 'image_url': {'url': f'data:{media_type};base64,{encoded}'}},
                ],
            }],
        }
        url = f"{str(payload.get('base_url') or '').rstrip('/')}/chat/completions"
        raw = self._request_json(payload, url, request_payload, provider='openai_compat')
        choices = raw.get('choices') or []
        message = choices[0].get('message', {}) if choices else {}
        return {'summary': message.get('content', '')}

    def _describe_via_ollama(self, payload: dict, image_bytes: bytes) -> dict:
        model = str(payload.get('model') or '').strip()
        if not model:
            raise ValueError('vision model is required for ollama vision')
        encoded = base64.b64encode(image_bytes).decode('ascii')
        request_payload = {
            'model': model,
            'stream': False,
            'messages': [{
                'role': 'user',
                'content': self._prompt_for_media(str(payload.get('media_kind') or 'image')),
                'images': [encoded],
            }],
        }
        url = f"{str(payload.get('base_url') or '').rstrip('/')}/api/chat"
        raw = self._request_json(payload, url, request_payload, provider='ollama')
        message = raw.get('message') or {}
        return {'summary': message.get('content', '')}

    def _request_json(self, payload: dict, url: str, request_payload: dict, *, provider: str) -> dict:
        timeout = int(payload.get('timeout_seconds') or 30)
        model = str(payload.get('model') or '')
        media_kind = str(payload.get('media_kind') or '')
        self._debug(
            payload,
            'vision_request_started',
            'Vision request started',
            {
                'provider': provider,
                'url': url,
                'model': model,
                'media_kind': media_kind,
                'timeout_seconds': timeout,
            },
            'Vision request started: provider=%s model=%s url=%s media_kind=%s timeout=%s',
            provider,
            model,
            url,
            media_kind,
            timeout,
        )
        try:
            raw = self.http_client(
                url,
                request_payload,
                headers=self._headers(payload),
                timeout=timeout,
            )
        except Exception as exc:
            self._debug(
                payload,
                'vision_request_failed',
                'Vision request failed',
                {
                    'provider': provider,
                    'url': url,
                    'model': model,
                    'media_kind': media_kind,
                    'error_type': type(exc).__name__,
                    'error_message': str(exc),
                },
                'Vision request failed: provider=%s model=%s url=%s error=%s',
                provider,
                model,
                url,
                str(exc),
            )
            raise
        self._debug(
            payload,
            'vision_request_succeeded',
            'Vision request succeeded',
            {
                'provider': provider,
                'url': url,
                'model': model,
                'media_kind': media_kind,
            },
            'Vision request succeeded: provider=%s model=%s url=%s',
            provider,
            model,
            url,
        )
        return raw

    def _headers(self, payload: dict) -> dict:
        headers = {'Content-Type': 'application/json'}
        api_key = str(payload.get('api_key') or '').strip()
        if api_key:
            headers['Authorization'] = f'Bearer {api_key}'
        return headers

    def _prompt_for_media(self, media_kind: str) -> str:
        if media_kind == 'gif':
            return 'Describe this GIF briefly in one or two sentences. Mention the visible action or joke if clear.'
        return 'Describe this image briefly in one or two sentences. Mention any obvious text if present.'

    def _fetch_bytes(self, source_url: str) -> tuple[bytes, str]:
        from core import net

        source_url = str(source_url or '').strip()
        if not source_url:
            raise ValueError('source_url is required')

        last_err = None
        for attempt in range(3):
            try:
                # M13: stream with a hard cap (an attachment is a stranger's
                # bytes) and never follow redirects — Discord's CDN doesn't
                # redirect, and a redirect is how a link reaches a LAN host.
                response = net.get(
                    source_url,
                    headers=_FETCH_HEADERS,
                    timeout=30,
                    stream=True,
                    allow_redirects=False,
                )
                if response.status_code == 200:
                    buf = bytearray()
                    for chunk in response.iter_content(65536):
                        buf += chunk
                        if len(buf) > FETCH_MAX_BYTES:
                            raise ValueError(f'attachment over {FETCH_MAX_BYTES // (1024 * 1024)}MB — not fetched for vision')
                    content = bytes(buf)
                    if not content:
                        last_err = 'empty body'
                        continue
                    media_type, is_video = self._sniff_media_type(content, source_url)
                    if is_video:
                        raise ValueError('video attachments are not supported for vision')
                    if not media_type:
                        media_type = (
                            (response.headers.get('Content-Type') or 'image/png')
                            .split(';')[0]
                            .strip()
                            .lower()
                        )
                    return content, media_type
                last_err = f'HTTP {response.status_code}'
            except ValueError:
                raise
            except Exception as exc:
                last_err = f'{type(exc).__name__}: {exc}'
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))

        raise RuntimeError(f'image fetch failed after 3 attempts: {last_err} for {source_url}')

    def _sniff_media_type(self, data: bytes, url: str = '') -> tuple[str | None, bool]:
        if not data or len(data) < 12:
            return ('image/jpeg', False)
        head = data[:16]
        if head.startswith(b'\xff\xd8\xff'):
            return ('image/jpeg', False)
        if head.startswith(b'\x89PNG\r\n\x1a\n'):
            return ('image/png', False)
        if head.startswith(b'GIF87a') or head.startswith(b'GIF89a'):
            return ('image/gif', False)
        if head.startswith(b'RIFF') and data[8:12] == b'WEBP':
            return ('image/webp', False)
        if head[4:8] == b'ftyp':
            return (None, True)
        if head.startswith(b'\x1a\x45\xdf\xa3'):
            return (None, True)
        url_lower = (url or '').lower()
        if any(url_lower.endswith(ext) for ext in ('.mp4', '.mov', '.webm', '.mkv', '.avi')):
            return (None, True)
        if any(url_lower.endswith(ext) for ext in ('.gif', '.png', '.jpg', '.jpeg', '.webp')):
            return ('image/jpeg', False)
        return ('image/jpeg', False)

    def _prepare_for_vision(
        self,
        image_bytes: bytes,
        media_type: str,
        media_kind: str,
        gif_mode: str,
    ) -> tuple[bytes, str]:
        if media_kind != 'gif' and media_type != 'image/gif':
            return image_bytes, media_type
        if gif_mode != 'first_frame':
            return image_bytes, media_type
        return self._gif_first_frame(image_bytes)

    def _gif_first_frame(self, data: bytes) -> tuple[bytes, str]:
        if data[:6] not in (b'GIF87a', b'GIF89a'):
            return data, 'image/gif'
        try:
            from PIL import Image
        except ImportError:
            logger.warning('Pillow unavailable; sending raw GIF bytes to vision model')
            return data, 'image/gif'

        try:
            image = Image.open(io.BytesIO(data))
            if image.mode in ('RGBA', 'P', 'LA'):
                image = image.convert('RGB')
            buffer = io.BytesIO()
            image.save(buffer, format='PNG')
            return buffer.getvalue(), 'image/png'
        except Exception as exc:
            logger.warning('GIF first-frame conversion failed: %s', exc)
            return data, 'image/gif'

    def _post_json(self, url: str, payload: dict, *, headers: dict, timeout: int) -> dict:
        # core.net: the one egress door (SOCKS/NO_PROXY policy, LAN classify).
        # This POST — image bytes + the API key — rode raw urllib while its
        # fetch twin was moved onto the facade in M13 (row 67).
        from core import net
        response = net.post(url, json=payload, headers=headers, timeout=timeout)
        response.raise_for_status()
        return response.json() or {}

    def _debug(self, payload: dict, trace_type: str, summary: str, detail: dict, log_message: str, *args) -> None:
        if not payload.get('debug_enabled'):
            return
        if self.trace_recorder:
            self.trace_recorder(trace_type, summary, detail)
        if self.debug_logger:
            self.debug_logger.info(log_message, *args)

    def _normalize(self, raw: dict | None, *, source: str) -> dict:
        # M12: a rambling or <think>-wrapped caption rode straight into the
        # reply prompt. Think tags stripped, prose capped.
        from plugins.discord.conversation.think_tags import strip_think_tags

        raw = raw or {}
        summary = raw.get('summary')
        if summary is None:
            summary = raw.get('description')
        return {
            'summary': strip_think_tags(str(summary or '')).strip()[:CAPTION_MAX_CHARS],
            'entities': [str(e) for e in list(raw.get('entities') or [])[:12]],
            'tone': str(raw.get('tone') or '').strip()[:80],
            'ocr_text': strip_think_tags(str(raw.get('ocr_text') or '')).strip()[:CAPTION_MAX_CHARS],
            'confidence': float(raw.get('confidence', 0.5) or 0.5),
            'source': source,
        }

    def _fallback(
        self,
        source_url: str,
        *,
        filename: str = '',
        content_type: str = '',
        error: Exception | None = None,
    ) -> dict:
        label = filename or content_type or source_url
        result = {
            'summary': f'Media attachment: {label}',
            'entities': [],
            'tone': '',
            'ocr_text': '',
            'confidence': 0.2,
            'source': 'fallback',
        }
        if error is not None:
            result['fallback'] = {
                'reason': 'vision_error',
                'error_type': type(error).__name__,
                'error_message': str(error),
            }
        return result
