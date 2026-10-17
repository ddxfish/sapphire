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


# ── hunt 2.13.0 row 34 + wave E: code fences survive chunking ──────────────
def test_prose_backticks_do_not_carry_a_fence():
    chunks = ['To fence code you type ``` before it.', 'Then your code.', "That's it!"]
    assert ReplyStyleService._rebalance_fences(chunks) == chunks


def test_real_fence_cut_by_a_chunk_is_closed_and_reopened():
    out = ReplyStyleService._rebalance_fences(['```py\nprint(1)', 'print(2)\n```'])
    assert out[0].endswith('\n```') and out[1].startswith('```\n')


def test_chunker_closes_and_reopens_code_fences():
    svc = ReplyStyleService.__new__(ReplyStyleService)
    svc.message_limit = 60
    body = '```py\n' + '\n'.join(f'line {i} of code' for i in range(12)) + '\n```'
    chunks = svc._split_message('here is code:\n\n' + body)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert chunk.count('```') % 2 == 0, chunk
    assert chunks[1].startswith('```')


# ── 2026-09-25: a wall of '...' paragraphs dripped out as ~1500 messages ────
from plugins.discord.conversation.reply_style_service import MAX_MESSAGES_PER_REPLY


def test_wall_of_dots_is_dropped():
    svc = ReplyStyleService()
    wall = "\n\n".join(["..."] * 30 + ["We", "......", "Sorry..."] + ["..."] * 30)
    assert svc.parse_llm_output(wall).chunks == ["We", "Sorry..."]


def test_lone_pause_and_emoji_replies_survive():
    svc = ReplyStyleService()
    assert svc.parse_llm_output("...").chunks == ["..."]
    assert svc.parse_llm_output("?!").chunks == ["?!"]
    assert svc.parse_llm_output("🔥🔥🔥").chunks == ["🔥🔥🔥"]
    assert svc.parse_llm_output("hey\n\n...\n\nanyway").chunks == ["hey", "anyway"]


def test_long_wall_with_no_words_sends_nothing():
    assert ReplyStyleService().parse_llm_output("." * 500).chunks == []
    assert ReplyStyleService().parse_llm_output("...\n\n...").chunks == []


def test_paragraph_flood_is_packed_under_the_cap():
    svc = ReplyStyleService(message_limit=1900)
    flood = "\n\n".join(f"line {i}" for i in range(60))
    chunks = svc.parse_llm_output(flood).chunks
    assert len(chunks) <= MAX_MESSAGES_PER_REPLY
    assert "\n\n".join(chunks) == flood


def test_eight_paragraphs_still_post_one_by_one():
    text = "\n\n".join(f"para {i}" for i in range(MAX_MESSAGES_PER_REPLY))
    assert ReplyStyleService().parse_llm_output(text).chunks == text.split("\n\n")


def test_long_code_dump_keeps_every_byte_past_the_cap():
    svc = ReplyStyleService(message_limit=100)
    text = "\n\n".join("x" * 90 for _ in range(20))
    chunks = svc.parse_llm_output(text).chunks
    assert len(chunks) == 20 and all(len(c) <= 100 for c in chunks)
