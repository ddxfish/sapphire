"""discord-personality — Zeebie's Discord features, rebuilt simple on the host's hooks.

MODULE RULE (2026-09-21): one file per module under modules/, one settings
group per module with `<module>.enabled` first. A module that is OFF is not
there: dispatch() only reaches modules whose toggle is on, read live from the
plugin's settings on every event (no restart). Birthdays needs People.

Every handler receives core's HookEvent: `event.metadata` carries the host's
payload plus `metadata['api']` (the DiscordAPI facade — send_message, react,
send_image, set_presence, recent_messages, channel_info, join/leave_voice).
Modules never import the Discord plugin.
"""
from __future__ import annotations

import importlib
import logging

logger = logging.getLogger(__name__)

PLUGIN = 'discord-personality'
MODULES = ('people', 'birthdays', 'reminders', 'typos', 'presence')
NEEDS = {'birthdays': 'people'}


def settings() -> dict:
    """The plugin's effective settings (manifest defaults + stored), live."""
    try:
        from core.plugin_loader import plugin_loader
        return plugin_loader.get_plugin_settings(PLUGIN)
    except Exception:
        logger.debug('[PERSONALITY] settings unavailable', exc_info=True)
        return {}


def enabled(module: str, cfg: dict | None = None) -> bool:
    cfg = settings() if cfg is None else cfg
    if not bool(cfg.get(f'{module}.enabled', False)):
        return False
    need = NEEDS.get(module)
    return enabled(need, cfg) if need else True


def active_modules(cfg: dict | None = None) -> list[str]:
    cfg = settings() if cfg is None else cfg
    return [m for m in MODULES if enabled(m, cfg)]


def _module(name: str):
    return importlib.import_module(f'discord_personality.modules.{name}')


def dispatch(hook: str, event) -> list[str]:
    """Run `hook` on every active module that defines it. Returns who handled it.
    One module's exception never reaches another module or the host."""
    handled = []
    for name in active_modules():
        try:
            fn = getattr(_module(name), hook, None)
        except Exception:
            logger.exception('[PERSONALITY] module %s failed to import', name)
            continue
        if fn is None:
            continue
        try:
            fn(event)
            handled.append(name)
        except Exception:
            logger.exception('[PERSONALITY] %s.%s failed', name, hook)
    return handled
