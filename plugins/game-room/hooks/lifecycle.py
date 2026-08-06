# hooks/lifecycle.py — chat_renamed / chat_deleted.
#
# The chat IS the save (0.5 ruling), so a chat's name is a primary key here:
# story journals live at user/story_saves/<slug>/<chat>/, active.json keys by
# chat, and game states key by 'game:<id>:<chat>'. Core had no way to tell a
# plugin the key moved — a rename stranded the whole playthrough, and a
# delete left a ghost that the next chat recycling that name inherited
# mid-story (findings 2.1 + 2.2, 2026-08-05).
#
# Post-fix review 2026-08-05 rework: key discovery walks the DISK, never the
# installed-pack catalogue — a disabled, signature-blocked or uninstalled
# pack's saves must still carry/archive, and the catalogue is exactly the
# thing that vanishes with the pack. Each carry step is isolated (one
# failure must not abort the rest), a rename target that already exists is
# archived (skipping handed the renamed chat a STRANGER's journal), and
# game-state moves hold the session lock so a mid-turn rename can't rewind
# the board. Game saves are ARCHIVED on delete, same law as journals
# (Krem's ruling 2026-08-05).
import logging
import shutil
import sys
from pathlib import Path

# .absolute(), never .resolve(): symlinked plugin dirs must not escape.
_PLUGIN_ROOT = str(Path(__file__).absolute().parent.parent)
if _PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, _PLUGIN_ROOT)

logger = logging.getLogger(__name__)


def _game_keys_for(store, chat):
    """Game-state keys belonging to one chat: 'game:<id>:<chat>'."""
    try:
        keys = list(store.all().keys())
    except Exception:
        return []
    return [k for k in keys
            if k.startswith("game:") and k.split(":", 2)[2:] == [chat]]


def _chat_dirs(chat):
    """Every on-disk playthrough dir belonging to `chat`, found by walking
    SAVES_ROOT — both naming schemes (current digest form + legacy). Yields
    (story_dir, chat_dir, is_legacy_name)."""
    from gameroom_story import rooms
    root = rooms.SAVES_ROOT
    if not root.is_dir():
        return
    safe, legacy = rooms._safe(chat), rooms._legacy_safe(chat)
    for story_dir in sorted(root.iterdir()):
        if not story_dir.is_dir():
            continue
        d = story_dir / safe
        if d.is_dir():
            yield story_dir, d, False
        if legacy != safe:
            d = story_dir / legacy
            if d.is_dir():
                yield story_dir, d, True


def _archive_dir(src):
    """Move a directory aside as _deleted-<name>(.n) beside itself."""
    dst = src.with_name(f"_deleted-{src.name}")
    n = 1
    while dst.exists():
        n += 1
        dst = src.with_name(f"_deleted-{src.name}.{n}")
    shutil.move(str(src), str(dst))
    return dst


def chat_renamed(event):
    old = (event.metadata or {}).get("old")
    new = (event.metadata or {}).get("new")
    if not old or not new or old == new:
        return event

    # 1. Story journals — per-dir isolation; a legacy-named dir carries under
    # the legacy scheme so save_dir()'s adoption still finds it later.
    try:
        from gameroom_story import rooms
        for story_dir, src, is_legacy in _chat_dirs(old):
            try:
                dst = story_dir / (rooms._legacy_safe(new) if is_legacy
                                   else rooms._safe(new))
                if src == dst:
                    continue
                if dst.exists():
                    arch = _archive_dir(dst)
                    logger.warning(f"[GAME-ROOM] rename target {dst.name} already "
                                   f"existed in {story_dir.name} — archived to "
                                   f"{arch.name} (pre-hook residue)")
                shutil.move(str(src), str(dst))
            except Exception as e:
                logger.error(f"[GAME-ROOM] journal carry {src} → '{new}' failed: {e}",
                             exc_info=True)
    except Exception as e:
        logger.error(f"[GAME-ROOM] journal walk for rename '{old}'→'{new}' failed: {e}",
                     exc_info=True)

    # 2. active.json — the playthrough pointer (new committed before old cleared)
    try:
        from gameroom_story import state as st
        active = st.get_active()
        if old in active:
            entry = active[old]
            st.set_active(new, entry.get("story"), entry.get("prev_prompt"))
            st.update_active(new, **{k: v for k, v in entry.items()
                                     if k not in ("story", "prev_prompt")})
            st.clear_active(old)
    except Exception as e:
        logger.error(f"[GAME-ROOM] active pointer carry '{old}'→'{new}' failed: {e}",
                     exc_info=True)

    # 3. Game saves — under the session lock, so a rename during an AI turn
    # waits for the turn's save instead of rewinding the board.
    try:
        import gameroom_core as gc
        for key in _game_keys_for(gc.store, old):
            gid = key.split(":", 2)[1]
            try:
                with gc.session_lock(gid, old):
                    val = gc.store.get(key)
                    if val is not None:
                        gc.store.save(gc.state_key(gid, new), val)
                    gc.store.delete(key)
            except Exception as e:
                logger.error(f"[GAME-ROOM] game save carry {key} → '{new}' failed: {e}",
                             exc_info=True)
        logger.info(f"[GAME-ROOM] carried saves from chat '{old}' to '{new}'")
    except Exception as e:
        logger.error(f"[GAME-ROOM] game-save walk for rename '{old}'→'{new}' failed: {e}",
                     exc_info=True)
    return event


def chat_deleted(event):
    """Drop the chat's playthrough. Journals AND game states are ARCHIVED,
    never erased — deleting a chat shouldn't silently burn a finished story
    or a 40-wave run (Krem's ruling 2026-08-05) — but the active pointer
    goes first, so a recreated chat of the same name starts clean instead of
    resuming a stranger's tale."""
    chat = (event.metadata or {}).get("name")
    if not chat:
        return event

    # 1. The pointer first — it decides whether a ghost resumes.
    try:
        from gameroom_story import state as st
        st.clear_active(chat)
    except Exception as e:
        logger.error(f"[GAME-ROOM] clearing active pointer for '{chat}' failed: {e}",
                     exc_info=True)

    # 2. Journals → _deleted- archive, per-dir isolation.
    try:
        for story_dir, src, _legacy in _chat_dirs(chat):
            try:
                _archive_dir(src)
            except Exception as e:
                logger.error(f"[GAME-ROOM] archiving {src} failed: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"[GAME-ROOM] journal walk for deleted '{chat}' failed: {e}",
                     exc_info=True)

    # 3. Game states → archive keys ('_deleted:' prefix keeps them out of
    # every 'game:'-prefixed enumeration), under the session lock.
    try:
        import gameroom_core as gc
        for key in _game_keys_for(gc.store, chat):
            gid = key.split(":", 2)[1]
            try:
                with gc.session_lock(gid, chat):
                    val = gc.store.get(key)
                    if val is not None:
                        akey = f"_deleted:{key}"
                        n = 1
                        while gc.store.get(akey) is not None:
                            n += 1
                            akey = f"_deleted:{key}.{n}"
                        gc.store.save(akey, val)
                    gc.store.delete(key)
            except Exception as e:
                logger.error(f"[GAME-ROOM] archiving game state {key} failed: {e}",
                             exc_info=True)
        logger.info(f"[GAME-ROOM] archived playthrough state for deleted chat '{chat}'")
    except Exception as e:
        logger.error(f"[GAME-ROOM] game-save walk for deleted '{chat}' failed: {e}",
                     exc_info=True)
    return event
