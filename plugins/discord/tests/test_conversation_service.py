from plugins.discord.conversation.batching_service import BatchingService
from plugins.discord.conversation.conversation_service import ConversationService
from plugins.discord.models.observations import TextMessageObservation
from plugins.discord.models.settings import SettingsStore


class FakeBridge:
    def __init__(self, accepted=True):
        self.accepted = accepted
        self.payloads = []

    def emit_discord_message(self, payload):
        self.payloads.append(payload)
        return self.accepted


class FakePolicy:
    def evaluate_text_observation(self, observation, resolved_settings=None):
        return {'allowed': True, 'reason': 'ok'}


class FakeContext:
    def __init__(self, context=None):
        self.context = context or {'recent_history': ['hi']}

    def build(self, batch):
        return self.context


class FakeTraceRepo:
    def __init__(self):
        self.traces = []

    def record_trace(self, trace_type, summary, detail=None):
        self.traces.append((trace_type, summary, detail or {}))


def make_obs():
    return TextMessageObservation(
        observation_id='obs-1',
        account_name='alpha',
        guild_id='g1',
        guild_name='Guild',
        channel_id='c1',
        channel_name='general',
        author_id='u1',
        username='alice',
        display_name='Alice',
        message_id='m1',
        content='hello',
        clean_content='hello',
        created_at=0.0,
        is_dm=False,
        mentioned=True,
        attachments=[],
    )


def test_emit_reply_intention_for_batch():
    batch_service = BatchingService(default_window_seconds=5, typing_extension_seconds=4)
    batch_service.add_message(make_obs())
    batch = batch_service.flush_ready(now=10.0)[0]
    bridge = FakeBridge()
    from plugins.discord.models.settings import SettingsStore

    store = SettingsStore()
    service = ConversationService(
        event_bridge=bridge,
        policy_service=FakePolicy(),
        prompt_context_service=FakeContext(),
        trace_repository=FakeTraceRepo(),
        settings_store=store,
    )

    emitted = service.process_batch(batch)

    assert emitted is True
    assert bridge.payloads[0]['message_id'] == 'm1'
    assert bridge.payloads[0]['batch_size'] == 1
    assert service.pending_reply('m1')['channel_id'] == 'c1'


def test_rejected_event_does_not_create_pending_metadata():
    batch_service = BatchingService(default_window_seconds=5, typing_extension_seconds=4)
    batch_service.add_message(make_obs())
    batch = batch_service.flush_ready(now=10.0)[0]
    bridge = FakeBridge(accepted=False)
    traces = FakeTraceRepo()
    service = ConversationService(
        event_bridge=bridge,
        policy_service=FakePolicy(),
        prompt_context_service=FakeContext(),
        trace_repository=traces,
        settings_store=SettingsStore(),
    )

    emitted = service.process_batch(batch)

    assert emitted is False
    assert service.pending_reply('m1') is None
    assert traces.traces[-1][0] == 'event_dropped'
