from plugins.discord.cognition.cognitive_orchestrator import CognitiveOrchestrator
from plugins.discord.cognition.world_model_service import WorldModelService
from plugins.discord.models.settings import CognitiveSettings, EffectiveSettings
from plugins.discord.storage.repositories.channels import ChannelRepository
from plugins.discord.storage.repositories.messages import MessageRepository
from plugins.discord.storage.repositories.tasks import TaskRepository
from plugins.discord.storage.sqlite import SQLiteService


def test_task_follow_up_generates_intention(tmp_path):
    sqlite = SQLiteService(tmp_path / 'task.sqlite3')
    sqlite.start()
    world = WorldModelService(
        channel_repository=ChannelRepository(sqlite),
        message_repository=MessageRepository(sqlite),
        task_repository=TaskRepository(sqlite),
    )
    task_id = world.create_task('alpha', 'voice_follow_up', target_id='c1', reason='session ended')
    orchestrator = CognitiveOrchestrator(world_model_service=world)
    settings = EffectiveSettings(cognitive=CognitiveSettings(task_follow_up_enabled=True))

    intentions = orchestrator.evaluate_task_intentions('alpha', settings)

    assert len(intentions) == 1
    assert intentions[0].metadata['task_id'] == task_id
