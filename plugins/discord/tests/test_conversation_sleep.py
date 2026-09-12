from plugins.discord.conversation.batching_service import BatchingService
from plugins.discord.conversation.conversation_service import ConversationService
from plugins.discord.models.observations import TextMessageObservation
from plugins.discord.models.settings import SettingsStore
from plugins.discord.proactive.sleep_service import SleepService
from plugins.discord.storage.repositories.proactive import ProactiveRepository
from plugins.discord.storage.sqlite import SQLiteService


class FakeBridge:
    def __init__(self):
        self.payloads = []

    def emit_discord_message(self, payload):
        self.payloads.append(payload)
        return True


class FakePolicy:
    def evaluate_text_observation(self, observation, resolved_settings=None):
        return {'allowed': True, 'reason': 'ok'}


class FakeContext:
    def build(self, batch):
        return {'recent_history': [], 'channel_summary': batch.channel_name}


class FakeTraceRepo:
    def record_trace(self, trace_type, summary, detail=None):
        pass


def _sleep_service(tmp_path):
    sqlite = SQLiteService(tmp_path / 'conv-sleep.sqlite3')
    sqlite.start()
    return SleepService(proactive_repository=ProactiveRepository(sqlite))


def _mentioned_obs(message_id='m1'):
    return TextMessageObservation(
        observation_id=f'obs:{message_id}',
        account_name='alpha',
        guild_id='g1',
        guild_name='Guild',
        channel_id='c1',
        channel_name='general',
        author_id='u1',
        username='alice',
        display_name='Alice',
        message_id=message_id,
        content='wake up',
        clean_content='wake up',
        created_at=0.0,
        is_dm=False,
        mentioned=True,
        attachments=[],
    )


def test_conversation_blocks_first_sleep_mention(tmp_path):
    store = SettingsStore()
    store.global_overlay.proactive.update({
        'sleep_schedule_enabled': True,
        'forced_wake_mention_threshold': 2,
        'greeting_targets': ['alpha:c1'],
    })
    sleep = _sleep_service(tmp_path)
    sleep.set_asleep('alpha', 'c1')
    bridge = FakeBridge()
    service = ConversationService(
        event_bridge=bridge,
        policy_service=FakePolicy(),
        prompt_context_service=FakeContext(),
        trace_repository=FakeTraceRepo(),
        settings_store=store,
        sleep_service=sleep,
    )
    batch_service = BatchingService(default_window_seconds=5, typing_extension_seconds=4)
    batch_service.add_message(_mentioned_obs('m1'))
    batch = batch_service.flush_ready(now=10.0)[0]

    accepted = service.process_batch(batch)

    assert accepted is False
    assert bridge.payloads == []


def test_conversation_replies_after_forced_wake_threshold(tmp_path):
    store = SettingsStore()
    store.global_overlay.proactive.update({
        'sleep_schedule_enabled': True,
        'forced_wake_mention_threshold': 2,
        'greeting_targets': ['alpha:c1'],
    })
    sleep = _sleep_service(tmp_path)
    sleep.set_asleep('alpha', 'c1')
    bridge = FakeBridge()
    service = ConversationService(
        event_bridge=bridge,
        policy_service=FakePolicy(),
        prompt_context_service=FakeContext(),
        trace_repository=FakeTraceRepo(),
        settings_store=store,
        sleep_service=sleep,
    )
    batch_service = BatchingService(default_window_seconds=5, typing_extension_seconds=4)

    batch_service.add_message(_mentioned_obs('m1'))
    service.process_batch(batch_service.flush_ready(now=10.0)[0])

    batch_service.add_message(_mentioned_obs('m2'))
    accepted = service.process_batch(batch_service.flush_ready(now=20.0)[0])

    assert accepted is True
    assert len(bridge.payloads) == 1
    assert 'woke you up' in bridge.payloads[0]['reply_instructions']
