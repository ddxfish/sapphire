"""Greetings clock — the host's own daily clock for the Discord: Greetings and
Discord: All interactions daemon sources (S1, 2026-09-22).

Each enabled task on those sources carries `greeting_time` / `goodnight_time`
(HH:MM, user-local) and `channels` (comma-separated channel ids) in its
Source Settings. Every runtime tick the clock compares the local time with
each task's times and, inside a five-minute window after the time, fires ONE
event per channel per kind per day into THAT task through core's fire_task.
The task's provider / persona / Instructions write the post; the host's reply
handler posts it (proactive_kind path: no quote, no read delay).

A late boot never greets: a time that passed more than the window ago is
skipped until tomorrow (the 1pm "good morning!" class). The per-day latch is
durable (plugin state) so a restart inside the window cannot double post.
LLM failure → the task logs it, nothing is posted. No canned text anywhere.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime

logger = logging.getLogger(__name__)

SOURCES = ('discord_greetings', 'discord_all')
KINDS = (('greeting', 'greeting_time'), ('goodnight', 'goodnight_time'))
WINDOW_SECONDS = 300
STATE_KEY = 'greetings_fired'
INSTRUCTIONS = {
    'greeting': (
        'Post a short, warm good-morning message for this Discord channel. '
        'One or two sentences. Vary your wording — do not repeat the same greeting each day.'
    ),
    'goodnight': (
        'Post a short goodnight message for this Discord channel before you head out for the night. '
        'One or two sentences. Vary your wording from night to night.'
    ),
}


def now_user() -> datetime:
    """Now on Sapphire's configured clock (config.USER_TIMEZONE), else OS-local.
    Naive either way, so `replace(hour=…)` arithmetic stays simple."""
    try:
        import config
        from zoneinfo import ZoneInfo
        tz_name = getattr(config, 'USER_TIMEZONE', '') or ''
        if tz_name:
            return datetime.now(ZoneInfo(tz_name)).replace(tzinfo=None)
    except Exception:
        pass
    return datetime.now()


def parse_hhmm(text) -> tuple[int, int] | None:
    m = re.fullmatch(r'\s*(\d{1,2}):(\d{2})\s*', str(text or ''))
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def parse_channels(text) -> list[str]:
    """Channel ids from a comma / space / newline separated field. An
    `account:channel_id` entry (the old picker format) yields its id."""
    if isinstance(text, (list, tuple)):
        parts = [str(p) for p in text]
    else:
        parts = re.split(r'[,\s]+', str(text or ''))
    ids: list[str] = []
    for part in parts:
        part = part.strip()
        if ':' in part:
            part = part.rsplit(':', 1)[-1].strip()
        if part.isdigit() and part not in ids:
            ids.append(part)
    return ids


class _MemoryState:
    def __init__(self):
        self._d: dict = {}

    def get(self, key, default=None):
        return self._d.get(key, default)

    def save(self, key, value):
        self._d[key] = value


class GreetingsClock:
    def __init__(self, *, plugin_loader, transport=None, message_repository=None,
                 channel_repository=None, account_repository=None, state=None):
        self.plugin_loader = plugin_loader
        self.transport = transport
        self.message_repository = message_repository
        self.channel_repository = channel_repository
        self.account_repository = account_repository
        self.state = state if state is not None else _MemoryState()

    # -- the tick -----------------------------------------------------------
    def tick(self, now: datetime | None = None) -> list[dict]:
        """Fire every due greeting/goodnight. Returns what was fired."""
        tasks_for = getattr(self.plugin_loader, 'tasks_for_source', None)
        fire = getattr(self.plugin_loader, 'fire_task', None)
        if not callable(tasks_for) or not callable(fire):
            return []
        now = now or now_user()
        connected = set(self.transport.list_connected()) if self.transport else None
        fired: list[dict] = []
        for source in SOURCES:
            for task in tasks_for(source) or []:
                tc = task.get('trigger_config') or {}
                account = str(tc.get('account') or '').strip()
                channels = parse_channels(tc.get('channels'))
                if not account or not channels:
                    continue
                if connected is not None and account not in connected:
                    continue
                for kind, key in KINDS:
                    if not self._due(tc.get(key), now):
                        continue
                    for channel_id in channels:
                        latch = f"{task.get('id')}:{kind}:{channel_id}"
                        if self._fired_today(latch, now):
                            continue
                        payload = self.build_payload(account, channel_id, kind)
                        result = fire(task['id'], payload, plugin='discord') or {}
                        if result.get('success'):
                            self._latch(latch, now)
                            fired.append({'task_id': task.get('id'), 'kind': kind,
                                          'account': account, 'channel_id': channel_id})
                            logger.info('[DISCORD] %s fired into task %r for channel %s',
                                        kind, task.get('name') or task.get('id'), channel_id)
                        else:
                            logger.warning('[DISCORD] %s for task %r not accepted: %s', kind,
                                           task.get('name') or task.get('id'), result.get('error'))
        return fired

    @staticmethod
    def _due(text, now: datetime) -> bool:
        hm = parse_hhmm(text)
        if not hm:
            return False
        at = now.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
        delta = (now - at).total_seconds()
        return 0 <= delta < WINDOW_SECONDS

    # -- the durable per-day latch ---------------------------------------------
    def _fired_today(self, latch: str, now: datetime) -> bool:
        return (self.state.get(STATE_KEY) or {}).get(latch) == now.strftime('%Y-%m-%d')

    def _latch(self, latch: str, now: datetime) -> None:
        today = now.strftime('%Y-%m-%d')
        fired = dict(self.state.get(STATE_KEY) or {})
        fired = {k: v for k, v in fired.items() if v == today}      # yesterday's entries drop
        fired[latch] = today
        self.state.save(STATE_KEY, fired)

    # -- the event -------------------------------------------------------------
    def build_payload(self, account: str, channel_id: str, kind: str) -> dict:
        guild_id, guild_name, channel_name = self._names(channel_id)
        return {
            'account': account,
            'guild_id': guild_id,
            'guild_name': guild_name,
            'channel_id': channel_id,
            'channel_name': channel_name,
            'message_id': f'proactive-{kind}-{channel_id}-{int(time.time())}',
            'content': INSTRUCTIONS[kind],
            'recent_history': self._recent(account, channel_id),
            'proactive_kind': kind,
        }

    def _names(self, channel_id: str) -> tuple[str, str, str]:
        if not self.channel_repository:
            return '', '', ''
        try:
            channel = self.channel_repository.get_channel(channel_id) or {}
            guild_id = str(channel.get('guild_id') or '')
            guild_name = self.channel_repository.get_guild_name(guild_id) if guild_id else ''
            return guild_id, str(guild_name or ''), str(channel.get('name') or '')
        except Exception:
            logger.debug('[DISCORD] channel names unavailable for %s', channel_id, exc_info=True)
            return '', '', ''

    def _recent(self, account: str, channel_id: str) -> list[str]:
        """Recent chat so she greets in context — her own lines labelled 'You'."""
        if not self.message_repository:
            return []
        try:
            rows = self.message_repository.get_recent_messages(account, channel_id, limit=20) or []
        except Exception:
            logger.debug('[DISCORD] recent history unavailable for %s', channel_id, exc_info=True)
            return []
        if not rows:
            return []
        from plugins.discord.conversation.bot_identity import bot_identity_fields, bot_name_aliases
        from plugins.discord.conversation.transcript_service import format_recent_history
        fields = bot_identity_fields(account, transport=self.transport, account_repository=self.account_repository)
        bot_names = {name.lower() for name in bot_name_aliases(fields)}
        lines = []
        for line in format_recent_history(rows, line_max_chars=1000):
            author, _, rest = line.partition(':')
            lines.append(f'You:{rest}' if author.strip().lower() in bot_names else line)
        return lines
