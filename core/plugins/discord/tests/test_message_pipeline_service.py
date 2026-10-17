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


def make_message(message_id='m1', created_at=None):
    return TextMessageObservation(
        observation_id=f'obs-{message_id}', account_name='alpha', guild_id='g1', guild_name='Guild', channel_id='c1',
        channel_name='general', author_id='u1', username='alice', display_name='Alice', message_id=message_id,
        content='hello', clean_content='hello', created_at=created_at if created_at is not None else time.time(),
        is_dm=False, mentioned=True, attachments=[],
    )


def _pipeline(window, bridge=None, **kw):
    batching = BatchingService(default_window_seconds=window, typing_extension_seconds=window)
    conversation = ConversationService(event_bridge=bridge or FakeBridge())
    return MessagePipelineService(batching_service=batching, conversation_service=conversation, **kw)


def test_pipeline_flushes_batch_into_conversation_service():
    bridge = FakeBridge()
    pipeline = _pipeline(0.1, bridge)
    observation = make_message()
    pipeline.handle_message(observation)
    results = asyncio.run(pipeline.flush_due(observation.created_at + 1.0))
    assert len(results) == 1 and results[0]['accepted'] is True and results[0]['message_ids'] == ['m1']
    assert len(bridge.payloads) == 1


def test_typing_extends_the_batch_window():
    pipeline = _pipeline(5.0)
    pipeline.handle_message(make_message(created_at=0.0))
    typing = TypingObservation(observation_id='typing-1', account_name='alpha', guild_id='g1', guild_name='Guild',
                               channel_id='c1', channel_name='general', author_id='u1', username='alice',
                               display_name='Alice', created_at=4.0, is_dm=False)
    pipeline.handle_typing(typing)
    assert asyncio.run(pipeline.flush_due(6.0)) == []
    assert len(asyncio.run(pipeline.flush_due(10.0))) == 1


def test_a_batch_that_raises_does_not_stop_the_others():
    bridge = FakeBridge()
    pipeline = _pipeline(0.1, bridge)
    calls = {'n': 0}
    real = pipeline.conversation_service.process_batch

    async def flaky(batch):
        calls['n'] += 1
        if calls['n'] == 1:
            raise RuntimeError('boom')
        return await real(batch)
    pipeline.conversation_service.process_batch = flaky
    a = make_message('m1', created_at=0.0)
    a.channel_id = 'c1'
    b = make_message('m2', created_at=0.0)
    b.channel_id = 'c2'
    pipeline.handle_message(a)
    pipeline.handle_message(b)
    results = asyncio.run(pipeline.flush_due(5.0))
    assert len(results) == 1 and len(bridge.payloads) == 1


def test_pipeline_background_flush_loop():
    async def run():
        bridge = FakeBridge()
        pipeline = _pipeline(0.05, bridge, flush_interval_seconds=0.05)
        await pipeline.start()
        pipeline.handle_message(make_message())
        await asyncio.sleep(0.25)
        await pipeline.stop()
        assert len(bridge.payloads) == 1
    asyncio.run(run())
