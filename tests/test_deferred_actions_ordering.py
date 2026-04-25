"""Regression for 2026-04-25 user report:

Heap corruption (malloc(): invalid size SIGABRT) when wake word inits
during a multi-component boot batch — the web UI's first PUT
/api/settings/batch triggered toggle_wakeword + switch_stt_provider +
switch_tts_provider all in sequence on asyncio.to_thread workers.
Wake word's onnxruntime/audio init was fragile when other C extensions
had just initialized on possibly-different worker threads.

Manual single-toggle worked fine because nothing else was competing.
The fix sorts deferred_actions so wake word always runs LAST in a
batch — TTS/STT/embedding settle first, then wake word inits cleanly.

This test locks the ordering invariant. A future refactor that loses
the sort will fail this test before reintroducing the bug.
"""


def _sort_invariant(deferred_actions):
    """Replicate the sort applied in core/routes/settings.py:set_settings_batch
    so we can validate the invariant directly without spinning the route."""
    deferred_actions.sort(key=lambda a: a[0] == 'toggle_wakeword')
    return deferred_actions


def test_wakeword_sorts_to_end_when_present():
    """Mixed batch — wake word ends up last."""
    actions = [
        ('toggle_wakeword', True, 'WAKE_WORD_ENABLED', 'hot'),
        ('switch_tts_provider', 'kokoro', 'TTS_PROVIDER', 'hot'),
        ('switch_stt_provider', 'faster_whisper', 'STT_PROVIDER', 'hot'),
    ]
    sorted_actions = _sort_invariant(list(actions))
    assert sorted_actions[-1][0] == 'toggle_wakeword', (
        "toggle_wakeword must be the last deferred action so other C-extension "
        "inits (TTS/STT/embedding) settle before wake word's audio capture starts"
    )


def test_non_wakeword_relative_order_preserved():
    """Stable sort: TTS/STT/embedding keep their original ordering."""
    actions = [
        ('switch_embedding', 'voyage', 'EMBEDDING_PROVIDER', 'hot'),
        ('toggle_wakeword', True, 'WAKE_WORD_ENABLED', 'hot'),
        ('switch_stt_provider', 'whisper', 'STT_PROVIDER', 'hot'),
        ('switch_tts_provider', 'kokoro', 'TTS_PROVIDER', 'hot'),
    ]
    sorted_actions = _sort_invariant(list(actions))
    non_wake = [a for a in sorted_actions if a[0] != 'toggle_wakeword']
    expected_order = ['switch_embedding', 'switch_stt_provider', 'switch_tts_provider']
    assert [a[0] for a in non_wake] == expected_order, (
        f"Non-wakeword actions must keep relative order. Got: "
        f"{[a[0] for a in non_wake]}, expected: {expected_order}"
    )


def test_no_wakeword_passes_through_unchanged():
    """Batch without wake word — order is unchanged."""
    actions = [
        ('switch_tts_provider', 'kokoro', 'TTS_PROVIDER', 'hot'),
        ('switch_stt_provider', 'whisper', 'STT_PROVIDER', 'hot'),
        ('switch_embedding', 'voyage', 'EMBEDDING_PROVIDER', 'hot'),
    ]
    original = list(actions)
    sorted_actions = _sort_invariant(list(actions))
    assert sorted_actions == original


def test_wakeword_only_unchanged():
    """Batch with only wake word — single entry, sort is a no-op."""
    actions = [('toggle_wakeword', True, 'WAKE_WORD_ENABLED', 'hot')]
    assert _sort_invariant(list(actions)) == actions


def test_multiple_wakeword_entries_all_sort_to_end():
    """Defensive — if WAKE_WORD_ENABLED somehow appears twice (unusual but
    not impossible if a client sends duplicates), all wakeword entries
    still drop to the end. Doesn't break correctness."""
    actions = [
        ('toggle_wakeword', False, 'WAKE_WORD_ENABLED', 'hot'),
        ('switch_tts_provider', 'kokoro', 'TTS_PROVIDER', 'hot'),
        ('toggle_wakeword', True, 'WAKE_WORD_ENABLED', 'hot'),
    ]
    sorted_actions = _sort_invariant(list(actions))
    assert sorted_actions[0][0] == 'switch_tts_provider'
    assert sorted_actions[1][0] == 'toggle_wakeword'
    assert sorted_actions[2][0] == 'toggle_wakeword'


def test_empty_list_safe():
    """No actions — sort is a no-op, doesn't raise."""
    actions = []
    _sort_invariant(actions)
    assert actions == []


def test_sort_invariant_matches_route_implementation():
    """Cross-check: the sort key in this test file MUST match the one in
    core/routes/settings.py. If someone changes the route's sort logic
    (different key, different ordering rule), this test fails — pulling
    attention to whether the regression risk has actually been preserved."""
    from pathlib import Path
    src = (Path(__file__).parent.parent / "core" / "routes" / "settings.py").read_text(encoding='utf-8')
    expected = "deferred_actions.sort(key=lambda a: a[0] == 'toggle_wakeword')"
    assert expected in src, (
        f"core/routes/settings.py no longer contains the wake-word-last sort. "
        f"Expected literal: {expected!r}. If the implementation changed shape "
        f"(e.g., to a list comprehension or explicit reorder), update this test "
        f"and the test_wakeword_sorts_to_end_when_present test to match. "
        f"Don't just remove the assertion — the boot-race regression it "
        f"prevents is real and reproducible."
    )
