"""The reply decision, the live channel window, and the pending map."""
import asyncio
import time

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


class FakeTransport:
    """The one Discord call the reply path makes: the channel window, live."""

    def __init__(self, rows=None, fail=False):
        self.rows = rows if rows is not None else [{'message_id': 'm0', 'author': 'Bob', 'content': 'hi'}]
        self.fail = fail
        self.calls = []

    async def recent_messages_async(self, account_name, channel_id, *, limit):
        self.calls.append((account_name, channel_id, limit))
        if self.fail:
            raise RuntimeError('discord down')
        return list(self.rows)


class FakeDecisions:
    def __init__(self):
        self.rejections = []

    def note(self, kind, **kw):
        if kind == 'rejected':
            self.rejections.append((kw['stage'], kw['reason']))


def make_obs(**over):
    base = dict(
        observation_id='obs-1', account_name='alpha', guild_id='g1', guild_name='Guild', channel_id='c1',
        channel_name='general', author_id='u1', username='alice', display_name='Alice', message_id='m1',
        content='hello', clean_content='hello', created_at=0.0, is_dm=False, mentioned=True, attachments=[],
    )
    base.update(over)
    return TextMessageObservation(**base)


def _batch(*obs):
    bs = BatchingService(default_window_seconds=5, typing_extension_seconds=4)
    for o in obs:
        bs.add_message(o)
    return bs.flush_ready(now=10.0)[0]


def _service(bridge=None, store=None, **kw):
    kw.setdefault('transport', FakeTransport())
    return ConversationService(event_bridge=bridge or FakeBridge(), settings_store=store or SettingsStore(),
                               decisions=FakeDecisions(), **kw)


def _go(service, batch):
    return asyncio.run(service.process_batch(batch))


def test_mention_emits_the_event_with_the_transcript_and_a_pending_row():
    bridge = FakeBridge()
    service = _service(bridge)
    assert _go(service, _batch(make_obs())) is True
    payload = bridge.payloads[0]
    assert payload['message_id'] == 'm1' and payload['batch_size'] == 1 and payload['mentioned'] == 'True'
    assert payload['recent_history'] == ['Bob: hi']
    assert payload['content'] == 'hello'
    assert service.pending_reply('m1')['channel_id'] == 'c1'


def test_rejected_event_does_not_create_pending_metadata():
    bridge = FakeBridge(accepted=False)
    service = _service(bridge)
    assert _go(service, _batch(make_obs())) is False
    assert service.pending_reply('m1') is None
    assert service.decisions.rejections == [('daemon', 'no_daemon_task')]


def test_unaddressed_message_in_mentions_only_mode_is_dropped_with_the_reason():
    service = _service(store=SettingsStore({'channel': {'reply_mode': 'mentions_only'}}))
    assert _go(service, _batch(make_obs(mentioned=False))) is False
    assert service.decisions.rejections == [('trigger', 'mentions_only')]


def test_the_newest_addressed_message_is_the_trigger():
    bridge = FakeBridge()
    service = _service(bridge)
    batch = _batch(make_obs(message_id='m1', mentioned=True, created_at=1.0),
                   make_obs(message_id='m2', mentioned=False, created_at=2.0, clean_content='and then'))
    assert _go(service, batch) is True
    assert bridge.payloads[0]['message_id'] == 'm1' and bridge.payloads[0]['batch_size'] == 2


def test_cooldown_runs_after_the_respond_decision():
    service = _service(store=SettingsStore({'safety': {'rate_limit_seconds': 3600}}))
    assert _go(service, _batch(make_obs(message_id='m1'))) is True
    assert _go(service, _batch(make_obs(message_id='m2'))) is False
    assert service.decisions.rejections[-1] == ('policy', 'cooldown')


def test_discard_pending_drops_payload_and_latches():
    class Style:
        def __init__(self):
            self.discarded = []

        def discard(self, message_id):
            self.discarded.append(message_id)

    style = Style()
    service = _service(reply_style_service=style)
    assert _go(service, _batch(make_obs())) is True
    service.discard_pending('m1')
    assert service.pending_reply('m1') is None and style.discarded == ['m1']


def test_sweep_pending_drops_stale_rows():
    service = _service()
    assert _go(service, _batch(make_obs())) is True
    service._pending['m1']['at'] = time.time() - 7200
    assert service._sweep_pending() == 1 and service.pending_reply('m1') is None


def test_empty_content_never_reaches_the_prompt_empty():
    bridge = FakeBridge()
    service = _service(bridge)
    _go(service, _batch(make_obs(clean_content='', content='',
                                 attachments=[{'url': 'https://x/y.png', 'content_type': 'image/png'}])))
    assert bridge.payloads[0]['content'] == '[The user sent an image with no caption.]'


def test_the_window_is_fetched_once_per_reply_and_only_when_she_answers():
    transport = FakeTransport()
    service = _service(transport=transport, store=SettingsStore({'channel': {'context_messages': 7}}))
    assert _go(service, _batch(make_obs(mentioned=False))) is False          # not addressed: no Discord call
    assert transport.calls == []
    assert _go(service, _batch(make_obs())) is True
    assert transport.calls == [('alpha', 'c1', 7)]


def test_window_off_or_unavailable_still_replies():
    bridge = FakeBridge()
    service = _service(bridge, transport=FakeTransport(), store=SettingsStore({'channel': {'context_messages': 0}}))
    assert _go(service, _batch(make_obs())) is True and bridge.payloads[0]['recent_history'] == []
    bridge = FakeBridge()
    service = _service(bridge, transport=FakeTransport(fail=True))
    assert _go(service, _batch(make_obs())) is True and bridge.payloads[0]['recent_history'] == []
