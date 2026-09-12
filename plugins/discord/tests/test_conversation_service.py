from plugins.discord.conversation.batching_service import BatchingService
from plugins.discord.conversation.conversation_service import ConversationService
from plugins.discord.cognition.cognitive_orchestrator import CognitiveOrchestrator
from plugins.discord.cognition.goal_engine import GoalEngine
from plugins.discord.cognition.intent_engine import IntentEngine
from plugins.discord.cognition.world_state_builder import WorldStateBuilder
from plugins.discord.models.observations import TextMessageObservation
from plugins.discord.models.settings import SettingsStore


class FakeBridge:
    def __init__(self, accepted=True):
        self.accepted = accepted
        self.payloads = []

    def emit_discord_message(self, payload):
        self.payloads.append(payload)
        return self.accepted


class FakePolicy:
    def evaluate_text_observation(self, observation, resolved_settings=None):
        return {'allowed': True, 'reason': 'ok'}


class FakeContext:
    def __init__(self, context=None):
        self.context = context or {'recent_history': ['hi']}

    def build(self, batch):
        return self.context


class FakeTraceRepo:
    def __init__(self):
        self.traces = []

    def record_trace(self, trace_type, summary, detail=None):
        self.traces.append((trace_type, summary, detail or {}))


def make_obs():
    return TextMessageObservation(
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
        content='hello',
        clean_content='hello',
        created_at=0.0,
        is_dm=False,
        mentioned=True,
        attachments=[],
    )


def test_emit_reply_includes_vision_description_in_content():
    batch_service = BatchingService(default_window_seconds=5, typing_extension_seconds=4)
    obs = make_obs()
    obs.attachments = [{'url': 'https://cdn/a.png', 'filename': 'cat.png', 'content_type': 'image/png'}]
    obs.clean_content = ''
    obs.content = ''
    batch_service.add_message(obs)
    batch = batch_service.flush_ready(now=10.0)[0]
    bridge = FakeBridge()
    media_context = [{
        'media_kind': 'image',
        'source_url': 'https://cdn/a.png',
        'interpretation': {
            'summary': 'A cat on a windowsill.',
            'source': 'vision',
        },
    }]
    service = ConversationService(
        event_bridge=bridge,
        policy_service=FakePolicy(),
        prompt_context_service=FakeContext({'recent_history': [], 'media': media_context}),
        trace_repository=FakeTraceRepo(),
        settings_store=SettingsStore(),
        cognitive_orchestrator=CognitiveOrchestrator(
            intent_engine=IntentEngine(goal_engine=GoalEngine()),
            world_state_builder=WorldStateBuilder(),
        ),
    )

    emitted = service.process_batch(batch)

    assert emitted is True
    assert 'automated vision description' in bridge.payloads[0]['content']
    assert 'A cat on a windowsill.' in bridge.payloads[0]['content']


def test_emit_reply_intention_for_batch():
    batch_service = BatchingService(default_window_seconds=5, typing_extension_seconds=4)
    batch_service.add_message(make_obs())
    batch = batch_service.flush_ready(now=10.0)[0]
    bridge = FakeBridge()
    from plugins.discord.cognition.cognitive_orchestrator import CognitiveOrchestrator
    from plugins.discord.cognition.goal_engine import GoalEngine
    from plugins.discord.cognition.intent_engine import IntentEngine
    from plugins.discord.cognition.world_state_builder import WorldStateBuilder
    from plugins.discord.models.settings import SettingsStore

    orchestrator = CognitiveOrchestrator(
        intent_engine=IntentEngine(goal_engine=GoalEngine()),
        world_state_builder=WorldStateBuilder(),
    )
    store = SettingsStore()
    service = ConversationService(
        event_bridge=bridge,
        policy_service=FakePolicy(),
        prompt_context_service=FakeContext(),
        trace_repository=FakeTraceRepo(),
        settings_store=store,
        cognitive_orchestrator=orchestrator,
    )

    emitted = service.process_batch(batch)

    assert emitted is True
    assert bridge.payloads[0]['message_id'] == 'm1'
    assert bridge.payloads[0]['batch_size'] == 1
    assert service.pending_reply('m1')['channel_id'] == 'c1'


def test_rejected_event_does_not_create_pending_metadata():
    batch_service = BatchingService(default_window_seconds=5, typing_extension_seconds=4)
    batch_service.add_message(make_obs())
    batch = batch_service.flush_ready(now=10.0)[0]
    bridge = FakeBridge(accepted=False)
    traces = FakeTraceRepo()
    service = ConversationService(
        event_bridge=bridge,
        policy_service=FakePolicy(),
        prompt_context_service=FakeContext(),
        trace_repository=traces,
        settings_store=SettingsStore(),
        cognitive_orchestrator=CognitiveOrchestrator(
            intent_engine=IntentEngine(goal_engine=GoalEngine()),
            world_state_builder=WorldStateBuilder(),
        ),
    )

    emitted = service.process_batch(batch)

    assert emitted is False
    assert service.pending_reply('m1') is None
    assert traces.traces[-1][0] == 'event_dropped'


class FakeMentionMap:
    def mention_format_hint(self):
        return 'Use @DisplayName for mentions.'

    def build_for_channel(self, *args, **kwargs):
        return {}


def test_emit_reply_omits_plugin_scheduled_without_follow_up_hints():
    batch_service = BatchingService(default_window_seconds=5, typing_extension_seconds=4)
    batch_service.add_message(make_obs())
    batch = batch_service.flush_ready(now=10.0)[0]
    bridge = FakeBridge()
    service = ConversationService(
        event_bridge=bridge,
        policy_service=FakePolicy(),
        prompt_context_service=FakeContext(),
        trace_repository=FakeTraceRepo(),
        settings_store=SettingsStore(),
        mention_map_service=FakeMentionMap(),
        cognitive_orchestrator=CognitiveOrchestrator(
            intent_engine=IntentEngine(goal_engine=GoalEngine()),
            world_state_builder=WorldStateBuilder(),
        ),
    )

    assert service.process_batch(batch) is True
    payload = bridge.payloads[0]
    assert 'reply_hints' in payload
    assert 'plugin_scheduled' not in payload


def test_emit_reply_sets_plugin_scheduled_for_follow_up_hints():
    batch_service = BatchingService(default_window_seconds=5, typing_extension_seconds=4)
    obs = make_obs()
    obs.follow_up_hints = ['You scheduled a reminder for 5 minutes: "drink water".']
    batch_service.add_message(obs)
    batch = batch_service.flush_ready(now=10.0)[0]
    bridge = FakeBridge()
    service = ConversationService(
        event_bridge=bridge,
        policy_service=FakePolicy(),
        prompt_context_service=FakeContext(),
        trace_repository=FakeTraceRepo(),
        settings_store=SettingsStore(),
        mention_map_service=FakeMentionMap(),
        cognitive_orchestrator=CognitiveOrchestrator(
            intent_engine=IntentEngine(goal_engine=GoalEngine()),
            world_state_builder=WorldStateBuilder(),
        ),
    )

    assert service.process_batch(batch) is True
    payload = bridge.payloads[0]
    assert payload.get('plugin_scheduled') == 'true'
    assert any('scheduled a reminder' in hint for hint in payload['reply_hints'])
