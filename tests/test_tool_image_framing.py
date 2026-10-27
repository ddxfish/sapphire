"""A tool's image reaches a vision model framed as hers to see (2026-10-27).

The old note said only "already been shown to the user"; Sapphire, handed her
own browser screenshot on a vision model, read it as not-for-her and told the
user she can't see images. The brake against re-calling the tool stays.
"""
from core.chat.chat import _inject_tool_images


class Vision:
    supports_images = True


class Blind:
    supports_images = False


def test_a_vision_model_is_told_it_is_seeing_the_image():
    msgs = []
    _inject_tool_images(msgs, [{'data': 'AAAA', 'media_type': 'image/jpeg'}], Vision())
    note = msgs[-1]['content'][0]['text']
    assert 'You are seeing them now' in note
    assert 'do not call the tool again' in note and 'NOT a message from the user' in note
    assert msgs[-1]['content'][1]['type'] == 'image'


def test_a_blind_model_still_gets_the_honest_placeholder():
    msgs = []
    _inject_tool_images(msgs, [{'data': 'AAAA'}], Blind())
    assert 'does not support image inputs' in msgs[-1]['content']
