"""Channel situation, relationship policy, and intention competition."""

from types import SimpleNamespace

from plugins.discord.cognition.channel_situation import ChannelSituation, ChannelSituationService
from plugins.discord.cognition.intention_scorer import choose_social_intention
from plugins.discord.cognition.relationship_policy import (
    organic_multiplier,
    reaction_multiplier,
    relationship_snapshot,
)
from plugins.discord.conversation.batching_service import BatchingService
from plugins.discord.conversation.conversation_service import ConversationService
from plugins.discord.conversation.trigger_service import evaluate_organic_chance
from plugins.discord.cognition.cognitive_orchestrator import CognitiveOrchestrator
from plugins.discord.memory.distill_service import DistillService
from plugins.discord.models.observations import TextMessageObservation
from plugins.discord.models.settings import SettingsStore


class FakeTraceRepo:
    def __init__(self):
        self.traces = []

    def record_trace(self, trace_type, summary, detail=None):
        self.traces.append((trace_type, summary, detail or {}))


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


class FakeMessageRepo:
    def __init__(self, rows):
        self.rows = rows

    def get_recent_messages(self, account_name, channel_id, limit=24):
        return list(self.rows)[:limit]


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


def _batch(obs):
    batching = BatchingService(default_window_seconds=5, typing_extension_seconds=4)
    batching.add_message(obs)
    return batching.flush_ready(now=10.0)[0]


def test_situation_from_quiet_channel():
    now = 1_700_000_000.0
    rows = [
        {'author_id': 'u1', 'content': 'hey', 'created_at': now - 7200},
    ]
    service = ChannelSituationService(message_repository=FakeMessageRepo(rows))
    situation = service.build('alpha', 'c1', now=now, use_cache_seconds=0)
    assert situation.vibe == 'quiet'
    assert service.organic_multiplier(situation) == 1.25
    allowed, reason = service.outreach_allowed(situation)
    assert allowed is True


def test_situation_heated_blocks_outreach():
    now = 1_700_000_000.0
    rows = [
        {'author_id': f'u{i}', 'content': 'you are wrong and stupid wtf', 'created_at': now - 10}
        for i in range(6)
    ]
    service = ChannelSituationService(message_repository=FakeMessageRepo(rows))
    situation = service.build('alpha', 'c1', now=now, use_cache_seconds=0)
    assert situation.vibe == 'heated'
    assert service.organic_multiplier(situation) == 0.35
    allowed, reason = service.outreach_allowed(situation)
    assert allowed is False
    assert reason == 'situation_heated'


def test_relationship_multipliers_prefer_known_people():
    stranger = relationship_snapshot({'familiarity': 0.0, 'fondness': 0.5, 'interest': 0.5})
    friend = relationship_snapshot({'familiarity': 0.8, 'fondness': 0.7, 'interest': 0.7})
    assert organic_multiplier(friend) > organic_multiplier(stranger)
    assert reaction_multiplier(friend) > reaction_multiplier(stranger)


def test_organic_chance_respects_multiplier():
    store = SettingsStore()
    store.global_overlay.channel.update({'human_response_chance': 50})
    settings = store.resolve()
    hit = evaluate_organic_chance(_obs(), settings, rng=lambda: 0.4, chance_multiplier=0.5)
    # effective chance 25% — roll 0.4 misses
    assert hit['allowed'] is False
    assert hit['chance'] == 25.0
    assert hit['chance_multiplier'] == 0.5


def test_intention_competition_forced_address():
    store = SettingsStore()
    store.global_overlay.cognitive.update({'intention_competition_enabled': True})
    settings = store.resolve()
    chosen = choose_social_intention(
        settings=settings,
        addressed=True,
        is_dm=False,
        reply_mode='default',
        organic_base_chance=0,
        rng=lambda: 0.99,
    )
    assert chosen.kind == 'reply'
    assert chosen.reason == 'forced_address'


def test_intention_competition_heated_prefers_non_reply():
    store = SettingsStore()
    store.global_overlay.cognitive.update({'intention_competition_enabled': True})
    settings = store.resolve()
    situation = ChannelSituation(vibe='heated', heat=0.8, silence_seconds=5)
    # Deterministic: pick by walking weights; use high roll toward silent weight.
    kinds = set()
    for i in range(40):
        # Cycle rolls through [0,1)
        roll_val = (i + 0.5) / 40.0
        chosen = choose_social_intention(
            settings=settings,
            addressed=False,
            is_dm=False,
            reply_mode='default',
            situation=situation,
            relationship={'familiarity': 0.0},
            organic_base_chance=40.0,
            organic_multiplier=0.35,
            reaction_base_chance=20.0,
            rng=lambda r=roll_val: r,
        )
        kinds.add(chosen.kind)
    assert 'silent' in kinds or 'react' in kinds


def test_process_batch_competition_react_only(monkeypatch):
    store = SettingsStore()
    store.global_overlay.channel.update({'reply_mode': 'default', 'human_response_chance': 50})
    store.global_overlay.cognitive.update({'intention_competition_enabled': True})
    store.global_overlay.reaction.update({
        'enabled': True,
        'silent_enabled': True,
        'reaction_chance': 100,
        'read_only_enabled': True,
    })

    class FakeReaction:
        def __init__(self):
            self.calls = []

        def evaluate_silent(self, trigger, *, settings, world_state=None, reply_planned=False, read_only=False):
            self.calls.append({'force_react': bool((world_state or {}).get('force_react')), 'read_only': read_only})
            return SimpleNamespace(
                intention_type='add_reaction',
                channel_id=trigger.channel_id,
                reason='silent_sentiment',
                emoji='👍',
            )

        def execute_silent(self, intention, *, transport, settings=None):
            return {'status': 'ok'}

    monkeypatch.setattr(
        'plugins.discord.conversation.conversation_service.choose_social_intention',
        lambda **kwargs: SimpleNamespace(kind='react', score=0.5, reason='scored_react'),
    )
    traces = FakeTraceRepo()
    reactions = FakeReaction()
    service = ConversationService(
        event_bridge=FakeBridge(),
        policy_service=FakePolicy(),
        prompt_context_service=FakeContext(),
        trace_repository=traces,
        settings_store=store,
        cognitive_orchestrator=CognitiveOrchestrator(),
        reaction_service=reactions,
        transport=object(),
    )
    assert service.process_batch(_batch(_obs())) is False
    assert any(t[0] == 'intention_scored' for t in traces.traces)
    assert reactions.calls and reactions.calls[0]['force_react'] is True


def test_distill_near_duplicate_jaccard():
    assert DistillService._is_near_duplicate(
        'Has a dog named Mochi',
        {'has a dog named mochi'},
    )
    assert DistillService._is_near_duplicate(
        'Has a dog named Mochi the pup',
        {'has a dog named mochi'},
    )
    assert not DistillService._is_near_duplicate(
        'Works night shifts at the hospital',
        {'has a dog named mochi'},
    )


def test_task_follow_up_skips_heated_channel():
    class FakeWorld:
        def list_due_tasks(self, account_name, now_ts=None, limit=10):
            return [{
                'id': 7,
                'task_type': 'commitment_follow_up',
                'target_id': 'c-hot',
                'payload_json': {
                    'user_id': 'u1',
                    'display_name': 'Alice',
                    'commitment': 'ship the hotfix',
                    'when_label': 'in 3 days',
                },
            }]

    class FakeSituation:
        def build(self, account_name, channel_id, **kwargs):
            return ChannelSituation(vibe='heated', heat=0.9, silence_seconds=5)

        def outreach_allowed(self, situation):
            return False, 'situation_heated'

    traces = FakeTraceRepo()
    orch = CognitiveOrchestrator(
        world_model_service=FakeWorld(),
        channel_situation_service=FakeSituation(),
        trace_service=SimpleNamespace(
            record_intention=lambda *a, **k: None,
            record_policy_rejection=lambda reason, detail=None: traces.record_trace(
                'policy_rejected', reason, detail or {},
            ),
        ),
    )
    store = SettingsStore()
    store.global_overlay.cognitive.update({'situation_enabled': True, 'task_follow_up_enabled': True})
    intentions = orch.evaluate_task_intentions('alpha', store.resolve())
    assert intentions == []
    assert any(t[0] == 'policy_rejected' and 'situation_heated' in str(t[1]) for t in traces.traces)


def test_reminder_follow_up_bypasses_heated_gate():
    class FakeWorld:
        def list_due_tasks(self, account_name, now_ts=None, limit=10):
            return [{
                'id': 14,
                'task_type': 'reminder_follow_up',
                'target_id': 'c-hot',
                'payload_json': {
                    'user_id': 'u1',
                    'display_name': 'Zeebie',
                    'reminder': 'drink water',
                    'when_label': 'in 2 minutes',
                    'instruction': 'Remind Zeebie to drink water.',
                },
            }]

    class FakeSituation:
        def build(self, account_name, channel_id, **kwargs):
            raise AssertionError('reminders must not consult situation')

        def outreach_allowed(self, situation):
            raise AssertionError('reminders must not consult situation')

    orch = CognitiveOrchestrator(
        world_model_service=FakeWorld(),
        channel_situation_service=FakeSituation(),
    )
    store = SettingsStore()
    store.global_overlay.cognitive.update({'situation_enabled': True, 'task_follow_up_enabled': True})
    intentions = orch.evaluate_task_intentions('alpha', store.resolve())
    assert len(intentions) == 1
    assert intentions[0].metadata.get('task_type') == 'reminder_follow_up'


def test_stale_argue_keyword_does_not_heat_channel():
    now = 1_700_000_000.0
    rows = [
        {'author_id': 'u1', 'content': 'remind me in 2 minutes to drink water', 'created_at': now - 120},
        {'author_id': 'u1', 'content': 'bedtime story about an angry prince', 'created_at': now - 86400 * 7},
    ]
    service = ChannelSituationService(message_repository=FakeMessageRepo(rows))
    situation = service.build('alpha', 'c1', now=now, use_cache_seconds=0)
    assert situation.vibe != 'heated'
    assert situation.argue_hits == 0
    allowed, _reason = service.outreach_allowed(situation)
    assert allowed is True
