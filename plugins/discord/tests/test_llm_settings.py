from types import SimpleNamespace

from plugins.discord.models.settings import CognitiveSettings, EffectiveSettings, ProactiveSettings
from plugins.discord.sapphire.llm_settings import (
    cognitive_llm_from_settings,
    llm_event_fields,
    proactive_llm_from_settings,
)


def test_cognitive_llm_defaults():
    settings = EffectiveSettings()
    assert settings.cognitive.llm_primary == 'auto'
    assert settings.cognitive.llm_model == ''


def test_llm_event_fields_auto_is_empty():
    settings = EffectiveSettings()
    assert llm_event_fields(settings) == {}


def test_llm_event_fields_includes_provider_and_model():
    settings = EffectiveSettings()
    settings.cognitive = CognitiveSettings(llm_primary='ollama', llm_model='llama3.2')
    assert llm_event_fields(settings) == {
        'llm_primary': 'ollama',
        'llm_model': 'llama3.2',
    }


def test_llm_event_fields_omits_blank_model():
    settings = EffectiveSettings()
    settings.cognitive = CognitiveSettings(llm_primary='claude')
    assert llm_event_fields(settings) == {'llm_primary': 'claude'}


def test_cognitive_llm_from_settings():
    settings = SimpleNamespace(cognitive=CognitiveSettings(llm_primary='openai', llm_model='gpt-4o'))
    assert cognitive_llm_from_settings(settings) == ('openai', 'gpt-4o')


def test_proactive_llm_inherits_reply_llm():
    settings = EffectiveSettings(
        cognitive=CognitiveSettings(llm_primary='ollama', llm_model='llama3.2'),
        proactive=ProactiveSettings(),
    )
    assert proactive_llm_from_settings(settings, kind='greeting') == ('ollama', 'llama3.2')
    assert proactive_llm_from_settings(settings, kind='goodnight') == ('ollama', 'llama3.2')


def test_proactive_llm_goodnight_inherits_greeting_override():
    settings = EffectiveSettings(
        cognitive=CognitiveSettings(llm_primary='ollama', llm_model='llama3.2'),
        proactive=ProactiveSettings(
            greeting_model_provider='claude',
            greeting_model_name='sonnet',
        ),
    )
    assert proactive_llm_from_settings(settings, kind='greeting') == ('claude', 'sonnet')
    assert proactive_llm_from_settings(settings, kind='goodnight') == ('claude', 'sonnet')
