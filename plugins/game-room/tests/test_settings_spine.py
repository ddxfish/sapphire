# The settings spine (2026-09-09, Krem's requirement): room defaults that
# every game inherits and may override, and a session may override again —
# effective(game, session) = room ⊕ game ⊕ session, each key declared once
# with the layers allowed to set it. Cadence min/max is the first tenant.
import sys
import types
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).parent.parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

import gameroom_core as gc  # noqa: E402


class FakeStore:
    def __init__(self):
        self.d = {}

    def get(self, k):
        return self.d.get(k)

    def save(self, k, v):
        self.d[k] = v

    def delete(self, k):
        self.d.pop(k, None)


@pytest.fixture
def store(monkeypatch):
    fake = FakeStore()
    monkeypatch.setattr(gc, 'store', fake)
    return fake


def _key(k):
    return next(f for f in gc.ROOM_KEYS if f['key'] == k)


# ── declarations ────────────────────────────────────────────────────────────

def test_every_key_declared_once_with_a_ladder():
    keys = [k['key'] for k in gc.ROOM_KEYS]
    assert len(keys) == len(set(keys))
    for f in gc.ROOM_KEYS:
        assert f['scope'] and set(f['scope']) <= {'room', 'game', 'session'}
        assert 'default' in f and f['type'] in ('string', 'text', 'number', 'range', 'checkbox', 'select')
    assert _key('cadence_min')['scope'] == ['room', 'game', 'session']
    assert _key('cadence_paused')['scope'] == ['session']            # a table pauses, not the room
    assert _key('player_name')['scope'] == ['room']


def test_room_schema_filters_by_scope_and_fills_options(monkeypatch):
    monkeypatch.setattr(gc, '_dynamic_options', lambda kind: [{'value': kind, 'label': kind}])
    game_keys = {f['key'] for f in gc.room_schema('game')}
    assert 'cadence_min' in game_keys and 'player_name' not in game_keys
    sess_keys = {f['key'] for f in gc.room_schema('session')}
    assert 'cadence_paused' in sess_keys and 'session_prompt_piece' not in sess_keys
    tools = next(f for f in gc.room_schema('room', with_options=True) if f['key'] == 'new_session_toolset')
    assert tools['options'][0]['value'] == '' and tools['options'][1]['value'] == 'toolsets'
    # the declaration itself is never mutated by option filling
    assert 'options' not in _key('new_session_toolset')


# ── coercion (shared with engine SETTINGS — S2 #8) ──────────────────────────

def test_coerce_field_types():
    num = {'type': 'number', 'min': 5, 'max': 100}
    assert gc.coerce_field(num, '7.9') == 7 and gc.coerce_field(num, 0) == 5 and gc.coerce_field(num, 999) == 100
    assert gc.coerce_field(num, 'x') is None
    assert gc.coerce_field({'type': 'range', 'min': 0, 'max': 1}, '0.5') == 0.5
    chk = {'type': 'checkbox'}
    assert gc.coerce_field(chk, 'true') is True and gc.coerce_field(chk, 'off') is False
    assert gc.coerce_field(chk, False) is False and gc.coerce_field(chk, 1) is True
    sel = {'type': 'select', 'options': [{'value': 'a'}, 'b']}
    assert gc.coerce_field(sel, 'b') == 'b' and gc.coerce_field(sel, 'zzz') is None
    assert gc.coerce_field({'type': 'select', 'options': ['a'], 'allow_custom': True}, 'zzz') == 'zzz'
    assert gc.coerce_field({'type': 'text'}, 'x' * 9000) == 'x' * 8000


def test_save_game_settings_uses_shared_coercion(store, monkeypatch):
    eng = types.SimpleNamespace(SETTINGS=[
        {'key': 'auto', 'type': 'checkbox', 'default': False},
        {'key': 'level', 'type': 'select', 'options': ['easy', 'hard'], 'default': 'easy'},
        {'key': 'gold', 'type': 'number', 'min': 1, 'max': 9, 'default': 5}])
    monkeypatch.setattr(gc, 'get_game', lambda gid: ({}, eng))
    merged = gc.save_game_settings('g', {'auto': 'true', 'level': 'nope', 'gold': '50'})
    assert merged == {'auto': True, 'level': 'easy', 'gold': 9}    # bool, refused select keeps default, clamped


# ── the three layers ────────────────────────────────────────────────────────

def test_effective_precedence_and_ladder(store):
    eff, layers = gc.effective(with_layers=True)
    assert eff['cadence_min'] == 60 and layers['cadence_min'] == 'default'
    gc.save_room_defaults({'cadence_min': 30, 'cadence_max': 90, 'player_name': 'Krem',
                           'bogus': 1, 'cadence_paused': True})   # unknown + room-may-not-set dropped
    assert store.d['roomcfg'] == {'cadence_min': 30, 'cadence_max': 90, 'player_name': 'Krem'}
    gc.save_game_room_overrides('doom', {'cadence_max': 600, 'player_name': 'Hacker'})   # room-only key refused
    assert store.d['gamecfg:doom']['_room'] == {'cadence_max': 600}
    chat = {'mode': 'game', 'game_id': 'doom', 'game_room': {'cadence_min': 10, 'session_prompt_piece': 'nope'}}
    eff, layers = gc.effective('doom', chat_settings=chat, with_layers=True)
    assert eff['cadence_min'] == 10 and layers['cadence_min'] == 'session'
    assert eff['cadence_max'] == 600 and layers['cadence_max'] == 'game'
    assert eff['player_name'] == 'Krem' and layers['player_name'] == 'room'
    assert eff['session_prompt_piece'] == '' and layers['session_prompt_piece'] == 'default'
    # another game inherits the room, not doom's overrides
    assert gc.effective('poker')['cadence_max'] == 90


def test_clearing_an_override_inherits_again(store):
    gc.save_room_defaults({'cadence_min': 30})
    gc.save_game_room_overrides('doom', {'cadence_min': 5})
    assert gc.effective('doom')['cadence_min'] == 5
    gc.save_game_room_overrides('doom', {'cadence_min': None})
    assert gc.effective('doom')['cadence_min'] == 30 and store.d['gamecfg:doom']['_room'] == {}
    gc.save_room_defaults({'cadence_min': None})
    assert gc.effective('doom')['cadence_min'] == 60


def test_cadence_range_never_inverts(store):
    gc.save_room_defaults({'cadence_min': 300, 'cadence_max': 60})
    eff = gc.effective()
    assert eff['cadence_min'] == 300 and eff['cadence_max'] == 300


def test_game_overrides_live_beside_engine_keys(store, monkeypatch):
    eng = types.SimpleNamespace(SETTINGS=[{'key': 'cadence_min', 'type': 'number', 'default': 1}])
    monkeypatch.setattr(gc, 'get_game', lambda gid: ({}, eng))
    gc.save_game_settings('g', {'cadence_min': 7})            # the ENGINE's own key
    gc.save_game_room_overrides('g', {'cadence_min': 40})     # the ROOM key's override
    assert gc.game_settings('g') == {'cadence_min': 7}
    assert gc.effective('g')['cadence_min'] == 40


def test_room_config_wrappers_still_serve_the_old_callers(store):
    gc.save_room_config({'player_name': 'Krem', 'llm_primary': 'lmstudio', 'return_prompt': 'sapphire'})
    rc = gc.room_config()
    assert rc['player_name'] == 'Krem' and rc['llm_primary'] == 'lmstudio' and rc['return_prompt'] == 'sapphire'
    assert rc['cadence_min'] == 60                              # the whole room layer rides along


def test_effective_for_chat_resolves_through_the_game(store, monkeypatch):
    gc.save_game_room_overrides('poker', {'cadence_min': 15})
    monkeypatch.setattr(gc, '_chat_settings', lambda chat: {
        'tbl': {'mode': 'game', 'game_id': 'poker'},
        'tale': {'mode': 'game', 'game_id': 'story:titanic', 'game_room': {'cadence_min': 25}},
        'plain': {},
    }.get(chat))
    assert gc.effective_for_chat('tbl')['cadence_min'] == 15
    assert gc.effective_for_chat('tale')['cadence_min'] == 25     # its own session layer, no game
    assert gc.effective_for_chat('plain')['cadence_min'] == 60


# ── routes ──────────────────────────────────────────────────────────────────

def test_room_settings_routes(store, monkeypatch):
    from routes import play
    monkeypatch.setattr(gc, '_dynamic_options', lambda kind: [])
    got = play.get_room_settings()
    assert {f['key'] for f in got['schema']} == {f['key'] for f in gc.ROOM_KEYS if 'room' in f['scope']}
    out = play.set_room_settings(body={'settings': {'cadence_min': 45, 'tts_route': 'speakers'}})
    assert out['effective']['cadence_min'] == 45 and out['effective']['tts_route'] == 'speakers'


def test_game_settings_route_carries_the_room_layer(store, monkeypatch):
    from routes import play
    eng = types.SimpleNamespace(SETTINGS=[{'key': 'x', 'type': 'number', 'default': 1}])
    monkeypatch.setattr(gc, 'get_game', lambda gid: ({'title': 'G'}, eng))
    monkeypatch.setattr(gc, '_dynamic_options', lambda kind: [])
    gc.save_room_defaults({'cadence_min': 30})
    got = play.get_game_settings('g')
    assert got['room']['inherited']['cadence_min'] == 30 and got['room']['overrides'] == {}
    out = play.set_game_settings('g', body={'settings': {'x': 3}, 'room_overrides': {'cadence_min': 12}})
    assert out['settings'] == {'x': 3} and out['room_overrides'] == {'cadence_min': 12}
    assert out['effective']['cadence_min'] == 12
    # overrides alone, on an engine with no SETTINGS, is not "no settings here"
    eng2 = types.SimpleNamespace()
    monkeypatch.setattr(gc, 'get_game', lambda gid: ({'title': 'G'}, eng2))
    out2 = play.set_game_settings('g2', body={'room_overrides': {'cadence_max': 900}})
    assert out2['room_overrides'] == {'cadence_max': 900}


def test_session_settings_routes(store, monkeypatch, hermetic_chat_store):
    from routes import play
    sm = hermetic_chat_store
    sm.create_chat('tbl')
    sm.set_named_chat_settings('tbl', {'mode': 'game', 'game_id': 'poker'})
    import core.api_fastapi as af
    system = types.SimpleNamespace(llm_chat=types.SimpleNamespace(session_manager=sm))
    monkeypatch.setattr(af, 'get_system', lambda: system)
    from gameroom_story import session as story_session
    monkeypatch.setattr(story_session, '_is_live', lambda s, c: False)
    monkeypatch.setattr(gc, '_dynamic_options', lambda kind: [])
    gc.save_game_room_overrides('poker', {'cadence_min': 20})
    got = play.get_session_settings(query={'session': 'tbl'})
    assert got['inherited']['cadence_min'] == 20 and got['overrides'] == {}
    assert {f['key'] for f in got['schema']} == {f['key'] for f in gc.ROOM_KEYS if 'session' in f['scope']}
    out = play.set_session_settings(body={'session': 'tbl', 'settings': {'cadence_min': 8, 'cadence_paused': 'true',
                                                                         'session_prompt_piece': 'not mine'}})
    assert out['overrides'] == {'cadence_min': 8, 'cadence_paused': True}
    assert out['effective']['cadence_min'] == 8 and out['effective']['cadence_paused'] is True
    assert sm.get_settings_for('tbl')['game_room'] == {'cadence_min': 8, 'cadence_paused': True}
    out2 = play.set_session_settings(body={'session': 'tbl', 'settings': {'cadence_min': None}})
    assert out2['overrides'] == {'cadence_paused': True} and out2['effective']['cadence_min'] == 20
    assert play.get_session_settings(query={'session': 'nope'})[1] == 404
    eff = play.get_effective(query={'session': 'tbl'})
    assert eff['effective']['cadence_min'] == 20 and eff['layers']['cadence_min'] == 'game'


# ── the costume hook ────────────────────────────────────────────────────────

def _hook(name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(f'gameroom_test_hook_{name}', PLUGIN_DIR / 'hooks' / f'{name}.py')
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_costume_hook_injects_the_games_line(store, monkeypatch):
    costume = _hook('costume')
    monkeypatch.setattr(gc, 'game_session', lambda chat: 'doom' if chat == 'tbl' else None)
    monkeypatch.setattr(gc, '_chat_settings', lambda chat: {'mode': 'game', 'game_id': 'doom'})
    gc.save_room_defaults({'session_prompt_piece': 'You are at the table.'})
    gc.save_game_room_overrides('doom', {'session_prompt_piece': "You're on the couch watching Krem play Doom."})
    ev = types.SimpleNamespace(chat_name='tbl', context_parts=[])
    costume.prompt_inject(ev)
    assert ev.context_parts == ["You're on the couch watching Krem play Doom."]
    ev2 = types.SimpleNamespace(chat_name='plain', context_parts=[])
    costume.prompt_inject(ev2)
    assert ev2.context_parts == []


# ── the dual nature: turn vs event vs timer, declared by the game ──────────

def test_cadence_mode_declared_and_hides_the_clock_rows():
    mode = _key('cadence_mode')
    assert [o['value'] for o in mode['options']] == ['off', 'turn', 'event', 'timer'] and mode['default'] == 'timer'
    # nature, not taste: no room layer, never in the library (a room-level
    # "timer" read as "this applies to poker" — Krem 2026-09-09)
    assert mode['scope'] == ['game', 'session'] and mode['show_in'] == ['room']
    # one lever per nature: the clock is the timer's, every-N is the event's
    assert _key('cadence_min')['reveal_if'] == {'key': 'cadence_mode', 'is': ['timer']}
    assert _key('cadence_max')['reveal_if'] == {'key': 'cadence_mode', 'is': ['timer']}
    assert _key('send_frames')['reveal_if'] == {'key': 'cadence_mode', 'is': ['event', 'timer']}
    assert _key('frames_per_tick')['reveal_if'] == ['send_frames', {'key': 'cadence_mode', 'is': ['timer']}]


def test_game_declaration_sits_between_room_and_override(store, monkeypatch):
    import core.games_registry as reg
    monkeypatch.setattr(reg, 'list_games', lambda: [
        {'id': 'poker', 'room_defaults': {'cadence_mode': 'turn', 'send_frames': False, 'player_name': 'nope', 'bogus': 1}},
        {'id': 'doom', 'room_defaults': {'cadence_mode': 'timer', 'cadence_min': 30}}])
    gc.save_room_defaults({'cadence_mode': 'event', 'cadence_min': 100})
    assert 'cadence_mode' not in store.d['roomcfg']                     # the room has no nature
    assert gc.effective()['cadence_mode'] == 'timer'                    # a game declaring nothing = the shipped default
    # the game's nature beats the shipped default…
    eff, layers = gc.effective('poker', with_layers=True)
    assert eff['cadence_mode'] == 'turn' and layers['cadence_mode'] == 'game_default'
    assert eff['send_frames'] is False
    assert eff['player_name'] == '' and 'bogus' not in eff          # room-only / unknown keys refused
    assert eff['cadence_min'] == 100 and layers['cadence_min'] == 'room'   # taste keys flow from the room
    # …and the user's per-game override beats the declaration
    gc.save_game_room_overrides('poker', {'cadence_mode': 'event'})
    eff, layers = gc.effective('poker', with_layers=True)
    assert eff['cadence_mode'] == 'event' and layers['cadence_mode'] == 'game'
    # what the game sidebar shows as inherited = room ⊕ declaration (no overrides)
    inh = gc.inherited_for_game('poker')
    assert inh['cadence_mode'] == 'turn' and inh['cadence_min'] == 100
    assert gc.inherited_for_game('doom')['cadence_min'] == 30
    assert gc.game_declared_defaults(None) == {} and gc.game_declared_defaults('unknown') == {}


# ── show_in: where a key is rendered (independent of scope) ────────────────

def test_show_in_filters_the_sidebars_not_the_resolver(store, monkeypatch):
    monkeypatch.setattr(gc, '_dynamic_options', lambda kind: [])
    assert _key('new_session_toolset')['show_in'] == ['library']
    lib = {f['key'] for f in gc.room_schema('room', surface='library')}
    room = {f['key'] for f in gc.room_schema('game', surface='room')}
    assert 'new_session_toolset' in lib and 'new_session_toolset' not in room
    assert 'cadence_min' in lib and 'cadence_min' in room                 # absent show_in = both
    # the resolver still honors a manifest declaration of a library-only key…
    import core.games_registry as reg
    monkeypatch.setattr(reg, 'list_games', lambda: [{'id': 'doom', 'room_defaults': {'new_session_toolset': 'none'}}])
    assert gc.effective('doom')['new_session_toolset'] == 'none'
    # …but the USER's per-game layer has no door for it: refused on write, ignored on read
    gc.save_game_room_overrides('doom', {'new_session_toolset': 'work', 'cadence_min': 9})
    assert store.d['gamecfg:doom']['_room'] == {'cadence_min': 9}
    store.d['gamecfg:doom']['_room']['new_session_toolset'] = 'stale'      # a value stored before the rule
    assert gc.game_room_overrides('doom') == {'cadence_min': 9}
    assert gc.effective('doom')['new_session_toolset'] == 'none'
    from routes import play
    assert 'new_session_toolset' in {f['key'] for f in play.get_room_settings()['schema']}


def test_cadence_every_sits_with_the_nature(store):
    k = _key('cadence_every')
    assert k['scope'] == ['game', 'session'] and k['show_in'] == ['room']
    assert k['reveal_if'] == {'key': 'cadence_mode', 'is': ['event']}
    assert gc.effective()['cadence_every'] == 1
    gc.save_game_room_overrides('doom', {'cadence_every': 4})
    assert gc.effective('doom')['cadence_every'] == 4


def test_room_schema_words_every_n_with_the_games_noun(store, monkeypatch):
    import core.games_registry as reg
    monkeypatch.setattr(reg, 'list_games', lambda: [
        {'id': 'td', 'cadence_event': 'wave'}, {'id': 'pk', 'cadence_event': ''}])
    row = {f['key']: f for f in gc.room_schema('game', surface='room', game_id='td')}['cadence_every']
    assert row['label'] == 'every N waves' and 'every third wave' in row['help']
    row = {f['key']: f for f in gc.room_schema('game', surface='room', game_id='pk')}['cadence_every']
    assert row['label'] == 'every N events'
    assert {f['key']: f for f in gc.room_schema('game', surface='room')}['cadence_every']['label'] == 'every N events'
    assert gc.coerce_field(_key('cadence_mode'), 'off') == 'off'
