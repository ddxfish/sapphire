from plugins.discord.conversation.gif_service import (
    GifService,
    build_gif_reply_hint,
    normalize_gif_query,
    strip_placeholder_gif_urls,
    user_requested_gif,
)
from plugins.discord.conversation.reply_style_service import ReplyStyleService
from plugins.discord.models.settings import EffectiveSettings, MediaSettings, SettingsStore


def _settings(**media_kwargs):
    store = SettingsStore()
    store.global_overlay.media.update(media_kwargs)
    return store.resolve()


def test_strip_placeholder_gif_urls():
    raw = 'Sure!\nhttps://media.example/gif?q=fox+waving+hello\nEnjoy'
    assert 'media.example' not in strip_placeholder_gif_urls(raw)
    assert 'Sure!' in strip_placeholder_gif_urls(raw)


def test_normalize_gif_query_from_placeholder_url():
    assert normalize_gif_query('https://media.example/gif?q=fox+waving+hello') == 'fox waving hello'


def test_maybe_send_gif_requires_enabled_and_api_key():
    service = GifService()
    settings = _settings(gif_enabled=False, gif_api_key='secret')

    class Parsed:
        gif_query = 'party'

    assert service.maybe_send_gif(Parsed(), settings=settings) is None

    settings = _settings(gif_enabled=True, gif_api_key='')
    assert service.maybe_send_gif(Parsed(), settings=settings) is None

    settings = _settings(gif_enabled=True, gif_api_key='secret')
    assert service.maybe_send_gif(Parsed(), settings=settings) == 'party'


def test_search_gif_url_uses_provider(monkeypatch):
    service = GifService()
    settings = _settings(gif_enabled=True, gif_api_key='key', gif_provider='klipy')
    monkeypatch.setattr(
        'plugins.discord.conversation.gif_service.search_gif_url',
        lambda query, api_key, **kwargs: f'https://cdn.test/{query}.gif',
    )

    assert service.search_gif_url('fox wave', settings=settings) == 'https://cdn.test/fox wave.gif'


def test_build_gif_reply_hint_when_configured():
    settings = EffectiveSettings(media=MediaSettings(gif_enabled=True, gif_api_key='abc'))
    hint = build_gif_reply_hint(settings)
    assert '[gif:' in hint
    assert 'media.example' in hint


def test_parse_llm_output_strips_placeholder_url():
    service = ReplyStyleService()
    parsed = service.parse_llm_output('Here you go\nhttps://media.example/gif?q=fox')
    assert parsed.chunks == ['Here you go']
    assert parsed.gif_query == ''


def test_user_requested_gif_detects_ask():
    assert user_requested_gif('can you still send gifs?') is True
    assert user_requested_gif('hello there') is False
