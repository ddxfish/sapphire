from plugins.discord.models.settings import EffectiveSettings, ProactiveSettings
from plugins.discord.proactive.proactive_llm import (
    normalize_proactive_output,
    proactive_llm_gen_params,
    salvage_proactive_from_thinking,
)
from plugins.discord.proactive.proactive_message_service import ProactiveMessageService


def test_proactive_llm_gen_params_disable_thinking():
    params = proactive_llm_gen_params({'model': 'minimax-m3'}, max_tokens=180)
    assert params['max_tokens'] == 180
    assert params['disable_thinking'] is True
    assert params['extra_body']['chat_template_kwargs']['enable_thinking'] is False


def test_salvage_proactive_from_thinking_extracts_quoted_text():
    raw = 'Some reasoning here "Hey everyone, hope you slept well and have a lovely day!"'
    assert salvage_proactive_from_thinking(raw) == 'Hey everyone, hope you slept well and have a lovely day!'


def test_normalize_proactive_output_strips_self_greeting():
    fields = {'bot_display_name': 'Remmi', 'bot_username': 'remmi'}
    text = normalize_proactive_output('Morning, Remmi — hope you all slept well!', fields)
    assert 'Remmi' not in text
    assert text.startswith('Morning')


def test_build_greeting_uses_fallback_when_llm_disabled():
    service = ProactiveMessageService()
    settings = EffectiveSettings(
        proactive=ProactiveSettings(
            greeting_use_llm=False,
            greeting_fallback='Static morning!',
            greeting_message='Custom template',
        )
    )
    assert service.build_greeting('alpha', 'c1', settings) == 'Custom template'


def test_build_greeting_uses_fallback_when_llm_disabled_without_instructions():
    service = ProactiveMessageService()
    settings = EffectiveSettings(
        proactive=ProactiveSettings(
            greeting_use_llm=False,
            greeting_fallback='Static morning!',
        )
    )
    assert service.build_greeting('alpha', 'c1', settings) == 'Static morning!'


def test_build_goodnight_uses_fallback_when_llm_unavailable(monkeypatch):
    service = ProactiveMessageService()
    settings = EffectiveSettings(
        proactive=ProactiveSettings(
            goodnight_use_llm=True,
            goodnight_fallback='Night night!',
        )
    )
    monkeypatch.setattr(service, '_get_system', lambda: None)
    assert service.build_goodnight('alpha', 'c1', settings) == 'Night night!'


def test_build_greeting_calls_llm_when_enabled(monkeypatch):
    service = ProactiveMessageService()
    settings = EffectiveSettings(
        proactive=ProactiveSettings(
            greeting_use_llm=True,
            greeting_fallback='Fallback',
        )
    )
    monkeypatch.setattr(service, '_get_system', lambda: object())
    monkeypatch.setattr(
        'plugins.discord.proactive.proactive_message_service.generate_greeting',
        lambda *args, **kwargs: 'Fresh morning from the LLM',
    )
    assert service.build_greeting('alpha', 'c1', settings) == 'Fresh morning from the LLM'


def test_recent_chat_formats_message_history():
    from unittest.mock import MagicMock

    repo = MagicMock()
    repo.get_recent_messages.return_value = [
        {'author_name': 'alice', 'content': 'morning', 'author_id': '1'},
    ]
    service = ProactiveMessageService(message_repository=repo)
    lines = service._recent_chat('alpha', 'c1')
    repo.get_recent_messages.assert_called_once_with('alpha', 'c1', limit=20)
    assert isinstance(lines, list)
