"""The runtime: every service the host runs, built once, started and stopped in order (S7).

One object, one place to read the wiring. Routes, tools, hooks and the
schedule handler reach it through daemon.get_runtime().
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime

from plugins.discord import hooks_out
from plugins.discord.accounts import DiscordAccounts
from plugins.discord.conversation.batching_service import BatchingService
from plugins.discord.conversation.bot_gate import BotGate
from plugins.discord.conversation.conversation_service import ConversationService
from plugins.discord.conversation.gif_service import GifService
from plugins.discord.conversation.images import ImageLane
from plugins.discord.conversation.mention_map_service import MentionMapService
from plugins.discord.conversation.message_pipeline_service import MessagePipelineService
from plugins.discord.conversation.reactions import Reactions
from plugins.discord.conversation.reply_style_service import ReplyStyleService
from plugins.discord.greetings import GreetingsClock
from plugins.discord.models.intentions import LeaveVoiceIntention
from plugins.discord.models.settings import SettingsStore
from plugins.discord.observability.decisions import DecisionLog
from plugins.discord.sapphire.event_bridge import SapphireEventBridge
from plugins.discord.sapphire.scheduler_bridge import SapphireSchedulerBridge
from plugins.discord.sapphire.speech_bridge import SapphireSpeechBridge
from plugins.discord.transport.discord_event_adapter import DiscordEventAdapter
from plugins.discord.transport.discord_transport import DiscordTransport
from plugins.discord.transport.voice_transport import VoiceTransport
from plugins.discord.voice import voice_deps
from plugins.discord.voice.auto_join_service import VoiceAutoJoinService
from plugins.discord.voice.discord_conversation_runner import DiscordConversationRunner
from plugins.discord.voice.voice_gate import VoiceGate
from plugins.discord.voice.voice_listener_service import VoiceListenerService
from plugins.discord.voice.voice_service import VoiceService
from plugins.discord.voice.voice_sessions import VoiceSessions

logger = logging.getLogger(__name__)

CONNECT_BACKOFF_SECONDS = 300   # repeated failed logins get rate-banned by Discord
VOICE_REAP_SECONDS = 60


@dataclass
class RuntimeHealth:
    state: str = 'created'
    detail: str = ''
    started_at: float | None = None
    updated_at: float = field(default_factory=time.time)

    def mark(self, state: str, detail: str = '') -> None:
        if state == 'ready' and self.started_at is None:
            self.started_at = time.time()
        self.state, self.detail, self.updated_at = state, detail, time.time()

    def as_dict(self) -> dict:
        return {'state': self.state, 'detail': self.detail, 'started_at': self.started_at, 'updated_at': self.updated_at}


async def _quiet(coro, message: str) -> None:
    try:
        await coro
    except Exception:
        logger.exception(message)


class RuntimeContainer:
    def __init__(self, *, plugin_name: str, plugin_loader, settings: dict, loop: asyncio.AbstractEventLoop,
                 accounts: DiscordAccounts | None = None):
        self.plugin_name = plugin_name
        self.plugin_loader = plugin_loader
        self.settings = dict(settings or {})
        self.loop = loop
        self.health = RuntimeHealth()
        self.tick_seconds = max(1.0, float(self.settings.get('scheduler_interval_seconds', 15)))
        # The bot accounts live in core's credentials manager; the plugin has no database.
        self.account_repository = accounts or DiscordAccounts()
        self._tick_task: asyncio.Task | None = None
        self._connect_backoff: dict[str, float] = {}
        self._last_voice_reap = 0.0
        self._build()

    # ── wiring ───────────────────────────────────────────────────────────────

    def _build(self) -> None:
        loader = self.plugin_loader
        # Core's plugin settings, read live on every resolve (no overlays since S6).
        self.settings_store = SettingsStore()
        self.decisions = DecisionLog()
        self.event_bridge = SapphireEventBridge(loader)
        self.scheduler_bridge = SapphireSchedulerBridge(loader)
        self.speech_bridge = SapphireSpeechBridge(loader)
        self.voice_gate = VoiceGate(loader)

        self.mention_map_service = MentionMapService()
        self.transport = DiscordTransport(loop=self.loop, account_repository=self.account_repository,
                                          mention_map_service=self.mention_map_service)
        self.mention_map_service.set_transport(self.transport)
        self.voice_gate.describe = self.transport.describe_voice_channel

        self.image_lane = ImageLane()
        self.bot_gate = BotGate()
        self.reply_style_service = ReplyStyleService()
        self.reactions = Reactions()
        self.gif_service = GifService()
        self.event_adapter = DiscordEventAdapter(image_lane=self.image_lane, settings_store=self.settings_store,
                                                 mention_map_service=self.mention_map_service)
        self.batching_service = BatchingService(default_window_seconds=self._batch_window())
        self.conversation_service = ConversationService(
            event_bridge=self.event_bridge, transport=self.transport,
            settings_store=self.settings_store, reply_style_service=self.reply_style_service,
            gif_service=self.gif_service, reactions=self.reactions, bot_gate=self.bot_gate,
            mention_map_service=self.mention_map_service, decisions=self.decisions,
            image_lane=self.image_lane, account_repository=self.account_repository,
        )
        self.message_pipeline = MessagePipelineService(batching_service=self.batching_service,
                                                       conversation_service=self.conversation_service)
        self.transport.set_event_adapter(self.event_adapter)
        self.transport.set_message_pipeline(self.message_pipeline)
        self.transport.set_on_account_connected(self._on_account_connected)

        # S1: greeting / goodnight times live on the daemon tasks; the clock
        # fires them into the task through core's fire_task.
        get_state = getattr(loader, 'get_plugin_state', None)
        self.greetings = GreetingsClock(
            plugin_loader=loader, transport=self.transport, account_repository=self.account_repository,
            state=get_state(self.plugin_name) if callable(get_state) else None,
        )

        # Voice: one lane on core's conversation engine, gated by the Realtime rule.
        self.voice_transport = VoiceTransport(discord_transport=self.transport)
        self.voice_sessions = VoiceSessions()
        self.discord_conversation_runner = DiscordConversationRunner(
            transport=self.transport,
            settings_store=self.settings_store, sessions=self.voice_sessions, speech_bridge=self.speech_bridge,
            voice_transport=self.voice_transport, gate=self.voice_gate,
        )
        self.voice_listener_service = VoiceListenerService(
            voice_transport=self.voice_transport, conversation_runner=self.discord_conversation_runner,
            speech_bridge=self.speech_bridge, settings_store=self.settings_store,
        )
        self.voice_service = VoiceService(
            voice_transport=self.voice_transport, sessions=self.voice_sessions, gate=self.voice_gate,
            voice_listener_service=self.voice_listener_service, loop=self.loop,
        )
        self.voice_auto_join_service = VoiceAutoJoinService(transport=self.transport, voice_service=self.voice_service,
                                                            gate=self.voice_gate)
        # Every leave (hang-up, /voice leave, the tool, auto) reaches the latch,
        # and <<HANG UP>> leaves through the same door as /voice leave — wired
        # here at build, not on the first tick.
        self.voice_service.on_leave = self.voice_auto_join_service.note_leave
        self.discord_conversation_runner.leave_fn = self._leave_voice

    def _batch_window(self) -> float:
        try:
            return max(1.0, float(self.settings_store.resolve().channel.batching_seconds))
        except Exception:
            return 8.0

    def refresh_settings(self) -> None:
        """Core's settings-saved hook: the store reads core live; only the one
        construct-time scalar (the batch window) needs a nudge."""
        self.batching_service.default_window_seconds = self._batch_window()

    def _leave_voice(self, account_name: str, channel_id: str) -> dict:
        return self.voice_service.leave(LeaveVoiceIntention(
            intention_type='leave_voice', account_name=account_name, channel_id=str(channel_id),
            message_id='', reason='hangup_sentinel',
        ))

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        try:
            self.health.mark('starting', 'Importing any old account database')
            self.account_repository.import_legacy()
            self.health.mark('starting', 'Applying voice patches')
            voice_deps.apply_patches()
            self.health.mark('starting', 'Starting message pipeline')
            await self.message_pipeline.start()
            self._tick_task = asyncio.create_task(self._tick_loop(), name='discord-tick')
            self.health.mark('ready', 'Runtime ready')
            await self._connect_stored_accounts()
        except Exception as exc:
            self.health.mark('error', str(exc))
            logger.exception('Discord runtime startup failed')
            raise

    async def stop(self) -> None:
        self.health.mark('stopping', 'Stopping message pipeline')
        await _quiet(self.message_pipeline.stop(), 'Message pipeline stop failed')
        if self._tick_task is not None:
            self._tick_task.cancel()
            try:
                await self._tick_task
            except (asyncio.CancelledError, Exception):
                pass
            self._tick_task = None
        self.health.mark('stopping', 'Closing voice')
        await _quiet(self.discord_conversation_runner.stop_all_async(), 'Conversation runner shutdown failed')
        for item in self.voice_transport.list_connections():
            await _quiet(self.voice_transport.disconnect_async(item['account_name'], item['channel_id']),
                         f'Voice disconnect failed for {item}')
        try:
            from plugins.discord.voice import voice_workers
            voice_workers.shutdown()
        except Exception:
            logger.exception('Voice worker pool shutdown failed')
        self.health.mark('stopping', 'Closing transport')
        await _quiet(self.transport.close_all(), 'Transport close failed')
        self.health.mark('stopped', 'Runtime stopped')

    async def _connect_stored_accounts(self) -> None:
        # Only bots selected by an enabled daemon task log in (house semantic);
        # the tick reconciles later if tasks change.
        selected = self.scheduler_bridge.selected_accounts()
        if not selected:
            logger.info('[DISCORD] No enabled daemon task selects a bot — not connecting any accounts')
            return
        first = True
        for account in self.account_repository.list_accounts():
            name = account.get('name', '')
            token = self.account_repository.get_token(name)
            if not name or not token:
                continue
            if name not in selected:
                logger.info('[DISCORD] Skipping %s — no enabled daemon task selects it', name)
                continue
            if not first:
                await asyncio.sleep(5)  # stagger multi-bot boots — Discord rate limits logins
            first = False
            await _quiet(self.transport.connect_account(name, token), f'Failed to connect stored account {name}')

    async def _on_account_connected(self, account_name: str) -> None:
        # Presence is the discord-personality plugin's job (its presence
        # module sets it on the next tick); the host connects plain online.
        logger.debug('[DISCORD] account %s connected', account_name)

    # ── the tick ─────────────────────────────────────────────────────────────

    async def _tick_loop(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception('Discord tick failed')
            await asyncio.sleep(self.tick_seconds)

    async def _tick(self) -> None:
        await self._reconcile_accounts()
        try:
            await asyncio.to_thread(self.greetings.tick)
        except Exception:
            logger.exception('Greetings clock tick failed')
        for account_name in self.transport.list_connected():
            try:
                await self.voice_auto_join_service.tick_async(account_name)
            except Exception:
                logger.exception('Voice auto-join tick failed for %s', account_name)
            try:
                # S0 door: add-ons get a clock per connected account (worker
                # thread, so their facade calls may block).
                await asyncio.to_thread(hooks_out.fire, 'discord_tick', self._tick_payload(account_name))
            except Exception:
                logger.exception('discord_tick hook failed for %s', account_name)
        await self._reap_voice_chats()

    def _tick_payload(self, account_name: str) -> dict:
        guilds = []
        try:
            client = self.transport.get_client(account_name)
            guilds = [str(g.id) for g in (getattr(client, 'guilds', None) or [])]
        except Exception:
            pass
        now = datetime.now()
        return {'account': account_name, 'connected_guilds': guilds, 'local_hour': now.hour,
                'local_time': now.strftime('%H:%M'), 'interval_s': self.tick_seconds}

    async def _reap_voice_chats(self) -> None:
        """Delete VC chats idle past their TTL (M8). Once a minute, off the loop
        (core's reaper takes the history lock); chats with a live session are excluded."""
        now = time.monotonic()
        if now - self._last_voice_reap < VOICE_REAP_SECONDS:
            return
        self._last_voice_reap = now
        live = set(self.discord_conversation_runner.active_chat_names())
        try:
            from core.api_fastapi import get_system
            system = get_system()
        except Exception:
            return
        if system is None:
            return
        from plugins.discord.sapphire.voice_chat import reap_voice_chats
        await _quiet(asyncio.to_thread(reap_voice_chats, system, live=live), 'Voice chat reap failed')

    async def retry_account_connect(self, name: str) -> None:
        """User-triggered retry (e.g. after fixing portal intents): forget backoff, reconcile now."""
        name = str(name or '')
        self._connect_backoff.pop(name, None)
        self.transport.clear_connect_failure(name)
        await self._reconcile_accounts()

    async def _reconcile_accounts(self) -> None:
        """Keep connections matched to daemon tasks: only task-selected bots stay online."""
        selected = self.scheduler_bridge.selected_accounts()
        connected = set(self.transport.list_connected())
        for name in connected - selected:
            await _quiet(self.transport.disconnect_account(name), f'Failed to disconnect {name}')
            logger.info('Disconnected %s — no enabled daemon task selects it', name)
        for name in selected - connected:
            if time.monotonic() < self._connect_backoff.get(name, 0):
                continue
            if (self.transport.account_health(name) or {}).get('state') == 'connecting':
                continue   # the boot connect (or the last tick's) is still logging in
            # Gateway logins fail AFTER connect_account returns (async runner) —
            # honor those failures too, or a bad token/intents gets hammered every
            # tick and Discord's daily identify cap eats the token.
            failed_at = self.transport.last_connect_failure(name)
            if failed_at and (time.monotonic() - failed_at) < CONNECT_BACKOFF_SECONDS:
                continue
            token = self.account_repository.get_token(name)
            if not token:
                continue
            try:
                await self.transport.connect_account(name, token)
                logger.info('Connecting %s — selected by an enabled daemon task', name)
            except Exception:
                self._connect_backoff[name] = time.monotonic() + CONNECT_BACKOFF_SECONDS
                logger.exception('Failed to connect %s (retry in 5 min)', name)
