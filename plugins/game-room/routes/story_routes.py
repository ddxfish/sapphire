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
    # Open-mansion v1: Mad-Libs slot values + optional object set to stock
    # the house with (plan tmp/open-mansion-plan.md).
    slots = (body or {}).get("slots")
    slots = slots if isinstance(slots, dict) else None
    objset = str((body or {}).get("objset") or "").strip() or None
    msg, ok = _session().start(_system(), slug, character, mode=mode, local=local,
                               session=_sess_arg(body), slots=slots, objset=objset)
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


def extend_wait(body=None, **_):
    """+60s on a live seal wait (she's blocked inside story_act, the player
    clicked ⏳ More time). remaining=None → the moment already passed."""
    body = body or {}
    remaining = _session().extend_seal_wait(_system(), body.get("key"),
                                            session=_sess_arg(body))
    return {"success": remaining is not None, "remaining": remaining}


def pause(body=None, **_):
    paused = bool((body or {}).get("paused", True))
    msg, ok = _session().set_paused(_system(), paused, session=_sess_arg(body))
    return {"success": ok, "detail": msg}


def end(body=None, **_):
    msg, ok = _session().end(_system(), session=_sess_arg(body))
    return {"success": ok, "detail": msg}


def get_story_settings(slug, **_):
    """Story settings (schema shape drives the shared modal): the story's
    own text (role backstory / premise / player role — user overrides of the
    SHIPPED pack text, stored in storycfg:{slug}) + dual-layer GM conduct
    (universal style with per-story toggle, this story's DM guide). Shipped
    meta is read raw — defaults must show the pack's text, not an override."""
    from gameroom_story import rooms
    sess = _session()
    if slug not in rooms.list_stories():
        return ({'error': f'Unknown story: {slug}'}, 404)
    meta = rooms.load_story(slug, raw=True)['meta']
    uni = sess._store().get('storycfg:universal') or {}
    mine = sess._store().get(f'storycfg:{slug}') or {}
    role = meta.get('role') or {}
    role_name = (role.get('name') or '').strip()
    schema = []
    settings = {}
    if role_name:  # a story with no shipped role gets no half-role editor
        schema.append(
            {'key': 'role_text', 'label': f'{role_name} — backstory & character '
             '(the pack ships this default)',
             'type': 'text', 'rows': 9, 'tab': 'This story',
             'default': role.get('text') or ''})
        settings['role_text'] = (mine.get('role_text') or '').strip() \
            or (role.get('text') or '')
    schema += [
        {'key': 'premise', 'label': 'Premise — the setup (the pack ships this default)',
         'type': 'text', 'rows': 4, 'tab': 'This story',
         'default': meta.get('premise') or ''},
        {'key': 'player_role', 'label': 'Player role — who the player is in the tale',
         'type': 'text', 'rows': 2, 'tab': 'This story',
         'default': meta.get('player_role') or ''},
        {'key': 'dm_guide', 'label': 'DM guide — this story only (the pack ships this default)',
         'type': 'text', 'rows': 9, 'tab': 'This story',
         'default': meta.get('dm_guide') or ''},
        {'key': 'use_universal', 'label': 'Use the shared GM style in this story',
         'type': 'checkbox', 'tab': 'GM Style (all stories)', 'default': True},
        {'key': 'gm_universal', 'label': 'GM style — shared by ALL stories',
         'type': 'text', 'rows': 9, 'tab': 'GM Style (all stories)',
         'default': sess.UNIVERSAL_GM_DEFAULT},
    ]
    settings.update({
        'premise': (mine.get('premise') or '').strip() or (meta.get('premise') or ''),
        'player_role': (mine.get('player_role') or '').strip() or (meta.get('player_role') or ''),
        'dm_guide': (mine.get('dm_guide') or '').strip() or (meta.get('dm_guide') or ''),
        'use_universal': sess._as_bool(mine.get('use_universal'), True),
        'gm_universal': (uni.get('text') or '').strip() or sess.UNIVERSAL_GM_DEFAULT,
    })
    return {'story': slug, 'title': meta.get('title', slug),
            'schema': schema, 'settings': settings}


def set_story_settings(slug, body=None, **_):
    """Save story text overrides + GM layers. Storing the shipped text
    verbatim = no override (pack updates keep flowing) — same rule for every
    text field. Active story re-renders immediately — the tuning loop lands
    on the very next turn."""
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
    shipped_meta = rooms.load_story(slug, raw=True)['meta']
    for key, shipped in (
            ('dm_guide', shipped_meta.get('dm_guide') or ''),
            ('role_text', (shipped_meta.get('role') or {}).get('text') or ''),
            ('premise', shipped_meta.get('premise') or ''),
            ('player_role', shipped_meta.get('player_role') or '')):
        if key in vals:
            v = str(vals[key]).strip()
            mine[key] = '' if v == shipped.strip() else v
    sess._store().save(f'storycfg:{slug}', mine)
    refreshed = False
    try:
        refreshed = sess.refresh_prompt(_system(), session=_sess_arg(body))
    except Exception:
        pass
    return {'status': 'ok', 'refreshed': refreshed}


# ── Open-mansion v1: setup, placed objects, presets, object sets ────────────
# Plan: tmp/open-mansion-plan.md. Slots/presets are per-story config
# (plugin store); placed objects + room text are per-PLAYTHROUGH (chat rows).

def get_setup(slug, **_):
    """Everything the setup form needs: declared slots, saved setup presets
    (with values, for client-side prefill), and object-set names."""
    from gameroom_story import rooms
    sess = _session()
    if slug not in rooms.list_stories():
        return ({'error': f'Unknown story: {slug}'}, 404)
    meta = rooms.load_story(slug)['meta']
    presets = sess._store().get(f'storypresets:{slug}') or {}
    objsets = sess._store().get(f'storyobjsets:{slug}') or {}
    return {'slug': slug, 'title': meta.get('title', slug),
            'slots': rooms.story_slots(meta),
            'open_flag': (meta.get('open_flag') or '').strip(),
            'presets': presets,
            'objsets': sorted(objsets)}


def _active_ctx(query=None, body=None):
    """(chat, slug, error) for routes operating on the session's active
    playthrough."""
    from gameroom_story import state as st
    system = _system()
    sess = _session()
    chat = sess._chat_name(system, _sess_arg(body, query))
    entry = st.get_active().get(chat)
    if not entry:
        return None, None, {'active': False, 'success': False,
                            'detail': 'No story is active in this session.'}
    return chat, entry['story'], None


def get_objects(query=None, **_):
    """The Objects editor's world view: every room with shipped + override
    text, and the playthrough's placed objects."""
    from gameroom_story import rooms, state as st
    chat, slug, err = _active_ctx(query=query)
    if err:
        return err
    shipped = rooms.load_story(slug)
    layer = st.get_user_layer(slug, chat)
    room_rows = []
    for rid in sorted(shipped['rooms']):
        room = shipped['rooms'][rid]
        ov = (layer.get('rooms') or {}).get(str(rid)) or {}
        room_rows.append({'id': rid, 'title': room.get('title'),
                          'shipped_template': room.get('template') or '',
                          'shipped_player_desc': room.get('player_desc') or '',
                          'template': ov.get('template') or '',
                          'player_desc': ov.get('player_desc') or ''})
    return {'active': True, 'slug': slug,
            'open_flag': (shipped['meta'].get('open_flag') or '').strip(),
            'rooms': room_rows,
            'objects': layer.get('objects') or {}}


def set_object(body=None, **_):
    """Upsert one player-authored object. NEVER passes through the AI —
    same law as sealed blanks: on her side it's simply the world."""
    from gameroom_story import rooms
    body = body or {}
    chat, slug, err = _active_ctx(body=body)
    if err:
        return err
    try:
        rid = int(body.get('room_id'))
    except (TypeError, ValueError):
        return {'success': False, 'detail': 'room_id must be a room number.'}
    shipped = rooms.load_story(slug)
    room = shipped['rooms'].get(rid)
    if not room:
        return {'success': False, 'detail': f'No room {rid} in this story.'}
    name = str(body.get('name') or '').strip()
    if name in (room.get('objects') or {}):
        return {'success': False,
                'detail': f"The story already ships an object named '{name}' there."}
    spec = body.get('spec')
    if not isinstance(spec, dict):
        return {'success': False, 'detail': 'spec must be an object.'}
    spec.pop('_author', None)
    msg, ok = _session().upsert_user_object(chat, slug, rid, name, spec,
                                            author='player')
    return {'success': ok, 'detail': msg}


def delete_object(body=None, **_):
    body = body or {}
    chat, slug, err = _active_ctx(body=body)
    if err:
        return err
    msg, ok = _session().delete_user_object(chat, slug, body.get('room_id'),
                                            str(body.get('name') or '').strip())
    return {'success': ok, 'detail': msg}


def set_room_text(body=None, **_):
    """Per-room text overrides, verbatim-equals-shipped = reset (the house
    rule)."""
    from gameroom_story import rooms
    body = body or {}
    chat, slug, err = _active_ctx(body=body)
    if err:
        return err
    try:
        rid = int(body.get('room_id'))
    except (TypeError, ValueError):
        return {'success': False, 'detail': 'room_id must be a room number.'}
    shipped = rooms.load_story(slug)['rooms'].get(rid)
    if not shipped:
        return {'success': False, 'detail': f'No room {rid} in this story.'}
    t, p = body.get('template'), body.get('player_desc')
    if t is not None and str(t).strip() == (shipped.get('template') or '').strip():
        t = ''
    if p is not None and str(p).strip() == (shipped.get('player_desc') or '').strip():
        p = ''
    msg, ok = _session().set_room_text(chat, slug, rid, template=t, player_desc=p)
    return {'success': ok, 'detail': msg}


_PRESET_CAP = 50


def get_presets(slug, **_):
    return {'presets': _session()._store().get(f'storypresets:{slug}') or {}}


def set_preset(slug, body=None, **_):
    """Save/delete one setup preset (slot values only — the who-we-are
    axis). Object sets are the separate what-the-house-holds axis."""
    body = body or {}
    name = str(body.get('name') or '').strip()[:60]
    if not name:
        return {'success': False, 'detail': 'Preset needs a name.'}
    sess = _session()
    key = f'storypresets:{slug}'
    cur = sess._store().get(key) or {}
    if body.get('delete'):
        cur.pop(name, None)
    else:
        if name not in cur and len(cur) >= _PRESET_CAP:
            return {'success': False, 'detail': f'Preset cap reached ({_PRESET_CAP}).'}
        slots = body.get('slots') if isinstance(body.get('slots'), dict) else {}
        cur[name] = {'slots': {str(k)[:60]: str(v)[:500]
                               for k, v in slots.items() if str(v).strip()}}
    sess._store().save(key, cur)
    return {'success': True, 'presets': sorted(cur)}


def get_objsets(slug, **_):
    return {'objsets': _session()._store().get(f'storyobjsets:{slug}') or {}}


def set_objset(slug, body=None, **_):
    """Save/delete one object set. Save snapshots the SESSION's current user
    layer — minus AI-placed objects (fork 2: hers were that run's surprise).
    Import back happens at story start (objset param) or mid-run via load."""
    from gameroom_story import state as st
    body = body or {}
    name = str(body.get('name') or '').strip()[:60]
    if not name:
        return {'success': False, 'detail': 'Object set needs a name.'}
    sess = _session()
    key = f'storyobjsets:{slug}'
    cur = sess._store().get(key) or {}
    if body.get('delete'):
        cur.pop(name, None)
        sess._store().save(key, cur)
        return {'success': True, 'objsets': sorted(cur)}
    chat, active_slug, err = _active_ctx(body=body)
    if err:
        return err
    if active_slug != slug:
        return {'success': False,
                'detail': f"This session is playing '{active_slug}', not '{slug}'."}
    # Privacy gate (plan care point): the plugin store is NOT encrypted — a
    # private playthrough's authored objects must not leak into it. Fails
    # CLOSED on a settings-read error (silent-default class rule).
    sm = None
    try:
        sm = _system().llm_chat.session_manager
    except Exception:
        sm = None                      # hermetic tests: no system, no chat privacy
    if sm is not None:
        try:
            s = sm.get_settings_for(chat)
            if isinstance(s, dict) and s.get('private_chat'):
                return {'success': False, 'detail':
                        'This playthrough is private — object sets save to '
                        'unencrypted storage. Release the chat first if you '
                        'really want this set shared.'}
        except Exception:
            return {'success': False,
                    'detail': 'Could not verify chat privacy — refusing to save.'}
    if name not in cur and len(cur) >= _PRESET_CAP:
        return {'success': False, 'detail': f'Object-set cap reached ({_PRESET_CAP}).'}
    layer = st.get_user_layer(slug, chat)
    objects = {}
    for rid, objs in (layer.get('objects') or {}).items():
        if not isinstance(objs, dict):
            continue
        keep = {n: s for n, s in objs.items()
                if isinstance(s, dict) and s.get('_author') != 'ai'}
        if keep:
            objects[rid] = keep
    cur[name] = {'objects': objects, 'rooms': layer.get('rooms') or {}}
    sess._store().save(key, cur)
    return {'success': True, 'objsets': sorted(cur)}


def set_slots(body=None, **_):
    """Edit a running playthrough's slot values — re-bakes the costume."""
    body = body or {}
    slots = body.get('slots') if isinstance(body.get('slots'), dict) else {}
    msg, ok = _session().update_slots(_system(), slots, session=_sess_arg(body))
    return {'success': ok, 'detail': msg}


def load_objset(body=None, **_):
    """Mid-run set import — the live transform: load 'Backyard BBQ' while
    she's three rooms deep; it materializes at the zork-line."""
    from gameroom_story import rooms
    body = body or {}
    chat, slug, err = _active_ctx(body=body)
    if err:
        return err
    name = str(body.get('name') or '').strip()
    story = rooms.load_story(slug)
    sess = _session()
    try:
        sess._import_objset(chat, slug, story, name)
    except KeyError:
        return {'success': False, 'detail': f"No object set named '{name}'."}
    except Exception as e:
        return {'success': False, 'detail': str(e)}
    return {'success': True, 'detail': f"'{name}' loaded into this playthrough."}


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
    # load_active, not a hand-rolled load: the merged+substituted world
    # (user objects, room-text overrides, slot values) is what she actually
    # plays — inspect must show THAT, not the shipped skeleton.
    story, state = sess.load_active(chat)
    room = story["rooms"].get(state["room"]) if story else None
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
