from plugins.discord.conversation.media_prompt import build_reply_content, format_media_context_for_reply


def test_format_media_context_for_vision_source():
    block = format_media_context_for_reply([
        {
            'media_kind': 'image',
            'interpretation': {
                'summary': 'A cat on a windowsill.',
                'source': 'vision',
            },
        }
    ])

    assert 'automated vision description' in block
    assert 'A cat on a windowsill.' in block
    assert 'do not say you cannot see' in block


def test_build_reply_content_prepends_vision_for_image_only_message():
    content = build_reply_content('', [
        {
            'media_kind': 'image',
            'interpretation': {
                'summary': 'A blurry selfie in a car.',
                'source': 'vision',
            },
        }
    ])

    assert 'automated vision description' in content
    assert 'with no caption' in content


def test_build_reply_content_adds_attachment_note_when_user_also_wrote_text():
    content = build_reply_content('what do you think?', [
        {
            'media_kind': 'gif',
            'interpretation': {
                'summary': 'Animated fox waving hello.',
                'source': 'vision',
            },
        }
    ])

    assert 'what do you think?' in content
    assert 'also attached media' in content
