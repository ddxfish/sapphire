from plugins.discord.sapphire.continuity_payload import prepare_continuity_payload


def test_plugin_scheduled_appends_reminder_hint_to_recent_history():
    payload = {
        'content': 'remind me in 5 minutes',
        'recent_history': ['Alice: hello'],
        'plugin_scheduled': 'true',
    }

    prepared = prepare_continuity_payload(payload)

    assert prepared['recent_history'][:1] == ['Alice: hello']
    assert len(prepared['recent_history']) == 2
    assert 'already scheduled' in prepared['recent_history'][-1]
    assert 'cannot set reminders' in prepared['recent_history'][-1]
    assert prepared['plugin_scheduled'] == 'true'


def test_task_follow_up_appends_follow_up_hint_to_recent_history():
    payload = {
        'content': 'drink water',
        'recent_history': [],
        'task_follow_up': 'true',
    }

    prepared = prepare_continuity_payload(payload)

    assert len(prepared['recent_history']) == 1
    assert 'scheduled reminder follow-up' in prepared['recent_history'][0]
    assert '@mention' in prepared['recent_history'][0]
    assert 'reply_to_message_id' in prepared['recent_history'][0]
    assert prepared['task_follow_up'] == 'true'


def test_reply_instructions_appended_to_recent_history():
    payload = {
        'content': 'wake up',
        'recent_history': ['Bob: ping'],
        'reply_instructions': 'Alice woke you up after repeated mentions.',
    }

    prepared = prepare_continuity_payload(payload)

    assert prepared['recent_history'][:1] == ['Bob: ping']
    assert prepared['recent_history'][-1] == (
        'Reply instructions: Alice woke you up after repeated mentions.'
    )
    assert prepared['reply_instructions'] == 'Alice woke you up after repeated mentions.'


def test_reply_hints_used_when_reply_instructions_missing():
    payload = {
        'content': 'gif please',
        'reply_hints': ['Use a celebratory GIF.', 'Keep it short.'],
    }

    prepared = prepare_continuity_payload(payload)

    assert prepared['recent_history'][-1] == (
        'Reply instructions: Use a celebratory GIF.\n\nKeep it short.'
    )
    assert prepared['reply_hints'] == ['Use a celebratory GIF.', 'Keep it short.']
