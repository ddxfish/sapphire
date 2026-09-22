from plugins.discord.sapphire.continuity_payload import prepare_continuity_payload


def test_proactive_kind_appends_proactive_hint_to_recent_history():
    payload = {
        'content': 'Post a short good-morning message.',
        'recent_history': ['Alice: night all'],
        'proactive_kind': 'greeting',
    }

    prepared = prepare_continuity_payload(payload)

    assert prepared['recent_history'][:1] == ['Alice: night all']
    assert 'scheduled proactive post' in prepared['recent_history'][-1]
    assert 'reply_to_message_id' in prepared['recent_history'][-1]
    assert prepared['proactive_kind'] == 'greeting'


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
