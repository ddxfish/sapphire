"""Send messages and reactions on the daemon asyncio loop."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from plugins.discord.transport.voice_occupancy import voice_channel_occupancy

logger = logging.getLogger(__name__)

MESSAGE_LIMIT = 1900


def _event_guild_id() -> str:
    try:
        from core.continuity.executor import current_event_data
        event = current_event_data.get() or {}
        if isinstance(event, dict):
            return str(event.get('guild_id') or '').strip()
    except ImportError:
        return ''
    return ''


def _build_reference(channel_id: int, message_id: str | int):
    import discord
    try:
        message_id = int(message_id)
    except (TypeError, ValueError):
        return None
    if message_id <= 0:
        return None
    if hasattr(discord, 'MessageReference'):
        return discord.MessageReference(message_id=message_id, channel_id=int(channel_id), fail_if_not_exists=False)
    return discord.Object(id=message_id)


def _split_message(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    if not text:
        return []
    chunks = []
    remaining = text.strip()
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            pass
            return chunks
        split_at = remaining.rfind('\n', 0, limit)
        if split_at <= 0:
            split_at = remaining.rfind(' ', 0, limit)
        if split_at <= 0:
            split_at = limit
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    return chunks





class DiscordExecution:
    def __init__(self, transport):
        self.transport = transport
        self._voice_listeners = {}
        self._playback_paths = {}
        self._streaming_playback = {}

    def _state_for_account(self, account_name: str | None) -> tuple[str, dict]:
        account_name = str(account_name or '').strip()
        if account_name and account_name in self.transport._accounts:
            return account_name, self.transport._accounts[account_name]
        connected = self.transport.list_connected()
        if connected:
            name = connected[0]
            return name, self.transport._accounts[name]
        if self.transport._accounts:
            name = next(iter(self.transport._accounts))
            return name, self.transport._accounts[name]
        raise RuntimeError('No Discord account is connected')

    async def _resolve_channel(self, account_name: str | None, channel_ref: str | int):
        name, state = self._state_for_account(account_name)
        client = state.get('client')
        if not client:
            raise RuntimeError(f"Account '{name}' has no Discord client")
        channel_ref = str(channel_ref or '').strip().lstrip('#')
        if not channel_ref:
            raise RuntimeError('Channel is required')
        if channel_ref.isdigit():
            channel_id = int(channel_ref)
            channel = client.get_channel(channel_id)
            if channel is None:
                channel = await client.fetch_channel(channel_id)
            if channel is None:
                raise RuntimeError(f'Channel {channel_id} not found')
            return name, channel
        target = channel_ref.lower()
        preferred_guild_id = str(_event_guild_id() or '').strip()
        guilds = list(getattr(client, 'guilds', []) or [])
        if preferred_guild_id:
            guild = client.get_guild(int(preferred_guild_id))
            if guild:
                guilds = [guild] + [g for g in guilds if g.id != guild.id]
        for guild in guilds:
            for ch in getattr(guild, 'text_channels', []) or []:
                if getattr(ch, 'name', '').lower() == target:
                    return name, ch
        raise RuntimeError(f"Channel '{channel_ref}' not found")

    async def resolve_channel_id(self, account_name: str | None, channel_ref: str | int) -> str:
        _, channel = await self._resolve_channel(account_name, channel_ref)
        return str(channel.id)

    async def trigger_typing(self, account_name: str | None, channel_id: str | int) -> None:
        try:
            _, channel = await self._resolve_channel(account_name, channel_id)
            await channel.trigger_typing()
        except Exception as exc:
            logger.debug('Typing indicator failed for channel %s: %s', channel_id, exc)

    async def hold_typing(self, account_name: str | None, channel_id: str | int, duration: float) -> None:
        """Pulse Discord typing indicator for a realistic duration."""
        try:
            _, channel = await self._resolve_channel(account_name, channel_id)
        except Exception as exc:
            logger.debug('Typing hold skipped for channel %s: %s', channel_id, exc)
            return
        duration = max(0.0, float(duration))
        if duration <= 0:
            return
        try:
            loop = asyncio.get_running_loop()
            end_time = loop.time() + duration
            async with channel.typing():
                while loop.time() < end_time:
                    await asyncio.sleep(min(4, end_time - loop.time()))
        except Exception as exc:
            logger.debug('Typing hold failed for channel %s: %s', channel_id, exc)

    async def send_message(
        self,
        channel_id: str | int,
        text: str,
        *,
        account_name: str | None = None,
        reply_to_message_id: str | int | None = None,
    ) -> list[dict]:
        name, channel = await self._resolve_channel(account_name, channel_id)
        reference = None
        if reply_to_message_id:
            reference = _build_reference(int(channel.id), reply_to_message_id)
        sent = []
        for chunk in _split_message(text):
            message = await channel.send(chunk, reference=reference)
            sent.append({'message_id': str(message.id), 'channel_id': str(channel.id), 'account': name})
            reference = None
        return sent

    async def edit_message(
        self,
        channel_id: str | int,
        message_id: str | int,
        text: str,
        *,
        account_name: str | None = None,
    ) -> dict:
        _, channel = await self._resolve_channel(account_name, channel_id)
        message = await channel.fetch_message(int(message_id))
        edited = await message.edit(content=text)
        return {
            'status': 'edited',
            'message_id': str(edited.id),
            'channel_id': str(channel.id),
        }

    async def add_reaction(
        self,
        channel_id: str | int,
        message_id: str | int,
        emoji: str,
        *,
        account_name: str | None = None,
    ) -> dict:
        _, channel = await self._resolve_channel(account_name, channel_id)
        message = await channel.fetch_message(int(message_id))
        await message.add_reaction(emoji)
        return {'status': 'reacted', 'message_id': str(message_id), 'emoji': emoji}

    async def _resolve_voice_channel(self, account_name: str | None, channel_id: str | int):
        import discord
        name, state = self._state_for_account(account_name)
        client = state.get('client')
        if not client:
            raise RuntimeError(f"Account '{name}' has no Discord client")
        channel_id = int(channel_id)
        channel = client.get_channel(channel_id)
        if channel is None:
            channel = await client.fetch_channel(channel_id)
        if channel is None:
            raise RuntimeError(f'Voice channel {channel_id} not found')
        if not isinstance(channel, discord.VoiceChannel):
            stage = getattr(discord, 'StageChannel', None)
            if not stage or not isinstance(channel, stage):
                if getattr(channel, 'type', None) not in (2, 13):
                    raise RuntimeError(f'Channel {channel_id} is not a voice channel')
        if not hasattr(channel, 'connect'):
            raise RuntimeError(f'Channel {channel_id} does not support voice connect')
        return name, state, channel

    async def get_voice_channel_state(self, account_name: str | None, channel_id: str | int) -> dict:
        name, state, channel = await self._resolve_voice_channel(account_name, channel_id)
        bot_id = str(state.get('bot_id') or getattr(getattr(state.get('client'), 'user', None), 'id', '') or '')
        occupancy = voice_channel_occupancy(channel, bot_id)
        guild = getattr(channel, 'guild', None)
        return {
            'status': 'ok',
            'account_name': name,
            'channel_id': str(channel.id),
            'guild_id': str(getattr(guild, 'id', '') or ''),
            'channel_name': str(getattr(channel, 'name', '') or ''),
            **occupancy,
        }

    async def connect_voice(self, account_name: str | None, channel_id: str | int) -> dict:
        name, _state, channel = await self._resolve_voice_channel(account_name, channel_id)
        guild = channel.guild
        existing = getattr(guild, 'voice_client', None) if guild else None
        if existing and getattr(existing, 'channel', None) and str(existing.channel.id) == str(channel.id):
            return {
                'status': 'connected',
                'account_name': name,
                'channel_id': str(channel.id),
                'guild_id': str(getattr(guild, 'id', '') or ''),
            }
        if existing:
            await existing.disconnect(force=True)
        try:
            await channel.connect(reconnect=True, timeout=30)
        except Exception as exc:
            message = str(exc)
            if 'PyNaCl' in message or 'pynacl' in message.lower():
                message = f'{message} — install PyNaCl in the Sapphire environment (pip install PyNaCl) and reload the plugin'
            raise RuntimeError(message) from exc
        voice_client = getattr(guild, 'voice_client', None) if guild else None
        dave_state = {}
        if voice_client:
            from plugins.discord.voice.dave_session import log_voice_dave_state, wait_for_dave_ready
            dave_state = await wait_for_dave_ready(voice_client)
            log_voice_dave_state(voice_client, context='after_connect')
        return {
            'status': 'connected',
            'account_name': name,
            'channel_id': str(channel.id),
            'guild_id': str(getattr(guild, 'id', '') or ''),
            'dave': dave_state,
        }

    async def _voice_client_for_channel(self, account_name: str | None, channel_id: str | int):
        _name, _state, channel = await self._resolve_voice_channel(account_name, channel_id)
        guild = channel.guild
        voice_client = getattr(guild, 'voice_client', None) if guild else None
        if not voice_client or not getattr(voice_client, 'channel', None):
            raise RuntimeError('Bot is not connected to a voice channel')
        if str(voice_client.channel.id) != str(channel_id):
            raise RuntimeError('Bot voice client is connected to a different channel')
        return _name, voice_client

    async def start_voice_listener(
        self,
        account_name: str | None,
        channel_id: str | int,
        *,
        on_utterance,
        loop=None,
        silence_seconds: float = 1.2,
        min_duration_seconds: float = 0.5,
        on_pcm_frame=None,
    ) -> dict:
        from plugins.discord.transport.discord_voice_sink import UtteranceVoiceSink
        from plugins.discord.voice.voice_deps import voice_receive_error
        hint = voice_receive_error()
        if hint:
            raise RuntimeError(hint)
        name, voice_client = await self._voice_client_for_channel(account_name, channel_id)
        key = (name, str(channel_id))
        if key in self._voice_listeners:
            entry = self._voice_listeners[key]
            cached_client = entry.get('voice_client')
            sink = entry.get('sink')
            if sink is not None:
                if on_utterance is not None:
                    sink.on_utterance = on_utterance
                if on_pcm_frame is not None:
                    sink.on_pcm_frame = on_pcm_frame
            if cached_client and getattr(cached_client, 'is_recording', lambda: False)():
                return {'status': 'already_listening', 'account_name': name, 'channel_id': str(channel_id)}
            if cached_client and sink is not None:
                try:
                    if hasattr(sink, 'init'):
                        sink.init(cached_client)
                    elif hasattr(sink, 'vc'):
                        sink.vc = cached_client
                    cached_client.start_recording(sink, lambda *args, **kwargs: None)
                    self._voice_listeners[key] = {'sink': sink, 'voice_client': cached_client}
                    logger.info('Resumed voice recording for %s:%s', name, channel_id)
                    return {'status': 'already_listening', 'account_name': name, 'channel_id': str(channel_id)}
                except Exception as exc:
                    logger.debug('Voice recording resume failed for %s:%s: %s', name, channel_id, exc)
            stale_sink = entry.get('sink')
            self._voice_listeners.pop(key, None)
            if stale_sink and hasattr(stale_sink, 'cleanup'):
                stale_sink.cleanup()
            logger.info('Restarting stale voice listener for %s:%s', name, channel_id)
        from plugins.discord.voice.dave_session import log_voice_dave_state, wait_for_dave_ready
        dave_state = await wait_for_dave_ready(voice_client)
        if dave_state.get('is_dave') and not dave_state.get('dave_ready'):
            raise RuntimeError('DAVE voice encryption is not ready yet — STT would hear corrupted audio. Retry in a few seconds or check davey/py-cord install.')
        log_voice_dave_state(voice_client, context='before_recording')
        if loop is None:
            loop = asyncio.get_running_loop()
        sink = UtteranceVoiceSink(
            on_utterance=on_utterance,
            loop=loop,
            silence_seconds=silence_seconds,
            min_duration_seconds=min_duration_seconds,
            on_pcm_frame=on_pcm_frame,
        )
        if hasattr(sink, 'init'):
            sink.init(voice_client)
        elif hasattr(sink, 'vc'):
            sink.vc = voice_client
        try:
            voice_client.start_recording(sink, lambda *args, **kwargs: None)
        except Exception as exc:
            sink.cleanup()
            raise RuntimeError(f'Voice recording failed: {exc}') from exc
        logger.info(
            'Voice recording started for %s:%s',
            name,
            channel_id,
        )
        self._voice_listeners[key] = {'sink': sink, 'voice_client': voice_client}
        return {'status': 'listening', 'account_name': name, 'channel_id': str(channel_id)}

    async def stop_voice_listener(self, account_name: str | None, channel_id: str | int) -> dict:
        name, _voice_client = await self._voice_client_for_channel(account_name, channel_id)
        key = (name, str(channel_id))
        entry = self._voice_listeners.pop(key, None)
        if not entry:
            return {'status': 'not_listening', 'account_name': name, 'channel_id': str(channel_id)}
        voice_client = entry.get('voice_client')
        sink = entry.get('sink')
        try:
            if voice_client and getattr(voice_client, 'is_recording', lambda: False)():
                voice_client.stop_recording()
        except Exception as exc:
            logger.debug('Voice stop_recording failed for %s:%s: %s', name, channel_id, exc)
        if sink and hasattr(sink, 'cleanup'):
            sink.cleanup()
        return {'status': 'stopped', 'account_name': name, 'channel_id': str(channel_id)}

    async def play_voice_audio(
        self,
        account_name: str | None,
        channel_id: str | int,
        audio_bytes: bytes,
        *,
        audio_format: str = 'wav',
    ) -> dict:
        import discord
        from plugins.discord.transport.discord_audio import write_playback_file
        name, voice_client = await self._voice_client_for_channel(account_name, channel_id)
        if not audio_bytes:
            return {'status': 'empty', 'account_name': name, 'channel_id': str(channel_id)}
        from plugins.discord.voice.dave_session import wait_for_dave_ready
        dave_state = await wait_for_dave_ready(voice_client)
        if dave_state.get('is_dave') and not dave_state.get('dave_ready'):
            return {
                'status': 'error',
                'error': 'DAVE voice encryption not ready — playback would sound garbled',
                'account_name': name,
                'channel_id': str(channel_id),
                'dave': dave_state,
            }
        key = (name, str(channel_id))
        if voice_client.is_playing():
            voice_client.stop()
        await self._stop_streaming_session(name, str(channel_id), voice_client=voice_client)
        old_path = self._playback_paths.pop(key, None)
        if old_path:
            try:
                import os
                os.unlink(old_path)
            except OSError:
                pass
        suffix = f'.{audio_format}' if audio_format and not audio_format.startswith('.') else '.wav'
        path = write_playback_file(audio_bytes, suffix=suffix)
        self._playback_paths[key] = path

        def _after_playback(error):
            if error:
                logger.warning('Voice playback error for %s:%s: %s', name, channel_id, error)
            stored = self._playback_paths.pop(key, None)
            if stored:
                try:
                    import os
                    os.unlink(stored)
                except OSError:
                    pass

        source = discord.FFmpegPCMAudio(path)
        voice_client.play(source, after=_after_playback)
        return {'status': 'playing', 'account_name': name, 'channel_id': str(channel_id), 'bytes': len(audio_bytes)}

    async def stop_voice_playback(self, account_name: str | None, channel_id: str | int) -> dict:
        name, voice_client = await self._voice_client_for_channel(account_name, channel_id)
        await self._stop_streaming_session(name, str(channel_id), voice_client=voice_client)
        if voice_client.is_playing():
            voice_client.stop()
            return {'status': 'stopped', 'account_name': name, 'channel_id': str(channel_id)}
        return {'status': 'idle', 'account_name': name, 'channel_id': str(channel_id)}

    def _streaming_key(self, account_name: str, channel_id: str) -> tuple[str, str]:
        return (str(account_name), str(channel_id))

    async def _stop_streaming_session(
        self,
        account_name: str,
        channel_id: str,
        *,
        voice_client=None,
    ) -> None:
        key = self._streaming_key(account_name, channel_id)
        playback = self._streaming_playback.pop(key, None)
        if playback is not None:
            playback.stop()
        if voice_client is None:
            try:
                _name, voice_client = await self._voice_client_for_channel(account_name, channel_id)
            except Exception:
                return
        if voice_client and voice_client.is_playing():
            voice_client.stop()

    async def start_streaming_playback(self, account_name: str | None, channel_id: str | int) -> dict:
        from plugins.discord.transport.discord_streaming_playback import (
            StreamingVoicePlayback,
            build_pcm_audio_source,
        )

        name, voice_client = await self._voice_client_for_channel(account_name, channel_id)
        from plugins.discord.voice.dave_session import wait_for_dave_ready

        dave_state = await wait_for_dave_ready(voice_client)
        if dave_state.get('is_dave') and not dave_state.get('dave_ready'):
            return {
                'status': 'error',
                'error': 'DAVE voice encryption not ready — playback would sound garbled',
                'account_name': name,
                'channel_id': str(channel_id),
                'dave': dave_state,
            }
        key = self._streaming_key(name, str(channel_id))
        existing = self._streaming_playback.get(key)
        if existing is not None and not existing._stopped:
            existing.begin_turn()
            if not voice_client.is_playing():
                source = build_pcm_audio_source(existing)
                current = existing

                def _after_playback(error):
                    if error:
                        logger.warning(
                            'Streaming voice playback error for %s:%s: %s',
                            name,
                            channel_id,
                            error,
                        )
                    if self._streaming_playback.get(key) is current:
                        self._streaming_playback.pop(key, None)

                voice_client.play(source, after=_after_playback)
            return {
                'status': 'streaming',
                'account_name': name,
                'channel_id': str(channel_id),
                'reused': True,
            }

        await self._stop_streaming_session(name, str(channel_id), voice_client=voice_client)
        playback = StreamingVoicePlayback()
        playback.start()
        source = build_pcm_audio_source(playback)
        self._streaming_playback[key] = playback
        current = playback

        def _after_playback(error):
            if error:
                logger.warning(
                    'Streaming voice playback error for %s:%s: %s',
                    name,
                    channel_id,
                    error,
                )
            if self._streaming_playback.get(key) is current:
                self._streaming_playback.pop(key, None)

        voice_client.play(source, after=_after_playback)
        return {'status': 'streaming', 'account_name': name, 'channel_id': str(channel_id)}

    async def feed_streaming_chunk(
        self,
        account_name: str | None,
        channel_id: str | int,
        chunk: dict,
    ) -> dict:
        name, _voice_client = await self._voice_client_for_channel(account_name, channel_id)
        key = self._streaming_key(name, str(channel_id))
        playback = self._streaming_playback.get(key)
        if playback is None:
            return {'status': 'not_streaming', 'account_name': name, 'channel_id': str(channel_id)}
        playback.feed_chunk(chunk)
        return {
            'status': 'fed',
            'account_name': name,
            'channel_id': str(channel_id),
            'pending_bytes': playback.pending_bytes(),
        }

    async def finish_streaming_playback(self, account_name: str | None, channel_id: str | int) -> dict:
        name, _voice_client = await self._voice_client_for_channel(account_name, channel_id)
        key = self._streaming_key(name, str(channel_id))
        playback = self._streaming_playback.get(key)
        if playback is None:
            return {'status': 'not_streaming', 'account_name': name, 'channel_id': str(channel_id)}
        playback.finish()
        return {'status': 'finishing', 'account_name': name, 'channel_id': str(channel_id)}

    async def stop_streaming_playback(self, account_name: str | None, channel_id: str | int) -> dict:
        name, voice_client = await self._voice_client_for_channel(account_name, channel_id)
        await self._stop_streaming_session(name, str(channel_id), voice_client=voice_client)
        return {'status': 'stopped', 'account_name': name, 'channel_id': str(channel_id)}

    async def wait_streaming_playback(
        self,
        account_name: str | None,
        channel_id: str | int,
        *,
        timeout: float = 180.0,
    ) -> dict:
        import asyncio

        name, _voice_client = await self._voice_client_for_channel(account_name, channel_id)
        key = self._streaming_key(name, str(channel_id))
        playback = self._streaming_playback.get(key)
        if playback is None:
            return {'status': 'not_streaming', 'account_name': name, 'channel_id': str(channel_id)}

        def _wait():
            playback.wait(timeout=timeout)

        await asyncio.to_thread(_wait)
        return {'status': 'drained', 'account_name': name, 'channel_id': str(channel_id)}

    async def disconnect_voice(self, account_name: str | None, channel_id: str | int | None = None) -> dict:
        name, state = self._state_for_account(account_name)
        client = state.get('client')
        if not client:
            raise RuntimeError(f"Account '{name}' has no Discord client")
        for key in list(self._voice_listeners):
            if key[0] == name and (not channel_id or key[1] == str(channel_id)):
                self._voice_listeners.pop(key, None)
        disconnected = False
        left_channel = None
        for guild in getattr(client, 'guilds', []) or []:
            voice_client = getattr(guild, 'voice_client', None)
            if not voice_client or not getattr(voice_client, 'channel', None):
                continue
            if channel_id and str(voice_client.channel.id) != str(channel_id):
                continue
            left_channel = voice_client.channel
            await voice_client.disconnect(force=True)
            disconnected = True
            break
        if not disconnected:
            return {'status': 'not_connected', 'account_name': name, 'channel_id': str(channel_id or '')}
        return {
            'status': 'disconnected',
            'account_name': name,
            'channel_id': str(getattr(left_channel, 'id', '') or channel_id or ''),
            'guild_id': str(getattr(getattr(left_channel, 'guild', None), 'id', '') or ''),
        }

    async def change_presence(
        self,
        account_name: str | None,
        *,
        status: str = 'online',
        activity: str | None = None,
    ) -> dict:
        import discord
        from plugins.discord.presence.activity_parser import parse_activity_entry
        name, state = self._state_for_account(account_name)
        client = state.get('client')
        if not client:
            raise RuntimeError(f"Account '{name}' has no Discord client")
        status_map = {
            'online': discord.Status.online,
            'idle': discord.Status.idle,
            'dnd': discord.Status.dnd,
            'invisible': discord.Status.invisible,
        }
        discord_status = status_map.get(str(status or 'online').lower(), discord.Status.online)
        discord_activity = None
        activity_text = str(activity or '').strip()
        if activity_text:
            kind, label = parse_activity_entry(activity_text)
            if kind == 'playing':
                discord_activity = discord.Game(name=label or activity_text)
            elif kind == 'watching':
                discord_activity = discord.Activity(type=discord.ActivityType.watching, name=label or activity_text)
            elif kind == 'competing':
                discord_activity = discord.Activity(type=discord.ActivityType.competing, name=label or activity_text)
            elif kind == 'custom':
                discord_activity = discord.CustomActivity(name=label or activity_text)
            elif kind == 'listening':
                discord_activity = discord.Activity(type=discord.ActivityType.listening, name=label or activity_text)
        await client.change_presence(status=discord_status, activity=discord_activity)
        return {
            'status': 'updated',
            'account_name': name,
            'presence': {
                'status': status,
                'activity': activity_text,
            },
        }

    async def send_url(
        self,
        channel_id: str | int,
        url: str,
        *,
        account_name: str | None = None,
    ) -> dict:
        _, channel = await self._resolve_channel(account_name, channel_id)
        message = await channel.send(url)
        return {'message_id': str(message.id), 'channel_id': str(channel.id)}
