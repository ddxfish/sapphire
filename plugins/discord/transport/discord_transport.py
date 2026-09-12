"""Discord connection lifecycle management."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Callable

from plugins.discord.transport.discord_execution import DiscordExecution
from plugins.discord.transport.voice_occupancy import voice_channel_occupancy

logger = logging.getLogger(__name__)


def _is_expected_disconnect_error(exc: BaseException) -> bool:
    message = str(exc).strip().lower()
    return message in {'session is closed', 'connector is closed'}


class DiscordTransport:
    def __init__(self, *, loop: asyncio.AbstractEventLoop, client_factory: Callable[..., Any] | None = None, account_repository=None, mention_map_service=None):
        self.loop = loop
        self.client_factory = client_factory
        self.account_repository = account_repository
        self._mention_map_service = mention_map_service
        self._accounts: dict[str, dict] = {}
        self._event_adapter = None
        self._command_service = None
        self._message_pipeline = None
        self._on_account_connected = None
        self._execution = DiscordExecution(self)

    @property
    def execution(self) -> DiscordExecution:
        return self._execution

    def set_event_adapter(self, adapter) -> None:
        self._event_adapter = adapter

    def set_command_service(self, service) -> None:
        self._command_service = service

    def set_message_pipeline(self, pipeline) -> None:
        self._message_pipeline = pipeline

    def set_mention_map_service(self, service) -> None:
        self._mention_map_service = service

    def _resolve_outbound_text(self, text: str, *, channel_id: str, account_name: str | None, guild_id: str = '') -> str:
        if not self._mention_map_service or not text:
            return text
        account = str(account_name or '').strip()
        if not account:
            connected = self.list_connected()
            if len(connected) == 1:
                account = connected[0]
        if not account:
            return text
        return self._mention_map_service.apply_text(
            text,
            account,
            str(channel_id),
            guild_id=guild_id,
        )

    def set_on_account_connected(self, callback) -> None:
        self._on_account_connected = callback

    async def _notify_account_connected(self, account_name: str) -> None:
        callback = self._on_account_connected
        if not callback:
            return
        try:
            result = callback(account_name)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            logger.exception('Account connected callback failed for %s', account_name)

    def _build_intents(self):
        if self.client_factory is not None:
            return None
        import discord
        intents = discord.Intents.default()
        intents.guilds = True
        intents.message_content = True
        intents.members = True
        intents.voice_states = True
        return intents

    def _make_client(self):
        if self.client_factory is not None:
            return self.client_factory(intents=self._build_intents())
        import discord
        return discord.Client(intents=self._build_intents())

    async def connect_account(self, name: str, token: str) -> dict:
        current = self._accounts.get(name)
        if current and current.get('state') in {'connecting', 'connected'}:
            return self.account_health(name)

        client = self._make_client()
        state = {
            'name': name,
            'state': 'connecting',
            'bot_name': '',
            'bot_id': '',
            'last_error': '',
            'client': client,
            'task': None,
            'sent_messages': [],
        }
        self._accounts[name] = state

        if hasattr(client, 'event'):
            @client.event
            async def on_ready():
                user = getattr(client, 'user', None)
                state['state'] = 'connected'
                state['bot_name'] = getattr(user, 'name', '') or ''
                state['bot_id'] = str(getattr(user, 'id', '') or '')
                if self.account_repository:
                    self.account_repository.update_connection_state(name, 'connected', bot_name=state['bot_name'], bot_id=state['bot_id'], last_error='')
                await self._notify_account_connected(name)

            @client.event
            async def on_message(message):
                if self._event_adapter:
                    observation = self._event_adapter.adapt_message_event(name, state.get('bot_id') or None, message)
                    if observation and self._message_pipeline:
                        self._message_pipeline.handle_message(observation)

            @client.event
            async def on_typing(channel, user, when):
                if self._event_adapter:
                    observation = await self._event_adapter.adapt_typing_event(name, state.get('bot_id') or None, channel, user, when)
                    if observation and self._message_pipeline:
                        self._message_pipeline.handle_typing(observation)

        async def runner():
            try:
                await client.start(token)
                if state['state'] == 'connecting':
                    user = getattr(client, 'user', None)
                    state['state'] = 'connected'
                    state['bot_name'] = getattr(user, 'name', '') or ''
                    state['bot_id'] = str(getattr(user, 'id', '') or '')
                    await self._notify_account_connected(name)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if state.get('state') == 'disconnecting' or (
                    state.get('state') in {'disconnecting', 'disconnected'}
                    and _is_expected_disconnect_error(exc)
                ):
                    logger.debug('Discord connection closed for %s: %s', name, exc)
                    return
                logger.error('Discord connection failed for %s: %s', name, exc)
                state['state'] = 'error'
                state['last_error'] = str(exc)
                if self.account_repository:
                    self.account_repository.update_connection_state(name, 'error', last_error=str(exc))

        state['task'] = asyncio.create_task(runner(), name=f'discord-account-{name}')
        await asyncio.sleep(0)
        return self.account_health(name)

    async def disconnect_account(self, name: str) -> dict:
        state = self._accounts.get(name)
        if not state:
            self._accounts[name] = {'name': name, 'state': 'disconnected', 'bot_name': '', 'bot_id': '', 'last_error': '', 'client': None, 'task': None, 'sent_messages': []}
            return self.account_health(name)
        client = state.get('client')
        task = state.get('task')
        state['state'] = 'disconnecting'
        if client and hasattr(client, 'close'):
            await client.close()
        if task:
            try:
                await asyncio.wait_for(task, timeout=1)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                task.cancel()
            except Exception:
                pass
        state['state'] = 'disconnected'
        if self.account_repository:
            self.account_repository.update_connection_state(name, 'disconnected', bot_name=state.get('bot_name', ''), bot_id=state.get('bot_id', ''), last_error='')
        return self.account_health(name)

    async def reconnect_account(self, name: str, token: str) -> dict:
        await self.disconnect_account(name)
        return await self.connect_account(name, token)

    async def close_all(self) -> None:
        for name in list(self._accounts.keys()):
            await self.disconnect_account(name)

    def list_connected(self) -> list[str]:
        return sorted(name for name, state in self._accounts.items() if state.get('state') == 'connected')

    def get_client(self, account_name: str):
        """Return the live py-cord client for an account, or None."""
        state = self._accounts.get(str(account_name or '').strip()) or {}
        return state.get('client')

    def client_map(self) -> dict[str, Any]:
        """Account name → client for connected (or started) bots. Legacy-compat surface."""
        return {
            name: state.get('client')
            for name, state in self._accounts.items()
            if state.get('client') is not None
        }

    def account_health(self, name: str) -> dict:
        state = self._accounts.get(name) or {'name': name, 'state': 'disconnected', 'bot_name': '', 'bot_id': '', 'last_error': ''}
        return {'name': name, 'state': state.get('state', 'disconnected'), 'bot_name': state.get('bot_name', ''), 'bot_id': state.get('bot_id', ''), 'last_error': state.get('last_error', '')}

    async def test_account_token(self, token: str) -> dict:
        token = str(token or '').strip()
        if not token:
            return {'success': False, 'error': 'Bot token required'}
        return {'success': True, 'message': 'Token format accepted'}

    def list_servers(self):
        servers = []
        for name, state in sorted(self._accounts.items()):
            client = state.get('client')
            guilds = getattr(client, 'guilds', []) if client else []
            for guild in guilds:
                servers.append({'account': name, 'id': str(getattr(guild, 'id', '')), 'name': getattr(guild, 'name', '')})
        return servers

    async def list_proactive_targets(self) -> list[dict]:
        """List text channels from connected guilds for proactive target selection."""
        targets: list[dict] = []
        for name, state in sorted(self._accounts.items()):
            if state.get('state') != 'connected':
                continue
            client = state.get('client')
            if not client:
                continue
            for guild in getattr(client, 'guilds', []) or []:
                guild_id = str(getattr(guild, 'id', '') or '')
                guild_name = str(getattr(guild, 'name', '') or 'Server')
                channels = list(getattr(guild, 'text_channels', []) or [])
                if not channels:
                    try:
                        import discord
                        for channel in getattr(guild, 'channels', []) or []:
                            if isinstance(channel, discord.TextChannel):
                                channels.append(channel)
                    except Exception:
                        for channel in getattr(guild, 'channels', []) or []:
                            if getattr(channel, 'type', None) == 0:
                                channels.append(channel)
                for channel in channels:
                    channel_id = str(getattr(channel, 'id', '') or '')
                    channel_name = str(getattr(channel, 'name', '') or 'channel')
                    if not channel_id:
                        continue
                    targets.append({
                        'account': name,
                        'guild_id': guild_id,
                        'guild_name': guild_name,
                        'channel_id': channel_id,
                        'channel_name': channel_name,
                        'value': f'{name}:{channel_id}',
                        'label': f'{name} · {guild_name} · #{channel_name}',
                    })
        targets.sort(key=lambda item: (item['account'].lower(), item['guild_name'].lower(), item['channel_name'].lower()))
        return targets

    async def list_guild_bots(self) -> list[dict]:
        """List other bots visible in connected guilds for allowlist selection."""
        aggregated: dict[str, dict] = {}
        for name, state in sorted(self._accounts.items()):
            if state.get('state') != 'connected':
                continue
            client = state.get('client')
            if not client:
                continue
            self_id = str(state.get('bot_id') or getattr(getattr(client, 'user', None), 'id', '') or '')
            for guild in getattr(client, 'guilds', []) or []:
                guild_id = str(getattr(guild, 'id', '') or '')
                guild_name = str(getattr(guild, 'name', '') or 'Server')
                await self._ensure_guild_members_loaded(guild)
                for member in getattr(guild, 'members', []) or []:
                    if not getattr(member, 'bot', False):
                        continue
                    user_id = str(getattr(member, 'id', '') or '')
                    if not user_id or user_id == self_id:
                        continue
                    username = str(getattr(member, 'name', '') or 'bot')
                    display_name = str(getattr(member, 'display_name', '') or username)
                    entry = aggregated.get(user_id)
                    if entry is None:
                        aggregated[user_id] = {
                            'account': name,
                            'user_id': user_id,
                            'username': username,
                            'display_name': display_name,
                            'guild_names': {guild_name},
                            'guild_ids': {guild_id},
                            'value': user_id,
                        }
                    else:
                        entry['guild_names'].add(guild_name)
                        entry['guild_ids'].add(guild_id)
        bots: list[dict] = []
        for user_id in sorted(aggregated, key=lambda key: aggregated[key]['display_name'].lower()):
            entry = aggregated[user_id]
            guild_names = ', '.join(sorted(entry['guild_names']))
            label = f"{entry['display_name']} (@{entry['username']})"
            if guild_names:
                label = f'{label} · {guild_names}'
            bots.append({
                'account': entry['account'],
                'user_id': user_id,
                'username': entry['username'],
                'display_name': entry['display_name'],
                'guild_ids': sorted(entry['guild_ids']),
                'guild_names': sorted(entry['guild_names']),
                'value': user_id,
                'label': label,
            })
        return bots

    async def _ensure_guild_members_loaded(self, guild) -> None:
        try:
            cached = len(getattr(guild, 'members', []) or [])
            total = int(getattr(guild, 'member_count', 0) or 0)
            if total and cached < min(total, 100) and hasattr(guild, 'chunk'):
                await guild.chunk()
        except Exception:
            logger.debug('Guild member chunk skipped for %s', getattr(guild, 'id', ''), exc_info=True)

    async def list_voice_targets(self) -> list[dict]:
        """List voice channels from connected guilds for auto-join target selection."""
        targets: list[dict] = []
        for name, state in sorted(self._accounts.items()):
            if state.get('state') != 'connected':
                continue
            client = state.get('client')
            if not client:
                continue
            for guild in getattr(client, 'guilds', []) or []:
                guild_id = str(getattr(guild, 'id', '') or '')
                guild_name = str(getattr(guild, 'name', '') or 'Server')
                channels = list(getattr(guild, 'voice_channels', []) or [])
                if not channels:
                    try:
                        import discord
                        for channel in getattr(guild, 'channels', []) or []:
                            if isinstance(channel, discord.VoiceChannel):
                                channels.append(channel)
                    except Exception:
                        for channel in getattr(guild, 'channels', []) or []:
                            if getattr(channel, 'type', None) == 2:
                                channels.append(channel)
                for channel in channels:
                    channel_id = str(getattr(channel, 'id', '') or '')
                    channel_name = str(getattr(channel, 'name', '') or 'voice')
                    if not channel_id:
                        continue
                    bot_id = str(state.get('bot_id') or getattr(getattr(client, 'user', None), 'id', '') or '')
                    occupancy = voice_channel_occupancy(channel, bot_id)
                    member_count = int(occupancy.get('member_count') or 0)
                    targets.append({
                        'account': name,
                        'guild_id': guild_id,
                        'guild_name': guild_name,
                        'channel_id': channel_id,
                        'channel_name': channel_name,
                        'member_count': member_count,
                        'value': f'{name}:{channel_id}',
                        'label': f'{name} · {guild_name} · {channel_name} ({member_count} in channel)',
                    })
        targets.sort(key=lambda item: (item['account'].lower(), item['guild_name'].lower(), item['channel_name'].lower()))
        return targets

    def list_proactive_targets_sync(self) -> list[dict]:
        return self._run_on_loop(self.list_proactive_targets(), timeout=60)

    def list_voice_targets_sync(self) -> list[dict]:
        return self._run_on_loop(self.list_voice_targets(), timeout=60)

    def list_guild_bots_sync(self) -> list[dict]:
        return self._run_on_loop(self.list_guild_bots(), timeout=60)

    def get_voice_channel_state_sync(self, account_name: str, channel_id: str) -> dict:
        try:
            return self._run_on_loop(
                self._execution.get_voice_channel_state(account_name, channel_id),
                timeout=30,
            )
        except Exception as exc:
            logger.warning('Voice channel state failed for %s:%s: %s', account_name, channel_id, exc)
            return {'status': 'error', 'error': str(exc), 'channel_id': str(channel_id)}

    def connect_voice_sync(self, account_name: str, channel_id: str) -> dict:
        try:
            return self._run_on_loop(
                self._execution.connect_voice(account_name, channel_id),
                timeout=45,
            )
        except Exception as exc:
            logger.error('Voice connect failed for %s:%s: %s', account_name, channel_id, exc, exc_info=True)
            return {'status': 'error', 'error': str(exc), 'channel_id': str(channel_id)}

    def disconnect_voice_sync(self, account_name: str, channel_id: str) -> dict:
        try:
            return self._run_on_loop(
                self._execution.disconnect_voice(account_name, channel_id),
                timeout=30,
            )
        except Exception as exc:
            logger.error('Voice disconnect failed for %s:%s: %s', account_name, channel_id, exc, exc_info=True)
            return {'status': 'error', 'error': str(exc), 'channel_id': str(channel_id)}

    def read_messages(self, channel, count=20):
        return []

    def _run_on_loop(self, coro, *, timeout: float = 30):
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self.loop:
            raise RuntimeError(
                'Blocking Discord transport call from the daemon event loop; use async transport methods'
            )
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return future.result(timeout=timeout)

    async def get_voice_channel_state_async(self, account_name: str, channel_id: str) -> dict:
        return await self._execution.get_voice_channel_state(account_name, channel_id)

    async def connect_voice_async(self, account_name: str, channel_id: str) -> dict:
        return await self._execution.connect_voice(account_name, channel_id)

    async def disconnect_voice_async(self, account_name: str, channel_id: str) -> dict:
        return await self._execution.disconnect_voice(account_name, channel_id)

    async def start_voice_listener_async(
        self,
        account_name: str,
        channel_id: str,
        *,
        on_utterance,
        loop=None,
        **kwargs,
    ) -> dict:
        try:
            return await self._execution.start_voice_listener(
                account_name,
                channel_id,
                on_utterance=on_utterance,
                loop=loop or asyncio.get_running_loop(),
                **kwargs,
            )
        except Exception as exc:
            logger.error('Voice listener start failed for %s:%s: %s', account_name, channel_id, exc, exc_info=True)
            return {'status': 'error', 'error': str(exc), 'channel_id': str(channel_id)}

    async def stop_voice_listener_async(self, account_name: str, channel_id: str) -> dict:
        try:
            return await self._execution.stop_voice_listener(account_name, channel_id)
        except Exception as exc:
            logger.warning('Voice listener stop failed for %s:%s: %s', account_name, channel_id, exc)
            return {'status': 'error', 'error': str(exc), 'channel_id': str(channel_id)}

    async def play_voice_audio_async(
        self,
        account_name: str,
        channel_id: str,
        audio_bytes: bytes,
        *,
        audio_format: str = 'wav',
    ) -> dict:
        try:
            return await self._execution.play_voice_audio(
                account_name,
                channel_id,
                audio_bytes,
                audio_format=audio_format,
            )
        except Exception as exc:
            logger.error('Voice playback failed for %s:%s: %s', account_name, channel_id, exc, exc_info=True)
            return {'status': 'error', 'error': str(exc), 'channel_id': str(channel_id)}

    async def stop_voice_playback_async(self, account_name: str, channel_id: str) -> dict:
        try:
            return await self._execution.stop_voice_playback(account_name, channel_id)
        except Exception as exc:
            logger.debug('Voice playback stop failed for %s:%s: %s', account_name, channel_id, exc)
            return {'status': 'error', 'error': str(exc), 'channel_id': str(channel_id)}

    def start_voice_listener_sync(
        self,
        account_name: str,
        channel_id: str,
        *,
        on_utterance,
        loop=None,
        **kwargs,
    ) -> dict:
        try:
            return self._run_on_loop(
                self._execution.start_voice_listener(
                    account_name,
                    channel_id,
                    on_utterance=on_utterance,
                    loop=loop or self.loop,
                    **kwargs,
                ),
                timeout=30,
            )
        except Exception as exc:
            logger.error('Voice listener start failed for %s:%s: %s', account_name, channel_id, exc, exc_info=True)
            return {'status': 'error', 'error': str(exc), 'channel_id': str(channel_id)}

    def stop_voice_listener_sync(self, account_name: str, channel_id: str) -> dict:
        try:
            return self._run_on_loop(
                self._execution.stop_voice_listener(account_name, channel_id),
                timeout=30,
            )
        except Exception as exc:
            logger.warning('Voice listener stop failed for %s:%s: %s', account_name, channel_id, exc)
            return {'status': 'error', 'error': str(exc), 'channel_id': str(channel_id)}

    def play_voice_audio_sync(
        self,
        account_name: str,
        channel_id: str,
        audio_bytes: bytes,
        *,
        audio_format: str = 'wav',
    ) -> dict:
        try:
            return self._run_on_loop(
                self._execution.play_voice_audio(
                    account_name,
                    channel_id,
                    audio_bytes,
                    audio_format=audio_format,
                ),
                timeout=120,
            )
        except Exception as exc:
            logger.error('Voice playback failed for %s:%s: %s', account_name, channel_id, exc, exc_info=True)
            return {'status': 'error', 'error': str(exc), 'channel_id': str(channel_id)}

    def stop_voice_playback_sync(self, account_name: str, channel_id: str) -> dict:
        try:
            return self._run_on_loop(
                self._execution.stop_voice_playback(account_name, channel_id),
                timeout=15,
            )
        except Exception as exc:
            logger.debug('Voice playback stop failed for %s:%s: %s', account_name, channel_id, exc)
            return {'status': 'error', 'error': str(exc), 'channel_id': str(channel_id)}

    async def start_streaming_playback_async(self, account_name: str, channel_id: str) -> dict:
        try:
            return await self._execution.start_streaming_playback(account_name, channel_id)
        except Exception as exc:
            logger.error('Streaming playback start failed for %s:%s: %s', account_name, channel_id, exc, exc_info=True)
            return {'status': 'error', 'error': str(exc), 'channel_id': str(channel_id)}

    def start_streaming_playback_sync(self, account_name: str, channel_id: str) -> dict:
        try:
            return self._run_on_loop(
                self._execution.start_streaming_playback(account_name, channel_id),
                timeout=30,
            )
        except Exception as exc:
            logger.error('Streaming playback start failed for %s:%s: %s', account_name, channel_id, exc, exc_info=True)
            return {'status': 'error', 'error': str(exc), 'channel_id': str(channel_id)}

    async def feed_streaming_chunk_async(self, account_name: str, channel_id: str, chunk: dict) -> dict:
        try:
            return await self._execution.feed_streaming_chunk(account_name, channel_id, chunk)
        except Exception as exc:
            logger.warning('Streaming chunk feed failed for %s:%s: %s', account_name, channel_id, exc)
            return {'status': 'error', 'error': str(exc), 'channel_id': str(channel_id)}

    def feed_streaming_chunk_sync(self, account_name: str, channel_id: str, chunk: dict) -> dict:
        try:
            return self._run_on_loop(
                self._execution.feed_streaming_chunk(account_name, channel_id, chunk),
                timeout=30,
            )
        except Exception as exc:
            logger.warning('Streaming chunk feed failed for %s:%s: %s', account_name, channel_id, exc)
            return {'status': 'error', 'error': str(exc), 'channel_id': str(channel_id)}

    async def finish_streaming_playback_async(self, account_name: str, channel_id: str) -> dict:
        try:
            return await self._execution.finish_streaming_playback(account_name, channel_id)
        except Exception as exc:
            logger.warning('Streaming playback finish failed for %s:%s: %s', account_name, channel_id, exc)
            return {'status': 'error', 'error': str(exc), 'channel_id': str(channel_id)}

    def finish_streaming_playback_sync(self, account_name: str, channel_id: str) -> dict:
        try:
            return self._run_on_loop(
                self._execution.finish_streaming_playback(account_name, channel_id),
                timeout=30,
            )
        except Exception as exc:
            logger.warning('Streaming playback finish failed for %s:%s: %s', account_name, channel_id, exc)
            return {'status': 'error', 'error': str(exc), 'channel_id': str(channel_id)}

    async def stop_streaming_playback_async(self, account_name: str, channel_id: str) -> dict:
        try:
            return await self._execution.stop_streaming_playback(account_name, channel_id)
        except Exception as exc:
            logger.debug('Streaming playback stop failed for %s:%s: %s', account_name, channel_id, exc)
            return {'status': 'error', 'error': str(exc), 'channel_id': str(channel_id)}

    def stop_streaming_playback_sync(self, account_name: str, channel_id: str) -> dict:
        try:
            return self._run_on_loop(
                self._execution.stop_streaming_playback(account_name, channel_id),
                timeout=15,
            )
        except Exception as exc:
            logger.debug('Streaming playback stop failed for %s:%s: %s', account_name, channel_id, exc)
            return {'status': 'error', 'error': str(exc), 'channel_id': str(channel_id)}

    async def wait_streaming_playback_async(
        self,
        account_name: str,
        channel_id: str,
        *,
        timeout: float = 180.0,
    ) -> dict:
        try:
            return await self._execution.wait_streaming_playback(
                account_name,
                channel_id,
                timeout=timeout,
            )
        except Exception as exc:
            logger.warning('Streaming playback wait failed for %s:%s: %s', account_name, channel_id, exc)
            return {'status': 'error', 'error': str(exc), 'channel_id': str(channel_id)}

    def wait_streaming_playback_sync(
        self,
        account_name: str,
        channel_id: str,
        *,
        timeout: float = 180.0,
    ) -> dict:
        try:
            return self._run_on_loop(
                self._execution.wait_streaming_playback(
                    account_name,
                    channel_id,
                    timeout=timeout,
                ),
                timeout=max(30.0, float(timeout) + 5.0),
            )
        except Exception as exc:
            logger.warning('Streaming playback wait failed for %s:%s: %s', account_name, channel_id, exc)
            return {'status': 'error', 'error': str(exc), 'channel_id': str(channel_id)}

    async def send_message_async(self, channel, text, reply_to_message_id=None, account_name=None, guild_id=None):
        try:
            text = self._resolve_outbound_text(
                text,
                channel_id=str(channel),
                account_name=account_name,
                guild_id=str(guild_id or ''),
            )
            sent = await self._execution.send_message(
                channel,
                text,
                account_name=account_name,
                reply_to_message_id=reply_to_message_id,
            )
            return {'status': 'sent', 'channel_id': str(channel), 'messages': sent}
        except Exception as exc:
            logger.error('Discord send failed for channel %s: %s', channel, exc, exc_info=True)
            return {'status': 'error', 'error': str(exc), 'channel_id': str(channel)}

    def send_message_sync(self, channel, text, reply_to_message_id=None, account_name=None, guild_id=None):
        try:
            text = self._resolve_outbound_text(
                text,
                channel_id=str(channel),
                account_name=account_name,
                guild_id=str(guild_id or ''),
            )
            sent = self._run_on_loop(self._execution.send_message(
                channel,
                text,
                account_name=account_name,
                reply_to_message_id=reply_to_message_id,
            ))
            return {'status': 'sent', 'channel_id': str(channel), 'messages': sent}
        except Exception as exc:
            logger.error('Discord send failed for channel %s: %s', channel, exc, exc_info=True)
            return {'status': 'error', 'error': str(exc), 'channel_id': str(channel)}

    def trigger_typing_sync(self, channel, account_name=None):
        try:
            self._run_on_loop(self._execution.trigger_typing(account_name, channel), timeout=5)
            return {'status': 'typing'}
        except Exception as exc:
            logger.debug('Discord typing failed for channel %s: %s', channel, exc)
            return {'status': 'error', 'error': str(exc)}

    def hold_typing_sync(self, channel, duration: float, account_name=None):
        try:
            self._run_on_loop(
                self._execution.hold_typing(account_name, channel, duration),
                timeout=max(5.0, float(duration) + 5.0),
            )
            return {'status': 'typing', 'duration': duration}
        except Exception as exc:
            logger.debug('Discord typing hold failed for channel %s: %s', channel, exc)
            return {'status': 'error', 'error': str(exc)}

    def upload_file_sync(self, channel, file_path, caption='', account_name=None):
        if not Path(file_path).exists():
            raise FileNotFoundError(file_path)
        async def _upload():
            import discord
            _, ch = await self._execution._resolve_channel(account_name, channel)
            file = discord.File(str(file_path))
            content = caption or None
            message = await ch.send(content=content, file=file)
            return {'status': 'uploaded', 'message_id': str(message.id)}
        return self._run_on_loop(_upload())

    def resolve_channel_id_sync(self, channel_ref, account_name=None):
        return self._run_on_loop(self._execution.resolve_channel_id(account_name, channel_ref), timeout=30)

    async def send_gif_async(self, channel, query, account_name=None):
        url = str(query or '').strip()
        if not url.startswith('http'):
            return {'status': 'error', 'error': 'GIF URL required', 'channel_id': str(channel)}
        try:
            return await self._execution.send_url(channel, url, account_name=account_name)
        except Exception as exc:
            logger.error('Discord GIF send failed for channel %s: %s', channel, exc, exc_info=True)
            return {'status': 'error', 'error': str(exc), 'channel_id': str(channel)}

    def send_gif_sync(self, channel, query, account_name=None):
        url = str(query or '').strip()
        if not url.startswith('http'):
            return {'status': 'error', 'error': 'GIF URL required', 'channel_id': str(channel)}
        try:
            return self._run_on_loop(self._execution.send_url(channel, url, account_name=account_name))
        except Exception as exc:
            logger.error('Discord GIF send failed for channel %s: %s', channel, exc, exc_info=True)
            return {'status': 'error', 'error': str(exc), 'channel_id': str(channel)}

    def edit_message_sync(self, channel, message_id, text, account_name=None):
        try:
            return self._run_on_loop(self._execution.edit_message(
                channel, message_id, text, account_name=account_name,
            ))
        except Exception as exc:
            logger.error('Discord edit failed for message %s: %s', message_id, exc, exc_info=True)
            return {'status': 'error', 'error': str(exc), 'message_id': str(message_id)}

    async def add_reaction_async(self, channel, message_id, emoji, account_name=None):
        try:
            return await self._execution.add_reaction(
                channel, message_id, emoji, account_name=account_name,
            )
        except Exception as exc:
            logger.error('Discord reaction failed: %s', exc, exc_info=True)
            return {'status': 'error', 'error': str(exc)}

    def add_reaction_sync(self, channel, message_id, emoji, account_name=None):
        try:
            return self._run_on_loop(self._execution.add_reaction(
                channel, message_id, emoji, account_name=account_name,
            ))
        except Exception as exc:
            logger.error('Discord reaction failed: %s', exc, exc_info=True)
            return {'status': 'error', 'error': str(exc)}

    def change_presence_sync(self, account_name: str, *, status: str = 'online', activity: str = '') -> dict:
        try:
            return self._run_on_loop(
                self._execution.change_presence(
                    account_name,
                    status=status,
                    activity=activity or None,
                ),
                timeout=15,
            )
        except Exception as exc:
            logger.warning('Discord presence update failed for %s: %s', account_name, exc, exc_info=True)
            return {'status': 'error', 'error': str(exc), 'account_name': account_name}

    async def change_presence_async(self, account_name: str, *, status: str = 'online', activity: str = '') -> dict:
        return await self._execution.change_presence(
            account_name,
            status=status,
            activity=activity or None,
        )
