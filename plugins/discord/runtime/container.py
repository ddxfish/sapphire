"""Dependency injection root for the Discord cognitive plugin."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

from plugins.discord.cognition.attention_service import AttentionService
from plugins.discord.cognition.cognitive_orchestrator import CognitiveOrchestrator
from plugins.discord.cognition.commitment_service import CommitmentService
from plugins.discord.cognition.goal_engine import GoalEngine
from plugins.discord.cognition.intent_engine import IntentEngine
from plugins.discord.cognition.observation_interpreter import ObservationInterpreter
from plugins.discord.cognition.policy_service import PolicyService
from plugins.discord.cognition.world_model_service import WorldModelService
from plugins.discord.cognition.world_state_builder import WorldStateBuilder
from plugins.discord.conversation.batching_service import BatchingService
from plugins.discord.conversation.bot_session_service import BotSessionService
from plugins.discord.conversation.conversation_service import ConversationService
from plugins.discord.conversation.mention_map_service import MentionMapService
from plugins.discord.conversation.message_pipeline_service import MessagePipelineService
from plugins.discord.conversation.gif_service import GifService
from plugins.discord.conversation.media_service import MediaService
from plugins.discord.conversation.meme_service import MemeService
from plugins.discord.conversation.prompt_context_service import PromptContextService
from plugins.discord.conversation.delivery_style_service import DeliveryStyleService
from plugins.discord.conversation.edit_history_service import EditHistoryService
from plugins.discord.conversation.reaction_service import ReactionService
from plugins.discord.conversation.reply_style_service import ReplyStyleService
from plugins.discord.memory.birthday_service import BirthdayService
from plugins.discord.memory.memory_service import MemoryService
from plugins.discord.memory.profile_distill_service import ProfileDistillService
from plugins.discord.memory.profile_service import ProfileService
from plugins.discord.proactive.greeting_service import GreetingService
from plugins.discord.proactive.outreach_service import OutreachService
from plugins.discord.proactive.proactive_message_service import ProactiveMessageService
from plugins.discord.proactive.proactive_coordinator import ProactiveCoordinator
from plugins.discord.proactive.proactive_executor import ProactiveExecutor
from plugins.discord.proactive.sleep_service import SleepService
from plugins.discord.models.settings import SettingsStore
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
from plugins.discord.storage.repositories.media import MediaRepository
from plugins.discord.storage.repositories.memory import MemoryRepository
from plugins.discord.storage.repositories.messages import MessageRepository
from plugins.discord.storage.repositories.presence import PresenceRepository
from plugins.discord.storage.repositories.proactive import ProactiveRepository
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
        self.settings_bridge = None
        self.speech_bridge = None
        self.observation_interpreter = None
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
        self.attention_service = None
        self.goal_engine = None
        self.intent_engine = None
        self.world_state_builder = None
        self.cognitive_orchestrator = None
        self.memory_service = None
        self.profile_service = None
        self.profile_distill_service = None
        self.proactive_repository = None
        self.greeting_service = None
        self.outreach_service = None
        self.sleep_service = None
        self.media_service = None
        self.meme_service = None
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
        self.retention_service = None

    async def start(self) -> None:
        await self.lifecycle.start(self)

    async def stop(self) -> None:
        await self.lifecycle.stop(self)

    def build_settings_store(self) -> None:
        self.settings_store = SettingsStore.from_dict(self.settings.get("settings_overrides") or {})

    def build_repositories(self) -> None:
        self.account_repository = AccountRepository(self.sqlite_service)
        self.channel_repository = ChannelRepository(self.sqlite_service)
        self.message_repository = MessageRepository(self.sqlite_service)
        self.memory_repository = MemoryRepository(self.sqlite_service)
        self.profile_repository = ProfileRepository(self.sqlite_service)
        self.task_repository = TaskRepository(self.sqlite_service)
        self.trace_repository = TraceRepository(self.sqlite_service)
        self.trace_service = TraceService(trace_repository=self.trace_repository)
        self.retention_service = RetentionService(sqlite_service=self.sqlite_service, trace_repository=self.trace_repository)
        self.proactive_repository = ProactiveRepository(self.sqlite_service)
        self.presence_repository = PresenceRepository(self.sqlite_service)
        self.media_repository = MediaRepository(self.sqlite_service)
        self.voice_session_repository = VoiceSessionRepository(self.sqlite_service)
        stored = self.channel_repository.load_settings_store()
        self.settings_store = stored.merge_store(self.settings_store or SettingsStore())

    def build_bridges(self) -> None:
        self.event_bridge = SapphireEventBridge(self.plugin_loader)
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
        self.attention_service = AttentionService(profile_repository=self.profile_repository)
        self.goal_engine = GoalEngine()
        self.intent_engine = IntentEngine(goal_engine=self.goal_engine)
        self.world_state_builder = WorldStateBuilder(
            attention_service=self.attention_service,
            profile_service=self.profile_service,
            world_model_service=self.world_model_service,
        )
        self.cognitive_orchestrator = CognitiveOrchestrator(
            intent_engine=self.intent_engine,
            world_state_builder=self.world_state_builder,
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
        self.profile_service = ProfileService(profile_repository=self.profile_repository)
        self.birthday_service = BirthdayService(
            profile_repository=self.profile_repository,
            trace_repository=self.trace_repository,
        )
        self.profile_distill_service = ProfileDistillService(
            profile_repository=self.profile_repository,
            profile_service=self.profile_service,
            llm_bridge=self.llm_bridge,
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
        )
        self.media_service = MediaService(
            media_repository=self.media_repository,
            llm_bridge=self.llm_bridge,
            trace_repository=self.trace_repository,
        )
        self.meme_service = MemeService()
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
        for account_name in self.transport.list_connected():
            if self.proactive_coordinator:
                await self.proactive_coordinator.tick_async(account_name)
            if self.voice_auto_join_service:
                try:
                    await self.voice_auto_join_service.tick_async(account_name)
                except Exception:
                    logger.exception("Voice auto-join tick failed for %s", account_name)

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
        self.observation_interpreter = ObservationInterpreter()
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
        )
        self.batching_service = BatchingService()
        self.prompt_context_service = PromptContextService(
            message_repository=self.message_repository,
            observation_interpreter=self.observation_interpreter,
            memory_service=self.memory_service,
            profile_service=self.profile_service,
            attention_service=self.attention_service,
            media_service=self.media_service,
            trace_service=self.trace_service,
            edit_history_service=self.edit_history_service,
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
            profile_distill_service=self.profile_distill_service,
            attention_service=self.attention_service,
            gif_service=self.gif_service,
            reaction_service=self.reaction_service,
            settings_store=self.settings_store,
            trace_service=self.trace_service,
            cognitive_orchestrator=self.cognitive_orchestrator,
            world_state_builder=self.world_state_builder,
            account_repository=self.account_repository,
            sleep_service=self.sleep_service,
            bot_session_service=self.bot_session_service,
            mention_map_service=self.mention_map_service,
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
