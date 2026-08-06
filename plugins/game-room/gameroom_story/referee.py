# engine/referee.py — the rulebook. The AI is dungeon master AND a player
# that can't cheat: it acts through ONE tool, this module validates every
# act against the room JSON, and solutions/hidden objects never enter the
# AI's context (render.py builds the visible layer; the raw room file is
# engine-side only). Returns resolved-outcome events for the journal.
import logging
import random

logger = logging.getLogger(__name__)

GENERIC_VERBS = ("move", "look", "search", "solve")


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


def check_condition(cond, state):
    """Edge/object condition: {"has": item} and/or {"flags": {k: expected}}
    (also accepts {"flag": name} as a truthy check)."""
    if not cond:
        return True
    if cond.get("has") and cond["has"] not in state["inventory"]:
        return False
    flag = cond.get("flag")
    if flag and not state["flags"].get(flag):
        return False
    for k, expected in (cond.get("flags") or {}).items():
        if state["flags"].get(k) != expected:
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
    return events


def blockers(room, state):
    """What stands between the player and continuing — derived from failing
    exit conditions (author blocked_message or an auto line) plus any
    room-declared 'blockers' entries whose 'until' condition is unmet.
    Feeds the ghost block and the sidebar accordion."""
    out = []
    for ex in room.get("exits", []):
        cond = ex.get("condition")
        if cond and not check_condition(cond, state):
            if ex.get("blocked_message"):
                out.append(f"'{ex.get('label')}' — {ex['blocked_message']}")
            else:
                needs = []
                if cond.get("has"):
                    needs.append(f"needs {cond['has'].replace('_', ' ')}")
                if cond.get("flag"):
                    needs.append(f"needs {cond['flag'].replace('_', ' ')}")
                for k, v in (cond.get("flags") or {}).items():
                    needs.append(f"needs {k.replace('_', ' ')} = {v}")
                out.append(f"'{ex.get('label')}' — {', '.join(needs) or 'blocked'}")
    for b in room.get("blockers", []):
        if not check_condition(b.get("until"), state):
            out.append(str(b.get("text", "an unmet requirement")))
    return out


def _visible_objects(room, state):
    out = {}
    for name, obj in (room.get("objects") or {}).items():
        if obj.get("hidden") and name not in state["found"]:
            continue
        out[name] = obj
    return out


def resolve(story, state, room, all_rooms, verb, target=None, answer=None):
    """Adjudicate one act. Returns (events, message, ok).

    Events are resolved OUTCOMES ready for the journal (turn stamped by the
    caller). `message` is what the AI gets back — outcome truth to narrate
    from, never the mechanics behind it.
    """
    verb = _norm(verb)
    target_n = _norm(target)

    if verb == "look":
        if target_n:
            _name, obj = _find(_visible_objects(room, state), target_n)
            if not obj:
                return [], f"There is no '{target}' here to look at.", False
            bits = [obj.get("desc", "nothing remarkable")]
            if obj.get("puzzle"):
                solved = target_n in state["solved"]
                bits.append(f"Its puzzle{' (SOLVED)' if solved else ''}: {obj['puzzle'].get('riddle', '')}")
            verbs = sorted({_norm(v) for v in (obj.get("interactions") or {})} |
                           ({"solve"} if obj.get("puzzle") else set()))
            if verbs:
                bits.append(f"It responds to: {', '.join(verbs)}")
            return [], f"{target}: " + " — ".join(bits), True
        return [], "The room's full details are in my turn context — narrate from them.", True

    if verb == "move":
        if not target_n:
            return [], "move needs a target — one of the exit labels.", False
        exit_match = None
        for ex in room.get("exits", []):
            if _key(ex.get("label")) == _key(target_n) or (target_n.isdigit() and ex.get("to") == int(target_n)):
                exit_match = ex
                break
        if not exit_match:
            labels = ", ".join(f"'{e.get('label')}'" for e in room.get("exits", []))
            return [], f"No exit matches '{target}'. Exits here: {labels or 'none'}.", False
        if not check_condition(exit_match.get("condition"), state):
            return [], exit_match.get("blocked_message",
                                      f"The way '{exit_match['label']}' is closed to you — something is still required."), False
        if exit_match.get("to") is None or exit_match.get("generate"):
            return [], ("That way leads somewhere no one has written yet — room generation "
                        "arrives in the next build. Narrate it as impassable for now."), False
        dest = all_rooms.get(exit_match["to"])
        if not dest:
            return [], f"Exit '{exit_match['label']}' leads to a missing room ({exit_match['to']}) — author error; treat as blocked.", False
        events = [{"event": "moved", "to": dest["id"]}]
        events += _effect_events(dest.get("on_enter") or {}, state)
        return events, f"Moved to '{dest['title']}'. The new room's details arrive in your turn context.", True

    if verb == "search":
        found = []
        for name, obj in (room.get("objects") or {}).items():
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
        if not obj or (obj.get("hidden") and name not in state["found"]):
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
    objs = _visible_objects(room, state)
    obj_name, obj = _find(objs, target_n)
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
                key = seal_key(room, target_n, canon)
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
                key = seal_key(room, target_n, canon)
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
                           "room": (room or {}).get("id"),
                           "value": value, "beat": beat, "sides": sides, "success": won}]
                events += _effect_events(branch, state)
                msg = branch.get("message") or (f"You {canon} the {target}." if won
                                                else f"The {canon} fails.")
                return events, f"\U0001F3B2 Rolled {value} of {sides} (needed {beat}+) — {msg}", True
            events = [{"event": "interacted", "target": target_n, "verb": canon}]
            events += _effect_events(spec, state)
            return events, spec.get("message", f"You {canon} the {target}."), True

    # Off-script acts are a FEATURE (Krem 2026-08-03, the bracelet-overboard
    # incident): no mechanical hook means the DM improvises — a license, not
    # a refusal. Refusals taught the model to force wrong mappings.
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
