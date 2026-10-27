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
    monkeypatch.setattr(inbox, '_seat_room', lambda c: _fake_seat_room(sysm, c))
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


def _fake_seat_room(sysm, c):
    s = sysm.llm_chat.session_manager.read_chat_settings(c)
    return s.get('room') if isinstance(s, dict) and s.get('mode') == 'seat' else None


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
    hist, cue, _ = rooms.projection(rows, {'id': a['id'], 'name': 'Sapphire'}, rooms._names(room))
    assert hist == [{'role': 'user', 'content': 'Krem: Evening, both.'},
                    {'role': 'assistant', 'content': 'Evening, Krem.'}]
    assert cue == 'Sapphire 2: Hi.\n\nKrem: Boat talk?\n\nVisitor: hello from outside\n\n(Your turn, Sapphire.)'
    # the twin sees Sapphire's line as someone else's, never as its own
    hist_b, cue_b, _ = rooms.projection(rows, {'id': b['id'], 'name': 'Sapphire 2'}, rooms._names(room))
    assert hist_b[0]['content'] == 'Krem: Evening, both.\n\nSapphire: Evening, Krem.'
    assert hist_b[1] == {'role': 'assistant', 'content': 'Hi.'}
    assert cue_b.endswith('(Your turn, Sapphire 2.)')
    # nothing said yet
    _, cue0, _ = rooms.projection([], {'id': a['id'], 'name': 'Sapphire'}, rooms._names(room))
    assert cue0.startswith('(Nobody has spoken yet.)')


# ── the brain ────────────────────────────────────────────────────────────────

def test_seat_brain_scopes_tools_context_privacy(world):
    from core.chat.function_manager import scope_setting_keys
    chat, a, b = _salon()
    rooms.update_seat(chat, a['id'], {'context': 'You are the optimist.'})
    world.llm_chat.session_manager.chats[chat]['settings']['private_chat'] = True
    s_room, room = rooms._room(chat)
    seat = next(s for s in room['seats'] if s['id'] == a['id'])
    brain, cue, _ = rooms.seat_brain(chat, s_room, room, seat)
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
    assert ("House rules: the bots take a round after each of Krem's lines; no round cap; round robin; name a seat as "
            "@Name and it answers next, whoever wrote the line, and a chain of mentions stops after 3 mentioned turns "
            "since Krem's last line; nothing to add = (pass) or <<PASS>>.") in brain['system_prompt']   # every seat sees the rules (2026-10-13)
    assert 'You are Sapphire. Also here: Krem, Sapphire 2 (sapphire).' in brain['system_prompt']
    assert 'This is your first turn here: read your self-sheet (read_self)' in brain['system_prompt']
    assert brain['history'] == [] and cue.startswith('(Nobody has spoken yet.)')
    # her second turn: her chat has her rows, the first-turn line is gone
    world.llm_chat.session_manager.chats[brain['chat']]['rows'].append({'role': 'assistant', 'content': 'Hi.'})
    brain, _, _ = rooms.seat_brain(chat, s_room, room, seat)
    assert 'first turn' not in brain['system_prompt'] and brain['history'] == [{'role': 'assistant', 'content': 'Hi.'}]
    # memory writes on for THIS seat only
    rooms.update_seat(chat, a['id'], {'memory_write': True})
    s_room, room = rooms._room(chat)
    seat = next(s for s in room['seats'] if s['id'] == a['id'])
    brain, _, _ = rooms.seat_brain(chat, s_room, room, seat)
    names = {t['function']['name'] for t in brain['tools']}
    assert names == {'search_memory', 'read_self', 'save_memory', 'save_goal', 'web_search'}
    # a 'none' toolset is no tools at all
    s_room, room = rooms._room(chat)
    c = rooms.add_seat(chat, 'cobalt')
    brain, _, _ = rooms.seat_brain(chat, s_room, rooms._room(chat)[1], next(s for s in rooms._room(chat)[1]['seats'] if s['id'] == c['id']))
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
    # a seat's own chat takes nothing from outside the room: a door's turn or a line put
    # there would run her bare (no room block, no tool fence) into her seat history; the
    # Chat Manager opens her row as her room and the Chat view steers off it (Krem 2026-10-13)
    own = f"seat-{a['id']}"
    for item in (inbox.Item(run=lambda: 'typed', source='web', lane='now'),
                 inbox.Item(text='psst', source='mcp:1.2.3.4', lane='now')):
        with pytest.raises(inbox.InboxRefused, match="own seat in the chat room 'salon'"):
            inbox.put(own, item)
    from pathlib import Path
    static = Path(__file__).resolve().parent.parent / 'interfaces' / 'web' / 'static'
    assert "if (mode === 'seat' && c?.settings?.room)" in (static / 'views' / 'chat-manage.js').read_text(encoding='utf-8')
    assert "!['game', 'room', 'seat'].includes(active.mode)" in (static / 'views' / 'chat.js').read_text(encoding='utf-8')
    route = (Path(__file__).resolve().parent.parent / 'core' / 'routes' / 'chat.py').read_text(encoding='utf-8')
    assert "keep = ('mode', 'game_id', 'room'," in route                  # the seat row knows its room
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

    def hang(chat_, seat, rt, prep=None):
        rt.streams[seat['id']] = stream
        started.set()
        _wait(lambda: stream.cancel_flag, timeout=3)
        rt.streams.pop(seat['id'], None)
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
    assert (d['human_every'], d['pause_s'], d['max_rounds'], d['bots_enabled'], d['shuffle'], d['blind']) == (1, 0.0, 0, True, False, False)
    assert d['person'] == 'Krem' and d['guests'] == []
    rooms.update_room(chat, {'human_every': '3', 'pause_s': 1.5, 'max_rounds': -4, 'shuffle': 1, 'blind': 1, 'purpose': 'New'})
    d = rooms.describe(chat)
    assert (d['human_every'], d['pause_s'], d['max_rounds'], d['shuffle'], d['blind'], d['purpose']) == (3, 1.5, 0, True, True, 'New')
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
        self.answer, self.fail, self.cards, self.cues, self.tools, self.told, self.briefs = answer, fail, [], [], [], [], []

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

    def reset(self, device, config, secrets, card):
        self.told.append(card)
        if self.fail:
            raise RuntimeError(self.fail)
        return 'kept whole; fresh'

    def update(self, device, config, secrets, card):
        self.briefs.append(card)
        if self.fail:
            raise RuntimeError(self.fail)
        return 'carries the new words'


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
                    'instructions': 'Be the sceptic.', 'private': False, 'name': 'Prime',
                    'rules': rooms._rules_text(rooms._room(chat)[1])}
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
    assert rows[1].pop('k') and rows[1] == {'role': 'assistant', 'content': 'Evening from the server.', 'speaker': seat['id'],
                                            'name': 'Prime', 'kind': 'sapphire', 'tools': [{'name': 'read_self'}], 'seen': 1}
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
    # the room cleared: her box sets her room chat aside and starts a fresh one (room_reset
    # through her door, nothing of hers wiped); closed: set aside only
    rooms.clear(chat)
    assert _wait(lambda: door.told)
    assert door.told == [{'room': 'salon', 'host': 'dev', 'closed': False}]
    rooms.chat_deleting(chat)
    assert _wait(lambda: len(door.told) == 2) and door.told[1] == {'room': 'salon', 'host': 'dev', 'closed': True}


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
            {'role': 'assistant', 'content': '<think>Boats first.</think>', 'tool_calls': [
                {'id': 'c1', 'type': 'function', 'function': {'name': 'web_search', 'arguments': '{"q": "boats"}'}},
                {'id': 'c2', 'type': 'function', 'function': {'name': 'read_self', 'arguments': ''}}]},
            {'role': 'tool', 'tool_call_id': 'c1', 'name': 'web_search', 'content': 'Boats are good.'},
            {'role': 'tool', 'tool_call_id': 'c2', 'name': 'read_self', 'content': 'You are Sapphire.'},
            {'role': 'assistant', 'content': '<think>Two hits.</think>\n\nFound it.'}]
    out, end, rows, _ = _seat_turn_with(world, monkeypatch, chat, [
        {'type': 'content', 'text': 'Found it.'}, report(text='Found it.', tools=['web_search', 'read_self'])],
        text='Found it.', rows_after=hers)
    assert out == 'Found it.' and rows[-1]['speaker'] == a['id']
    # her thinking rides in front of her line, one block per row of hers that carried one, so
    # her row shows what the live bubble showed (the swap used to lose it, Krem 2026-10-13)...
    assert rows[-1]['content'] == '<think>Boats first.</think>\n\n<think>Two hits.</think>\n\nFound it.'
    # ...and never reaches another seat's cue
    assert rooms.projection(rows, {'id': 'zz', 'name': 'Zed'}, {a['id']: 'Sapphire', 'zz': 'Zed'})[1] \
        == 'Sapphire: Found it.\n\n(Your turn, Zed.)'
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
    # remove: gone - and the seat that is SPEAKING has her turn cut first (the store
    # refuses to delete a chat mid-stream; hers used to stay behind until the next boot)
    rt = rooms._rt(chat)
    rt.streams[b['id']] = type('S', (), {'cancel_flag': False})()
    rooms.remove_seat(chat, b['id'])
    assert rt.streams[b['id']].cancel_flag is True and f"seat-{b['id']}" not in sm.chats and own in sm.chats
    rt.streams.clear()
    # a seat whose record cannot be written takes its chat back out: no chat without a seat
    write = rooms._write
    monkeypatch.setattr(rooms, '_write', lambda c, r: (_ for _ in ()).throw(rooms.RoomError('no')))
    with pytest.raises(rooms.RoomError):
        rooms.add_seat(chat, 'cobalt')
    assert not [n for n in sm.chats if n.startswith('seat-') and n != own]
    monkeypatch.setattr(rooms, '_write', write)
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



def test_her_silent_turn_says_why_on_the_wire_and_the_view_shows_it(world, monkeypatch):
    """A guest's END carries `why` like a local seat's; the room view listens for a
    dropped room turn and says "<name> passed" in the strip until the next seat
    speaks (the vanishing bubble read as a lost line twice, 2026-10-12)."""
    chat, a, b = _salon(two=False)
    door = _with_door(monkeypatch, FakeDoor(answer='(pass)'))
    seat = rooms.invite_sapphire(chat, 'prime', name='Prime')['seat']
    published = []
    monkeypatch.setattr(rooms, 'publish', lambda et, data=None, **k: published.append((et, data)))
    s_room, room = rooms._room(chat)
    assert rooms._guest_turn(chat, next(s for s in room['seats'] if s['id'] == seat['id']), rooms._rt(chat)) == ''
    end = next(d for et, d in published if et == Events.VOICE_TURN_END)
    assert end['dropped'] is True and end['why'] == "her door said: '(pass)'" and end['tools'] == []
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / 'interfaces' / 'web' / 'static' / 'views' / 'rooms.js').read_text(encoding='utf-8')
    assert "eventBus.on('voice_turn_end', onTurnEnd)" in js and "eventBus.off('voice_turn_end', onTurnEnd)" in js
    assert "d?.source !== 'room' || !d?.speaker) return;" in js and "`${R.passed.name} passed`" in js
    assert "if (d.turn) R.passed = null;" in js
    # the cadence block (Krem's sketch 2026-10-13): a heading with the gear, then one row:
    # turns, speaking order, play. The turns select maps to the rules
    # (none = bots off, N = human every N, 0 = until you stop them); Stop is the chat bar's
    # (the cancel route stops a room); skip-me and the toggle are gone
    assert 'class="rm-sec-title">Bot turns per round</label>' in js
    assert js.index('rm-sec-title') < js.index('id="rm-settings"') < js.index('id="rm-rounds"') < js.index('id="rm-order"') < js.index('id="rm-go"')
    # the roster is a headed section of its own under the cadence block (Krem 2026-10-13)
    assert 'class="rm-sec-title">Roster</label>' in js
    assert js.index('<div class="rm-run">') < js.index('id="rm-rounds"') and js.index('id="rm-go"') < js.index('>Roster</label>') < js.index('id="rm-add-persona"')
    assert 'id="rm-rounds"' in js and '<option value="off">bots off</option>' in js and '<option value="2">2 turns</option>' in js
    assert "updateRoom(v === 'off' ? { bots_enabled: false } : { bots_enabled: true, human_every:" in js
    assert 'id="rm-go" class="sb-btn-sm" title="One round: every bot takes one more turn, without you">&#x25B6;</button>' in js
    assert js.count('id="rm-settings"') == 1 and js.index('sb-chat-header') < js.index('rm-quick') < js.index('id="rm-settings"')
    for gone in ('rm-skip', 'rm-human-every', 'id="rm-bots"', 'rm-stop', 'One round<', 'rm-field'):
        assert gone not in js, gone
    # the rail says who is working until her words arrive, and an untracked room END
    # never leaves Stop lit (Krem 2026-10-12)
    main = (Path(__file__).resolve().parent.parent / 'interfaces' / 'web' / 'static' / 'main.js').read_text(encoding='utf-8')
    assert "ui.updateStatus(`${data.name || 'a bot'} is generating...${data.kind === 'sapphire' ? ' (on her machine)' : ''}`)" in main
    assert "if (data?.source === 'room' && data?.text) ui.hideStatus();" in main
    assert "if (data?.source === 'room' && getIsProc() && !getAbortController()) {" in main
    route = (Path(__file__).resolve().parent.parent / 'core' / 'routes' / 'chat.py').read_text(encoding='utf-8')
    assert "if requested_chat and rooms.is_room(requested_chat):" in route and "rooms.stop(requested_chat)" in route
    # the sidebar's x (DELETE /api/history/messages) and the Chat Manager's bulk clear both go
    # through rooms.clear for a room: seat chats wiped, guests reset (Krem hit the gap, 2026-10-12)
    assert route.count("rooms.clear(") >= 2 and "if rooms.is_room(target):" in route


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
    hist, cue, _ = rooms.projection(rows, {'id': b['id'], 'name': 'Sapphire 2'}, rooms._names(room))
    assert hist == [] and cue.startswith('Room: Summary of earlier talk.\n\nSapphire: Fine.')


# ── the blind round ──────────────────────────────────────────────────────────

def test_a_blind_round_cues_every_seat_before_anyone_speaks(world, monkeypatch):
    """`blind` (Krem 2026-10-13: whoever went first swayed the second): every seat's cue
    is read before any of them speaks, then the turns run one after another as ever (two
    streams at once left the second starved on Fireworks). Neither cue holds the other's
    answer; the state names the one speaking; `seen` on each row is where her cue left
    off, so the next round's cues carry both answers."""
    from core import cadence
    chat, a, b = _salon()
    rooms.update_room(chat, {'blind': True})
    rooms.append_line(chat, 'user', 'Boats?', 'user', 'Krem')
    cues, turns, gate, gate2 = {}, [], threading.Event(), threading.Event()

    def run_turn(own, cue, **kw):
        seat = own.split('seat-', 1)[1]
        cues.setdefault(seat, []).append(cue)
        turns.append((seat, rooms.state(chat)['turn']))
        kw['on_stream'](type('S', (), {'cancel_flag': False})())
        if seat == a['id']:
            (gate if len(cues[seat]) == 1 else gate2).wait(timeout=3)  # a is mid-turn: b's cue is already read
        kw['on_event']({'type': 'turn_report', 'text': f'{seat} says', 'tools': [], 'finish': 'stop', 'why': ''})
        return f'{seat} says'
    monkeypatch.setattr(cadence, 'run_turn', run_turn)
    rows = world.llm_chat.session_manager.chats[chat]['rows']
    rooms.start(chat)
    assert _wait(lambda: len(turns) == 1) and rooms.state(chat)['turn'] == a['id']
    rooms.say(chat, 'typed while a speaks')                             # lands after the round, like any mid-round line
    gate.set()
    # that line IS the person's turn: no cue of round 1 read it, so round 2 starts at once to
    # answer it instead of waiting for a second message (Krem's ruling A, 2026-10-27)
    assert _wait(lambda: len(cues[a['id']]) == 2)
    assert [r['speaker'] for r in rows] == ['user', a['id'], b['id'], 'user']
    assert turns[:2] == [(a['id'], a['id']), (b['id'], b['id'])]         # one after another, each the one speaking
    # both cues of round 1 were read before either spoke: the person's line, not the other seat's answer
    assert cues[a['id']][0] == 'Krem: Boats?\n\n(Your turn, Sapphire.)'
    assert cues[b['id']][0] == 'Krem: Boats?\n\n(Your turn, Sapphire 2.)'
    assert rows[1]['seen'] == 1 and rows[2]['seen'] == 1                 # where each cue left off
    st = rooms.state(chat)
    assert st['turn'] == a['id'] and st['waiting'] is False
    gate2.set()
    # round 2 is where they read each other and the line typed meanwhile; then the floor is the person's
    assert _wait(lambda: len(cues[b['id']]) == 2 and rooms.state(chat)['waiting'])
    assert cues[a['id']][1] == f"Sapphire 2: {b['id']} says\n\nKrem: typed while a speaks\n\n(Your turn, Sapphire.)"
    assert cues[b['id']][1] == f"Sapphire: {a['id']} says\n\nKrem: typed while a speaks\n\n(Your turn, Sapphire 2.)"
    rooms.stop(chat)
    assert _wait(lambda: not rooms.state(chat)['running'])
    # the sidebar's speaking order offers it (not the gear, which nobody found); the rules carry it
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / 'interfaces' / 'web' / 'static' / 'views' / 'rooms.js').read_text(encoding='utf-8')
    assert '<option value="blind">' in js and "updateRoom({ shuffle: v === 'shuffle', blind: v === 'blind' });" in js
    assert "$('#rm-order').value = info.blind ? 'blind' : info.shuffle ? 'shuffle' : 'fixed';" in js
    assert "id: 'order'" not in js and 'data.order' not in js
    main = (Path(__file__).resolve().parent.parent / 'interfaces' / 'web' / 'static' / 'main.js').read_text(encoding='utf-8')
    assert 'parallel' not in main and '_roomLive' not in main           # one live bubble at a time, as ever
    # projection: `seen` on her row is where her cue left off, so the other answers of her
    # round reach her next cue; a stale one (a row deleted before it) falls back to the
    # lines since her line; rows without one (one at a time, imports) read as before
    rws = [{'role': 'user', 'content': 'q', 'speaker': 'user', 'name': 'Krem'},
           {'role': 'assistant', 'content': 'b1', 'speaker': 'B', 'name': 'Bee', 'seen': 1},
           {'role': 'assistant', 'content': 'a1', 'speaker': 'A', 'name': 'Ay', 'seen': 1},
           {'role': 'user', 'content': 'more', 'speaker': 'user', 'name': 'Krem'}]
    nm = {'A': 'Ay', 'B': 'Bee', 'user': 'Krem'}
    assert rooms.projection(rws, {'id': 'A', 'name': 'Ay'}, nm)[1] == 'Bee: b1\n\nKrem: more\n\n(Your turn, Ay.)'
    assert rooms.projection(rws, {'id': 'B', 'name': 'Bee'}, nm)[1] == 'Ay: a1\n\nKrem: more\n\n(Your turn, Bee.)'
    rws[2]['seen'] = 7
    assert rooms.projection(rws, {'id': 'A', 'name': 'Ay'}, nm)[1] == 'Krem: more\n\n(Your turn, Ay.)'
    del rws[2]['seen']
    assert rooms.projection(rws, {'id': 'A', 'name': 'Ay'}, nm)[1] == 'Krem: more\n\n(Your turn, Ay.)'


def test_stop_ends_a_blind_round_before_the_next_seat(world, monkeypatch):
    chat, a, b = _salon()
    rooms.update_room(chat, {'blind': True})
    turns, stream = [], type('S', (), {'cancel_flag': False})()

    def hang(chat_, seat, rt, prep=None):
        turns.append(seat['id'])
        rt.streams[seat['id']] = stream
        _wait(lambda: stream.cancel_flag, timeout=3)
        rt.streams.pop(seat['id'], None)
        return ''
    monkeypatch.setattr(rooms, '_seat_turn', hang)
    monkeypatch.setattr(rooms, '_prep', lambda chat_, seat: {'brain': None, 'cue': '', 'seen': 0})
    rooms.start(chat)
    assert _wait(lambda: turns == [a['id']])
    rooms.stop(chat)
    assert stream.cancel_flag is True
    assert _wait(lambda: not rooms.state(chat)['running'])
    assert turns == [a['id']] and world.llm_chat.session_manager.chats[chat]['rows'] == []


def test_a_seats_row_is_in_before_her_end_goes_out(world, monkeypatch):
    """The END used to go out first and the row land after: the browser's swap had
    500 ms of luck to find it. Now the row is written, then the END (both kinds of seat)."""
    from core import cadence
    chat, a, b = _salon(two=False)
    order = []
    rows = world.llm_chat.session_manager.chats[chat]['rows']

    def run_turn(own, cue, **kw):
        kw['on_stream'](type('S', (), {'cancel_flag': False})())
        kw['on_event']({'type': 'turn_report', 'text': 'Hi.', 'tools': [], 'finish': 'stop', 'why': ''})
        return 'Hi.'
    monkeypatch.setattr(cadence, 'run_turn', run_turn)
    monkeypatch.setattr(rooms, 'publish', lambda et, data=None, **k: order.append((et, len(rows))))
    rooms._seat_turn(chat, rooms._room(chat)[1]['seats'][0], rooms._rt(chat))
    assert (Events.VOICE_TURN_END, 1) in order                           # her row was there when END went out
    door = _with_door(monkeypatch, FakeDoor(answer='Evening.'))
    seat = rooms.invite_sapphire(chat, 'prime', name='Prime')['seat']
    order.clear()
    rooms._guest_turn(chat, next(s for s in rooms._room(chat)[1]['seats'] if s['id'] == seat['id']), rooms._rt(chat))
    assert (Events.VOICE_TURN_END, 2) in order


def test_a_picture_on_a_line_reaches_every_seat_and_stays_with_her(world, monkeypatch):
    """Krem 2026-10-13: "to see what each bot sees in an image". The line's picture is
    stored under the ROOM chat (one owner per image) and sits as a part beside the words
    on the row; a picture alone is a line; the cue labels it and carries the pixels to a
    local seat; her turn keeps her own copy (keep_images) so a cleared room never takes
    the picture out of her chat; a guest's door gets the words only."""
    import base64
    from core import images as store
    kept = []

    def stash(raw, mt=None, *, visible=False, chat_name=None):
        kept.append((raw, mt, visible, chat_name))
        return f"img:k{len(kept)}"
    monkeypatch.setattr(store, 'stash', stash)
    monkeypatch.setattr(store, 'resolve', lambda h: type('R', (), {'data': b'PIX-' + h.encode(), 'media_type': 'image/png'})())
    chat, a, b = _salon()
    rooms.update_seat(chat, a['id'], {'enabled': False})
    rooms.update_seat(chat, b['id'], {'enabled': False})           # lines land, nobody runs
    rows = world.llm_chat.session_manager.chats[chat]['rows']
    with pytest.raises(rooms.RoomError, match='Say something'):
        rooms.say(chat, '  ', images=['junk', {'media_type': 'image/png'}])
    png = base64.b64encode(b'PNG').decode('ascii')
    rooms.say(chat, '', images=[{'data': png, 'media_type': 'image/png'}, 'junk'])
    assert _wait(lambda: len(rows) == 1)
    assert rows[0]['content'] == [{'type': 'image', 'media_type': 'image/png', 'handle': 'img:k1'}]
    assert kept == [(b'PNG', 'image/png', True, chat)]
    rooms.say(chat, 'Look.', images=[{'data': png, 'media_type': 'image/jpeg'}])
    assert _wait(lambda: len(rows) == 2)
    assert rows[1]['content'] == [{'type': 'text', 'text': 'Look.'}, {'type': 'image', 'media_type': 'image/jpeg', 'handle': 'img:k2'}]
    names = rooms._names(rooms._room(chat)[1])
    seat_a = next(s for s in rooms._room(chat)[1]['seats'] if s['id'] == a['id'])
    out, cue, parts = rooms.projection(rows, seat_a, names)
    assert out == []                                           # her view: nothing of hers yet, the cue carries the rest
    assert cue == 'Krem: (an image)\n\nKrem: Look.\n\n(Your turn, Sapphire.)'
    assert [p['handle'] for p in parts] == ['img:k1', 'img:k2']
    assert '(an image)' in rooms._guest_cue(chat, rooms._room(chat)[1], seat_a)
    # her turn: the pixels ride the cue and her chat keeps her own copy
    report = {'type': 'turn_report', 'text': 'A boat.', 'tools': [], 'finish': 'stop', 'why': ''}
    out, end, rows, seen = _seat_turn_with(world, monkeypatch, chat, [report], text='A boat.')
    assert out == 'A boat.' and seen['keep_images'] is True
    assert seen['images'] == [{'data': base64.b64encode(b'PIX-img:k1').decode('ascii'), 'media_type': 'image/png'},
                              {'data': base64.b64encode(b'PIX-img:k2').decode('ascii'), 'media_type': 'image/png'}]
    # a picture the store no longer has is left out of the cue, not a crash
    monkeypatch.setattr(store, 'resolve', lambda h: (_ for _ in ()).throw(RuntimeError('gone')))
    assert rooms._prep(chat, seat_a)['images'] == []
    # a picture the store refuses rides inline on the row
    monkeypatch.setattr(store, 'stash', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('sealed')))
    rooms.say(chat, '', images=[{'data': png, 'media_type': 'image/png'}])
    assert _wait(lambda: len(rows) == 4)
    assert rows[3]['content'] == [{'type': 'image', 'media_type': 'image/png', 'data': png}]
    assert rooms._pixels(rows[3]['content'][0]) == {'data': png, 'media_type': 'image/png'}
    # the doors: the route takes `images` beside `text`; the composer's staged pictures go with the line
    from pathlib import Path
    route = (Path(__file__).resolve().parent.parent / 'core' / 'routes' / 'rooms.py').read_text(encoding='utf-8')
    assert "rooms.say, chat, body.get('text'), images=images" in route and 'up to 8 of {data, media_type}' in route
    js = (Path(__file__).resolve().parent.parent / 'interfaces' / 'web' / 'static' / 'views' / 'rooms.js').read_text(encoding='utf-8')
    assert 'const images = Images.getImagesForApi();' in js and "images.length ? { text: txt, images } : { text: txt }" in js
    assert 'Images.clearPendingUploadImages(); updateImagePreviewArea();' in js


def test_a_room_change_reaches_a_guests_box_and_a_seat_can_be_muted(world, monkeypatch):
    """J15/J16 closed (Krem 2026-10-13): a new purpose, or new instructions for a guest,
    go through her door (room_update) and she re-writes the room block in her chat;
    an unchanged purpose, a rule, or a local seat's instructions send nothing; a door
    that fails is a notice in the room. And every seat has a TTS switch (`tts`, on by
    default) the browser's voice queue honours."""
    door = _with_door(monkeypatch, FakeDoor())
    chat, a, b = _salon()
    g = rooms.invite_sapphire(chat, 'prime', name='Prime')['seat']
    rows = world.llm_chat.session_manager.chats[chat]['rows']
    rooms.update_room(chat, {'purpose': 'Boats only.'})
    assert _wait(lambda: len(door.briefs) == 1)
    assert door.briefs[0] == {'room': 'salon', 'host': 'dev', 'purpose': 'Boats only.',
                              'people': ['Krem', 'Sapphire', 'Sapphire 2', 'Prime'], 'instructions': '',
                              'rules': rooms._rules_text(rooms._room(chat)[1])}          # the rules ride on her card too
    rooms.update_room(chat, {'purpose': 'Boats only.', 'pause_s': 1})                 # nothing new for her
    rooms.update_seat(chat, a['id'], {'context': 'You are the sceptic.'})           # a local seat: hers alone
    time.sleep(0.1)
    assert len(door.briefs) == 1
    rooms.update_seat(chat, g['id'], {'context': 'Be brief.'})
    assert _wait(lambda: len(door.briefs) == 2)
    assert door.briefs[1]['instructions'] == 'Be brief.' and door.briefs[1]['purpose'] == 'Boats only.'
    assert next(s for s in rooms.describe(chat)['seats'] if s['id'] == g['id'])['context'] == 'Be brief.'
    door.fail = 'her door is down'
    rooms.update_room(chat, {'purpose': 'Boats and planes.'})
    assert _wait(lambda: any(r.get('speaker') == rooms.NOTICE and 'could not be told' in str(r.get('content')) for r in rows))
    # the TTS switch
    d = rooms.describe(chat)
    assert all(s['tts'] is True for s in d['seats'])
    rooms.update_seat(chat, a['id'], {'tts': 0})
    rooms.update_seat(chat, g['id'], {'tts': False})
    d = {s['id']: s['tts'] for s in rooms.describe(chat)['seats']}
    assert d[a['id']] is False and d[g['id']] is False and d[b['id']] is True
    from pathlib import Path
    static = Path(__file__).resolve().parent.parent / 'interfaces' / 'web' / 'static'
    js = (static / 'views' / 'rooms.js').read_text(encoding='utf-8')
    assert js.count('data-f="tts"') == 3 and "SEAT_SECTIONS = ['prompt', 'toolset', 'brain', 'voice', 'mind']" in js
    assert 'speakLine(seatOf(d.speaker), d.text, d.heard);' in js and 'audio.isTtsPlaying()' in js and 'hush();' in js
    assert 'R = { chat, info, placeholder: null, mute: 0, voiceGen: 0 };' in js     # undefined === 0 kept every line silent (2026-10-13)
    assert "audio.playTextStreaming(text, voice, me.chat, { speed: st.speed, pitch: st.pitch })" in js
    assert '<textarea data-f="context" rows="3" placeholder="Goes to her box' in js     # a guest\'s brief is editable now
    audio_js = (static / 'audio.js').read_text(encoding='utf-8')
    assert 'playTextStreaming = async (text, voice = null, chat = null, opts = null)' in audio_js
    core = Path(__file__).resolve().parent.parent / 'core'
    assert "speed_override=speed, pitch_override=pitch" in (core / 'routes' / 'tts.py').read_text(encoding='utf-8')
    assert "self._speed = speed_override or" in (core / 'tts' / 'stream_pump.py').read_text(encoding='utf-8')
    drv = (core / 'devices' / 'drivers' / 'sapphire.py').read_text(encoding='utf-8')
    assert "_call('room_update', card, config, secrets, UPDATE_WAIT)" in drv


def test_the_room_is_the_record_and_a_seats_chat_follows_its_edits(world, monkeypatch):
    """Krem 2026-10-13: he deleted his question and both answers, asked again, and both
    bots still remembered the question - it sat in their own chats. Now her turn's rows
    ride on her room row (`turn`), and a room edited under her (a line gone, an edit, an
    import) rebuilds her chat from the room before her next turn: the others' lines as the
    cues she was handed (what each held then, by `seen`), her turns as the rows they saved,
    pictures pointing at the room's. An append never rebuilds."""
    chat, a, b = _salon()
    rooms.update_seat(chat, b['id'], {'enabled': False})
    own = f"seat-{a['id']}"
    sm = world.llm_chat.session_manager
    rows = sm.chats[chat]['rows']
    report = lambda **k: {'type': 'turn_report', 'text': '', 'tools': [], 'finish': 'stop', 'why': '', **k}
    rooms.append_line(chat, 'user', 'Who won?', 'user', 'Krem')
    first = [{'role': 'user', 'content': 'Krem: Who won?\n\n(Your turn, Sapphire.)'},
             {'role': 'assistant', 'content': '<think>Lookup.</think>',
              'tool_calls': [{'id': 'c1', 'type': 'function', 'function': {'name': 'web_search', 'arguments': '{}'}}]},
             {'role': 'tool', 'tool_call_id': 'c1', 'name': 'web_search', 'content': 'The Reds.'},
             {'role': 'assistant', 'content': '<think>Got it.</think>\n\nThe Reds won.'}]
    out, _, _, _ = _seat_turn_with(world, monkeypatch, chat, [report(text='The Reds won.', tools=['web_search'])],
                                   text='The Reds won.', rows_after=first)
    assert out == 'The Reds won.' and rows[-1]['turn'] == first[1:] and len(rows[-1]['k']) == 8
    assert sm.chats[own]['rows'] == first                                  # her chat as the lane left it
    assert sm.chats[own]['settings']['room_sync'] == [rooms._key(rows[0])]  # synced at her cue: the room as it stood
    # an append never rebuilds: her chat keeps the lane's rows exactly
    rooms.append_line(chat, 'user', 'And the score?', 'user', 'Krem')
    second = [{'role': 'user', 'content': 'Krem: And the score?\n\n(Your turn, Sapphire.)'}, {'role': 'assistant', 'content': 'Three nil.'}]
    _seat_turn_with(world, monkeypatch, chat, [report(text='Three nil.')], text='Three nil.', rows_after=second)
    assert sm.chats[own]['rows'] == first + second
    assert [r.get('speaker') for r in rows] == ['user', a['id'], 'user', a['id']]
    # Krem deletes the question and its answer from the room, then asks again...
    del rows[0:2]
    rooms.append_line(chat, 'user', 'Who won, again?', 'user', 'Krem')
    _, _, _, seen = _seat_turn_with(world, monkeypatch, chat, [report(text='The Reds.')], text='The Reds.',
                                    rows_after=[{'role': 'user', 'content': 'x'}, {'role': 'assistant', 'content': 'The Reds.'}])
    # ...and her chat was rebuilt from the room before her turn: the deleted question and the turn that answered it are gone
    hers = sm.chats[own]['rows']
    assert hers[:2] == [{'role': 'user', 'content': 'Krem: And the score?\n\n(Your turn, Sapphire.)'}, {'role': 'assistant', 'content': 'Three nil.'}]
    assert 'Who won?' not in str(hers) and seen['cue'] == 'Krem: Who won, again?\n\n(Your turn, Sapphire.)'
    assert sm.chats[own]['settings']['room_sync'] == [rooms._key(r) for r in rows[:3]]
    # the view by hand: a blind round's `seen` keeps a line that landed during her turn out of that cue and in the
    # next; a picture rides as the room's part; a row from before turns were kept is her line alone; a silent
    # stretch is the cue it was; a stale `seen` falls back to the lines before her line
    names = {a['id']: 'Sapphire', 'B': 'Bee', 'user': 'Krem'}
    img = {'type': 'image', 'media_type': 'image/png', 'handle': 'img:r1'}
    rws = [{'role': 'user', 'content': [{'type': 'text', 'text': 'Look.'}, img], 'speaker': 'user', 'name': 'Krem'},
           {'role': 'assistant', 'content': 'b1', 'speaker': 'B', 'name': 'Bee', 'seen': 1},
           {'role': 'assistant', 'content': 'a1', 'speaker': a['id'], 'name': 'Sapphire', 'seen': 1},
           {'role': 'assistant', 'content': 'a2', 'speaker': a['id'], 'name': 'Sapphire', 'seen': 3,
            'turn': [{'role': 'assistant', 'content': '<think>t</think>\n\na2'}]},
           {'role': 'user', 'content': 'More.', 'speaker': 'user', 'name': 'Krem'},
           {'role': 'assistant', 'content': 'a3', 'speaker': a['id'], 'name': 'Sapphire', 'seen': 99}]
    assert rooms._view(rws, {'id': a['id'], 'name': 'Sapphire'}, names) == [
        {'role': 'user', 'content': [{'type': 'text', 'text': 'Krem: Look.\n\n(Your turn, Sapphire.)'}, img]},
        {'role': 'assistant', 'content': 'a1'},
        {'role': 'user', 'content': 'Bee: b1\n\n(Your turn, Sapphire.)'},
        {'role': 'assistant', 'content': '<think>t</think>\n\na2'},
        {'role': 'user', 'content': 'Krem: More.\n\n(Your turn, Sapphire.)'},
        {'role': 'assistant', 'content': 'a3'}]
    assert rooms._view([{'role': 'assistant', 'content': 'hi', 'speaker': a['id']}], {'id': a['id'], 'name': 'Sapphire'}, names) == [
        {'role': 'user', 'content': '(Nobody has spoken yet.)\n\n(Your turn, Sapphire.)'}, {'role': 'assistant', 'content': 'hi'}]


def test_the_rail_comes_home_empty_and_the_chat_view_paints_on_show():
    """Krem 2026-10-13 (and 2026-10-12): back from the room, the Chat page showed the
    room's transcript under the right picker. Root: the rail is one DOM organ lent by
    name; it carried the room's rows home, the Chat view never repainted on show, and
    this tab's own pointer return publishes no CHAT_SWITCHED to repaint on. Now a
    hand-over wipes the transcript both ways, the Chat view paints the picker's chat
    every time it opens, and the steer waits for a pending pointer move before it
    reads the list (the exit's return and the steer used to race for the pointer)."""
    from pathlib import Path
    static = Path(__file__).resolve().parent.parent / 'interfaces' / 'web' / 'static'
    organs = (static / 'surface' / 'organs.js').read_text(encoding='utf-8')
    assert "document.getElementById('chat-container')?.replaceChildren();" in organs
    assert organs.count('    wipe();') == 2 and organs.index('_records = [];\n    wipe();') < organs.index("_holder = ownerId || 'unknown';\n    wipe();")
    chat = (static / 'views' / 'chat.js').read_text(encoding='utf-8')
    assert 'await loadSidebar();\n        // the rail is painted' in chat and 'await refresh(false);\n    },' in chat
    assert 'api.hasPendingActivate(); i++) await new Promise(r => setTimeout(r, 50));' in chat
    assert chat.index('hasPendingActivate') < chat.index('const data = await api.fetchChatList();')


def test_a_satellites_line_is_read_back_there_and_the_chat_view_never_moves(world, monkeypatch):
    """The landing-chat split (Krem 2026-10-13): the room never becomes the active chat;
    it is the LANDING chat while open (views/rooms.js, /api/chats/{chat}/landing), so a
    satellite's words land as the person's line. The bots' lines are then read back on
    that satellite, in the seat's own voice, in order, the round never waiting; the
    browser's queue stays quiet (END carries `heard`); a web line takes the reading back
    to the browser; a muted seat is not read; Stop drops the lines waiting and halts."""
    from core.devices import voice
    said, halted = [], []
    monkeypatch.setattr(voice, 'say', lambda dev, text, chat_settings=None, voice=None, speed=None, pitch=None:
                        (said.append((dev, text, chat_settings.get('mode'), voice, speed, pitch)) or ('ok', True)))
    monkeypatch.setattr(voice, 'halt', lambda dev: halted.append(dev) or True)
    chat, a, b = _salon()
    rooms.update_seat(chat, a['id'], {'enabled': False, 'settings': {'voice': 'af_sky', 'speed': 1.2, 'pitch': 0.9}})
    rooms.update_seat(chat, b['id'], {'enabled': False})           # lines land, no round runs: the turns below are driven by hand
    rows = world.llm_chat.session_manager.chats[chat]['rows']
    report = lambda **k: {'type': 'turn_report', 'text': '', 'tools': [], 'finish': 'stop', 'why': '', **k}
    rooms.say(chat, 'From the kitchen.', source='device:pi')
    assert _wait(lambda: rows and rooms.state(chat)['heard'] == 'device:pi')
    assert rows[-1]['speaker'] == 'user' and rows[-1]['content'] == 'From the kitchen.'
    _, end, _, _ = _seat_turn_with(world, monkeypatch, chat, [report(text='Tea is on.')], text='Tea is on.')
    assert end['heard'] == 'device:pi'
    assert _wait(lambda: len(said) == 1)
    assert said[0] == ('pi', 'Tea is on.', 'room', 'af_sky', 1.2, 0.9)
    # a muted seat is not read; a web line brings the reading back to the browser
    rooms.update_seat(chat, a['id'], {'tts': False})
    _seat_turn_with(world, monkeypatch, chat, [report(text='Quietly.')], text='Quietly.')
    rooms.update_seat(chat, a['id'], {'tts': True})
    rooms.say(chat, 'Typed.')
    assert _wait(lambda: rooms.state(chat)['heard'] is None and any(r.get('content') == 'Typed.' for r in rows))
    _, end, _, _ = _seat_turn_with(world, monkeypatch, chat, [report(text='Back here.')], text='Back here.')
    time.sleep(0.1)
    assert end['heard'] is None and len(said) == 1
    # Stop: the lines waiting go unread and the satellite is halted
    rt = rooms._rt(chat)
    rt.heard = 'device:pi'
    rt.voice.put(('pi', 'never', None, None, None))
    rooms._cut_all(rt)
    assert rt.voice.empty() and halted == ['pi']
    # the browser side: the room lands voice instead of taking the pointer, and hands it back
    from pathlib import Path
    static = Path(__file__).resolve().parent.parent / 'interfaces' / 'web' / 'static'
    js = (static / 'views' / 'rooms.js').read_text(encoding='utf-8')
    assert 'await api.setLanding(chat, true);' in js and 'api.setLanding(was, false)' in js
    assert 'pointerBefore' not in js and 'api.activateChat(' not in js
    assert 'speakLine(seatOf(d.speaker), d.text, d.heard);' in js and 'if (heard) return;' in js
    assert "export const setLanding = (name, on = true)" in (static / 'api.js').read_text(encoding='utf-8')
    route = (Path(__file__).resolve().parent.parent / 'core' / 'routes' / 'chat.py').read_text(encoding='utf-8')
    assert '@router.post("/api/chats/{chat_name}/landing")' in route and '_bound_belt' not in route


# ── a seat at the door (Claude Code's; core/rooms.py DOOR) ───────────────────

def test_a_door_seat_has_no_chat_and_is_found_by_its_persona(world):
    """Krem 2026-10-13: "lets wire you in first". A persona seated AT the door is
    answered by whoever holds an API key that speaks as her (room_wait / room_post on
    the MCP door): no brain here, no chat of her own (nothing to sync or sweep), her
    face and voice the persona's. The door tools find her by the persona's name, case
    apart, and refuse in words otherwise. The preamble names her among the others and
    teaches every seat the pass mark."""
    chat, a, _ = _salon(two=False)
    sm = world.llm_chat.session_manager
    d = rooms.add_seat(chat, 'cobalt', door=True)
    assert d['kind'] == 'door' and d['persona'] == 'cobalt' and d['name'] == 'Cobalt' and 'chat' not in d
    assert not any(s['settings'].get('seat') == d['id'] for s in sm.chats.values())
    assert rooms.sweep() == [] and rooms.describe(chat)['seats'][1]['kind'] == 'door'
    _, room = rooms._room(chat)
    assert rooms._door_for(room, ' COBALT ')['id'] == d['id'] and rooms._door_for(room, 'sapphire') is None
    assert rooms.door_rooms('cobalt') == [{'room': chat, 'you': 'Cobalt', 'purpose': 'Talk about the boat.', 'running': False}]
    assert rooms.door_rooms('sapphire') == []
    with pytest.raises(rooms.RoomError, match='no seat at the door'):
        rooms.door_read('sapphire', chat)
    with pytest.raises(rooms.RoomError, match='speaks as a persona'):
        rooms.door_wait('', chat, 1)
    with pytest.raises(rooms.RoomError, match='no room named'):
        rooms.door_read('cobalt', 'nowhere')
    names = rooms._names(room)
    pre = rooms._preamble(chat, room, next(s for s in room['seats'] if s['id'] == a['id']), names)
    assert 'Also here: Krem, Cobalt.' in pre and 'answer exactly (pass), or put <<PASS>> in your line' in pre
    for words in ('(pass)', ' (Pass). ', 'Nothing to add here. <<PASS>>', 'I <<pass>> this one'):
        assert rooms._passed(words), words
    for words in ('', 'I pass on the boat talk.', 'pass the salt', None):
        assert not rooms._passed(words), words
    rooms.remove_seat(chat, d['id'])
    assert [s['id'] for s in rooms.describe(chat)['seats']] == [a['id']]


def _door_world(world, monkeypatch):
    """A room with one silent local seat and Cobalt at the door; the picture store faked."""
    from core import images as store
    monkeypatch.setattr(store, 'stash', lambda raw, mt=None, *, visible=False, chat_name=None: 'img:door1')
    monkeypatch.setattr(store, 'resolve', lambda h: type('R', (), {'data': b'PIX', 'media_type': 'image/png'})())
    chat, a, _ = _salon(two=False)
    rooms.update_seat(chat, a['id'], {'enabled': False})
    rooms.update_room(chat, {'bots_enabled': False})                # lines land, no round runs: turns are driven by hand
    d = rooms.add_seat(chat, 'cobalt', door=True)
    rooms.update_seat(chat, d['id'], {'context': 'Keep it short.', 'settings': {'voice': 'bm_lewis'}})
    seat = next(s for s in rooms._room(chat)[1]['seats'] if s['id'] == d['id'])
    return chat, d, seat, world.llm_chat.session_manager.chats[chat]['rows']


def test_her_turn_at_the_door_is_a_cue_handed_out_once_and_her_line_back(world, monkeypatch, caplog):
    """Away (nobody at the door): skipped at once, a line in the log and nothing in the
    room. Here (a wait parked): her turn parks the cue - the lines since her last, the
    pictures on them, the room's about - and the wait takes it, once; a second wait is
    told to post. room_post lands her line as her (speaker = her seat, kind door, seen),
    read on the satellite the person spoke from, END carries it; a late post finds no
    turn open. <<PASS>> anywhere passes, an empty post passes: nothing in the room."""
    import base64
    from core.devices import voice
    said, published = [], []
    monkeypatch.setattr(voice, 'say', lambda dev, text, **k: (said.append((dev, text, k.get('voice'))) or ('ok', True)))
    monkeypatch.setattr(rooms, 'publish', lambda et, data=None, **k: published.append((et, data)))
    chat, d, seat, rows = _door_world(world, monkeypatch)
    png = base64.b64encode(b'PNG').decode('ascii')
    rooms.say(chat, 'Who is at the door?', source='device:pi', images=[{'data': png, 'media_type': 'image/png'}])
    assert _wait(lambda: len(rows) == 1 and rooms.state(chat)['heard'] == 'device:pi')
    rt = rooms._rt(chat)
    with caplog.at_level(logging.INFO, logger='core.rooms'):
        assert rooms._seat_turn(chat, seat, rt) == ''              # away
    assert 'away: nobody at the door' in caplog.text and len(rows) == 1
    assert not [et for et, _ in published if et == Events.VOICE_TURN_START]
    box = {}
    waiter = threading.Thread(target=lambda: box.update(cue=rooms.door_wait('cobalt', chat, 5)))
    waiter.start()
    assert _wait(lambda: rooms.state(chat)['doors'].get(d['id']) is True)   # parked = here
    turn = threading.Thread(target=lambda: box.update(line=rooms._seat_turn(chat, seat, rt)))
    turn.start()
    waiter.join(3)
    cue = box['cue']
    assert cue['turn'] and cue['seconds'] == 120 and '<<PASS>>' in cue['note']
    assert cue['cue'] == 'Krem: Who is at the door?\n\n(Your turn, Cobalt.)'
    assert cue['images'] == [{'data': base64.b64encode(b'PIX').decode('ascii'), 'media_type': 'image/png'}]
    assert cue['about'] == {'room': chat, 'you': 'Cobalt', 'purpose': 'Talk about the boat.', 'people': ['Krem'],
                            'instructions': 'Keep it short.', 'rules': rooms._rules_text(rooms._room(chat)[1])}
    again = rooms.door_wait('cobalt', chat, 1)
    assert again['turn'] and 'handed out before' in again['note'] and again['cue'] == cue['cue']   # every waiter gets the open cue (a killed watcher's wait took it first, 2026-10-13)
    assert rooms.door_post('cobalt', chat, 'I am.') == "Posted as Cobalt in 'salon'."
    turn.join(3)
    assert box['line'] == 'I am.' and len(rows) == 2
    row = rows[1]
    assert (row['role'], row['speaker'], row['name'], row['kind'], row['seen'], row['content']) == \
           ('assistant', d['id'], 'Cobalt', 'door', 1, 'I am.')
    end = next(data for et, data in published if et == Events.VOICE_TURN_END)
    assert end['text'] == 'I am.' and end['speaker'] == d['id'] and end['kind'] == 'door' and end['heard'] == 'device:pi'
    assert _wait(lambda: said == [('pi', 'I am.', 'bm_lewis')])
    with pytest.raises(rooms.RoomError, match="not Cobalt's turn"):
        rooms.door_post('cobalt', chat, 'late')
    read = rooms.door_read('cobalt', chat)
    assert read['lines'] == [{'k': rows[0]['k'], 'who': 'Krem', 'text': 'Who is at the door?', 'pictures': 1},
                             {'k': rows[1]['k'], 'who': 'Cobalt', 'text': 'I am.', 'you': True}]
    assert read['your_turn'] is False and read['about']['people'] == ['Krem']
    # the next cue starts after her line: nothing new
    for words in ('Nothing to add. <<pass>>', ''):
        n = len(rows)
        turn = threading.Thread(target=lambda: box.update(line=rooms._seat_turn(chat, seat, rt)))
        turn.start()
        assert _wait(lambda: rt.doors[d['id']].cue is not None)
        assert rt.doors[d['id']].cue['cue'] == '(Nobody has spoken since your last line.)\n\n(Your turn, Cobalt.)'
        assert rooms.door_post('cobalt', chat, words) == 'Passed.'
        turn.join(3)
        assert box['line'] == '' and len(rows) == n
    assert published[-1][1]['dropped'] and published[-1][1]['why'] == 'said nothing'


def test_at_the_door_out_of_time_is_a_notice_and_stop_withdraws_the_cue(world, monkeypatch):
    """Here but silent past DOOR_WAIT: skipped with a notice, like a local seat cut off.
    Stop while her turn is open: the cue is withdrawn, the turn ends with nothing in the
    room, a late post finds no turn. A wait parked when her seat goes wakes empty-handed;
    her next call is refused in words. A line out of turn from her key's holder (tell,
    who = the persona) lands as her, never as a stranger's."""
    pub = []
    monkeypatch.setattr(rooms, 'publish', lambda et, data=None, **k: pub.append((et, data)))
    monkeypatch.setattr(rooms, 'DOOR_WAIT', 0.3)
    chat, d, seat, rows = _door_world(world, monkeypatch)
    rooms.say(chat, 'Anyone?')
    assert _wait(lambda: len(rows) == 1)
    rt = rooms._rt(chat)
    rooms.door_read('cobalt', chat)                                  # a call at the door: she is here
    assert rooms.state(chat)['doors'] == {d['id']: True}
    assert rooms._seat_turn(chat, seat, rt) == ''
    assert rows[-1]['speaker'] == 'room' and rows[-1]['content'] == 'Cobalt did not answer in time and was skipped.'
    monkeypatch.setattr(rooms, 'DOOR_WAIT', 30.0)
    box = {}
    turn = threading.Thread(target=lambda: box.update(line=rooms._seat_turn(chat, seat, rt)))
    turn.start()
    assert _wait(lambda: rt.doors[d['id']].cue is not None)
    rooms.stop(chat)
    turn.join(3)
    assert box['line'] == '' and len(rows) == 2 and rt.doors[d['id']].cue is None
    with pytest.raises(rooms.RoomError, match='not Cobalt'):
        rooms.door_post('cobalt', chat, 'late')
    rt.stop_flag.clear()
    # out of turn, as her
    rooms.say(chat, 'Out of turn.', source='mcp:10.0.0.9', who='Cobalt')
    assert _wait(lambda: len(rows) == 3)
    assert (rows[2]['speaker'], rows[2]['name'], rows[2]['role']) == (d['id'], 'Cobalt', 'assistant')   # a bot's row, never a person's with her name (2026-10-13)
    ends = [x for et, x in pub if et == Events.VOICE_TURN_END and (x or {}).get('speaker') == d['id']]
    assert ends and ends[-1]['text'] == 'Out of turn.' and ends[-1]['source'] == 'room' and ends[-1]['kind'] == 'door'   # painted and read like a turn
    rooms.say(chat, 'A stranger.', source='mcp:10.0.0.9', who='Visitor')
    assert _wait(lambda: len(rows) == 4)
    assert rows[3]['speaker'] == 'mcp:Visitor'
    # her seat goes while a wait is parked
    waiter = threading.Thread(target=lambda: box.update(wait=rooms.door_wait('cobalt', chat, 5)))
    waiter.start()
    assert _wait(lambda: rt.doors[d['id']].parked == 1)
    rooms.remove_seat(chat, d['id'])
    waiter.join(3)
    assert box['wait']['turn'] is False and d['id'] not in rt.doors
    with pytest.raises(rooms.RoomError, match='no seat at the door'):
        rooms.door_wait('cobalt', chat, 1)
    # the browser side: the picker offers the door, the roster shows here/away
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / 'interfaces' / 'web' / 'static' / 'views' / 'rooms.js').read_text(encoding='utf-8')
    assert "const AT_DOOR = 'door:';" in js and "label=\"At the door (over MCP)\"" in js
    assert "{ persona: persona.slice(AT_DOOR.length), door: true }" in js
    assert "s.kind === 'door' ? `at the door · ${R?.info?.state?.doors?.[s.id] ? 'here' : 'away'}`" in js


def test_a_seat_that_fails_says_why_in_the_room(world, monkeypatch):
    """Opening night (Krem 2026-10-13): a seat's LM Studio had no model loaded and the
    room said only "could not take a turn (RuntimeError)"; the cause sat in the journal
    and Claude had to go read it. The notice carries the words now, on every path a
    turn fails on (round robin, the blind round), and so does the turn's `why`."""
    chat, a, b = _salon()
    rooms.update_seat(chat, b['id'], {'enabled': False})
    rows = world.llm_chat.session_manager.chats[chat]['rows']
    cause = 'Model not found or not loaded. If using LM Studio, make sure a model is loaded and running.'

    def broken(chat, seat, rt, prep=None):
        raise RuntimeError(cause)
    monkeypatch.setattr(rooms, '_seat_turn', broken)
    notices = lambda: [r['content'] for r in rows if r.get('speaker') == rooms.NOTICE]
    stopped = 'Nobody spoke this round, so the room stopped. Say something to start it again.'
    rooms.say(chat, 'Anyone there?')
    assert _wait(lambda: len(notices()) >= 2 and not rooms.state(chat)['running'])
    assert notices() == [f"Sapphire could not take a turn: {cause}", stopped]
    rooms.update_seat(chat, b['id'], {'enabled': True})                            # the blind round's own catch
    rooms.update_room(chat, {'blind': True})
    rooms.say(chat, 'Still nobody?')
    assert _wait(lambda: len(notices()) >= 5 and not rooms.state(chat)['running'])
    assert notices()[2:] == [f"Sapphire could not take a turn: {cause}", f"Sapphire 2 could not take a turn: {cause}", stopped]
    assert rooms._cause(RuntimeError('')) == 'RuntimeError' and len(rooms._cause(RuntimeError('x' * 900))) == 400


def test_mentions_cue_only_the_seats_named_and_chain_to_the_cap(world, monkeypatch):
    """Krem 2026-10-13: `@Name` in a line cues that seat ahead of any round, whoever
    wrote it; a seat's answer naming another chains; `mention_max` (an int, his call:
    3 to start, 50 for a coding session, 0 = no cap) is the brake. A round's lines
    chain too, right after the round, not after the person's next line."""
    chat, a, b = _salon()
    order = []

    def fake(chat_, seat, rt):
        order.append(seat['name'])
        other = 'Sapphire 2' if seat['name'] == 'Sapphire' else 'Sapphire'
        line = f"@{other} your go"
        rooms.append_line(chat_, 'assistant', line, seat['id'], seat['name'], persona=seat['persona'])
        return line
    monkeypatch.setattr(rooms, '_seat_turn', fake)
    rows = world.llm_chat.session_manager.chats[chat]['rows']
    _, room = rooms._room(chat)
    assert rooms._mentions(room, 'hey @sapphire 2 and @SAPPHIRE, not @Nobody') == [b['id'], a['id']]
    assert rooms._mentions(room, '@all settle it') == [] and rooms._mentions(room, 'no at-sign') == []
    assert rooms.describe(chat)['mention_max'] == 3
    assert rooms.say(chat, '@Sapphire 2 you first')
    assert _wait(lambda: rooms.state(chat)['waiting'])
    assert order == ['Sapphire 2', 'Sapphire', 'Sapphire 2']                       # named, then the chain, capped at 3
    assert rows[-1]['speaker'] == rooms.NOTICE and rows[-1]['content'] == 'Stopped after 3 mentioned turns. Say something to go on.'
    rooms.update_room(chat, {'mention_max': 1})
    rooms.say(chat, 'a plain line: the usual round, whose lines name each other')
    assert _wait(lambda: len(order) == 6 and rooms.state(chat)['waiting'])
    assert order[3:] == ['Sapphire', 'Sapphire 2', 'Sapphire 2']                   # the round, then one mentioned turn
    rooms.say(chat, '@all speak up')                                                # @all: the usual round
    assert _wait(lambda: len(order) == 9 and rooms.state(chat)['waiting'])
    assert order[6:8] == ['Sapphire', 'Sapphire 2']
