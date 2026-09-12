from plugins.discord.cognition.cognitive_orchestrator import CognitiveOrchestrator
from plugins.discord.cognition.goal_engine import GoalEngine
from plugins.discord.cognition.intent_engine import IntentEngine
from plugins.discord.cognition.world_state_builder import WorldStateBuilder
from plugins.discord.conversation.batching_service import BatchingService
from plugins.discord.models.observations import TextMessageObservation
from plugins.discord.models.settings import CognitiveSettings, EffectiveSettings, SettingsStore
from plugins.discord.storage.repositories.tasks import TaskRepository
from plugins.discord.storage.sqlite import SQLiteService
from plugins.discord.cognition.world_model_service import WorldModelService
from plugins.discord.storage.repositories.channels import ChannelRepository
from plugins.discord.storage.repositories.messages import MessageRepository


def _obs(mentioned=True):
    return TextMessageObservation(
        observation_id='obs-1', account_name='alpha', guild_id='g1', guild_name='Guild',
        channel_id='c1', channel_name='general', author_id='u1', username='alice',
        display_name='Alice', message_id='m1', content='hello', clean_content='hello',
        created_at=0.0, is_dm=False, mentioned=mentioned, attachments=[],
    )


def test_mentioned_message_generates_intention_via_orchestrator():
    orchestrator = CognitiveOrchestrator(
        intent_engine=IntentEngine(goal_engine=GoalEngine()),
        world_state_builder=WorldStateBuilder(),
    )
    batch_service = BatchingService(default_window_seconds=5, typing_extension_seconds=4)
    batch_service.add_message(_obs(mentioned=True))
    batch = batch_service.flush_ready(now=10.0)[0]
    settings = EffectiveSettings(cognitive=CognitiveSettings(enabled=True, mode='integrated'))

    intentions = orchestrator.evaluate_message_batch(batch, settings)

    assert len(intentions) == 1
    assert intentions[0].reason == 'mentioned'


def test_low_activation_suppressed_in_conservative_mode():
    orchestrator = CognitiveOrchestrator(
        intent_engine=IntentEngine(goal_engine=GoalEngine()),
        world_state_builder=WorldStateBuilder(),
    )
    batch_service = BatchingService(default_window_seconds=5, typing_extension_seconds=4)
    batch_service.add_message(_obs(mentioned=False))
    batch = batch_service.flush_ready(now=10.0)[0]
    settings = EffectiveSettings(cognitive=CognitiveSettings(enabled=True, mode='conservative'))

    intentions = orchestrator.evaluate_message_batch(batch, settings)

    assert intentions == []


def test_task_follow_up_generates_intention(tmp_path):
    sqlite = SQLiteService(tmp_path / 'task.sqlite3')
    sqlite.start()
    world = WorldModelService(
        channel_repository=ChannelRepository(sqlite),
        message_repository=MessageRepository(sqlite),
        task_repository=TaskRepository(sqlite),
    )
    task_id = world.create_task('alpha', 'voice_follow_up', target_id='c1', reason='session ended')
    orchestrator = CognitiveOrchestrator(
        intent_engine=IntentEngine(goal_engine=GoalEngine()),
        world_state_builder=WorldStateBuilder(world_model_service=world),
        world_model_service=world,
    )
    settings = EffectiveSettings(cognitive=CognitiveSettings(task_follow_up_enabled=True))

    intentions = orchestrator.evaluate_task_intentions('alpha', settings)

    assert len(intentions) == 1
    assert intentions[0].metadata['task_id'] == task_id
