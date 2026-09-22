"""S0 doors: the six host hooks fire from the existing pipeline with the api facade.

These are the contract the S7 rewrite must keep and the discord-personality
plugin's tests build on. Handlers register by name through core's hook_runner.
"""
import asyncio
from types import SimpleNamespace

import pytest

from core.hooks import hook_runner
from plugins.discord import hooks_out
from plugins.discord.conversation.batching_service import BatchingService
from plugins.discord.conversation.conversation_service import ConversationService
from plugins.discord.conversation.message_pipeline_service import MessagePipelineService
from plugins.discord.models.observations import TextMessageObservation

PLUGIN = 't-personality'


@pytest.fixture(autouse=True)
def _clean_hooks():
    hook_runner.unregister_plugin(PLUGIN)
    yield
    hook_runner.unregister_plugin(PLUGIN)


def _listen(name, fn):
    hook_runner.register(name, fn, priority=60, plugin_name=PLUGIN)


def make_obs(**over):
    base = dict(
        observation_id='obs-1', account_name='alpha', guild_id='g1', guild_name='Guild',
        channel_id='c1', channel_name='general', author_id='u1', username='alice',
        display_name='Alice', message_id='m1', content='hello', clean_content='hello',
        created_at=0.0, is_dm=False, mentioned=True, attachments=[],
    )
    base.update(over)
    return TextMessageObservation(**base)


# ── fire() itself ────────────────────────────────────────────────────────────

def test_fire_stamps_public_and_carries_the_api():
    seen = []
    _listen('discord_message_observed', lambda ev: seen.append(ev))

    ev = hooks_out.fire('discord_message_observed', {'account': 'alpha', 'content': 'hi'})

    assert seen == [ev]
    assert ev.chat_private is False and ev.chat_name is None
    assert isinstance(ev.metadata['api'], hooks_out.DiscordAPI)
    assert ev.metadata['hook'] == 'discord_message_observed'
    assert ev.metadata['account'] == 'alpha' and ev.metadata['content'] == 'hi'


def test_fire_without_handlers_never_enters_the_runner(monkeypatch):
    def boom(*a, **k):
        raise AssertionError('runner.fire called with no handlers')
    monkeypatch.setattr(hook_runner, 'fire', boom)
    ev = hooks_out.fire('discord_tick', {'account': 'alpha'})
    assert ev.metadata['account'] == 'alpha'


def test_a_raising_handler_is_skipped_not_raised():
    def bad(ev):
        raise RuntimeError('handler bug')
    _listen('discord_reply_sent', bad)
    ev = hooks_out.fire('discord_reply_sent', {'account': 'alpha'})
    assert ev.metadata['account'] == 'alpha'


def test_fire_threaded_is_synchronous_off_loop():
    seen = []
    _listen('discord_message_observed', lambda ev: seen.append(ev.metadata['content']))
    hooks_out.fire_threaded('discord_message_observed', {'content': 'x'})
    assert seen == ['x']


# ── the fire sites ───────────────────────────────────────────────────────────

class FakeBatching:
    def add_message(self, obs):
        return SimpleNamespace(message_count=1, flush_at=0.0)


def test_pipeline_fires_observed_for_every_inbound():
    seen = []
    _listen('discord_message_observed', lambda ev: seen.append(dict(ev.metadata)))
    pipeline = MessagePipelineService(batching_service=FakeBatching(), conversation_service=None)

    pipeline.handle_message(make_obs())

    assert len(seen) == 1
    md = seen[0]
    assert md['account'] == 'alpha' and md['channel_id'] == 'c1' and md['message_id'] == 'm1'
    assert md['content'] == 'hello' and md['mentioned'] is True and md['is_dm'] is False
    assert md['is_bot'] is False and md['display_name'] == 'Alice'


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
        return {'recent_history': ['hi']}


class Traces:
    def record_trace(self, *a, **k):
        return None


def _batch():
    bs = BatchingService(default_window_seconds=5, typing_extension_seconds=4)
    bs.add_message(make_obs())
    return bs.flush_ready(now=10.0)[0]


def test_process_batch_fires_prompt_context_and_appends_to_the_prompt():
    from plugins.discord.models.settings import SettingsStore
    seen = []

    def handler(ev):
        seen.append(dict(ev.metadata))
        ev.metadata['context_parts'].append('Speak like a pirate.')
    _listen('discord_prompt_context', handler)
    bridge = FakeBridge()
    service = ConversationService(
        event_bridge=bridge, policy_service=FakePolicy(), prompt_context_service=FakeContext(),
        trace_repository=Traces(), settings_store=SettingsStore(),
    )

    assert service.process_batch(_batch()) is True

    assert seen[0]['message_id'] == 'm1' and seen[0]['reply_reason'] == 'mentioned'
    assert seen[0]['recent_history'] == ['hi']
    payload = bridge.payloads[0]
    assert 'Speak like a pirate.' in payload['reply_hints']
    assert 'Speak like a pirate.' in payload['reply_instructions']


class FakeTransport:
    def __init__(self):
        self.sent, self.reactions = [], []

    def send_message_sync(self, channel_id, text, reply_to_message_id=None, account_name=None, guild_id=None):
        self.sent.append({'channel_id': channel_id, 'text': text, 'reply_to': reply_to_message_id})
        return {'status': 'sent', 'messages': [{'message_id': f'sent-{len(self.sent)}'}]}

    def hold_typing_sync(self, *a, **k):
        return None

    def add_reaction_sync(self, channel_id, message_id, emoji, account_name=None):
        self.reactions.append((channel_id, message_id, emoji))


class FakeReplyStyle:
    def should_skip_auto_reply(self, message_id):
        return False

    def parse_llm_output(self, text, strip_thinking=True):
        from plugins.discord.conversation.reply_style_service import ParsedReply
        return ParsedReply(chunks=[text], reaction='', gif_query='')


def _delivery_service(transport, monkeypatch):
    monkeypatch.setattr('plugins.discord.conversation.conversation_service.deliver_gif_and_reaction',
                        lambda **kwargs: None)
    monkeypatch.setattr('plugins.discord.conversation.conversation_service.time.sleep', lambda s: None)
    return ConversationService(
        event_bridge=None, policy_service=None, prompt_context_service=None,
        trace_repository=Traces(), reply_style_service=FakeReplyStyle(), transport=transport,
    )


EVENT = {'message_id': 'm-trigger', 'account': 'alpha', 'channel_id': 'c1', 'guild_id': 'g1'}


def test_reply_planned_can_reshape_quote_and_react_then_reply_sent_reports(monkeypatch):
    planned, sent = [], []

    def plan(ev):
        planned.append(dict(ev.metadata))
        ev.metadata['chunks'] = ['edited by hook']
        ev.metadata['quote_reply'] = ''          # deliberately unquoted
        ev.metadata['reaction'] = '👀'
    _listen('discord_reply_planned', plan)
    _listen('discord_reply_sent', lambda ev: sent.append(dict(ev.metadata)))
    transport = FakeTransport()
    service = _delivery_service(transport, monkeypatch)

    result = service.handle_llm_response(None, dict(EVENT), 'original text')

    assert result['status'] == 'sent'
    assert planned[0]['chunks'] == ['original text'] and planned[0]['message_id'] == 'm-trigger'
    assert transport.sent == [{'channel_id': 'c1', 'text': 'edited by hook', 'reply_to': None}]
    assert transport.reactions == [('c1', 'm-trigger', '👀')]
    assert sent[0]['sent_message_ids'] == ['sent-1'] and sent[0]['chunks'] == ['edited by hook']
    assert sent[0]['account'] == 'alpha' and sent[0]['channel_id'] == 'c1'


def test_reply_planned_cannot_silence_the_reply_and_legacy_quote_holds(monkeypatch):
    _listen('discord_reply_planned', lambda ev: ev.metadata.__setitem__('chunks', []))
    transport = FakeTransport()
    service = _delivery_service(transport, monkeypatch)

    service.handle_llm_response(None, dict(EVENT), 'still said')

    assert transport.sent[0]['text'] == 'still said'
    assert transport.sent[0]['reply_to'] == 'm-trigger'      # untouched legacy quote
    assert transport.reactions == []


def test_reply_planned_delay_is_clamped(monkeypatch):
    slept = []
    _listen('discord_reply_planned', lambda ev: ev.metadata.__setitem__('delay_s', 999))
    transport = FakeTransport()
    service = _delivery_service(transport, monkeypatch)
    monkeypatch.setattr('plugins.discord.conversation.conversation_service.time.sleep',
                        lambda s: slept.append(s))

    service.handle_llm_response(None, dict(EVENT), 'x')

    assert 30.0 in slept and max(slept) == 30.0


def test_tick_fires_per_connected_account_off_the_loop():
    from plugins.discord.runtime.container import RuntimeContainer
    seen = []
    _listen('discord_tick', lambda ev: seen.append(dict(ev.metadata)))

    async def _noop():
        return None

    fake = SimpleNamespace(
        transport=SimpleNamespace(
            list_connected=lambda: ['alpha', 'beta'],
            get_client=lambda name: SimpleNamespace(guilds=[SimpleNamespace(id=11), SimpleNamespace(id=22)]),
        ),
        scheduler=SimpleNamespace(interval_seconds=15.0),
        greetings=None, voice_auto_join_service=None,
        _reconcile_accounts=_noop, _reap_voice_chats=_noop,
    )
    fake._tick_payload = lambda name: RuntimeContainer._tick_payload(fake, name)

    asyncio.run(RuntimeContainer._scheduler_tick(fake))

    assert [m['account'] for m in seen] == ['alpha', 'beta']
    assert seen[0]['connected_guilds'] == ['11', '22']
    assert 0 <= seen[0]['local_hour'] <= 23 and len(seen[0]['local_time']) == 5
    assert seen[0]['interval_s'] == 15.0


# ── the facade ───────────────────────────────────────────────────────────────

class FakeLoopTransport:
    def __init__(self, loop):
        self.loop = loop
        self.sync_calls, self.async_calls = [], []

    def send_message_sync(self, cid, text, reply_to_message_id=None, account_name=None):
        self.sync_calls.append(('send', cid, text))
        return {'status': 'sent', 'messages': [{'message_id': '1'}]}

    async def send_message_async(self, cid, text, reply_to_message_id=None, account_name=None):
        self.async_calls.append(('send', cid, text))

    def read_messages(self, cid, count=20, account_name=None):
        return [{'message_id': '9', 'content': 'old'}]

    def list_connected(self):
        return ['alpha']


def test_api_is_loop_aware_and_never_raises(monkeypatch):
    api = hooks_out.DiscordAPI()
    monkeypatch.setattr(hooks_out, '_runtime', lambda: None)
    assert api.send_message('c1', 'hi')['status'] == 'error'
    assert api.recent_messages('alpha', 'c1') == [] and api.accounts() == []
    assert api.channel_info('c1') == {}

    loop = asyncio.new_event_loop()
    t = FakeLoopTransport(loop)
    monkeypatch.setattr(hooks_out, '_runtime', lambda: SimpleNamespace(transport=t))

    # off the loop: synchronous path
    assert api.send_message('c1', 'hi', account='alpha')['status'] == 'sent'
    assert t.sync_calls == [('send', 'c1', 'hi')]
    assert api.recent_messages('alpha', 'c1')[0]['content'] == 'old'
    assert api.accounts() == ['alpha']

    async def on_loop():
        queued = api.send_message('c1', 'later')
        read = api.recent_messages('alpha', 'c1')
        await asyncio.sleep(0)
        return queued, read

    queued, read = loop.run_until_complete(on_loop())
    loop.close()
    assert queued == {'status': 'queued'} and read == []
    assert t.async_calls == [('send', 'c1', 'later')]


def test_api_send_image_refuses_disk_paths(monkeypatch):
    api = hooks_out.DiscordAPI()
    res = api.send_image('c1', '/etc/passwd')
    assert res['status'] == 'error' and 'img:' in res['error']


def test_hooks_list_is_the_contract():
    assert hooks_out.HOOKS == (
        'discord_message_observed', 'discord_prompt_context', 'discord_reply_planned',
        'discord_reply_sent', 'discord_voice_utterance', 'discord_tick',
    )
