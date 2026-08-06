# engine/render.py — the VISIBLE layer. The raw room file never enters the
# AI's context: this module renders what the room shows (template, exits,
# declared objects, riddle text) and withholds what it doesn't (solutions,
# unfound hidden objects). Plain English, one fact per line — the ghost
# block is rebuilt fresh every turn by the ghost rail (never persisted).
import logging

logger = logging.getLogger(__name__)

# build_ghost_message caps a plugin contribution at 2048 chars — trim the
# room template before we hit the cliff so the cap never truncates
# mid-structure.
_TEMPLATE_CAP = 900


def ghost_block(story, state, room):
    """The per-turn state block. Current-state only — replace semantics come
    free (ghost messages are rebuilt per LLM call, never persisted)."""
    lines = [
        f"Story: {story['meta'].get('title', story['meta']['slug'])} — I narrate from inside the story; anything mechanical goes through my story_act tool.",
        f"Turn: {state['turn']}",
        f"Room: {room['title']}",
    ]
    # GM conduct lives in the PROMPT (user-editable, previewable — no hidden
    # instructions, Krem's ruling 2026-08-03). This nudge stays engine-side
    # only because turns_in_room==0 is logic text can't express.
    if state.get("turns_in_room", 0) == 0:
        lines.append(
            "NEW SCENE — set it in my own voice first: what we see, who's here, "
            "what can be done, where the ways lead — so the player knows their "
            "options before anything moves. No story tools are needed to see "
            "the scene: everything is right here.")
    tmpl = room.get("template", "").strip()
    if len(tmpl) > _TEMPLATE_CAP:
        tmpl = tmpl[:_TEMPLATE_CAP] + "…"
    lines.append(f"Scene: {tmpl}")

    exits = []
    for ex in room.get("exits", []):
        d = f" — {ex['desc']}" if ex.get("desc") else ""
        exits.append(f"{ex.get('label')}{d}")
    lines.append("Exits: " + ("; ".join(exits) if exits else "none"))

    objs = []
    for name, obj in (room.get("objects") or {}).items():
        if obj.get("hidden") and name not in state["found"]:
            continue  # unfound hidden objects stay engine-side
        bits = [obj.get("desc", "")]
        if obj.get("puzzle"):
            solved = name in state["solved"]
            bits.append(f"puzzle{' (SOLVED)' if solved else ''}: {obj['puzzle'].get('riddle', 'unmarked')}")
        if obj.get("interactions"):
            bits.append("responds to: " + ", ".join(sorted(obj["interactions"])))
        objs.append(f"{name} — " + "; ".join(b for b in bits if b))
    if objs:
        lines.append("Objects: " + " | ".join(objs))

    lines.append("Inventory: " + (", ".join(state["inventory"]) or "empty"))
    if state["emotions"]:
        lines.append("Mood pieces active: " + ", ".join(state["emotions"]))
    for k, v in state["flags"].items():
        lines.append(f"{k.replace('_', ' ').capitalize()}: {v}")

    # Unmet requirements — the "you can't continue without" section
    from . import referee
    blocked = referee.blockers(room, state)
    if blocked:
        lines.append("Blocked paths: " + " | ".join(blocked))

    # Author-declared hints, gated on time-stuck-in-room (per-hint threshold).
    # Coerced like _due_hints, never trusted: this renders inside the ghost
    # hook, and since render moved BEFORE the turn tick (4.7 fix) a raw
    # int() raising here froze the story clock PERMANENTLY — every
    # turn-gated reveal dead, one warning per turn (post-fix review
    # 2026-08-05).
    for hint in room.get("ghost_hints", []):
        if not isinstance(hint, dict):
            continue
        try:
            after = int(hint.get("after_turns", 3) or 0)
        except (TypeError, ValueError):
            after = 3
        if state["turns_in_room"] >= after:
            w = str(hint.get("whisper", "")).strip()
            if w:
                lines.append(f"Hint (weave it in, don't announce it): {w}")

    return "\n".join(lines)


def resolve_piece(slug, ctype, name, components):
    """Story pieces resolve deep-namespace first: story_{slug}_{name} →
    story_engine_{name} → literal name. Returns (key, text) or None."""
    for key in (f"story_{slug}_{name}", f"story_engine_{name}", name):
        text = components.get(ctype, {}).get(key)
        if text:
            return key, text
    return None


def resolve_emotion_piece(slug, emotion, components):
    return resolve_piece(slug, "emotions", emotion, components)


def story_prompt(story, state, components, character=None, mode=None, local_ctx=None, conduct=None):
    """Render the story's ACTIVE prompt — ASSEMBLED from pieces, in one of
    three identity modes (Krem's ruling 2026-08-03):

      story    — TOTAL SWAP. The pack's role IS the character; the local
                 persona never enters the prompt. (Default when a role ships.)
      local    — the user's persona inside the story as themselves (the
                 original 2026-07-14 behavior; default when no role ships).
      combined — persona + mask: local character/relationship pieces stay,
                 engine glue makes the role an explicit costume on top.

    local_ctx (built by session._local_ctx from the chosen local prompt):
    {name, character_key, character_text, relationship_text, monolith_text}.
    Falls back to the legacy `character` piece key when absent. Delivered as
    a pack monolith so no global assembled-state ever mutates — other chats
    immune. Order mirrors assemble_prompt: guide, character, relationship,
    location, scenario(premise), format, extras, emotions."""
    slug = story["meta"]["slug"]
    title = story["meta"].get("title", slug)
    role = story["meta"].get("role") or {}
    role_name = (role.get("name") or "").strip()
    role_text = (role.get("text") or "").strip()
    player_role = (story["meta"].get("player_role") or "").strip()
    mode = mode or ("story" if role_text else "local")
    if mode in ("story", "combined") and not role_text:
        logger.warning(f"[STORY] mode '{mode}' needs a role but '{slug}' ships none — falling back to local")
        mode = "local"
    parts = []

    # Actor pieces (local persona) — from local_ctx, else the legacy key
    char_text = (local_ctx or {}).get("character_text") \
        or components.get("character", {}).get(character or "")
    rel_text = (local_ctx or {}).get("relationship_text")
    monolith = (local_ctx or {}).get("monolith_text")
    actor = ((local_ctx or {}).get("character_key") or character
             or (local_ctx or {}).get("name") or "myself")
    actor = str(actor).replace("_", " ").strip()
    actor_disp = actor[:1].upper() + actor[1:] if actor else "Myself"

    if mode == "story":
        parts.append(role_text if not role_name else f"I am {role_name}. {role_text}")
        if player_role:
            parts.append(f"The player beside me in this story is playing: {player_role}.")
    elif mode == "combined":
        parts.append(
            f'This is an interactive story. I am {actor_disp}, playing the role of '
            f'{role_name or "a character"} in "{title}". I stay in character as '
            f'{role_name or "the role"} inside the tale; underneath the mask I know '
            f'I am {actor_disp} — and my player knows it too.')
        base = char_text or monolith
        if base:
            parts.append(base)
        else:
            logger.warning(f"[STORY] no local character/monolith for combined mode — mask has no face under it")
        parts.append(f"In this story I am playing: {role_name} — {role_text}"
                     if role_name else f"In this story I am playing: {role_text}")
        if rel_text:
            parts.append(f"Relationship: {rel_text}")
        if player_role:
            parts.append(f"In the story, they are playing: {player_role}.")
    else:  # local — the user's persona, inside the story as themselves
        base = char_text or monolith
        if base:
            parts.append(base)
        else:
            logger.warning(f"[STORY] character piece '{character}' not found — prompt renders without one")
        if rel_text:
            parts.append(f"Relationship: {rel_text}")

    hit = resolve_piece(slug, "location", "scene", components)
    if hit:
        parts.append(f"You are currently {hit[1]}.")

    premise = story["meta"].get("premise", "").strip()
    if premise:
        parts.append(f"Scenario: {premise}")

    # Format = the AI's operating manual for story mode. Stories may name
    # their own piece via meta.format; else the engine's per-mode manual
    # (format_story / format_local / format_combined), else the classic one.
    fhit = None
    for fmt_name in ([story["meta"]["format"]] if story["meta"].get("format") else []) \
            + [f"format_{mode}", "format"]:
        fhit = resolve_piece(slug, "format", fmt_name, components)
        if fhit:
            break
    if fhit:
        parts.append(fhit[1])

    # GM conduct — the dual layer (session.conduct_for): universal style
    # shared by all stories (user-editable, per-story toggle) + this story's
    # DM guide (pack default, user override). Prompt-level = previewable.
    universal, dm = conduct or (None, None)
    if universal:
        parts.append(universal)
    if dm:
        parts.append(f"DM guide for this story: {dm}")

    for extra in state.get("extras", []):
        ehit = resolve_piece(slug, "extras", extra, components)
        if ehit:
            parts.append(ehit[1])

    for emotion in state["emotions"]:
        ehit = resolve_emotion_piece(slug, emotion, components)
        if ehit:
            parts.append(ehit[1])
        else:
            logger.debug(f"[STORY] no piece for emotion '{emotion}' (story {slug})")
    return "\n\n".join(parts)
