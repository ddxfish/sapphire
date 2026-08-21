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
    # Open-mansion v1: Mad-Libs slot values (plan tmp/open-mansion-plan.md).
    # Scenario ENV rides story/scenarios/load, flushed by the form pre-start.
    slots = (body or {}).get("slots")
    slots = slots if isinstance(slots, dict) else None
    msg, ok = _session().start(_system(), slug, character, mode=mode, local=local,
                               session=_sess_arg(body), slots=slots)
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
    # Slot-assembled stories (Krem's 3-tab ruling 2026-08-20): the Story tab
    # (slots) already owns role/premise/player identity — the raw storycfg
    # text editors would just show {tokens} and invite breakage. Those
    # stories get ONE schema tab: GM (dm_guide + both conduct layers).
    has_slots = bool(rooms.story_slots(meta))
    gm_tab = 'GM' if has_slots else 'GM Style (all stories)'
    dm_tab = 'GM' if has_slots else 'This story'
    schema = []
    settings = {}
    if not has_slots:
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
        ]
        settings.update({
            'premise': (mine.get('premise') or '').strip() or (meta.get('premise') or ''),
            'player_role': (mine.get('player_role') or '').strip() or (meta.get('player_role') or ''),
        })
    schema += [
        {'key': 'dm_guide', 'label': 'DM guide — this story only (the pack ships this default)',
         'type': 'text', 'rows': 9, 'tab': dm_tab,
         'default': meta.get('dm_guide') or ''},
        {'key': 'use_universal', 'label': 'Use the shared GM style in this story',
         'type': 'checkbox', 'tab': gm_tab, 'default': True},
        {'key': 'gm_universal', 'label': 'GM style — shared by ALL stories',
         'type': 'text', 'rows': 9, 'tab': gm_tab,
         'default': sess.UNIVERSAL_GM_DEFAULT},
    ]
    settings.update({
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
    scen = _scenarios(sess._store(), slug)
    return {'slug': slug, 'title': meta.get('title', slug),
            'slots': rooms.story_slots(meta),
            'open_flag': (meta.get('open_flag') or '').strip(),
            # Slim view: the form needs names + slot values; the env half
            # stays server-side and applies via story/scenarios/load.
            'scenarios': {n: {'slots': (e or {}).get('slots') or {}}
                          for n, e in scen.items()}}


def _scenarios(store, slug):
    """The unified per-story scenario store (Krem 2026-08-20: one dropdown,
    slots + environment = one authored thing). Lazy one-time migration
    folds the old setup presets and object sets in — same-named pairs merge
    into one scenario; the old keys stay behind untouched."""
    key = f'storyscenarios:{slug}'
    cur = store.get(key)
    if cur is not None:
        return cur if isinstance(cur, dict) else {}
    cur = {}
    for name, p in (store.get(f'storypresets:{slug}') or {}).items():
        if isinstance(p, dict):
            cur[str(name)] = {'slots': p.get('slots') or {}}
    for name, o in (store.get(f'storyobjsets:{slug}') or {}).items():
        if isinstance(o, dict):
            ent = cur.setdefault(str(name), {})
            ent['objects'] = o.get('objects') or {}
            ent['rooms'] = o.get('rooms') or {}
    store.save(key, cur)
    return cur


def _active_ctx(query=None, body=None):
    """(chat, slug, error) for routes operating on the session's active
    playthrough. Pre-start fallback (2026-08-20): the setup modal's
    Environment tab edits the slug+chat user layer BEFORE story/start —
    an explicit `slug` names the story, and the playthrough merges those
    rows at load. Same authority as the active path, just earlier."""
    from gameroom_story import rooms, state as st
    system = _system()
    sess = _session()
    chat = sess._chat_name(system, _sess_arg(body, query))
    entry = st.get_active().get(chat)
    if entry:
        return chat, entry['story'], None
    slug = str((body or {}).get('slug') or (query or {}).get('slug') or '').strip()
    if slug and slug in rooms.list_stories():
        return chat, slug, None
    return None, None, {'active': False, 'success': False,
                        'detail': 'No story is active in this session.'}


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
    def _mech(o):
        # Anything beyond desc/hidden/plain-message interactions = story
        # machinery (effects, seals, dice, conditions, items...)
        if set(o) - {'desc', 'hidden', 'interactions'}:
            return True
        return any(set(v) - {'message'}
                   for v in (o.get('interactions') or {}).values()
                   if isinstance(v, dict))

    sess = _session()
    for rid in sorted(shipped['rooms']):
        room = shipped['rooms'][rid]
        ov = (layer.get('rooms') or {}).get(str(rid)) or {}
        objs = room.get('objects') or {}
        room_rows.append({'id': rid, 'title': room.get('title'),
                          'backdrop': sess._backdrop_url(shipped, room) or '',
                          'shipped_template': room.get('template') or '',
                          'shipped_player_desc': room.get('player_desc') or '',
                          'template': ov.get('template') or '',
                          'player_desc': ov.get('player_desc') or '',
                          # Shipped objects, editor view (editor v2: cards +
                          # shadow/tombstone) — counts everything, hidden/
                          # gated included; this is the author's surface.
                          'shipped_objs': {
                              n: {'desc': (o or {}).get('desc') or '',
                                  'hidden': bool((o or {}).get('hidden')),
                                  'verbs': {v: str((s or {}).get('message') or '')
                                            for v, s in ((o or {}).get('interactions') or {}).items()},
                                  'has_mechanics': _mech(o or {})}
                              for n, o in objs.items() if isinstance(o, dict)},
                          'shipped_exits': [
                              {'label': e.get('label') or '', 'to': e.get('to')}
                              for e in (room.get('exits') or []) if isinstance(e, dict)],
                          'add_exits': list(ov.get('add_exits') or []),
                          'exits': len(room.get('exits') or []),
                          'shipped_objects': len(objs),
                          'shipped_actions': sum(
                              len((o or {}).get('interactions') or {})
                              for o in objs.values() if isinstance(o, dict))})
    # Where the player IS right now — the Environment pane opens there.
    cur_room = None
    try:
        cur_room = st.replay(slug, chat).get('room')
    except Exception:
        pass
    return {'active': True, 'slug': slug,
            'open_flag': (shipped['meta'].get('open_flag') or '').strip(),
            'current_room': cur_room,
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
    # Shipped names are ALLOWED — that's the shadow path (editor v2,
    # 2026-08-20): the override field-merges at load, mechanics survive.
    spec = body.get('spec')
    if not isinstance(spec, dict):
        return {'success': False, 'detail': 'spec must be an object.'}
    spec.pop('_author', None)
    spec.pop('_removed', None)   # tombstoning goes through delete, not upsert
    msg, ok = _session().upsert_user_object(chat, slug, rid, name, spec,
                                            author='player')
    return {'success': ok, 'detail': msg}


def delete_object(body=None, **_):
    """Delete a user object — or, for a SHIPPED name, write a tombstone
    (restorable). `restore: true` drops the layer entry for a shipped name
    instead: tombstone lifted / shadow edits reset, back to the pack."""
    from gameroom_story import rooms
    body = body or {}
    chat, slug, err = _active_ctx(body=body)
    if err:
        return err
    try:
        rid = int(body.get('room_id'))
    except (TypeError, ValueError):
        return {'success': False, 'detail': 'room_id must be a room number.'}
    name = str(body.get('name') or '').strip()
    sess = _session()
    shipped_objs = (rooms.load_story(slug)['rooms'].get(rid) or {}).get('objects') or {}
    if name in shipped_objs:
        if body.get('restore'):
            msg, ok = sess.delete_user_object(chat, slug, rid, name)
            return {'success': ok,
                    'detail': f"'{name}' restored to shipped." if ok else msg}
        msg, ok = sess.upsert_user_object(chat, slug, rid, name, {'_removed': True})
        return {'success': ok,
                'detail': f"'{name}' removed from the room (restorable)." if ok else msg}
    msg, ok = sess.delete_user_object(chat, slug, rid, name)
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
    all_rooms = rooms.load_story(slug)['rooms']
    shipped = all_rooms.get(rid)
    if not shipped:
        return {'success': False, 'detail': f'No room {rid} in this story.'}
    t, p = body.get('template'), body.get('player_desc')
    if t is not None and str(t).strip() == (shipped.get('template') or '').strip():
        t = ''
    if p is not None and str(p).strip() == (shipped.get('player_desc') or '').strip():
        p = ''
    # User-added exits (additive): validated to real rooms, capped, labels
    # default to the target room's title. None = leave stored exits alone.
    ax = body.get('add_exits')
    add_exits = None
    if ax is not None:
        add_exits = []
        for e in (ax if isinstance(ax, list) else [])[:12]:
            if not isinstance(e, dict):
                continue
            try:
                to = int(e.get('to'))
            except (TypeError, ValueError):
                continue
            if to == rid or to not in all_rooms:
                continue
            label = str(e.get('label') or all_rooms[to].get('title') or to).strip()[:80]
            if not any(x['to'] == to for x in add_exits):
                add_exits.append({'label': label, 'to': to})
    msg, ok = _session().set_room_text(chat, slug, rid, template=t,
                                       player_desc=p, add_exits=add_exits)
    return {'success': ok, 'detail': msg}


_PRESET_CAP = 50


def set_scenario(slug, body=None, **_):
    """Save/delete one scenario — slots (from the form) + the playthrough's
    current ENVIRONMENT (user layer minus AI-placed objects; fork 2: hers
    were that run's surprise). One named thing = the whole authored world
    (Krem 2026-08-20)."""
    from gameroom_story import state as st
    body = body or {}
    name = str(body.get('name') or '').strip()[:60]
    if not name:
        return {'success': False, 'detail': 'Scenario needs a name.'}
    sess = _session()
    store = sess._store()
    key = f'storyscenarios:{slug}'
    cur = dict(_scenarios(store, slug))
    if body.get('delete'):
        cur.pop(name, None)
        store.save(key, cur)
        return {'success': True, 'scenarios': sorted(cur)}
    chat, active_slug, err = _active_ctx(body=body)
    if err:
        return err
    if active_slug != slug:
        return {'success': False,
                'detail': f"This session is playing '{active_slug}', not '{slug}'."}
    # Privacy gate (plan care point): the plugin store is NOT encrypted — a
    # private playthrough's authored world must not leak into it. Fails
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
                        'This playthrough is private — scenarios save to '
                        'unencrypted storage. Release the chat first if you '
                        'really want this scenario shared.'}
        except Exception:
            return {'success': False,
                    'detail': 'Could not verify chat privacy — refusing to save.'}
    if name not in cur and len(cur) >= _PRESET_CAP:
        return {'success': False, 'detail': f'Scenario cap reached ({_PRESET_CAP}).'}
    slots = body.get('slots') if isinstance(body.get('slots'), dict) else {}
    slots = {str(k)[:60]: str(v)[:1200] for k, v in slots.items() if str(v).strip()}
    layer = st.get_user_layer(slug, chat)
    objects = {}
    for rid, objs in (layer.get('objects') or {}).items():
        if not isinstance(objs, dict):
            continue
        keep = {n: s for n, s in objs.items()
                if isinstance(s, dict) and s.get('_author') != 'ai'}
        if keep:
            objects[rid] = keep
    cur[name] = {'slots': slots, 'objects': objects,
                 'rooms': layer.get('rooms') or {}}
    store.save(key, cur)
    return {'success': True, 'scenarios': sorted(cur)}


def set_slots(body=None, **_):
    """Edit a running playthrough's slot values — re-bakes the costume."""
    body = body or {}
    slots = body.get('slots') if isinstance(body.get('slots'), dict) else {}
    msg, ok = _session().update_slots(_system(), slots, session=_sess_arg(body))
    return {'success': ok, 'detail': msg}


def load_scenario(body=None, **_):
    """Apply a scenario's ENVIRONMENT half to this playthrough (staged by
    the form, flushed on Save/\u25b6 Start — also works pre-start via the
    slug fallback). The slots half rides the form/start path."""
    from gameroom_story import rooms
    body = body or {}
    chat, slug, err = _active_ctx(body=body)
    if err:
        return err
    name = str(body.get('name') or '').strip()
    story = rooms.load_story(slug)
    sess = _session()
    try:
        sess._import_scenario_env(chat, slug, story, name)
    except KeyError:
        return {'success': False, 'detail': f"No scenario named '{name}'."}
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
