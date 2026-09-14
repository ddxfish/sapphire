from plugins.discord.conversation.batching_service import BatchingService
from plugins.discord.conversation.conversation_service import ConversationService
from plugins.discord.models.observations import TextMessageObservation
from plugins.discord.models.settings import SettingsStore


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
    def __init__(self):
        self.traces = []

    def record_trace(self, trace_type, summary, detail=None):
        self.traces.append((trace_type, summary))


def _dm_obs(message_id='m1'):
    return TextMessageObservation(
        observation_id=f'obs:{message_id}',
        account_name='alpha',
        guild_id='',
        guild_name='',
        channel_id='dm1',
        channel_name='DM',
        author_id='u1',
        username='alice',
        display_name='Alice',
        message_id=message_id,
        content='hey there',
        clean_content='hey there',
        created_at=0.0,
        is_dm=True,
        mentioned=False,
        attachments=[],
    )


def _service(store):
    return ConversationService(
        event_bridge=FakeBridge(),
        policy_service=FakePolicy(),
        prompt_context_service=FakeContext(),
        trace_repository=FakeTraceRepo(),
        settings_store=store,
    )


def _run_dm(service):
    batching = BatchingService(default_window_seconds=5, typing_extension_seconds=4)
    batching.add_message(_dm_obs())
    return service.process_batch(batching.flush_ready(now=10.0)[0])


def test_dm_gate_blocks_by_default():
    # M19 (hunt 2026-09-12): anyone sharing a server can DM the bot — an
    # outside line until the operator opens it.
    service = _service(SettingsStore())
    assert _run_dm(service) is False
    assert service.event_bridge.payloads == []


def test_dm_replies_when_enabled():
    store = SettingsStore()
    store.global_overlay.safety.update({'allow_direct_messages': True})
    service = _service(store)
    assert _run_dm(service) is True
    assert len(service.event_bridge.payloads) == 1


def test_dm_gate_blocks_when_disabled():
    store = SettingsStore()
    store.global_overlay.safety.update({'allow_direct_messages': False})
    service = _service(store)
    assert _run_dm(service) is False
    assert service.event_bridge.payloads == []
    assert any(s == 'Direct messages disabled' for _, s in service.trace_repository.traces)
