"""W3 voice words: a down STT says why (on the mic press and in one
notification, never 'Task "STT" failed'); audio errors carry no class names
or setting keys."""
from types import SimpleNamespace
from unittest.mock import patch

from core.audio.errors import classify_audio_error


def _fallback(exc):
    from sapphire import VoiceChatSystem
    me = SimpleNamespace(stt_why=None)
    with patch('core.notify.notify') as n:
        VoiceChatSystem._publish_stt_fallback_event(me, 'faster_whisper', exc)
    return me.stt_why, n


def test_a_model_that_fails_to_load_says_so_and_where():
    why, n = _fallback(RuntimeError("Failed to initialize STT model: CUDA out of memory"))
    assert why.startswith("Speech-to-text couldn't start (CUDA out of memory).")
    assert 'Settings › STT' in why and 'RuntimeError' not in why
    title, level, body = n.call_args.args[:3]
    assert body == why and n.call_args.kwargs['go'] == {'view': 'settings', 'tab': 'stt'}
    assert n.call_args.kwargs['key'] == 'stt-down'          # one row, however often it fails


def test_a_missing_package_is_named_in_words():
    why, _ = _fallback(ImportError("No module named 'faster_whisper'"))
    assert "Python package isn't installed" in why and 'No module named' not in why


def test_audio_errors_name_the_tab_not_the_key():
    words = classify_audio_error(OSError('Invalid sample rate [PaErrorCode -9997]'))
    assert 'Audio Sample Rates' in words and 'AUDIO_SAMPLE_RATES' not in words
    assert 'OSError' not in classify_audio_error(OSError('something odd'))
