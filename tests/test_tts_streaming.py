"""Tests for core.tts.streaming.chunkify_for_speech.

Pure-function tests — no Sapphire integration. Focused on:
- Sentence boundary detection (.!? + capital lookahead)
- Block-level stripping that survives token-split tags
- Edge cases the plan calls out (Dr./3:45/URLs, decimals, ellipsis)
- Pause-after-ms metadata for the player
"""
import pytest

from core.tts.streaming import SpeechChunker, chunkify_for_speech, PAUSE_AFTER_MS


def _collect(tokens, min_chars=3, split_mode="sentence", **kw):
    """Run chunkify and collect into a list. Default min_chars=3 here so
    short test strings actually exercise split logic — production default
    is 15 (verified separately in min-char guard tests).

    Defaults to split_mode='sentence' because the tests in this file
    target the chunker's sentence-detection logic (Dr./3:45/ellipsis/etc.).
    Production default is 'paragraph' via TTS_STREAMING_SPLIT_MODE setting;
    paragraph-mode behavior is covered separately in stream_pump tests."""
    return list(chunkify_for_speech(iter(tokens), min_chars=min_chars,
                                    split_mode=split_mode, **kw))


# ─── Sentence detection — basic cases ─────────────────────────────────────────

def test_single_sentence_emits_one_chunk():
    chunks = _collect(["Hello there."])
    assert len(chunks) == 1
    assert chunks[0]["text"] == "Hello there."
    assert chunks[0]["boundary"] == "end"
    assert chunks[0]["index"] == 0


def test_two_sentences_emit_two_chunks():
    chunks = _collect(["Hi. How are you?"])
    assert len(chunks) == 2
    assert chunks[0]["text"] == "Hi."
    assert chunks[0]["boundary"] == "sentence"
    assert chunks[1]["text"] == "How are you?"
    assert chunks[1]["boundary"] == "end"


def test_three_sentence_cascade():
    chunks = _collect(["Yes! No? Maybe."])
    assert [c["text"] for c in chunks] == ["Yes!", "No?", "Maybe."]
    assert [c["boundary"] for c in chunks] == ["sentence", "sentence", "end"]


def test_indices_are_zero_based_and_sequential():
    chunks = _collect(["One. Two. Three. Four."])
    assert [c["index"] for c in chunks] == [0, 1, 2, 3]


# ─── Negative sentence-detection (the "don't split" cases) ────────────────────

def test_dr_smith_does_not_split():
    chunks = _collect(["Dr. Smith arrived early."])
    # Acceptable failure per plan: this DOES split (period+space+capital).
    # If we ever upgrade to abbreviation-aware detection, this test changes.
    # Document current behavior:
    assert len(chunks) == 2
    assert chunks[0]["text"] == "Dr."
    assert chunks[1]["text"] == "Smith arrived early."


def test_decimal_does_not_split():
    chunks = _collect(["Pi is 3.14 approximately."])
    assert len(chunks) == 1
    assert chunks[0]["text"] == "Pi is 3.14 approximately."


def test_time_with_colon_no_split_mid_sentence():
    # "3:45 PM" — colon followed by space + uppercase. _SECONDARY_RE
    # would normally split there, but only if chunk is already past the
    # secondary-split threshold. Short sentence stays one chunk.
    chunks = _collect(["Meeting at 3:45 PM today."])
    assert len(chunks) == 1
    assert chunks[0]["text"] == "Meeting at 3:45 PM today."


def test_url_does_not_split():
    chunks = _collect(["Visit https://example.com for info."])
    assert len(chunks) == 1
    assert "example.com" not in chunks[0]["text"]       # never spoken (hunt 21)
    assert chunks[0]["text"].startswith("Visit") and chunks[0]["text"].endswith("for info.")


def test_streamed_markdown_and_tool_calls_are_never_spoken():
    """Hunt 21 (proven): cleanup ran on token-sized pieces, so links, table
    rows, inline code and text-format tool calls were read aloud."""
    from core.tts.streaming import SpeechChunker

    def run(text, step=4):
        c = SpeechChunker(min_chars=1, split_mode='sentence')
        out = []
        for i in range(0, len(text), step):
            out += c.push(text[i:i + step])
        return ' | '.join(x['text'] for x in out + c.flush())
    assert run("See [the guide](https://example.com/p?x=1) for more. Done.") == "See the guide for more. | Done."
    assert 'Alice' not in run("Here:\n\n| Name | Age |\n|---|---|\n| Alice | 30 |\n\nThat's it.")
    assert 'pip' not in run("Run `pip install sapphire-core` now. Okay.")
    said = run('Looking. <tool_call>{"name": "web_search", "arguments": {"query": "home address"}}</tool_call> Found it.')
    assert 'address' not in said and 'Found it.' in said
    assert 'boston' not in run('Checking. <function=web_search><parameter=query>boston</parameter></function> Sunny.')


def test_lowercase_after_period_splits_via_casual_rule():
    # 2026-05-20: casual sentence boundary added. Sapphire's casual register
    # frequently produces "Hello world. and then more text." — previously
    # held as one chunk (which delayed first-audio for the whole reply).
    # Now splits via _CASUAL_SENTENCE_RE since the word before the period
    # is ≥4 chars (passing the abbreviation guard).
    chunks = _collect(["Hello world. and then more text."])
    assert len(chunks) == 2
    assert chunks[0]["text"] == "Hello world."
    assert chunks[1]["text"] == "and then more text."


def test_short_word_period_does_not_split_lowercase():
    # Abbreviation guard: "Mr." has only 2 chars before the period, so the
    # casual rule's 4-char lookbehind blocks the split. "Mr. smith" stays
    # together even though "smith" is lowercase.
    chunks = _collect(["Hi from Mr. smith who is here."])
    assert len(chunks) == 1
    assert "Mr. smith" in chunks[0]["text"]


# ─── Block tag stripping ──────────────────────────────────────────────────────

def test_think_block_stripped():
    chunks = _collect(["<think>reasoning here</think>Hello there."])
    assert len(chunks) == 1
    assert chunks[0]["text"] == "Hello there."


def test_reasoning_block_stripped():
    chunks = _collect(["<reasoning>plans</reasoning>Hi there."])
    assert len(chunks) == 1
    assert chunks[0]["text"] == "Hi there."


def test_code_fence_stripped():
    chunks = _collect(["Hi.\n```\ncode block\nstuff\n```\nBye."])
    texts = [c["text"] for c in chunks]
    assert texts == ["Hi.", "Bye."]


def test_inline_code_stripped():
    chunks = _collect(["Use `foo()` to call. It works."])
    assert len(chunks) == 2
    # "foo()" is replaced with space; sentence retains structure
    assert "foo()" not in chunks[0]["text"]


def test_html_tag_stripped():
    chunks = _collect(["Hello <b>bold</b> world. Next."])
    assert len(chunks) == 2
    assert "<b>" not in chunks[0]["text"]
    assert "</b>" not in chunks[0]["text"]


def test_only_think_tags_emits_nothing():
    chunks = _collect(["<think>thinking thinking thinking</think>"])
    assert chunks == []


def test_only_code_block_emits_nothing():
    chunks = _collect(["```python\nprint('hi')\n```"])
    assert chunks == []


# ─── Streaming behavior — tag/text split across tokens ────────────────────────

def test_partial_think_tag_across_tokens():
    chunks = _collect(["<thi", "nk>secret reasoning</think>", "Hi there."])
    assert len(chunks) == 1
    assert chunks[0]["text"] == "Hi there."
    assert "secret" not in chunks[0]["text"]


def test_partial_close_tag_across_tokens():
    chunks = _collect(["<think>plans</thi", "nk>", "Real text."])
    assert len(chunks) == 1
    assert chunks[0]["text"] == "Real text."


def test_partial_code_fence_across_tokens():
    chunks = _collect(["``", "`\nprint('hi')\n``", "`\nBye."])
    # Code fence stripped; final "Bye." emits
    assert len(chunks) == 1
    assert chunks[0]["text"] == "Bye."


def test_one_char_at_a_time_still_chunks_correctly():
    tokens = list("Hi. How are you today?")
    chunks = _collect(tokens)
    assert len(chunks) == 2
    assert chunks[0]["text"] == "Hi."
    assert chunks[1]["text"] == "How are you today?"


def test_one_char_at_a_time_with_think_block():
    s = "<think>foo</think>Hello. World."
    chunks = _collect(list(s))
    texts = [c["text"] for c in chunks]
    assert texts == ["Hello.", "World."]


# ─── Boundary types + pause metadata ──────────────────────────────────────────

def test_paragraph_break_splits():
    chunks = _collect(["First para text.\n\nSecond para text."])
    assert len(chunks) == 2
    # The paragraph boundary fires first (before the period of "text.")
    assert chunks[0]["boundary"] == "paragraph"
    assert chunks[0]["text"] == "First para text."
    assert chunks[1]["text"] == "Second para text."


def test_ellipsis_splits_like_period_in_sentence_mode():
    # `...` has no special boundary — the sentence regex treats its final
    # dot like any period. Dots stay in the text for Kokoro. 2026-06-11.
    chunks = _collect(["Hmm... Maybe later."])
    assert len(chunks) == 2
    assert chunks[0]["text"] == "Hmm..."
    assert chunks[0]["boundary"] == "sentence"
    assert chunks[0]["pause_after_ms"] == PAUSE_AFTER_MS["sentence"]


def test_ellipsis_does_not_split_in_paragraph_mode():
    # Paragraph mode doesn't split on periods, so `...` rides through as
    # one chunk — Kokoro renders the trail-off from the dots. 2026-06-11.
    chunks = _collect(["Fishy... You know I like pizza."], split_mode="paragraph")
    assert len(chunks) == 1
    assert chunks[0]["text"] == "Fishy... You know I like pizza."


def test_sentence_boundary_pause():
    chunks = _collect(["Hello. World."])
    assert chunks[0]["pause_after_ms"] == PAUSE_AFTER_MS["sentence"]
    assert chunks[1]["pause_after_ms"] == PAUSE_AFTER_MS["end"]


def test_paragraph_pause_metadata():
    chunks = _collect(["Para one.\n\nPara two."])
    assert chunks[0]["pause_after_ms"] == PAUSE_AFTER_MS["paragraph"]


def test_tiny_paragraph_merges_forward_not_own_chunk():
    # A 1-word paragraph ("Oh-") mid-reply must NOT become its own chunk —
    # Kokoro would render it out of context and it adds a stray pause. It
    # merges FORWARD into the next paragraph. 2026-05-28.
    chunks = _collect(
        ["Oh-\n\nHere is a much longer paragraph with real content to speak."],
        min_chars=15, split_mode="paragraph",
    )
    texts = [c["text"] for c in chunks]
    assert "Oh-" not in texts, f"tiny paragraph leaked as its own chunk: {texts}"
    assert any("Oh-" in t and "longer paragraph" in t for t in texts), texts


def test_short_whole_reply_still_renders():
    # A whole short reply ("Sure") is below min_chars and has no boundary, but
    # flush() must still emit it at end-of-stream — min_chars only gates
    # MID-STREAM splitting, never the final flush. 2026-05-28.
    chunks = _collect(["Sure"], min_chars=15, split_mode="paragraph")
    assert [c["text"] for c in chunks] == ["Sure"]


def test_two_tiny_paragraphs_both_merge_forward():
    # Several tiny paragraphs in a row accumulate until a chunk reaches
    # min_chars rather than each becoming its own out-of-context fragment.
    chunks = _collect(
        ["Oh-\n\nHm.\n\nWell, here is the actual substantive reply content."],
        min_chars=15, split_mode="paragraph",
    )
    texts = [c["text"] for c in chunks]
    assert "Oh-" not in texts and "Hm." not in texts, texts
    assert any("substantive reply" in t for t in texts), texts


# ─── Max-char fallback ───────────────────────────────────────────────────────

def test_max_char_fallback_splits_long_no_punctuation():
    # Build a 300-char string with no terminal punctuation
    text = " ".join(["filler"] * 50) + " more text without punctuation here"
    assert len(text) > 200
    chunks = _collect([text], max_chars=200, min_chars=15)
    assert len(chunks) >= 2
    # First chunk should be maxlen (no sentence boundary) and <= max_chars
    assert chunks[0]["boundary"] == "maxlen"
    assert len(chunks[0]["text"]) <= 200


def test_max_char_fallback_splits_on_whitespace():
    text = "word " * 50  # 250 chars, no terminal punctuation
    chunks = _collect([text], max_chars=200, min_chars=15)
    assert chunks[0]["boundary"] == "maxlen"
    # Should split on whitespace, not mid-word
    assert not chunks[0]["text"].endswith("wor")
    assert chunks[0]["text"].endswith("word")


# ─── Min-char guard ───────────────────────────────────────────────────────────

def test_short_chunks_dont_emit_mid_stream():
    # "Hi." alone is 3 chars — below min_chars. Should wait for more.
    chunks_during = list(chunkify_for_speech(iter(["Hi."]), min_chars=15))
    # At end of stream, we emit whatever's left regardless of min_chars
    assert len(chunks_during) == 1
    assert chunks_during[0]["text"] == "Hi."
    assert chunks_during[0]["boundary"] == "end"


def test_short_sentence_emits_at_stream_end_only():
    # Pieced: send "Hi." token, then nothing. Should only emit at end.
    # We can't easily distinguish "during stream" emit vs "end" emit
    # without instrumenting — proxy by checking boundary type.
    chunks = list(chunkify_for_speech(iter(["Hi."]), min_chars=20))
    assert len(chunks) == 1
    assert chunks[0]["boundary"] == "end"


# ─── Quote handling ───────────────────────────────────────────────────────────

def test_quote_after_period_stays_with_sentence():
    chunks = _collect(["She said 'Yes.' Then she left."])
    assert len(chunks) == 2
    assert chunks[0]["text"] == "She said 'Yes.'"
    assert chunks[1]["text"] == "Then she left."


def test_double_quote_after_period():
    chunks = _collect(['He shouted "Now!" Then ran.'])
    assert len(chunks) == 2
    assert chunks[0]["text"] == 'He shouted "Now!"'


# ─── Empty / degenerate input ─────────────────────────────────────────────────

def test_empty_stream_emits_nothing():
    assert _collect([]) == []


def test_only_whitespace_emits_nothing():
    assert _collect(["   \n\t  "]) == []


def test_empty_tokens_ignored():
    chunks = _collect(["", "Hi.", "", " World.", ""])
    texts = [c["text"] for c in chunks]
    assert texts == ["Hi.", "World."]


# ─── Markdown handling ────────────────────────────────────────────────────────

def test_markdown_link_keeps_text():
    chunks = _collect(["See [the docs](https://example.com) for info."])
    assert len(chunks) == 1
    assert "the docs" in chunks[0]["text"]
    assert "example.com" not in chunks[0]["text"]


def test_bold_markers_stripped():
    chunks = _collect(["This is **important** text."])
    assert len(chunks) == 1
    assert "**" not in chunks[0]["text"]
    assert "important" in chunks[0]["text"]


def test_table_stripped():
    chunks = _collect(["Before table.\n| a | b |\n| 1 | 2 |\nAfter table."])
    texts = [c["text"] for c in chunks]
    # Table content should be gone; "Before" and "After" remain
    full = " ".join(texts)
    assert "Before table" in full
    assert "After table" in full
    assert "| a |" not in full


# ─── Whitespace normalization ─────────────────────────────────────────────────

def test_whitespace_collapsed_in_chunks():
    chunks = _collect(["Lots   of    spaces.    Next."])
    assert chunks[0]["text"] == "Lots of spaces."
    assert chunks[1]["text"] == "Next."


# ─── Sanity: structural fields always present ─────────────────────────────────

def test_chunk_shape():
    chunks = _collect(["Hello. World."])
    for c in chunks:
        assert isinstance(c, dict)
        assert set(c.keys()) == {"text", "boundary", "pause_after_ms", "index"}
        assert isinstance(c["text"], str)
        assert isinstance(c["boundary"], str)
        assert isinstance(c["pause_after_ms"], int)
        assert isinstance(c["index"], int)


# ─── SpeechChunker push/flush API (used by chat_streaming for live LLM) ──────

def test_chunker_push_then_flush():
    """Push-based API: same chunks as the generator function. Sentence mode
    because we're testing sentence-boundary splitting specifically."""
    chunker = SpeechChunker(min_chars=3, split_mode="sentence")
    out = []
    for tok in ["Hello", " there", ". Next", " up."]:
        out.extend(chunker.push(tok))
    out.extend(chunker.flush())
    assert [c["text"] for c in out] == ["Hello there.", "Next up."]


def test_chunker_yields_during_push_not_just_flush():
    """When a sentence boundary fully resolves mid-stream (i.e. the next
    capital arrives so the lookahead succeeds), push() returns the chunk
    immediately rather than waiting for flush(). Sentence mode."""
    chunker = SpeechChunker(min_chars=3, split_mode="sentence")
    out_during = []
    out_during.extend(chunker.push("Hello there. "))  # lookahead missing
    out_during.extend(chunker.push("More text here."))  # 'M' satisfies lookahead
    # First sentence should have emerged during push (we never called flush)
    assert any(c["text"] == "Hello there." for c in out_during)


def test_chunker_indices_continuous_across_push_and_flush():
    chunker = SpeechChunker(min_chars=3)
    out = []
    out.extend(chunker.push("Hi. "))
    out.extend(chunker.push("How are you? "))
    out.extend(chunker.flush())
    indices = [c["index"] for c in out]
    assert indices == list(range(len(out)))


def test_chunker_partial_tag_across_pushes():
    """The push API must handle the same partial-tag-across-tokens cases
    as the generator function."""
    chunker = SpeechChunker(min_chars=3)
    out = []
    out.extend(chunker.push("<thi"))
    out.extend(chunker.push("nk>secret</think>"))
    out.extend(chunker.push("Hello there."))
    out.extend(chunker.flush())
    assert [c["text"] for c in out] == ["Hello there."]


def test_chunker_empty_stream_flushes_nothing():
    chunker = SpeechChunker()
    assert chunker.push("") == []
    assert chunker.flush() == []


def test_first_sentence_comes_early_in_paragraph_mode():
    # First audio waits on the first sentence, not the whole paragraph;
    # the rest of the paragraph stays one chunk for its prosody.
    chunks = _collect(["The lamp is lit tonight. The sea is calm. The gulls are quiet."],
                      split_mode="paragraph")
    assert [c["text"] for c in chunks] == ["The lamp is lit tonight.",
                                           "The sea is calm. The gulls are quiet."]
    assert chunks[0]["boundary"] == "sentence"


def test_first_sentence_too_short_merges_forward():
    # "Sure!" alone is under min_chars: the first chunk runs to the next end.
    chunks = _collect(["Sure! I can check that for you. It takes a moment. Then we see."],
                      split_mode="paragraph", min_chars=15)
    assert chunks[0]["text"] == "Sure! I can check that for you."
    assert chunks[1]["text"] == "It takes a moment. Then we see."


def test_first_sentence_never_ends_on_a_trail_off():
    chunks = _collect(["Well, that took a while... You know I like pizza. And the sea."],
                      split_mode="paragraph", min_chars=15)
    assert chunks[0]["text"] == "Well, that took a while... You know I like pizza."


def test_first_sentence_arrives_before_the_paragraph_ends():
    # Pushed token by token, the first chunk is out as soon as the second
    # sentence has begun — no wait for the paragraph break.
    c = SpeechChunker(split_mode="paragraph")
    out = []
    for tok in ["The lamp ", "is lit tonight. ", "The", " sea is calm"]:
        out += c.push(tok)
    assert [x["text"] for x in out] == ["The lamp is lit tonight."]


def _ramp(text, **kw):
    c = SpeechChunker(split_mode="paragraph", **kw)
    out = []
    for w in text.split(" "):
        out += c.push(w + " ")
    return [x["text"] for x in out + c.flush()]


def test_each_chunk_fits_what_2x_realtime_makes_meanwhile():
    # Chunk k <= first + all before it (+ separators): an engine at 2x
    # realtime always has it ready while the audio ahead plays. And it GROWS:
    # equal sentences must not stall at one per chunk.
    s = "The lamp turns slowly. "
    texts = _ramp(s * 30)
    assert texts[0] == s.strip()
    for k in range(1, len(texts) - 1):
        assert len(texts[k]) <= len(texts[0]) + sum(map(len, texts[:k])) + k, texts[k]
    assert len(texts) <= 6 and len(texts[2]) > len(texts[1]) > len(texts[0])


def test_ramp_keeps_a_long_sentence_whole():
    # No sentence end fits the cap: the next one is taken whole, never cut
    # mid-sentence.
    long = "This sentence runs on and on " + "and on " * 20 + "until it stops."
    texts = _ramp("Here is the opening line. " + long + " Then a short one.")
    assert texts[1] == long


def test_ramp_ends_once_chunks_reach_max_chars():
    # Past max_chars the cap no longer binds: paragraphs come whole again.
    para = "Waves fold over the rocks. " * 6
    texts = _ramp("Opening line is here now. " + para.strip() + "\n\n" + para.strip() + "\n\nEnd.",
                  max_chars=200)
    assert texts[-2] == para.strip()


def test_early_cuts_skip_abbreviations_initials_and_list_numbers():
    texts = _ramp("Yesterday I spoke with Dr. Smith about the results. They looked good. "
                  "We met at the U.S. Embassy after that. It went fine.")
    assert texts[0] == "Yesterday I spoke with Dr. Smith about the results."
    assert not any(t.endswith(("Dr.", "U.S.")) for t in texts)
    texts = _ramp("Here are the steps.\n1. First do this thing. 2. Then do that thing. 3. Rest.")
    assert not any(t.rstrip().endswith(("1.", "2.", "3.")) and len(t) < 30 for t in texts[:-1])


def test_ramp_takes_a_fitting_sentence_before_a_maxlen_cut():
    run_on = "So " + "and then we went on " * 40 + "end."
    texts = _ramp("Yes, I can help with that. Here is the deal for you. " + run_on)
    assert texts[:2] == ["Yes, I can help with that.", "Here is the deal for you."]


def test_ramp_follows_the_engine_speed():
    s = "The lamp turns slowly. " * 30

    def run(speed=None, ramp=True):
        c = SpeechChunker(split_mode="paragraph")
        c.speed, c.ramp = speed, ramp
        out = []
        for w in s.split(" "):
            out += c.push(w + " ")
        return [len(x["text"]) for x in out + c.flush()]

    fast, slow = run(4.0), run(1.5)
    assert fast[0] == slow[0] == 22                  # one sentence first either way
    assert fast[1] > slow[1]                         # a faster engine ramps faster
    assert len(run(1.1)) == 1 and len(run(ramp=False)) == 1   # too slow / off: one paragraph chunk
