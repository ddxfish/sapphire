"""core.think — the ONE reader of thinking blocks (2026-09-27).

The contract every lane now shares. Cases are carried over from the parsers
this replaced (history extract, the scheduler's greedy strip, cadence, the
Discord plugin's backtick rule) plus the two shapes that broke in the wild:
a typed tag inside thinking, and prose between two think blocks.
"""
import pytest

from core import think


# ── rule 2: a block ──────────────────────────────────────────────────────────

def test_a_block_is_split_from_the_answer():
    assert think.split('<think>Let me reason.</think>The answer is 42.') == ('The answer is 42.', 'Let me reason.')
    assert think.strip('<think>\nInternal reasoning here.\n</think>\nHello world') == 'Hello world'
    assert think.strip('<think>hm</think> Nice wave.') == 'Nice wave.'


@pytest.mark.parametrize('name', ['think', 'seed:think', 'thinking', 'redacted_thinking', 'reasoning'])
def test_every_tag_name_is_known(name):
    assert think.split(f'<{name}>plan</{name}>Visible reply') == ('Visible reply', 'plan')
    assert think.split(f'<{name.upper()}>plan</{name.upper()}>Visible reply') == ('Visible reply', 'plan')


def test_attributes_and_spacing_in_the_tag():
    assert think.strip('<think type="deep">x</think >Answer') == 'Answer'
    assert think.strip('<thinker>not a tag</thinker>') == '<thinker>not a tag</thinker>'


def test_several_blocks_keep_the_prose_between_them():
    """One think block per tool round: the old first-opener-to-last-closer
    sweep ate 'Middle text'."""
    text = '<think>First thought</think>Middle text <think>Second thought</think>Final answer'
    answer, thinking = think.split(text)
    assert answer == 'Middle text Final answer'
    assert thinking == 'First thought\n\nSecond thought'


def test_seed_budget_note_inside_thinking():
    text = '<seed:think>plan<seed:cot_budget_reflect>used 40</seed:cot_budget_reflect>more plan</seed:think>Hi'
    answer, thinking = think.split(text)
    assert answer == 'Hi' and 'plan' in thinking and 'more plan' in thinking


# ── rule 3: a closer with nothing open (greedy, fenced) ──────────────────────

def test_lost_opener_means_the_head_was_thinking():
    assert think.split('Still thinking...</think>Here is the real answer.') == ('Here is the real answer.',
                                                                                  'Still thinking...')
    assert think.strip('tail of a block</think>Real words.') == 'Real words.'


def test_early_close_is_folded_into_the_thinking():
    """GLM: <think>A</think>B</think>C — B was thinking too."""
    assert think.split('<think>A</think>B</think>C') == ('C', 'A\n\nB')


def test_greedy_never_reaches_across_a_block():
    text = 'Answer one. <think>a</think>Answer two. <think>b</think>stray</think>Answer three.'
    answer, thinking = think.split(text)
    assert answer == 'Answer one. Answer two. Answer three.'
    assert thinking == 'a\n\nb\n\nstray'


# ── rule 4: an opener that never closes ──────────────────────────────────────

def test_unclosed_opener_at_a_block_start_is_thinking():
    assert think.split('<think>cut off mid-thought') == ('', 'cut off mid-thought')
    assert think.split('  <think>never closes and keeps going') == ('', 'never closes and keeps going')
    assert think.split("Some answer.<think>I'm still working on this") == ('Some answer.', "I'm still working on this")
    assert think.split('Done.\n<think>more') == ('Done.', 'more')


def test_unclosed_opener_mid_sentence_is_a_word():
    text = 'You can wrap reasoning in <think> tags like this. Models do it a lot.'
    assert think.split(text) == (text, '')


# ── rule 1: typed tags are words ─────────────────────────────────────────────

def test_backticked_tags_are_words():
    text = 'Reasoning models emit `<think>` and `</think>` around their thoughts.'
    assert think.split(text) == (text, '')


def test_a_typed_tag_inside_thinking_does_not_split_it():
    """The shape that broke the chat page 2026-09-26: she quoted the tag
    while thinking about a caption that carried one."""
    text = ('<think>The image came with two things:\nA `<think>` block and a description. '
            'It even has a `</think>` in it.</think>\n\nYes, I can see the full caption.')
    answer, thinking = think.split(text)
    assert answer == 'Yes, I can see the full caption.'
    assert thinking.startswith('The image came with two things:') and thinking.endswith('in it.')
    assert [kind for kind, _, _ in think.segments(text)] == ['think', 'text']


def test_tags_inside_a_code_fence_of_the_answer_are_words():
    text = 'The format is:\n```\n<think>reasoning</think>\n```\nThat is all.'
    assert think.split(text) == (text, '')
    after = '<think>plan</think>Format:\n```xml\n<think>x</think>\n```\nDone.'
    assert think.split(after) == ('Format:\n```xml\n<think>x</think>\n```\nDone.', 'plan')


def test_a_fence_inside_thinking_does_not_hide_the_closer():
    text = '<think>I will write:\n```python\nprint(1)\n```\nok</think>Here it is.'
    assert think.split(text) == ('Here it is.', 'I will write:\n```python\nprint(1)\n```\nok')


# ── the edges ────────────────────────────────────────────────────────────────

def test_empty_none_and_plain():
    assert think.split('') == ('', '') and think.split(None) == (None, '')
    assert think.strip(None) == '' and think.strip('') == ''
    assert think.split('Just a plain answer.') == ('Just a plain answer.', '')
    assert think.split('<think>Only thinking here</think>') == ('', 'Only thinking here')
    assert think.thinking('<think>a</think>b') == 'a'


def test_has():
    assert think.has('<think>a</think>b') and think.has('lost</think>b') and think.has('<think>open')
    assert not think.has('plain') and not think.has('a `<think>` tag') and not think.has(None)


def test_segments_are_in_order_with_their_tag():
    segs = think.segments('Hi <seed:think>a</seed:think>there <think>b</think>end')
    assert segs == [('text', 'Hi ', ''), ('think', 'a', 'seed:think'), ('text', 'there ', ''),
                    ('think', 'b', 'think'), ('text', 'end', '')]


def test_wrap_is_the_one_form_and_round_trips():
    assert think.wrap('plan', 'Answer') == '<think>plan</think>\n\nAnswer'
    assert think.wrap('plan') == '<think>plan</think>' and think.wrap('', 'Answer') == 'Answer'
    assert think.wrap(None, None) == ''
    for thinking, answer in [('plan', 'Answer'), ('a\n\nb', 'Two\n\nparas'), ('only', '')]:
        assert think.split(think.wrap(thinking, answer)) == (answer, thinking)


def test_only_keeps_thinking_and_never_cuts_a_reply():
    assert think.only('<think>plan</think>I will now generate the image.') == '<think>plan</think>'
    assert think.only('<think>a</think>x<think>b</think>y') == '<think>a\n\nb</think>'
    assert think.only('No tags, just prose.') == '<think>No tags, just prose.</think>'
    assert think.only('') == '' and think.only(None) == ''


def test_unfinished_counts_only_the_open_block():
    assert think.unfinished('<think>abc') == 3
    assert think.unfinished('<think>abc</think>done') == 0
    assert think.unfinished('plain') == 0 and think.unfinished(None) == 0
    assert think.unfinished('<think>a</think><think>bcd') == 3
    assert think.unfinished('a `<think>` tag typed') == 0


def test_pairs_cover_every_opener():
    assert ('<think>', '</think>') in think.PAIRS and ('<reasoning>', '</reasoning>') in think.PAIRS
    assert len(think.PAIRS) == len(think.OPEN_NAMES)
    assert think.OPEN == '<think>' and think.CLOSE == '</think>'
