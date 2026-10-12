# tests/test_rooms.py - the chat room (core/rooms.py) on a faked store and engine
#
# The store is a dict, the inbox is real (no system: begin_stream judges),
# a seat's turn is a recorder, run_turn is a recorder that must never be
# called for a room. Record: tmp/chat-room-plan.md (BUILD 2026-10-11).
import threading
import time
from unittest.mock import patch

import logging
import pytest

from core import rooms
from core.event_bus import Events
from core.chat import inbox
from core.chat.display_format import format_messages_for_display


class FakeSM:
    def __init__(self):
        self.chats = {}
        self.max_history = 50
        self.active = None

    def read_chat_settings(self, c):
        s = self.chats.get(c)
        return dict(s['settings']) if s else None

    get_settings_for = read_chat_settings

    def chat_exists(self, c):
        return c in self.chats

    def create_chat(self, c, settings=None):
        if c in self.chats:
            return False
        self.chats[c] = {'settings': dict(settings or {}), 'rows': []}
        return True

    def set_named_chat_settings(self, c, patch, touch_updated=True):
        if c not in self.chats:
            return False
        self.chats[c]['settings'].update(patch)
        return True

    def append_messages_to_chat(self, c, rows, max_wait_if_streaming=60.0):
        if c not in self.chats:
            return False
        self.chats[c]['rows'].extend(dict(r) for r in rows)
        return True

    def clear_chat(self, c):
        if c not in self.chats:
            return False
        self.chats[c]['rows'] = []
        return True

    def replace_messages(self, c, msgs, expected_count=None, expected_digest=None):
        if c not in self.chats:
            return False, 'no such chat'
        self.chats[c]['rows'] = [dict(m) for m in msgs]
        return True, None

    def _read_store_messages(self, c):
        s = self.chats.get(c)
        return (list(s['rows']) if s else None), None, 0

    def list_chat_files(self, stats=False):
        return [{'name': n, 'mode': s['settings'].get('mode') or '', 'settings': dict(s['settings'])}
                for n, s in self.chats.items()]

    def make_stream_session(self, c):
        s = self.chats.get(c)
        if s is None:
            return None
        return {'chat': c, 'settings': dict(s['settings']), 'system_prompt': None, 'tools': None, 'history': list(s['rows'])}

    def delete_chat(self, c):
        return self.chats.pop(c, None) is not None

    def get_active_chat_name(self):
        return self.active


class FakeFM:
    META = {'search_memory': ('memory', False), 'read_self': ('memory', False), 'save_memory': ('memory', True),
            'web_search': ('web', False), 'reset_chat': ('meta_danger', True),
            'agent_spawn': ('agents', True), 'ask_user': ('meta', False), 'save_goal': ('goals', True)}

    def tools_in_category(self, cat, writes=None):
        return [n for n, (c, w) in self.META.items() if c == cat and (writes is None or w is writes)]

    def resolve_tools(self, toolset, extra_toolsets=None, add_tools=None, drop_tools=None, closed_scopes=None):
        if not toolset or toolset == 'none':
            return None
        return [{'type': 'function', 'function': {'name': n}} for n in self.META if n not in (drop_tools or set())]


class FakePersonas:
    DATA = {'sapphire': {'avatar': 's.png', 'settings': {'prompt': 'sapphire', 'llm_primary': 'fireworks',
                                                          'llm_model': 'glm', 'toolset': 'all',
                                                          'memory_scope': 'default', 'trim_color': '#39f',
                                                          'custom_context': 'Likes boats.'}},
            'cobalt': {'avatar': None, 'settings': {'prompt': 'cobalt', 'toolset': 'none'}}}

    def get(self, name):
        return self.DATA.get(name)


class FakeSystem:
    def __init__(self):
        self.llm_chat = type('LLM', (), {})()
        self.llm_chat.session_manager = FakeSM()
        self.llm_chat.function_manager = FakeFM()


@pytest.fixture
def world(monkeypatch):
    sysm = FakeSystem()
    monkeypatch.setattr(rooms, '_system', lambda: sysm)
    monkeypatch.setattr(inbox, '_system', lambda: None)
    monkeypatch.setattr(inbox, '_cadence_ready', lambda: True)
    monkeypatch.setattr(inbox, 'SWEEP_S', 0.03)
    monkeypatch.setattr(inbox, 'BACKOFF_MIN', 0.005)
    monkeypatch.setattr(inbox, 'BACKOFF_MAX', 0.02)
    monkeypatch.setattr(inbox, 'DRAINER_IDLE_EXIT_S', 0.2)
    monkeypatch.setattr(inbox, '_is_room', rooms.is_room)
    monkeypatch.setattr('core.personas.persona_manager', FakePersonas())
    monkeypatch.setattr('core.prompts.get_prompt',
                        lambda n: {'content': f'PROMPT[{n}]'} if n in ('sapphire', 'cobalt', 'default') else None)
    monkeypatch.setattr('core.chat.history.normalize_llm_pin', lambda cur, p: p)
    monkeypatch.setattr('config.DEFAULT_USERNAME', 'Krem', raising=False)
    monkeypatch.setattr(rooms, '_delete_chat', lambda name: sysm.llm_chat.session_manager.delete_chat(name))
    inbox._chats.clear()
    rooms._rooms.clear()
    yield sysm
    for c in list(rooms._rooms):
        rooms.stop(c)
    for name in list(inbox._chats):
        inbox.drop_chat(name, 'test over')
    inbox._chats.clear()
    rooms._rooms.clear()


def _wait(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


def _salon(two=True):
    chat = rooms.create('salon', 'Talk about the boat.')
    a = rooms.add_seat(chat, 'sapphire')
    b = rooms.add_seat(chat, 'sapphire') if two else None
    return chat, a, b


# ── the store ────────────────────────────────────────────────────────────────

def test_create_seats_and_describe(world):
    chat, a, b = _salon()
    assert chat == 'salon'
    assert world.llm_chat.session_manager.chats['salon']['settings']['mode'] == 'room'
    d = rooms.describe(chat)
    assert d['purpose'] == 'Talk about the boat.'
    assert [s['name'] for s in d['seats']] == ['Sapphire', 'Sapphire 2']     # one persona, two seats
    assert a['id'] != b['id'] and a['settings']['llm_model'] == 'glm' and a['color'] == '#39f'
    rooms.update_seat(chat, b['id'], {'name': 'Sapph Purple', 'context': 'You are the sceptic.',
                                      'memory_write': True, 'settings': {'llm_model': 'claude'}})
    d = rooms.describe(chat)
    s2 = next(s for s in d['seats'] if s['id'] == b['id'])
    assert (s2['name'], s2['context'], s2['memory_write'], s2['settings']['llm_model']) == \
           ('Sapph Purple', 'You are the sceptic.', True, 'claude')
    assert next(s for s in d['seats'] if s['id'] == a['id'])['settings']['llm_model'] == 'glm'   # the twin untouched
    rooms.remove_seat(chat, a['id'])
    assert [s['id'] for s in rooms.describe(chat)['seats']] == [b['id']]
    with pytest.raises(rooms.RoomError):
        rooms.add_seat(chat, 'nobody')
    with pytest.raises(rooms.RoomError):
        rooms.create('salon')
    assert [r['chat'] for r in rooms.list_rooms()] == ['salon']


# ── the projection ───────────────────────────────────────────────────────────

def test_projection_keys_on_seat_id_and_labels_the_rest(world):
    chat, a, b = _salon()
    rows = [
        {'role': 'user', 'content': 'Evening, both.', 'speaker': 'user', 'name': 'Krem'},
        {'role': 'assistant', 'content': 'Evening, Krem.', 'speaker': a['id'], 'persona': 'sapphire'},
        {'role': 'assistant', 'content': 'Hi.', 'speaker': b['id'], 'persona': 'sapphire'},
        {'role': 'tool', 'content': 'never here', 'name': 'x'},
        {'role': 'user', 'content': [{'type': 'text', 'text': 'Boat talk?'}], 'speaker': 'user'},
        {'role': 'user', 'content': 'hello from outside', 'speaker': 'mcp:Visitor', 'name': 'Visitor'},
    ]
    _, room = rooms._room(chat)
    hist, cue = rooms.projection(rows, {'id': a['id'], 'name': 'Sapphire'}, rooms._names(room))
    assert hist == [{'role': 'user', 'content': 'Krem: Evening, both.'},
                    {'role': 'assistant', 'content': 'Evening, Krem.'}]
    assert cue == 'Sapphire 2: Hi.\n\nKrem: Boat talk?\n\nVisitor: hello from outside\n\n(Your turn, Sapphire.)'
    # the twin sees Sapphire's line as someone else's, never as its own
    hist_b, cue_b = rooms.projection(rows, {'id': b['id'], 'name': 'Sapphire 2'}, rooms._names(room))
    assert hist_b[0]['content'] == 'Krem: Evening, both.\n\nSapphire: Evening, Krem.'
    assert hist_b[1] == {'role': 'assistant', 'content': 'Hi.'}
    assert cue_b.endswith('(Your turn, Sapphire 2.)')
    # nothing said yet
    _, cue0 = rooms.projection([], {'id': a['id'], 'name': 'Sapphire'}, rooms._names(room))
    assert cue0.startswith('(Nobody has spoken yet.)')


# ── the brain ────────────────────────────────────────────────────────────────

def test_seat_brain_scopes_tools_context_privacy(world):
    from core.chat.function_manager import scope_setting_keys
    chat, a, b = _salon()
    rooms.update_seat(chat, a['id'], {'context': 'You are the optimist.'})
    world.llm_chat.session_manager.chats[chat]['settings']['private_chat'] = True
    s_room, room = rooms._room(chat)
    seat = next(s for s in room['seats'] if s['id'] == a['id'])
    brain, cue = rooms.seat_brain(chat, s_room, room, seat)
    st = brain['settings']
    # on HER OWN chat's session, never ephemeral: the engine saves there (2026-10-12)
    assert brain['chat'] == f"seat-{a['id']}" and 'ephemeral' not in brain
    assert brain['seat'] == a['id'] and brain['room'] == chat
    assert st['memory_scope'] == 'default'                                   # the persona's own: reads allowed
    assert all(st[k] == 'none' for k in scope_setting_keys() if k != 'memory_scope')   # unlisted = OFF, never hers
    assert st['private_chat'] is True and st['spice_enabled'] is False and st['rag_context'] == 'off'
    assert st['custom_context'] == 'Likes boats.\n\nYou are the optimist.'
    names = {t['function']['name'] for t in brain['tools']}
    assert names == {'search_memory', 'read_self', 'web_search'}   # writers, meta_danger, agents and the card are gone
    assert brain['system_prompt'].startswith('PROMPT[sapphire]\n\n[Chat room "salon"]')
    assert 'What this room is for: Talk about the boat.' in brain['system_prompt']
    assert 'You are Sapphire. Also here: Krem, Sapphire 2 (sapphire).' in brain['system_prompt']
    assert 'This is your first turn here: read your self-sheet (read_self)' in brain['system_prompt']
    assert brain['history'] == [] and cue.startswith('(Nobody has spoken yet.)')
    # her second turn: her chat has her rows, the first-turn line is gone
    world.llm_chat.session_manager.chats[brain['chat']]['rows'].append({'role': 'assistant', 'content': 'Hi.'})
    brain, _ = rooms.seat_brain(chat, s_room, room, seat)
    assert 'first turn' not in brain['system_prompt'] and brain['history'] == [{'role': 'assistant', 'content': 'Hi.'}]
    # memory writes on for THIS seat only
    rooms.update_seat(chat, a['id'], {'memory_write': True})
    s_room, room = rooms._room(chat)
    seat = next(s for s in room['seats'] if s['id'] == a['id'])
    brain, _ = rooms.seat_brain(chat, s_room, room, seat)
    names = {t['function']['name'] for t in brain['tools']}
    assert names == {'search_memory', 'read_self', 'save_memory', 'save_goal', 'web_search'}
    # a 'none' toolset is no tools at all
    s_room, room = rooms._room(chat)
    c = rooms.add_seat(chat, 'cobalt')
    brain, _ = rooms.seat_brain(chat, s_room, rooms._room(chat)[1], next(s for s in rooms._room(chat)[1]['seats'] if s['id'] == c['id']))
    assert brain['tools'] is None


# ── the inbox's room rule ───────────────────────────────────────────────────

def test_inbox_refuses_a_doors_turn_and_takes_text_as_lines(world):
    chat, a, b = _salon()
    rooms.update_seat(chat, a['id'], {'enabled': False})
    rooms.update_seat(chat, b['id'], {'enabled': False})           # no seats run: a line only lands
    with pytest.raises(inbox.InboxRefused, match='chat room'):
        inbox.put(chat, inbox.Item(run=lambda: 'typed', source='web', lane='now'))
    ran = inbox.put(chat, inbox.Item(run=lambda: 'seat', source='room', lane='now'))
    assert ran.reply.result(timeout=3) == 'seat'
    with patch('core.cadence.run_turn') as rt:
        note = inbox.ask(chat, 'hello in there', source='mcp:1.2.3.4', who='Visitor', timeout=3)
        assert rt.call_count == 0                                    # never her turn
    assert note.startswith("Posted to the room 'salon'")
    rows = world.llm_chat.session_manager.chats[chat]['rows']
    assert rows[-1]['content'] == 'hello in there' and rows[-1]['speaker'] == 'mcp:Visitor' and rows[-1]['name'] == 'Visitor'
    rooms.say(chat, 'and me')
    assert _wait(lambda: len(rows) == 2)
    assert rows[-1]['speaker'] == 'user' and rows[-1]['name'] == 'Krem' and rows[-1]['role'] == 'user'
    # a door that brought the person's words (voice, the phone, a satellite):
    # its turn never runs, the words land as the person's line
    said = inbox.turn(chat, lambda: pytest.fail('her turn must not run on a room'),
                      source='device:board-1', text='heard by the board')
    assert said.startswith("Posted to the room 'salon'")
    assert rows[-1]['content'] == 'heard by the board' and rows[-1]['speaker'] == 'user'
    note = inbox.turn(chat, lambda: 'x', source='voice', text='the wake word')
    assert rows[-1]['speaker'] == 'user' and 'Posted' in note


# ── the runner ───────────────────────────────────────────────────────────────

def _recording_seat_turn(order):
    def fake(chat, seat, rt):
        order.append(seat['name'])
        rooms.append_line(chat, 'assistant', f"{seat['name']} speaks", seat['id'], seat['name'], persona=seat['persona'])
        return 'spoke'
    return fake


def test_rounds_then_the_floor_is_the_persons(world, monkeypatch):
    chat, a, b = _salon()
    order = []
    monkeypatch.setattr(rooms, '_seat_turn', _recording_seat_turn(order))
    rows = world.llm_chat.session_manager.chats[chat]['rows']
    rooms.start(chat)
    assert _wait(lambda: rooms.state(chat)['waiting'])
    assert order == ['Sapphire', 'Sapphire 2'] and rooms.state(chat)['round'] == 1
    rooms.say(chat, 'go on')                                           # the person's line ends the wait
    assert _wait(lambda: rooms.state(chat)['round'] == 2 and rooms.state(chat)['waiting'])
    assert order == ['Sapphire', 'Sapphire 2'] * 2
    assert [r['speaker'] for r in rows] == [a['id'], b['id'], 'user', a['id'], b['id']]
    assert rows[2]['content'] == 'go on' and rows[0]['persona'] == 'sapphire'
    rooms.stop(chat)
    assert _wait(lambda: not rooms.state(chat)['running'])


def test_a_line_mid_round_lands_between_seats(world, monkeypatch):
    chat, a, b = _salon()
    gate = threading.Event()
    order = []

    def slow(chat_, seat, rt):
        order.append(seat['name'])
        if seat['id'] == a['id']:
            gate.wait(timeout=3)                                      # the first seat is mid-turn
        rooms.append_line(chat_, 'assistant', 'x', seat['id'], seat['name'])
        return 'x'
    monkeypatch.setattr(rooms, '_seat_turn', slow)
    rows = world.llm_chat.session_manager.chats[chat]['rows']
    rooms.start(chat)
    assert _wait(lambda: order == ['Sapphire'])
    rooms.say(chat, 'wait, one thing')                                 # typed while Sapphire talks
    time.sleep(0.1)
    assert [r['speaker'] for r in rows] == []                          # not written inside her turn
    gate.set()
    assert _wait(lambda: rooms.state(chat)['waiting'])
    assert [r['speaker'] for r in rows] == [a['id'], 'user', b['id']]  # between the seats, in order


def test_skip_rounds_then_stop(world, monkeypatch):
    chat, a, b = _salon()
    order = []
    monkeypatch.setattr(rooms, '_seat_turn', _recording_seat_turn(order))
    rooms.start(chat, rounds=1)                                        # one round without the person
    assert _wait(lambda: rooms.state(chat)['waiting'])
    assert rooms.state(chat)['round'] == 2 and len(order) == 4
    st = rooms.stop(chat)
    assert _wait(lambda: not rooms.state(chat)['running'])
    assert st['skip'] == 0


def test_stop_cancels_the_seat_in_flight(world, monkeypatch):
    chat, a, b = _salon()
    started = threading.Event()
    stream = type('S', (), {'cancel_flag': False})()

    def hang(chat_, seat, rt):
        rt.stream = stream
        started.set()
        _wait(lambda: stream.cancel_flag, timeout=3)
        rt.stream = None
        return ''                                                      # the partial line is dropped
    monkeypatch.setattr(rooms, '_seat_turn', hang)
    rooms.start(chat)
    assert started.wait(3)
    rooms.stop(chat)
    assert stream.cancel_flag is True
    assert _wait(lambda: not rooms.state(chat)['running'])
    assert world.llm_chat.session_manager.chats[chat]['rows'] == []


def test_chat_deleted_ends_the_room(world, monkeypatch):
    chat, a, b = _salon()
    monkeypatch.setattr(rooms, '_seat_turn', _recording_seat_turn([]))
    rooms.start(chat)
    assert _wait(lambda: rooms.state(chat)['waiting'])
    rooms.chat_deleted(chat)
    assert chat not in rooms._rooms


# ── the belts ────────────────────────────────────────────────────────────────

def test_run_turn_refuses_a_room(monkeypatch):
    from core import cadence
    sm = type('SM', (), {'chat_exists': lambda self, c: True,
                         'get_settings_for': lambda self, c: {'mode': 'room'}})()
    llm = type('L', (), {'session_manager': sm})()
    llm.begin_stream = lambda *a, **k: pytest.fail('begin_stream must not run on a room')
    monkeypatch.setattr(cadence, '_system', type('Sys', (), {'llm_chat': llm})())
    with pytest.raises(RuntimeError, match='chat room'):
        cadence.run_turn('salon', 'hello')


def test_display_splits_bubbles_per_speaker():
    rows = [{'role': 'user', 'content': 'hi', 'speaker': 'user', 'name': 'Krem'},
            {'role': 'assistant', 'content': 'a1', 'speaker': 'a', 'name': 'Blue', 'persona': 'sapphire'},
            {'role': 'assistant', 'content': 'b1', 'speaker': 'b', 'name': 'Purple', 'persona': 'sapphire'},
            {'role': 'assistant', 'content': 'b2', 'speaker': 'b', 'name': 'Purple'}]
    out = format_messages_for_display(rows)
    assert [(m['role'], m.get('speaker'), m.get('name')) for m in out] == \
           [('user', 'user', 'Krem'), ('assistant', 'a', 'Blue'), ('assistant', 'b', 'Purple')]
    assert [p['text'] for p in out[2]['parts']] == ['b1', 'b2']          # the same speaker still rolls up
    plain = format_messages_for_display([{'role': 'assistant', 'content': 'x'}, {'role': 'assistant', 'content': 'y'}])
    assert len(plain) == 1 and 'speaker' not in plain[0]                  # a normal chat is unchanged
    # a line's footnote (2026-10-12): the tools she used, before her words; a local seat's
    # with inputs and results, a guest's by name only; no tool_call_id (no x button)
    noted = format_messages_for_display([{'role': 'assistant', 'content': 'line', 'speaker': 'a', 'name': 'Purple',
                                          'tools': [{'id': 'c1', 'name': 'web_search', 'args': {'q': 'boats'}, 'result': 'Boats.'},
                                                    {'name': 'read_self'}, 'junk', {'args': {}}]}])
    assert noted[0]['parts'] == [
        {'type': 'tool_result', 'name': 'web_search', 'tool_call_id': None, 'result': 'Boats.', 'inputs': {'q': 'boats'}},
        {'type': 'tool_result', 'name': 'read_self', 'tool_call_id': None, 'result': '(ran on her machine; the result stays there)'},
        {'type': 'content', 'text': 'line'}]


def test_mcp_header_and_text_travel_apart_with_who():
    from core import mcp_server
    sm = type('SM', (), {'get_settings_for': lambda self, n: {'private_chat': False}})()
    system = type('Sys', (), {'llm_chat': type('L', (), {'session_manager': sm})()})()
    with patch('core.chat.inbox.tell') as tell:
        text, ok = mcp_server.tell(system, {'chat': 'salon', 'text': 'hi there', 'from': 'Visitor'}, where='1.2.3.4')
    assert ok and tell.call_count == 1
    args, kw = tell.call_args
    assert args[:2] == ('salon', 'hi there')
    assert kw['header_line'].startswith('[This is from Visitor at 1.2.3.4') and kw['who'] == 'Visitor'
    # the inbox composes header + text for a normal chat exactly as the door used to
    it = inbox.Item(text='hi there', header=kw['header_line'], source='mcp:1.2.3.4')
    c = inbox._Chat('salon')
    assert inbox._compose(c, [it]) == f"{kw['header_line']}\nhi there"


# ── the rules ────────────────────────────────────────────────────────────────

def test_rules_round_trip_and_describe(world):
    chat, a, b = _salon()
    d = rooms.describe(chat)
    assert (d['human_every'], d['pause_s'], d['max_rounds'], d['bots_enabled'], d['shuffle']) == (1, 0.0, 0, True, False)
    assert d['person'] == 'Krem' and d['guests'] == []
    rooms.update_room(chat, {'human_every': '3', 'pause_s': 1.5, 'max_rounds': -4, 'shuffle': 1, 'purpose': 'New'})
    d = rooms.describe(chat)
    assert (d['human_every'], d['pause_s'], d['max_rounds'], d['shuffle'], d['purpose']) == (3, 1.5, 0, True, 'New')
    rooms.update_room(chat, {'human_every': 'junk'})
    assert rooms.describe(chat)['human_every'] == 1                       # a bad value is the default
    rows = world.llm_chat.session_manager.chats[chat]['rows']
    rows.append({'role': 'user', 'content': 'hi', 'speaker': 'mcp:Visitor', 'name': 'Visitor'})
    rows.append({'role': 'user', 'content': 'again', 'speaker': 'mcp:Visitor', 'name': 'Visitor'})
    rows.append({'role': 'user', 'content': 'notice', 'speaker': 'room', 'name': 'Room'})
    assert rooms.describe(chat)['guests'] == [{'speaker': 'mcp:Visitor', 'name': 'Visitor'}]


def test_human_every_counts_only_the_person(world, monkeypatch):
    chat, a, b = _salon()
    order = []
    monkeypatch.setattr(rooms, '_seat_turn', _recording_seat_turn(order))
    rooms.update_room(chat, {'human_every': 2})
    rooms.say(chat, 'begin')
    assert _wait(lambda: rooms.state(chat)['waiting'])
    st = rooms.state(chat)
    assert st['round'] == 2 and st['since_human'] == 2 and len(order) == 4          # two rounds, then the floor
    with patch('core.cadence.run_turn') as rt:
        inbox.ask(chat, 'psst', source='mcp:1.2.3.4', who='Visitor', timeout=3)      # a door's line
    time.sleep(0.15)
    assert rooms.state(chat)['waiting'] and len(order) == 4                           # never stands in for the person
    rooms.say(chat, 'carry on')
    assert _wait(lambda: rooms.state(chat)['round'] == 4 and rooms.state(chat)['waiting'])
    assert len(order) == 8 and rooms.state(chat)['since_human'] == 2
    rooms.stop(chat)
    assert _wait(lambda: not rooms.state(chat)['running'])


def test_max_rounds_stops_a_run_with_a_notice(world, monkeypatch):
    chat, a, b = _salon()
    order = []
    monkeypatch.setattr(rooms, '_seat_turn', _recording_seat_turn(order))
    rooms.update_room(chat, {'human_every': 0, 'max_rounds': 3})                      # never wait, cap at 3
    rooms.start(chat)
    assert _wait(lambda: not rooms.state(chat)['running'])
    rows = world.llm_chat.session_manager.chats[chat]['rows']
    assert len(order) == 6 and rows[-1]['speaker'] == 'room' and rows[-1]['content'] == 'Stopped after 3 rounds.'
    rooms.say(chat, 'more')                                                          # a fresh run, fresh count
    assert _wait(lambda: len(order) == 12 and not rooms.state(chat)['running'])


def test_bots_switch(world, monkeypatch):
    chat, a, b = _salon()
    order = []
    monkeypatch.setattr(rooms, '_seat_turn', _recording_seat_turn(order))
    rooms.update_room(chat, {'bots_enabled': False})
    note = rooms.say(chat, 'just noting this')
    rows = world.llm_chat.session_manager.chats[chat]['rows']
    assert _wait(lambda: len(rows) == 1)
    time.sleep(0.1)
    assert order == [] and not rooms.state(chat)['running']                           # the line landed, nobody answered
    with pytest.raises(rooms.RoomError, match='switched off'):
        rooms.start(chat)
    rooms.update_room(chat, {'bots_enabled': True})                                   # on: the unanswered line is answered
    assert _wait(lambda: rooms.state(chat)['waiting'])
    assert order == ['Sapphire', 'Sapphire 2']
    rooms.update_room(chat, {'bots_enabled': False})                                  # off mid-wait: the run ends
    assert _wait(lambda: not rooms.state(chat)['running'])


def test_pause_between_seats(world, monkeypatch):
    chat, a, b = _salon()
    stamps = []

    def stamp(chat_, seat, rt):
        stamps.append(time.monotonic())
        return 'said'
    monkeypatch.setattr(rooms, '_seat_turn', stamp)
    rooms.update_room(chat, {'pause_s': 0.15})
    rooms.start(chat)
    assert _wait(lambda: rooms.state(chat)['waiting'])
    assert len(stamps) == 2 and stamps[1] - stamps[0] >= 0.12
    rooms.stop(chat)
    assert _wait(lambda: not rooms.state(chat)['running'])


# ── clear and import keep the seats ─────────────────────────────────────────

def test_clear_keeps_seats_and_rules_and_resets_the_count(world, monkeypatch):
    chat, a, b = _salon()
    monkeypatch.setattr(rooms, '_seat_turn', _recording_seat_turn([]))
    rooms.update_room(chat, {'human_every': 2, 'purpose': 'Keep me'})
    rooms.say(chat, 'hello')
    assert _wait(lambda: rooms.state(chat)['waiting'])
    assert rooms.state(chat)['since_human'] == 2
    st = rooms.clear(chat)
    assert world.llm_chat.session_manager.chats[chat]['rows'] == []
    assert (st['round'], st['since_human'], st['rounds_run'], st['running']) == (0, 0, 0, False)
    d = rooms.describe(chat)
    assert [s['id'] for s in d['seats']] == [a['id'], b['id']] and d['human_every'] == 2 and d['purpose'] == 'Keep me'


def test_import_transcript_and_whole_room(world):
    chat, a, b = _salon()
    other = rooms.create('other', 'Elsewhere')
    exported = {'messages': [{'role': 'user', 'content': 'from the file', 'speaker': 'user', 'name': 'Krem'}],
                'room': {'purpose': 'Imported purpose', 'human_every': 3, 'shuffle': True,
                         'seats': [{'id': 'zz1', 'persona': 'cobalt', 'name': 'Cobalt', 'context': '', 'memory_write': False,
                                    'enabled': True, 'color': '', 'avatar': None, 'settings': {'toolset': 'none'}},
                                   {'bad': 'row'}]}}
    d = rooms.import_room(other, {'messages': exported['messages']})          # transcript only
    assert world.llm_chat.session_manager.chats[other]['rows'][0]['content'] == 'from the file'
    assert d['purpose'] == 'Elsewhere' and d['seats'] == []
    d = rooms.import_room(other, exported)                                       # the whole room
    assert d['purpose'] == 'Imported purpose' and d['human_every'] == 3 and d['shuffle'] is True
    assert [s['id'] for s in d['seats']] == ['zz1'] and d['seats'][0]['settings'] == {'toolset': 'none'}
    assert [s['id'] for s in rooms.describe(chat)['seats']] == [a['id'], b['id']]  # the salon untouched
    with pytest.raises(rooms.RoomError):
        rooms.import_room('nope', exported)


# ── another Sapphire's seat ─────────────────────────────────────────────────

class FakeDoor:
    """The Another Sapphire driver's two core hooks, played."""
    def __init__(self, decision=None, answer='Evening from the server.', fail=None):
        self.decision = decision or {'joined': True, 'chat': 'room-dev-salon', 'name': 'Prime', 'said': 'Happy to.'}
        self.answer, self.fail, self.cards, self.cues, self.tools, self.told = answer, fail, [], [], [], []

    def invite(self, device, config, secrets, card):
        self.cards.append(card)
        if self.fail:
            raise RuntimeError(self.fail)
        return self.decision

    def cue(self, device, config, secrets, chat, text, who):
        self.cues.append((chat, text, who))
        if self.fail:
            raise RuntimeError(self.fail)
        return self.answer, list(self.tools)

    def notify(self, device, config, secrets, chat, text, who):
        self.told.append((chat, text, who))
        if self.fail:
            raise RuntimeError(self.fail)


def _with_door(monkeypatch, door):
    brief = {'id': 'prime', 'label': 'Sapph Prime', 'location': ''}
    monkeypatch.setattr(rooms, '_door', lambda device_id: (door, brief, {'url': 'https://x'}, {'token': 't'})
                        if device_id == 'prime' else (_ for _ in ()).throw(rooms.RoomError(f"no device '{device_id}'")))
    monkeypatch.setattr('config.INSTANCE_NAME', 'dev', raising=False)
    return door


def test_the_door_lookup_speaks_the_engines_words(world, monkeypatch):
    """Her door off, unreachable, a refused token: the driver says which when
    the invitation is sent. The lookup before it only has to find the device
    and pass the engine's own words through."""
    from core.devices import engine
    calls = []

    def door(device_id, capability, hook):
        calls.append((device_id, capability, hook))
        raise engine.DeviceError("There is no device named 'prime'.")
    monkeypatch.setattr(engine, 'door', door)
    chat, _, _ = _salon()
    with pytest.raises(rooms.RoomError, match="no device named 'prime'"):
        rooms.invite_sapphire(chat, 'prime')
    assert calls == [('prime', 'chat', 'invite')]


def test_inviting_another_sapphire_seats_her_when_she_joins(world, monkeypatch):
    chat, a, b = _salon()
    door = _with_door(monkeypatch, FakeDoor())
    out = rooms.invite_sapphire(chat, 'prime', name='Prime', instructions='Be the sceptic.')
    assert out['joined'] is True and out['said'] == 'Happy to.'
    card = door.cards[0]
    assert card == {'room': 'salon', 'host': 'dev', 'purpose': 'Talk about the boat.', 'people': ['Krem', 'Sapphire', 'Sapphire 2'],
                    'instructions': 'Be the sceptic.', 'private': False, 'name': 'Prime'}
    seat = out['seat']
    assert (seat['kind'], seat['device'], seat['label'], seat['chat'], seat['name']) == ('sapphire', 'prime', 'Sapph Prime', 'room-dev-salon', 'Prime')
    assert seat['persona'] == '' and seat['settings'] == {} and seat['context'] == 'Be the sceptic.'
    d = rooms.describe(chat)
    assert [x['name'] for x in d['seats']] == ['Sapphire', 'Sapphire 2', 'Prime'] and d['seats'][2]['kind'] == 'sapphire'
    # her settings are hers: a brain patch is ignored, her name and colour are ours to set
    rooms.update_seat(chat, seat['id'], {'name': 'Prime!', 'color': '#f0f', 'settings': {'llm_model': 'claude'}})
    s3 = rooms.describe(chat)['seats'][2]
    assert (s3['name'], s3['color'], s3['settings']) == ('Prime!', '#f0f', {})
    # she declined: no seat, her words back (her first seat removed: one seat per Sapphire)
    rooms.remove_seat(chat, seat['id'])
    door.decision = {'joined': False, 'said': 'Not today.'}
    out = rooms.invite_sapphire(chat, 'prime')
    assert out == {'joined': False, 'said': 'Not today.'} and len(rooms.describe(chat)['seats']) == 2
    # her door refused, or there is no such device, or the room is private: a reason in words
    door.fail = 'She does not take chat-room invitations.'
    with pytest.raises(rooms.RoomError, match='does not take'):
        rooms.invite_sapphire(chat, 'prime')
    with pytest.raises(rooms.RoomError, match="no device"):
        rooms.invite_sapphire(chat, 'nobody')
    world.llm_chat.session_manager.chats[chat]['settings']['private_chat'] = True
    with pytest.raises(rooms.RoomError, match='private'):
        rooms.invite_sapphire(chat, 'prime')
    # a guest seat survives an export and import
    world.llm_chat.session_manager.chats[chat]['settings']['private_chat'] = False
    door.fail = None
    door.decision = {'joined': True, 'chat': 'room-dev-salon', 'name': 'Prime', 'said': 'Back.'}
    rooms.invite_sapphire(chat, 'prime')
    rec = world.llm_chat.session_manager.chats[chat]['settings']['room']
    other = rooms.create('other')
    d = rooms.import_room(other, {'room': rec})
    assert [x.get('kind') for x in d['seats']] == [None, None, 'sapphire']


def test_her_turn_is_one_ask_with_the_lines_since_her_last(world, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger='core.rooms')
    chat, a, b = _salon(two=False)
    door = _with_door(monkeypatch, FakeDoor())
    seat = rooms.invite_sapphire(chat, 'prime', name='Prime')['seat']
    rooms.update_seat(chat, a['id'], {'enabled': False})                  # only she speaks
    rows = world.llm_chat.session_manager.chats[chat]['rows']
    door.tools = ['read_self']                     # her box shares the names of her tools: the line's footnote
    rooms.say(chat, 'Evening, Prime.')
    assert _wait(lambda: rooms.state(chat)['waiting'])
    assert [r['speaker'] for r in rows] == ['user', seat['id']]
    assert rows[1] == {'role': 'assistant', 'content': 'Evening from the server.', 'speaker': seat['id'], 'name': 'Prime',
                       'kind': 'sapphire', 'tools': [{'name': 'read_self'}]}
    door.tools = []
    far_chat, cue, who = door.cues[0]
    assert far_chat == 'room-dev-salon' and who == "the chat room 'salon' on dev"
    assert cue == 'Krem: Evening, Prime.\n\n(Your turn, Prime.)'
    assert rows[1]['kind'] == 'sapphire'
    # her pass is no line; a round nobody spoke in stops the room with a line of its own
    door.answer = '(pass)'
    rooms.say(chat, 'Anything?')
    assert _wait(lambda: len(door.cues) == 2 and not rooms.state(chat)['running'])
    assert [r['speaker'] for r in rows] == ['user', seat['id'], 'user', 'room']
    assert rows[-1]['content'].startswith('Nobody spoke this round, so the room stopped.')
    assert door.cues[1][1] == 'Krem: Anything?\n\n(Your turn, Prime.)'    # since HER last line, her own left out
    # her door's words for an empty answer are a pass too, never her line
    door.answer = '(she said nothing)'
    rooms.say(chat, 'Hm?')
    assert _wait(lambda: len(door.cues) == 3 and not rooms.state(chat)['running'])
    assert [r['speaker'] for r in rows][-3:] == ['room', 'user', 'room']
    # ...and so are her door's words WITH the reason (her box's turn report, 2026-10-12)
    door.answer = '(she said nothing: thinking never finished (past 6000 chars with no words, cut off here))'
    rooms.say(chat, 'Hm?')
    assert _wait(lambda: len(door.cues) == 4 and not rooms.state(chat)['running'])
    assert [r['speaker'] for r in rows][-3:] == ['room', 'user', 'room']
    # silent turns are lines in the LOG, with what her door said (three silent turns in a
    # row looked like lost lines once, field report 2026-10-12)
    said = [r.getMessage() for r in caplog.records if 'passed' in r.getMessage()]
    assert "[ROOM] 'salon': Prime passed (her door said: '(pass)')" in said
    assert "[ROOM] 'salon': Prime passed (her door said: '(she said nothing)')" in said
    assert "[ROOM] 'salon': Prime passed (her door said: '(she said nothing: thinking never finished " \
           "(past 6000 chars with no words, cut off here))')" in said
    # her miss is a line in the room, not a failed turn
    door.fail = 'Could not reach her. Is she running?'
    rooms.say(chat, 'Still there?')
    assert _wait(lambda: len(door.cues) == 5 and not rooms.state(chat)['running'])
    assert rows[-2]['speaker'] == 'room' and rows[-2]['content'] == 'Prime did not answer: Could not reach her. Is she running?'
    # a room turned private skips her with a line and never calls her door
    door.fail = None
    world.llm_chat.session_manager.chats[chat]['settings']['private_chat'] = True
    rooms.say(chat, 'Secret.')
    assert _wait(lambda: 'private' in rows[-2]['content'] and not rooms.state(chat)['running'])
    assert rows[-2]['speaker'] == 'room' and len(door.cues) == 5
    # the room cleared: she is told through her door, in her room chat; hers to keep or clear
    rooms.clear(chat)
    assert _wait(lambda: door.told)
    assert door.told == [('room-dev-salon', "The chat room 'salon' on dev was cleared by its host. This chat is yours to keep or clear.",
                          "the chat room 'salon' on dev")]
    rooms.chat_deleting(chat)
    assert _wait(lambda: len(door.told) == 2) and 'was closed by its host' in door.told[1][1]


def _seat_turn_with(world, monkeypatch, chat, events, text='', rows_after=()):
    """One local seat turn against a faked cadence lane: `events` reach the
    room's hook, `text` is her line back, `rows_after` are rows the lane
    'saved' on her own chat. Returns (the line, the END event or None, the
    room's rows, what the lane was called with)."""
    from core import cadence
    seen = {}

    def run_turn(own, cue, **kw):
        seen.update(chat=own, cue=cue, **kw)
        kw['on_stream'](type('S', (), {'cancel_flag': False})())
        world.llm_chat.session_manager.chats[own]['rows'].extend(dict(r) for r in rows_after)
        for ev in events:
            kw['on_event'](ev)
        return text
    monkeypatch.setattr(cadence, 'run_turn', run_turn)
    published = []
    monkeypatch.setattr(rooms, 'publish', lambda et, data=None, **k: published.append((et, data)))
    _, room = rooms._room(chat)
    out = rooms._seat_turn(chat, room['seats'][0], rooms._rt(chat))
    end = next((d for et, d in published if et == Events.VOICE_TURN_END), None)
    seen['wire'] = [et for et, _ in published]
    return out, end, world.llm_chat.session_manager.chats[chat]['rows'], seen


def test_a_seat_turn_runs_on_her_own_chat_and_says_why_when_silent(world, monkeypatch, caplog):
    """The cadence lane on HER chat (keep_prompt, quiet, the room's brain), painted here
    under her name. No line in the room when she is silent, Krem's rule - but the journal
    and the END event name the reason: tools and no words, the model's token ceiling, the
    pass word. Her line carries the tools she used, lifted from her own chat's rows."""
    caplog.set_level(logging.INFO, logger='core.rooms')
    chat, a, b = _salon(two=False)
    own = f"seat-{a['id']}"
    report = lambda **k: {'type': 'turn_report', 'text': '', 'tools': [], 'finish': None, 'why': '', **k}
    # tools, then no words
    out, end, rows, seen = _seat_turn_with(world, monkeypatch, chat, [
        {'type': 'tool_start', 'id': 't1', 'name': 'read_self', 'args': {}},
        {'type': 'tool_end', 'id': 't1', 'name': 'read_self', 'result': '...', 'error': False},
        report(tools=['read_self', 'search_memory'], why='ran read_self, search_memory and ended without words')])
    assert out == '' and rows == []
    assert end['dropped'] is True and end['tools'] == ['read_self', 'search_memory'] and end['speaker'] == a['id']
    assert end['why'] == 'ran read_self, search_memory and ended without words'
    assert seen['chat'] == own and seen['keep_prompt'] is True and seen['quiet'] is True and seen['source'] == 'room'
    assert seen['brain']['chat'] == own and seen['cue'].startswith('(Nobody has spoken yet.)')
    assert seen['wire'] == ['voice_turn_start', 'voice_turn_tool', 'voice_turn_tool', 'voice_turn_end']
    # the model's ceiling
    out, end, rows, _ = _seat_turn_with(world, monkeypatch, chat, [
        report(finish='length', why='the model hit its reply token limit (finish=length) before any words')])
    assert out == '' and rows == [] and end['dropped'] is True
    assert end['why'] == 'the model hit its reply token limit (finish=length) before any words'
    # the pass word: her own choice, dropped on the wire too (the swap used to paint the
    # previous bot's line for a text that has no row)
    out, end, rows, _ = _seat_turn_with(world, monkeypatch, chat, [report(text='(pass)')], text='(pass)')
    assert out == '' and rows == [] and end['dropped'] is True and end['text'] == '' and end['why'] == "said: '(pass)'"
    # words: a line, with the tools she used as its footnote - lifted from HER chat's rows,
    # inputs off the tool_calls row and results off the tool row
    hers = [{'role': 'user', 'content': 'cue'},
            {'role': 'assistant', 'content': '', 'tool_calls': [
                {'id': 'c1', 'type': 'function', 'function': {'name': 'web_search', 'arguments': '{"q": "boats"}'}},
                {'id': 'c2', 'type': 'function', 'function': {'name': 'read_self', 'arguments': ''}}]},
            {'role': 'tool', 'tool_call_id': 'c1', 'name': 'web_search', 'content': 'Boats are good.'},
            {'role': 'tool', 'tool_call_id': 'c2', 'name': 'read_self', 'content': 'You are Sapphire.'},
            {'role': 'assistant', 'content': 'Found it.'}]
    out, end, rows, _ = _seat_turn_with(world, monkeypatch, chat, [
        {'type': 'content', 'text': 'Found it.'}, report(text='Found it.', tools=['web_search', 'read_self'])],
        text='Found it.', rows_after=hers)
    assert out == 'Found it.' and rows[-1]['content'] == 'Found it.' and rows[-1]['speaker'] == a['id']
    assert rows[-1]['tools'] == [{'id': 'c1', 'name': 'web_search', 'args': {'q': 'boats'}, 'result': 'Boats are good.'},
                                 {'id': 'c2', 'name': 'read_self', 'args': {}, 'result': 'You are Sapphire.'}]
    assert end['dropped'] is False and end['why'] == '' and end['tools'] == ['web_search', 'read_self']
    # her chat kept everything the lane saved; her next cue is the lines since her line
    assert world.llm_chat.session_manager.chats[own]['rows'] == hers
    rooms.append_line(chat, 'user', 'Nice.', 'user', 'Krem')
    _, _, _, seen = _seat_turn_with(world, monkeypatch, chat, [report(text='Ta.')], text='Ta.')
    assert seen['cue'] == 'Krem: Nice.\n\n(Your turn, Sapphire.)'
    # a lane that raises (a refused chat, ChatBusy for the inbox to retry) raises here too
    from core import cadence

    def busy(own, cue, **kw):
        raise RuntimeError('turn refused')
    monkeypatch.setattr(cadence, 'run_turn', busy)
    with pytest.raises(RuntimeError):
        rooms._seat_turn(chat, rooms._room(chat)[1]['seats'][0], rooms._rt(chat))
    said = [r.getMessage() for r in caplog.records if 'passed' in r.getMessage()]
    assert said[:3] == ["[ROOM] 'salon': Sapphire passed (ran read_self, search_memory and ended without words)",
                        "[ROOM] 'salon': Sapphire passed (the model hit its reply token limit (finish=length) before any words)",
                        "[ROOM] 'salon': Sapphire passed (said: '(pass)')"]


def test_every_local_seat_has_a_chat_of_her_own(world, monkeypatch):
    """Made with the seat (mode seat, tagged to the room, shown as "room · name", her
    persona's settings on it); renamed with her; wiped with the room; gone with her
    seat; retagged when the room is renamed; gone with the room, BEFORE the row goes
    (chat_deleting); swept when orphaned."""
    sm = world.llm_chat.session_manager
    chat, a, b = _salon()
    own = f"seat-{a['id']}"
    assert a['chat'] == own
    s = sm.chats[own]['settings']
    assert (s['mode'], s['room'], s['seat'], s['persona'], s['private_display_name'], s['private_chat']) == \
           ('seat', chat, a['id'], 'sapphire', 'salon · Sapphire', False)
    assert s['prompt'] == 'sapphire' and s['memory_scope'] == 'default'      # her persona's: a typed turn there is her too
    rooms.update_seat(chat, a['id'], {'name': 'Purple', 'settings': {'llm_model': 'claude'}})
    s = sm.chats[own]['settings']
    assert s['private_display_name'] == 'salon · Purple' and s['llm_model'] == 'claude'
    assert next(x for x in rooms.describe(chat)['seats'] if x['id'] == a['id'])['chat'] == own
    # clear: hers too
    sm.chats[own]['rows'].append({'role': 'user', 'content': 'cue'})
    rooms.append_line(chat, 'user', 'hi', 'user', 'Krem')
    rooms.clear(chat)
    assert sm.chats[chat]['rows'] == [] and sm.chats[own]['rows'] == []
    # remove: gone
    rooms.remove_seat(chat, b['id'])
    assert f"seat-{b['id']}" not in sm.chats and own in sm.chats
    # the room renamed: retagged, reshown
    sm.chats['parlour'] = sm.chats.pop(chat)
    rooms.chat_renamed(chat, 'parlour')
    s = sm.chats[own]['settings']
    assert s['room'] == 'parlour' and s['private_display_name'] == 'parlour · Purple'
    # orphans are swept (a seat that is gone, a room that is gone); hers stays
    sm.chats['seat-ghost'] = {'settings': {'mode': 'seat', 'room': 'parlour', 'seat': 'ghost'}, 'rows': []}
    sm.chats['seat-lost'] = {'settings': {'mode': 'seat', 'room': 'nowhere', 'seat': 'x'}, 'rows': []}
    assert sorted(rooms.sweep()) == ['seat-ghost', 'seat-lost'] and own in sm.chats
    # a seat chat deleted by hand comes back at her turn (seat_chat makes it)
    sm.chats.pop(own)
    s_room, room = rooms._room('parlour')
    assert rooms.seat_chat('parlour', s_room, room['seats'][0]) == own and own in sm.chats
    # the room goes: hers goes first; any other chat: nothing happens
    rooms.chat_deleting('parlour')
    assert own not in sm.chats
    rooms.chat_deleting('not-a-room')



def test_stop_abandons_her_answer(world, monkeypatch):
    chat, a, b = _salon(two=False)
    gate = threading.Event()

    class SlowDoor(FakeDoor):
        def cue(self, *args):
            self.cues.append(args)
            gate.wait(timeout=3)
            return 'too late'
    door = _with_door(monkeypatch, SlowDoor())
    seat = rooms.invite_sapphire(chat, 'prime', name='Prime')['seat']
    rooms.update_seat(chat, a['id'], {'enabled': False})
    rows = world.llm_chat.session_manager.chats[chat]['rows']
    rooms.say(chat, 'Go.')
    assert _wait(lambda: len(door.cues) == 1)
    rooms.stop(chat)
    assert _wait(lambda: not rooms.state(chat)['running'])
    # her worker is still waiting on her door: the next round skips her with a line
    rooms.say(chat, 'Again.')
    assert _wait(lambda: any('still answering' in r['content'] for r in rows))
    assert len(door.cues) == 1 and not rooms.state(chat)['running']
    gate.set()
    time.sleep(0.1)
    assert [r['speaker'] for r in rows] == ['user', 'user', 'room', 'room']    # her late answer is dropped
    assert 'too late' not in ' '.join(r['content'] for r in rows)


def test_a_round_nobody_spoke_in_stops_the_run(world, monkeypatch):
    chat, a, b = _salon()
    rooms.update_room(chat, {'human_every': 0})                              # the case that used to spin forever
    calls = []

    def passing(chat_, seat, rt):
        calls.append(seat['name'])
        return ''
    monkeypatch.setattr(rooms, '_seat_turn', passing)
    rows = world.llm_chat.session_manager.chats[chat]['rows']
    rooms.start(chat)
    assert _wait(lambda: not rooms.state(chat)['running'])
    assert calls == ['Sapphire', 'Sapphire 2']                                # one round, not one more
    assert rows[-1]['speaker'] == 'room' and rows[-1]['content'].startswith('Nobody spoke this round')
    rooms.say(chat, 'wake up')                                               # the person's line starts it again
    assert _wait(lambda: len(calls) == 4)


def test_the_same_sapphire_is_seated_once(world, monkeypatch):
    chat, a, b = _salon(two=False)
    door = _with_door(monkeypatch, FakeDoor())
    rooms.invite_sapphire(chat, 'prime', name='Prime')
    with pytest.raises(rooms.RoomError, match='already seated'):
        rooms.invite_sapphire(chat, 'prime')
    assert len(door.cards) == 1                                              # her door was not asked twice


def test_import_writes_the_record_only_after_the_transcript_took(world, monkeypatch):
    chat, a, b = _salon()
    sm = world.llm_chat.session_manager
    monkeypatch.setattr(sm, 'replace_messages', lambda *a, **k: (False, 'Chat is streaming'))
    with pytest.raises(rooms.RoomError, match='streaming'):
        rooms.import_room(chat, {'messages': [], 'room': {'purpose': 'Replaced', 'seats': []}})
    d = rooms.describe(chat)
    assert d['purpose'] == 'Talk about the boat.' and len(d['seats']) == 2    # untouched: the record waits for the rows


def test_a_speakerless_row_is_labelled_the_rooms(world):
    chat, a, b = _salon()
    _, room = rooms._room(chat)
    rows = [{'role': 'user', 'content': 'Summary of earlier talk.'},           # a compress summary
            {'role': 'assistant', 'content': 'Fine.', 'speaker': a['id']}]
    hist, cue = rooms.projection(rows, {'id': b['id'], 'name': 'Sapphire 2'}, rooms._names(room))
    assert hist == [] and cue.startswith('Room: Summary of earlier talk.\n\nSapphire: Fine.')
