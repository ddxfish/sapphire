"""Presence selection driven by sleep, quiet hours, affect, and cycling presets."""

from __future__ import annotations

import random
import time

from plugins.discord.presence.presence_catalog import activity_pool, load_sleep_statuses


class DiscordPresenceService:
    def __init__(self):
        self._last_update = {}
        self._last_mode = {}

    def should_update(
        self, account_name: str, *, mode: str, interval_seconds: float, force: bool = False
    ) -> bool:
        if force:
            return True
        if mode in {'quiet', 'sleep'}:
            return self._last_mode.get(account_name) != mode
        prev_mode = self._last_mode.get(account_name)
        if prev_mode != mode:
            return True
        interval = max(60.0, float(interval_seconds or 300))
        return time.time() - self._last_update.get(account_name, 0.0) >= interval

    def mark_updated(self, account_name: str, mode: str) -> None:
        self._last_update[account_name] = time.time()
        self._last_mode[account_name] = mode

    def resolve_mode(
        self, settings, *, asleep: bool, forced_wake: bool, local_hour: int
    ) -> str:
        if asleep and not forced_wake:
            return 'sleep'
        if settings.safety.quiet_hours_enabled and self._in_quiet_hours(settings.safety, local_hour):
            return 'quiet'
        return 'awake'

    def select_presence(
        self, settings, affect, *, asleep: bool, forced_wake: bool, local_hour: int
    ) -> dict:
        presence = settings.presence
        mode = self.resolve_mode(
            settings,
            asleep=asleep,
            forced_wake=forced_wake,
            local_hour=local_hour,
        )
        if mode == 'sleep':
            sleep_pool = list(load_sleep_statuses())
            fallback = presence.sleep_activity or 'custom: sleeping'
            activity = random.choice(sleep_pool) if sleep_pool else fallback
            return {
                'mode': mode,
                'status': presence.quiet_status or 'idle',
                'activity': activity,
            }
        if mode == 'quiet':
            return {
                'mode': mode,
                'status': presence.quiet_status or 'idle',
                'activity': presence.activity or '',
            }
        if not presence.cycling_enabled:
            status = presence.status or 'online'
            activity = presence.activity or ''
            if getattr(affect, 'energy', 0.7) < 0.3:
                status = presence.quiet_status or 'idle'
            if getattr(affect, 'sociability', 0.6) < 0.3 and not activity:
                activity = 'Taking a break'
            return {'mode': mode, 'status': status, 'activity': activity}
        pool = activity_pool(presence)
        activity_text = random.choice(pool) if pool else (presence.activity or '')
        status = presence.status or 'online'
        if getattr(affect, 'energy', 0.7) < 0.3:
            status = presence.quiet_status or 'idle'
        return {
            'mode': mode,
            'status': status,
            'activity': activity_text,
        }

    def _in_quiet_hours(self, safety, local_hour: int) -> bool:
        start = int(safety.quiet_hours_start) % 24
        end = int(safety.quiet_hours_end) % 24
        if start == end:
            return False
        if start < end:
            return start <= local_hour < end
        return local_hour >= start or local_hour < end
