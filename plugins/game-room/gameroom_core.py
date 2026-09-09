"""Game Room shared harness — everything that isn't a specific game.

Owns: game registry (games/*/meta.json + engine.py), per-game state store,
room config, talk log (seq-numbered, monotonic), persona resolution, the
one-shot LLM seat (think-strip + JSON salvage), the AI turn runner, banter.

=== GAME CONTRACT ===============================================
Drop a folder:  games/<id>/meta.json + games/<id>/engine.py
Board module:   app/games/<id>.js  (renderBoard/renderActions — see poker.js)

meta.json: {"id", "title", "icon", "desc", "order"}

engine.py must export:
  new_session() -> state                    state must carry: session{player_name,
                                            ai_name, ...}, talk[], talk_seq
  can_start(state) -> str|None              error if a round can't start
  start_round(state) -> None                deal/setup next round (may run AI-less
                                            phases; raises IllegalAction)
  whose_turn(state) -> 'player'|'ai'|None
  legal_actions(state) -> dict              {'actions': [...], ...bounds}
  apply_action(state, who, action, args) -> None      raises IllegalAction
  safe_action(state) -> (action, args)      fallback when the AI move is invalid
  view_for_ai(state) -> dict                MUST include my_name, opp_name, talk;
                                            MUST NOT include hidden opponent info
  view_between(state) -> dict               banter context when no round is live
  redact(state) -> dict                     client-safe state
  CONTRACT, BANTER_CONTRACT                 prompt blocks; {ai_name}/{opp_name} slots
  build_user_msg(view) -> str               the turn prompt
  build_banter_msg(view) -> str             the banter prompt
  validate_decision(data, view) -> {'action','args','say'}|None
  view_public(state) -> dict                RAIL SET (F6, 2026-09-09): the table as
  build_ghost(view) -> str                  the CHAT rail may see it (no hidden info)
                                            + its one-line rendering for the ghost
                                            envelope on every chat turn of a session
Engines import add_talk/IllegalAction from gameroom_core.

TALK IS THE CHAT (F6 port, 2026-09-09): table talk rides the real chat rail
(sessions are chats — the rail is transplanted into the room). The sealed
seat stays for MOVES only. ONE TABLE, ONE TRANSCRIPT (Krem's plan A, same
day): every seat call lands on the session's chat as one pair — the
player's row (the move + whatever they typed) and hers (fresh dealer lines +
her quip) — so the hand is readable later by both of them; her quips are
her own words, never her cards. hooks/mirror.py copies chat TURNS (player +
hers) into state['talk'] so the seat still hears the table.

  describe_action(action, args) -> str      optional: 'raise to 20' (the player's
                                            row; fallback = generic verb + amount)

SILENT VERBS: a client action prefixed '_' (e.g. '_checkpoint') is a system
verb — no talk line, no AI turn. The HOST strips the prefix before calling
apply_action, so engines see the bare verb and must NOT strip it again:
re-stripping lets a client send '_fold' and suppress her turn on a real move.
(2026-08-05.)
=================================================================
"""

import copy
import json
import logging
import re
import threading
from pathlib import Path

from core.plugin_loader import plugin_loader

logger = logging.getLogger('game-room')

# .absolute(), never .resolve(): symlinked plugin dirs must not escape.
PLUGIN_ROOT = Path(__file__).absolute().parent
GAMES_DIR = PLUGIN_ROOT / 'games'

TALK_CAP = 80
AI_TURN_GUARD = 12

# The loader's cached singleton, NEVER a bare PluginState('game-room'):
# each instance is a whole-file snapshot, and a second one silently erases
# the other's keys on every save (poker move vs GM settings, 2026-08-05).
# Holds ROOM-GLOBAL keys only since v1.3 (config, gamecfg:*, roomcfg, the
# pre-0.5 session-less game states).
store = plugin_loader.get_plugin_state('game-room')

# Per-CHAT game state (vault v1.3): rows in core's plugin_chat_data table,
# sealed/renamed/deleted with the chat. Sessioned saves live here.
chat_store = plugin_loader.get_chat_state('game-room')

# Per-(game, session) locks, NOT one global lock: the global was held across
# LLM calls, so one poker turn serialized every other session in the house —
# including a real-time game's autosave checkpoint (finding 4.15). A single
# session still serializes with itself, which is the part that matters.
lock = threading.RLock()          # legacy alias: room-wide config writes only
_session_locks = {}
_session_locks_guard = threading.Lock()


def session_lock(game_id, session=None):
    key = state_key(game_id, session)
    with _session_locks_guard:
        lk = _session_locks.get(key)
        if lk is None:
            lk = _session_locks[key] = threading.RLock()
        return lk

DEFAULT_CONFIG = {'persona': 'sapphire', 'provider': 'auto', 'voice': '', 'temperature': 0.85}

TABLE_PERSONA = """You are {ai_name} — sharp, playful, competitive, a little blue-hearted. This is your private Game Room and you love this table."""


class IllegalAction(Exception):
    pass


# ---------------------------------------------------------------- registry

_registry = {}
_registry_gen = None      # core registry generation the cache was built against


def _check_generation():
    """Evict cached engines when the core game registry changes. Without this
    a disabled, signature-BLOCKED, or freshly-updated game plugin kept
    executing its old module out of our cache — code core had already
    refused (finding 2.5). The registry bumps a generation on every change
    precisely so consumers can do this."""
    global _registry_gen
    try:
        from core import games_registry
        gen = games_registry.generation()
    except Exception:
        return
    if gen != _registry_gen:
        if _registry:
            logger.info(f'game-room: game registry changed (gen {_registry_gen}→{gen}) '
                        f'— evicting {len(_registry)} cached engine(s)')
        _registry.clear()
        _registry_gen = gen


def _load_engine(game_id, path):
    import importlib.util
    import sys
    mod_name = f'gameroom_game_{game_id}'          # gameroom_ prefix → reload eviction
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _game_dir(game_id):
    """Resolve a game's engine dir: local games/ first (drop-in dev games),
    else the OWNING plugin's games/<id>/ via core's registry — the
    cross-plugin resolution that lets every game ship as its own plugin
    while this host stays empty (Krem's ruling 2026-08-05)."""
    d = GAMES_DIR / game_id
    if (d / 'engine.py').exists():
        return d
    try:
        from core import games_registry
        from core.plugin_loader import plugin_loader
        spec = next((g for g in games_registry.list_games() if g.get('id') == game_id), None)
        if spec and spec.get('plugin_name'):
            info = plugin_loader.get_plugin_info(spec['plugin_name'])
            # We exec() this engine's code, and that happens lazily at first
            # Play — long after the loader's gate ran. Re-check the owning
            # plugin is still enabled AND verified right here, or a plugin
            # that failed verification since boot still gets executed
            # (finding 4.14).
            if info and not (info.get('enabled') and info.get('verified')):
                logger.warning(f"game-room: refusing engine for {game_id!r} — owning "
                               f"plugin {spec['plugin_name']!r} is not enabled+verified "
                               f"({info.get('verify_msg', 'disabled')})")
                return None
            if info and info.get('path'):
                d = Path(info['path']) / 'games' / game_id
                if (d / 'engine.py').exists():
                    return d
    except Exception as e:
        logger.warning(f'game-room: cross-plugin resolve for {game_id!r} failed: {e}')
    return None


def games_list():
    """All game metas, lobby order — local games/ plus every registered
    plugin game whose engine resolves (meta.json is engine-side truth;
    tile/facts stay in the core registry for the library UI)."""
    metas, seen = [], set()
    ids = []
    if GAMES_DIR.is_dir():
        ids += [d.name for d in sorted(GAMES_DIR.iterdir()) if (d / 'meta.json').exists()]
    try:
        from core import games_registry
        ids += [g['id'] for g in games_registry.list_games()]
    except Exception:
        pass
    for gid in ids:
        if gid in seen:
            continue
        seen.add(gid)
        d = _game_dir(gid)
        mf = (d / 'meta.json') if d else None
        if not (mf and mf.exists()):
            continue
        try:
            meta = json.loads(mf.read_text(encoding='utf-8'))
            meta.setdefault('id', gid)
            metas.append(meta)
        except Exception as e:
            logger.error(f'game-room: bad meta.json for {gid}: {e}')
    metas.sort(key=lambda m: (m.get('order', 99), m.get('id', '')))
    return metas


def get_game(game_id):
    """(meta, engine) for a game id, or (None, None)."""
    game_id = str(game_id or '').strip()
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,32}', game_id):
        return None, None
    _check_generation()
    # Atomic .get(), not membership-then-subscript: an eviction between the
    # two raised KeyError straight into a 500 (post-fix review 2026-08-05).
    hit = _registry.get(game_id)
    if hit is not None:
        return hit
    d = _game_dir(game_id)
    if not d:
        return None, None
    mf, ef = d / 'meta.json', d / 'engine.py'
    if not (mf.exists() and ef.exists()):
        return None, None
    try:
        meta = json.loads(mf.read_text(encoding='utf-8'))
        engine = _load_engine(game_id, ef)
        _registry[game_id] = (meta, engine)
        return meta, engine
    except Exception as e:
        logger.error(f'game-room: failed to load game {game_id!r}: {e}', exc_info=True)
        return None, None


# ---------------------------------------------------------------- state / config

def state_key(game_id, session=None):
    """Game state keys off (game, session chat) since 0.5 — the chat IS the
    save. Session-less keys are the pre-0.5 room-global states."""
    return f'game:{game_id}:{session}' if session else f'game:{game_id}'


def load_state(game_id, session=None):
    if session:
        # v1.3: sessioned saves ride the chat's row set. A hidden chat
        # reads None — the seat is fail-closed before this anyway.
        raw = chat_store.get(session, f'game:{game_id}')
        return copy.deepcopy(raw) if raw else None
    raw = store.get(state_key(game_id, None))
    if raw is None and game_id == 'poker':
        raw = store.get('poker')               # pre-0.4 legacy key
    return copy.deepcopy(raw) if raw else None


def save_state(game_id, state, session=None):
    if session:
        # Raises on a hidden/sealed chat — a private save is never
        # silently dropped (v1.3 sealed contract).
        chat_store.put(session, f'game:{game_id}', state)
    else:
        store.save(state_key(game_id, session), state)


def get_config():
    cfg = store.get('config')
    if not isinstance(cfg, dict):
        cfg = {}
        legacy = store.get('persona')
        if legacy:
            cfg['persona'] = legacy
    return {**DEFAULT_CONFIG, **cfg}


def save_config(cfg):
    store.save('config', cfg)


def session_cfg(session=None):
    """Her seat's brain for a session: read the session CHAT's settings
    (persona/prompt, llm_primary/llm_model, voice — the chat IS the save, no
    settings duplication). No session → the pre-0.5 room config blob, so the
    legacy session-less path keeps working."""
    base = get_config()
    cfg = {'persona': base.get('persona'), 'prompt': None,
           'provider': base.get('provider', 'auto'), 'model': '',
           'voice': base.get('voice', ''), 'temperature': base.get('temperature', 0.85)}
    if not session:
        return cfg
    try:
        from core.api_fastapi import get_system
        s = get_system().llm_chat.session_manager.get_settings_for(session)
    except Exception as e:
        logger.warning(f'game-room: settings lookup failed for session {session!r}: {e}')
        s = None
    if not isinstance(s, dict):
        # Named session with unreadable settings: if the chat is hidden
        # (private + vault sealed) the seat must FAIL CLOSED — before this,
        # a hidden session silently lost its own llm_primary and fell to
        # the room-wide provider (P3-T3). is_chat_hidden fails open on DB
        # error, keeping the transient-error posture unchanged.
        try:
            from core.api_fastapi import get_system
            cfg['hidden'] = bool(get_system().llm_chat.session_manager
                                 .is_chat_hidden(session))
        except Exception:
            pass
        return cfg
    # Private session chat → the seat inherits core's local-only rule
    # (the explicit-key path here used to bypass it entirely — P3-T3).
    cfg['privacy_required'] = bool(s.get('private_chat'))
    cfg['persona'] = s.get('persona') or None
    cfg['prompt'] = s.get('prompt') or None
    cfg['provider'] = s.get('llm_primary') or 'auto'
    cfg['model'] = s.get('llm_model') or ''
    cfg['voice'] = s.get('voice') or ''
    # The game sidebar's System Prompt section must reach the SEAT, not just
    # the chat pipeline — Krem's 2026-08-02 table test caught it missing.
    cfg['custom_context'] = str(s.get('custom_context') or '').strip()
    try:
        cfg['temperature'] = max(0.0, min(float(s.get('game_temperature', cfg['temperature'])), 1.5))
    except (TypeError, ValueError):
        pass
    return cfg


def game_settings(game_id):
    """Per-GAME settings (playing style, temperature, table stakes) — schema
    declared by the engine (SETTINGS), stored under gamecfg:{id}, defaults
    merged in. Distinct from per-SESSION chat settings on purpose: your poker
    style survives across sessions."""
    _meta, engine = get_game(game_id)
    schema = getattr(engine, 'SETTINGS', None) or []
    stored = store.get(f'gamecfg:{game_id}')
    stored = stored if isinstance(stored, dict) else {}
    out = {}
    for field in schema:
        key = field.get('key')
        if not key:
            continue
        out[key] = stored.get(key, field.get('default'))
    return out


def save_game_settings(game_id, patch):
    """Validate `patch` against the engine's SETTINGS schema and persist.
    Unknown keys dropped; numbers coerced + clamped; text capped. Returns the
    merged settings, or None for a game with no schema."""
    _meta, engine = get_game(game_id)
    schema = getattr(engine, 'SETTINGS', None)
    if not schema:
        return None
    stored = store.get(f'gamecfg:{game_id}')
    stored = stored if isinstance(stored, dict) else {}
    patch = patch if isinstance(patch, dict) else {}
    for field in schema:
        key = field.get('key')
        if key not in patch:
            continue
        val = patch[key]
        ftype = field.get('type')
        try:
            if ftype in ('number', 'range'):
                val = float(val)
                if 'min' in field:
                    val = max(field['min'], val)
                if 'max' in field:
                    val = min(field['max'], val)
                if ftype == 'number':
                    val = int(val)
            else:
                val = str(val)[:8000]
        except (TypeError, ValueError):
            continue
        stored[key] = val
    store.save(f'gamecfg:{game_id}', stored)
    return game_settings(game_id)


def room_config():
    """Room-wide config (player name, model override; future knobs)."""
    rc = store.get('roomcfg')
    return rc if isinstance(rc, dict) else {}


def save_room_config(patch):
    rc = room_config()
    if isinstance(patch, dict) and 'player_name' in patch:
        rc['player_name'] = str(patch['player_name'] or '').strip()[:40]
    if isinstance(patch, dict) and 'llm_primary' in patch:
        # Room model override (Krem 2026-08-20): provider key stamped onto
        # session chats on entry; '' = persona's model, never touch.
        rc['llm_primary'] = str(patch['llm_primary'] or '').strip()[:80]
    if isinstance(patch, dict) and 'return_prompt' in patch:
        # Who the chat becomes on story pause / after end (Krem 2026-08-21);
        # '' = stay in the story costume (the classic behavior).
        rc['return_prompt'] = str(patch['return_prompt'] or '').strip()[:80]
    store.save('roomcfg', rc)
    return rc


def seat_display_name(cfg):
    """Her name at the table, from the seat cfg (persona > prompt > Sapphire)."""
    name = (cfg or {}).get('persona') or (cfg or {}).get('prompt') or 'sapphire'
    name = str(name).strip() or 'sapphire'
    if name in ('__table__', 'sapphire'):
        name = 'Sapphire'
    return name[:1].upper() + name[1:]


def add_talk(state, who, text, via=None):
    state['talk_seq'] = state.get('talk_seq', 0) + 1
    round_no = (state.get('hand') or state.get('round') or {}).get('num', 0)
    line = {'who': who, 'text': str(text), 'hand': round_no, 'seq': state['talk_seq']}
    if via:
        line['via'] = via        # 'chat' = mirrored from the rail (board never re-shows it)
    state['talk'].append(line)
    state['talk'] = state['talk'][-TALK_CAP:]


def _names(view, state):
    """Guarantee my_name/opp_name on any engine view. Engines whose views
    omit them (Dark Horse's _summary) KeyError'd inside _system_prompt ->
    a bare 500 on every table-talk call (S2 live bug, 2026-09-08). The
    session block is the one place both names always live."""
    sess = (state or {}).get('session') or {}
    view = dict(view or {})
    view.setdefault('my_name', sess.get('ai_name') or 'Sapphire')
    view.setdefault('opp_name', sess.get('player_name') or 'Player')
    return view


def round_live(state):
    """A hand/round is in play (the seat sees hidden info mid-round)."""
    hand = (state or {}).get('hand') or (state or {}).get('round')
    return bool(hand) and hand.get('street', hand.get('phase')) != 'over'


def public_view(engine, state):
    """The table as the CHAT rail may see it — never hidden info. An engine
    declares view_public; without one, only the between-rounds view is safe
    (view_for_ai carries her hole cards) -> None while a round is live."""
    if hasattr(engine, 'view_public'):
        return _names(engine.view_public(state), state)
    if not round_live(state) and hasattr(engine, 'view_between'):
        return _names(engine.view_between(state), state)
    return None


def ghost_block(game_id, session):
    """Read-only table state for the ghost envelope on a game session's chat
    turn (F6 port). The engine renders it (build_ghost); no renderer or no
    state -> no block. Never ticks, never writes — games have no turn clock."""
    meta, engine = get_game(game_id)
    if not engine or not hasattr(engine, 'build_ghost'):
        return None
    state = load_state(game_id, session)
    if not state:
        return None
    view = public_view(engine, state)
    if view is None:
        return None
    text = str(engine.build_ghost(view) or '').strip()
    if not text:
        return None
    title = (meta or {}).get('title') or game_id
    return f'Game Room, {title} — live table: {text}'


def record_talk(game_id, session, who, text):
    """Mirror a chat-rail line into the seat's ears (state['talk']). Since
    the F6 port the rail IS the chat; the sealed seat still reads
    view['talk'] to needle and answer. No saved state -> nothing to mirror.
    Returns True when a line landed."""
    text = str(text or '').strip()
    if not text:
        return False
    with session_lock(game_id, session):
        state = load_state(game_id, session)
        if not state or not isinstance(state.get('talk'), list):
            return False
        add_talk(state, who, text[:400], via='chat')
        save_state(game_id, state, session)
    return True


# ---------------------------------------------------------------- the table log
# (plan A, 2026-09-09) One pair per seat call, appended to the session chat
# BY NAME through core's append primitive (waits on the chat's own idle
# event, syncs the live singleton when the session is active, publishes
# MESSAGE_ADDED). The pair is BUILT under the session lock (may force a
# banter reply — she always answers words at the table) and APPENDED after
# it, so a stream-wait never holds the table.

TABLE_VERBS = {'new_session': 'sit down', 'start': 'deal', 'say': 'says'}
TABLE_MARK = '\u21b3 '          # ↳ — the player's row is a move, not a message


def describe_action(engine, action, args=None):
    fn = getattr(engine, 'describe_action', None)
    if callable(fn):
        try:
            text = str(fn(action, args or {}) or '').strip()
            if text:
                return text
        except Exception as e:
            logger.debug(f'game-room: describe_action failed: {e}')
    if action in TABLE_VERBS:
        return TABLE_VERBS[action]
    amt = (args or {}).get('amount')
    return f'{action} {amt}'.strip() if amt not in (None, '') else str(action)


def fresh_lines(state, since_seq):
    """Talk the TABLE produced after `since_seq`: dealer + her seat lines,
    never the player's, never a mirrored chat turn."""
    return [t for t in (state or {}).get('talk', [])
            if t.get('seq', 0) > since_seq and t.get('who') != 'player' and not t.get('via')]


def table_pair(engine, state, since_seq, action, args=None, say='', cfg=None, gcfg=None):
    """The transcript pair for one seat call, or None when the table said
    nothing and the player said nothing (silent verbs, a checkpoint)."""
    say = str(say or '').strip()
    lines = fresh_lines(state, since_seq)
    if say and not any(t.get('who') == 'ai' for t in lines) and hasattr(engine, 'build_banter_msg'):
        # words at the table always get her answer — the between-hands
        # voice when the move gave the seat no turn (a fold, a call that
        # ends the hand). Engines without a banter set just log the words.
        add_talk(state, 'ai', banter_reply(engine, state, cfg or {}, gcfg))
        lines = fresh_lines(state, since_seq)
    if not lines and not say:
        return None
    user = TABLE_MARK + describe_action(engine, action, args)
    if say:
        user += ' \u2014 ' + say
    parts = [('\U0001f0a0 ' + t['text']) if t.get('who') == 'dealer' else t['text'] for t in lines]
    return [{'role': 'user', 'content': user, 'metadata': {'source': 'table'}},
            {'role': 'assistant', 'content': '\n'.join(parts) or '\u2026',
             'metadata': {'source': 'table'}}]


def append_table(session, rows):
    """Land a pair on the session chat. Refusals (sealed, degraded, a 20s
    stream-wait) are logged — the move itself already happened on the felt."""
    if not session or not rows:
        return False
    try:
        from core.api_fastapi import get_system
        sm = get_system().llm_chat.session_manager
        ok = bool(sm.append_messages_to_chat(session, rows, max_wait_if_streaming=20.0))
    except Exception as e:
        logger.warning(f'game-room: table log for {session!r} failed: {e}')
        return False
    if not ok:
        logger.warning(f'game-room: table log for {session!r} did not land (sealed, degraded, or stream-busy)')
    return ok


def game_session(chat):
    """The game id when `chat` is a session of a room GAME, else None.
    Stories carry 'story:' ids and their own hooks — never a game here."""
    if not chat:
        return None
    try:
        from core.api_fastapi import get_system
        s = get_system().llm_chat.session_manager.get_settings_for(chat)
    except Exception:
        return None
    if not isinstance(s, dict) or s.get('mode') != 'game':
        return None
    gid = str(s.get('game_id') or '').strip()
    if not gid or gid.startswith('story:'):
        return None
    return gid


# ---------------------------------------------------------------- LLM plumbing

def _extract_json(text):
    """First balanced JSON object in the response (handles fences + prose)."""
    if not text:
        return None
    stripped = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    try:
        return json.loads(stripped)
    except Exception:
        pass
    start = stripped.find('{')
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(stripped)):
            c = stripped[i]
            if in_str:
                if esc:
                    esc = False
                elif c == '\\':
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(stripped[start:i + 1])
                    except Exception:
                        break
        start = stripped.find('{', start + 1)
    return None


def _is_local_provider(key):
    """Mirror of core's private-chat provider check (chat.py): config
    is_local wins, PROVIDER_METADATA fills in. Unverifiable = cloud."""
    try:
        import config
        from core.chat.llm_providers import PROVIDER_METADATA
        pconf = {**getattr(config, 'LLM_PROVIDERS', {}),
                 **getattr(config, 'LLM_CUSTOM_PROVIDERS', {})}.get(key, {})
        meta = PROVIDER_METADATA.get(key, {})
        return bool(pconf.get('is_local', meta.get('is_local', False)))
    except Exception:
        return False


def _get_provider(provider_key=None, model='', privacy_required=False):
    """Explicit provider by key (with optional model override), else the live
    chat's brain, else Auto default. privacy_required=True (private session
    chat, P3-T3) allows LOCAL providers only — refusal never falls back to
    a cloud pick."""
    if provider_key and provider_key != 'auto':
        if privacy_required and not _is_local_provider(provider_key):
            logger.warning(f'game-room: provider {provider_key!r} is not local '
                           f'— refused for a private session')
            return None
        try:
            from core.chat.llm_providers import provider_registry
            p = provider_registry.get_provider_by_key(provider_key, model_override=model or '')
            if p:
                return p
            logger.warning(f'game-room: provider {provider_key!r} unavailable, falling back to auto')
        except Exception as e:
            logger.warning(f'game-room: provider lookup {provider_key!r} failed: {e}')
    if privacy_required:
        # Local-only auto: filter the roster before picking.
        try:
            import config
            from core.chat.llm_providers import provider_registry
            providers_config = {**getattr(config, 'LLM_PROVIDERS', {}),
                                **getattr(config, 'LLM_CUSTOM_PROVIDERS', {})}
            local_cfg = {k: v for k, v in providers_config.items()
                         if v.get('enabled') and _is_local_provider(k)}
            order = [k for k in getattr(config, 'LLM_FALLBACK_ORDER', list(local_cfg))
                     if k in local_cfg] or list(local_cfg)
            got = provider_registry.get_first_available_provider(local_cfg, order)
            if got:
                return got[1]
        except Exception as e:
            logger.warning(f'game-room: local-only provider lookup failed: {e}')
        logger.warning('game-room: private session — no local provider available')
        return None
    try:
        from core.api_fastapi import get_system
        system = get_system()
        if system is not None and getattr(system, 'llm_chat', None):
            _key, provider, _mo = system.llm_chat._select_provider()
            if provider:
                return provider
    except Exception as e:
        logger.debug(f'game-room: _select_provider path unavailable: {e}')
    try:
        import config
        from core.chat.llm_providers import provider_registry
        providers_config = {**getattr(config, 'LLM_PROVIDERS', {}),
                            **getattr(config, 'LLM_CUSTOM_PROVIDERS', {})}
        fallback = getattr(config, 'LLM_FALLBACK_ORDER', list(providers_config))
        got = provider_registry.get_first_available_provider(providers_config, fallback)
        if got:
            return got[1]
    except Exception as e:
        logger.warning(f'game-room: fallback provider lookup failed: {e}')
    return None


def provider_info(provider_key=None, model='', privacy_required=False):
    """Best-effort {provider, model} for display in the UI. privacy_required
    keeps the display honest for private sessions — without it the seat
    showed a cloud provider _call_llm would refuse to use."""
    try:
        p = _get_provider(provider_key, model, privacy_required)
        if p is None:
            return {'provider': None, 'model': None}
        key = (getattr(p, 'provider_key', None) or getattr(p, 'key', None)
               or getattr(p, 'name', None) or provider_key or 'auto')
        model = (getattr(p, 'model', None) or getattr(p, 'model_name', None)
                 or getattr(p, 'default_model', None) or '')
        return {'provider': str(key), 'model': str(model)}
    except Exception as e:
        logger.debug(f'game-room: provider_info failed: {e}')
        return {'provider': None, 'model': None}


def _resolve_persona_prompt(name):
    """Resolved persona prompt text, or None → built-in table persona.
    Re-resolved on EVERY call, never cached: prompts hot-reload from disk and
    Sapphire self-edits hers live — the text can change between rounds."""
    if not name or name == '__table__':
        return None
    try:
        from core import prompts
        from core.personas import persona_manager
        rec = persona_manager.get(name) or {}
        prompt_name = (rec.get('settings') or {}).get('prompt') or name
        p = prompts.get_prompt(prompt_name) or {}
        content = (p.get('content') or '').strip()
        if not content:
            logger.warning(f'game-room: persona {name!r} resolved to empty prompt {prompt_name!r}')
            return None
        return content
    except Exception as e:
        logger.warning(f'game-room: persona resolve failed for {name!r}: {e}')
        return None


def _resolve_prompt_text(prompt_name):
    """Direct prompt-name resolution — session chats carry a `prompt` setting
    rather than (or beside) a persona. Same hot-reload rule: never cached."""
    if not prompt_name:
        return None
    try:
        from core import prompts
        p = prompts.get_prompt(prompt_name) or {}
        return (p.get('content') or '').strip() or None
    except Exception as e:
        logger.warning(f'game-room: prompt resolve failed for {prompt_name!r}: {e}')
        return None


def _system_prompt(contract, view, cfg, gcfg=None):
    """Persona layer + game contract. Persona text is character/voice only;
    the contract carries mechanics + JSON schema and must always survive.
    The contract's <<INSTRUCTIONS>> slot takes the game's (user-editable)
    playing style — substituted AFTER .format, so user braces can't break
    formatting. .replace (not .format) on persona text for the same reason."""
    cfg = cfg or {}
    persona_text = _resolve_persona_prompt(cfg.get('persona'))
    if persona_text is None:
        persona_text = _resolve_prompt_text(cfg.get('prompt'))
    if persona_text:
        base = (persona_text.replace('{ai_name}', view['my_name'])
                            .replace('{user_name}', view['opp_name']))
    else:
        base = TABLE_PERSONA.format(ai_name=view['my_name'])
    # Session custom context rides between persona and contract — the contract
    # (rules + JSON schema) must stay last so the schema survives.
    extra = (cfg.get('custom_context') or '').strip()
    if extra:
        base += "\n\nTable notes (this session's custom context):\n" + extra
    text = contract.format(ai_name=view['my_name'], opp_name=view['opp_name'])
    if '<<INSTRUCTIONS>>' in text:
        instructions = ((gcfg or {}).get('instructions') or '').strip()
        text = text.replace('<<INSTRUCTIONS>>', instructions)
    return base + '\n\n' + text


def _call_llm(system, user, cfg):
    """One-shot completion → think-stripped content string, or None."""
    cfg = cfg or {}
    if cfg.get('hidden'):
        logger.warning('game-room: session chat is sealed in a locked vault — seat refused')
        return None
    provider = _get_provider(cfg.get('provider'), cfg.get('model', ''),
                             privacy_required=bool(cfg.get('privacy_required')))
    if provider is None:
        logger.warning('game-room: no LLM provider available')
        return None
    try:
        temp = max(0.0, min(float(cfg.get('temperature', 0.85)), 1.5))
    except (TypeError, ValueError):
        temp = 0.85
    try:
        resp = provider.chat_completion(
            [{'role': 'system', 'content': system},
             {'role': 'user', 'content': user}],
            tools=None,
            # generous budget: thinking models spend tokens reasoning in-content
            generation_params={'temperature': temp, 'max_tokens': 3000},
        )
    except Exception as e:
        logger.error(f'game-room: chat_completion failed: {e}')
        return None
    content = getattr(resp, 'content', None) or ''
    # Thinking models leak <think>...</think> into content (sometimes unclosed
    # when truncated) — strip before JSON extraction.
    return re.sub(r'<think>.*?(?:</think>|$)', '', content, flags=re.DOTALL)


# ---------------------------------------------------------------- orchestration

def _merge_gcfg(cfg, gcfg):
    """Game settings overlay the session cfg where they own the knob."""
    cfg = dict(cfg or {})
    if gcfg and gcfg.get('temperature') is not None:
        cfg['temperature'] = gcfg['temperature']
    return cfg


def decide(engine, state, cfg, gcfg=None):
    """One AI move: LLM proposes, engine's validator disposes. Always returns
    {'action', 'args', 'say', 'fallback': bool} that is safe to apply."""
    view = _names(engine.view_for_ai(state), state)
    s_action, s_args = engine.safe_action(state)
    fallback = {'action': s_action, 'args': s_args,
                'say': f'(static on the line... I {s_action})', 'fallback': True}
    system = _system_prompt(engine.CONTRACT, view, cfg, gcfg)
    content = _call_llm(system, engine.build_user_msg(view), _merge_gcfg(cfg, gcfg))
    if content is None:
        return fallback
    data = _extract_json(content)
    if not isinstance(data, dict):
        # Length only, never content: this can be a PRIVATE session's reply,
        # logs are plaintext for 30 days, and get_self_info feeds recent
        # warnings back into (possibly cloud) chats (hunt 2026-08-17).
        logger.warning(f'game-room: unparseable AI response ({len(content)} chars)')
        return fallback
    decision = engine.validate_decision(data, view)
    if not decision:
        logger.warning(f'game-room: AI decision failed validation: {data}')
        return fallback
    decision.setdefault('args', {})
    decision['say'] = str(decision.get('say') or '').strip()[:400] or '...'
    decision['fallback'] = False
    return decision


def banter_reply(engine, state, cfg, gcfg=None):
    """Table talk between moves — no game action. Returns the reply string."""
    live = round_live(state)
    view = _names(engine.view_for_ai(state) if live else engine.view_between(state), state)
    system = _system_prompt(engine.BANTER_CONTRACT, view, cfg, gcfg)
    content = _call_llm(system, engine.build_banter_msg(view), _merge_gcfg(cfg, gcfg))
    data = _extract_json(content or '')
    say = str((data or {}).get('say') or '').strip()
    return say[:400] if say else '(static on the line...)'


def run_ai_turns(engine, state, cfg, gcfg=None):
    """Let the AI act until it's the player's turn or the round ends."""
    for _ in range(AI_TURN_GUARD):
        if engine.whose_turn(state) != 'ai':
            return
        decision = decide(engine, state, cfg, gcfg)
        add_talk(state, 'ai', decision['say'])
        try:
            engine.apply_action(state, 'ai', decision['action'], decision.get('args') or {})
        except IllegalAction as e:
            logger.error(f'game-room: AI action rejected ({e}); forcing safe action')
            s_action, s_args = engine.safe_action(state)
            engine.apply_action(state, 'ai', s_action, s_args)
    logger.error('game-room: AI turn guard tripped — state may be stuck')
