from plugins.discord.conversation.reply_style_service import ReplyStyleService
from plugins.discord.conversation.think_tags import strip_think_tags


def test_strip_redacted_thinking_block():
    raw = """<think>
Internal reasoning here.
</think>
Hello world"""
    assert strip_think_tags(raw) == 'Hello world'


def test_strip_thinking_tag_variant():
    raw = '<thinking>plan</thinking>Visible reply'
    assert strip_think_tags(raw) == 'Visible reply'


def test_parse_llm_output_strips_thinking_by_default():
    service = ReplyStyleService()
    parsed = service.parse_llm_output(
        '<think>hidden</think>\n<@123> hey there'
    )
    assert parsed.chunks == ['<@123> hey there']


def test_parse_llm_output_can_keep_thinking_when_disabled():
    service = ReplyStyleService()
    raw = '<think>hidden</think>\nvisible'
    parsed = service.parse_llm_output(raw, strip_thinking=False)
    assert 'hidden' in parsed.chunks[0]


# ── hunt 2.13.0 row 16: the stripper never eats prose ──────────────────────
def test_think_tag_in_prose_survives():
    text = 'You can wrap reasoning in <think> tags like this. Models do it a lot.'
    assert strip_think_tags(text) == text


def test_think_block_shapes_still_stripped():
    assert strip_think_tags('<think>secret</think>Hello there') == 'Hello there'
    assert strip_think_tags('<think>cut off mid-thought') == ''
    assert strip_think_tags('leftover thinking</think>Answer.') == 'Answer.'


def test_backticked_think_tag_is_prose():
    text = 'Reasoning models emit `<think>` and `</think>` around their thoughts.'
    assert strip_think_tags(text) == text
