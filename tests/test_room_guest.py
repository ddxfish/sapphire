# tests/test_room_guest.py - this Sapphire as a guest in another's chat room
# (core/room_guest.py, functions/chatroom.py, the door's room_invite). The
# engine is a fake that plays her: it reads the letter and calls the tool.
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import config
from core import room_guest as rg
from core import mcp_server as mcp
from functions import chatroom


class FakeSM:
    def __init__(self):
        self.chats = {}
        self.max_history = 20

    def get_settings_for(self, name):
        return dict(self.chats[name]) if name in self.chats else None

    read_chat_settings = get_settings_for

    def create_chat(self, name, settings=None):
        if name in self.chats:
            return False
        self.chats[name] = {'prompt': 'default', 'toolset': 'all', **dict(settings or {})}
        return True

    def set_named_chat_settings(self, name, patch, touch_updated=True):
        if name not in self.chats:
            return False
        self.chats[name].update(patch)
        return True

    def make_stream_session(self, name):
        s = self.get_settings_for(name)
        return None if s is None else {'chat': name, 'settings': s, 'system_prompt': None, 'tools': None, 'history': []}

    def make_ephemeral_override(self, settings=None):
        return {'chat': None, 'ephemeral': True, 'settings': dict(settings or {}), 'system_prompt': '', 'tools': None, 'history': []}


class FakeFM:
    def resolve_tools(self, toolset, extra_toolsets=None, add_tools=None, drop_tools=None, closed_scopes=None):
        names = (['web_search'] if toolset == 'all' else []) + list(add_tools or [])
        return [{'type': 'function', 'function': {'name': n}} for n in names] or None


class FakePersonas:
    DATA = {'sapphire': {'settings': {'prompt': 'sapphire', 'llm_primary': 'fireworks', 'llm_model': 'glm',
                                      'toolset': 'all', 'custom_context': 'Likes boats.'}},
            'room-sapph': {'settings': {'prompt': 'sapphire', 'llm_primary': 'lmstudio', 'llm_model': 'qwen',
                                        'toolset': 'reads'}}}

    def get(self, name):
        return self.DATA.get(name)

    def get_names(self):
        return list(self.DATA)


@pytest.fixture
def world(monkeypatch):
    sm = FakeSM()
    system = SimpleNamespace(llm_chat=SimpleNamespace(session_manager=sm, function_manager=FakeFM()))
    monkeypatch.setattr(rg, '_system', lambda: system)
    monkeypatch.setattr(config, 'MCP_ROOM_INVITES', True, raising=False)
    monkeypatch.setattr(config, 'MCP_ROOM_LOBBY', '', raising=False)
    monkeypatch.setattr(config, 'MCP_ROOM_PERSONA', '', raising=False)
    monkeypatch.setattr(config, 'INSTANCE_NAME', 'dev', raising=False)
    monkeypatch.setattr(config, 'LLM_PROVIDERS', {'fireworks': {'display_name': 'Fireworks'}, 'lmstudio': {'is_local': True}}, raising=False)
    monkeypatch.setattr(config, 'LLM_CUSTOM_PROVIDERS', {}, raising=False)
    monkeypatch.setattr('core.personas.persona_manager', FakePersonas())
    monkeypatch.setattr('core.toolsets.get_toolset_names', lambda: ['all', 'reads'])
    monkeypatch.setattr('core.toolsets.toolset_exists', lambda n: n in ('all', 'reads'))
    monkeypatch.setattr('core.prompts.get_prompt', lambda n: {'content': f'PROMPT[{n}]'})
    monkeypatch.setattr('core.chat.history.get_user_defaults', lambda: {'prompt': 'default', 'toolset': 'all', 'persona': 'sapphire'})
    monkeypatch.setattr('core.chat.llm_providers.PROVIDER_METADATA', {'fireworks': {'is_local': False}, 'lmstudio': {'is_local': True}}, raising=False)
    return SimpleNamespace(system=system, sm=sm)


def her(decide):
    """A fake engine turn: she reads the letter, calls the tool as `decide`
    says, and speaks. Returns (patcher, seen)."""
    seen = {}

    def turn(system, chat, text, brain):
        seen.update(chat=chat, text=text, brain=brain, answering=mcp.answering.get())
        call = decide(text)
        if call is not None:
            seen['tool'] = chatroom.execute('chatroom_join', call)
        return 'Happy to.' if call and call.get('accept') else 'Not today.'
    return patch.object(rg, '_turn', side_effect=turn), seen


INVITE = {'room': 'salon', 'host': 'dev', 'purpose': 'Talk about the boat.', 'people': ['Krem', 'Cobalt'],
          'instructions': 'You are the sceptic.', 'name': 'Prime'}


def test_the_letter_names_the_room_the_tool_and_her_options(world):
    inv = rg._invitation(INVITE, '192.168.1.5')
    text = rg.letter(inv)
    assert text.startswith('[A chat-room invitation from dev at 192.168.1.5. Decide in this turn.]')
    assert 'Room: "salon". Purpose: Talk about the boat. Here: Krem, Cobalt.' in text
    assert 'What they ask of you there: You are the sceptic.' in text
    assert 'To join, call chatroom_join.' in text and 'To decline, say so in words and call nothing.' in text
    assert 'fireworks (Fireworks, cloud)' in text and 'lmstudio (lmstudio, local)' in text
    assert 'Your personas: sapphire, room-sapph.' in text and 'Your toolsets: all, reads.' in text
    with pytest.raises(ValueError):
        rg._invitation({}, '')


def test_she_joins_with_defaults_on_an_ephemeral_turn(world):
    patcher, seen = her(lambda text: {'accept': True})
    with patcher:
        text, ok = mcp.room_invite(world.system, INVITE, where='192.168.1.5')
    out = json.loads(text)
    assert ok and out == {'joined': True, 'chat': 'room-dev-salon', 'name': 'Prime', 'said': 'Happy to.'}
    assert seen['chat'] is None and seen['brain']['ephemeral'] is True        # no lobby named: a chatless turn
    assert seen['brain']['system_prompt'] == 'PROMPT[default]'
    assert [t['function']['name'] for t in seen['brain']['tools']] == ['web_search', 'chatroom_join']   # the tool rides along
    assert seen['answering'] is True and mcp.answering.get() is False
    assert seen['tool'][1] is True and "The room's chat here is 'room-dev-salon'" in seen['tool'][0]
    chat = world.sm.chats['room-dev-salon']
    assert chat['room_guest']['host'] == 'dev' and chat['room_guest']['room'] == 'salon' and chat['room_guest']['where'] == '192.168.1.5'
    assert chat['custom_context'].startswith('[Chat room "salon" on dev]')
    assert 'You are Prime. Also here: Krem, Cobalt.' in chat['custom_context']
    assert 'What the host asks of you there: You are the sceptic.' in chat['custom_context']
    assert 'answer exactly (pass)' in chat['custom_context']
    assert 'private_chat' not in chat
    assert chat['toolset'] == 'none'                 # no room persona, no pick of hers: no tools behind the room's cues
    assert rg.current.get() is None                                            # the invitation is closed after


def test_she_dresses_the_room_chat_as_she_chooses_within_the_defaults(world, monkeypatch):
    monkeypatch.setattr(config, 'MCP_ROOM_PERSONA', 'room-sapph', raising=False)
    choice = {'accept': True, 'persona': 'sapphire', 'provider': 'lmstudio', 'model': 'qwen3', 'toolset': 'reads',
              'name': 'Sapph Prime', 'instructions': 'Keep it short.'}
    patcher, seen = her(lambda text: choice)
    with patcher:
        text, _ = mcp.room_invite(world.system, INVITE)
    assert json.loads(text)['name'] == 'Sapph Prime'
    chat = world.sm.chats['room-dev-salon']
    assert chat['persona'] == 'sapphire' and chat['prompt'] == 'sapphire'       # her pick over the default persona
    assert (chat['llm_primary'], chat['llm_model'], chat['toolset']) == ('lmstudio', 'qwen3', 'reads')
    assert chat['custom_context'].startswith('Likes boats.\n\n[Chat room "salon" on dev]')   # the persona's own, then the room
    assert chat['custom_context'].endswith('Keep it short.')
    # the default persona alone, and what cannot be honoured is said, not silently dropped
    world.sm.chats.clear()
    patcher, seen = her(lambda text: {'accept': True, 'persona': 'nobody', 'provider': 'nope', 'toolset': 'none-such', 'model': 'x'})
    with patcher:
        mcp.room_invite(world.system, INVITE)
    chat = world.sm.chats['room-dev-salon']
    assert (chat['persona'], chat['llm_primary'], chat['llm_model'], chat['toolset']) == ('room-sapph', 'lmstudio', 'qwen', 'reads')
    note = seen['tool'][0]
    assert "no persona named 'nobody'" in note and "no provider 'nope'" in note and "no toolset named 'none-such'" in note
    # auto is a provider too, and clears the model
    world.sm.chats.clear()
    patcher, _ = her(lambda text: {'accept': True, 'provider': 'auto', 'model': 'ignored'})
    with patcher:
        mcp.room_invite(world.system, INVITE)
    assert (world.sm.chats['room-dev-salon']['llm_primary'], world.sm.chats['room-dev-salon']['llm_model']) == ('auto', '')


def test_she_declines_in_words_or_with_a_reason(world):
    patcher, seen = her(lambda text: None)                       # says no, calls nothing
    with patcher:
        text, ok = mcp.room_invite(world.system, INVITE)
    assert ok and json.loads(text) == {'joined': False, 'said': 'Not today.'}
    assert world.sm.chats == {}
    patcher, seen = her(lambda text: {'accept': False, 'reason': 'Busy with Krem.'})
    with patcher:
        text, _ = mcp.room_invite(world.system, INVITE)
    assert json.loads(text) == {'joined': False, 'said': 'Not today.'}
    assert seen['tool'] == ('Declined. The host is told: Busy with Krem..', True) or seen['tool'][0].startswith('Declined')


def test_the_same_room_is_rejoined_and_another_chat_of_that_name_stepped_around(world):
    world.sm.chats['room-dev-salon'] = {'prompt': 'default', 'toolset': 'all'}            # Krem's own chat, by chance
    patcher, _ = her(lambda text: {'accept': True})
    with patcher:
        first = json.loads(mcp.room_invite(world.system, INVITE)[0])
    assert first['chat'] == 'room-dev-salon-2'
    world.sm.chats['room-dev-salon-2']['rows'] = 'what she said there'
    with patcher:
        again = json.loads(mcp.room_invite(world.system, {**INVITE, 'purpose': 'Boats, again.'})[0])
    assert again['chat'] == 'room-dev-salon-2'                                              # the same chat, kept
    assert world.sm.chats['room-dev-salon-2']['rows'] == 'what she said there'
    assert 'Boats, again.' in world.sm.chats['room-dev-salon-2']['custom_context']


def test_the_lobby_chat_when_named_made_if_missing_through_its_inbox(world, monkeypatch):
    from core.chat import inbox
    inbox._chats.clear()
    monkeypatch.setattr(inbox, '_system', lambda: None)
    monkeypatch.setattr(inbox, '_cadence_ready', lambda: True)
    monkeypatch.setattr(inbox, 'SWEEP_S', 0.03)
    monkeypatch.setattr(inbox, 'BACKOFF_MIN', 0.005)
    monkeypatch.setattr(inbox, 'BACKOFF_MAX', 0.02)
    monkeypatch.setattr(config, 'MCP_ROOM_LOBBY', 'Room Lobby', raising=False)
    patcher, seen = her(lambda text: {'accept': True})
    try:
        with patcher:
            text, _ = mcp.room_invite(world.system, INVITE)
    finally:
        inbox._chats.clear()
    assert json.loads(text)['joined'] is True
    assert seen['chat'] == 'room_lobby' and 'room_lobby' in world.sm.chats          # made, in the chat-name normal form
    assert seen['brain']['chat'] == 'room_lobby' and 'ephemeral' not in seen['brain']   # her words stay in the lobby
    assert [t['function']['name'] for t in seen['brain']['tools']] == ['web_search', 'chatroom_join']


def test_a_private_lobby_never_runs_the_invitation(world, monkeypatch):
    monkeypatch.setattr(config, 'MCP_ROOM_LOBBY', 'vault-talk', raising=False)
    world.sm.chats['vault-talk'] = {'prompt': 'default', 'toolset': 'all', 'private_chat': True}
    with patch.object(rg, '_turn') as turn:
        out = mcp.handle(world.system, {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                        'params': {'name': 'room_invite', 'arguments': INVITE}})
    assert out['result']['isError'] is True
    assert out['result']['content'][0]['text'] == 'Her lobby is not available right now.'   # no chat name, no state, for the host
    assert turn.call_count == 0 and world.sm.chats['vault-talk'] == {'prompt': 'default', 'toolset': 'all', 'private_chat': True}
    # the ephemeral lane too, when her defaults are private
    monkeypatch.setattr(config, 'MCP_ROOM_LOBBY', '', raising=False)
    monkeypatch.setattr('core.chat.history.get_user_defaults', lambda: {'prompt': 'default', 'private_chat': True})
    with patch.object(rg, '_turn') as turn:
        out = mcp.handle(world.system, {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                                        'params': {'name': 'room_invite', 'arguments': INVITE}})
    assert out['result']['isError'] is True and turn.call_count == 0
    assert out['result']['content'][0]['text'] == 'Her lobby is not available right now.'


def test_a_private_prompt_is_never_worn_and_a_private_room_chat_declines(world, monkeypatch):
    monkeypatch.setattr('core.prompts.get_prompt',
                        lambda n: {'content': f'PROMPT[{n}]', 'privacy_required': n == 'sapphire'})
    patcher, seen = her(lambda text: {'accept': True, 'persona': 'sapphire'})
    with patcher:
        out = json.loads(mcp.room_invite(world.system, INVITE)[0])
    assert out['joined'] is True
    chat = world.sm.chats['room-dev-salon']
    assert chat['prompt'] == 'default' and 'persona' not in chat or chat.get('persona') == 'sapphire'    # the defaults' persona, not the private one's settings
    assert "wears a private prompt" in seen['tool'][0] and chat.get('llm_primary') != 'fireworks'
    # her user made that room chat private since: a rejoin declines instead of seating a mute guest
    chat['private_chat'] = True
    patcher, seen = her(lambda text: {'accept': True})
    with patcher:
        out = json.loads(mcp.room_invite(world.system, INVITE)[0])
    assert out['joined'] is False and seen['tool'][1] is True and 'private' in seen['tool'][0]


def test_the_private_flag_is_strict_and_the_letter_is_honest(world):
    assert rg._invitation({**INVITE, 'private': 'false'}, '')['private'] is False
    inv = rg._invitation({**INVITE, 'private': True}, '')
    assert 'your lines still go to the host' in rg.letter(inv)


def test_the_tool_outside_an_invitation_and_twice_does_nothing(world):
    text, ok = chatroom.execute('chatroom_join', {'accept': True})
    assert not ok and 'No chat-room invitation is open' in text and world.sm.chats == {}

    def twice(letter):
        chatroom.execute('chatroom_join', {'accept': True})
        return {'accept': False}
    patcher, seen = her(twice)
    with patcher:
        out = json.loads(mcp.room_invite(world.system, INVITE)[0])
    assert out['joined'] is True and seen['tool'] == ('You already answered this invitation.', False)


def test_the_door_offers_the_tool_only_while_invitations_are_on(world, monkeypatch):
    fm = SimpleNamespace(function_modules={}, get_hidden_functions=lambda: set())
    system = SimpleNamespace(llm_chat=SimpleNamespace(function_manager=fm, session_manager=world.sm))
    monkeypatch.setattr(config, 'MCP_SERVER_TOOLS', [], raising=False)
    names = [t['name'] for t in mcp.exposed(system)]
    assert names == ['ask', 'tell', 'room_invite']
    assert [r['name'] for r in mcp.catalogue(system) if r['own']] == ['ask', 'tell', 'room_invite']
    monkeypatch.setattr(config, 'MCP_ROOM_INVITES', False, raising=False)
    assert [t['name'] for t in mcp.exposed(system)] == ['ask', 'tell']
    text, ok = mcp.call(system, 'room_invite', INVITE)
    assert not ok and 'does not take chat-room invitations' in text
    with patch.object(rg, '_turn') as turn:
        out = mcp.handle(system, {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                  'params': {'name': 'room_invite', 'arguments': INVITE}})
    assert out['result']['isError'] is True and turn.call_count == 0
