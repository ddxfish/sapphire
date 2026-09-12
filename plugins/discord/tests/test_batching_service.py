from plugins.discord.conversation.batching_service import BatchingService
from plugins.discord.models.observations import TextMessageObservation, TypingObservation


def make_message(message_id, content, created_at):
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
        content=content,
        clean_content=content,
        created_at=created_at,
        is_dm=False,
        mentioned=False,
        attachments=[],
    )


def test_single_message_flushes_after_window():
    service = BatchingService(default_window_seconds=5, typing_extension_seconds=4)
    batch = service.add_message(make_message('1', 'hello', 0.0))
    assert batch.message_count == 1
    ready = service.flush_ready(now=6.0)
    assert len(ready) == 1
    assert ready[0].message_ids == ['1']


def test_multi_message_batches_by_channel():
    service = BatchingService(default_window_seconds=5, typing_extension_seconds=4)
    service.add_message(make_message('1', 'hello', 0.0))
    batch = service.add_message(make_message('2', 'again', 2.0))
    assert batch.message_count == 2
    ready = service.flush_ready(now=8.0)
    assert ready[0].message_ids == ['1', '2']


def test_typing_extends_batch_window():
    service = BatchingService(default_window_seconds=5, typing_extension_seconds=4)
    service.add_message(make_message('1', 'hello', 0.0))
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
    service.record_typing(typing)
    assert service.flush_ready(now=6.0) == []
    ready = service.flush_ready(now=10.0)
    assert len(ready) == 1
    assert ready[0].typing_extended is True
