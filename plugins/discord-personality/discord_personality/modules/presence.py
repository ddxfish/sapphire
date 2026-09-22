"""Presence cycling — a status line that rotates, and a night status (S1, 2026-09-22).

Settings: presence.statuses (one per line: 'listening: chat', 'watching: the
server', 'playing: …', or plain custom text), presence.cycle_minutes,
presence.status (online/idle/dnd), presence.night_start / night_end (HH:MM,
blank = no night), presence.night_status, presence.night_activity. Runs on
discord_tick per connected account through api.set_presence.
"""
from __future__ import annotations

import logging
import time

import discord_personality as dp
from discord_personality.clock import in_window, now_user

logger = logging.getLogger(__name__)

DEFAULT_STATUSES = ['listening: chat', 'watching: the server', 'playing: with ideas',
                    'daydreaming', 'listening: lo-fi beats', 'just vibing']
_last: dict[str, dict] = {}     # account -> {'at': ts, 'activity': str, 'status': str, 'night': bool, 'index': int}


def discord_tick(event):
    md = event.metadata
    apply(str(md.get('account') or ''), api=md.get('api'))


def statuses(cfg: dict) -> list[str]:
    raw = cfg.get('presence.statuses')
    lines = raw if isinstance(raw, list) else str(raw or '').splitlines()
    picked = [str(x).strip() for x in lines if str(x).strip()]
    return picked or list(DEFAULT_STATUSES)


def choose(cfg: dict, now, prev: dict | None) -> dict | None:
    """The presence to set now, or None when nothing should change."""
    night = in_window(now, cfg.get('presence.night_start'), cfg.get('presence.night_end'))
    if night:
        want = {'status': str(cfg.get('presence.night_status') or 'idle'),
                'activity': str(cfg.get('presence.night_activity') or 'custom: sleeping'), 'night': True, 'index': -1}
        if prev and prev.get('night') and prev.get('activity') == want['activity'] and prev.get('status') == want['status']:
            return None
        return want
    pool = statuses(cfg)
    try:
        cycle = max(1.0, float(cfg.get('presence.cycle_minutes') or 30)) * 60
    except (TypeError, ValueError):
        cycle = 1800.0
    if prev and not prev.get('night') and time.time() - float(prev.get('at') or 0) < cycle:
        return None
    index = 0 if not prev or prev.get('night') else (int(prev.get('index', -1)) + 1) % len(pool)
    return {'status': str(cfg.get('presence.status') or 'online'), 'activity': pool[index], 'night': False, 'index': index}


def apply(account: str, *, api, cfg: dict | None = None, now=None) -> dict | None:
    if not account or api is None:
        return None
    cfg = dp.settings() if cfg is None else cfg
    want = choose(cfg, now or now_user(), _last.get(account))
    if want is None:
        return None
    result = api.set_presence(account, status=want['status'], activity=want['activity'])
    _last[account] = {**want, 'at': time.time()}
    logger.debug('[PERSONALITY] presence for %s → %s / %s (%s)', account, want['status'], want['activity'],
                 (result or {}).get('status'))
    return want
