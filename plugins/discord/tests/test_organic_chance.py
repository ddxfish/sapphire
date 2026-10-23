"""Chance-based organic reply gating."""

import asyncio

from plugins.discord.conversation.batching_service import BatchingService
from plugins.discord.conversation.bot_gate import BotGate
from plugins.discord.conversation.conversation_service import ConversationService
from plugins.discord.conversation.trigger_service import evaluate_organic_chance, evaluate_reply_trigger
from plugins.discord.models.observations import TextMessageObservation
from plugins.discord.models.settings import SettingsStore


class FakeBridge:
    def __init__(self):
        self.payloads = []

    def emit_discord_message(self, payload):
        self.payloads.append(payload)
        return True


class FakeDecisions:
    def __init__(self):
        self.rejections = []

    def note(self, kind, **kw):
        if kind == 'rejected':
            self.rejections.append((kw['stage'], kw['reason']))


ORGANIC_HIT = {'allowed': True, 'reason': 'organic_chance', 'organic_reply': True, 'chance': 100.0}


def _obs(**kwargs):
    base = dict(
        observation_id='obs-1', account_name='alpha', guild_id='g1', guild_name='Guild', channel_id='c1',
        channel_name='general', author_id='u1', username='alice', display_name='Alice', message_id='m1',
        content='hello everyone', clean_content='hello everyone', created_at=0.0, is_dm=False, mentioned=False,
        author_is_bot=False, attachments=[],
    )
    base.update(kwargs)
    return TextMessageObservation(**base)


def _service(store, *, bot_gate=None):
    return ConversationService(event_bridge=FakeBridge(), settings_store=store, bot_gate=bot_gate,
                               decisions=FakeDecisions())


def _batch(obs):
    batching = BatchingService(default_window_seconds=5, typing_extension_seconds=4)
    batching.add_message(obs)
    return batching.flush_ready(now=10.0)[0]


def test_evaluate_organic_chance_human_hit():
    settings = SettingsStore({'channel': {'human_response_chance': 50}}).resolve()
    result = evaluate_organic_chance(_obs(), settings, rng=lambda: 0.0)
    assert result['allowed'] is True and result['organic_reply'] is True and result['reason'] == 'organic_chance'


def test_evaluate_organic_chance_human_miss():
    settings = SettingsStore({'channel': {'human_response_chance': 50}}).resolve()
    result = evaluate_organic_chance(_obs(), settings, rng=lambda: 0.99)
    assert result['allowed'] is False and result['reason'] == 'human_response_chance'


def test_evaluate_organic_chance_bot_uses_bot_setting():
    settings = SettingsStore({'channel': {'bot_response_chance': 0, 'human_response_chance': 100}}).resolve()
    result = evaluate_organic_chance(_obs(author_is_bot=True), settings, rng=lambda: 0.0)
    assert result['allowed'] is False and result['reason'] == 'bot_response_chance'


def test_mentions_only_still_blocks_without_chance_reason():
    settings = SettingsStore({'channel': {'reply_mode': 'mentions_only', 'human_response_chance': 100}}).resolve()
    result = evaluate_reply_trigger(_obs(), settings)
    assert result['allowed'] is False and result['reason'] == 'mentions_only'


def test_process_batch_organic_hit_emits_reply(monkeypatch):
    monkeypatch.setattr('plugins.discord.conversation.conversation_service.evaluate_organic_chance',
                        lambda *a, **k: ORGANIC_HIT)
    service = _service(SettingsStore({'channel': {'reply_mode': 'default', 'human_response_chance': 100}}))
    assert asyncio.run(service.process_batch(_batch(_obs()))) is True
    assert service.event_bridge.payloads[0]['message_id'] == 'm1'


def test_process_batch_organic_miss_drops():
    service = _service(SettingsStore({'channel': {'reply_mode': 'default', 'human_response_chance': 0}}))
    assert asyncio.run(service.process_batch(_batch(_obs()))) is False
    assert not service.event_bridge.payloads
    assert service.decisions.rejections[-1] == ('trigger', 'human_response_chance')


def test_mention_bypasses_zero_chance():
    service = _service(SettingsStore({'channel': {'reply_mode': 'default', 'human_response_chance': 0}}))
    assert asyncio.run(service.process_batch(_batch(_obs(mentioned=True)))) is True
    assert service.event_bridge.payloads


def test_dm_bypasses_zero_chance():
    service = _service(SettingsStore({'safety': {'allow_direct_messages': True},
                                      'channel': {'reply_mode': 'default', 'human_response_chance': 0}}))
    assert asyncio.run(service.process_batch(_batch(_obs(is_dm=True, mentioned=False)))) is True


def test_bot_organic_requires_allowlist_then_chance(monkeypatch):
    monkeypatch.setattr('plugins.discord.conversation.conversation_service.evaluate_organic_chance',
                        lambda *a, **k: ORGANIC_HIT)
    store = SettingsStore({'channel': {'reply_mode': 'default', 'bot_response_chance': 100},
                           'bot': {'allowlist_ids': ['peer-bot']}})
    service = _service(store, bot_gate=BotGate())
    assert asyncio.run(service.process_batch(_batch(_obs(author_id='peer-bot', author_is_bot=True, username='PeerBot')))) is True


def test_bot_organic_blocked_when_not_allowlisted():
    store = SettingsStore({'channel': {'reply_mode': 'default', 'bot_response_chance': 100},
                           'bot': {'allowlist_ids': ['other-bot']}})
    service = _service(store, bot_gate=BotGate())
    assert asyncio.run(service.process_batch(_batch(_obs(author_id='peer-bot', author_is_bot=True, username='PeerBot')))) is False
    assert not service.event_bridge.payloads
    assert service.decisions.rejections[-1] == ('bot_gate', 'bot_not_allowlisted')


def test_a_bot_never_rolls_without_a_bot_gate():
    service = _service(SettingsStore({'channel': {'reply_mode': 'default', 'bot_response_chance': 100}}))
    assert asyncio.run(service.process_batch(_batch(_obs(author_id='peer-bot', author_is_bot=True)))) is False
    assert service.decisions.rejections[-1] == ('routing', 'not_addressed')
