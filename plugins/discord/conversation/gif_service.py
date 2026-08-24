"""GIF search, cooldown, and outbound sending."""

from __future__ import annotations

import random
import re
import time
from urllib.parse import unquote_plus

from plugins.discord.conversation.gif_search import search_gif_url

_PLACEHOLDER_GIF_URL_RE = re.compile(
    r'https?://media\.example/gif\?q=([^\s\]]+)',
    re.IGNORECASE,
)
_GIF_REQUEST_RE = re.compile(
    r'\b(gifs?|giphy|tenor|klipy|meme\s*gif|send\s+a\s+gif|respond\s+with\s+gifs?)\b',
    re.IGNORECASE,
)


def strip_placeholder_gif_urls(text: str) -> str:
    """Remove stub/placeholder GIF URLs from model output."""
    text = _PLACEHOLDER_GIF_URL_RE.sub('', text or '')
    lines = [line for line in text.splitlines() if line.strip() != 'https://media.example/gif']
    return '\n'.join(lines).strip()


def normalize_gif_query(query: str) -> str:
    """Turn a placeholder URL or raw text into a search query."""
    query = (query or '').strip()
    if not query:
        return ''
    match = _PLACEHOLDER_GIF_URL_RE.search(query)
    if match:
        return unquote_plus(match.group(1).replace('+', ' ')).strip()
    if query.startswith('http') and 'media.example' in query:
        return ''
    return query


def build_gif_reply_hint(settings) -> str:
    media = getattr(settings, 'media', None) if settings else None
    if not media or not media.gif_enabled:
        return ''
    if not (media.gif_api_key or '').strip():
        return ''
    return (
        'GIF replies are enabled. To send a GIF, put `[gif:search terms]` on its own line '
        '(stripped before sending; a real GIF is searched and posted after your text). '
        'You can also call `discord_send_gif` with a search query. '
        'Do NOT paste fake or example GIF URLs (e.g. media.example) — they will not embed.'
    )


def user_requested_gif(user_text: str) -> bool:
    return bool(_GIF_REQUEST_RE.search(user_text or ''))


class GifService:
    def __init__(self, *, trace_repository=None):
        self.trace_repository = trace_repository
        self._last_sent_at: dict[tuple[str, str], float] = {}

    def gif_allowed(self, settings) -> bool:
        media = getattr(settings, 'media', None) if settings else None
        if not media or not media.gif_enabled:
            return False
        return bool((media.gif_api_key or '').strip())

    def resolve_query(self, parsed_reply) -> str:
        return normalize_gif_query((getattr(parsed_reply, 'gif_query', '') or '').strip())

    def maybe_send_gif(self, parsed_reply, *, account_name: str = '', channel_id: str = '', settings=None) -> str | None:
        del account_name, channel_id
        query = self.resolve_query(parsed_reply)
        if not query or not self.gif_allowed(settings):
            return None
        return query

    def search_gif_url(self, query: str, *, settings=None) -> str | None:
        query = normalize_gif_query(query)
        if not query or not self.gif_allowed(settings):
            return None
        media = settings.media
        url = search_gif_url(
            query,
            media.gif_api_key,
            provider=media.gif_provider or 'klipy',
            content_filter=media.gif_content_filter or 'medium',
        )
        if url and self.trace_repository:
            self.trace_repository.record_trace('gif_search', 'Resolved GIF URL', {
                'query': query,
                'provider': media.gif_provider or 'klipy',
            })
        return url or None

    def should_auto_gif(self, account_name: str, channel_id: str, settings) -> bool:
        if not self.gif_allowed(settings):
            return False
        chance = float(settings.media.gif_auto_chance or 0.0)
        if chance <= 0:
            return False
        cooldown = max(0, int(settings.media.gif_cooldown_seconds))
        key = (account_name, channel_id)
        now = time.time()
        if cooldown and now - self._last_sent_at.get(key, 0) < cooldown:
            return False
        return random.random() < chance

    def mark_sent(self, account_name: str, channel_id: str) -> None:
        self._last_sent_at[(account_name, channel_id)] = time.time()
