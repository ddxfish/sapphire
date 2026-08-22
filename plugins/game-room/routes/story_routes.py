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
    # Refresh ONLY a chat actually playing THIS story (2026-08-21 hunt, R6:
    # the modal's autosave posting without a session fell back to the
    # globally active chat and re-dressed whatever story IT was running).
    try:
        from gameroom_story import state as st
        chat = sess._chat_name(_system(), _sess_arg(body))
        if (st.get_active_entry(chat) or {}).get('story') == slug:
            refreshed = sess.refresh_prompt(_system(), session=chat)
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


def _scenarios(store, slug, persist=False):
    """The unified per-story scenario store (Krem 2026-08-20: one dropdown,
    slots + environment = one authored thing). Lazy one-time migration
    folds the old setup presets and object sets in — same-named pairs merge
    into one scenario; the old keys stay behind untouched. The migration
    PERSISTS only from write lanes (persist=True) — a GET that wrote the
    store was the 2026-08-21 hunt's side-effect finding."""
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
    if persist:
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
    entry = st.get_active_entry(chat)
    slug = str((body or {}).get('slug') or (query or {}).get('slug') or '').strip()
    # An explicit slug NAMES the story it edits (2026-08-21 hunt, D8):
    # editing story X's environment from the library while story Y runs in
    # this chat used to silently land X's edits on Y's layer. Same authority
    # as the pre-start lane — the rows wait on (chat, X) until X starts.
    if slug and entry and slug != entry['story']:
        if slug in rooms.list_stories():
            return chat, slug, None
        return None, None, {'active': False, 'success': False,
                            'detail': f"No story named '{slug}'."}
    if entry:
        return chat, entry['story'], None
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
        # Effective art (W1): the override wins over the pack field —
        # resolve through the same lane play uses (store name or pack file).
        eff_bd = str(ov.get('backdrop') or '').strip() or (room.get('backdrop') or '')
        room_rows.append({'id': rid, 'title': room.get('title'),
                          'backdrop': sess._art_url(shipped, eff_bd) or '',
                          'backdrop_file': eff_bd,
                          'backdrop_override': str(ov.get('backdrop') or '').strip(),
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
                                  'has_mechanics': _mech(o or {}),
                                  # Full spec VERBATIM (2026-08-21): the
                                  # editor's fidelity gate round-trips it;
                                  # this gear is the GM console — spoilers
                                  # (riddle answers) are the GM's to see.
                                  'spec': o}
                              for n, o in objs.items() if isinstance(o, dict)},
                          # Exits, editor view (exits editor 2026-08-21):
                          # shipped rows carry the editable text fields +
                          # the door's actual machinery VERBATIM (mechanics
                          # editing rides the fidelity gate client-side);
                          # shadows/tombstones ride so badges paint ✏/ghosts.
                          'shipped_exits': [
                              {'label': e.get('label') or '', 'to': e.get('to'),
                               'desc': e.get('desc') or '',
                               'blocked_message': e.get('blocked_message') or '',
                               'has_mechanics': bool(e.get('condition') or e.get('roll')
                                                     or e.get('effects') or e.get('visible_when')
                                                     or e.get('generate') or e.get('to') is None),
                               **{k: e[k] for k in _MECH_FIELDS + ('generate',)
                                  if e.get(k) is not None}}
                              for e in (room.get('exits') or []) if isinstance(e, dict)],
                          'exit_shadows': dict(ov.get('exit_shadows') or {}),
                          'add_exits': list(ov.get('add_exits') or []),
                          'exits': len(room.get('exits') or []),
                          'shipped_objects': len(objs),
                          'shipped_actions': sum(
                              len((o or {}).get('interactions') or {})
                              for o in objs.values() if isinstance(o, dict))})
    # Playthrough-created rooms (W2, 2026-08-21) — author rows built from
    # the layer entry itself: no shipped anything, all lanes user-side.
    for rid, txt in sorted(sess.user_rooms(chat, slug).items()):
        bd = str(txt.get('backdrop') or '').strip()
        room_rows.append({'id': rid, 'title': str(txt.get('title') or '').strip(),
                          'user_room': True,
                          'backdrop': sess._art_url(shipped, bd) or '',
                          'backdrop_file': bd, 'backdrop_override': bd,
                          'shipped_template': '', 'shipped_player_desc': '',
                          'template': str(txt.get('template') or ''),
                          'player_desc': str(txt.get('player_desc') or ''),
                          'shipped_objs': {}, 'shipped_exits': [],
                          'exit_shadows': {},
                          'add_exits': list(txt.get('add_exits') or []),
                          'exits': len(txt.get('add_exits') or []),
                          'shipped_objects': 0, 'shipped_actions': 0})
    # Where the player IS right now — the Environment pane opens there.
    cur_room = None
    try:
        cur_room = st.replay(slug, chat).get('room')
    except Exception:
        pass
    return {'active': True, 'slug': slug,
            'open_flag': (shipped['meta'].get('open_flag') or '').strip(),
            'scenario': layer.get('scenario') or '',
            'current_room': cur_room,
            'rooms': room_rows,
            'pack_backdrops': _pack_backdrops(shipped['path']),
            'pieces': {**sess.builtin_story_pieces(slug),
                       **sess.get_story_pieces(slug)},
            'objects': layer.get('objects') or {},
            # Starting items (2026-08-22): shipped pool (editor view, spec
            # verbatim for the fidelity gate) + the layer's kit bucket.
            'shipped_items': {n: {'desc': (o or {}).get('desc') or '',
                                  'verbs': {v: str((s or {}).get('message') or '')
                                            for v, s in ((o or {}).get('interactions') or {}).items()
                                            if isinstance(s, dict)},
                                  'spec': o}
                              for n, o in (shipped['meta'].get('start_items') or {}).items()
                              if isinstance(o, dict)},
            'user_items': layer.get('items') or {}}


_ART_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".gif")


def _pack_backdrops(story_path):
    """Image filenames in a pack's backdrops/ dir (basenames only)."""
    from pathlib import Path
    d = Path(story_path) / "backdrops"
    if not d.is_dir():
        return []
    return sorted(f.name for f in d.iterdir()
                  if f.is_file() and f.suffix.lower() in _ART_SUFFIXES)


def get_art(name=None, **_):
    """Serve one store image (W1). Content-hash names → immutable cache."""
    from fastapi.responses import JSONResponse, Response
    from gameroom_story import art
    p = art.art_path(name)
    if p is None:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return Response(content=p.read_bytes(), media_type="image/webp",
                    headers={"Cache-Control": "public, max-age=31536000, immutable"})


async def upload_art(request=None, **_):
    """Multipart upload → recompress → content-hash store. Returns the
    bare name (the reference everything stores) + a display URL."""
    from gameroom_story import art
    try:
        # Cap BEFORE buffering into RAM (2026-08-21 hunt): a 2GB body used
        # to be read whole and only then refused by ingest's size check.
        try:
            cl = int(request.headers.get("content-length") or 0)
        except (TypeError, ValueError):
            cl = 0
        if cl > art.MAX_UPLOAD + 1024 * 1024:
            return {"success": False, "detail":
                    f"Image too large ({art.MAX_UPLOAD // (1024 * 1024)}MB cap)."}
        form = await request.form()
        f = form.get("file")
        data = await f.read(art.MAX_UPLOAD + 1) if f is not None else b""
    except Exception as e:
        return {"success": False, "detail": f"Bad upload: {e}"}
    name, err = art.ingest(data)
    if err:
        return {"success": False, "detail": err}
    return {"success": True, "name": name,
            "url": f"/api/plugin/game-room/story/art/{name}"}


def get_pieces(slug, **_):
    """This story's Prompt Pieces (Characters tab, 2026-08-21): the user
    pool + pack-shipped built-ins (one user concept — Krem's ruling)."""
    sess = _session()
    return {'pieces': sess.get_story_pieces(slug),
            'builtin': sess.builtin_story_pieces(slug)}


def set_piece(slug, body=None, **_):
    """Upsert/delete one pool piece; live-refresh any active playthrough
    of this story in the calling session's chat so text edits land now."""
    body = body or {}
    sess = _session()
    msg, ok, name = sess.set_story_piece(slug, body.get('name'),
                                         body.get('text'))
    if ok:
        try:
            chat = _sess_arg(body=body)
            system = _system()
            if system and chat and (sess.st.get_active_entry(chat) or {}) \
                    .get('story') == slug:
                sess.refresh_prompt(system, session=chat)
        except Exception:
            pass   # next re-render picks it up
    return {'success': ok, 'detail': msg, 'name': name,
            'pieces': sess.get_story_pieces(slug),
            'builtin': sess.builtin_story_pieces(slug)}


def create_room(body=None, **_):
    """New playthrough room (W2) — a user-layer room definition; every
    editor lane (text/backdrop/objects/exits) then works on it as-is."""
    body = body or {}
    chat, slug, err = _active_ctx(body=body)
    if err:
        return err
    msg, ok, rid = _session().create_user_room(chat, slug, body.get('title'))
    return {'success': ok, 'detail': msg, 'id': rid}


def delete_room(body=None, **_):
    """Remove a playthrough-created room (never shipped ones)."""
    body = body or {}
    chat, slug, err = _active_ctx(body=body)
    if err:
        return err
    try:
        rid = int(body.get('room_id'))
    except (TypeError, ValueError):
        return {'success': False, 'detail': 'room_id must be a room number.'}
    msg, ok = _session().delete_user_room(chat, slug, rid)
    return {'success': ok, 'detail': msg}


def set_backdrop(body=None, **_):
    """Set/clear one room's backdrop override (playthrough layer, W1).
    name = store hash-name OR a pack backdrops/ filename; '' clears back
    to shipped. UNGATED like objects/exits — deliberate user content."""
    from gameroom_story import art, rooms
    body = body or {}
    chat, slug, err = _active_ctx(body=body)
    if err:
        return err
    try:
        rid = int(body.get('room_id'))
    except (TypeError, ValueError):
        return {'success': False, 'detail': 'room_id must be a room number.'}
    sess = _session()
    story = rooms.load_story(slug)
    if rid not in story['rooms'] and rid not in sess.user_rooms(chat, slug):
        return {'success': False, 'detail': f'No room {rid} in this story.'}
    name = str(body.get('name') or '').strip()
    if name and not (art.art_path(name) or name in _pack_backdrops(story['path'])):
        return {'success': False, 'detail': 'Unknown image — upload it first '
                                            'or pick one of this story’s backdrops.'}
    def mutate(cur):
        if name:
            cur['backdrop'] = name
        else:
            cur.pop('backdrop', None)
    sess._mutate_room_layer(chat, slug, rid, mutate)
    return {'success': True,
            'detail': 'Backdrop set.' if name else 'Backdrop back to shipped.'}


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
    # W2: user rooms take objects too — no shipped names there, so every
    # object rides the user lane.
    room = shipped['rooms'].get(rid) if rid in shipped['rooms'] \
        else ({} if rid in _session().user_rooms(chat, slug) else None)
    if room is None:
        return {'success': False, 'detail': f'No room {rid} in this story.'}
    name = str(body.get('name') or '').strip()
    # Shipped names are ALLOWED — that's the shadow path (editor v2,
    # 2026-08-20): the override field-merges at load, mechanics survive.
    spec = body.get('spec')
    if not isinstance(spec, dict):
        return {'success': False, 'detail': 'spec must be an object.'}
    spec.pop('_author', None)
    spec.pop('_removed', None)   # tombstoning goes through delete, not upsert
    spec.pop('_replace', None)   # the replace lane is the explicit flag below
    # Fidelity-gate lane (2026-08-21): with the flag, the compiled spec
    # REPLACES the shipped object wholesale at merge. Shipped names only —
    # on user names the marker would be inert noise.
    if body.get('replace') and name in (room.get('objects') or {}):
        spec['_replace'] = True
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


def set_item(body=None, **_):
    """Upsert one starting item — the player's kit (story-level, no room).
    Same NEVER-through-the-AI law as objects; membership is derived at
    load, so it's in her inventory next turn."""
    body = body or {}
    chat, slug, err = _active_ctx(body=body)
    if err:
        return err
    name = str(body.get('name') or '').strip()
    spec = body.get('spec')
    if not isinstance(spec, dict):
        return {'success': False, 'detail': 'spec must be an object.'}
    spec.pop('_author', None)
    spec.pop('_removed', None)
    spec.pop('_replace', None)
    from gameroom_story import rooms
    shipped = (rooms.load_story(slug)['meta'].get('start_items') or {})
    if body.get('replace') and name in shipped:
        spec['_replace'] = True
    msg, ok = _session().upsert_user_item(chat, slug, name, spec,
                                          author='player')
    return {'success': ok, 'detail': msg}


def delete_item(body=None, **_):
    """Delete a user starting item — or tombstone a SHIPPED one
    (restorable); `restore: true` lifts the tombstone / resets shadows."""
    body = body or {}
    chat, slug, err = _active_ctx(body=body)
    if err:
        return err
    name = str(body.get('name') or '').strip()
    sess = _session()
    from gameroom_story import rooms
    shipped = (rooms.load_story(slug)['meta'].get('start_items') or {})
    if name in shipped:
        if body.get('restore'):
            msg, ok = sess.delete_user_item(chat, slug, name)
            return {'success': ok,
                    'detail': f"'{name}' restored to shipped." if ok else msg}
        msg, ok = sess.upsert_user_item(chat, slug, name, {'_removed': True})
        return {'success': ok,
                'detail': f"'{name}' removed from the kit (restorable)." if ok else msg}
    msg, ok = sess.delete_user_item(chat, slug, name)
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
    ur = _session().user_rooms(chat, slug)
    # W2: a user room has no shipped text — verbatim-equals-'' clears.
    shipped = all_rooms.get(rid) if rid in all_rooms \
        else ({} if rid in ur else None)
    if shipped is None:
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
            if to == rid or (to not in all_rooms and to not in ur):
                continue
            label = str(e.get('label')
                        or (all_rooms.get(to) or ur.get(to) or {}).get('title')
                        or to).strip()[:80]
            if not any(x['to'] == to for x in add_exits):
                add_exits.append({'label': label, 'to': to})
    msg, ok = _session().set_room_text(chat, slug, rid, template=t,
                                       player_desc=p, add_exits=add_exits)
    return {'success': ok, 'detail': msg}


def _clean_cond(c):
    """Light whitelist for an editor-authored condition dict — the referee
    is defensive, this just keeps junk shapes out of the layer. Values
    COERCE to what the grammar means (2026-08-21 hunt: str()-validating but
    storing raw let {'has': 3} through — the referee's .replace() then
    500'd the 12s poll)."""
    if not isinstance(c, dict):
        return None
    out = {k: str(c[k]).strip() for k in ('has', 'did', 'flag', 'solved')
           if str(c.get(k) or '').strip()}
    for k in ('flags', 'flag_gte'):
        if isinstance(c.get(k), dict) and c[k]:
            vals = {str(fk): fv for fk, fv in c[k].items()
                    if isinstance(fv, (str, int, float, bool))}
            if vals:
                out[k] = vals
    return out or None


_MECH_FIELDS = ('condition', 'roll', 'effects', 'visible_when')


def _mech_from_body(body):
    """Compile the editor's mechanics fields into an exit-grammar dict —
    one compile shared by the user-exit lane and the shipped-exit
    mechanics shadow (2026-08-21)."""
    m = {}
    cond = _clean_cond(body.get('condition'))
    if cond:
        m['condition'] = cond
    vis = _clean_cond(body.get('visible_when'))
    if vis:
        m['visible_when'] = vis
    roll = body.get('roll')
    if isinstance(roll, dict) and roll.get('sides'):
        m['roll'] = roll
    fx = body.get('effects')
    if isinstance(fx, dict) and fx:
        m['effects'] = fx
    return m


def set_exit(body=None, **_):
    """Upsert one exit (exits editor, 2026-08-21). A `to` matching a
    SHIPPED exit is the shadow path — text fields (label/desc/blocked
    message) diff-only over the pack; with the `edit_mechanics` marker the
    compiled grammar REPLACES the shipped mechanics as a unit (fidelity
    gate lives client-side — the marker only rides when the widget could
    express the door losslessly), verbatim-equals-shipped stores nothing.
    Without the marker any existing mechanics shadow rides forward. Any
    other `to` is a user-added exit carrying the full editor grammar
    (visible_when/condition/roll/effects) verbatim. Never passes through
    the AI — on her side it's the world."""
    import json
    from gameroom_story import rooms, state as st
    body = body or {}
    chat, slug, err = _active_ctx(body=body)
    if err:
        return err
    try:
        rid = int(body.get('room_id'))
        to = int(body.get('to'))
    except (TypeError, ValueError):
        return {'success': False, 'detail': 'room_id and to must be room numbers.'}
    sess0 = _session()
    all_rooms = rooms.load_story(slug)['rooms']
    ur = sess0.user_rooms(chat, slug)
    # W2: user rooms are valid at BOTH ends; a user from-room has no pack
    # exits, so everything on it rides the user lane below.
    room = all_rooms.get(rid) if rid in all_rooms else ({} if rid in ur else None)
    if room is None:
        return {'success': False, 'detail': f'No room {rid} in this story.'}
    if to == rid or (to not in all_rooms and to not in ur):
        return {'success': False, 'detail': 'Destination must be a different, real room.'}
    dest_title = (all_rooms.get(to) or ur.get(to) or {}).get('title')
    label = str(body.get('label') or dest_title or to).strip()[:80]
    desc = str(body.get('desc') or '').strip()[:300]
    blocked = str(body.get('blocked_message') or '').strip()[:300]
    sess = _session()
    shipped_ex = next((e for e in (room.get('exits') or [])
                       if isinstance(e, dict) and e.get('to') == to), None)
    if shipped_ex:
        shadow = {}
        if label and label != str(shipped_ex.get('label') or '').strip():
            shadow['label'] = label
        if desc != str(shipped_ex.get('desc') or '').strip():
            if desc:
                shadow['desc'] = desc
        if blocked != str(shipped_ex.get('blocked_message') or '').strip():
            if blocked:
                shadow['blocked_message'] = blocked
        prev = (((st.get_user_layer(slug, chat).get('rooms') or {})
                 .get(str(rid)) or {}).get('exit_shadows') or {}).get(str(to)) or {}
        if body.get('edit_mechanics'):
            mech = _mech_from_body(body)
            if mech != {k: shipped_ex[k] for k in _MECH_FIELDS
                        if shipped_ex.get(k) is not None}:
                shadow['mechanics'] = mech      # {} = door stripped bare
        elif isinstance(prev.get('mechanics'), dict):
            shadow['mechanics'] = prev['mechanics']
        try:
            if len(json.dumps(shadow)) > sess._OBJ_BYTES:
                return {'success': False, 'detail': 'Exit override too large.'}
        except (TypeError, ValueError):
            return {'success': False, 'detail': "Exit override isn't JSON-serializable."}
        msg, ok = sess.set_exit_shadow(chat, slug, rid, to, shadow or None)
        return {'success': ok,
                'detail': msg if shadow else 'Exit matches shipped — override cleared.'}
    spec = {'to': to, 'label': label}
    if desc:
        spec['desc'] = desc
    if blocked:
        spec['blocked_message'] = blocked
    spec.update(_mech_from_body(body))
    msg, ok = sess.set_user_exit(chat, slug, rid, to, spec)
    return {'success': ok, 'detail': msg}


def delete_exit(body=None, **_):
    """Remove one exit. Shipped destination → tombstone (restorable);
    `restore: true` clears the shadow/tombstone instead. User-added →
    plain removal."""
    from gameroom_story import rooms
    body = body or {}
    chat, slug, err = _active_ctx(body=body)
    if err:
        return err
    try:
        rid = int(body.get('room_id'))
        to = int(body.get('to'))
    except (TypeError, ValueError):
        return {'success': False, 'detail': 'room_id and to must be room numbers.'}
    sess = _session()
    all_rooms = rooms.load_story(slug)['rooms']
    room = all_rooms.get(rid) if rid in all_rooms \
        else ({} if rid in sess.user_rooms(chat, slug) else None)
    if room is None:
        return {'success': False, 'detail': f'No room {rid} in this story.'}
    shipped_ex = any(isinstance(e, dict) and e.get('to') == to
                     for e in (room.get('exits') or []))
    if shipped_ex:
        if body.get('restore'):
            msg, ok = sess.set_exit_shadow(chat, slug, rid, to, None)
            return {'success': ok, 'detail': 'Exit restored to shipped.' if ok else msg}
        msg, ok = sess.set_exit_shadow(chat, slug, rid, to, {'_removed': True})
        return {'success': ok,
                'detail': 'Exit walled off (restorable).' if ok else msg}
    msg, ok = sess.remove_user_exit(chat, slug, rid, to)
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
    if name.strip('—- ').lower() == 'default':
        return {'success': False,
                'detail': "'default' is the shipped story — pick another name."}
    sess = _session()
    store = sess._store()
    key = f'storyscenarios:{slug}'
    cur = dict(_scenarios(store, slug, persist=True))
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
    # layer_lock across the read→save (2026-08-21 hunt, race R1 family):
    # the scenario-tag stamp is a whole-blob rewrite of the layer it read.
    with st.layer_lock:
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
        # The canvas IS this scenario now — remember it (gear reopens on it).
        # Spread-forward: only the tag changes here; every other bucket
        # (items included) rides untouched.
        st.save_user_layer(slug, chat, {**layer, 'scenario': name})
    return {'success': True, 'scenarios': sorted(cur)}


def set_slots(body=None, **_):
    """Edit a running playthrough's slot values — re-bakes the costume."""
    body = body or {}
    slots = body.get('slots') if isinstance(body.get('slots'), dict) else {}
    msg, ok = _session().update_slots(_system(), slots, session=_sess_arg(body))
    return {'success': ok, 'detail': msg}


def load_scenario(body=None, **_):
    """PURE SWAP: this playthrough's environment BECOMES the named scenario
    (empty name = reset to the shipped-only house). Applied the moment the
    dropdown changes — also works pre-start via the slug fallback. The
    slots half rides the form/start path."""
    from gameroom_story import rooms
    body = body or {}
    chat, slug, err = _active_ctx(body=body)
    if err:
        return err
    name = str(body.get('name') or '').strip()
    story = rooms.load_story(slug)
    sess = _session()
    # Write lane: fold any legacy presets/objsets in first, persisted —
    # _apply_scenario_env reads the unified key raw (migration used to lean
    # on the setup GET's side-effect write; that GET is pure now).
    _scenarios(sess._store(), slug, persist=True)
    try:
        sess._apply_scenario_env(chat, slug, story, name)
    except KeyError:
        return {'success': False, 'detail': f"No scenario named '{name}'."}
    except Exception as e:
        return {'success': False, 'detail': str(e)}
    return {'success': True,
            'detail': (f"'{name}' loaded — the house is now this scenario."
                       if name else 'Back to the default story.')}


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
    entry = (st.get_active_entry(chat) or {})
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
