"""Opt-in auto-join over the voice channels a rule covers; leave when empty (S6, filters 2026-09-22).

Each tick, every voice channel the bot can see is matched against the gate
(most-specific rule wins). Any covered channel: she leaves when it empties.
A rule with `auto_join` ON also walks her in when a human is there — unless
the channel is LATCHED: she left it on purpose (<<HANG UP>>, /voice leave,
the leave tool) and is not rejoined until it has emptied once (Krem's trap,
2026-09-22: she hung up and auto-join marched her straight back in). A live
session in a channel no rule covers any more is left: voice off = leave now
(broadsword H4).
"""

from __future__ import annotations

import logging

from plugins.discord.models.intentions import JoinVoiceIntention, LeaveVoiceIntention

logger = logging.getLogger(__name__)
AUTO_REASONS = ('auto_join_empty', 'no_voice_task')


class VoiceAutoJoinService:
    def __init__(self, *, transport, voice_service, gate, trace_service=None):
        self.transport = transport
        self.voice_service = voice_service
        self.gate = gate
        self.trace_service = trace_service
        self._latched: set[tuple[str, str]] = set()
        self._last_state: dict[tuple[str, str], str] = {}

    # ── the latch ──
    def note_leave(self, account_name: str, channel_id: str, reason: str = '') -> None:
        """Every leave passes here (VoiceService.on_leave); a deliberate one latches the channel."""
        if reason in AUTO_REASONS:
            return
        key = (account_name, str(channel_id))
        if key not in self._latched:
            self._latched.add(key)
            logger.info('Voice auto-join latched %s:%s (%s) — no rejoin until the channel empties',
                        account_name, channel_id, reason or 'left')

    def latched(self, account_name: str, channel_id: str) -> bool:
        return (account_name, str(channel_id)) in self._latched

    # ── reporting (GET voice/auto-join) ──
    def inspect(self, account_name: str) -> dict:
        if self.gate is None or not self.gate.tasks(account_name):
            return {'enabled': False, 'reason': 'no_voice_task', 'targets': []}
        fn = getattr(self.transport, 'list_voice_targets_sync', None)
        try:
            targets = fn(account_name) if callable(fn) else []
        except Exception as exc:
            return {'enabled': True, 'reason': f'targets_unavailable: {exc}', 'targets': []}
        rows = []
        for target in targets:
            task = self.gate.select(account_name, target)
            if task is None:
                continue
            channel_id = str(target.get('channel_id') or '')
            auto = self.gate.auto_join(task)
            rows.append({'account_name': account_name, 'channel_id': channel_id,
                         'channel_name': target.get('channel_name', ''), 'guild_id': target.get('guild_id', ''),
                         'guild_name': target.get('guild_name', ''), 'task': str(task.get('name') or ''),
                         'auto_join': auto, 'latched': self.latched(account_name, channel_id),
                         'human_count': int(target.get('human_count') or 0),
                         'member_count': int(target.get('member_count') or 0),
                         'bot_connected': bool(target.get('bot_connected')),
                         'status': 'watching' if auto else 'manual'})
        return {'enabled': True, 'reason': 'ok', 'targets': rows}

    # ── the tick (~15 s, on the daemon loop) ──
    async def tick_async(self, account_name: str) -> list[dict]:
        results = []
        for channel_id in self._live_channels(account_name):
            if self.gate is None or self.gate.allowed(account_name, channel_id) is None:
                results.append(await self._leave_async(account_name, channel_id, reason='no_voice_task'))
        if self.gate is None or not self.voice_service:
            return results
        for target in await self._targets(account_name):
            task = self.gate.select(account_name, target)
            if task is None:
                continue
            result = await self._evaluate_async(account_name, target, task)
            if result is not None:
                results.append(result)
        return results

    async def _targets(self, account_name: str) -> list[dict]:
        fn = getattr(self.transport, 'list_voice_targets', None)
        if not callable(fn):
            return []
        try:
            return await fn(account_name) or []
        except Exception as exc:
            logger.warning('Voice auto-join could not list voice channels for %s: %s', account_name, exc)
            return []

    async def _evaluate_async(self, account_name: str, target: dict, task: dict):
        channel_id = str(target.get('channel_id') or '')
        key = (account_name, channel_id)
        humans = int(target.get('human_count') or 0)
        connected = bool(target.get('bot_connected'))
        if humans == 0:
            self._latched.discard(key)
            self._last_state.pop(key, None)
            if connected:
                return await self._leave_async(account_name, channel_id, reason='auto_join_empty')
            return None
        if connected:
            ensured = await self.voice_service.ensure_listener_async(
                account_name, channel_id, guild_id=str(target.get('guild_id') or ''))
            if (ensured or {}).get('status') == 'error':
                logger.warning('Voice listener ensure failed for %s:%s: %s', account_name, channel_id, ensured)
            self._log_state(key, target, f'live ({humans} humans)')
            return None
        if not self.gate.auto_join(task):
            self._log_state(key, target, f'{humans} humans, waiting for /voice join')
            return None
        if key in self._latched:
            self._log_state(key, target, f'{humans} humans, latched after a deliberate leave')
            return None
        return await self._join_async(account_name, channel_id, target, humans)

    def _log_state(self, key, target, state: str) -> None:
        if self._last_state.get(key) != state:
            self._last_state[key] = state
            logger.info('Voice auto-join %s:%s — %s', key[0], target.get('channel_name') or key[1], state)

    # ── join / leave through the voice service ──
    async def _join_async(self, account_name: str, channel_id: str, target: dict, humans: int) -> dict:
        intention = JoinVoiceIntention(intention_type='join_voice', account_name=account_name, channel_id=channel_id,
                                       message_id='', reason='auto_join', guild_id=str(target.get('guild_id') or ''))
        result = await self.voice_service.join_async(intention)
        if self.trace_service:
            self.trace_service.record_voice_decision('auto_join', {'account_name': account_name, 'channel_id': channel_id,
                                                                   'human_count': humans, 'result': result})
        if result.get('status') in ('error', 'blocked'):
            logger.warning('Voice auto-join failed for %s:%s: %s', account_name, channel_id,
                           result.get('reason') or result.get('error') or result.get('status'))
        else:
            logger.info('Voice auto-join %s -> channel %s (%s humans)', account_name, channel_id, humans)
        return result

    async def _leave_async(self, account_name: str, channel_id: str, *, reason: str) -> dict:
        intention = LeaveVoiceIntention(intention_type='leave_voice', account_name=account_name, channel_id=channel_id,
                                        message_id='', reason=reason)
        result = await self.voice_service.leave_async(intention)
        if self.trace_service:
            self.trace_service.record_voice_decision('auto_leave', {'account_name': account_name, 'channel_id': channel_id,
                                                                    'reason': reason, 'result': result})
        logger.info('Voice auto-leave %s <- channel %s (%s)', account_name, channel_id, reason)
        return result

    def _live_channels(self, account_name: str) -> list[str]:
        sessions = getattr(self.voice_service, 'sessions', None)
        if sessions is None:
            return []
        return [str(s.channel_id) for s in sessions.list_active(account_name) if getattr(s, 'channel_id', '')]
