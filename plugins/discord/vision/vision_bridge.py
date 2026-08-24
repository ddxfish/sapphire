"""Vision provider adapter for structured media descriptions."""

from __future__ import annotations

import base64
import io
import json
import logging
import time
from urllib.parse import urlparse
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

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
        self.fetch_bytes = fetch_bytes or self._fetch_bytes
        self.trace_recorder = trace_recorder
        self.debug_logger = debug_logger or logger

    def describe_media(
        self,
        source_url: str,
        *,
        media_kind: str,
        settings,
        filename: str = '',
        content_type: str = '',
    ) -> dict:
        payload = {
            'source_url': source_url,
            'media_kind': media_kind,
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
        if not self.provider_client and not str(payload['base_url']).strip():
            return self._fallback(source_url, filename=filename, content_type=content_type)
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

        base_url = str(payload.get('base_url') or '').strip()
        if not base_url:
            raise AttributeError('provider client does not support vision description')

        provider = self._detect_provider(str(payload.get('provider') or ''), base_url)
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

        if provider == 'ollama':
            return self._describe_via_ollama(payload, image_bytes)
        return self._describe_via_openai_compat(payload, image_bytes, media_type)

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
        import requests as req

        source_url = str(source_url or '').strip()
        if not source_url:
            raise ValueError('source_url is required')

        last_err = None
        for attempt in range(3):
            try:
                response = req.get(
                    source_url,
                    headers=_FETCH_HEADERS,
                    timeout=30,
                    allow_redirects=True,
                )
                if response.status_code == 200 and response.content:
                    media_type, is_video = self._sniff_media_type(response.content, source_url)
                    if is_video:
                        raise ValueError('video attachments are not supported for vision')
                    if not media_type:
                        media_type = (
                            (response.headers.get('Content-Type') or 'image/png')
                            .split(';')[0]
                            .strip()
                            .lower()
                        )
                    return response.content, media_type
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
        request = Request(
            url,
            data=json.dumps(payload).encode('utf-8'),
            headers=headers,
            method='POST',
        )
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode('utf-8') or '{}')

    def _debug(self, payload: dict, trace_type: str, summary: str, detail: dict, log_message: str, *args) -> None:
        if not payload.get('debug_enabled'):
            return
        if self.trace_recorder:
            self.trace_recorder(trace_type, summary, detail)
        if self.debug_logger:
            self.debug_logger.info(log_message, *args)

    def _normalize(self, raw: dict | None, *, source: str) -> dict:
        raw = raw or {}
        summary = raw.get('summary')
        if summary is None:
            summary = raw.get('description')
        return {
            'summary': str(summary or '').strip(),
            'entities': list(raw.get('entities') or []),
            'tone': str(raw.get('tone') or '').strip(),
            'ocr_text': str(raw.get('ocr_text') or '').strip(),
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
