from plugins.discord.conversation.transcript_service import format_message_line, format_recent_history


def test_format_message_line_annotates_image_only_message():
    line = format_message_line(
        {'author_name': 'Alice', 'content': ''},
        media_items=[{
            'media_kind': 'image',
            'interpretation': {'summary': 'A tabby cat reaching upward.'},
        }],
    )

    assert line == 'Alice: [sent image: A tabby cat reaching upward.]'


def test_format_message_line_appends_media_to_existing_text():
    line = format_message_line(
        {'author_name': 'Alice', 'content': 'look at this'},
        media_items=[{
            'media_kind': 'gif',
            'interpretation': {'summary': 'Animated fox waving hello.'},
        }],
    )

    assert line == 'Alice: look at this [GIF: Animated fox waving hello.]'


def test_format_recent_history_excludes_trigger_message():
    rows = [
        {'message_id': 'm1', 'author_name': 'Alice', 'content': 'hi'},
        {'message_id': 'm2', 'author_name': 'Alice', 'content': 'follow up'},
    ]
    media_by_message = {
        'm1': [{
            'media_kind': 'image',
            'interpretation': {'summary': 'A cat on a shelf.'},
        }],
    }

    lines = format_recent_history(rows, media_by_message, exclude_message_id='m2')

    assert lines == ['Alice: hi [image: A cat on a shelf.]']
