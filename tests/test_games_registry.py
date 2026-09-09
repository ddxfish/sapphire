# tests/test_games_registry.py — capabilities.games registry (Phase 2,
# tmp/chat-surface-plan.md). Pure-module tests, no app boot.

import importlib

import core.games_registry as gr


def _fresh():
    return importlib.reload(gr)


def test_register_and_list():
    m = _fresh()
    assert m.register_game('poker', {'title': 'Heads-Up Hold\'em',
                                     'genre': 'card', 'surfaces': ['room'],
                                     'entry_js': 'app/games/poker.js'}, 'game-room')
    games = m.list_games()
    assert len(games) == 1
    g = games[0]
    assert g['id'] == 'poker' and g['plugin_name'] == 'game-room'
    assert g['surfaces'] == ['room']


def test_invalid_id_and_surfaces_rejected():
    m = _fresh()
    assert not m.register_game('Bad Name!', {}, 'p')
    assert not m.register_game('', {}, 'p')
    assert not m.register_game('ok', {'surfaces': ['tv']}, 'p')
    # room surface requires entry_js
    assert not m.register_game('ok', {'surfaces': ['room']}, 'p')
    assert m.list_games() == []


def test_chat_sidebar_only_needs_no_entry_js():
    m = _fresh()
    assert m.register_game('missile-command', {'surfaces': ['chat_sidebar']}, 'avatar')
    assert m.list_games()[0]['surfaces'] == ['chat_sidebar']


def test_cross_plugin_shadow_refused():
    m = _fresh()
    assert m.register_game('poker', {'surfaces': ['chat_sidebar']}, 'game-room')
    assert not m.register_game('poker', {'surfaces': ['chat_sidebar']}, 'imposter')
    # same plugin may re-register (reload path)
    assert m.register_game('poker', {'surfaces': ['chat_sidebar']}, 'game-room')
    assert m.list_games()[0]['plugin_name'] == 'game-room'


def test_unregister_plugin_and_generation():
    m = _fresh()
    g0 = m.generation()
    m.register_game('a', {'surfaces': ['chat_sidebar']}, 'p1')
    m.register_game('b', {'surfaces': ['chat_sidebar']}, 'p2')
    assert m.generation() > g0
    gen = m.generation()
    gone = m.unregister_plugin('p1')
    assert gone == ['a']
    assert [g['id'] for g in m.list_games()] == ['b']
    assert m.generation() > gen
    # unregistering a plugin with nothing registered is a quiet no-op
    assert m.unregister_plugin('p1') == []


def test_bad_spec_never_raises():
    m = _fresh()
    assert not m.register_game(None, None, 'p')
    assert not m.register_game('x', {'surfaces': 'room'}, 'p')  # string, not list — TypeError inside → False


def test_room_host_hints_pass_through():
    # F6 (2026-09-09): stage_mode + keeps_focus ride the registry entry so the
    # room host can lay the board out; unknown modes fall to '' (host default).
    m = _fresh()
    assert m.register_game('doom2', {'surfaces': ['room'], 'entry_js': 'app/doom.js',
                                     'stage_mode': 'Fullscreen', 'keeps_focus': 1}, 'game-doom')
    assert m.register_game('cards', {'surfaces': ['room'], 'entry_js': 'app/c.js',
                                     'stage_mode': 'sideways'}, 'game-cards')
    by = {g['id']: g for g in m.list_games()}
    assert by['doom2']['stage_mode'] == 'fullscreen' and by['doom2']['keeps_focus'] is True
    assert by['cards']['stage_mode'] == '' and by['cards']['keeps_focus'] is False
