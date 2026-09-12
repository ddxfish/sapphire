"""Poll configured voice channels and join/leave based on occupancy."""

from __future__ import annotations

import logging

from plugins.discord.models.intentions import JoinVoiceIntention, LeaveVoiceIntention
from plugins.discord.proactive.greeting_service import parse_target

logger = logging.getLogger(__name__)


class VoiceAutoJoinService:
    def __init__(self, *, transport, voice_service, settings_store=None, trace_service=None, sleep_service=None):
        self.transport = transport
        self.voice_service = voice_service
        self.settings_store = settings_store
        self.trace_service = trace_service
        self.sleep_service = sleep_service
        self._last_errors: dict[str, str] = {}
        self._last_idle_log: dict[str, str] = {}

    def inspect(self, account_name: str) -> dict:
        settings = self.settings_store.resolve() if self.settings_store else None
        if not settings:
            return {'enabled': False, 'reason': 'no_settings', 'targets': []}
        if not settings.voice.enabled:
            return {'enabled': False, 'reason': 'voice_disabled', 'targets': []}
        if settings.voice.emergency_disabled:
            return {'enabled': False, 'reason': 'voice_emergency_disabled', 'targets': []}
        if self._voice_blocked_for_sleep(account_name, settings):
            return {'enabled': False, 'reason': 'sleeping', 'targets': []}
        targets = settings.voice.join_targets or []
        if not targets:
            return {'enabled': True, 'reason': 'no_targets', 'targets': []}
        rows = []
        for entry in targets:
            parsed = parse_target(entry)
            if not parsed:
                rows.append({'entry': entry, 'status': 'invalid_target'})
                continue
            target_account, channel_id = parsed
            if target_account.lower() != str(account_name or '').lower():
                rows.append({
                    'entry': entry,
                    'account_name': target_account,
                    'channel_id': channel_id,
                    'status': 'other_account',
                })
                continue
            state = self._fetch_state_sync(account_name, channel_id)
            if state.get('status') == 'error':
                rows.append({
                    'entry': entry,
                    'account_name': target_account,
                    'channel_id': channel_id,
                    'status': 'error',
                    'error': state.get('error', 'unknown'),
                })
                continue
            rows.append({
                'entry': entry,
                'account_name': target_account,
                'channel_id': channel_id,
                'channel_name': state.get('channel_name', ''),
                'guild_id': state.get('guild_id', ''),
                'human_count': int(state.get('human_count') or 0),
                'member_count': int(state.get('member_count') or 0),
                'bot_connected': bool(state.get('bot_connected')),
                'status': 'watching',
                'last_error': self._last_errors.get(f'{target_account}:{channel_id}', ''),
            })
        return {'enabled': True, 'reason': 'ok', 'targets': rows}

    def tick(self, account_name: str) -> list[dict]:
        return self._tick_sync(account_name)

    async def tick_async(self, account_name: str) -> list[dict]:
        return await self._tick_async(account_name)

    def _tick_sync(self, account_name: str) -> list[dict]:
        settings = self.settings_store.resolve() if self.settings_store else None
        if not settings or not settings.voice.enabled or settings.voice.emergency_disabled:
            return []
        targets = settings.voice.join_targets or []
        if not targets:
            return []
        if not self.transport or not self.voice_service:
            return []

        sleeping = self._voice_blocked_for_sleep(account_name, settings)

        results: list[dict] = []
        for entry in targets:
            parsed = parse_target(entry)
            if not parsed or parsed[0].lower() != str(account_name or '').lower():
                continue
            channel_id = parsed[1]
            key = f'{account_name}:{channel_id}'
            state = self._fetch_state_sync(account_name, channel_id)
            result = self._evaluate_state_sync(account_name, channel_id, key, state, sleeping=sleeping)
            if result is not None:
                results.append(result)
        return results

    async def _tick_async(self, account_name: str) -> list[dict]:
        settings = self.settings_store.resolve() if self.settings_store else None
        if not settings or not settings.voice.enabled or settings.voice.emergency_disabled:
            return []
        targets = settings.voice.join_targets or []
        if not targets:
            return []
        if not self.transport or not self.voice_service:
            return []

        sleeping = self._voice_blocked_for_sleep(account_name, settings)

        results: list[dict] = []
        for entry in targets:
            parsed = parse_target(entry)
            if not parsed or parsed[0].lower() != str(account_name or '').lower():
                continue
            channel_id = parsed[1]
            key = f'{account_name}:{channel_id}'
            state = await self._fetch_state_async(account_name, channel_id)
            result = await self._evaluate_state_async(account_name, channel_id, key, state, sleeping=sleeping)
            if result is not None:
                results.append(result)
        return results

    def _voice_blocked_for_sleep(self, account_name: str, settings) -> bool:
        if not self.sleep_service:
            return False
        return self.sleep_service.voice_blocked_for_sleep(account_name, settings)

    def _evaluate_state_sync(self, account_name: str, channel_id: str, key: str, state: dict, *, sleeping: bool = False):
        if state.get('status') == 'error':
            error = str(state.get('error') or 'unknown')
            self._last_errors[key] = error
            logger.warning(
                'Voice auto-join skipped %s:%s: %s',
                account_name,
                channel_id,
                error,
            )
            return None

        self._last_errors.pop(key, None)
        humans = int(state.get('human_count') or 0)
        bot_connected = bool(state.get('bot_connected'))

        if sleeping:
            if bot_connected:
                return self._leave(account_name, channel_id, state)
            return None

        if humans > 0 and not bot_connected:
            result = self._join(account_name, channel_id, state, humans)
            if result and result.get('status') in ('error', 'blocked'):
                self._last_errors[key] = str(result.get('reason') or result.get('error') or result.get('status'))
            return result
        if humans > 0 and bot_connected:
            self._ensure_listener(account_name, channel_id, state)
            self._log_idle(account_name, channel_id, key, state, humans, bot_connected)
            return None
        if humans == 0 and bot_connected:
            return self._leave(account_name, channel_id, state)

        self._log_idle(account_name, channel_id, key, state, humans, bot_connected)
        return None

    async def _evaluate_state_async(self, account_name: str, channel_id: str, key: str, state: dict, *, sleeping: bool = False):
        if state.get('status') == 'error':
            error = str(state.get('error') or 'unknown')
            self._last_errors[key] = error
            logger.warning(
                'Voice auto-join skipped %s:%s: %s',
                account_name,
                channel_id,
                error,
            )
            return None

        self._last_errors.pop(key, None)
        humans = int(state.get('human_count') or 0)
        bot_connected = bool(state.get('bot_connected'))

        if sleeping:
            if bot_connected:
                return await self._leave_async(account_name, channel_id)
            return None

        if humans > 0 and not bot_connected:
            result = await self._join_async(account_name, channel_id, state, humans)
            if result and result.get('status') in ('error', 'blocked'):
                self._last_errors[key] = str(result.get('reason') or result.get('error') or result.get('status'))
            return result
        if humans > 0 and bot_connected:
            await self._ensure_listener_async(account_name, channel_id, state)
            self._log_idle(account_name, channel_id, key, state, humans, bot_connected)
            return None
        if humans == 0 and bot_connected:
            return await self._leave_async(account_name, channel_id)

        self._log_idle(account_name, channel_id, key, state, humans, bot_connected)
        return None

    def _ensure_listener(self, account_name: str, channel_id: str, state: dict) -> None:
        if not self.voice_service:
            return
        result = self.voice_service.ensure_listener(
            account_name,
            channel_id,
            guild_id=str(state.get('guild_id') or ''),
        )
        self._record_listener_ensure(account_name, channel_id, result)

    async def _ensure_listener_async(self, account_name: str, channel_id: str, state: dict) -> None:
        if not self.voice_service:
            return
        result = await self.voice_service.ensure_listener_async(
            account_name,
            channel_id,
            guild_id=str(state.get('guild_id') or ''),
        )
        self._record_listener_ensure(account_name, channel_id, result)

    def _record_listener_ensure(self, account_name: str, channel_id: str, result: dict) -> None:
        status = result.get('status', '')
        if status == 'error':
            key = f'{account_name}:{channel_id}'
            self._last_errors[key] = str(result.get('error') or 'listener_failed')
            logger.warning(
                'Voice listener ensure failed for %s:%s: %s',
                account_name,
                channel_id,
                result,
            )

    def _log_idle(self, account_name, channel_id, key, state, humans, bot_connected) -> None:
        idle_key = f'{humans}:{bot_connected}'
        if self._last_idle_log.get(key) != idle_key:
            self._last_idle_log[key] = idle_key
            logger.info(
                'Voice auto-join waiting %s:%s (%s humans, bot_connected=%s)',
                account_name,
                state.get('channel_name') or channel_id,
                humans,
                bot_connected,
            )

    def _fetch_state_sync(self, account_name: str, channel_id: str) -> dict:
        try:
            return self.transport.get_voice_channel_state_sync(account_name, channel_id)
        except Exception as exc:
            logger.warning(
                'Voice auto-join state lookup failed for %s:%s: %s',
                account_name,
                channel_id,
                exc,
            )
            return {'status': 'error', 'error': str(exc)}

    async def _fetch_state_async(self, account_name: str, channel_id: str) -> dict:
        try:
            return await self.transport.get_voice_channel_state_async(account_name, channel_id)
        except Exception as exc:
            logger.warning(
                'Voice auto-join state lookup failed for %s:%s: %s',
                account_name,
                channel_id,
                exc,
            )
            return {'status': 'error', 'error': str(exc)}

    def _join(self, account_name: str, channel_id: str, state: dict, humans: int) -> dict | None:
        intention = JoinVoiceIntention(
            intention_type='join_voice',
            account_name=account_name,
            channel_id=channel_id,
            message_id='',
            reason='auto_join',
            guild_id=str(state.get('guild_id') or ''),
        )
        result = self.voice_service.join(intention)
        return self._record_join(account_name, channel_id, humans, result)

    async def _join_async(self, account_name: str, channel_id: str, state: dict, humans: int) -> dict | None:
        intention = JoinVoiceIntention(
            intention_type='join_voice',
            account_name=account_name,
            channel_id=channel_id,
            message_id='',
            reason='auto_join',
            guild_id=str(state.get('guild_id') or ''),
        )
        result = await self.voice_service.join_async(intention)
        return self._record_join(account_name, channel_id, humans, result)

    def _record_join(self, account_name: str, channel_id: str, humans: int, result: dict) -> dict:
        if self.trace_service:
            self.trace_service.record_voice_decision('auto_join', {
                'account_name': account_name,
                'channel_id': channel_id,
                'human_count': humans,
                'result': result,
            })
        if result.get('status') in ('error', 'blocked'):
            logger.warning(
                'Voice auto-join failed for %s:%s: %s',
                account_name,
                channel_id,
                result.get('reason') or result.get('error') or result.get('status'),
            )
        else:
            logger.info(
                'Voice auto-join %s -> channel %s (%s humans)',
                account_name,
                channel_id,
                humans,
            )
        return result

    def _leave(self, account_name: str, channel_id: str, state: dict) -> dict | None:
        intention = LeaveVoiceIntention(
            intention_type='leave_voice',
            account_name=account_name,
            channel_id=channel_id,
            message_id='',
            reason='auto_join_empty',
        )
        result = self.voice_service.leave(intention)
        return self._record_leave(account_name, channel_id, result)

    async def _leave_async(self, account_name: str, channel_id: str) -> dict | None:
        intention = LeaveVoiceIntention(
            intention_type='leave_voice',
            account_name=account_name,
            channel_id=channel_id,
            message_id='',
            reason='auto_join_empty',
        )
        result = await self.voice_service.leave_async(intention)
        return self._record_leave(account_name, channel_id, result)

    def _record_leave(self, account_name: str, channel_id: str, result: dict) -> dict:
        if self.trace_service:
            self.trace_service.record_voice_decision('auto_leave', {
                'account_name': account_name,
                'channel_id': channel_id,
                'result': result,
            })
        logger.info('Voice auto-leave %s <- channel %s (empty)', account_name, channel_id)
        return result
