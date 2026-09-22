"""Lifecycle only — no thread. Core calls start() once the scheduler is up:
we take the plugin_loader (fire_task, plugin state) and register the reply
handler for the plugin's own daemon source (discord_birthday)."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

_PLUGIN_ROOT = str(Path(__file__).absolute().parent)
if _PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, _PLUGIN_ROOT)

import discord_personality as dp  # noqa: E402

logger = logging.getLogger(__name__)


def _reply_handler(task, event_data: dict, response_text: str):
    kind = str((event_data or {}).get('proactive_kind') or '')
    if kind == 'birthday':
        from discord_personality.modules import birthdays
        return birthdays.deliver(event_data or {}, response_text or '')
    logger.warning('[PERSONALITY] reply for unknown kind %r dropped', kind)
    return None


def start(plugin_loader, settings):
    dp.set_loader(plugin_loader)
    plugin_loader.register_reply_handler(dp.PLUGIN, _reply_handler)
    logger.info('[PERSONALITY] ready — modules on: %s', ', '.join(dp.active_modules()) or 'none')


def stop():
    ld = dp.loader()
    unreg = getattr(ld, 'unregister_reply_handler', None) if ld else None
    if callable(unreg):
        try:
            unreg(dp.PLUGIN)
        except Exception:
            logger.debug('[PERSONALITY] reply handler unregister failed', exc_info=True)
    dp.set_loader(None)
