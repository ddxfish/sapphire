from plugins.discord.conversation.reply_style_service import ReplyStyleService
from plugins.discord.conversation.typing_indicator import contextual_wpm, typing_duration_seconds


def test_break_splits_into_separate_chunks():
    service = ReplyStyleService()
    parsed = service.parse_llm_output('First thought.[break]Second thought.')
    assert len(parsed.chunks) == 2
    assert parsed.chunks[0] == 'First thought.'
    assert parsed.chunks[1] == 'Second thought.'


def test_only_first_chunk_should_quote_reply_convention():
    service = ReplyStyleService()
    parsed = service.parse_llm_output('Part one[break]Part two[break]Part three')
    assert len(parsed.chunks) == 3


def test_contextual_wpm_in_human_range():
    samples = [contextual_wpm('hello there friend') for _ in range(20)]
    assert all(60 <= wpm <= 120 or wpm < 60 for wpm in samples)
    assert any(60 <= wpm <= 120 for wpm in samples)


def test_typing_duration_scales_with_length():
    short = typing_duration_seconds(20, text='quick reply')
    long = typing_duration_seconds(400, text='a longer thoughtful reply ' * 10)
    assert long > short
