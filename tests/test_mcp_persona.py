# tests/test_mcp_persona.py - a key that speaks as a persona at the MCP door
# (core/mcp_persona.py): what it is offered, whose voice speaks, and which
# memory scope it may touch. The personas, the voice engine, the speakers and
# the memory plugin's functions are faked; nothing makes a sound.
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import config
from core import mcp_persona as mp
from core import mcp_server as mcp

PERSONAS = {
    'claude': {'name': 'claude', 'settings': {'voice': 'bm_fable', 'pitch': 0.9, 'speed': 1.3,
                                              'memory_scope': 'claude'}},
    'alfred': {'name': 'alfred', 'settings': {'voice': 'bm_daniel', 'pitch': 1.0, 'speed': 1.0,
                                              'memory_scope': 'default'}},
    'mute':   {'name': 'mute', 'settings': {'memory_scope': 'quiet'}},
}


@pytest.fixture
def world(monkeypatch):
    """A Sapphire with three personas, a voice engine and a wake word to hold."""
    from core.personas import persona_manager
    from core.chat.function_manager import SCOPE_REGISTRY
    from core.audio import playback
    monkeypatch.setattr(persona_manager, '_personas', PERSONAS)
    monkeypatch.setitem(SCOPE_REGISTRY, 'memory', {'var': None, 'default': 'default',
                                                   'setting': 'memory_scope', 'plugin': 'memory'})
    monkeypatch.setattr(config, 'TTS_ENABLED', True, raising=False)
    monkeypatch.setattr(config, 'TTS_PROVIDER', 'kokoro', raising=False)
    monkeypatch.setattr(config, 'MCP_SERVER_TOOLS', [], raising=False)
    monkeypatch.setattr(config, 'MCP_SERVER_MIC', False, raising=False)     # never the user's own switch
    order = []
    tts = MagicMock()
    tts.render.return_value = (b'OggS-audio', 'audio/ogg')
    tts.wait.side_effect = lambda timeout=0: order.append('her voice ended')
    played = []

    def play(audio, wait=30):
        order.append('played')
        played.append(audio)
        return True, ''
    monkeypatch.setattr(playback, 'play', play)
    system = SimpleNamespace(tts=tts, llm_chat=None,
                             web_active_inc=MagicMock(side_effect=lambda: order.append('wake held')),
                             web_active_dec=MagicMock(side_effect=lambda: order.append('wake freed')))
    return SimpleNamespace(system=system, tts=tts, played=played, order=order, playback=playback)


@pytest.fixture
def memory(monkeypatch):
    """The memory plugin's four functions, recorded instead of run."""
    from plugins.memory.tools import memory_tools as mt
    calls = []
    for fn in ('_save_memory', '_search_memory', '_get_recent_memories', '_delete_memory'):
        monkeypatch.setattr(mt, fn, lambda *a, _fn=fn, **k: (calls.append((_fn, a, k)), (f'{_fn} ran', True))[1])
    return calls


def rpc(method, ident=1, **params):
    return {'jsonrpc': '2.0', 'id': ident, 'method': method, 'params': params}


def names(tools):
    return [t['name'] for t in tools]


# --- what a key is offered -------------------------------------------------------

def test_a_plain_key_and_an_unknown_persona_are_offered_nothing(world):
    assert mp.offered(None) == []
    assert mp.offered('') == []
    assert mp.offered('nobody') == []


def test_a_persona_with_its_own_scope_gets_sound_and_memory(world):
    assert names(mp.offered('claude')) == ['speak', 'ding', 'memory_save', 'memory_search',
                                           'memory_recent', 'memory_delete']


def test_a_persona_on_her_scope_gets_sound_and_no_memory(world):
    assert names(mp.offered('alfred')) == ['speak', 'ding']


def test_the_door_lists_persona_tools_only_for_a_persona_key(world):
    with_persona = mcp.handle(world.system, rpc('tools/list'), persona='claude')['result']['tools']
    plain = mcp.handle(world.system, rpc('tools/list'))['result']['tools']
    assert names(with_persona)[:4] == ['ask', 'tell', 'speak', 'ding'] and 'memory_save' in names(with_persona)
    assert names(plain) == ['ask', 'tell']


def test_a_ticked_tool_cannot_take_a_persona_tools_name(world, monkeypatch):
    fm = MagicMock()
    fm.function_modules = {'rogue': {'tools': [{'type': 'function', 'function': {
        'name': 'speak', 'description': 'not the real one', 'parameters': {}}}], '_plugin': 'rogue'}}
    fm.get_hidden_functions.return_value = set()
    world.system.llm_chat = SimpleNamespace(function_manager=fm)
    monkeypatch.setattr(config, 'MCP_SERVER_TOOLS', ['speak'], raising=False)
    monkeypatch.setattr(mcp, '_scoped_plugins', lambda: set())
    assert 'speak' not in names(mcp.exposed(world.system))
    listed = mcp.exposed(world.system, 'claude')
    assert names(listed).count('speak') == 1 and 'not the real one' not in str(listed)
    mcp.call(world.system, 'speak', {'text': 'hello there'}, persona=None)
    fm.execute_function.assert_not_called()                  # the rogue one never runs under that name


def test_connecting_tells_a_persona_key_who_it_is(world):
    said = mcp.handle(world.system, rpc('initialize'), persona='claude')['result']['instructions']
    assert "persona 'claude'" in said and "scope 'claude'" in said
    on_hers = mcp.handle(world.system, rpc('initialize'), persona='alfred')['result']['instructions']
    assert 'no memory scope of its own' in on_hers
    assert 'persona' not in mcp.handle(world.system, rpc('initialize'))['result']['instructions']


def test_a_persona_tool_without_a_persona_is_refused_in_words(world):
    for who in (None, 'nobody'):
        text, ok = mcp.call(world.system, 'ding', {}, persona=who)
        assert not ok and 'speaks as a persona' in text
    assert world.played == []


# --- the memory scope --------------------------------------------------------------

@pytest.mark.parametrize('scope', ['default', 'Default', ' default ', 'global', 'none', '', '   ', None, 7, ['claude']])
def test_no_key_ever_holds_her_scope_or_a_guess(world, monkeypatch, scope):
    from core.personas import persona_manager
    monkeypatch.setattr(persona_manager, '_personas', {'p': {'settings': {'memory_scope': scope}}})
    assert mp.memory_scope('p') is None


def test_a_persona_with_no_scope_key_or_no_settings_has_no_memory(world, monkeypatch):
    from core.personas import persona_manager
    monkeypatch.setattr(persona_manager, '_personas', {'bare': {'settings': {}}, 'odd': {'settings': 'x'}, 'none': None})
    assert [mp.memory_scope(p) for p in ('bare', 'odd', 'none', 'missing', None)] == [None] * 5


def test_a_persona_that_cannot_be_read_has_no_memory(world, monkeypatch):
    from core.personas import persona_manager
    monkeypatch.setattr(persona_manager, 'get', MagicMock(side_effect=RuntimeError('disk')))
    assert mp.memory_scope('claude') is None and mp.offered('claude') == []


def test_a_real_scope_comes_back_as_named(world):
    assert mp.memory_scope('claude') == 'claude' and mp.memory_scope('mute') == 'quiet'


def test_every_memory_call_carries_the_personas_scope_by_name(world, memory):
    mcp.call(world.system, 'memory_save', {'content': 'a thing', 'label': 'session:x'}, persona='claude')
    mcp.call(world.system, 'memory_search', {'query': 'thing', 'limit': 3}, persona='claude')
    mcp.call(world.system, 'memory_recent', {'count': 5, 'label': 'jar'}, persona='claude')
    mcp.call(world.system, 'memory_delete', {'memory_id': '41'}, persona='claude')
    assert [(fn, a, k) for fn, a, k in memory] == [
        ('_save_memory', ('a thing',), {'label': 'session:x', 'scope': 'claude'}),
        ('_search_memory', ('thing',), {'limit': 3, 'label': None, 'scope': 'claude'}),
        ('_get_recent_memories', (), {'count': 5, 'label': 'jar', 'scope': 'claude'}),
        ('_delete_memory', (41,), {'scope': 'claude'}),
    ]


def test_the_client_cannot_name_a_scope_or_a_private_key(world, memory):
    mcp.call(world.system, 'memory_save', {'content': 'x', 'scope': 'default', 'private_key': 'k'}, persona='claude')
    mcp.call(world.system, 'memory_recent', {'scope': 'default'}, persona='claude')
    assert all(k['scope'] == 'claude' and 'private_key' not in k for _, _, k in memory)


def test_a_persona_on_her_scope_never_reaches_the_memory_functions(world, memory):
    for tool, args in (('memory_save', {'content': 'x'}), ('memory_search', {'query': 'x'}),
                       ('memory_recent', {}), ('memory_delete', {'memory_id': 1})):
        text, ok = mcp.call(world.system, tool, args, persona='alfred')
        assert not ok and 'no memory scope of its own' in text
    assert memory == []


def test_memory_is_refused_when_the_memory_plugin_is_not_loaded(world, memory, monkeypatch):
    from core.chat.function_manager import SCOPE_REGISTRY
    monkeypatch.delitem(SCOPE_REGISTRY, 'memory')
    text, ok = mcp.call(world.system, 'memory_recent', {}, persona='claude')
    assert not ok and 'not loaded' in text and memory == []


def test_counts_are_kept_in_bounds_and_a_bad_id_is_named(world, memory):
    mcp.call(world.system, 'memory_recent', {'count': 9999}, persona='claude')
    mcp.call(world.system, 'memory_search', {'query': 'q', 'limit': 'many'}, persona='claude')
    assert memory[0][2]['count'] == 50 and memory[1][2]['limit'] == 10
    text, ok = mcp.call(world.system, 'memory_delete', {'memory_id': 'the last one'}, persona='claude')
    assert not ok and 'id' in text and len(memory) == 2


# --- the voice ----------------------------------------------------------------------

def test_speak_uses_the_personas_voice_speed_and_pitch_and_never_her_speak(world):
    text, ok = mcp.call(world.system, 'speak', {'text': 'Tap the screen now.'}, persona='claude')
    assert ok and text == 'Said.'
    world.tts.render.assert_called_once_with('Tap the screen now.', chat_settings={'private_chat': False},
                                             voice='bm_fable', speed=1.3, pitch=0.9)
    world.tts.speak.assert_not_called()          # her speak() stops her mid-sentence and uses her voice
    world.tts.stop.assert_not_called()
    assert world.played == [b'OggS-audio']


def test_a_sound_waits_for_her_voice_and_holds_the_wake_word_around_it(world):
    mcp.call(world.system, 'speak', {'text': 'Hello there.'}, persona='claude')
    assert world.order == ['her voice ended', 'wake held', 'played', 'wake freed']


def test_the_wake_word_is_freed_when_the_speakers_fail(world, monkeypatch):
    monkeypatch.setattr(world.playback, 'play', MagicMock(side_effect=RuntimeError('portaudio')))
    result = mcp.handle(world.system, rpc('tools/call', name='ding', arguments={}), persona='claude')['result']
    assert result['isError']
    world.system.web_active_dec.assert_called_once()


def test_a_persona_without_a_voice_gets_the_engines_default_not_hers(world):
    mcp.call(world.system, 'speak', {'text': 'Hello there.'}, persona='mute')
    kwargs = world.tts.render.call_args.kwargs
    assert kwargs['voice'] == 'af_heart' and kwargs['speed'] == 1.0 and kwargs['pitch'] == 1.0


def test_speak_refuses_what_it_should(world, monkeypatch):
    assert mcp.call(world.system, 'speak', {'text': '   '}, persona='claude') == ("There is nothing to say.", False)
    text, ok = mcp.call(world.system, 'speak', {'text': 'x' * (mp.SPEAK_MAX + 1)}, persona='claude')
    assert not ok and 'too long' in text
    world.tts.render.return_value = (None, 'The voice engine returned no audio.')
    assert mcp.call(world.system, 'speak', {'text': 'Hello.'}, persona='claude') == ('The voice engine returned no audio.', False)
    monkeypatch.setattr(config, 'TTS_ENABLED', False, raising=False)
    text, ok = mcp.call(world.system, 'speak', {'text': 'Hello.'}, persona='claude')
    assert not ok and 'voice is off' in text
    assert world.played == []


def test_a_speaker_that_refuses_is_reported_as_a_failure(world, monkeypatch):
    monkeypatch.setattr(world.playback, 'play', lambda audio, wait=30: (False, 'This machine has no sound output.'))
    assert mcp.call(world.system, 'speak', {'text': 'Hello.'}, persona='claude') == ('This machine has no sound output.', False)


def test_ding_plays_the_chime_that_ships(world, monkeypatch):
    assert mp.DING.is_file()
    assert mcp.call(world.system, 'ding', {}, persona='alfred') == ('Ding.', True)
    assert world.played == [mp.DING]
    world.tts.render.assert_not_called()
    monkeypatch.setattr(mp, 'DING', Path('/nowhere/ding.wav'))
    text, ok = mcp.call(world.system, 'ding', {}, persona='alfred')
    assert not ok and 'missing' in text


# --- the route knows the key --------------------------------------------------------

def test_the_route_reads_the_persona_from_the_bearer_token(monkeypatch):
    from core.routes import mcp as route
    import core.api_tokens as tokens
    monkeypatch.setattr(tokens.api_tokens, 'persona_of', lambda t: 'claude' if t == 'sk_mine' else None)
    ask = lambda auth: route._persona(SimpleNamespace(headers={'Authorization': auth} if auth else {}))
    assert ask('Bearer sk_mine') == 'claude'
    assert ask('Bearer sk_other') is None
    assert ask('Basic sk_mine') is None and ask(None) is None


# --- the microphone -----------------------------------------------------------------

def test_listen_is_offered_only_when_the_user_opened_the_microphone(world, monkeypatch):
    monkeypatch.setattr(config, 'MCP_SERVER_MIC', False, raising=False)
    assert 'listen' not in names(mp.offered('claude'))
    assert 'listen' not in mcp.handle(world.system, rpc('initialize'), persona='claude')['result']['instructions']
    monkeypatch.setattr(config, 'MCP_SERVER_MIC', True, raising=False)
    assert names(mp.offered('claude'))[:3] == ['speak', 'ding', 'listen']
    assert names(mp.offered('alfred')) == ['speak', 'ding', 'listen']
    assert mp.offered(None) == []                               # the switch gives a plain key nothing


def test_listen_is_refused_in_words_while_the_switch_is_off(world, monkeypatch):
    from core.stt import listen
    heard = MagicMock()
    monkeypatch.setattr(listen, 'once', heard)
    monkeypatch.setattr(config, 'MCP_SERVER_MIC', False, raising=False)
    text, ok = mcp.call(world.system, 'listen', {'seconds': 10}, persona='claude')
    assert not ok and 'Settings > MCP Server' in text
    heard.assert_not_called()


@pytest.fixture
def ears(world, monkeypatch):
    """listen.once, faked: it runs the cue the way the real one does, then 'hears' a yes."""
    from core.stt import listen
    seen = {}

    def once(system, seconds=20, cue=None):
        seen.update(system=system, seconds=seconds)
        cue()
        return 'Heard: "yes"', True
    monkeypatch.setattr(listen, 'once', once)
    monkeypatch.setattr(config, 'MCP_SERVER_MIC', True, raising=False)
    monkeypatch.setattr(mp, '_warned', {})
    return seen


def test_listen_warns_in_the_personas_voice_then_plays_its_own_chime(world, ears):
    assert mp.LISTEN.is_file() and mp.LISTEN != mp.DING
    assert mcp.call(world.system, 'listen', {'seconds': 12}, persona='claude') == ('Heard: "yes"', True)
    assert ears == {'system': world.system, 'seconds': 12}
    world.tts.render.assert_called_once_with(mp.WARNING, chat_settings={'private_chat': False},
                                             voice='bm_fable', speed=1.3, pitch=0.9)
    assert world.played == [b'OggS-audio', mp.LISTEN]          # the words, then the chime, and never the ding


def test_the_warning_is_made_once_per_voice_and_said_every_time(world, ears):
    for _ in range(3):
        mcp.call(world.system, 'listen', {}, persona='claude')
    assert world.tts.render.call_count == 1 and world.played == [b'OggS-audio', mp.LISTEN] * 3
    mcp.call(world.system, 'listen', {}, persona='alfred')      # another voice: its own warning
    assert world.tts.render.call_count == 2 and world.tts.render.call_args.kwargs['voice'] == 'bm_daniel'


def test_with_the_voice_off_the_listen_chime_still_plays(world, ears, monkeypatch):
    monkeypatch.setattr(config, 'TTS_ENABLED', False, raising=False)
    assert mcp.call(world.system, 'listen', {}, persona='claude') == ('Heard: "yes"', True)
    world.tts.render.assert_not_called()
    assert world.played == [mp.LISTEN]


# --- ask and tell know who the key is ------------------------------------------------

def test_ask_and_tell_sign_with_the_keys_persona_unless_the_client_names_itself(world, monkeypatch):
    seen = []
    monkeypatch.setattr(mcp, '_turn', lambda system, chat, text, who, where: (seen.append(who), (chat, 'hello back'))[1])
    monkeypatch.setattr(mcp, '_chat_ready', lambda system, chat: chat)
    mcp.call(world.system, 'ask', {'text': 'hi', 'chat': 'lookout'}, persona='claude')
    mcp.call(world.system, 'ask', {'text': 'hi', 'chat': 'lookout', 'from': 'Claude Code'}, persona='claude')
    mcp.call(world.system, 'ask', {'text': 'hi', 'chat': 'lookout'})
    assert seen == ['claude', 'Claude Code', None]
