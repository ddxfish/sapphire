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
