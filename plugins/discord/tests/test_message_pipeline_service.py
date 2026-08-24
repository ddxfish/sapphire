import asyncio
import time

from plugins.discord.conversation.batching_service import BatchingService
from plugins.discord.conversation.conversation_service import ConversationService
from plugins.discord.conversation.message_pipeline_service import MessagePipelineService
from plugins.discord.models.observations import TextMessageObservation, TypingObservation


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
        return {'recent_history': ['hi'], 'channel_summary': batch.channel_name}


class FakeTraceRepo:
    def __init__(self):
        self.traces = []

    def record_trace(self, trace_type, summary, detail=None):
        self.traces.append((trace_type, summary, detail or {}))


def make_message(message_id='m1', created_at=None):
    return TextMessageObservation(
      observation_id=f'obs-{message_id}',
      account_name='alpha',
      guild_id='g1',
      guild_name='Guild',
      channel_id='c1',
      channel_name='general',
      author_id='u1',
      username='alice',
      display_name='Alice',
      message_id=message_id,
      content='hello',
      clean_content='hello',
      created_at=created_at if created_at is not None else time.time(),
      is_dm=False,
      mentioned=True,
      attachments=[],
    )


def test_pipeline_flushes_batch_into_conversation_service():
    batching = BatchingService(default_window_seconds=0.1, typing_extension_seconds=0.1)
    bridge = FakeBridge()
    traces = FakeTraceRepo()
    conversation = ConversationService(
        event_bridge=bridge,
        policy_service=FakePolicy(),
        prompt_context_service=FakeContext(),
        trace_repository=traces,
    )
    pipeline = MessagePipelineService(
        batching_service=batching,
        conversation_service=conversation,
        trace_repository=traces,
    )
    observation = make_message()
    pipeline.handle_message(observation)
    results = pipeline.flush_due(observation.created_at + 1.0)
    assert len(results) == 1
    assert results[0]['accepted'] is True
    assert len(bridge.payloads) == 1
    assert any(trace[0] == 'batch_queued' for trace in traces.traces)
    assert any(trace[0] == 'batch_flushed' for trace in traces.traces)


def test_pipeline_records_typing_extension():
    batching = BatchingService(default_window_seconds=5.0, typing_extension_seconds=4.0)
    traces = FakeTraceRepo()
    conversation = ConversationService(
        event_bridge=FakeBridge(),
        policy_service=FakePolicy(),
        prompt_context_service=FakeContext(),
        trace_repository=traces,
    )
    pipeline = MessagePipelineService(
        batching_service=batching,
        conversation_service=conversation,
        trace_repository=traces,
    )
    observation = make_message(created_at=0.0)
    pipeline.handle_message(observation)
    typing = TypingObservation(
        observation_id='typing-1',
        account_name='alpha',
        guild_id='g1',
        guild_name='Guild',
        channel_id='c1',
        channel_name='general',
        author_id='u1',
        username='alice',
        display_name='Alice',
        created_at=4.0,
        is_dm=False,
    )
    pipeline.handle_typing(typing)
    assert pipeline.flush_due(6.0) == []
    assert any(trace[0] == 'batch_typing' for trace in traces.traces)


async def _run_pipeline_loop_test():
    batching = BatchingService(default_window_seconds=0.05, typing_extension_seconds=0.05)
    bridge = FakeBridge()
    conversation = ConversationService(
        event_bridge=bridge,
        policy_service=FakePolicy(),
        prompt_context_service=FakeContext(),
        trace_repository=FakeTraceRepo(),
    )
    pipeline = MessagePipelineService(
        batching_service=batching,
        conversation_service=conversation,
        flush_interval_seconds=0.05,
    )
    await pipeline.start()
    pipeline.handle_message(make_message())
    await asyncio.sleep(0.2)
    await pipeline.stop()
    assert len(bridge.payloads) == 1


def test_pipeline_background_flush_loop():
    asyncio.run(_run_pipeline_loop_test())
