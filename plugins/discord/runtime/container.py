"""Dependency injection root for the Discord cognitive plugin."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

from plugins.discord import hooks_out
from plugins.discord.conversation.policy_service import PolicyService
from plugins.discord.conversation.batching_service import BatchingService
from plugins.discord.conversation.bot_session_service import BotSessionService
from plugins.discord.conversation.conversation_service import ConversationService
from plugins.discord.conversation.mention_map_service import MentionMapService
from plugins.discord.conversation.message_pipeline_service import MessagePipelineService
from plugins.discord.conversation.gif_service import GifService
from plugins.discord.conversation.media_service import MediaService
from plugins.discord.conversation.prompt_context_service import PromptContextService
from plugins.discord.conversation.delivery_style_service import DeliveryStyleService
from plugins.discord.conversation.edit_history_service import EditHistoryService
from plugins.discord.conversation.reaction_service import ReactionService
from plugins.discord.conversation.reply_style_service import ReplyStyleService
from plugins.discord.greetings import GreetingsClock
from plugins.discord.models.settings import SettingsStore
from plugins.discord.observability.llm_debug_service import LlmDebugService
from plugins.discord.observability.trace_service import TraceService
from plugins.discord.runtime.health import RuntimeHealth
from plugins.discord.runtime.forget_service import ForgetService
from plugins.discord.runtime.retention_service import RetentionService
from plugins.discord.runtime.lifecycle import LifecycleManager
from plugins.discord.runtime.scheduler_loop import SchedulerLoop
from plugins.discord.sapphire.event_bridge import SapphireEventBridge
from plugins.discord.sapphire.llm_bridge import SapphireLlmBridge
from plugins.discord.sapphire.scheduler_bridge import SapphireSchedulerBridge
from plugins.discord.sapphire.settings_bridge import SapphireSettingsBridge
from plugins.discord.sapphire.speech_bridge import SapphireSpeechBridge
from plugins.discord.storage.repositories.accounts import AccountRepository
from plugins.discord.storage.repositories.channels import ChannelRepository
from plugins.discord.storage.repositories.media import MediaRepository
from plugins.discord.storage.repositories.messages import MessageRepository
from plugins.discord.storage.repositories.traces import TraceRepository
from plugins.discord.storage.repositories.voice_sessions import VoiceSessionRepository
from plugins.discord.storage.sqlite import SQLiteService, resolve_default_db_path
from plugins.discord.transport.discord_event_adapter import DiscordEventAdapter
from plugins.discord.transport.discord_transport import DiscordTransport
from plugins.discord.transport.voice_transport import VoiceTransport
from plugins.discord.voice.voice_execution_service import VoiceExecutionService
from plugins.discord.voice.voice_perception_service import VoicePerceptionService
from plugins.discord.voice.voice_service import VoiceService
from plugins.discord.voice.voice_turn_taking_service import VoiceTurnTakingService
from plugins.discord.voice.voice_session_service import VoiceSessionService
from plugins.discord.voice.auto_join_service import VoiceAutoJoinService
from plugins.discord.voice.discord_conversation_runner import DiscordConversationRunner
from plugins.discord.sapphire.voice_event_bridge import VoiceEventBridge
from plugins.discord.voice.voice_streaming_playback_service import VoiceStreamingPlaybackService
from plugins.discord.voice.voice_listener_service import VoiceListenerService
from plugins.discord.voice.voice_conversation_service import VoiceConversationService


@dataclass
class RuntimeContainer:
    plugin_name: str
    plugin_loader: object
    settings: dict
    loop: asyncio.AbstractEventLoop

    def __post_init__(self):
        database_path = self.settings.get("database_path") or resolve_default_db_path(self.plugin_name)
        self.health = RuntimeHealth()
        self.lifecycle = LifecycleManager()
        self.sqlite_service = SQLiteService(database_path)
        self.scheduler = SchedulerLoop(interval_seconds=float(self.settings.get("scheduler_interval_seconds", 15)))
        self.settings_store = None
        self.transport = None
        self.account_repository = None
        self.channel_repository = None
        self.message_repository = None
        self.trace_repository = None
        self.presence_repository = None
        self.media_repository = None
        self.voice_session_repository = None
        self.event_bridge = None
        self.llm_bridge = None
        self.scheduler_bridge = None
        self._connect_backoff = {}
        self._last_voice_reap = 0.0
        self.settings_bridge = None
        self.speech_bridge = None
        self.event_adapter = None
        self.batching_service = None
        self.message_pipeline = None
        self.policy_service = None
        self.prompt_context_service = None
        self.reply_style_service = None
        self.delivery_style_service = None
        self.edit_history_service = None
        self.reaction_service = None
        self.gif_service = None
        self.conversation_service = None
        self.media_service = None
        self.greetings = None
        self.mention_map_service = None
        self.voice_transport = None
        self.voice_session_service = None
        self.voice_perception_service = None
        self.voice_execution_service = None
        self.voice_turn_taking_service = None
        self.voice_listener_service = None
        self.voice_conversation_service = None
        self.voice_service = None
        self.voice_auto_join_service = None
        self.trace_service = None
        self.llm_debug_service = None
        self.retention_service = None

    async def start(self) -> None:
        await self.lifecycle.start(self)

    async def stop(self) -> None:
        await self.lifecycle.stop(self)

    def build_settings_store(self) -> None:
        # Global layer lives in core plugin settings (read live at resolve time);
        # this store only carries guild/channel/dm overlays once repositories load.
        self.settings_store = SettingsStore()

    def build_repositories(self) -> None:
        self.account_repository = AccountRepository(self.sqlite_service)
        self.channel_repository = ChannelRepository(self.sqlite_service)
        self.message_repository = MessageRepository(self.sqlite_service)
        self.trace_repository = TraceRepository(self.sqlite_service)
        self.trace_service = TraceService(trace_repository=self.trace_repository)
        self.llm_debug_service = LlmDebugService(limit=10, plugin_loader=self.plugin_loader)
        self.forget_service = ForgetService(sqlite_service=self.sqlite_service, trace_repository=self.trace_repository)
        self.retention_service = RetentionService(
            sqlite_service=self.sqlite_service, trace_repository=self.trace_repository,
            forget_service=self.forget_service,
        )
        self.media_repository = MediaRepository(self.sqlite_service)
        self.voice_session_repository = VoiceSessionRepository(self.sqlite_service)
        self.settings_store = self.channel_repository.load_settings_store()

    def build_bridges(self) -> None:
        self.event_bridge = SapphireEventBridge(self.plugin_loader, llm_debug_service=self.llm_debug_service)
        self.llm_bridge = SapphireLlmBridge(self.plugin_loader)
        self.scheduler_bridge = SapphireSchedulerBridge(self.plugin_loader)
        self.settings_bridge = SapphireSettingsBridge(self.plugin_loader, self.plugin_name)
        self.speech_bridge = SapphireSpeechBridge(self.plugin_loader)

    def build_media_and_clock(self) -> None:
        self.media_service = MediaService(
            media_repository=self.media_repository,
            llm_bridge=self.llm_bridge,
            trace_repository=self.trace_repository,
            scheduler_bridge=self.scheduler_bridge,
        )
        # S1 (2026-09-22): the greetings clock replaced the proactive family.
        # Times live on the Greetings / All interactions daemon tasks; the
        # clock fires them into the task through core's fire_task.
        get_state = getattr(self.plugin_loader, 'get_plugin_state', None)
        self.greetings = GreetingsClock(
            plugin_loader=self.plugin_loader,
            transport=self.transport,
            message_repository=self.message_repository,
            channel_repository=self.channel_repository,
            account_repository=self.account_repository,
            state=get_state(self.plugin_name) if callable(get_state) else None,
        )
        self.scheduler.set_tick_handler(self._scheduler_tick)

    def build_voice(self) -> None:
        self.voice_transport = VoiceTransport(discord_transport=self.transport)
        self.voice_turn_taking_service = VoiceTurnTakingService()
        self.voice_session_service = VoiceSessionService(
            voice_session_repository=self.voice_session_repository,
            trace_repository=self.trace_repository,
        )
        self.voice_perception_service = VoicePerceptionService(
            voice_session_repository=self.voice_session_repository,
            speech_bridge=self.speech_bridge,
            trace_repository=self.trace_repository,
            settings_store=self.settings_store,
        )
        self.voice_execution_service = VoiceExecutionService(
            speech_bridge=self.speech_bridge,
            voice_transport=self.voice_transport,
            policy_service=self.policy_service,
            turn_taking_service=self.voice_turn_taking_service,
            trace_repository=self.trace_repository,
            trace_service=self.trace_service,
            settings_store=self.settings_store,
        )
        self.voice_streaming_playback_service = VoiceStreamingPlaybackService(
            voice_transport=self.voice_transport,
        )
        self.discord_conversation_runner = DiscordConversationRunner(
            playback_service=self.voice_streaming_playback_service,
            transport=self.transport,
            settings_store=self.settings_store,
            voice_session_service=self.voice_session_service,
            speech_bridge=self.speech_bridge,
            voice_transport=self.voice_transport,
        )
        self.voice_conversation_service = VoiceConversationService(
            voice_execution_service=self.voice_execution_service,
            voice_session_repository=self.voice_session_repository,
            settings_store=self.settings_store,
            reply_style_service=self.reply_style_service,
            trace_repository=self.trace_repository,
            llm_debug_service=self.llm_debug_service,
        )
        self.voice_listener_service = VoiceListenerService(
            voice_transport=self.voice_transport,
            voice_perception_service=self.voice_perception_service,
            voice_conversation_service=self.voice_conversation_service,
            voice_turn_taking_service=self.voice_turn_taking_service,
            conversation_runner=self.discord_conversation_runner,
            voice_session_service=self.voice_session_service,
            settings_store=self.settings_store,
        )
        self.voice_event_bridge = VoiceEventBridge(
            voice_session_repository=self.voice_session_repository,
            trace_repository=self.trace_repository,
            conversation_runner=self.discord_conversation_runner,
        )
        self.voice_service = VoiceService(
            voice_transport=self.voice_transport,
            voice_session_service=self.voice_session_service,
            voice_perception_service=self.voice_perception_service,
            voice_execution_service=self.voice_execution_service,
            voice_listener_service=self.voice_listener_service,
            settings_store=self.settings_store,
            channel_repository=self.channel_repository,
            trace_repository=self.trace_repository,
            loop=self.loop,
        )
        self.voice_auto_join_service = VoiceAutoJoinService(
            transport=self.transport,
            voice_service=self.voice_service,
            settings_store=self.settings_store,
            trace_service=self.trace_service,
        )
        from plugins.discord.voice.voice_deps import voice_receive_error

        hint = voice_receive_error()
        if hint:
            logger.warning("Discord voice receive unavailable:\n%s", hint)

    async def _scheduler_tick(self):
        if not self.transport:
            return
        await self._reconcile_accounts()
        if self.greetings:
            try:
                await asyncio.to_thread(self.greetings.tick)
            except Exception:
                logger.exception("Greetings clock tick failed")
        for account_name in self.transport.list_connected():
            if self.voice_auto_join_service:
                try:
                    await self.voice_auto_join_service.tick_async(account_name)
                except Exception:
                    logger.exception("Voice auto-join tick failed for %s", account_name)
            try:
                # S0 door: add-ons get a clock per connected account (worker
                # thread, so their facade calls may block).
                await asyncio.to_thread(hooks_out.fire, 'discord_tick', self._tick_payload(account_name))
            except Exception:
                logger.exception("discord_tick hook failed for %s", account_name)
        await self._reap_voice_chats()

    def _tick_payload(self, account_name: str) -> dict:
        from datetime import datetime
        guilds = []
        try:
            client = self.transport.get_client(account_name)
            guilds = [str(g.id) for g in (getattr(client, 'guilds', None) or [])]
        except Exception:
            pass
        now = datetime.now()
        return {'account': account_name, 'connected_guilds': guilds,
                'local_hour': now.hour, 'local_time': now.strftime('%H:%M'),
                'interval_s': float(getattr(self.scheduler, 'interval_seconds', 15.0) or 15.0)}

    async def _reap_voice_chats(self) -> None:
        """Delete VC chats idle past their TTL (M8). Once a minute, off the loop
        (core's reaper takes the history lock); chats with a live conversation
        session are excluded."""
        import time
        now = time.monotonic()
        if now - self._last_voice_reap < 60.0:
            return
        self._last_voice_reap = now
        runner = getattr(self, 'discord_conversation_runner', None)
        live = set(runner.active_chat_names()) if runner else set()
        try:
            from core.api_fastapi import get_system
            system = get_system()
        except Exception:
            return
        if system is None:
            return
        from plugins.discord.sapphire.voice_chat import reap_voice_chats
        try:
            await asyncio.to_thread(reap_voice_chats, system, live=live)
        except Exception:
            logger.exception('Voice chat reap failed')

    async def retry_account_connect(self, name: str) -> None:
        """User-triggered retry (e.g. after fixing portal intents): forget backoff, reconcile now."""
        name = str(name or '')
        self._connect_backoff.pop(name, None)
        if self.transport:
            self.transport.clear_connect_failure(name)
        await self._reconcile_accounts()

    async def _reconcile_accounts(self):
        """Keep connections matched to daemon tasks: only task-selected bots stay online."""
        if not self.account_repository or not self.scheduler_bridge:
            return
        import time
        selected = self.scheduler_bridge.selected_accounts()
        connected = set(self.transport.list_connected())
        for name in connected - selected:
            try:
                await self.transport.disconnect_account(name)
                logger.info('Disconnected %s — no enabled daemon task selects it', name)
            except Exception:
                logger.exception('Failed to disconnect %s', name)
        for name in selected - connected:
            if time.monotonic() < self._connect_backoff.get(name, 0):
                continue
            # Gateway logins fail AFTER connect_account returns (async runner) —
            # honor those failures too, or a bad token/intents gets hammered every
            # 15s and Discord's daily identify cap eats the token.
            failed_at = self.transport.last_connect_failure(name)
            if failed_at and (time.monotonic() - failed_at) < 300:
                continue
            token = self.account_repository.get_token(name)
            if not token:
                continue
            try:
                await self.transport.connect_account(name, token)
                logger.info('Connecting %s — selected by an enabled daemon task', name)
            except Exception:
                # back off 5 min — repeated failed logins get rate-banned by Discord
                self._connect_backoff[name] = time.monotonic() + 300
                logger.exception('Failed to connect %s (retry in 5 min)', name)

        # <<HANG UP>> sentinel: the runner leaves through the same door as
        # /voice leave and the leave tool (session closed, listener stopped,
        # summary, disconnect).
        def _leave_voice(account_name: str, channel_id: str) -> dict:
            from plugins.discord.models.intentions import LeaveVoiceIntention
            return self.voice_service.leave(LeaveVoiceIntention(
                intention_type='leave_voice', account_name=account_name,
                channel_id=str(channel_id), message_id='', reason='hangup_sentinel',
            ))

        self.discord_conversation_runner.leave_fn = _leave_voice

    def build_transport(self) -> None:
        self.mention_map_service = MentionMapService(
            message_repository=self.message_repository,
            channel_repository=self.channel_repository,
        )
        self.transport = DiscordTransport(
            loop=self.loop,
            account_repository=self.account_repository,
            mention_map_service=self.mention_map_service,
        )
        self.mention_map_service.set_transport(self.transport)
        self.policy_service = PolicyService()
        self.bot_session_service = BotSessionService()
        self.reply_style_service = ReplyStyleService()
        self.delivery_style_service = DeliveryStyleService()
        self.edit_history_service = EditHistoryService()
        self.reaction_service = ReactionService(
            message_repository=self.message_repository,
            trace_repository=self.trace_repository,
        )
        self.gif_service = GifService(trace_repository=self.trace_repository)
        self.build_media_and_clock()
        self.event_adapter = DiscordEventAdapter(
            message_repository=self.message_repository,
            channel_repository=self.channel_repository,
            trace_repository=self.trace_repository,
            media_service=self.media_service,
            settings_store=self.settings_store,
            mention_map_service=self.mention_map_service,
            llm_debug_service=self.llm_debug_service,
        )
        # Wire channel.batching_seconds — this was a live UI knob that nothing
        # read (the service always ran on its hardcoded 8s default).
        try:
            _batch_window = float(self.settings_store.resolve().channel.batching_seconds)
        except Exception:
            _batch_window = 8.0
        self.batching_service = BatchingService(
            default_window_seconds=max(1.0, _batch_window))
        self.prompt_context_service = PromptContextService(
            message_repository=self.message_repository,
            media_service=self.media_service,
            trace_service=self.trace_service,
            edit_history_service=self.edit_history_service,
        )
        self.conversation_service = ConversationService(
            event_bridge=self.event_bridge,
            policy_service=self.policy_service,
            prompt_context_service=self.prompt_context_service,
            trace_repository=self.trace_repository,
            media_service=self.media_service,        # image-IN lane was never wired (row 14)
            reply_style_service=self.reply_style_service,
            delivery_style_service=self.delivery_style_service,
            edit_history_service=self.edit_history_service,
            transport=self.transport,
            gif_service=self.gif_service,
            reaction_service=self.reaction_service,
            settings_store=self.settings_store,
            trace_service=self.trace_service,
            account_repository=self.account_repository,
            bot_session_service=self.bot_session_service,
            mention_map_service=self.mention_map_service,
            llm_debug_service=self.llm_debug_service,
        )
        self.message_pipeline = MessagePipelineService(
            batching_service=self.batching_service,
            conversation_service=self.conversation_service,
            trace_repository=self.trace_repository,
        )
        self.transport.set_event_adapter(self.event_adapter)
        self.transport.set_message_pipeline(self.message_pipeline)
        self.transport.set_on_account_connected(self._on_account_connected)
        self.build_voice()

    async def _on_account_connected(self, account_name: str) -> None:
        # Presence is the discord-personality plugin's job now (its presence
        # module sets it on the next tick); the host connects plain online.
        logger.debug('[DISCORD] account %s connected', account_name)
