from types import SimpleNamespace

from plugins.discord.models.settings import CognitiveSettings, EffectiveSettings, ProfileSettings
from plugins.discord.sapphire.llm_settings import (
    cognitive_llm_from_settings,
    distill_llm_from_settings,
    llm_event_fields,
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


def test_distill_llm_inherits_reply_then_override():
    settings = EffectiveSettings(
        cognitive=CognitiveSettings(llm_primary='ollama', llm_model='llama3.2'),
        profile=ProfileSettings(),
    )
    assert distill_llm_from_settings(settings) == ('ollama', 'llama3.2')
    settings.profile = ProfileSettings(
        distill_model_provider='openai',
        distill_model_name='gpt-4o-mini',
    )
    assert distill_llm_from_settings(settings) == ('openai', 'gpt-4o-mini')
