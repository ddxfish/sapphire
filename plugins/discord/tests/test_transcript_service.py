from plugins.discord.conversation.transcript_service import format_message_line, format_recent_history


def test_format_message_line_uses_the_best_name_and_caps_length():
    assert format_message_line({'author_name': 'Alice', 'content': 'look at\nthis'}) == 'Alice: look at this'
    assert format_message_line({'username': 'bob', 'content': ''}) == 'bob:'
    assert format_message_line({'content': 'x' * 50}, line_max_chars=20).endswith('…')


def test_format_recent_history_excludes_trigger_message():
    rows = [
        {'message_id': 'm1', 'author_name': 'Alice', 'content': 'hi'},
        {'message_id': 'm2', 'author_name': 'Alice', 'content': 'follow up'},
    ]
    assert format_recent_history(rows, exclude_message_id='m2') == ['Alice: hi']
