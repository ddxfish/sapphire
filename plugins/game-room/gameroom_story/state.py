# engine/state.py — the event journal (Krem's design, 2026-07-13).
#
# No save system, no state store: every resolved event appends to a JSONL
# journal keyed by (story, chat). Current state is a pure fold over the
# journal — replay of 1000+ events is sub-ms, complete BY CONSTRUCTION
# because every mutation passes through the one-tool referee. Revert =
# truncate journal at turn N (+ chat truncation, handled by the tool layer).
# Events log resolved OUTCOMES, never intents — replay uses no LLM, no
# randomness, no clock.
import json
import logging
import threading

from .rooms import SAVES_ROOT, save_dir

logger = logging.getLogger(__name__)
_anchor_warned = False   # one warning per process, not five per turn

_lock = threading.Lock()

ACTIVE_FILE = SAVES_ROOT / "active.json"
DYNAMIC_FILE = SAVES_ROOT / "_dynamic_monoliths.json"


def get_dynamic():
    """Rendered story prompts persisted across restarts — merged into every
    pack registration so story_{slug} always resolves after a reboot.

    A corrupt file is QUARANTINED, not silently swallowed: returning {} let
    the next save_dynamic overwrite the only copy of every active costume,
    and every story chat would quietly un-register (Tier 5, front D —
    PluginState already handles corruption this way, this didn't)."""
    if not DYNAMIC_FILE.exists():
        return {}
    try:
        data = json.loads(DYNAMIC_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
        raise ValueError(f"expected an object, got {type(data).__name__}")
    except Exception as e:
        from datetime import datetime
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        bad = DYNAMIC_FILE.with_suffix(f".json.bad-{stamp}")
        try:
            DYNAMIC_FILE.rename(bad)
            logger.error(f"[STORY] dynamic monolith sidecar corrupt ({e}) — quarantined "
                         f"to {bad.name}. Active story costumes must be re-asserted "
                         f"(enter the room, or story settings save).")
        except Exception as rename_err:
            logger.error(f"[STORY] dynamic sidecar corrupt ({e}) and could not be "
                         f"quarantined ({rename_err}) — next save will overwrite it.")
        return {}


def save_dynamic(name, content, privacy_required=False):
    with _lock:
        data = get_dynamic()
        # Privacy rides WITH the rendered prompt: the sidecar re-registers it
        # at every boot, so a flag dropped here is a send-gate lost forever
        # (finding 2.10).
        data[name] = {"content": content, "privacy_required": bool(privacy_required)}
        DYNAMIC_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = DYNAMIC_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(DYNAMIC_FILE)


def drop_dynamic(name):
    with _lock:
        data = get_dynamic()
        if data.pop(name, None) is not None:
            tmp = DYNAMIC_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(DYNAMIC_FILE)


# ── Active-story map (chat → story) ─────────────────────────────────────────

def get_active():
    """{chat_name: {story, prev_prompt}} — which chats are mid-story."""
    try:
        return json.loads(ACTIVE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def set_active(chat, story, prev_prompt):
    with _lock:
        data = get_active()
        data[chat] = {"story": story, "prev_prompt": prev_prompt}
        ACTIVE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = ACTIVE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(ACTIVE_FILE)


def update_active(chat, **fields):
    """Update fields on an existing active entry (e.g. paused=True).
    Returns False if the chat has no active story."""
    with _lock:
        data = get_active()
        if chat not in data:
            return False
        data[chat].update(fields)
        tmp = ACTIVE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(ACTIVE_FILE)
        return True


def clear_active(chat):
    with _lock:
        data = get_active()
        entry = data.pop(chat, None)
        if entry is not None:
            tmp = ACTIVE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(ACTIVE_FILE)
        return entry


# ── Journal ──────────────────────────────────────────────────────────────────

def journal_path(story, chat):
    return save_dir(story, chat) / "journal.jsonl"


def append(story, chat, event):
    """Append one resolved event. Caller supplies {'event': ..., ...};
    the current turn number is stamped in by the caller via 'turn'.

    Turn anchor (Krem's ruling 2026-08-03): every event records the chat's
    message count at write time, so a future revert can trim the transcript
    back to the matching point. Time-machine rule — anchors must exist
    before anyone can travel to them. Best-effort; replay ignores it."""
    if "msg_index" not in event:
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
    path = journal_path(story, chat)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")


def _read_journal_unlocked(path):
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                logger.warning(f"[STORY] skipping corrupt journal line in {path}")
    return out


def read_journal(story, chat):
    return _read_journal_unlocked(journal_path(story, chat))


def truncate(story, chat, turn):
    """Revert: drop all events with turn > N (single timeline — the dead
    branch is archived to one file for forensics, then gone).

    The read happens INSIDE the lock: reading first meant any event appended
    while the revert ran was erased by the rewrite and never archived —
    silent, unrecoverable loss of a turn the player just took (finding 2.9)."""
    path = journal_path(story, chat)
    with _lock:
        events = _read_journal_unlocked(path)
        keep = [e for e in events if e.get("turn", 0) <= turn]
        dropped = events[len(keep):]
        if dropped:
            arch = path.with_suffix(".reverted.jsonl")
            with open(arch, "a", encoding="utf-8") as f:
                for e in dropped:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for e in keep:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        tmp.replace(path)
    return len(dropped)


# ── Replay (pure fold) ───────────────────────────────────────────────────────

def initial_state():
    return {
        "room": None, "turn": 0, "turns_in_room": 0,
        "inventory": [], "flags": {}, "emotions": [], "extras": [],
        "solved": [], "found": [], "used": [], "rolled": [], "ended": False,
        "seals": {}, "seal_skipped": [], "seal_holds": {}, "revealed": [],
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
