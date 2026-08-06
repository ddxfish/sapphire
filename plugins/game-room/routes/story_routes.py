# routes/story_routes.py — the story accordion + library tiles backend.
#
# Every route carries the SESSION it operates on (finding 1.1, 2026-08-05):
# the client has a room open and knows its own chat name. Resolving "whatever
# chat is active right now" server-side made client identity a fiction that
# any second tab, phone turn, or daemon could win — ▶Start/⏹End then landed
# on the wrong chat and stamped a story prompt + toolset onto it stickily.
# Session absent (older client, curl) → the engine falls back to this turn's
# effective chat, which is the old behavior minus the global-active bug.

import sys
from pathlib import Path

# .absolute(), never .resolve(): symlinked plugin dirs must not escape.
_PLUGIN_ROOT = str(Path(__file__).absolute().parent.parent)
if _PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, _PLUGIN_ROOT)


def _session():
    from gameroom_story import session
    return session


def _system():
    from core.api_fastapi import get_system
    return get_system()


def _sess_arg(body=None, query=None):
    """The client's declared session chat, or None."""
    for src in (body, query):
        if isinstance(src, dict):
            v = str(src.get("session") or "").strip()
            if v:
                return v
    return None


def status(query=None, **_):
    """Stories list + character pieces + active playthrough state."""
    from gameroom_story import rooms
    from core.prompt_manager import prompt_manager
    system = _system()
    sess = _session()
    session_name = _sess_arg(query=query)
    stories = [{"slug": slug, "title": meta["title"], "description": meta["description"],
                "role": meta.get("role"), "player_role": meta.get("player_role"),
                "tags": meta.get("tags") or [], "facts": meta.get("facts") or [],
                "stats": meta.get("stats") or {},
                "tile": sess._art_url(meta, meta.get("tile_file"))}
               for slug, meta in rooms.list_stories().items()]
    active = sess.full_state(system, session=session_name) if system else None
    characters = sorted(prompt_manager.components.get("character", {}).keys())
    default_char = sess.default_character()
    last = None if active else (sess.last_played(system, session=session_name) if system else None)
    return {"stories": stories, "active": active, "last": last,
            "characters": characters, "default_character": default_char}


def start(body=None, **_):
    slug = ((body or {}).get("story") or "").strip()
    if not slug:
        return {"success": False, "detail": "story required"}
    character = ((body or {}).get("character") or "").strip() or None
    mode = ((body or {}).get("mode") or "").strip() or None
    local = ((body or {}).get("local") or "").strip() or None
    msg, ok = _session().start(_system(), slug, character, mode=mode, local=local,
                               session=_sess_arg(body))
    return {"success": ok, "detail": msg}


def set_mode(body=None, **_):
    """Switch identity mode (story/local/combined) and/or set the
    after-story return prompt."""
    mode = ((body or {}).get("mode") or "").strip() or None
    local = ((body or {}).get("local") or "").strip() or None
    # return_prompt: absent = untouched; "" = CLEAR (back to default stash)
    return_prompt = (body or {}).get("return_prompt")
    if return_prompt is not None:
        return_prompt = str(return_prompt).strip()
    msg, ok = _session().set_mode(_system(), mode, local=local, return_prompt=return_prompt,
                                  session=_sess_arg(body))
    return {"success": ok, "detail": msg}


def reassert(body=None, **_):
    """Costume assert on room entry: an active story re-renders and
    re-activates its prompt (also re-stamps the chat's prompt setting) —
    heals reboot registry gaps and core's missing-prompt default rewrite.
    Same philosophy as the backdrop: assert truth at the door, never trust
    a memo. (Sapph-not-Rose bug, 2026-08-05.)"""
    try:
        ok = _session().refresh_prompt(_system(), session=_sess_arg(body))
    except Exception as e:
        return {"success": False, "detail": str(e)}
    return {"success": bool(ok),
            "detail": "costume re-asserted" if ok else "no active story"}


def fill_seal(body=None, **_):
    """The player writes a sealed blank (or skips to the author's fallback).
    NEVER passes through the AI — that's the entire mechanic."""
    body = body or {}
    msg, ok = _session().fill_seal(_system(), body.get("key"),
                                   text=body.get("text"),
                                   skip=bool(body.get("skip")),
                                   session=_sess_arg(body))
    return {"success": ok, "detail": msg}


def pause(body=None, **_):
    paused = bool((body or {}).get("paused", True))
    msg, ok = _session().set_paused(_system(), paused, session=_sess_arg(body))
    return {"success": ok, "detail": msg}


def end(body=None, **_):
    msg, ok = _session().end(_system(), session=_sess_arg(body))
    return {"success": ok, "detail": msg}


def get_story_settings(slug, **_):
    """Dual-layer GM conduct settings (schema shape drives the shared modal):
    universal style (all stories, per-story toggle) + this story's DM guide."""
    from gameroom_story import rooms
    sess = _session()
    if slug not in rooms.list_stories():
        return ({'error': f'Unknown story: {slug}'}, 404)
    meta = rooms.load_story(slug)['meta']
    uni = sess._store().get('storycfg:universal') or {}
    mine = sess._store().get(f'storycfg:{slug}') or {}
    schema = [
        {'key': 'use_universal', 'label': 'Use the shared GM style in this story',
         'type': 'checkbox', 'tab': 'GM Style (all stories)', 'default': True},
        {'key': 'gm_universal', 'label': 'GM style — shared by ALL stories',
         'type': 'text', 'rows': 9, 'tab': 'GM Style (all stories)',
         'default': sess.UNIVERSAL_GM_DEFAULT},
        {'key': 'dm_guide', 'label': 'DM guide — this story only (the pack ships this default)',
         'type': 'text', 'rows': 9, 'tab': 'This story',
         'default': meta.get('dm_guide') or ''},
    ]
    settings = {
        'use_universal': sess._as_bool(mine.get('use_universal'), True),
        'gm_universal': (uni.get('text') or '').strip() or sess.UNIVERSAL_GM_DEFAULT,
        'dm_guide': (mine.get('dm_guide') or '').strip() or (meta.get('dm_guide') or ''),
    }
    return {'story': slug, 'title': meta.get('title', slug),
            'schema': schema, 'settings': settings}


def set_story_settings(slug, body=None, **_):
    """Save the GM layers. Storing the shipped DM text verbatim = no override
    (pack updates keep flowing). Active story re-renders immediately — the
    tuning loop lands on the very next turn."""
    from gameroom_story import rooms
    sess = _session()
    if slug not in rooms.list_stories():
        return ({'error': f'Unknown story: {slug}'}, 404)
    vals = (body or {}).get('settings') or {}
    if 'gm_universal' in vals:
        sess._store().save('storycfg:universal', {'text': str(vals['gm_universal']).strip()})
    mine = sess._store().get(f'storycfg:{slug}') or {}
    if 'use_universal' in vals:
        mine['use_universal'] = sess._as_bool(vals['use_universal'], True)
    if 'dm_guide' in vals:
        shipped = (rooms.load_story(slug)['meta'].get('dm_guide') or '').strip()
        dm = str(vals['dm_guide']).strip()
        mine['dm_guide'] = '' if dm == shipped else dm
    sess._store().save(f'storycfg:{slug}', mine)
    refreshed = False
    try:
        refreshed = sess.refresh_prompt(_system(), session=_sess_arg(body))
    except Exception:
        pass
    return {'status': 'ok', 'refreshed': refreshed}


def inspect(query=None, **_):
    """Everything the AI currently sees — the accordion's Inspect modal."""
    from gameroom_story import rooms, state as st, render
    from core.prompt_manager import prompt_manager
    system = _system()
    sess = _session()
    session_name = _sess_arg(query=query)
    full = sess.full_state(system, session=session_name) if system else None
    if not full:
        return {"active": False}
    chat = sess._chat_name(system, session_name)
    entry = st.get_active().get(chat, {})
    story = rooms.load_story(entry["story"])
    story["rooms"].update(rooms.load_generated_rooms(entry["story"], chat))
    state = st.replay(entry["story"], chat)
    room = story["rooms"].get(state["room"])
    ghost = render.ghost_block(story, state, room) if room else "(no room)"
    # The ACTIVE registered monolith — byte-exact what she gets, never a
    # re-render that could drift from it (mode-switch bug, 2026-08-03).
    # REGISTRY first, sidecar second. Inspect promises byte-exact "what she
    # is receiving"; reading the sidecar first inverted that — after a
    # registration the two can differ, and the modal would show text she is
    # NOT getting (Tier 5, front D). Live registry > persisted copy > render.
    prompt_name = entry.get("prompt_name") or f"story_{entry['story']}"
    registered = prompt_manager.monoliths.get(prompt_name)
    prompt = (registered.get("content") if isinstance(registered, dict) else None) \
        or (st.get_dynamic().get(prompt_name) or {}).get("content") \
        or render.story_prompt(story, state, prompt_manager.components, entry.get("character"))
    # Journal events carry sealed text — the ONE thing that must never leave
    # the engine before its reveal. Inspect is a debug view, not a spoiler
    # hatch, and nothing consumes the tail anyway (Tier 5, F1).
    return {"active": True, "state": full, "ghost": ghost, "prompt": prompt,
            "chat": chat}
