# engine/state.py — the event journal (Krem's design, 2026-07-13).
#
# No save system, no state store: every resolved event appends to a journal
# keyed by (story, chat). Current state is a pure fold over the journal —
# replay of 1000+ events is sub-ms, complete BY CONSTRUCTION because every
# mutation passes through the one-tool referee. Revert = truncate journal at
# turn N (+ chat truncation, handled by the tool layer). Events log resolved
# OUTCOMES, never intents — replay uses no LLM, no randomness, no clock.
#
# Storage (vault v1.3, 2026-08-15): rows in core's plugin_chat_data table,
# NOT files — journals/active/costumes follow the chat through vault (sealed
# to '@enc1:'), rename, and delete. The old user/story_saves/ files are
# retired unread (no-migration ruling — alpha, fresh start). Sealed-blank
# player text now seals with the chat instead of sitting plaintext on disk.
# Keys: 'story:active' + 'story:monolith' (per chat, seq0), and per story
# 'story:journal:<slug>' (append rows), ':run<n>' archives, 'story:reverted:
# <slug>' (revert forensics).
import json
import logging
import threading

logger = logging.getLogger(__name__)
_anchor_warned = False   # one warning per process, not five per turn

_lock = threading.Lock()

K_ACTIVE = "story:active"
K_MONO = "story:monolith"

_CS = None


def _cs():
    """The plugin's chat-scoped store (core-owned; cached veneer)."""
    global _CS
    if _CS is None:
        from core.plugin_loader import plugin_loader
        _CS = plugin_loader.get_chat_state("game-room")
    return _CS


def _jkey(story):
    return f"story:journal:{story}"


def get_dynamic():
    """{prompt_name: {content, privacy_required}} — rendered story costumes
    across every VISIBLE chat, merged into pack registration so story
    prompts always resolve after a reboot. A hidden chat's costume is
    structurally absent (core's hidden filter — the old P3-T16 manual
    filter's job, now unskippable)."""
    out = {}
    try:
        for chat, row in _cs().get_all_chats(K_MONO).items():
            if isinstance(row, dict) and row.get("name"):
                out[row["name"]] = {
                    "content": row.get("content", ""),
                    "privacy_required": bool(row.get("privacy_required")),
                    # kind 'story': hidden from every prompt picker; the
                    # engine activates costumes by exact name (unfiltered).
                    "kind": "story",
                }
    except Exception as e:
        logger.warning(f"[STORY] dynamic monolith read failed: {e}")
    return out


def save_dynamic(name, content, privacy_required=False, chat=None):
    """Persist a rendered costume ON ITS CHAT'S ROW — it seals, renames and
    dies with the playthrough. Privacy rides WITH the rendered prompt
    (finding 2.10). chat is required in the DB world; the kwarg default
    keeps the old signature importable."""
    if not chat:
        raise ValueError("save_dynamic needs the owning chat")
    _cs().put(chat, K_MONO, {"name": name, "content": content,
                             "privacy_required": bool(privacy_required)})


def drop_dynamic(name):
    """Drop a costume by prompt name, whichever visible chat holds it. A
    name nobody holds (already replaced, legacy key) is a no-op."""
    try:
        for chat, row in _cs().get_all_chats(K_MONO).items():
            if isinstance(row, dict) and row.get("name") == name:
                _cs().delete(chat, K_MONO)
    except Exception as e:
        logger.warning(f"[STORY] drop_dynamic({name!r}) failed: {e}")


# ── Active-story map (chat → story) ─────────────────────────────────────────

def get_active():
    """{chat_name: {story, prev_prompt, ...}} — which VISIBLE chats are
    mid-story. Hidden chats answer like they never happened."""
    try:
        return _cs().get_all_chats(K_ACTIVE)
    except Exception:
        return {}


def get_active_entry(chat):
    """One chat's active entry (hot-path form — the ghost hook runs this
    every player message; no cross-chat sweep needed)."""
    return _cs().get(chat, K_ACTIVE)


def set_active(chat, story, prev_prompt):
    _cs().put(chat, K_ACTIVE, {"story": story, "prev_prompt": prev_prompt})


def update_active(chat, **fields):
    """Update fields on an existing active entry (e.g. paused=True).
    Returns False if the chat has no active story."""
    with _lock:
        entry = _cs().get(chat, K_ACTIVE)
        if entry is None:
            return False
        entry.update(fields)
        _cs().put(chat, K_ACTIVE, entry)
        return True


def clear_active(chat):
    with _lock:
        entry = _cs().get(chat, K_ACTIVE)
        if entry is not None:
            _cs().delete(chat, K_ACTIVE)
        return entry


# ── User layer: placed objects + room-text overrides (open-mansion v1) ──────
# One seq-0 row per (chat, slug): {"objects": {room_id_str: {name: spec}},
# "rooms": {room_id_str: {template, player_desc}}}. put-only (never append —
# history.py's mixing rule). Rides vault/rename/delete like the journal.

def _okey(story):
    return f"story:objects:{story}"


# Serializes every read-modify-write of the user layer (2026-08-21 hunt,
# race R1: two editor lanes — or an editor lane and story_place — doing
# bare get→mutate→save clobbered each other's whole-blob writes). RLock:
# create_user_room allocates its id and then writes through
# _mutate_room_layer inside the same critical section.
layer_lock = threading.RLock()


def get_user_layer(story, chat):
    row = _cs().get(chat, _okey(story))
    return row if isinstance(row, dict) else {}


def save_user_layer(story, chat, data):
    _cs().put(chat, _okey(story), data)


# ── Journal ──────────────────────────────────────────────────────────────────

def _stamp_anchor(chat, event):
    """Turn anchor (Krem's ruling 2026-08-03): every event records the chat's
    message count at write time, so a future revert can trim the transcript
    back to the matching point. Time-machine rule — anchors must exist
    before anyone can travel to them. Best-effort; replay ignores it."""
    if "msg_index" in event:
        return
    try:
        from core.api_fastapi import get_system
        system = get_system()
        # `is not None`, never truthiness: SessionManager defines __len__,
        # so an empty chat makes the whole manager falsy (found 2026-08-03).
        sm = system.llm_chat.session_manager if system else None
        if sm is not None and sm.get_active_chat_name() == chat:
            event["msg_index"] = len(sm.get_messages_for_display())
    except Exception as e:
        global _anchor_warned
        if not _anchor_warned:
            _anchor_warned = True
            logger.warning(f"[STORY] turn anchors not recording (first failure: {e}) — revert-to-message will lack alignment for this run")


def append(story, chat, event):
    """Append one resolved event. Caller supplies {'event': ..., ...};
    the current turn number is stamped in by the caller via 'turn'.
    Returns True if the write landed, False if the store refused it —
    callers that report outcomes MUST check (2026-08-21 fix-wave: a
    swallowed refusal made act() narrate moves the journal never got)."""
    _stamp_anchor(chat, event)
    # Sealed-vault backstop (P3-T9, now core-enforced): the store RAISES on
    # a hidden chat's write — catch and warn to keep this rail no-raise for
    # its per-turn callers. Loud, matching the fail-loudly ruling.
    with _lock:
        try:
            _cs().append(chat, _jkey(story), event)
            return True
        except Exception as e:
            logger.warning(f"[STORY] journal write refused: {e}")
            return False


def append_many(story, chat, events):
    """Append a resolve's events in ONE transaction — all land or none
    (2026-08-21 torn-commit incident: per-event appends journaled `hold`
    but died before its set-flag, leaving replay half a turn). Same anchor
    stamping and refusal-returns-False contract as append."""
    if not events:
        return True
    for ev in events:
        _stamp_anchor(chat, ev)
    with _lock:
        try:
            _cs().append_many(chat, _jkey(story), events)
            return True
        except Exception as e:
            logger.warning(f"[STORY] journal write refused: {e}")
            return False


def read_journal(story, chat):
    return _cs().read_all(chat, _jkey(story))


def journal_meta(story, chat):
    """{'rows': n, 'updated_at': max-ISO} or None — 'does a playthrough
    exist, and how fresh' without reading a row (last_played's mtime)."""
    return _cs().meta(chat, _jkey(story))


def truncate(story, chat, turn):
    """Revert: drop all events with turn > N (single timeline — the dead
    branch is archived to a forensics key, then gone).

    Read INSIDE the lock: reading first meant any event appended while the
    revert ran was erased by the rewrite and never archived — silent,
    unrecoverable loss of a turn the player just took (finding 2.9). The
    lock serializes against append(); the final replace is atomic in the
    store."""
    with _lock:
        events = _cs().read_all(chat, _jkey(story))
        keep = [e for e in events if e.get("turn", 0) <= turn]
        dropped = events[len(keep):]
        for e in dropped:
            _cs().append(chat, f"story:reverted:{story}", e)
        _cs().replace(chat, _jkey(story), keep)
    return len(dropped)


def rewind_to_match(story, chat, msg_count):
    """Two-ledger regen (plan tmp/regen-two-ledger-plan.md): the chat was
    rewound (regenerate/delete), so journal events anchored AT or PAST the
    current message count belong to turns whose chat messages no longer
    exist. Drop those turns — forensics-archived, same law as truncate —
    SALVAGING the player's sealed text: their words survive any rewind
    (re-anchored at the cut), while the reveal event drops, so the blank
    re-closes still filled and the ✉ chip offers it back for editing.

    Conservative by design (F1/F2 ruling 2026-08-24): any doomed event
    missing its anchor ⇒ no-op — anchors are best-effort, and we never do
    state surgery on ambiguity. Returns {"turn", "dropped", "salvaged"}
    when a rewind happened, else None."""
    with _lock:
        events = _cs().read_all(chat, _jkey(story))
        doomed_turns = {e["turn"] for e in events
                       if e.get("event") == "turn_tick"
                       and isinstance(e.get("msg_index"), int)
                       and e["msg_index"] >= msg_count}
        if not doomed_turns:
            return None
        t_cut = min(doomed_turns) - 1
        keep = [e for e in events if e.get("turn", 0) <= t_cut]
        drop = [e for e in events if e.get("turn", 0) > t_cut]
        if any(not isinstance(e.get("msg_index"), int) for e in drop):
            logger.warning(f"[STORY] chat rewound on '{chat}' but the doomed "
                           f"span has unanchored events — leaving the world alone")
            return None
        salvaged = []
        for e in drop:
            if e.get("event") == "sealed":
                s = {k: v for k, v in e.items() if k != "msg_index"}
                s["turn"] = t_cut
                _stamp_anchor(chat, s)     # fresh anchor: survives the NEXT rewind too
                salvaged.append(s)
        for e in drop:
            _cs().append(chat, f"story:reverted:{story}", e)
        _cs().replace(chat, _jkey(story), keep + salvaged)
        return {"turn": t_cut, "dropped": len(drop), "salvaged": len(salvaged)}


def new_run(story, chat):
    """Fresh playthrough: archive the existing journal aside as run<n>
    (never erased — same law as revert forensics), leaving the live key
    empty for the new start."""
    with _lock:
        events = _cs().read_all(chat, _jkey(story))
        if not events:
            return
        n = 1
        while _cs().read_all(chat, f"{_jkey(story)}:run{n}"):
            n += 1
        _cs().replace(chat, f"{_jkey(story)}:run{n}", events)
        _cs().delete(chat, _jkey(story))


# ── Replay (pure fold) ───────────────────────────────────────────────────────

def initial_state():
    return {
        "room": None, "turn": 0, "turns_in_room": 0,
        "inventory": [], "flags": {}, "emotions": [], "extras": [],
        "solved": [], "found": [], "used": [], "rolled": [], "taken": [],
        "ended": False,
        "seals": {}, "seal_skipped": [], "seal_holds": {}, "revealed": [],
        "shown": [],
    }


def apply_event(state, ev):
    """Apply ONE resolved event. Pure, deterministic, no I/O."""
    kind = ev.get("event")
    if kind == "started":
        # A start is a NEW playthrough — reset everything (an old 'ended'
        # event must not poison the fresh run; found 2026-07-14, the
        # "story won't turn back on" bug).
        fresh = initial_state()
        state.clear()
        state.update(fresh)
        state["room"] = ev["room"]
    elif kind == "turn_tick":
        state["turn"] = ev.get("turn", state["turn"] + 1)
        state["turns_in_room"] += 1
    elif kind == "moved":
        state["room"] = ev["to"]
        state["turns_in_room"] = 0
    elif kind == "found":
        if ev["target"] not in state["found"]:
            state["found"].append(ev["target"])
        item = ev.get("item")
        if item and item not in state["inventory"]:
            state["inventory"].append(item)
    elif kind == "solved":
        if ev["target"] not in state["solved"]:
            state["solved"].append(ev["target"])
    elif kind == "state_set":
        state["flags"][ev["key"]] = ev["value"]
    elif kind == "state_adjust":
        base = state["flags"].get(ev["key"]) or 0
        try:
            state["flags"][ev["key"]] = base + ev["delta"]
        except TypeError:
            state["flags"][ev["key"]] = ev["delta"]
    elif kind == "gained":
        if ev["item"] not in state["inventory"]:
            state["inventory"].append(ev["item"])
    elif kind == "emotions":
        for e in ev.get("add", []):
            if e not in state["emotions"]:
                state["emotions"].append(e)
        for e in ev.get("remove", []):
            if e in state["emotions"]:
                state["emotions"].remove(e)
    elif kind == "extras":
        for e in ev.get("add", []):
            if e not in state["extras"]:
                state["extras"].append(e)
        for e in ev.get("remove", []):
            if e in state["extras"]:
                state["extras"].remove(e)
    elif kind == "taken":
        # The object left the room and became inventory (takeable, 2026-08-20)
        taken = state.setdefault("taken", [])
        if ev.get("target") and ev["target"] not in taken:
            taken.append(ev["target"])
        if ev.get("target") and ev["target"] not in state["inventory"]:
            state["inventory"].append(ev["target"])
    elif kind == "interacted":
        # Touched-object memory (Krem 2026-08-03: the sketchbook checkmark).
        # Journals always carried these events — old playthroughs get their
        # checkmarks retroactively on replay. setdefault: pre-'used' saves.
        used = state.setdefault("used", [])
        if ev.get("target") and ev["target"] not in used:
            used.append(ev["target"])
    elif kind == "rolled":
        # Dice attempts (value journaled at roll time — replay never re-rolls).
        # Keys are room-scoped since 2026-08-05; events written before that
        # carry no room and replay under the old unscoped key, which the
        # referee still honors (finding 3.2).
        rolled = state.setdefault("rolled", [])
        k = (f"{ev['room']}:{ev.get('target')}:{ev.get('verb')}"
             if ev.get("room") is not None else f"{ev.get('target')}:{ev.get('verb')}")
        if k not in rolled:
            rolled.append(k)
    # Sealed blanks (Krem 2026-08-04): the player's text is journaled at
    # typing time — same law as dice: replay never re-prompts, revert to
    # before the fill re-opens the blank.
    elif kind == "sealed":
        state.setdefault("seals", {})[ev["key"]] = ev.get("text") or ""
    elif kind == "seal_skipped":
        sk = state.setdefault("seal_skipped", [])
        if ev["key"] not in sk:
            sk.append(ev["key"])
    elif kind == "seal_held":
        # She reached for it before the player wrote it — the client watches
        # this count to re-raise the popup with urgency.
        holds = state.setdefault("seal_holds", {})
        holds[ev["key"]] = holds.get(ev["key"], 0) + 1
    elif kind == "revealed":
        rv = state.setdefault("revealed", [])
        if ev["key"] not in rv:
            rv.append(ev["key"])
    elif kind == "shown":
        # Lightbox history (2026-08-22): every show-image effect, in order.
        # The list length is the client's dedup seq; revert un-shows.
        state.setdefault("shown", []).append(
            {"image": ev.get("image"), "caption": ev.get("caption") or "",
             "turn": ev.get("turn", 0)})
    elif kind == "ended":
        state["ended"] = True
    # room_created carries no direct state change beyond its companion
    # events; room_created rooms load from the playthrough dir.
    return state


def replay(story, chat):
    state = initial_state()
    for ev in read_journal(story, chat):
        apply_event(state, ev)
    return state
