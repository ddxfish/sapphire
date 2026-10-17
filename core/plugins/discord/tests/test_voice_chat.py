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


def _fake_scope_keys(monkeypatch, keys):
    import sys
    import types

    fm_mod = types.ModuleType('core.chat.function_manager')
    fm_mod.scope_setting_keys = lambda: list(keys)
    for pkg in ('core', 'core.chat'):
        if pkg not in sys.modules:
            monkeypatch.setitem(sys.modules, pkg, types.ModuleType(pkg.split('.')[-1]))
    monkeypatch.setitem(sys.modules, 'core.chat.function_manager', fm_mod)


def test_ensure_discord_voice_chat_settings_isolates_the_outside_line(monkeypatch):
    # C1 (hunt 2026-09-12): a fresh VC chat inherited the owner's Mind scopes and
    # default toolset. Now: toolset 'none', every Mind scope = the chat's own
    # name, the bot-account selector untouched, and a marker so it runs once.
    _fake_scope_keys(monkeypatch, ['memory_scope', 'knowledge_scope', 'goals_scope', 'discord_scope'])
    system = MagicMock()
    sm = MagicMock()
    sm.read_chat_settings.return_value = {}
    sm.set_named_chat_settings.return_value = True
    system.llm_chat.session_manager = sm
    system.tts.voice_name = 'af_heart'
    ensure_discord_voice_chat_settings(system, 'discord_111_222')
    payload = sm.set_named_chat_settings.call_args[0][1]
    assert payload['toolset'] == 'none'
    assert payload['memory_scope'] == 'discord_111_222'
    assert payload['knowledge_scope'] == 'discord_111_222'
    assert payload['goals_scope'] == 'discord_111_222'
    assert 'discord_scope' not in payload
    assert payload['discord_voice_isolated'] is True


def test_ensure_discord_voice_chat_settings_respects_owner_opt_in(monkeypatch):
    # Once stamped, the owner may hand a VC chat memory or tools from the
    # sidebar; the next join must not claw it back.
    _fake_scope_keys(monkeypatch, ['memory_scope', 'discord_scope'])
    system = MagicMock()
    sm = MagicMock()
    sm.read_chat_settings.return_value = {
        'discord_voice_isolated': True,
        'toolset': 'limited_web',
        'memory_scope': 'default',
        'tts_voice': 'af_heart',
        'llm_request_timeout': 20.0,
    }
    sm.set_named_chat_settings.return_value = True
    system.llm_chat.session_manager = sm
    system.tts.voice_name = 'af_heart'
    ensure_discord_voice_chat_settings(system, 'discord_111_222')
    payload = sm.set_named_chat_settings.call_args[0][1] if sm.set_named_chat_settings.called else {}
    assert 'toolset' not in payload
    assert 'memory_scope' not in payload


def test_ensure_voice_chat_skips_create_when_exists():
    system = MagicMock()
    sm = MagicMock()
    name = voice_chat_name('111', '222')
    sm.read_chat_settings.return_value = {'prompt': 'default'}
    system.llm_chat.session_manager = sm
    assert ensure_voice_chat(system, '111', '222') == name
    system.llm_chat.create_chat.assert_not_called()


# ── M8: VC chats are ephemeral unless the operator keeps them ──────────────

def _system_with(settings: dict):
    system = MagicMock()
    sm = MagicMock()
    sm.read_chat_settings.return_value = dict(settings)
    sm.set_named_chat_settings.return_value = True
    system.llm_chat.session_manager = sm
    system.tts.voice_name = 'af_heart'
    return system, sm


def test_voice_chat_is_marked_ephemeral_by_default(monkeypatch):
    from plugins.discord.sapphire.voice_chat import VOICE_CHAT_TTL_MINUTES

    _fake_scope_keys(monkeypatch, ['memory_scope'])
    system, sm = _system_with({})
    ensure_discord_voice_chat_settings(system, 'discord_111_222')
    payload = sm.set_named_chat_settings.call_args[0][1]
    assert payload['ephemeral_source'] == 'discord'
    assert payload['ephemeral_ttl_min'] == VOICE_CHAT_TTL_MINUTES
    assert payload['ephemeral_last_call'] > 0


def test_keep_history_unmarks_a_previously_ephemeral_chat(monkeypatch):
    _fake_scope_keys(monkeypatch, ['memory_scope'])
    system, sm = _system_with({
        'discord_voice_isolated': True,
        'tts_voice': 'af_heart',
        'llm_request_timeout': 20.0,
        'ephemeral_source': 'discord',
        'ephemeral_last_call': 1.0,
        'ephemeral_ttl_min': 30.0,
    })
    ensure_discord_voice_chat_settings(system, 'discord_111_222', keep_history=True)
    payload = sm.set_named_chat_settings.call_args[0][1]
    assert payload['ephemeral_source'] == ''
    assert 'ephemeral_last_call' not in payload


def test_keep_history_never_touches_an_unmarked_chat(monkeypatch):
    _fake_scope_keys(monkeypatch, ['memory_scope'])
    system, sm = _system_with({
        'discord_voice_isolated': True,
        'tts_voice': 'af_heart',
        'llm_request_timeout': 20.0,
        'custom_context': '',
    })
    ensure_discord_voice_chat_settings(system, 'discord_111_222', keep_history=True)
    payload = sm.set_named_chat_settings.call_args[0][1] if sm.set_named_chat_settings.called else {}
    assert 'ephemeral_source' not in payload


def test_touch_voice_chat_restamps_only_marked_chats():
    from plugins.discord.sapphire.voice_chat import touch_voice_chat

    system, sm = _system_with({'ephemeral_source': 'discord', 'ephemeral_last_call': 1.0})
    touch_voice_chat(system, 'discord_111_222')
    name, patch = sm.set_named_chat_settings.call_args[0]
    assert name == 'discord_111_222'
    assert set(patch) == {'ephemeral_last_call'} and patch['ephemeral_last_call'] > 1.0

    system, sm = _system_with({'ephemeral_source': 'twilio', 'ephemeral_last_call': 1.0})
    touch_voice_chat(system, '_phone_x')
    sm.set_named_chat_settings.assert_not_called()


def test_reap_voice_chats_uses_core_reaper_with_discord_source_and_live_exclusion():
    from plugins.discord.sapphire.voice_chat import reap_voice_chats

    system, sm = _system_with({})
    sm.reap_ephemeral_chats.return_value = ['discord_1_2']
    deleted = reap_voice_chats(system, live={'discord_9_9'})
    assert deleted == ['discord_1_2']
    kwargs = sm.reap_ephemeral_chats.call_args.kwargs
    assert kwargs['source'] == 'discord'
    assert kwargs['exclude'] == {'discord_9_9'}


def test_ensure_voice_chat_passes_keep_history_through():
    system = MagicMock()
    sm = MagicMock()
    sm.read_chat_settings.return_value = {'discord_voice_isolated': True, 'tts_voice': 'af_heart',
                                         'llm_request_timeout': 20.0, 'ephemeral_source': 'discord'}
    sm.set_named_chat_settings.return_value = True
    system.llm_chat.session_manager = sm
    system.tts.voice_name = 'af_heart'
    ensure_voice_chat(system, '111', '222', keep_history=True)
    payload = sm.set_named_chat_settings.call_args[0][1]
    assert payload['ephemeral_source'] == ''
