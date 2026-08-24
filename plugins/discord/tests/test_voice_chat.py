from unittest.mock import MagicMock

from plugins.discord.sapphire.voice_chat import (
    ensure_discord_voice_chat_settings,
    ensure_voice_chat,
    is_kokoro_streaming_voice,
    is_voice_chat_name,
    legacy_voice_chat_name,
    parse_voice_chat_name,
    resolve_voice_chat_name,
    sanitize_chat_name,
    voice_chat_name,
)


def test_voice_chat_name_matches_core_sanitization():
    name = voice_chat_name('1516753077489631314', '1516753078223896600')
    assert name == 'discord_1516753077489631314_1516753078223896600'
    assert name == sanitize_chat_name(f'discord_1516753077489631314_1516753078223896600')


def test_legacy_colon_name_matches_stored_format():
    legacy = legacy_voice_chat_name('1516753077489631314', '1516753078223896600')
    assert legacy == 'discord15167530774896313141516753078223896600'


def test_parse_underscore_voice_chat_name():
    parsed = parse_voice_chat_name('discord_111_222')
    assert parsed == ('111', '222')


def test_parse_legacy_stripped_voice_chat_name():
    parsed = parse_voice_chat_name('discord15167530774896313141516753078223896600')
    assert parsed == ('1516753077489631314', '1516753078223896600')


def test_is_voice_chat_name():
    assert is_voice_chat_name('discord_111_222')
    assert is_voice_chat_name('discord15167530774896313141516753078223896600')
    assert not is_voice_chat_name('testasdfg')
    assert not is_voice_chat_name('phone:call')


def test_resolve_voice_chat_prefers_existing_legacy():
    system = MagicMock()
    sm = MagicMock()
    legacy = legacy_voice_chat_name('111', '222')

    def _read(name):
        if name == legacy:
            return {'prompt': 'default'}
        return None

    sm.read_chat_settings.side_effect = _read
    system.llm_chat.session_manager = sm
    assert resolve_voice_chat_name(system, '111', '222') == legacy
    assert resolve_voice_chat_name(system, '999', '888') == voice_chat_name('999', '888')


def test_is_kokoro_streaming_voice():
    assert is_kokoro_streaming_voice('af_heart')
    assert not is_kokoro_streaming_voice('qwen3:horny2-5d3f46')
    assert not is_kokoro_streaming_voice('')


def test_ensure_discord_voice_chat_settings_fixes_qwen_voice():
    system = MagicMock()
    sm = MagicMock()
    sm.read_chat_settings.return_value = {'tts_voice': 'qwen3:horny2-5d3f46'}
    sm.set_named_chat_settings.return_value = True
    system.llm_chat.session_manager = sm
    system.tts.voice_name = 'qwen3:horny2-5d3f46'
    ensure_discord_voice_chat_settings(system, 'discord_111_222', bot_names=['Remmi'])
    sm.set_named_chat_settings.assert_called_once()
    payload = sm.set_named_chat_settings.call_args[0][1]
    assert payload['tts_voice'] == 'af_heart'
    assert 'custom_context' in payload
    assert 'Remmi' in payload['custom_context']


def test_ensure_voice_chat_skips_create_when_exists():
    system = MagicMock()
    sm = MagicMock()
    name = voice_chat_name('111', '222')
    sm.read_chat_settings.return_value = {'prompt': 'default'}
    system.llm_chat.session_manager = sm
    assert ensure_voice_chat(system, '111', '222') == name
    system.llm_chat.create_chat.assert_not_called()
