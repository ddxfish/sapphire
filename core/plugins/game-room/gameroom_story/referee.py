# engine/referee.py — the rulebook. The AI is dungeon master AND a player
# that can't cheat: it acts through ONE tool, this module validates every
# act against the room JSON, and solutions/hidden objects never enter the
# AI's context (render.py builds the visible layer; the raw room file is
# engine-side only). Returns resolved-outcome events for the journal.
import logging
import random

logger = logging.getLogger(__name__)

GENERIC_VERBS = ("move", "look", "search", "solve", "take", "wear", "remove")


def _norm(s):
    return str(s or "").strip().lower()


def _key(s):
    """Match key for author-declared names (objects, verbs, exit labels).

    Authors write 'crew-gate', 'Crew_Gate', 'crew gate' — and our own
    tap-to-draft prettifies 'crew-gate' into 'crew gate' before it comes
    back. Exact-string matching meant the referee missed, the act fell
    through to the off-script license, and she narrated the gate opening
    while the flag never set; capitalized keys were mechanically dead
    forever (finding 4.1). Separators and case stop being load-bearing."""
    return "".join(c for c in _norm(s) if c.isalnum())


def _find(mapping, target):
    """Value from a name-keyed dict, forgiving separators/case. Exact hit
    wins; ambiguous fuzzy hits resolve to the first in declaration order."""
    if not mapping:
        return None, None
    t = _norm(target)
    for name in mapping:
        if _norm(name) == t:
            return name, mapping[name]
    k = _key(target)
    if not k:
        return None, None
    for name in mapping:
        if _key(name) == k:
            return name, mapping[name]
    return None, None


TAKE_VERBS = ("take", "grab", "get", "pickup", "pocket")


def seal_key(room, obj_name, verb):
    """Room-scoped key for sealed blanks and once-only dice.

    Unscoped 'object:verb' keys collided ACROSS rooms: a 'letter:read' seal
    in room 1 and another in room 5 shared one entry, so the player's words
    from the first surfaced as the second's surprise, and fill_seal matched
    last-wins across the whole story (finding 3.2)."""
    return f"{(room or {}).get('id')}:{_norm(obj_name)}:{_norm(verb)}"


def legacy_seal_key(obj_name, verb):
    """The pre-2026-08-05 unscoped key — read-only, for saves already
    holding a player's words under it."""
    return f"{_norm(obj_name)}:{_norm(verb)}"


def seal_state(container, room, obj_name, verb, default=None):
    """Look up a seal-ish entry by scoped key, falling back to the legacy
    unscoped key so in-flight playthroughs keep their reveals."""
    container = container if container is not None else {}
    k = seal_key(room, obj_name, verb)
    if k in container:
        return container[k]
    lk = legacy_seal_key(obj_name, verb)
    if lk in container:
        return container[lk]
    return default


_ANSWER_STRIP = str.maketrans("", "", ".,!?;:'\"-")


def _norm_answer(s):
    """Forgiving riddle matching: case/whitespace-insensitive, punctuation
    stripped, leading articles dropped ('A Map!' solves 'map')."""
    words = _norm(s).translate(_ANSWER_STRIP).split()
    while words and words[0] in ("a", "an", "the"):
        words = words[1:]
    return " ".join(words)


def answers_of(puzzle):
    """Accepted answers: 'solutions' list or single 'solution', normalized."""
    sols = puzzle.get("solutions") or [puzzle.get("solution")]
    return [_norm_answer(x) for x in sols if x]


def _as_list(v):
    """Effect specs that accept one dict or a list of dicts."""
    if isinstance(v, dict):
        return [v]
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def _check_wearing(spec, state):
    """{"wearing": "spacesuit"} — anyone wears it, any slot. Dict form:
    {"char": id, "item": name} pins the character; add "slot" to pin the
    slot; {"char", "slot"} alone = that slot holds anything. Worn is worn —
    `has:` never sees gear (Krem's B ruling 2026-08-25)."""
    cast = state.get("cast") or {}
    if isinstance(spec, str):
        want = _key(spec)
        return any(_key(i) == want for c in cast.values()
                   for i in (c.get("wearing") or {}).values())
    if not isinstance(spec, dict):
        return False
    chars = [cast.get(spec["char"])] if spec.get("char") else list(cast.values())
    item, slot = spec.get("item"), spec.get("slot")
    for c in chars:
        if not c:
            continue
        w = c.get("wearing") or {}
        if slot and item:
            if _key(w.get(slot)) == _key(item):
                return True
        elif slot:
            if w.get(slot):
                return True
        elif item:
            if any(_key(i) == _key(item) for i in w.values()):
                return True
    return False


def _check_part(spec, state):
    """{"part": {"char","part","key","value"}} — value omitted = truthy."""
    if not isinstance(spec, dict):
        return False
    c = (state.get("cast") or {}).get(spec.get("char")) or {}
    part = (c.get("parts") or {}).get(spec.get("part")) or {}
    val = (part.get("state") or {}).get(spec.get("key"))
    return bool(val) if "value" not in spec else val == spec.get("value")


def cast_part_objects(state):
    """Parts as interactables (the unification, Krem 2026-08-24): a part is
    an object that lives on a character instead of in a room. Funnel key is
    '{char}_{part}' — _key matching forgives 'sapphire hands'. The whole
    object machinery rides free: per-verb conditions, effects, seals, dice,
    look. A top-level `condition` zork-lines the part like any object."""
    out = {}
    for cid, c in (state.get("cast") or {}).items():
        for pname, part in (c.get("parts") or {}).items():
            if isinstance(part, dict) and check_condition(part.get("condition"), state):
                out[f"{cid}_{pname}"] = part
    return out


def check_condition(cond, state):
    """Edge/object condition: {"has": item} and/or {"flags": {k: expected}}
    (also accepts {"flag": name} as a truthy check, {"flag_gte": {k: n}}
    for numeric thresholds — 'love over 50' — plus the editor pair
    (2026-08-20): {"did": obj} = the player/she has USED that object
    (replayed interaction history, verb-agnostic) and {"solved": obj} =
    its puzzle is answered. Gates everywhere conditions do: exits,
    blockers, endings, object visibility, per-verb locks)."""
    if not cond:
        return True
    if cond.get("has") and cond["has"] not in state["inventory"]:
        return False
    did = cond.get("did")
    if did and _key(did) not in {_key(u) for u in state.get("used") or []}:
        return False
    solved = cond.get("solved")
    if solved and _key(solved) not in {_key(s) for s in state.get("solved") or []}:
        return False
    flag = cond.get("flag")
    if flag and not state["flags"].get(flag):
        return False
    # {"after_turns": n} — the player has been in the CURRENT room n+ turns
    # (editor 2026-08-23: hints-as-objects — "a guard appears after 3").
    after = cond.get("after_turns")
    if after is not None:
        try:
            if int(state.get("turns_in_room") or 0) < int(after):
                return False
        except (TypeError, ValueError):
            return False
    for k, expected in (cond.get("flags") or {}).items():
        if state["flags"].get(k) != expected:
            return False
    for k, n in (cond.get("flag_gte") or {}).items():
        try:
            if float(state["flags"].get(k) or 0) < float(n):
                return False
        except (TypeError, ValueError):
            # author-typed junk threshold: gate stays closed, never crashes
            return False
    # ── Cast conditions (character system, 2026-08-25) ──────────────────
    if "wearing" in cond and not _check_wearing(cond["wearing"], state):
        return False
    if "part" in cond and not _check_part(cond["part"], state):
        return False
    cf = cond.get("cast_field")
    if isinstance(cf, dict):
        c = (state.get("cast") or {}).get(cf.get("char")) or {}
        if (c.get("fields") or {}).get(cf.get("key")) != cf.get("value"):
            return False
    cg = cond.get("cast_gte")
    if isinstance(cg, dict):
        c = (state.get("cast") or {}).get(cg.get("char")) or {}
        try:
            if float((c.get("fields") or {}).get(cg.get("key")) or 0) < float(cg.get("n", 0)):
                return False
        except (TypeError, ValueError):
            return False
    return True


def _effect_events(effects, state):
    """Resolve a declared effects block ({set, adjust, gives, emotions,
    emotions_remove}) into journal events. `adjust` is numeric arithmetic
    on a flag — HP, gold, trust ({"adjust": {"jack_hp": -2}})."""
    events = []
    for k, v in (effects.get("set") or {}).items():
        events.append({"event": "state_set", "key": k, "value": v})
    for k, delta in (effects.get("adjust") or {}).items():
        events.append({"event": "state_adjust", "key": k, "delta": delta})
    if effects.get("gives") and effects["gives"] not in state["inventory"]:
        events.append({"event": "gained", "item": effects["gives"]})
    add = effects.get("emotions") or []
    rem = effects.get("emotions_remove") or []
    if add or rem:
        events.append({"event": "emotions", "add": add, "remove": rem})
    xadd = effects.get("extras") or []
    xrem = effects.get("extras_remove") or []
    if xadd or xrem:
        events.append({"event": "extras", "add": xadd, "remove": xrem})
    # ── Cast effects (character system, 2026-08-25): wear/unwear move gear
    # between body and inventory (rulings B+C); part sets part state
    # (held hands, gloved fingers); cast_set/cast_adjust are the sheet's
    # set/adjust ({"cast_adjust": {"sapphire": {"trust": 1}}}).
    for spec in _as_list(effects.get("wear")):
        if spec.get("char") and spec.get("slot") and spec.get("item"):
            events.append({"event": "wore", "char": spec["char"],
                           "slot": spec["slot"], "item": spec["item"]})
    for spec in _as_list(effects.get("unwear")):
        if spec.get("char") and spec.get("slot"):
            events.append({"event": "unwore", "char": spec["char"],
                           "slot": spec["slot"]})
    for spec in _as_list(effects.get("part")):
        if spec.get("char") and spec.get("part") and spec.get("key"):
            events.append({"event": "part_set", "char": spec["char"],
                           "part": spec["part"], "key": spec["key"],
                           "value": spec.get("value", True)})
    for char, kv in (effects.get("cast_set") or {}).items():
        for k, v in (kv.items() if isinstance(kv, dict) else []):
            events.append({"event": "cast_set", "char": char, "key": k, "value": v})
    for char, kv in (effects.get("cast_adjust") or {}).items():
        for k, d in (kv.items() if isinstance(kv, dict) else []):
            events.append({"event": "cast_adjust", "char": char, "key": k, "delta": d})
    # Teleport (Krem's wand, 2026-08-21): {"goto": room_id} moves the
    # player as part of any effect. Emits the moved event ONLY — the
    # destination's on_enter does not fire on a teleport (documented;
    # keeps effects non-recursive).
    goto = effects.get("goto")
    if goto is not None:
        try:
            events.append({"event": "moved", "to": int(goto)})
        except (TypeError, ValueError):
            logger.warning(f"[STORY] goto effect with non-numeric room: {goto!r} — ignored")
    # Lightbox (Krem 2026-08-22): {"show": "name.webp"} or {"show": {"image":
    # ..., "caption": ...}} pops the image full-screen for the player. The
    # event carries name + caption only — URL resolution stays view-side
    # (full_state), so replay never bakes a path.
    show = effects.get("show")
    if isinstance(show, str) and show.strip():
        events.append({"event": "shown", "image": show.strip(), "caption": ""})
    elif isinstance(show, dict) and str(show.get("image") or "").strip():
        events.append({"event": "shown", "image": str(show["image"]).strip(),
                       "caption": str(show.get("caption") or "")})
    elif show is not None:
        logger.warning(f"[STORY] show effect with no usable image: {show!r} — ignored")
    return events


def blockers(room, state):
    """What stands between the player and continuing — derived from failing
    exit conditions (author blocked_message or an auto line) plus any
    room-declared 'blockers' entries whose 'until' condition is unmet.
    Feeds the ghost block and the sidebar. Exits sharing one message merge
    into a single entry (Krem 2026-08-20: six lockboxed doors read as one
    fact, not six paragraphs)."""
    order = []     # messages in first-seen order
    labels = {}    # message -> [exit labels] ([] = room-declared blocker)
    def _add(label, msg):
        if msg not in labels:
            labels[msg] = []
            order.append(msg)
        if label:
            labels[msg].append(label)
    for ex in visible_exits(room, state):
        cond = ex.get("condition")
        if cond and not check_condition(cond, state):
            if ex.get("blocked_message"):
                _add(f"'{ex.get('label')}'", ex["blocked_message"])
            else:
                needs = []
                # str() everywhere: this runs inside the 12s status poll, and
                # an author-typed non-string ({"has": 3} via API/pack) used to
                # AttributeError here and 500 the whole sidebar (2026-08-21).
                if cond.get("has"):
                    needs.append(f"needs {str(cond['has']).replace('_', ' ')}")
                if cond.get("flag"):
                    needs.append(f"needs {str(cond['flag']).replace('_', ' ')}")
                if cond.get("did"):
                    needs.append(f"needs {str(cond['did']).replace('_', ' ')} used first")
                if cond.get("solved"):
                    needs.append(f"needs {str(cond['solved']).replace('_', ' ')} solved")
                for k, v in (cond.get("flags") or {}).items():
                    needs.append(f"needs {str(k).replace('_', ' ')} = {v}")
                _add(f"'{ex.get('label')}'", ", ".join(needs) or "blocked")
    for b in room.get("blockers", []):
        if not check_condition(b.get("until"), state):
            _add(None, str(b.get("text", "an unmet requirement")))
    return [f"{', '.join(labels[m])} — {m}" if labels[m] else m for m in order]


def _visible_objects(room, state):
    """The one visibility funnel. Besides hidden/found, an object may carry
    a top-level `condition` — it does not EXIST until the condition holds
    (the zork-line: imported object sets materialize when the house opens).
    Distinct from per-interaction conditions, which gate verbs on a visible
    object."""
    out = {}
    taken = {_key(t) for t in state.get("taken") or []}
    for name, obj in (room.get("objects") or {}).items():
        if _key(name) in taken:
            continue                      # pocketed — it left the room
        if obj.get("hidden") and name not in state["found"]:
            continue
        if not check_condition(obj.get("condition"), state):
            continue
        out[name] = obj
    return out


def visible_exits(room, state):
    """Exits that EXIST right now (exits editor, 2026-08-21): an exit may
    carry `visible_when` — a condition gating its existence (secret doors;
    the passage appears when the lever's flag sets). Distinct from
    `condition`, which BLOCKS traversal of a visible exit. The one exit
    funnel — look, move, blockers, ghost and sidebar all drink here."""
    return [ex for ex in (room.get("exits") or []) if isinstance(ex, dict)
            and check_condition(ex.get("visible_when"), state)]


def _carried_objects(story, all_rooms, state):
    """{name: (home_room, spec)} for taken objects — they left their room
    but ride with the player (Krem 2026-08-21: 'what she carries, she can
    use'). home_room anchors seal/dice keys so once-chances and sealed
    reveals stay spent/revealed wherever the object travels. Starting items
    (2026-08-22) resolve from the story-level pool with home_room None —
    their seal/dice keys are room-independent by construction."""
    taken = {_key(t) for t in state.get("taken") or []}
    # Worn is worn (Krem's B ruling 2026-08-25): gear on a body is not
    # `has:`-carried, but it stays usable/inspectable — worn names resolve
    # specs here so declared actions and look keep firing from the body.
    pool = taken | {_key(i) for c in (state.get("cast") or {}).values()
                    for i in (c.get("wearing") or {}).values()}
    out = {}
    if not pool:
        return out
    for rm in (all_rooms or {}).values():
        for n, o in (rm.get("objects") or {}).items():
            if _key(n) in pool and isinstance(o, dict) and n not in out:
                out[n] = (rm, o)
    for n, o in ((story or {}).get("items") or {}).items():
        if _key(n) in pool and isinstance(o, dict) and n not in out:
            out[n] = (None, o)
    return out


def _take_rider(obj, canon, verb, carried, target_n):
    """Compose law (2026-08-23, the look-shadow's mirror): a DECLARED take
    verb on a TAKEABLE object used to fire its message and silently skip
    the pocket. Now the declared machinery fires AND the object is taken —
    'what she says when it's picked up' is just a take action card."""
    if not (obj and obj.get("takeable")) or carried:
        return []
    if _key(canon) in TAKE_VERBS or _key(verb) in TAKE_VERBS:
        return [{"event": "taken", "target": target_n}]
    return []


def _declares(obj, verb):
    """Does this object declare `verb` as an interaction (aliases included)?
    Lets authored machinery win over the generic look read (2026-08-22)."""
    for vname, vspec in ((obj or {}).get("interactions") or {}).items():
        if not isinstance(vspec, dict):
            continue
        raw = vspec.get("aliases") or []
        if isinstance(raw, str):
            raw = [raw]
        if _key(verb) in {_key(vname)} | {_key(a) for a in raw}:
            return True
    return False


def _scene_summary(room, state, cap=None):
    """Spoiler-free scene line — title, template, visible objects, visible
    exits. Shared by look-room and the move result; hidden/unfound objects
    and solutions never appear (visibility is the same gate the ghost uses)."""
    tmpl = (room.get("template") or "").strip()
    if cap and len(tmpl) > cap:
        tmpl = tmpl[:cap] + "…"
    bits = [f"{room['title']}: {tmpl}"]
    vis = _visible_objects(room, state)
    if vis:
        bits.append("Here: " + ", ".join(vis))
    labels = ", ".join(f"'{e.get('label')}'" for e in visible_exits(room, state))
    bits.append(f"Exits: {labels or 'none'}")
    return " — ".join(bits)


def resolve(story, state, room, all_rooms, verb, target=None, answer=None):
    """Adjudicate one act. Returns (events, message, ok).

    Events are resolved OUTCOMES ready for the journal (turn stamped by the
    caller). `message` is what the AI gets back — outcome truth to narrate
    from, never the mechanics behind it.
    """
    verb = _norm(verb)
    target_n = _norm(target)

    if verb == "look":
        if target_n and target_n not in ("room", "around", "here"):
            _name, obj = _find({**_visible_objects(room, state),
                                **cast_part_objects(state)}, target_n)
            carried = False
            if not obj:
                # Carried things (Krem 2026-08-21): a taken object left its
                # room but rides in the inventory — look works wherever she
                # is, resolving the spec from whichever room shipped it.
                _name, pair = _find(_carried_objects(story, all_rooms, state), target_n)
                if pair:
                    obj = pair[1]
                    carried = True
            if not obj:
                # Bare inventory tokens (a `gives` reward has no object
                # spec) still acknowledge — the off-script license voice.
                if _key(target_n) in {_key(i) for i in state.get("inventory") or []}:
                    return [], (f"{target}: an item in the inventory — no further "
                                f"detail is tracked; describe it freely."), True
                return [], f"There is no '{target}' here to look at.", False
            if not _declares(obj, "look"):
                bits = [obj.get("desc", "nothing remarkable")]
                if obj.get("puzzle"):
                    solved = target_n in state["solved"]
                    bits.append(f"Its puzzle{' (SOLVED)' if solved else ''}: {obj['puzzle'].get('riddle', '')}")
                verbs = sorted({_norm(v) for v in (obj.get("interactions") or {})} |
                               ({"solve"} if obj.get("puzzle") else set()))
                if verbs:
                    bits.append(f"It responds to: {', '.join(verbs)}")
                return [], f"{target}{' (in the inventory)' if carried else ''}: " \
                    + " — ".join(bits), True
            # Authored look wins (2026-08-22, the clown_key lightbox): a
            # declared 'look' interaction used to be SHADOWED by the generic
            # read above — its message, effects (show!), seals and dice
            # never fired. Same law examine/inspect already follow: declared
            # machinery beats the built-in verb. Fall through to the
            # interaction path below, which re-finds the object (visible or
            # carried, both lanes).
        else:
            # look room — on-demand re-read (Krem 2026-08-20): the ghost
            # block already carries this each turn, but a single room that
            # CHANGES (placed objects, zork-line reveals) deserves an
            # explicit read.
            return [], _scene_summary(room, state), True

    if verb == "move":
        if not target_n:
            return [], "move needs a target — one of the exit labels.", False
        exit_match = None
        exits_now = visible_exits(room, state)
        for ex in exits_now:
            if _key(ex.get("label")) == _key(target_n) or (target_n.isdigit() and ex.get("to") == int(target_n)):
                exit_match = ex
                break
        if not exit_match:
            labels = ", ".join(f"'{e.get('label')}'" for e in exits_now)
            return [], f"No exit matches '{target}'. Exits here: {labels or 'none'}.", False
        if not check_condition(exit_match.get("condition"), state):
            return [], exit_match.get("blocked_message",
                                      f"The way '{exit_match['label']}' is closed to you — something is still required."), False
        # Dice on exits (F1, 2026-08-21) — same law as interaction rolls:
        # the ROLLED VALUE is journaled even on failure (replay never
        # re-rolls), branch effects fire either way, `once` spends the
        # chance under the same room-scoped key.
        pre_events, dice_line = [], ""
        roll = exit_match.get("roll")
        if roll is not None and not isinstance(roll, dict):
            logger.warning(f"[STORY] roll on exit '{exit_match.get('label')}' is "
                           f"{type(roll).__name__}, not an object — ignored")
            roll = None
        if roll:
            key = seal_key(room, exit_match.get("label"), "move")
            if roll.get("once") and key in state.get("rolled", []):
                return [], roll.get("retry_message",
                                    f"That chance is spent — the way '{exit_match['label']}' won't yield to another try."), False
            try:
                sides = max(2, int(roll.get("sides", 100)))
                beat = int(roll.get("beat", sides // 2 + 1))
            except (TypeError, ValueError):
                logger.warning(f"[STORY] non-numeric roll bounds on exit "
                               f"'{exit_match.get('label')}' — using d100")
                sides, beat = 100, 51
            value = random.randint(1, sides)
            won = value >= beat
            branch = roll.get("success" if won else "failure") or {}
            pre_events = [{"event": "rolled", "target": exit_match.get("label"),
                           "verb": "move", "room": room.get("id"),
                           "value": value, "beat": beat, "sides": sides, "success": won}]
            pre_events += _effect_events(branch, state)
            if not won:
                msg = branch.get("message") or (f"The way '{exit_match['label']}' "
                                                f"defeats the attempt this time.")
                return pre_events, f"\U0001F3B2 Rolled {value} of {sides} (needed {beat}+) — {msg}", False
            dice_line = f"\U0001F3B2 Rolled {value} of {sides} (needed {beat}+) — "
        if exit_match.get("to") is None or exit_match.get("generate"):
            return pre_events, ("That way leads somewhere no one has written yet — room generation "
                                "arrives in the next build. Narrate it as impassable for now."), False
        dest = all_rooms.get(exit_match["to"])
        if not dest:
            return pre_events, f"Exit '{exit_match['label']}' leads to a missing room ({exit_match['to']}) — author error; treat as blocked.", False
        events = pre_events + [{"event": "moved", "to": dest["id"]}]
        # Per-exit effects (the rope ladder snaps behind you) fire on the
        # traverse, before the destination's own on_enter.
        events += _effect_events(exit_match.get("effects") or {}, state)
        events += _effect_events(dest.get("on_enter") or {}, state)
        # The scene rides IN the tool result (server-Sapph spiral,
        # 2026-08-23): the ghost block she holds was built BEFORE the move
        # and still shows the old room — promising "details in your turn
        # context" sent her hunting for context that wasn't there yet, on
        # repeat. Promise and payload now arrive together; next turn's
        # ghost repaints with NEW SCENE as before.
        return events, dice_line + f"Moved to '{dest['title']}'. " \
            + _scene_summary(dest, state, cap=900), True

    if verb == "search":
        found = []
        taken = {_key(t) for t in state.get("taken") or []}
        for name, obj in (room.get("objects") or {}).items():
            if _key(name) in taken:
                continue                  # pocketed — can't be re-found
            # condition-gated objects don't EXIST yet — search can't find
            # what the zork-line hasn't materialized.
            if not check_condition(obj.get("condition"), state):
                continue
            if obj.get("hidden") and name not in state["found"] and _norm(obj.get("found_by", "search")) == "search":
                ev = {"event": "found", "target": name}
                if obj.get("gives"):
                    ev["item"] = obj["gives"]
                found.append((name, obj, ev))
        if not found:
            return [], "A careful search turns up nothing new here.", True
        events = [ev for _, _, ev in found]
        parts = []
        for name, obj, ev in found:
            got = f" (you now carry: {obj['gives']})" if obj.get("gives") else ""
            parts.append(f"{name} — {obj.get('desc', 'found')}{got}")
        return events, "Search reveals: " + "; ".join(parts), True

    if verb == "solve":
        name, obj = _find(_visible_objects(room, state), target_n)
        if not obj:
            name, obj = _find(room.get("objects") or {}, target_n)
        if not obj:
            # carried puzzles solve anywhere (solved list is name-global)
            name, pair = _find(_carried_objects(story, all_rooms, state), target_n)
            if pair:
                obj = pair[1]
        if not obj or (obj.get("hidden") and name not in state["found"]) \
                or not check_condition(obj.get("condition"), state):
            return [], f"There is no '{target}' here to solve.", False
        # Journal under the AUTHOR's key, not the player's spelling — state
        # lookups elsewhere (solved/found lists, conditions) use the
        # declared name.
        target_n = name
        puzzle = obj.get("puzzle")
        if not puzzle:
            return [], f"'{target}' isn't a puzzle.", False
        if target_n in state["solved"]:
            return [], f"'{target}' is already solved.", True
        if not answer:
            return [], "solve needs an answer.", False
        if _norm_answer(answer) not in answers_of(puzzle):
            return ([{"event": "attempted", "target": target_n}],
                    f"'{answer}' doesn't work on {target}. The attempt stands in the record.", True)
        events = [{"event": "solved", "target": target_n}]
        effects = obj.get("on_solve") or {}
        events += _effect_events(effects, state)
        msg = effects.get("message") or f"Solved: {target}."
        return events, msg, True

    # Room-declared interaction verbs (sleep, pull, open, ...). Each spec may
    # declare "aliases": ["drink","sip"] — forgiveness for near-verbs (the
    # guess-the-verb wound, Krem 2026-08-03). Canonical names only in
    # listings; aliases resolve silently and journal under the canon verb.
    parts = cast_part_objects(state)
    objs = {**_visible_objects(room, state), **parts}
    obj_name, obj = _find(objs, target_n)
    home, carried = room, False
    if obj is not None and obj_name in parts:
        home = None          # parts travel with the body — room-free seal/dice keys
    if not obj:
        # Ring two (Krem 2026-08-21): carried objects — declared verbs,
        # dice, seals and effects all fire from the pocket, in any room.
        # Mechanics are state-scoped; only seal/dice keys are room-scoped,
        # and those anchor to the object's HOME room (see _carried_objects).
        obj_name, pair = _find(_carried_objects(story, all_rooms, state), target_n)
        if pair:
            home, obj = pair
            carried = True
    if obj:
        target_n = obj_name                      # author's key wins for state
        canon, spec = None, None
        for vname, vspec in (obj.get("interactions") or {}).items():
            if not isinstance(vspec, dict):
                logger.warning(f"[STORY] interaction '{vname}' on '{obj_name}' is "
                               f"{type(vspec).__name__}, not an object — skipped")
                continue
            # aliases must be a LIST; a bare string iterates per-character and
            # silently registered every letter as a verb (finding 4.13).
            raw_aliases = vspec.get("aliases") or []
            if isinstance(raw_aliases, str):
                logger.warning(f"[STORY] aliases for '{vname}' on '{obj_name}' is a "
                               f"string, not a list — treating as one alias")
                raw_aliases = [raw_aliases]
            names = {_key(vname)} | {_key(a) for a in raw_aliases}
            if _key(verb) in names:
                canon, spec = _norm(vname), vspec
                break
        if spec is not None:
            if not check_condition(spec.get("condition"), state):
                return [], spec.get("blocked_message", f"You can't {canon} the {target} yet."), False
            # Sealed blanks (Krem 2026-08-04): the author leaves this reveal
            # BLANK and the PLAYER writes it mid-run via a popup — the text
            # never passes through the AI on its way in, so the storyteller
            # is genuinely surprised. Unfilled = the act HOLDS, honestly
            # (she learns a surprise exists, never what) — fallback fires
            # only on the player's explicit Skip, so a fast open can't burn
            # the blank before the player has had their shot.
            sealed = spec.get("sealed")
            if isinstance(sealed, dict):
                key = seal_key(home, target_n, canon)
                legacy = legacy_seal_key(target_n, canon)
                revealed = state.get("revealed", [])
                if key in revealed or legacy in revealed:
                    text = (seal_state(state.get("seals"), room, target_n, canon)
                            or sealed.get("fallback") or "")
                    return [], f"Already revealed — the {target} held: “{text}”. Still canon.", True
                filled = seal_state(state.get("seals"), room, target_n, canon)
                skipped = state.get("seal_skipped", [])
                if filled is None and (key in skipped or legacy in skipped):
                    filled = sealed.get("fallback")
                    origin = "the author's line — "
                else:
                    origin = "written by the player just now, outside your knowledge — "
                if filled is None:
                    # target/verb/wait ride the event so session.act can run
                    # the live wait (Krem 2026-08-06) without re-walking the
                    # room for the spec — the fold ignores extra fields, and
                    # all three are already journal-public.
                    return ([{"event": "seal_held", "key": key,
                              "target": target_n, "verb": canon,
                              "wait": sealed.get("wait")}],
                            sealed.get("hold_message",
                                       f"The {target} holds something the player hasn't "
                                       f"written yet — this reveal is theirs to author, and "
                                       f"it isn't in yet. Try the same act again next turn."), False)
                events = [{"event": "interacted", "target": target_n, "verb": canon},
                          {"event": "revealed", "key": key}]
                events += _effect_events(spec, state)
                events += _take_rider(obj, canon, verb, carried, target_n)
                return events, (f"\U0001F512 Revealed for the first time: “{filled}” "
                                f"({origin}story canon now; narrate the discovery)."), True
            # Dice (Krem's ruling 2026-08-03): "roll": {sides, beat, once,
            # success: {effects+message}, failure: {...}}. The ROLLED VALUE is
            # journaled, so replay/revert stay pure — chance happens once,
            # history keeps it. Effects reuse the whole existing machinery
            # (set/adjust/gives/emotions), so rolls can bypass riddles, open
            # paths, drop hidden items, move stats. SPECIAL-style stat
            # modifiers ride later ("modifier_flag" adds a flag's value).
            roll = spec.get("roll")
            if roll is not None and not isinstance(roll, dict):
                logger.warning(f"[STORY] roll on '{obj_name}.{canon}' is "
                               f"{type(roll).__name__}, not an object — ignored")
                roll = None
            if roll:
                key = seal_key(home, target_n, canon)
                rolled = state.get("rolled", [])
                if roll.get("once") and (key in rolled
                                         or legacy_seal_key(target_n, canon) in rolled):
                    return [], roll.get("retry_message",
                                        f"That chance is spent — the {target} won't yield to another try."), False
                try:
                    sides = max(2, int(roll.get("sides", 100)))
                    beat = int(roll.get("beat", sides // 2 + 1))
                except (TypeError, ValueError):
                    logger.warning(f"[STORY] non-numeric roll bounds on "
                                   f"'{obj_name}.{canon}' — using d100")
                    sides, beat = 100, 51
                value = random.randint(1, sides)
                won = value >= beat
                branch = roll.get("success" if won else "failure") or {}
                events = [{"event": "rolled", "target": target_n, "verb": canon,
                           "room": (home or {}).get("id"),
                           "value": value, "beat": beat, "sides": sides, "success": won}]
                events += _effect_events(branch, state)
                if won:
                    events += _take_rider(obj, canon, verb, carried, target_n)
                msg = branch.get("message") or (f"You {canon} the {target}." if won
                                                else f"The {canon} fails.")
                return events, f"\U0001F3B2 Rolled {value} of {sides} (needed {beat}+) — {msg}", True
            events = [{"event": "interacted", "target": target_n, "verb": canon}]
            events += _effect_events(spec, state)
            events += _take_rider(obj, canon, verb, carried, target_n)
            return events, spec.get("message", f"You {canon} the {target}."), True

    # Dressing (character system, 2026-08-25): `wear` moves inventory gear
    # onto a body (`wears` slot on the item spec names where); `remove`
    # sends it back to the inventory (ruling C). answer = cast id; defaults
    # to the player-controlled character — the player dresses themself, she
    # dresses via answer or authored wear effects. Declared wear/remove
    # interactions on an object win (the loop above already returned).
    if _key(verb) in ("wear", "remove"):
        if not target_n:
            return [], f"{verb} needs a target item.", False
        cast = state.get("cast") or {}
        cid = _norm(answer) if answer and _norm(answer) in cast else next(
            (i for i, c in cast.items() if c.get("controlled_by") == "player"),
            next(iter(cast), None))
        if not cid:
            return [], "No cast records in this story — nothing tracked can dress.", False
        cname = cast[cid].get("name", cid)
        if _key(verb) == "remove":
            w = cast[cid].get("wearing") or {}
            slot = next((sl for sl, i in w.items()
                         if _key(i) == _key(target_n) or _key(sl) == _key(target_n)), None)
            if not slot:
                return [], f"{cname} isn't wearing '{target}'.", False
            return ([{"event": "unwore", "char": cid, "slot": slot}],
                    f"{cname} removes the {w[slot]} — it goes to the inventory.", True)
        inv_name = next((i for i in state.get("inventory") or []
                         if _key(i) == _key(target_n)), None)
        if not inv_name:
            return [], f"'{target}' isn't in the inventory — only carried gear can be worn.", False
        _n, ispec = _find(story.get("items") or {}, inv_name)
        if not ispec:
            _n, pair = _find(_carried_objects(story, all_rooms, state), inv_name)
            ispec = pair[1] if pair else None
        slot = (ispec or {}).get("wears")
        if not slot:
            return [], (f"Nothing tracked marks '{target}' wearable (no `wears` slot on "
                        f"its item spec) — narrate freely; the tracked world won't change."), False
        return ([{"event": "wore", "char": cid, "slot": slot, "item": inv_name}],
                f"{cname} now wears the {inv_name} ({slot}).", True)

    # Takeable (Krem 2026-08-20): the object ITSELF moves into inventory and
    # leaves the room — the classic verb, journaled so replay stays pure.
    # Declared 'take' interactions win (the loop above already returned);
    # non-takeable objects fall through to the off-script license so
    # narrative pick-ups stay free.
    if obj and obj.get("takeable") and _key(verb) in TAKE_VERBS:
        if carried:
            return [], f"The {target} is already in the inventory.", True
        events = [{"event": "taken", "target": target_n}]
        return events, obj.get(
            "take_message",
            f"You take the {target} — it's in the inventory now."), True

    # Off-script acts are a FEATURE (Krem 2026-08-03, the bracelet-overboard
    # incident): no mechanical hook means the DM improvises — a license, not
    # a refusal. Refusals taught the model to force wrong mappings.
    # examine/inspect fall back to LOOK — but only HERE, past the declared-
    # interaction path: titanic's ice-shavings declares `examine` as a real
    # verb, so a global alias would shadow authored machinery (2026-08-21).
    if _key(verb) in ("examine", "inspect"):
        return resolve(story, state, room, all_rooms, "look", target, answer)
    if obj:
        verbs = sorted({_norm(v) for v in (obj.get("interactions") or {})} | {"look"} |
                       ({"solve"} if obj.get("puzzle") else set()))
        return [], (f"No mechanical hook for '{verb}' on the {target} — this act is yours "
                    f"to narrate freely, with honest consequences in the fiction; the "
                    f"tracked world won't change. (Its mechanical hooks: {', '.join(verbs)}.)"), True
    if target:
        return [], (f"Nothing tracked by the name '{target}' here — if it exists in the "
                    f"fiction, narrate the act freely; the tracked world won't change."), True
    return [], f"Unknown act '{verb}'. Generic verbs: {', '.join(GENERIC_VERBS)}; rooms declare the rest.", False
