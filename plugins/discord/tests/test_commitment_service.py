from datetime import datetime

from plugins.discord.cognition.commitment_service import CommitmentService
from plugins.discord.cognition.world_model_service import WorldModelService
from plugins.discord.models.observations import TextMessageObservation
from plugins.discord.models.settings import CognitiveSettings, EffectiveSettings
from plugins.discord.storage.repositories.channels import ChannelRepository
from plugins.discord.storage.repositories.messages import MessageRepository
from plugins.discord.storage.repositories.tasks import TaskRepository
from plugins.discord.storage.sqlite import SQLiteService


def _obs(content: str, **kwargs):
    base = dict(
        observation_id='obs-1',
        account_name='alpha',
        guild_id='g1',
        guild_name='Guild',
        channel_id='c1',
        channel_name='general',
        author_id='u99',
        username='bob',
        display_name='Bob',
        message_id='m1',
        content=content,
        clean_content=content,
        created_at=0.0,
        is_dm=False,
        mentioned=False,
        attachments=[],
    )
    base.update(kwargs)
    return TextMessageObservation(**base)


def _stack(tmp_path):
    sqlite = SQLiteService(tmp_path / 'commit.sqlite3')
    sqlite.start()
    world = WorldModelService(
        channel_repository=ChannelRepository(sqlite),
        message_repository=MessageRepository(sqlite),
        task_repository=TaskRepository(sqlite),
    )
    service = CommitmentService(world_model_service=world)
    settings = EffectiveSettings(cognitive=CognitiveSettings(commitment_followups_enabled=True))
    return world, service, settings


def test_commitment_service_no_longer_schedules_birthday_tasks(tmp_path):
    world, service, settings = _stack(tmp_path)

    created, hints = service.scan_and_schedule(_obs("my birthday is tomorrow"), settings)

    assert created == []
    assert world.list_tasks('alpha', status='pending') == []


def test_next_week_commitment_schedules_task(tmp_path):
    world, service, settings = _stack(tmp_path)

    created, hints = service.scan_and_schedule(
        _obs("next week I'll be pushing to the dev build"),
        settings,
    )

    assert len(created) == 1
    tasks = world.list_tasks('alpha', status='pending')
    assert tasks[0]['task_type'] == 'commitment_follow_up'
    assert tasks[0]['reason'] == 'future_commitment'
    assert float(tasks[0]['run_at']) > 0


def test_in_three_days_commitment_schedules_task(tmp_path):
    world, service, settings = _stack(tmp_path)

    created, hints = service.scan_and_schedule(
        _obs('in 3 days I will ship the hotfix to production'),
        settings,
    )

    assert len(created) == 1
    tasks = world.list_tasks('alpha', status='pending')
    assert tasks[0]['task_type'] == 'commitment_follow_up'


def test_casual_message_not_scheduled(tmp_path):
    world, service, settings = _stack(tmp_path)

    created, hints = service.scan_and_schedule(_obs('lol nice one'), settings)

    assert created == []


def test_future_task_not_due_yet(tmp_path):
    world, service, settings = _stack(tmp_path)
    service.scan_and_schedule(_obs("next week I'll be pushing to the dev build"), settings)

    due_now = world.list_due_tasks('alpha', now_ts=datetime.now().timestamp())

    assert due_now == []


def test_duplicate_commitment_not_scheduled_twice(tmp_path):
    world, service, settings = _stack(tmp_path)
    obs = _obs("next week I'll be pushing to the dev build")

    first, _ = service.scan_and_schedule(obs, settings)
    second, _ = service.scan_and_schedule(obs, settings)

    assert len(first) == 1
    assert second == []


def test_reminder_in_five_minutes_schedules_task(tmp_path):
    world, service, settings = _stack(tmp_path)

    created, hints = service.scan_and_schedule(
        _obs('can you remind me in 5minutes to make a coffee'),
        settings,
    )

    assert len(created) == 1
    assert len(hints) == 1
    assert 'scheduled a reminder' in hints[0].lower()
    tasks = world.list_tasks('alpha', status='pending')
    assert tasks[0]['task_type'] == 'reminder_follow_up'
    assert tasks[0]['reason'] == 'user_reminder'
    payload = tasks[0].get('payload_json')
    if isinstance(payload, str):
        import json
        payload = json.loads(payload)
    assert payload['reminder'] == 'make a coffee'


def test_reminder_works_in_dm(tmp_path):
    world, service, settings = _stack(tmp_path)

    created, hints = service.scan_and_schedule(
        _obs('remind me in 10 minutes to stretch', is_dm=True),
        settings,
    )

    assert len(created) == 1
    assert world.list_tasks('alpha', status='pending')[0]['task_type'] == 'reminder_follow_up'


def test_reminder_due_after_run_at(tmp_path):
    world, service, settings = _stack(tmp_path)
    service.scan_and_schedule(_obs('remind me in 5 minutes to make a coffee'), settings)
    tasks = world.list_tasks('alpha', status='pending')
    run_at = float(tasks[0]['run_at'])

    due_before = world.list_due_tasks('alpha', now_ts=run_at - 1)
    due_after = world.list_due_tasks('alpha', now_ts=run_at + 1)

    assert due_before == []
    assert len(due_after) == 1
