"""Presence — a status line while she's awake, an away line while she isn't (2026-09-25).

Awake = the account has an enabled **Discord: Chat** task whose Active hours
(Continuity → the task → Active hours) include this hour. A task with no
window is always awake; so is a bot with no Chat task at all. Outside the
window core refuses the task's events (scheduler.fire_event_task), so "away"
is simply what Discord shows for a bot that will not answer — she stays
connected. Awake: online + a random line from `presence.statuses`, re-rolled
every `presence.cycle_minutes`. Away: idle + `presence.away_line`.

Off by default; off = the host never touches presence (a status set while it
was on is cleared once). Runs on the runtime tick per connected account.
"""
from __future__ import annotations

import logging
import random
import time

from core.continuity.scheduler import task_in_active_hours
from plugins.discord.greetings import now_user

logger = logging.getLogger(__name__)

SOURCE = 'discord_message'
AWAKE_STATUS, AWAY_STATUS = 'online', 'idle'
DEFAULT_STATUSES = ['listening: chat', 'watching: the server', 'playing: with ideas',
                    'daydreaming', 'listening: lo-fi beats', 'just vibing']


def status_lines(raw) -> list[str]:
    lines = raw if isinstance(raw, list) else str(raw or '').splitlines()
    return [str(x).strip() for x in lines if str(x).strip()]


class PresenceClock:
    def __init__(self, *, plugin_loader, transport, settings_store):
        self.plugin_loader = plugin_loader
        self.transport = transport
        self.settings_store = settings_store
        self._last: dict[str, dict] = {}     # account -> {'at', 'index', 'away', 'activity', 'status'}

    def awake(self, account: str, now=None) -> bool:
        tasks_for = getattr(self.plugin_loader, 'tasks_for_source', None)
        if not callable(tasks_for):
            return True
        mine = [t for t in (tasks_for(SOURCE) or [])
                if str((t.get('trigger_config') or {}).get('account') or '') == account]
        if not mine:
            return True
        hour = (now or now_user()).hour
        return any(task_in_active_hours(t, hour) for t in mine)

    def choose(self, account: str, cfg, now=None) -> dict | None:
        """The presence to set now, or None when nothing should change."""
        prev = self._last.get(account)
        if not cfg.enabled:
            return {'status': AWAKE_STATUS, 'activity': '', 'clear': True} if prev else None
        if not self.awake(account, now):
            want = {'status': AWAY_STATUS, 'activity': str(cfg.away_line or '').strip(), 'away': True, 'index': -1}
            if prev and prev.get('away') and prev.get('activity') == want['activity']:
                return None
            return want
        pool = status_lines(cfg.statuses)
        try:
            cycle = max(1.0, float(cfg.cycle_minutes)) * 60
        except (TypeError, ValueError):
            cycle = 1800.0
        if prev and not prev.get('away') and time.time() - float(prev.get('at') or 0) < cycle:
            return None
        if not pool:
            if prev and not prev.get('away') and not prev.get('activity'):
                return None
            return {'status': AWAKE_STATUS, 'activity': '', 'away': False, 'index': -1}
        index = random.randrange(len(pool))
        if prev and not prev.get('away') and len(pool) > 1 and index == prev.get('index'):
            index = (index + 1) % len(pool)
        return {'status': AWAKE_STATUS, 'activity': pool[index], 'away': False, 'index': index}

    async def tick_async(self, account: str, now=None) -> dict | None:
        cfg = self.settings_store.resolve().presence
        want = self.choose(account, cfg, now)
        if want is None:
            return None
        try:
            await self.transport.change_presence_async(account, status=want['status'], activity=want['activity'])
        except Exception as exc:
            logger.warning('[DISCORD] presence for %s not applied: %s', account, exc)
            return None
        if want.get('clear'):
            self._last.pop(account, None)
        else:
            self._last[account] = {**want, 'at': time.time()}
        logger.debug('[DISCORD] presence for %s → %s / %r', account, want['status'], want['activity'])
        return want

    def forget(self, account: str) -> None:
        """A (re)connect starts plain online on Discord's side — re-apply on the next tick."""
        self._last.pop(account, None)
