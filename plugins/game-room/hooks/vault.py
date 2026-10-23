# hooks/vault.py — vault_locked / vault_unlocked: re-register the prompt
# pack so its process-global copy tracks the vault's visibility (hunt
# 2026-08-17 F1: a sealed chat's rendered story costume — name AND full
# content — stayed served by /api/prompts until the next story action).
# _restore_pack reads dynamic monoliths through core's hidden-chat filter,
# so on lock the sealed costumes structurally drop out, and on unlock they
# come straight back instead of waiting for a story action.
import logging
import sys
from pathlib import Path

# .absolute(), never .resolve(): symlinked plugin dirs must not escape.
_PLUGIN_ROOT = str(Path(__file__).absolute().parent.parent)
if _PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, _PLUGIN_ROOT)

logger = logging.getLogger(__name__)


def _reregister(moment):
    try:
        from gameroom_story import session
        session._restore_pack()
        logger.info(f"[STORY] pack re-registered on {moment}")
    except Exception as e:
        logger.warning(f"[STORY] pack re-register on {moment} failed: {e}")


def vault_locked(event):
    _reregister("vault lock")
    return event


def vault_unlocked(event):
    _reregister("vault unlock")
    return event
