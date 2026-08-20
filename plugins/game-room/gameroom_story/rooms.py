# engine/rooms.py — story pack discovery + room loading.
#
# A story pack is a folder: story.json (slug, title, premise, start) +
# rooms/{id}-{slug}.json, one file per room — the file IS the room.
# Scanned roots: the plugin's own stories/, user/story_presets/, and any
# other plugin's stories/ dir (expansions). Generated rooms live with the
# PLAYTHROUGH (user/story_saves/{slug}/rooms/), never the canonical pack.
import hashlib
import json
import logging
import re
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_PLUGIN_DIR = Path(__file__).parent.parent
# Walk up from the plugin dir — .absolute(), never .resolve() (symlinked
# plugins must anchor to the entry path, not the target). Marker-walk, not
# counted parents: the plugin lives at plugins/ (2 deep) or user/plugins/
# (3 deep) and counted .parent math silently lands one level off when the
# band changes (found during the system-band move, 2026-08-05).


def _find_root(start):
    d = start.absolute()
    for _ in range(8):
        if (d / "sapphire.py").is_file():
            return d
        d = d.parent
    return start.absolute().parent.parent.parent   # legacy user-band guess


SAPPHIRE_ROOT = _find_root(_PLUGIN_DIR)
SAVES_ROOT = SAPPHIRE_ROOT / "user" / "story_saves"

_ROOM_FILE_RE = re.compile(r"^(\d+)-(.+)\.json$")


def _story_roots():
    """Where story packs live: our own stories/, the user's hand-authored
    story_presets/, and any PLUGIN that ships a stories/ dir.

    Plugin-band packs go through the plugin gate (finding 4.2): a raw dir
    walk meant a disabled or signature-BLOCKED pack still played — and a
    story writes her system prompt, so that is exactly the code path the
    gate exists to guard. user/story_presets/ is the user's own unsigned
    workspace and stays open by design."""
    roots = [_PLUGIN_DIR / "stories", SAPPHIRE_ROOT / "user" / "story_presets"]
    try:
        from core.plugin_loader import plugin_loader
    except Exception:
        plugin_loader = None
    for band in (SAPPHIRE_ROOT / "plugins", SAPPHIRE_ROOT / "user" / "plugins"):
        if not band.is_dir():
            continue
        for p in band.iterdir():
            cand = p / "stories"
            if not cand.is_dir() or cand == roots[0]:
                continue
            if plugin_loader is not None and (p / "plugin.json").exists():
                info = plugin_loader.get_plugin_info(p.name)
                if info and not (info.get("enabled") and info.get("verified")):
                    logger.info(f"[STORY] skipping pack in {p.name} — plugin not "
                                f"enabled+verified ({info.get('verify_msg', 'disabled')})")
                    continue
            roots.append(cand)
    return roots


def list_stories():
    """{slug: {title, description, path}} across all roots (first wins)."""
    out = {}
    for root in _story_roots():
        if not root.is_dir():
            continue
        for d in sorted(root.iterdir()):
            meta_f = d / "story.json"
            if not d.is_dir() or not meta_f.exists():
                continue
            try:
                meta = json.loads(meta_f.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning(f"[STORY] bad story.json in {d}: {e}")
                continue
            slug = meta.get("slug") or d.name
            if slug in out or slug.startswith("_"):
                continue  # _template and friends stay out of the picker
            ov = _overrides(slug)  # tiles show what the story will actually say
            out[slug] = {
                "title": meta.get("title", slug),
                "description": meta.get("description", ""),
                "role": (meta.get("role") or {}).get("name"),
                "player_role": ov.get("player_role") or meta.get("player_role"),
                "tags": meta.get("tags") or [],
                "facts": meta.get("facts") or [],
                "tile_file": meta.get("tile") or "",
                "premise": ov.get("premise") or meta.get("premise") or "",
                "stats": _story_stats(d),
                "path": str(d),
            }
    return out


def _story_stats(story_dir):
    """Tile stats: room/illustration/ending counts + art weight. Local disk,
    tiny files — computed per listing, no cache needed."""
    p = Path(story_dir)
    stats = {"rooms": 0, "endings": 0, "images": 0, "art_kb": 0}
    try:
        for f in sorted((p / "rooms").glob("*.json")):
            try:
                r = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            stats["rooms"] += 1
            if not r.get("exits"):
                # ending VARIANTS, not just ending rooms — each conditional
                # ending card is its own dawn (titanic: 2 rooms, 4 endings)
                stats["endings"] += max(1, len(r.get("ending_cards") or []))
        bd = p / "backdrops"
        if bd.is_dir():
            for f in bd.iterdir():
                if f.is_file():
                    stats["images"] += 1
                    stats["art_kb"] += f.stat().st_size // 1024
    except Exception:
        pass
    return stats


# ── user story-text overrides (storycfg:{slug}, the DM-guide pattern) ──────
# Shipped story.json is SIGNED — user edits live in the plugin store and
# merge at load time. Non-empty override wins; empty means shipped text
# shows through (reset = save the shipped text verbatim, same as dm_guide).
_OVERRIDE_FIELDS = ("role_text", "premise", "player_role")


def _overrides(slug):
    try:
        from core.plugin_loader import plugin_loader
        cfg = plugin_loader.get_plugin_state("game-room").get(f"storycfg:{slug}") or {}
    except Exception as e:
        logger.warning(f"[STORY] override read failed for '{slug}': {e}")
        return {}
    out = {}
    for k in _OVERRIDE_FIELDS:
        v = str(cfg.get(k) or "").strip()
        if v:
            out[k] = v
    return out


def _apply_overrides(slug, meta):
    ov = _overrides(slug)
    if not ov:
        return
    # role_text only when the pack ships a role — half a role (text without
    # a name) would flip the identity-mode fallbacks in render.story_prompt.
    if ov.get("role_text") and isinstance(meta.get("role"), dict) \
            and (meta["role"].get("name") or "").strip():
        meta["role"] = dict(meta["role"], text=ov["role_text"])
    if ov.get("premise"):
        meta["premise"] = ov["premise"]
    if ov.get("player_role"):
        meta["player_role"] = ov["player_role"]


def story_slots(meta):
    """Normalized Mad-Libs slot declarations from story.json `slots` (plan
    tmp/open-mansion-plan.md). Soft: malformed entries warn + drop, never
    break the story. Each: {key, label, options[], allow_custom, default,
    sealed, seal_key}. `default` falls back to the first option so an
    unanswered slot never leaks a raw {token} into her prompt."""
    out = []
    for s in (meta.get("slots") or []):
        if not isinstance(s, dict):
            continue
        key = str(s.get("key") or "").strip()
        if not key or not key.replace("_", "").isalnum():
            logger.warning(f"[STORY] slot with bad key {s.get('key')!r} dropped")
            continue
        options = [str(o) for o in (s.get("options") or []) if str(o).strip()]
        default = str(s.get("default") or "").strip() or (options[0] if options else "")
        out.append({
            "key": key,
            "label": str(s.get("label") or key.replace("_", " ")),
            "options": options,
            "allow_custom": bool(s.get("allow_custom", True)),
            "default": default,
            "sealed": bool(s.get("sealed")),
            "seal_key": str(s.get("seal_key") or "").strip().lower(),
        })
    return out


def load_story(slug, raw=False):
    """Full story: meta + all canonical rooms. Raises on missing/invalid.
    raw=True skips user overrides — the settings routes need the SHIPPED
    text for defaults and the verbatim-equals-shipped comparison."""
    entry = list_stories().get(slug)
    if not entry:
        raise KeyError(f"Unknown story '{slug}'")
    path = Path(entry["path"])
    meta = json.loads((path / "story.json").read_text(encoding="utf-8"))
    meta.setdefault("slug", slug)
    if not raw:
        _apply_overrides(slug, meta)
    rooms = {}
    rooms_dir = path / "rooms"
    if rooms_dir.is_dir():
        for f in rooms_dir.iterdir():
            room = _load_room_file(f)
            if room:
                rooms[room["id"]] = room
    if not rooms:
        raise ValueError(f"Story '{slug}' has no rooms")
    start = meta.get("start")
    if start not in rooms:
        raise ValueError(f"Story '{slug}' start room {start!r} not found")
    return {"meta": meta, "rooms": rooms, "path": str(path)}


def load_generated_rooms(slug, chat):
    """Playthrough-generated rooms for one save (may be empty)."""
    out = {}
    # save_dir(), not a hand-built path: this used the RAW slug while
    # journal_path sanitized it, so the two disagreed for any slug with a
    # space or dot in it (Tier 5, F1).
    gen_dir = save_dir(slug, chat) / "rooms"
    if gen_dir.is_dir():
        for f in gen_dir.iterdir():
            room = _load_room_file(f)
            if room:
                out[room["id"]] = room
    return out


def _load_room_file(f):
    m = _ROOM_FILE_RE.match(f.name)
    if not m:
        return None
    try:
        room = json.loads(f.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"[STORY] bad room file {f}: {e}")
        return None
    ok, msg = validate_room(room)
    if not ok:
        logger.warning(f"[STORY] invalid room {f}: {msg}")
        return None
    if room.get("id") != int(m.group(1)):
        logger.warning(f"[STORY] room file {f.name} id mismatch (file says {m.group(1)}, json says {room.get('id')})")
    return room


def validate_room(room):
    """Schema floor for a room dict. Returns (ok, msg)."""
    if not isinstance(room, dict):
        return False, "not an object"
    if not isinstance(room.get("id"), int):
        return False, "id must be an integer"
    if not isinstance(room.get("title"), str) or not room["title"].strip():
        return False, "title required"
    if not isinstance(room.get("template"), str) or not room["template"].strip():
        return False, "template required"
    exits = room.get("exits", [])
    if not isinstance(exits, list):
        return False, "exits must be a list"
    for ex in exits:
        if not isinstance(ex, dict) or not isinstance(ex.get("label"), str):
            return False, "exit needs a label"
        if ex.get("to") is not None and not isinstance(ex["to"], int):
            return False, f"exit '{ex.get('label')}' to must be int or null"
        if ex.get("to") is None and not ex.get("generate"):
            return False, f"exit '{ex.get('label')}' has no destination and no generate flag"
    # Total, never raising: a list-valued "objects" used to AttributeError
    # straight through load_story into a 500 (post-fix review 2026-08-05).
    objs = room.get("objects")
    if objs is not None and not isinstance(objs, dict):
        return False, "objects must be an object (name → spec map)"
    for oname, obj in (objs or {}).items():
        if not isinstance(obj, dict):
            return False, f"object '{oname}' must be an object"
    return True, "ok"


_WINDOWS_RESERVED = ({"CON", "PRN", "AUX", "NUL"}
                     | {f"COM{i}" for i in range(1, 10)}
                     | {f"LPT{i}" for i in range(1, 10)})


def _legacy_safe(name):
    """The pre-2026-08-05 mapping. Kept ONLY to adopt existing save dirs."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(name))


def _safe(name):
    """Chat/story name → a filesystem-safe, COLLISION-FREE directory name.

    The old one-line regex was four bugs (finding 2.7): it let `..` through
    untouched (the allowlist includes dots) so a slug could escape
    SAVES_ROOT; it produced Windows reserved device names that brick a story
    AFTER the cockpit is already stamped; and it was many-to-one — 'my chat'
    and 'my_chat' shared a journal, and any two non-Latin names collapsed to
    '___' and merged into ONE playthrough.

    Fix: sanitize for the filesystem, then bind the directory to the exact
    original name with a short digest. Different names can never share a
    directory again, whatever alphabet they're written in."""
    raw = str(name)
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", raw).strip("_. ")[:60]
    if not cleaned or cleaned.split(".")[0].upper() in _WINDOWS_RESERVED:
        cleaned = "chat"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    return f"{cleaned}-{digest}"


_adopt_lock = threading.Lock()


def save_dir(story, chat):
    """The ONE (story, chat) → playthrough-directory mapping. Adopts a
    legacy-named directory in place the first time it's touched, so saves
    written under the old scheme keep playing.

    Adoption is serialized: room entry fires status/reassert/ghost within
    milliseconds, and two concurrent first-touches both renamed — the loser
    got back a path that no longer existed, and the next append silently
    forked the journal into a fresh dir (post-fix review 2026-08-05)."""
    base = SAVES_ROOT / _safe(story)
    d = base / _safe(chat)
    if d.exists():
        return d
    with _adopt_lock:
        if d.exists():                       # raced: someone adopted first
            return d
        for legacy_base in (base, SAVES_ROOT / _legacy_safe(story)):
            legacy = legacy_base / _legacy_safe(chat)
            if legacy != d and legacy.is_dir():
                try:
                    d.parent.mkdir(parents=True, exist_ok=True)
                    legacy.rename(d)
                    logger.info(f"[STORY] adopted legacy save dir {legacy} → {d}")
                    return d
                except OSError as e:
                    if d.exists():           # rename half-won somewhere
                        return d
                    if legacy.is_dir():
                        logger.warning(f"[STORY] could not adopt {legacy}: {e} — using it in place")
                        return legacy
                    logger.warning(f"[STORY] could not adopt {legacy}: {e} — using new-scheme dir")
    return d
