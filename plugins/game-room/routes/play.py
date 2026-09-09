"""Game Room generic routes — one dispatcher for every game.

Adding a game touches NO route code: drop games/<id>/{meta.json,engine.py}
plus app/games/<id>.js, re-sign, rescan. See the contract in gameroom_core.py.

Since 0.5 every call carries a `session` (the mode-tagged chat name — the chat
IS the save): state is keyed per (game, session) and her seat's brain resolves
from that chat's settings. Session-less calls still work against the legacy
room-global state so nothing strands.

F6 port (2026-09-09): table talk is the chat rail. `banter` survives as the
published REST contract (docs/plugin-author/games.md) but the room no longer
calls it — a typed line at the table is a normal chat turn on the session.
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


def _with_last(out, rows, logged):
    """The caption's source (plan A subtitle, 2026-09-09): the pair this call
    put on the transcript — byte-for-byte what the rail shows — so the
    board's subtitle can never disagree with the chat. None = nothing said."""
    out['last'] = ({'user': rows[0]['content'], 'assistant': rows[1]['content'],
                    'logged': bool(logged)} if rows else None)
    return out


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
        before = state.get('talk_seq', 0)
        gc.add_talk(state, 'dealer', 'New session. Shuffle up.')
        rows = gc.table_pair(engine, state, before, 'new_session', cfg=cfg, gcfg=gcfg)
        gc.save_state(game, state, session)
        out = _out(engine, state, session)
    return _with_last(out, rows, gc.append_table(session, rows))


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
        before = state.get('talk_seq', 0)
        cfg, gcfg = gc.session_cfg(session), gc.game_settings(game)
        engine.start_round(state)
        gc.run_ai_turns(engine, state, cfg, gcfg)   # AI may act first
        rows = gc.table_pair(engine, state, before, 'start', cfg=cfg, gcfg=gcfg)
        gc.save_state(game, state, session)
        out = _out(engine, state, session)
    return _with_last(out, rows, gc.append_table(session, rows))


def act(game, body=None, query=None, **_):
    engine, err = _resolve(game)
    if err:
        return err
    body = body or {}
    session = _session(query, body)
    # One table, one transcript (plan A, 2026-09-09): `say` is whatever the
    # player typed when they clicked the move — it rides the move as their
    # row on the session chat, her quip lands as hers. No text = a silent
    # move (the old '(plays in silence)' marker is gone).
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
        before = state.get('talk_seq', 0)
        if not silent and say:
            gc.add_talk(state, 'player', say[:400])
        try:
            engine.apply_action(state, 'player', action, args)
        except gc.IllegalAction as e:
            return ({'error': str(e)}, 400)   # nothing saved — talk line discarded too
        rows = None
        if not silent:
            cfg, gcfg = gc.session_cfg(session), gc.game_settings(game)
            gc.run_ai_turns(engine, state, cfg, gcfg)
            rows = gc.table_pair(engine, state, before, action, args, say[:400], cfg, gcfg)
        gc.save_state(game, state, session)
        out = _out(engine, state, session)
    return _with_last(out, rows, gc.append_table(session, rows))


def get_game_settings(game, **_):
    """Per-game settings + the engine's schema (drives the settings modal),
    plus the spine's game layer: which room keys this game may override,
    what it inherits from the room, and what it has overridden."""
    meta, engine = gc.get_game(game)
    if not engine:
        return ({'error': f'Unknown game: {game}'}, 404)
    schema = getattr(engine, 'SETTINGS', None) or []
    return {'game': game, 'title': meta.get('title', game),
            'schema': schema, 'settings': gc.game_settings(game),
            'room': {'schema': gc.room_schema('game', with_options=True, surface='room'),
                     'notes': gc.TAB_NOTES.get('room', {}),
                     'inherited': gc.inherited_for_game(game),
                     'overrides': gc.game_room_overrides(game),
                     'effective': gc.effective(game)}}


def set_game_settings(game, body=None, **_):
    """Save per-game settings (validated against the engine's schema) and/or
    the game's room-key overrides (`room_overrides`: value = override,
    null = inherit again)."""
    _meta, engine = gc.get_game(game)
    if not engine:
        return ({'error': f'Unknown game: {game}'}, 404)
    body = body or {}
    out = {'status': 'ok'}
    if 'settings' in body:
        merged = gc.save_game_settings(game, body.get('settings') or {})
        if merged is None and 'room_overrides' not in body:
            return ({'error': 'This game has no settings.'}, 400)
        out['settings'] = merged
    if isinstance(body.get('room_overrides'), dict):
        out['room_overrides'] = gc.save_game_room_overrides(game, body['room_overrides'])
        out['effective'] = gc.effective(game)
    return out


def get_room_config(**_):
    """Room-wide defaults (the spine's room layer) — legacy shape."""
    return {'config': gc.room_config()}


def set_room_config(body=None, **_):
    return {'status': 'ok', 'config': gc.save_room_config((body or {}).get('config') or {})}


# ── the settings spine (2026-09-09) ────────────────────────────────────────

def get_room_settings(**_):
    """Room Defaults modal: the declared keys (room layer, options filled),
    the saved defaults, and the room-level resolution."""
    return {'schema': gc.room_schema('room', with_options=True, surface='library'),
            'notes': gc.TAB_NOTES.get('library', {}),
            'settings': gc.room_defaults(), 'effective': gc.effective()}


def set_room_settings(body=None, **_):
    saved = gc.save_room_defaults((body or {}).get('settings') or {})
    return {'status': 'ok', 'settings': saved, 'effective': gc.effective()}


def get_effective(query=None, **_):
    """The resolved settings for a game and/or session, with the layer that
    set each key. The cadence organ and the rooms read this."""
    query = query or {}
    game = str(query.get('game') or '').strip() or None
    session = _session(query)
    if session and not game:
        vals, layers = gc.effective(with_layers=True, chat_settings=gc._chat_settings(session) or {})
        # a game session resolves through its game
        s = gc._chat_settings(session) or {}
        gid = str(s.get('game_id') or '')
        if s.get('mode') == 'game' and gid and not gid.startswith('story:'):
            vals, layers = gc.effective(gid, chat_settings=s, with_layers=True)
    else:
        vals, layers = gc.effective(game, session=session, with_layers=True)
    return {'effective': vals, 'layers': layers}


def get_session_settings(query=None, **_):
    """This-session section: session-scope keys, what the session inherits
    (room ⊕ its game), and its own overrides."""
    session = _session(query)
    if not session:
        return ({'error': 'session required'}, 400)
    s = gc._chat_settings(session)
    if s is None:
        return ({'error': 'session not found'}, 404)
    gid = str(s.get('game_id') or '')
    if s.get('mode') != 'game' or gid.startswith('story:'):
        gid = ''
    return {'schema': gc.room_schema('session', with_options=True, surface='room'),
            'inherited': gc.effective(gid or None),      # room ⊕ declared ⊕ the game's overrides
            'overrides': gc.session_room_overrides(chat_settings=s),
            'effective': gc.effective(gid or None, chat_settings=s)}


def set_session_settings(body=None, query=None, **_):
    """Write a session's overrides onto its chat settings (`game_room`),
    by name — live chat or not (value = override, null = inherit)."""
    body = body or {}
    session = _session(query, body)
    if not session:
        return ({'error': 'session required'}, 400)
    s = gc._chat_settings(session)
    if s is None:
        return ({'error': 'session not found'}, 404)
    current = dict(gc.session_room_overrides(chat_settings=s))
    for key, val in gc._clean_layer(body.get('settings') or {}, 'session').items():
        if val is None:
            current.pop(key, None)
        else:
            current[key] = val
    from core.api_fastapi import get_system
    from gameroom_story import session as story_session
    story_session._stamp_settings(get_system(), session, {'game_room': current}, runtime_toolset=False)
    s2 = gc._chat_settings(session) or {}
    gid = str(s2.get('game_id') or '')
    if s2.get('mode') != 'game' or gid.startswith('story:'):
        gid = ''
    return {'status': 'ok', 'overrides': gc.session_room_overrides(chat_settings=s2),
            'effective': gc.effective(gid or None, chat_settings=s2)}


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
        before = state.get('talk_seq', 0)
        gc.add_talk(state, 'player', say[:400])
        cfg, gcfg = gc.session_cfg(session), gc.game_settings(game)
        reply = gc.banter_reply(engine, state, cfg, gcfg)
        gc.add_talk(state, 'ai', reply)
        rows = gc.table_pair(engine, state, before, 'say', say=say[:400], cfg=cfg, gcfg=gcfg)
        gc.save_state(game, state, session)
        out = _out(engine, state, session)
    return _with_last(out, rows, gc.append_table(session, rows))


# ── the cadence organ (F3, 2026-09-09) ─────────────────────────────────────
# The room arms core/cadence.py for its session from the resolved spine and
# keeps it alive (TTL 90s, keepalive every 30s from the open room); leaving
# disarms; a dead tab expires. Pause writes the session layer's
# `cadence_paused` and re-arms; poke = a game event ("wave 12 cleared").

def _cadence():
    from core import cadence
    return cadence


def _session_game(session):
    s = gc._chat_settings(session)
    if s is None:
        return None, ({'error': 'session not found'}, 404)
    gid = str(s.get('game_id') or '')
    if s.get('mode') != 'game' or not gid or gid.startswith('story:'):
        return None, ({'error': 'not a game session'}, 400)
    return gid, None


def cadence_arm(body=None, query=None, **_):
    session = _session(query, body)
    if not session:
        return ({'error': 'session required'}, 400)
    gid, err = _session_game(session)
    if err:
        return err
    meta, _engine = gc.get_game(gid)
    spec = gc.cadence_spec(session)
    st = _cadence().arm(session, owner='game-room', ttl=90, prompt=gc.cadence_prompt(session),
                        title=(meta or {}).get('title') or gid, **spec)
    return {'status': 'ok', 'cadence': st, 'spec': spec}


def cadence_disarm(body=None, query=None, **_):
    session = _session(query, body)
    if not session:
        return ({'error': 'session required'}, 400)
    return {'status': 'ok', 'disarmed': _cadence().disarm(session, owner='game-room')}


def cadence_status(query=None, **_):
    session = _session(query)
    if not session:
        return ({'error': 'session required'}, 400)
    return {'status': 'ok', 'cadence': _cadence().status(session), 'spec': gc.cadence_spec(session)}


def cadence_pause(body=None, query=None, **_):
    """Pause / resume her unprompted turns for THIS session (the session
    layer's one tenant) — persisted on the chat, mirrored into the organ."""
    body = body or {}
    session = _session(query, body)
    if not session:
        return ({'error': 'session required'}, 400)
    gid, err = _session_game(session)
    if err:
        return err
    paused = bool(body.get('paused'))
    set_session_settings(body={'session': session, 'settings': {'cadence_paused': paused}})
    st = _cadence().status(session)
    if st.get('armed'):
        st = _cadence().pause(session, paused)
    return {'status': 'ok', 'paused': paused, 'cadence': st}


def cadence_poke(body=None, query=None, **_):
    """A game moment (event mode fires on it, no sooner than the min gap;
    timer mode pulls its next turn forward to that floor)."""
    body = body or {}
    session = _session(query, body)
    if not session:
        return ({'error': 'session required'}, 400)
    note = str(body.get('note') or '').strip()[:400]
    if note:
        from core import perception
        percept = perception.peek(session) or {}
        perception.deposit(session, frames=percept.get('frames'), text=note, source='poke')
    st = _cadence().poke(session, note)
    return {'status': 'ok', 'cadence': st or {'armed': False}}


def session_end(body=None, query=None, **_):
    """The session is ending: her summary turn (spine: session_end_summary),
    run in the background — the room is already leaving."""
    body = body or {}
    session = _session(query, body)
    if not session:
        return ({'error': 'session required'}, 400)
    gid, err = _session_game(session)
    if err:
        return err
    eff = gc.effective_for_chat(session)
    _cadence().disarm(session, owner='game-room')
    if not eff.get('session_end_summary'):
        return {'status': 'ok', 'summary': False}
    import threading

    def _run():
        try:
            _cadence().fire_once(session, gc.summary_prompt(session),
                                 speak=eff.get('tts_route') or 'browser', source='session_end')
        except Exception as e:
            gc.logger.warning(f"game-room: session-end summary for {session!r} did not run: {e}")
    threading.Thread(target=_run, daemon=True, name='gameroom-session-end').start()
    return {'status': 'ok', 'summary': True}
