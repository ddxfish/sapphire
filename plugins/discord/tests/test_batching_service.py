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


def _obs_at(created_at, *, mentioned=False, content='hello'):
    from plugins.discord.models.observations import TextMessageObservation
    return TextMessageObservation(
        observation_id=f'obs:{created_at}', account_name='a', guild_id='g', guild_name='G',
        channel_id='c', channel_name='general', author_id='u', username='u', display_name='U',
        message_id=f'm{created_at}', content=content, clean_content=content,
        created_at=created_at, is_dm=False, mentioned=mentioned, attachments=[],
    )


def test_steady_traffic_cannot_defer_flush_forever():
    # Scout critical 2026-08-05: every message reset flush_at, so an active
    # channel never flushed and @mentions were silently dropped.
    from plugins.discord.conversation.batching_service import BatchingService
    service = BatchingService(default_window_seconds=8.0, typing_extension_seconds=4.0)
    service.add_message(_obs_at(0.0, mentioned=True, content='@sapph hello?'))
    for t in range(2, 60, 2):
        service.add_message(_obs_at(float(t)))
    batch = service._batches[('a', 'c')]
    assert batch.flush_at <= 0.0 + 8.0 * 2.5
    assert service.flush_ready(now=21.0)


def test_batch_urgency_keeps_short_window():
    from plugins.discord.conversation.batching_service import BatchingService
    service = BatchingService(default_window_seconds=8.0, typing_extension_seconds=4.0)
    service.add_message(_obs_at(0.0, mentioned=True))
    service.add_message(_obs_at(1.0, content='just chatter'))
    batch = service._batches[('a', 'c')]
    # Non-urgent follow-up must keep the batch's urgent (halved) window.
    assert batch.flush_at == 1.0 + 4.0
