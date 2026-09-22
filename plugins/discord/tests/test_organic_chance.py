"""Chance-based organic reply gating."""

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


class FakePolicy:
    def evaluate_text_observation(self, observation, resolved_settings=None):
        return {'allowed': True, 'reason': 'ok'}


class FakeContext:
    def build(self, batch):
        return {'recent_history': []}


class FakeTraceRepo:
    def __init__(self):
        self.traces = []

    def record_trace(self, trace_type, summary, detail=None):
        self.traces.append((trace_type, summary, detail or {}))


def _obs(**kwargs):
    base = dict(
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
        content='hello everyone',
        clean_content='hello everyone',
        created_at=0.0,
        is_dm=False,
        mentioned=False,
        author_is_bot=False,
        attachments=[],
    )
    base.update(kwargs)
    return TextMessageObservation(**base)


def _service(store, *, bot_gate=None):
    return ConversationService(
        event_bridge=FakeBridge(),
        policy_service=FakePolicy(),
        prompt_context_service=FakeContext(),
        trace_repository=FakeTraceRepo(),
        settings_store=store,
        bot_gate=bot_gate,
    )


def _batch(obs):
    batching = BatchingService(default_window_seconds=5, typing_extension_seconds=4)
    batching.add_message(obs)
    return batching.flush_ready(now=10.0)[0]


def test_evaluate_organic_chance_human_hit():
    store = SettingsStore()
    store.global_overlay.channel.update({'human_response_chance': 50})
    settings = store.resolve()
    result = evaluate_organic_chance(_obs(), settings, rng=lambda: 0.0)
    assert result['allowed'] is True
    assert result['organic_reply'] is True
    assert result['reason'] == 'organic_chance'


def test_evaluate_organic_chance_human_miss():
    store = SettingsStore()
    store.global_overlay.channel.update({'human_response_chance': 50})
    settings = store.resolve()
    result = evaluate_organic_chance(_obs(), settings, rng=lambda: 0.99)
    assert result['allowed'] is False
    assert result['reason'] == 'human_response_chance'


def test_evaluate_organic_chance_bot_uses_bot_setting():
    store = SettingsStore()
    store.global_overlay.channel.update({'bot_response_chance': 0, 'human_response_chance': 100})
    settings = store.resolve()
    result = evaluate_organic_chance(_obs(author_is_bot=True), settings, rng=lambda: 0.0)
    assert result['allowed'] is False
    assert result['reason'] == 'bot_response_chance'


def test_mentions_only_still_blocks_without_chance_reason():
    store = SettingsStore()
    store.global_overlay.channel.update({
        'reply_mode': 'mentions_only',
        'human_response_chance': 100,
    })
    settings = store.resolve()
    result = evaluate_reply_trigger(_obs(), settings)
    assert result['allowed'] is False
    assert result['reason'] == 'mentions_only'


def test_process_batch_organic_hit_emits_reply(monkeypatch):
    store = SettingsStore()
    store.global_overlay.channel.update({
        'reply_mode': 'default',
        'human_response_chance': 100,
    })
    monkeypatch.setattr(
        'plugins.discord.conversation.conversation_service.evaluate_organic_chance',
        lambda *a, **k: {
            'allowed': True,
            'reason': 'organic_chance',
            'organic_reply': True,
            'chance': 100.0,
        },
    )
    service = _service(store)
    assert service.process_batch(_batch(_obs())) is True
    assert service.event_bridge.payloads
    assert service.event_bridge.payloads[0]['message_id'] == 'm1'


def test_process_batch_organic_miss_drops(monkeypatch):
    store = SettingsStore()
    store.global_overlay.channel.update({
        'reply_mode': 'default',
        'human_response_chance': 0,
    })
    traces = FakeTraceRepo()
    service = ConversationService(
        event_bridge=FakeBridge(),
        policy_service=FakePolicy(),
        prompt_context_service=FakeContext(),
        trace_repository=traces,
        settings_store=store,
    )
    assert service.process_batch(_batch(_obs())) is False
    assert not service.event_bridge.payloads
    assert traces.traces[-1][0] == 'event_dropped'
    assert traces.traces[-1][2].get('reason') == 'human_response_chance'


def test_mention_bypasses_zero_chance():
    store = SettingsStore()
    store.global_overlay.channel.update({
        'reply_mode': 'default',
        'human_response_chance': 0,
    })
    service = _service(store)
    assert service.process_batch(_batch(_obs(mentioned=True))) is True
    assert service.event_bridge.payloads


def test_dm_bypasses_zero_chance():
    store = SettingsStore()
    store.global_overlay.safety.update({'allow_direct_messages': True})   # DMs are opt-in since 2026-09-13
    store.global_overlay.channel.update({
        'reply_mode': 'default',
        'human_response_chance': 0,
    })
    service = _service(store)
    assert service.process_batch(_batch(_obs(is_dm=True, mentioned=False))) is True


def test_bot_organic_requires_allowlist_then_chance(monkeypatch):
    store = SettingsStore()
    store.global_overlay.channel.update({
        'reply_mode': 'default',
        'bot_response_chance': 100,
    })
    store.global_overlay.bot.update({
        'enabled': True,
        'allowlist_ids': ['peer-bot'],
    })
    monkeypatch.setattr(
        'plugins.discord.conversation.conversation_service.evaluate_organic_chance',
        lambda *a, **k: {
            'allowed': True,
            'reason': 'organic_chance',
            'organic_reply': True,
            'chance': 100.0,
        },
    )
    service = _service(store, bot_gate=BotGate())
    obs = _obs(author_id='peer-bot', author_is_bot=True, username='PeerBot')
    assert service.process_batch(_batch(obs)) is True


def test_bot_organic_blocked_when_not_allowlisted():
    store = SettingsStore()
    store.global_overlay.channel.update({
        'reply_mode': 'default',
        'bot_response_chance': 100,
    })
    store.global_overlay.bot.update({
        'enabled': True,
        'allowlist_ids': ['other-bot'],
    })
    service = _service(store, bot_gate=BotGate())
    obs = _obs(author_id='peer-bot', author_is_bot=True, username='PeerBot')
    assert service.process_batch(_batch(obs)) is False
    assert not service.event_bridge.payloads
