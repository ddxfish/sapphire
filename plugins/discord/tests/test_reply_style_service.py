from plugins.discord.conversation.reply_style_service import ReplyStyleService


def test_split_chunks_and_extract_inline_tags():
    service = ReplyStyleService(message_limit=40)
    parsed = service.parse_llm_output("""[react:🔥]
[gif:party]
First paragraph.

Second paragraph that is longer than the limit.""")

    assert parsed.reaction == "🔥"
    assert parsed.gif_query == "party"
    assert len(parsed.chunks) >= 2


def test_parse_llm_output_extracts_inline_gif_anywhere():
    service = ReplyStyleService(message_limit=200)
    parsed = service.parse_llm_output('Nice one [gif:party cat]')

    assert parsed.gif_query == 'party cat'
    assert '[gif:' not in '\n'.join(parsed.chunks)


def test_no_double_send_marker_blocks_auto_reply():
    service = ReplyStyleService(message_limit=200)
    service.mark_tool_sent('m1', 'already sent')

    assert service.should_skip_auto_reply('m1') is True
    assert service.consume_tool_sent_text('m1') == 'already sent'


def test_gif_dedupe_blocks_second_send():
    service = ReplyStyleService()
    service.mark_gif_sent('m1')
    assert service.gif_already_sent('m1') is True
    assert service.gif_already_sent('m2') is False
