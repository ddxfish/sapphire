"""Settings reloaded from disk call a key's reload callback only when that
key's value changed. Before 2026-10-16 every callback fired on ANY outside
write: an unrelated hand edit rebuilt TTS mid-sentence and reloaded Whisper."""
import threading
from unittest.mock import MagicMock

from core.settings_manager import SettingsManager


def _manager(config, callbacks):
    mgr = SettingsManager.__new__(SettingsManager)
    mgr._lock = threading.Lock()
    mgr._config = dict(config)
    mgr._reload_callbacks = callbacks
    return mgr


def test_only_the_changed_key_fires():
    tts, log = MagicMock(), MagicMock()
    mgr = _manager({'TTS_PROVIDER': 'kokoro', 'LOG_LEVEL': 'DEBUG', 'OTHER': 2},
                   {'TTS_PROVIDER': tts, 'LOG_LEVEL': log})
    mgr._dispatch_reload_callbacks({'TTS_PROVIDER': 'kokoro', 'LOG_LEVEL': 'INFO'})
    tts.assert_not_called()
    log.assert_called_once_with('DEBUG')


def test_an_unrelated_edit_fires_nothing():
    tts, log = MagicMock(), MagicMock()
    mgr = _manager({'TTS_PROVIDER': 'kokoro', 'LOG_LEVEL': 'INFO', 'OTHER': 'changed'},
                   {'TTS_PROVIDER': tts, 'LOG_LEVEL': log})
    mgr._dispatch_reload_callbacks({'TTS_PROVIDER': 'kokoro', 'LOG_LEVEL': 'INFO'})
    tts.assert_not_called()
    log.assert_not_called()


def test_a_key_that_appeared_counts_as_changed():
    cb = MagicMock()
    mgr = _manager({'STT_PROVIDER': 'faster_whisper'}, {'STT_PROVIDER': cb})
    mgr._dispatch_reload_callbacks({'STT_PROVIDER': None})
    cb.assert_called_once_with('faster_whisper')


def test_one_failing_callback_does_not_stop_the_next():
    bad = MagicMock(side_effect=RuntimeError('boom'))
    good = MagicMock()
    mgr = _manager({'A': 2, 'B': 2}, {'A': bad, 'B': good})
    mgr._dispatch_reload_callbacks({'A': 1, 'B': 1})
    bad.assert_called_once_with(2)
    good.assert_called_once_with(2)


def test_callbacks_run_outside_the_lock():
    mgr = _manager({'A': 2}, {})
    seen = []

    def cb(value):
        seen.append(mgr._lock.acquire(timeout=1))   # would deadlock-timeout if held
        mgr._lock.release()
    mgr._reload_callbacks = {'A': cb}
    mgr._dispatch_reload_callbacks({'A': 1})
    assert seen == [True]
