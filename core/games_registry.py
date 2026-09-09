# core/games_registry.py — Plugin game registry (Phase 2, tmp/chat-surface-plan.md)
#
# Plugins declare games in their manifest (capabilities.games); the loader
# registers them here. Game HOST plugins (game-room) CONSUME this registry —
# core never imports them and carries zero game code (business ruling
# 2026-08-02: game/story surfaces are plugins, invisible unless enabled).
# The app runs fine with nothing registered; this module is a dumb,
# dependency-free holder, cloned from core/memory_layers.py.
#
# A game spec (all strings unless noted):
#   id        slug, [a-z0-9][a-z0-9_-]{0,32} — unique across plugins
#   title     display name
#   genre     freeform ("arcade", "rpg", "card") — library tiles group on it
#   desc      one-liner for the tile
#   icon      emoji fallback when a game has no art
#   surfaces  list ⊆ {room, chat_sidebar} — where the game can render
#   entry_js  plugin-relative path to the mount module (room surface)

import logging
import re
import threading

logger = logging.getLogger(__name__)

VALID_SURFACES = frozenset({'room', 'chat_sidebar'})
VALID_STAGE_MODES = {'side', 'stack', 'fullscreen'}
_ID_RE = re.compile(r'[a-z0-9][a-z0-9_-]{0,32}$')

_lock = threading.Lock()
_games = {}         # id → spec dict + plugin_name
_generation = 0     # bumped on every change — consumers cache-invalidate on it


def register_game(game_id, spec, plugin_name):
    """Register one game. Returns True on accept. Never raises — a bad
    declaration must not break plugin load."""
    global _generation
    try:
        game_id = str(game_id or '').strip().lower()
        if not _ID_RE.fullmatch(game_id):
            logger.warning(f"[GAMES] '{plugin_name}': game id '{game_id}' invalid — skipped")
            return False
        surfaces = [str(s).lower() for s in (spec.get('surfaces') or ['room'])]
        bad = [s for s in surfaces if s not in VALID_SURFACES]
        if bad or not surfaces:
            logger.warning(f"[GAMES] '{plugin_name}': game '{game_id}' has invalid "
                           f"surfaces {bad or surfaces} — skipped")
            return False
        if 'room' in surfaces and not spec.get('entry_js'):
            logger.warning(f"[GAMES] '{plugin_name}': game '{game_id}' declares the "
                           f"room surface without entry_js — skipped")
            return False
        with _lock:
            owner = _games.get(game_id, {}).get('plugin_name')
            if owner and owner != plugin_name:
                logger.warning(f"[GAMES] game '{game_id}' already registered by "
                               f"'{owner}' — '{plugin_name}' refused")
                return False
            _games[game_id] = {
                'id': game_id,
                'title': str(spec.get('title') or game_id.replace('-', ' ').title()),
                'genre': str(spec.get('genre') or ''),
                'desc': str(spec.get('desc') or spec.get('description') or ''),
                'icon': str(spec.get('icon') or ''),
                'surfaces': surfaces,
                'entry_js': str(spec.get('entry_js') or ''),
                'plugin_name': plugin_name,
                # Tile metadata (2026-08-04): one-liner + expandable facts for
                # the library's detail accordion. Typed + capped defensively.
                'players': str(spec.get('players') or '')[:80],
                'facts': [str(f)[:200] for f in (spec.get('facts') or [])
                          if isinstance(f, (str, int, float))][:8],
                # Tile art: plugin-web-relative path (web/<tile> or app/...)
                'tile': str(spec.get('tile') or '')[:200],
                # Room host hints (F6, 2026-09-09): how the board shares the
                # pane with the chat rail, and whether the stage owns keyboard
                # focus (an emulator) — unknown values fall to the host default.
                'stage_mode': (str(spec.get('stage_mode') or '').lower()
                               if str(spec.get('stage_mode') or '').lower() in VALID_STAGE_MODES else ''),
                'keeps_focus': bool(spec.get('keeps_focus')),
            }
            _generation += 1
        logger.info(f"[GAMES] Game registered: '{game_id}' from '{plugin_name}'")
        return True
    except Exception as e:
        logger.warning(f"[GAMES] register_game('{game_id}') failed: {e}")
        return False


def unregister_plugin(plugin_name):
    """Drop all games a plugin registered (unload/disable path). Data owned by
    the plugin (saves, state) survives — its games just go dark in the host."""
    global _generation
    with _lock:
        gone = [g for g, v in _games.items() if v.get('plugin_name') == plugin_name]
        for g in gone:
            _games.pop(g, None)
        if gone:
            _generation += 1
    if gone:
        logger.info(f"[GAMES] Unregistered {len(gone)} game(s) from '{plugin_name}': {gone}")
    return gone


def list_games():
    """Snapshot of all registered games, stable order (title, id)."""
    with _lock:
        games = [dict(v) for v in _games.values()]
    games.sort(key=lambda g: (g.get('title', ''), g.get('id', '')))
    return games


def generation():
    with _lock:
        return _generation
