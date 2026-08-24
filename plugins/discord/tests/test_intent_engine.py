from plugins.discord.cognition.intent_engine import IntentEngine
from plugins.discord.cognition.goal_engine import GoalEngine


def test_generates_conservative_reply_intention_for_high_activation_channel():
    goals = GoalEngine()
    engine = IntentEngine(goal_engine=goals)

    world_state = {
        'account_name': 'alpha',
        'channel_id': 'c1',
        'channel_name': 'general',
        'mentioned': True,
        'activation': 0.8,
    }

    intentions = engine.generate(world_state)

    assert intentions
    assert intentions[0].intention_type == 'reply_message'
    assert intentions[0].confidence <= 1.0


def test_low_activation_yields_no_intentions():
    engine = IntentEngine(goal_engine=GoalEngine())

    intentions = engine.generate({
        'account_name': 'alpha',
        'channel_id': 'c1',
        'mentioned': False,
        'name_matched': False,
        'respond_trigger': False,
        'activation': 0.05,
    })

    assert intentions == []


def test_maintain_relationships_does_not_reply_without_trigger_in_integrated_mode():
    engine = IntentEngine(goal_engine=GoalEngine())
    settings = type('S', (), {'cognitive': type('C', (), {'mode': 'integrated', 'affect_modulation_enabled': True})()})()

    intentions = engine.generate({
        'account_name': 'alpha',
        'channel_id': 'c1',
        'mentioned': False,
        'name_matched': False,
        'respond_trigger': False,
        'activation': 0.9,
        'is_dm': False,
    }, settings=settings)

    assert intentions == []


def test_expressive_mode_allows_high_activation_organic_reply():
    engine = IntentEngine(goal_engine=GoalEngine())
    settings = type('S', (), {'cognitive': type('C', (), {'mode': 'expressive', 'affect_modulation_enabled': True})()})()

    intentions = engine.generate({
        'account_name': 'alpha',
        'channel_id': 'c1',
        'mentioned': False,
        'name_matched': False,
        'respond_trigger': False,
        'activation': 0.9,
        'is_dm': False,
    }, settings=settings)

    assert intentions
    assert intentions[0].reason == 'high_activation'


def test_name_match_triggers_reply_like_mention():
    engine = IntentEngine(goal_engine=GoalEngine())

    intentions = engine.generate({
        'account_name': 'alpha',
        'channel_id': 'c1',
        'mentioned': False,
        'name_matched': True,
        'respond_trigger': True,
        'activation': 0.1,
    })

    assert intentions
    assert intentions[0].reason == 'name_matched'


def test_task_follow_up_event_payload_passes_mention_filter():
    engine = IntentEngine(goal_engine=GoalEngine())
    settings = type('S', (), {'cognitive': type('C', (), {'task_follow_up_enabled': True})()})()
    task = {
        'id': 42,
        'task_type': 'reminder_follow_up',
        'target_id': 'c1',
        'payload_json': {
            'user_id': 'u1',
            'username': 'alice',
            'display_name': 'Alice',
            'reminder': 'drink water',
            'when_label': 'in 5 minutes',
            'instruction': 'Remind Alice to drink water.',
        },
    }
    intention = engine.generate_task_follow_up({'account_name': 'alpha', 'channel_id': 'c1'}, task, settings=settings)
    payload = intention.metadata['event_payload']
    assert payload['mentioned'] == 'true'
    assert payload['task_follow_up'] == 'true'
    assert payload['task_id'] == '42'
    assert payload['reminder'] == 'drink water'
