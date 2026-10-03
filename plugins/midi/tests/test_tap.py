"""The live key tap: aseqdump lines become stamped events, the stream opens and
closes cleanly, and the clock says when nothing is linked. aseqdump is faked."""
import asyncio
import importlib.util
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).absolute().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, _ROOT.as_posix())

CANON = 'plugins.midi.tools.midi_tools'


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def tap(monkeypatch):
    """The tap module with a fake midi_tools that names one keyboard."""
    fake = SimpleNamespace(_listen_ports=lambda settings: ('128:0', 'FM-1_BLE'))
    monkeypatch.setitem(sys.modules, CANON, fake)
    mod = _load('fm1_tap_under_test', _ROOT / 'routes' / 'tap.py')
    monkeypatch.setattr(mod.midi, 'missing_tools', lambda: [])
    return mod


class FakeDump:
    """Stands in for the aseqdump process: a pipe we write lines into."""

    def __init__(self, lines, hold=0.0):
        import os
        self.r, self.w = os.pipe()
        self.stdout = os.fdopen(self.r, 'rb', buffering=0)
        self._lines, self._hold, self._rc = lines, hold, None

        def feed():
            time.sleep(0.02)
            for ln in lines:
                os.write(self.w, ln.encode() + b'\n')
                time.sleep(0.005)
            time.sleep(hold)
            os.close(self.w)
        threading.Thread(target=feed, daemon=True).start()

    def poll(self):
        return self._rc

    def terminate(self):
        self._rc = -15

    def wait(self, timeout=None):
        return self._rc

    def kill(self):
        self._rc = -9


LINES = [
    "Waiting for data. Press Ctrl+C to end.",
    "Source  Event                  Ch  Data",
    "128:0   Note on                 0, note 60, velocity 90",
    "128:0   Control change          0, controller 64, value 127",
    "128:0   Note off                0, note 60, velocity 0",
    "128:0   Note on                 0, note 64, velocity 0",
]


def test_read_keys_stamps_every_note_and_only_notes(tap):
    got = []
    with patch.object(tap.subprocess, 'Popen', return_value=FakeDump(LINES)):
        before = tap.now_ms()
        tap.read_keys('128:0', got.append, threading.Event())
        after = tap.now_ms()
    assert [(e['on'], e['n'], e['v']) for e in got] == [(True, 60, 90), (False, 60, 0), (False, 64, 0)]
    assert all(e['type'] == 'note' and before <= e['t'] <= after for e in got)
    assert got[0]['t'] <= got[1]['t'] <= got[2]['t']


def test_read_keys_stops_when_told(tap):
    stopped = threading.Event()
    dump = FakeDump(LINES[:3], hold=5)
    got = []
    with patch.object(tap.subprocess, 'Popen', return_value=dump):
        threading.Timer(0.15, stopped.set).start()
        t0 = time.monotonic()
        tap.read_keys('128:0', got.append, stopped)
    assert time.monotonic() - t0 < 2
    assert dump.poll() is not None               # the dump was ended, not left behind
    assert [e['n'] for e in got] == [60]


def test_clock_says_when_nothing_is_linked(tap, monkeypatch):
    out = tap.clock(settings={})
    assert out['ok'] is True and out['ports'] == 'FM-1_BLE' and out['now'] > 0

    def none(settings):
        raise tap.midi.MidiError('Nothing to listen to')
    monkeypatch.setattr(sys.modules[CANON], '_listen_ports', none)
    out = tap.clock(settings={})
    assert out['ok'] is False and out['ports'] == '' and 'Nothing to listen to' in out['detail']


def _open_and_collect(tap, n):
    """Open the stream and read n data lines on ONE loop (the reader thread
    hands events back to the loop that opened it), then let it close."""
    async def go():
        resp = await tap.stream(settings={}, request=None)
        assert resp.media_type == 'text/event-stream'
        lines = []
        agen = resp.body_iterator
        async for chunk in agen:
            text = chunk if isinstance(chunk, str) else chunk.decode()
            if text.startswith('data: '):
                lines.append(json.loads(text[6:]))
            if len(lines) >= n:
                break
        await agen.aclose()
        return lines
    return asyncio.run(go())


def test_stream_hello_then_notes_and_closes_the_dump(tap):
    dump = FakeDump(LINES, hold=5)
    with patch.object(tap.subprocess, 'Popen', return_value=dump):
        lines = _open_and_collect(tap, 3)
    assert lines[0]['type'] == 'hello' and lines[0]['ports'] == 'FM-1_BLE' and lines[0]['now'] > 0
    assert (lines[1]['n'], lines[1]['on']) == (60, True)
    assert (lines[2]['n'], lines[2]['on']) == (60, False)
    time.sleep(0.4)
    assert dump.poll() is not None               # closing the page ends aseqdump
    assert tap._open == 0


def test_stream_refuses_without_a_keyboard(tap, monkeypatch):
    def none(settings):
        raise tap.midi.MidiError('Nothing to listen to: neither the FM-1 nor another keyboard is linked')
    monkeypatch.setattr(sys.modules[CANON], '_listen_ports', none)
    resp = asyncio.run(tap.stream(settings={}, request=None))
    assert resp.status_code == 409
    assert b'Nothing to listen to' in resp.body


def test_stream_caps_open_taps(tap):
    tap._open = tap.MAX_TAPS
    try:
        resp = asyncio.run(tap.stream(settings={}, request=None))
        assert resp.status_code == 429
    finally:
        tap._open = 0
