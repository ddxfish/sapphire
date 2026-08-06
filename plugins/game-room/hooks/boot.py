# hooks/boot.py — plugins_ready: re-register the prompt pack WITH the
# runtime-rendered story monoliths merged (user/story_saves/
# _dynamic_monoliths.json). The manifest registration during the scan
# carries only the shipped pack files, so after a reboot mid-story the
# role prompt ('rose') didn't exist as a name — and core's missing-prompt
# fallback then REWROTE the chat to 'default' (Sapph-not-Rose bug,
# 2026-08-05). This hook makes state.py's "restart-proof" promise true.
import logging
import sys
from pathlib import Path

# .absolute(), never .resolve(): symlinked plugin dirs must not escape.
_PLUGIN_ROOT = str(Path(__file__).absolute().parent.parent)
if _PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, _PLUGIN_ROOT)

logger = logging.getLogger(__name__)


def plugins_ready(event):
    try:
        from gameroom_story import session
        session._restore_pack()
    except Exception as e:
        logger.warning(f"[STORY] boot pack re-merge failed: {e} — active story "
                       f"prompts won't resolve until a story action runs")
    return event
