"""Safety gates on the reply path: DMs are opt-in (M19) and budgeted per person per day."""
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


class FakeDebug:
    def __init__(self):
        self.rejections = []

    def record_rejection(self, **kw):
        self.rejections.append((kw['stage'], kw['reason']))


def _dm_obs(message_id='m1', author_id='u1'):
    return TextMessageObservation(
        observation_id=f'obs:{message_id}', account_name='alpha', guild_id='', guild_name='', channel_id='dm1',
        channel_name='DM', author_id=author_id, username='alice', display_name='Alice', message_id=message_id,
        content='hey there', clean_content='hey there', created_at=0.0, is_dm=True, mentioned=False, attachments=[],
    )


def _service(store):
    return ConversationService(event_bridge=FakeBridge(), settings_store=store, llm_debug_service=FakeDebug())


def _run_dm(service, obs=None):
    batching = BatchingService(default_window_seconds=5, typing_extension_seconds=4)
    batching.add_message(obs or _dm_obs())
    return service.process_batch(batching.flush_ready(now=10.0)[0])


def test_dm_gate_blocks_by_default():
    # Anyone sharing a server can DM the bot — an outside line until the operator opens it.
    service = _service(SettingsStore())
    assert _run_dm(service) is False
    assert service.event_bridge.payloads == []
    assert service.llm_debug_service.rejections == [('safety', 'direct_messages_disabled')]


def test_dm_replies_when_enabled():
    service = _service(SettingsStore({'safety': {'allow_direct_messages': True}}))
    assert _run_dm(service) is True
    assert len(service.event_bridge.payloads) == 1


def test_dm_budget_counts_per_person_per_day():
    service = _service(SettingsStore({'safety': {'allow_direct_messages': True, 'dm_daily_budget': 2, 'rate_limit_seconds': 0}}))
    assert _run_dm(service, _dm_obs('m1')) is True
    assert _run_dm(service, _dm_obs('m2')) is True
    assert _run_dm(service, _dm_obs('m3')) is False                       # the third of the day is refused
    assert service.llm_debug_service.rejections[-1] == ('safety', 'dm_daily_budget')
    assert _run_dm(service, _dm_obs('m4', author_id='u2')) is True         # another person has their own budget
