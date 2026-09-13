"""Dependency injection root for the Discord cognitive plugin."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

from plugins.discord.cognition.channel_situation import ChannelSituationService
from plugins.discord.cognition.cognitive_orchestrator import CognitiveOrchestrator
from plugins.discord.cognition.commitment_service import CommitmentService
from plugins.discord.cognition.policy_service import PolicyService
from plugins.discord.cognition.world_model_service import WorldModelService
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
from plugins.discord.memory.birthday_service import BirthdayService
from plugins.discord.memory.distill_service import DistillService
from plugins.discord.memory.interest_service import InterestService
from plugins.discord.memory.lore_service import LoreService
from plugins.discord.memory.memory_service import MemoryService
from plugins.discord.memory.milestone_service import MilestoneService
from plugins.discord.memory.profile_service import ProfileService
from plugins.discord.proactive.greeting_service import GreetingService
from plugins.discord.proactive.outreach_service import OutreachService
from plugins.discord.proactive.proactive_message_service import ProactiveMessageService
from plugins.discord.proactive.proactive_coordinator import ProactiveCoordinator
from plugins.discord.proactive.proactive_executor import ProactiveExecutor
from plugins.discord.proactive.sleep_service import SleepService
from plugins.discord.models.settings import SettingsStore
from plugins.discord.observability.cognition_debug_service import CognitionDebugService
from plugins.discord.observability.llm_debug_service import LlmDebugService
from plugins.discord.observability.trace_service import TraceService
from plugins.discord.runtime.health import RuntimeHealth
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
from plugins.discord.storage.repositories.interests import InterestRepository
from plugins.discord.storage.repositories.lore import LoreRepository
from plugins.discord.storage.repositories.media import MediaRepository
from plugins.discord.storage.repositories.memory import MemoryRepository
from plugins.discord.storage.repositories.messages import MessageRepository
from plugins.discord.storage.repositories.milestones import MilestoneRepository
from plugins.discord.storage.repositories.presence import PresenceRepository
from plugins.discord.storage.repositories.proactive import ProactiveRepository
from plugins.discord.storage.repositories.profile_buffers import ProfileBufferRepository
from plugins.discord.storage.repositories.profiles import ProfileRepository
from plugins.discord.storage.repositories.tasks import TaskRepository
from plugins.discord.storage.repositories.traces import TraceRepository
from plugins.discord.storage.repositories.voice_sessions import VoiceSessionRepository
from plugins.discord.storage.sqlite import SQLiteService, resolve_default_db_path
from plugins.discord.transport.discord_commands import DiscordCommandService
from plugins.discord.transport.discord_event_adapter import DiscordEventAdapter
from plugins.discord.transport.discord_presence import DiscordPresenceService
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
        self.memory_repository = None
        self.profile_repository = None
        self.task_repository = None
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
        self.command_service = None
        self.world_model_service = None
        self.cognitive_orchestrator = None
        self.memory_service = None
        self.profile_service = None
        self.milestone_service = None
        self.lore_service = None
        self.interest_service = None
        self.distill_service = None
        self.channel_situation_service = None
        self.milestone_repository = None
        self.lore_repository = None
        self.interest_repository = None
        self.profile_buffer_repository = None
        self.proactive_repository = None
        self.greeting_service = None
        self.outreach_service = None
        self.sleep_service = None
        self.media_service = None
        self.presence_service = None
        self.proactive_executor = None
        self.mention_map_service = None
        self.proactive_message_service = None
        self.proactive_coordinator = None
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
        self.cognition_debug_service = None
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
        self.memory_repository = MemoryRepository(self.sqlite_service)
        self.profile_repository = ProfileRepository(self.sqlite_service)
        self.milestone_repository = MilestoneRepository(self.sqlite_service)
        self.lore_repository = LoreRepository(self.sqlite_service)
        self.interest_repository = InterestRepository(self.sqlite_service)
        self.profile_buffer_repository = ProfileBufferRepository(self.sqlite_service)
        self.task_repository = TaskRepository(self.sqlite_service)
        self.trace_repository = TraceRepository(self.sqlite_service)
        self.trace_service = TraceService(trace_repository=self.trace_repository)
        self.llm_debug_service = LlmDebugService(limit=10, plugin_loader=self.plugin_loader)
        self.cognition_debug_service = CognitionDebugService()
        self.retention_service = RetentionService(sqlite_service=self.sqlite_service, trace_repository=self.trace_repository)
        self.proactive_repository = ProactiveRepository(self.sqlite_service)
        self.presence_repository = PresenceRepository(self.sqlite_service)
        self.media_repository = MediaRepository(self.sqlite_service)
        self.voice_session_repository = VoiceSessionRepository(self.sqlite_service)
        self.settings_store = self.channel_repository.load_settings_store()

    def build_bridges(self) -> None:
        self.event_bridge = SapphireEventBridge(self.plugin_loader, llm_debug_service=self.llm_debug_service)
        self.llm_bridge = SapphireLlmBridge(self.plugin_loader)
        self.scheduler_bridge = SapphireSchedulerBridge(self.plugin_loader)
        self.settings_bridge = SapphireSettingsBridge(self.plugin_loader, self.plugin_name)
        self.speech_bridge = SapphireSpeechBridge(self.plugin_loader)

    def build_cognition(self) -> None:
        self.world_model_service = WorldModelService(
            channel_repository=self.channel_repository,
            message_repository=self.message_repository,
            task_repository=self.task_repository,
            trace_repository=self.trace_repository,
        )
        self.commitment_service = CommitmentService(
            world_model_service=self.world_model_service,
            trace_repository=self.trace_repository,
        )
        self.cognitive_orchestrator = CognitiveOrchestrator(
            world_model_service=self.world_model_service,
            greeting_service=None,
            outreach_service=None,
            sleep_service=None,
            trace_service=self.trace_service,
        )
        self.memory_service = MemoryService(
            memory_repository=self.memory_repository,
            message_repository=self.message_repository,
        )
        self.milestone_service = MilestoneService(milestone_repository=self.milestone_repository)
        self.lore_service = LoreService(lore_repository=self.lore_repository)
        self.interest_service = InterestService(interest_repository=self.interest_repository)
        self.channel_situation_service = ChannelSituationService(
            message_repository=self.message_repository,
            interest_service=self.interest_service,
            trace_repository=self.trace_repository,
            cognition_debug_service=self.cognition_debug_service,
        )
        self.cognitive_orchestrator.channel_situation_service = self.channel_situation_service
        self.profile_service = ProfileService(
            profile_repository=self.profile_repository,
            milestone_service=self.milestone_service,
            interest_service=self.interest_service,
            lore_service=self.lore_service,
        )
        self.distill_service = DistillService(
            buffer_repository=self.profile_buffer_repository,
            profile_repository=self.profile_repository,
            sqlite_service=self.sqlite_service,
            trace_repository=self.trace_repository,
        )
        self.birthday_service = BirthdayService(
            profile_repository=self.profile_repository,
            trace_repository=self.trace_repository,
        )

    def build_proactive(self) -> None:
        self.sleep_service = SleepService(
            proactive_repository=self.proactive_repository,
            trace_repository=self.trace_repository,
        )
        self.greeting_service = GreetingService(
            proactive_repository=self.proactive_repository,
            trace_repository=self.trace_repository,
            sleep_service=self.sleep_service,
        )
        self.outreach_service = OutreachService(
            proactive_repository=self.proactive_repository,
            trace_repository=self.trace_repository,
            message_repository=self.message_repository,
            interest_service=self.interest_service,
            channel_situation_service=self.channel_situation_service,
            profile_service=self.profile_service,
            cognition_debug_service=self.cognition_debug_service,
        )
        self.media_service = MediaService(
            media_repository=self.media_repository,
            llm_bridge=self.llm_bridge,
            trace_repository=self.trace_repository,
            scheduler_bridge=self.scheduler_bridge,
        )
        self.presence_service = DiscordPresenceService()
        self.proactive_message_service = ProactiveMessageService(
            message_repository=self.message_repository,
            channel_repository=self.channel_repository,
            transport=self.transport,
            account_repository=self.account_repository,
            trace_repository=self.trace_repository,
        )
        self.proactive_executor = ProactiveExecutor(
            transport=self.transport,
            greeting_service=self.greeting_service,
            outreach_service=self.outreach_service,
            sleep_service=self.sleep_service,
            presence_service=self.presence_service,
            presence_repository=self.presence_repository,
            world_model_service=self.world_model_service,
            gif_service=self.gif_service,
            settings_store=self.settings_store,
            trace_repository=self.trace_repository,
            event_bridge=self.event_bridge,
            proactive_message_service=self.proactive_message_service,
            channel_repository=self.channel_repository,
            mention_map_service=self.mention_map_service,
            birthday_service=self.birthday_service,
        )
        if self.cognitive_orchestrator:
            self.cognitive_orchestrator.greeting_service = self.greeting_service
            self.cognitive_orchestrator.outreach_service = self.outreach_service
            self.cognitive_orchestrator.sleep_service = self.sleep_service
            self.cognitive_orchestrator.birthday_service = self.birthday_service
        self.proactive_coordinator = ProactiveCoordinator(
            settings_store=self.settings_store,
            greeting_service=self.greeting_service,
            outreach_service=self.outreach_service,
            sleep_service=self.sleep_service,
            presence_service=self.presence_service,
            profile_service=self.profile_service,
            proactive_executor=self.proactive_executor,
            policy_service=self.policy_service,
            cognitive_orchestrator=self.cognitive_orchestrator,
            transport=self.transport,
            trace_repository=self.trace_repository,
        )
        self.scheduler.set_tick_handler(self._scheduler_tick)

    def build_voice(self) -> None:
        self.voice_transport = VoiceTransport(discord_transport=self.transport)
        self.voice_turn_taking_service = VoiceTurnTakingService()
        self.voice_session_service = VoiceSessionService(
            voice_session_repository=self.voice_session_repository,
            world_model_service=self.world_model_service,
            trace_repository=self.trace_repository,
        )
        self.voice_perception_service = VoicePerceptionService(
            voice_session_repository=self.voice_session_repository,
            speech_bridge=self.speech_bridge,
            world_model_service=self.world_model_service,
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
            world_model_service=self.world_model_service,
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
            sleep_service=self.sleep_service,
        )
        from plugins.discord.voice.voice_deps import voice_receive_error

        hint = voice_receive_error()
        if hint:
            logger.warning("Discord voice receive unavailable:\n%s", hint)

    async def _scheduler_tick(self):
        if not self.transport:
            return
        await self._reconcile_accounts()
        for account_name in self.transport.list_connected():
            if self.proactive_coordinator:
                await self.proactive_coordinator.tick_async(account_name)
            if self.voice_auto_join_service:
                try:
                    await self.voice_auto_join_service.tick_async(account_name)
                except Exception:
                    logger.exception("Voice auto-join tick failed for %s", account_name)
        await self._reap_voice_chats()

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
        selected = self.scheduler_bridge.active_daemon_accounts('discord_message')
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

    def build_transport(self) -> None:
        self.build_cognition()
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
        self.build_proactive()
        self.event_adapter = DiscordEventAdapter(
            message_repository=self.message_repository,
            trace_repository=self.trace_repository,
            world_model_service=self.world_model_service,
            media_service=self.media_service,
            sleep_service=self.sleep_service,
            proactive_repository=self.proactive_repository,
            settings_store=self.settings_store,
            commitment_service=self.commitment_service,
            birthday_service=self.birthday_service,
            mention_map_service=self.mention_map_service,
            distill_service=self.distill_service,
            channel_situation_service=self.channel_situation_service,
            cognition_debug_service=self.cognition_debug_service,
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
            memory_service=self.memory_service,
            profile_service=self.profile_service,
            media_service=self.media_service,
            trace_service=self.trace_service,
            edit_history_service=self.edit_history_service,
            channel_situation_service=self.channel_situation_service,
            settings_store=self.settings_store,
        )
        self.conversation_service = ConversationService(
            event_bridge=self.event_bridge,
            policy_service=self.policy_service,
            prompt_context_service=self.prompt_context_service,
            trace_repository=self.trace_repository,
            reply_style_service=self.reply_style_service,
            delivery_style_service=self.delivery_style_service,
            edit_history_service=self.edit_history_service,
            transport=self.transport,
            profile_service=self.profile_service,
            gif_service=self.gif_service,
            reaction_service=self.reaction_service,
            settings_store=self.settings_store,
            trace_service=self.trace_service,
            cognitive_orchestrator=self.cognitive_orchestrator,
            account_repository=self.account_repository,
            sleep_service=self.sleep_service,
            bot_session_service=self.bot_session_service,
            mention_map_service=self.mention_map_service,
            llm_debug_service=self.llm_debug_service,
            channel_situation_service=self.channel_situation_service,
            world_model_service=self.world_model_service,
            cognition_debug_service=self.cognition_debug_service,
        )
        self.message_pipeline = MessagePipelineService(
            batching_service=self.batching_service,
            conversation_service=self.conversation_service,
            trace_repository=self.trace_repository,
        )
        self.command_service = DiscordCommandService(
            conversation_service=self.conversation_service,
            profile_service=self.profile_service,
            memory_service=self.memory_service,
        )
        self.transport.set_event_adapter(self.event_adapter)
        self.transport.set_command_service(self.command_service)
        self.transport.set_message_pipeline(self.message_pipeline)
        self.transport.set_on_account_connected(self._on_account_connected)
        self.build_voice()

    async def _on_account_connected(self, account_name: str) -> None:
        if self.proactive_coordinator:
            try:
                await self.proactive_coordinator.apply_presence_now_async(account_name, force=True)
            except Exception:
                logger.exception("Initial presence apply failed for %s", account_name)
