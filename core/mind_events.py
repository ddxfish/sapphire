"""Shared helper for publishing MIND_CHANGED events from plugin tool modules.

The Mind UI view subscribes to these and re-renders the active tab when a
matching event fires. Without this, a tool call like `save_goal` completes,
Sapphire says 'done', and the user's Mind tab stays stale — can't tell the
difference between 'tool silently failed' and 'tool worked, view stale.'
Scout 3 finding (2026-04-19).

Usage:
    from core.mind_events import publish_mind_changed
    publish_mind_changed('goal', scope, 'save')   # or 'update' / 'delete'
"""
import logging

from core.request_context import session_origin

logger = logging.getLogger(__name__)

_VALID_DOMAINS = {'memory', 'goal', 'knowledge', 'people'}
_VALID_ACTIONS = {'save', 'update', 'delete'}


def publish_mind_changed(domain: str, scope: str, action: str) -> None:
    """Fire MIND_CHANGED event. Swallows exceptions so tool saves never
    fail because of a publish-path issue."""
    if domain not in _VALID_DOMAINS:
        logger.debug(f"publish_mind_changed: unknown domain {domain!r}")
        return
    if action not in _VALID_ACTIONS:
        logger.debug(f"publish_mind_changed: unknown action {action!r}")
        return
    try:
        from core.event_bus import publish, Events
        publish(Events.MIND_CHANGED, {
            "domain": domain,
            "scope": scope,
            "action": action,
            # The tab that made the change skips its own echo (None from
            # tools / cron / librarian — those must reach every tab).
            "origin": session_origin.get(),
        })
    except Exception as e:
        logger.debug(f"MIND_CHANGED publish failed ({domain}/{scope}/{action}): {e}")
