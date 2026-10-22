"""core/provider_switch.py — a provider switch is a job with a row (2026-10-22).

Hermetic: a fake system, the notification center and the bus replaced by
recorders, the settings singleton with its disk write stubbed."""
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core import provider_switch as ps


class _Rows:
    def __init__(self):
        self.born, self.ended, self.ticks = [], [], []
        self._n = 0

    def begin(self, title, source='core', go=None, key=None, body=''):
        self._n += 1
        rid = f"row{self._n}"
        self.born.append((rid, title, key, go))
        return rid

    def finish(self, rid, failed=False, body=None, title=None, origin=None):
        self.ended.append((rid, failed, title, body))

    def tick(self, rid, **kw):
        self.ticks.append((rid, kw))


@pytest.fixture
def harness(monkeypatch):
    from core import settings_manager as sm_mod
    settings = sm_mod.settings
    monkeypatch.setattr(settings, 'save', lambda: True)
    orig = (dict(settings._user), dict(settings._runtime), dict(settings._config))

    rows = _Rows()
    monkeypatch.setattr(ps.notify, 'begin', rows.begin)
    monkeypatch.setattr(ps.notify, 'finish', rows.finish)
    monkeypatch.setattr(ps.notify, 'tick', rows.tick)
    events = []
    monkeypatch.setattr(ps, '_publish', lambda key, value, tier, ok, error='': events.append((key, value, ok, error)))
    monkeypatch.setattr(ps, '_reapply_chat', lambda system: None)
    monkeypatch.setattr(ps, '_warm_vad', lambda: None)
    monkeypatch.setattr(ps, '_embedding_ready', lambda system, value: (True, ''))
    monkeypatch.setattr(ps, 'TTS_READY_POLL_S', 0.001)

    sys = SimpleNamespace(
        switch_stt_provider=MagicMock(return_value=True),
        switch_tts_provider=MagicMock(return_value=True),
        switch_embedding_provider=MagicMock(return_value=None),
        toggle_wakeword=MagicMock(return_value=True),
        reload_wakeword_model=MagicMock(return_value=True),
        whisper_client=SimpleNamespace(is_available=lambda: True),
        tts=SimpleNamespace(_provider=SimpleNamespace(is_available=lambda: True)),
    )
    monkeypatch.setattr(ps, '_system', lambda: sys)
    # a clean job table
    with ps._GUARD:
        ps._inflight.clear()
    try:
        yield SimpleNamespace(settings=settings, rows=rows, events=events, sys=sys)
    finally:
        ps.wait(timeout=5)
        with sm_mod.settings._lock:
            settings._user, settings._runtime, settings._config = orig


def test_success_persists_after_the_switch_and_publishes_ok(harness):
    h = harness
    h.settings.set('TTS_PROVIDER', 'none', persist=False, _skip_callbacks=True)
    seen = {}
    def _switch(value):
        seen['config_during'] = h.settings.get('TTS_PROVIDER')     # B4: still the old value
        return True
    h.sys.switch_tts_provider.side_effect = _switch
    jobs = ps.start([('TTS_PROVIDER', 'kokoro')])
    ps.wait(timeout=5)
    assert jobs == {'TTS_PROVIDER': 'row1'}
    assert seen['config_during'] == 'none'
    assert h.settings.get('TTS_PROVIDER') == 'kokoro'
    assert h.settings.get('TTS_ENABLED') is True                    # re-derived with the key
    assert h.rows.ended == [('row1', False, 'Voice: Kokoro (Local)', None)] or h.rows.ended[0][1] is False
    assert h.events == [('TTS_PROVIDER', 'kokoro', True, '')]
    assert ps.busy('TTS_PROVIDER') is None


def test_failure_keeps_config_brings_the_live_side_back_and_says_why(harness):
    h = harness
    h.settings.set('STT_PROVIDER', 'fireworks_whisper', persist=False, _skip_callbacks=True)
    calls = []
    def _switch(value):
        calls.append(value)
        return value != 'faster_whisper'          # the new one fails, going back works
    h.sys.switch_stt_provider.side_effect = _switch
    ps.start([('STT_PROVIDER', 'faster_whisper')])
    ps.wait(timeout=5)
    assert calls == ['faster_whisper', 'fireworks_whisper']
    assert h.settings.get('STT_PROVIDER') == 'fireworks_whisper'
    rid, failed, title, body = h.rows.ended[0]
    assert failed and title.endswith('failed') and 'Kept' in body
    assert h.events[0][0:3] == ('STT_PROVIDER', 'fireworks_whisper', False)


def test_a_null_client_after_a_switch_is_a_failure(harness):
    """The registry launders a broken provider into a Null one and the switch
    returns True; the job does not believe it."""
    from core.stt.stt_null import NullWhisperClient
    h = harness
    h.settings.set('STT_PROVIDER', 'none', persist=False, _skip_callbacks=True)
    h.sys.whisper_client = NullWhisperClient()
    ps.start([('STT_PROVIDER', 'faster_whisper')])
    ps.wait(timeout=5)
    assert h.settings.get('STT_PROVIDER') == 'none'
    assert h.rows.ended[0][1] is True
    assert 'null' in h.rows.ended[0][3]


def test_both_sides_off_when_the_previous_provider_cannot_come_back(harness):
    h = harness
    h.settings.set('TTS_PROVIDER', 'elevenlabs', persist=False, _skip_callbacks=True)
    h.sys.switch_tts_provider.side_effect = lambda value: value == 'none'
    ps.start([('TTS_PROVIDER', 'kokoro')])
    ps.wait(timeout=5)
    assert h.settings.get('TTS_PROVIDER') == 'none'          # config follows the live side
    assert h.rows.ended[0][3].endswith('Kept off.')


def test_tts_waits_until_the_provider_answers(harness):
    h = harness
    h.settings.set('TTS_PROVIDER', 'none', persist=False, _skip_callbacks=True)
    answers = iter([False, False, True])
    h.sys.tts._provider = SimpleNamespace(is_available=lambda: next(answers), _health_why='starting')
    ps.start([('TTS_PROVIDER', 'kokoro')])
    ps.wait(timeout=5)
    assert h.settings.get('TTS_PROVIDER') == 'kokoro'
    assert h.rows.ticks and 'waiting' in h.rows.ticks[0][1]['text']


def test_tts_gives_up_after_the_deadline(harness, monkeypatch):
    h = harness
    monkeypatch.setattr(ps, 'TTS_READY_S', 0)
    h.settings.set('TTS_PROVIDER', 'none', persist=False, _skip_callbacks=True)
    h.sys.tts._provider = SimpleNamespace(is_available=lambda: False, _health_why='no answer')
    ps.start([('TTS_PROVIDER', 'kokoro')])
    ps.wait(timeout=5)
    assert h.settings.get('TTS_PROVIDER') == 'none'
    assert 'not ready' in h.rows.ended[0][3]


def test_same_value_joins_different_value_is_refused(harness):
    h = harness
    gate = threading.Event()
    h.sys.switch_tts_provider.side_effect = lambda value: gate.wait(5) or True
    first = ps.start([('TTS_PROVIDER', 'kokoro')])
    assert ps.start([('TTS_PROVIDER', 'kokoro')]) == first          # joins: the same row
    with pytest.raises(ps.SwitchInProgress):
        ps.start([('TTS_PROVIDER', 'piper')])
    assert len(h.rows.born) == 1
    gate.set()
    ps.wait(timeout=5)
    assert ps.busy('TTS_PROVIDER') is None


def test_a_batch_runs_in_order_wake_word_last_each_under_its_own_row(harness):
    h = harness
    order = []
    h.sys.toggle_wakeword.side_effect = lambda v: order.append('wake') or True
    h.sys.switch_stt_provider.side_effect = lambda v: order.append('stt') or True
    h.sys.switch_tts_provider.side_effect = lambda v: order.append('tts') or True
    jobs = ps.start([('WAKE_WORD_ENABLED', True), ('STT_PROVIDER', 'faster_whisper'), ('TTS_PROVIDER', 'kokoro')])
    ps.wait(timeout=5)
    assert order == ['stt', 'tts', 'wake']
    assert set(jobs) == {'WAKE_WORD_ENABLED', 'STT_PROVIDER', 'TTS_PROVIDER'}
    assert len(h.rows.born) == 3 and len(h.rows.ended) == 3
    assert h.settings.get('WAKE_WORD_ENABLED') is True


def test_the_legacy_toggles_map_onto_their_provider(harness, monkeypatch):
    import config
    monkeypatch.setattr(config, 'STT_PROVIDER', 'none', raising=False)
    monkeypatch.setattr(config, 'TTS_PROVIDER', 'elevenlabs', raising=False)
    assert ps.item_for('STT_ENABLED', True) == ('STT_PROVIDER', 'faster_whisper')
    assert ps.item_for('TTS_ENABLED', True) == ('TTS_PROVIDER', 'elevenlabs')
    assert ps.item_for('TTS_ENABLED', False) == ('TTS_PROVIDER', 'none')
    assert ps.item_for('WAKE_WORD_ENABLED', 1) == ('WAKE_WORD_ENABLED', True)
    assert ps.item_for('LOG_LEVEL', 'DEBUG') is None


def test_wakeword_model_is_set_in_config_before_the_reload_and_put_back_on_failure(harness):
    h = harness
    h.settings.set('WAKEWORD_MODEL', 'hey_sapphire', persist=False, _skip_callbacks=True)
    seen = []
    def _reload(value):
        seen.append(h.settings.get('WAKEWORD_MODEL'))
        return value != 'hey_jarvis'
    h.sys.reload_wakeword_model.side_effect = _reload
    ps.start([('WAKEWORD_MODEL', 'hey_jarvis')])
    ps.wait(timeout=5)
    assert seen == ['hey_jarvis', 'hey_sapphire']          # the reload reads config; then back
    assert h.settings.get('WAKEWORD_MODEL') == 'hey_sapphire'


def test_enqueue_is_a_reload_callback_that_never_persists_itself(harness):
    """A plugin unload already wrote 'none'; the callback only moves the live side."""
    h = harness
    h.settings.set('TTS_PROVIDER', 'none', persist=False, _skip_callbacks=True)
    cb = ps.enqueue('TTS_PROVIDER')
    cb('none')
    ps.wait(timeout=5)
    h.sys.switch_tts_provider.assert_called_once_with('none')
    assert h.events == [('TTS_PROVIDER', 'none', True, '')]


def test_a_crash_inside_the_job_still_ends_the_row(harness):
    h = harness
    h.sys.toggle_wakeword.side_effect = RuntimeError('mic exploded')
    h.settings.set('WAKE_WORD_ENABLED', False, persist=False, _skip_callbacks=True)
    ps.start([('WAKE_WORD_ENABLED', True)])
    ps.wait(timeout=5)
    assert h.rows.ended[0][1] is True and 'mic exploded' in h.rows.ended[0][3]
    assert h.settings.get('WAKE_WORD_ENABLED') is False
    assert ps.busy('WAKE_WORD_ENABLED') is None
