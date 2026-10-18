"""The live key tap: keys become stamped events, the stream opens and closes
cleanly, and the clock says when nothing is linked. The MIDI library is faked."""
import asyncio
import importlib.util
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).absolute().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, _ROOT.as_posix())

import fake_rtmidi as fake  # noqa: E402

CANON = 'plugins.midi.tools.midi_tools'
BLE = 'FM-1_BLE:FM-1_BLE Bluetooth 129:0'


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def tap(monkeypatch):
    """The tap module with a fake midi_tools that names one keyboard, on a
    pretend machine where that keyboard is plugged in."""
    fake.box = fake.Box(ins=[BLE])
    monkeypatch.setitem(sys.modules, 'rtmidi', fake)
    monkeypatch.setitem(sys.modules, CANON, SimpleNamespace(_listen_ports=lambda settings: ([BLE], 'FM-1_BLE')))
    mod = _load('fm1_tap_under_test', _ROOT / 'routes' / 'tap.py')
    yield mod
    mod.midi.shutdown()


def keys(*msgs, gap=0.02, after=0.05):
    """Someone plays these on the FM-1, one every `gap` seconds, starting after `after`."""
    def play():
        time.sleep(after)
        for m in msgs:
            fake.box.key(BLE, m)
            time.sleep(gap)
    threading.Thread(target=play, daemon=True).start()


PLAYED = ([0x90, 60, 90], [0xB0, 64, 127], [0x80, 60, 0], [0x90, 64, 0])


def test_read_keys_stamps_every_note_and_only_notes(tap):
    got, stopped = [], threading.Event()
    keys(*PLAYED)
    threading.Timer(0.5, stopped.set).start()
    before = tap.now_ms()
    tap.read_keys([BLE], got.append, stopped)
    after = tap.now_ms()
    assert [(e['on'], e['n'], e['v']) for e in got] == [(True, 60, 90), (False, 60, 0), (False, 64, 0)]
    assert all(e['type'] == 'note' and before <= e['t'] <= after for e in got)
    assert got[0]['t'] <= got[1]['t'] <= got[2]['t']
    assert fake.box.open('in') == 0                                      # the port is let go


def test_read_keys_stops_when_told(tap):
    stopped, got = threading.Event(), []
    keys(PLAYED[0])
    threading.Timer(0.15, stopped.set).start()
    t0 = time.monotonic()
    tap.read_keys([BLE], got.append, stopped)
    assert time.monotonic() - t0 < 2
    assert [e['n'] for e in got] == [60] and fake.box.open('in') == 0


def test_read_keys_ends_when_the_keys_go_away(tap, monkeypatch):
    monkeypatch.setattr(tap, 'GONE_CHECK', 0.1)
    threading.Timer(0.1, lambda: setattr(fake.box, '_ins', [])).start()   # unplugged
    t0 = time.monotonic()
    tap.read_keys([BLE], lambda e: None, threading.Event())
    assert time.monotonic() - t0 < 2 and fake.box.open('in') == 0


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


def test_stream_hello_then_notes_and_lets_the_port_go(tap):
    keys(*PLAYED, after=0.3)
    lines = _open_and_collect(tap, 3)
    assert lines[0]['type'] == 'hello' and lines[0]['ports'] == 'FM-1_BLE' and lines[0]['now'] > 0
    assert (lines[1]['n'], lines[1]['on']) == (60, True)
    assert (lines[2]['n'], lines[2]['on']) == (60, False)
    time.sleep(0.4)
    assert fake.box.open('in') == 0                                      # closing the page lets the port go
    assert tap._open == 0


def test_stream_refuses_without_a_keyboard(tap, monkeypatch):
    def none(settings):
        raise tap.midi.MidiError('Nothing to listen to: neither the FM-1 nor another keyboard is linked')
    monkeypatch.setattr(sys.modules[CANON], '_listen_ports', none)
    resp = asyncio.run(tap.stream(settings={}, request=None))
    assert resp.status_code == 409
    assert b'Nothing to listen to' in resp.body


def test_stream_says_when_the_library_is_missing(tap, monkeypatch):
    monkeypatch.setitem(sys.modules, 'rtmidi', None)
    resp = asyncio.run(tap.stream(settings={}, request=None))
    assert resp.status_code == 503 and b'python-rtmidi' in resp.body


def test_stream_caps_open_taps(tap):
    tap._open = tap.MAX_TAPS
    try:
        resp = asyncio.run(tap.stream(settings={}, request=None))
        assert resp.status_code == 429
    finally:
        tap._open = 0
