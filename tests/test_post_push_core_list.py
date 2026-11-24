"""Proofs for bugs that are known, listed and not fixed yet (the pre-push hunt
of 2026-11-24; the list is tmp/core-v-2-16-7.txt, core was in code lock).
Each test asserts the RIGHT behaviour and is expected to fail, strictly: the
fix makes it pass, and the suite then says to take the mark off.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.mark.xfail(strict=True, reason="stream_pump reads the paragraph pause with `or 80`: a saved 0 becomes 80 ms")
def test_a_paragraph_pause_of_zero_is_zero():
    # the help text for this setting says "set 0 for gapless flow"
    src = (REPO / 'core' / 'tts' / 'stream_pump.py').read_text(encoding='utf-8')
    line = next(l for l in src.splitlines() if 'TTS_STREAMING_PAUSE_PARAGRAPH_MS' in l)
    assert 'or 80' not in line


@pytest.mark.xfail(strict=True, reason='the "stop" voice command stops her voice without firing tts_stop: '
                                       "a face (avatar, FlashHead) plays the reply out")
def test_the_stop_voice_command_tells_the_faces():
    # the browser's stop (core/routes/tts.py) and the wake word's barge-in both fire it
    src = (REPO / 'core' / 'plugins' / 'voice-commands' / 'hooks' / 'stop.py').read_text(encoding='utf-8')
    assert 'tts_stop' in src


@pytest.mark.xfail(strict=True, reason="core/stt/utterance.py needs scipy to change a sample rate; no requirements "
                                       "file names scipy (it only comes with openwakeword)")
def test_heard_audio_is_brought_to_16k_without_scipy(monkeypatch):
    from core.stt import utterance
    monkeypatch.setitem(sys.modules, 'scipy', None)             # a minimal or Docker install
    monkeypatch.setitem(sys.modules, 'scipy.signal', None)
    one_second = (np.zeros(48000, dtype='<i2')).tobytes()
    assert len(utterance.to_16k(one_second, rate=48000)) == 16000
