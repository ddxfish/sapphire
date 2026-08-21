"""Game Room generic routes — one dispatcher for every game.

Adding a game touches NO route code: drop games/<id>/{meta.json,engine.py}
plus app/games/<id>.js, re-sign, rescan. See the contract in gameroom_core.py.

Since 0.5 every call carries a `session` (the mode-tagged chat name — the chat
IS the save): state is keyed per (game, session) and her seat's brain resolves
from that chat's settings. Session-less calls still work against the legacy
room-global state so nothing strands.
"""

import sys
from pathlib import Path

# .absolute(), never .resolve(): symlinked plugin dirs must not escape.
_PLUGIN_ROOT = str(Path(__file__).absolute().parent.parent)
if _PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, _PLUGIN_ROOT)

import gameroom_core as gc


def _resolve(game):
    meta, engine = gc.get_game(game)
    if not engine:
        return None, ({'error': f'Unknown game: {game}'}, 404)
    return engine, None


def _session(query=None, body=None):
    s = ''
    if isinstance(body, dict):
        s = str(body.get('session') or '').strip()
    if not s and isinstance(query, dict):
        s = str(query.get('session') or '').strip()
    return s or None


def _out(engine, state, session=None):
    view = engine.redact(state) if state else {'session': None}
    cfg = gc.session_cfg(session)
    view['seat'] = {**cfg, 'resolved': gc.provider_info(
        cfg.get('provider'), cfg.get('model', ''),
        privacy_required=bool(cfg.get('privacy_required') or cfg.get('hidden')))}
    return view


def list_games(**_):
    return {'games': gc.games_list()}


def get_state(game, query=None, **_):
    engine, err = _resolve(game)
    if err:
        return err
    session = _session(query)
    with gc.session_lock(game, session):
        return _out(engine, gc.load_state(game, session), session)


def new_session(game, body=None, query=None, **_):
    engine, err = _resolve(game)
    if err:
        return err
    session = _session(query, body)
    gcfg = gc.game_settings(game)
    kw = {'cfg': gcfg}
    player = gc.room_config().get('player_name')
    if player:
        kw['player_name'] = player
    with gc.session_lock(game, session):
        old = gc.load_state(game, session)
        try:
            state = engine.new_session(**kw)
        except TypeError:
            state = engine.new_session()      # engines predating cfg support
        if old:
            # talk_seq stays monotonic across sessions — the client's
            # "already spoken" watermark must never see seq go backwards
            state['talk_seq'] = old.get('talk_seq', 0)
        cfg = gc.session_cfg(session)
        display = gc.seat_display_name(cfg)
        if display != 'Sapphire':
            state['session']['ai_name'] = display
        gc.add_talk(state, 'dealer', 'New session. Shuffle up.')
        gc.save_state(game, state, session)
        return _out(engine, state, session)


def start(game, body=None, query=None, **_):
    """Start the next round (deal the next hand, set the next board, ...)."""
    engine, err = _resolve(game)
    if err:
        return err
    session = _session(query, body)
    with gc.session_lock(game, session):
        state = gc.load_state(game, session)
        if not state:
            return ({'error': 'No session — sit down first.'}, 400)
        why = engine.can_start(state)
        if why:
            return ({'error': why}, 400)
        engine.start_round(state)
        gc.run_ai_turns(engine, state, gc.session_cfg(session), gc.game_settings(game))   # AI may act first
        gc.save_state(game, state, session)
        return _out(engine, state, session)


def act(game, body=None, query=None, **_):
    engine, err = _resolve(game)
    if err:
        return err
    body = body or {}
    session = _session(query, body)
    # Table talk is optional since 0.5 — a silent move gets a marker line so
    # the table drama survives and her seat can needle the quiet (Krem's call,
    # 2026-08-02: simple play must work, she still always talks back).
    say = str(body.get('say') or '').strip()
    action = str(body.get('action') or '').lower().strip()
    args = body.get('args') or {}
    if not isinstance(args, dict):
        args = {}
    with gc.session_lock(game, session):
        state = gc.load_state(game, session)
        if not state or engine.whose_turn(state) != 'player':
            return ({'error': 'Not your turn.'}, 400)
        # '_'-prefixed actions are SILENT system verbs (checkpoints, state
        # syncs from real-time games) — no talk line, no AI turn. 2026-08-04.
        # The HOST owns this contract end to end: it strips the marker before
        # the engine ever sees the verb. Engines that re-stripped it
        # themselves let a client smuggle '_fold' past the silence check and
        # suppress her turn on a normal move (finding 4.18).
        silent = action.startswith('_')
        action = action.lstrip('_')
        if not silent:
            gc.add_talk(state, 'player', say[:400] if say else '(plays in silence)')
        try:
            engine.apply_action(state, 'player', action, args)
        except gc.IllegalAction as e:
            return ({'error': str(e)}, 400)   # nothing saved — talk line discarded too
        if not silent:
            gc.run_ai_turns(engine, state, gc.session_cfg(session), gc.game_settings(game))
        gc.save_state(game, state, session)
        return _out(engine, state, session)


def get_game_settings(game, **_):
    """Per-game settings + the engine's schema (drives the settings modal)."""
    meta, engine = gc.get_game(game)
    if not engine:
        return ({'error': f'Unknown game: {game}'}, 404)
    schema = getattr(engine, 'SETTINGS', None) or []
    return {'game': game, 'title': meta.get('title', game),
            'schema': schema, 'settings': gc.game_settings(game)}


def set_game_settings(game, body=None, **_):
    """Save per-game settings (validated against the engine's schema)."""
    _meta, engine = gc.get_game(game)
    if not engine:
        return ({'error': f'Unknown game: {game}'}, 404)
    merged = gc.save_game_settings(game, (body or {}).get('settings') or {})
    if merged is None:
        return ({'error': 'This game has no settings.'}, 400)
    return {'status': 'ok', 'settings': merged}


def get_room_config(**_):
    """Room-wide config (player name etc.) — drives the library sidebar."""
    return {'config': gc.room_config()}


def set_room_config(body=None, **_):
    return {'status': 'ok', 'config': gc.save_room_config((body or {}).get('config') or {})}


def apply_room_model(body=None, **_):
    """Stamp the room's model override onto a session chat, called by the
    client on every game/story session entry (Krem 2026-08-20: room-level
    provider picker — Sapph stays on her persona's model everywhere else;
    '' = persona default = this route touches nothing)."""
    override = (gc.room_config().get('llm_primary') or '').strip()
    if not override:
        return {'success': True, 'applied': ''}
    sess_name = str((body or {}).get('session') or '').strip()
    if not sess_name:
        return {'success': False, 'detail': 'session required'}
    from core.api_fastapi import get_system
    from gameroom_story import session as story_session
    story_session._stamp_settings(get_system(), sess_name,
                                  {'llm_primary': override},
                                  runtime_toolset=False)
    return {'success': True, 'applied': override}


def forget(game, body=None, query=None, **_):
    """Drop a session's saved game state — called by the room right before it
    deletes the session chat, so nothing orphans in plugin state.

    Deliberately does NOT require the engine to resolve: a game whose plugin
    was disabled or removed is exactly when its leftover state needs
    dropping, and 404-ing there made those keys unreachable forever
    (finding 4.17). Cleanup must outlive the thing being cleaned up."""
    game = str(game or '').strip().lower()
    session = _session(query, body)
    if not session:
        return ({'error': 'session required'}, 400)
    with gc.session_lock(game, session):
        # v1.3: sessioned saves live on the chat's rows; core also drops
        # them with the chat, but the room deletes save-first by design.
        gc.chat_store.delete(session, f'game:{game}')
        gc.store.delete(gc.state_key(game, session))   # pre-v1.3 residue
    return {'status': 'ok'}


def banter(game, body=None, query=None, **_):
    """Pure table talk — the AI replies in character, no game action."""
    engine, err = _resolve(game)
    if err:
        return err
    body = body or {}
    session = _session(query, body)
    say = str(body.get('say') or '').strip()
    if not say:
        return ({'error': 'Say something.'}, 400)
    with gc.session_lock(game, session):
        state = gc.load_state(game, session)
        if not state:
            return ({'error': 'No session — sit down first.'}, 400)
        gc.add_talk(state, 'player', say[:400])
        reply = gc.banter_reply(engine, state, gc.session_cfg(session), gc.game_settings(game))
        gc.add_talk(state, 'ai', reply)
        gc.save_state(game, state, session)
        return _out(engine, state, session)
