"""Regression for 2026-04-25 user report:

Heap corruption (malloc(): invalid size SIGABRT) when wake word inits
during a multi-component boot batch — the web UI's first PUT
/api/settings/batch triggered toggle_wakeword + switch_stt_provider +
switch_tts_provider all in sequence on asyncio.to_thread workers.
Wake word's onnxruntime/audio init was fragile when other C extensions
had just initialized on possibly-different worker threads.

Manual single-toggle worked fine because nothing else was competing.
The fix sorts the switches so wake word always runs LAST in a batch —
TTS/STT/embedding settle first, then wake word inits cleanly.

Since 2026-10-22 the switches are jobs (core/provider_switch.py): start()
sorts its (key, value) items the same way, and every job app-wide runs
under one lock, so the rule now holds across requests too.

This test locks the ordering invariant. A future refactor that loses
the sort will fail this test before reintroducing the bug.
"""
from core.provider_switch import WAKEWORD_KEYS


def _sort_invariant(items):
    """Replicate the sort applied in core/provider_switch.py:start
    so we can validate the invariant directly without spinning a job."""
    items.sort(key=lambda kv: kv[0] in WAKEWORD_KEYS)
    return items


def test_wakeword_sorts_to_end_when_present():
    """Mixed batch — wake word ends up last."""
    items = [
        ('WAKE_WORD_ENABLED', True),
        ('TTS_PROVIDER', 'kokoro'),
        ('STT_PROVIDER', 'faster_whisper'),
    ]
    out = _sort_invariant(list(items))
    assert out[-1][0] == 'WAKE_WORD_ENABLED', (
        "the wake word must be the last switch so other C-extension "
        "inits (TTS/STT/embedding) settle before wake word's audio capture starts"
    )


def test_non_wakeword_relative_order_preserved():
    """Stable sort: TTS/STT/embedding keep their original ordering."""
    items = [
        ('EMBEDDING_PROVIDER', 'voyage'),
        ('WAKE_WORD_ENABLED', True),
        ('STT_PROVIDER', 'whisper'),
        ('TTS_PROVIDER', 'kokoro'),
    ]
    out = _sort_invariant(list(items))
    non_wake = [k for k, _ in out if k != 'WAKE_WORD_ENABLED']
    assert non_wake == ['EMBEDDING_PROVIDER', 'STT_PROVIDER', 'TTS_PROVIDER']


def test_no_wakeword_passes_through_unchanged():
    items = [('TTS_PROVIDER', 'kokoro'), ('STT_PROVIDER', 'whisper'), ('EMBEDDING_PROVIDER', 'voyage')]
    assert _sort_invariant(list(items)) == items


def test_wakeword_only_unchanged():
    items = [('WAKE_WORD_ENABLED', True)]
    assert _sort_invariant(list(items)) == items


def test_multiple_wakeword_entries_all_sort_to_end():
    items = [('WAKE_WORD_ENABLED', False), ('TTS_PROVIDER', 'kokoro'), ('WAKE_WORD_ENABLED', True)]
    out = _sort_invariant(list(items))
    assert [k for k, _ in out] == ['TTS_PROVIDER', 'WAKE_WORD_ENABLED', 'WAKE_WORD_ENABLED']


def test_empty_list_safe():
    assert _sort_invariant([]) == []


def test_reload_wakeword_model_also_sorts_to_end():
    """WAKEWORD_MODEL (a detector rebuild) is a wake word init too."""
    items = [('WAKEWORD_MODEL', 'hey_jarvis'), ('TTS_PROVIDER', 'kokoro')]
    out = _sort_invariant(list(items))
    assert out[-1][0] == 'WAKEWORD_MODEL'
    assert set(WAKEWORD_KEYS) == {'WAKE_WORD_ENABLED', 'WAKEWORD_MODEL'}


def test_sort_invariant_matches_job_implementation():
    """Cross-check: the sort key in this test file MUST match the one in
    core/provider_switch.py. If someone changes the sort logic (different
    key, different ordering rule), this test fails — pulling attention to
    whether the regression risk has actually been preserved."""
    from pathlib import Path
    src = (Path(__file__).parent.parent / "core" / "provider_switch.py").read_text(encoding='utf-8')
    expected = "items.sort(key=lambda kv: kv[0] in WAKEWORD_KEYS)"
    assert expected in src, (
        f"core/provider_switch.py no longer contains the wake-word-last sort. "
        f"Expected literal: {expected!r}. If the implementation changed shape, "
        f"update this test to match. Don't just remove the assertion — the "
        f"boot-race regression it prevents is real and reproducible."
    )
    # and the old inline loop in the batch route is gone for good
    route = (Path(__file__).parent.parent / "core" / "routes" / "settings.py").read_text(encoding='utf-8')
    assert "deferred_actions" not in route
    assert "provider_switch.start(" in route
