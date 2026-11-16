"""Shared helper for sweeping orphaned scope references out of chat settings.

When a user deletes a scope (memory/goal/knowledge/people), any chat whose
settings reference that scope by name would otherwise silently keep pointing
at a dead scope — apply_scopes_from_settings on next activation sets the
ContextVar to the ghost string, and the AI writes into a room nobody sees
in the UI. Scout 2's HIGH-severity finding from the 2026-04-18 scouting
party, paired with the Mind-view hardcoded-scope bug we fixed the same day.

The holders go to 'none', not 'default' (2026-11-16): 'default' is a real
scope, the stock personas' own memory, and a chat whose scope was deleted was
moved INTO it (delete a second persona's scope and that persona read and
wrote the main one's memories from the next turn). A dead scope means no scope. Personas, tasks,
the new-chat defaults and room seats that held the name are released the same
way (core/referrers.py scope_deleted): one that kept it brought the scope
back as a ghost on its next write.

Usage from a scope-deleting tool:

    from core.chat.scope_cleanup import sweep_orphaned_scope_ref
    affected = sweep_orphaned_scope_ref('memory_scope', deleted_name)
"""
import logging

logger = logging.getLogger(__name__)


def sweep_orphaned_scope_ref(setting_key: str, deleted_scope: str,
                             reset_to: str = 'none') -> list:
    """Rewrite any chat whose settings[setting_key] == deleted_scope back
    to reset_to, and release the other holders of the name. Returns affected
    chat names (empty list if system singleton not yet wired, e.g. during
    plugin scan at boot).
    """
    try:
        from core import referrers
        referrers.scope_deleted(setting_key, deleted_scope, reset_to)
    except Exception as e:
        logger.warning(f"scope sweep (personas, tasks, seats) failed ({setting_key}={deleted_scope!r}): {e}")
    try:
        from core.api_fastapi import get_system
        system = get_system()
        sm = system.llm_chat.session_manager
    except Exception as e:
        # System singleton not available (pre-boot scan, test harness without
        # the full stack, etc). Skip silently — the on-disk chat settings will
        # still get reset on next explicit settings update, and a boot-time
        # cleanup sweep isn't in scope for this helper.
        logger.debug(f"scope sweep skipped ({setting_key}={deleted_scope!r}): {e}")
        return []
    return sm.reset_chat_scope_ref(setting_key, deleted_scope, reset_to=reset_to)
