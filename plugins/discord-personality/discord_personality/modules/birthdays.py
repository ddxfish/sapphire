"""Birthdays — a wish on the day, written by the LLM (S1, 2026-09-22).

Needs People: the birthday lives on the person's row (People browser on the
Personality page; the discord_people tool sets it from chat in S2). Once a
day at `birthdays.time` the module fires the plugin's own `discord_birthday`
daemon task (Source Settings: bot account + channel id) through core's
fire_task — one event per person due; the task's Instructions set the tone —
and the reply lands in the channel with an @mention. No task = nothing posts
(logged once per day). No canned text.
"""
from __future__ import annotations

import logging
import time

import discord_personality as dp
from discord_personality.clock import MemoryState, due, now_user
from discord_personality.storage import people as _people

logger = logging.getLogger(__name__)

SOURCE = 'discord_birthday'
STATE_KEY = 'birthdays_fired'


def discord_tick(event):
    run(str(event.metadata.get('account') or ''))


def run(account: str, now=None, *, people=None, loader=None, state=None, cfg=None) -> list[dict]:
    """One pass per account per day inside the window after birthdays.time."""
    if not account:
        return []
    cfg = dp.settings() if cfg is None else cfg
    now = now or now_user()
    if not due(cfg.get('birthdays.time') or '09:00', now):
        return []
    state = state if state is not None else (dp.state() or MemoryState())
    today = now.strftime('%Y-%m-%d')
    fired_map = dict(state.get(STATE_KEY) or {})
    if fired_map.get(account) == today:
        return []
    loader = loader if loader is not None else dp.loader()
    people = people if people is not None else _people()
    rows = people.with_birthday(account, now.strftime('%m-%d'))
    fired: list[dict] = []
    if rows:
        tasks = [t for t in (loader.tasks_for_source(SOURCE) if loader else [])
                 if str((t.get('trigger_config') or {}).get('account') or '') == account]
        if not tasks:
            logger.warning('[PERSONALITY] %d birthday(s) today on %s but no enabled "Discord: Birthdays" task — nothing posted',
                           len(rows), account)
        for task in tasks:
            channel_id = str((task.get('trigger_config') or {}).get('channel') or '').strip()
            if not channel_id:
                logger.warning('[PERSONALITY] birthday task %r has no channel id', task.get('name'))
                continue
            for row in rows:
                name = row.get('display_name') or f"user {row['user_id']}"
                payload = {
                    'account': account, 'channel_id': channel_id, 'user_id': row['user_id'],
                    'display_name': name, 'proactive_kind': 'birthday',
                    'message_id': f"birthday-{row['user_id']}-{int(time.time())}",
                    'content': (f"Today is {name}'s birthday. Post one warm happy-birthday message for them "
                                f"in this Discord channel — one to three sentences, your own words."),
                }
                result = loader.fire_task(task['id'], payload, plugin=dp.PLUGIN) or {}
                if result.get('success'):
                    fired.append({'task_id': task['id'], 'user_id': row['user_id'], 'channel_id': channel_id})
                    logger.info('[PERSONALITY] birthday wish for %s fired into task %r', name, task.get('name'))
                else:
                    logger.warning('[PERSONALITY] birthday task %r refused: %s', task.get('name'), result.get('error'))
    fired_map = {k: v for k, v in fired_map.items() if v == today}
    fired_map[account] = today
    state.save(STATE_KEY, fired_map)
    return fired


def deliver(event_data: dict, response_text: str, *, api=None) -> dict:
    """The task's answer → the channel, @mentioning the person if she didn't."""
    text = str(response_text or '').strip()
    channel_id = str(event_data.get('channel_id') or '')
    user_id = str(event_data.get('user_id') or '')
    if not text or not channel_id:
        return {'status': 'skipped', 'reason': 'empty'}
    if user_id and f'<@{user_id}>' not in text and f'<@!{user_id}>' not in text:
        text = f'<@{user_id}> {text}'
    api = api if api is not None else dp.api()
    result = api.send_message(channel_id, text, account=event_data.get('account') or None)
    logger.info('[PERSONALITY] birthday wish posted to %s: %s', channel_id, result.get('status'))
    return result
