"""Server-owned turns (core/chat/turn.py, 2026-09-15): the turn outlives its
viewers. Born from a phone locking mid-reply — the SSE socket dropped, the
route generator was closed, the engine's finally wrote "[Cancelled during
tool execution]" with nobody pressing Stop. Record: tmp/server-owned-turns-plan.md."""
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

import config
from core.chat import turn as turn_mod
from core.chat.turn import Turn
from core.chat.chat_streaming import StreamingChat


# ─── helpers ────────────────────────────────────────────────────────────────

class FakeStream:
    def __init__(self):
        self.cancel_flag = False
        self.llm_done = False
        self.ephemeral = False
        self.tts_stopped = False
        self.turn = None

    def stop_tts(self):
        self.tts_stopped = True


def gen_of(events, log=None, gate=None, raise_after=None):
    """A generator that logs its own finally (the engine's cleanup stand-in).
    gate: threading.Event awaited before EVERY yield (test-controlled pacing)."""
    def g():
        try:
            for i, ev in enumerate(events):
                if gate is not None:
                    gate.wait(5)
                if raise_after is not None and i == raise_after:
                    raise RuntimeError("provider exploded")
                yield ev
        finally:
            if log is not None:
                log.append('closed')
    return g()


def fed_gen(q, log=None):
    """Yields whatever the test puts on q until None — step-controlled pacing."""
    def g():
        try:
            while True:
                ev = q.get(timeout=5)
                if ev is None:
                    return
                yield ev
        finally:
            if log is not None:
                log.append('closed')
    return g()


def drain(viewer, timeout=5):
    out = []
    t = threading.Thread(target=lambda: out.extend(viewer), daemon=True)
    t.start()
    t.join(timeout)
    assert not t.is_alive(), "viewer never ended"
    return out


def content(events):
    return ''.join(e.get('text', '') for e in events if e.get('type') == 'content')


# ─── the lifecycle ──────────────────────────────────────────────────────────

def test_viewer_detach_never_closes_the_engine():
    """THE bug. A viewer leaving mid-turn (phone lock) must not close the
    generator; the turn runs to its end, the engine finally runs ONCE there,
    and on_end fires once."""
    log, ended = [], []
    gate = threading.Event()
    s = FakeStream()
    t = Turn(s, gen_of([{'type': 'content', 'text': 'a'}, {'type': 'content', 'text': 'b'},
                        {'type': 'llm_done', 'tts_streamed': False}], log=log, gate=gate),
             on_end=lambda: ended.append(1), label='t').start()
    v = t.attach(audio=True)
    gate.set()
    first = next(iter(v))
    assert first['type'] == 'content' and first['seq'] == 1
    t.detach(v)                       # the socket dropped
    assert t.done.wait(5)
    assert log == ['closed']          # exactly once, at the end — never BY the detach
    assert ended == [1]
    assert t.terminal == {'type': 'turn_end', 'done': True, 'ephemeral': False, 'seq': 4}
    t.thread.join(2)
    assert not t.thread.is_alive()


def test_terminal_reaches_viewer_and_on_end_precedes_it():
    order = []
    s = FakeStream()
    t = Turn(s, gen_of([{'type': 'content', 'text': 'hi'}]), on_end=lambda: order.append('end'))
    v = t.attach(audio=True)
    t.start()
    out = drain(v)
    assert [e['type'] for e in out] == ['content', 'turn_end']
    assert order == ['end']
    assert out[-1]['done'] is True


def test_provider_raise_gives_terminal_error_in_finally():
    log = []
    s = FakeStream()
    t = Turn(s, gen_of([{'type': 'content', 'text': 'x'}, {'type': 'content', 'text': 'y'}],
                       log=log, raise_after=1))
    v = t.attach(audio=True)
    t.start()
    out = drain(v)
    assert out[0]['type'] == 'content'
    assert out[-1]['type'] == 'turn_end' and 'provider exploded' in out[-1]['error']
    assert log == ['closed']


def test_cancel_before_llm_done_ends_the_turn_and_closes_the_engine():
    import queue as _q
    log, q = [], _q.Queue()
    s = FakeStream()
    t = Turn(s, fed_gen(q, log=log))
    v = t.attach(audio=True)
    t.start()
    it = iter(v)
    q.put({'type': 'content', 'text': 'a'})
    assert next(it)['text'] == 'a'
    s.cancel_flag = True              # /api/cancel
    q.put({'type': 'content', 'text': 'b'})
    rest = list(it)
    assert rest == [{'type': 'turn_end', 'cancelled': True, 'seq': 2}]
    assert log == ['closed']          # the engine was closed by the TURN, after the cancel


def test_cancel_after_llm_done_is_a_clean_done():
    """Stop during the audio tail: the row is written — the wire ends with
    `done`, exactly what the route did (E1#7)."""
    s = FakeStream()
    s.llm_done = True
    s.cancel_flag = True
    t = Turn(s, gen_of([{'type': 'tts_chunk', 'audio_b64': 'zz'}]))
    v = t.attach(audio=True)
    t.start()
    out = drain(v)
    assert out[-1]['done'] is True


# ─── ring + reattach ────────────────────────────────────────────────────────

def _finished_turn():
    s = FakeStream()
    evs = [{'type': 'content', 'text': 'Hel'}, {'type': 'content', 'text': 'lo '},
           {'type': 'tool_start', 'id': 'c1', 'name': 'x', 'args': {}},
           {'type': 'tool_end', 'id': 'c1', 'name': 'x', 'result': 'ok', 'error': False},
           {'type': 'content', 'text': 'wor'}, {'type': 'content', 'text': 'ld'},
           {'type': 'llm_done', 'tts_streamed': True}]
    t = Turn(s, gen_of(evs))
    v = t.attach(audio=True)
    t.start()
    live = drain(v)
    return t, live


def test_replay_since_is_exact_once_across_a_coalesced_run():
    t, live = _finished_turn()
    assert [e['seq'] for e in live] == list(range(1, 9))
    full = content(live)
    assert full == 'Hello world'
    # since=1: saw 'Hel' → gets 'lo ' (rest of run 1-2), tool pair, 'world', llm_done, turn_end
    out = drain(t.attach(since=1))
    assert content(out) == 'lo world'
    assert [e['type'] for e in out] == ['content', 'tool_start', 'tool_end', 'content', 'llm_done', 'turn_end']
    assert out[0]['seq'] == 2 and out[3]['seq'] == 6
    # since=5: mid second run → 'ld' only
    out = drain(t.attach(since=5))
    assert content(out) == 'ld' and out[0]['seq'] == 6
    # since=0: everything, coalesced into two content events
    out = drain(t.attach(since=0))
    assert content(out) == 'Hello world' and len([e for e in out if e['type'] == 'content']) == 2
    # since=8: saw it all → nothing but the end
    assert drain(t.attach(since=8)) == []


def test_attach_after_done_without_since_gets_only_the_terminal():
    t, _ = _finished_turn()
    out = drain(t.attach())
    assert len(out) == 1 and out[0]['type'] == 'turn_end'


def test_ring_truncation_sends_resync_first(monkeypatch):
    monkeypatch.setattr(turn_mod, 'RING_CHARS', 100)   # run cap 25 → runs of 3 chunks
    s = FakeStream()
    evs = [{'type': 'content', 'text': 'x' * 10} for _ in range(6)]
    t = Turn(s, gen_of(evs))
    v = t.attach()
    t.start()
    drain(v)
    assert t.ring_truncated
    out = drain(t.attach(since=1))
    assert out[0]['type'] == 'resync'
    assert out[-1]['type'] == 'turn_end'
    out2 = drain(t.attach(since=5))     # still reachable — no resync
    assert out2[0]['type'] == 'content'


def test_audio_is_live_only_and_only_for_audio_viewers():
    gate = threading.Event()
    s = FakeStream()
    evs = [{'type': 'tts_stream_start', 'stream_id': 'p'}, {'type': 'content', 'text': 'a'},
           {'type': 'tts_chunk', 'audio_b64': 'zz'}, {'type': 'tts_stream_end', 'stream_id': 'p'}]
    t = Turn(s, gen_of(evs, gate=gate)).start()
    va, vt = t.attach(audio=True), t.attach(audio=False)
    gate.set()
    a, b = drain(va), drain(vt)
    assert [e['type'] for e in a] == ['tts_stream_start', 'content', 'tts_chunk', 'tts_stream_end', 'turn_end']
    assert [e['type'] for e in b] == ['content', 'turn_end']
    assert [e['seq'] for e in a if e['type'].startswith('tts')] == [0, 1, 1]   # unnumbered: the seq of the moment
    assert [e['type'] for e in drain(t.attach(since=0))] == ['content', 'turn_end']


def test_last_audio_viewer_leaving_mutes_the_pump_not_the_turn():
    gate = threading.Event()
    s = FakeStream()
    t = Turn(s, gen_of([{'type': 'content', 'text': 'a'}, {'type': 'content', 'text': 'b'}], gate=gate)).start()
    va, vt = t.attach(audio=True), t.attach(audio=False)
    t.detach(vt)
    assert s.tts_stopped is False        # a text viewer leaving changes nothing
    t.detach(va)
    assert s.tts_stopped is True         # nobody can hear her — synth stops, writing continues
    gate.set()
    assert t.done.wait(5) and t.terminal['done'] is True


def test_slow_viewer_is_dropped_and_never_blocks_the_turn(monkeypatch):
    monkeypatch.setattr(turn_mod, 'VIEWER_QUEUE', 3)
    s = FakeStream()
    t = Turn(s, gen_of([{'type': 'content', 'text': str(i)} for i in range(10)]))
    v = t.attach()                        # never read
    t.start()
    assert t.done.wait(5)
    assert v.dropped is True
    assert t.viewers == 0


# ─── the REAL engine, driven by a Turn, viewer gone mid-reply ───────────────

def test_real_engine_finishes_after_its_viewer_leaves():
    """Drive the real chat_stream under a Turn; detach the only viewer after
    the first chunk (the phone locked). The reply is saved whole — no
    '[Cancelled during tool execution]', no '[Cancelled by user]'."""
    mock_main = MagicMock()
    mock_main.system = None
    fm = mock_main.function_manager
    fm.snapshot_scopes.return_value = {}
    fm.snapshot_executors.return_value = {}
    fm.enabled_tools = []
    fm.last_dangling_toolset = None
    sm = mock_main.session_manager
    sm.get_chat_settings.return_value = {}
    sm.get_active_chat_name.return_value = "t_chat"
    sm._effective_chat_name.return_value = "t_chat"
    sm._in_tool_cycle = True            # the finally's cancel branch is armed if the gen is closed early
    mock_main._build_base_messages.return_value = [{"role": "user", "content": "hi"}]
    provider = MagicMock()
    provider.provider_name = "test"
    provider.model = "test-model"
    gate = threading.Event()

    def slow_events():
        yield {"type": "content", "text": "First "}
        gate.wait(5)
        yield {"type": "content", "text": "second."}
        yield {"type": "done", "response": None}
    provider.chat_completion_stream.return_value = slow_events()
    mock_main._select_provider.return_value = ("test", provider, "")
    mock_main.tool_engine.extract_function_call_from_text.return_value = None

    sc = StreamingChat(mock_main)
    patches = [
        patch('core.chat.chat_streaming.get_generation_params', return_value={}),
        patch.object(config, 'FORCE_THINKING', False, create=True),
        patch.object(config, 'TTS_ENABLED', False, create=True),
        patch.object(config, 'TTS_STREAMING_ENABLED', False, create=True),
        patch('core.voice_privacy.tts_gate_reason', return_value=""),
        patch('core.chat.chat_streaming.publish'),
    ]
    for p in patches:
        p.start()
    try:
        t = Turn(sc, sc.chat_stream("hello"), label='real')
        v = t.attach(audio=True)
        t.start()
        it = iter(v)
        first = None
        for ev in it:
            if ev.get('type') == 'content':
                first = ev
                break
        assert first and 'First' in first['text']
        t.detach(v)                        # the phone locked
        gate.set()
        assert t.done.wait(10)
    finally:
        for p in reversed(patches):
            p.stop()
    assert t.terminal['done'] is True
    assert sc.llm_done is True
    finals = [c.args[0] if c.args else c.kwargs.get('content') for c in sm.add_assistant_final.call_args_list]
    assert finals and all('Cancelled' not in str(f) for f in finals), finals
    assert any('First second.' in str(f) for f in finals), finals
    sm.add_tool_result.assert_not_called()


# ─── routes ─────────────────────────────────────────────────────────────────

def _mock_stream(events):
    stream = MagicMock()
    stream.cancel_flag = False
    stream.llm_done = False
    stream.ephemeral = False
    stream.chat_stream.return_value = iter(events)
    return stream


def test_stream_route_wire_format_unchanged_plus_seq(client, mock_system):
    c, csrf = client
    stream = _mock_stream([
        {"type": "content", "text": "hello"},
        {"type": "tool_start", "id": "c1", "name": "x", "args": {"a": 1}},
        {"type": "tool_end", "id": "c1", "name": "x", "result": "ok", "error": False},
        {"type": "llm_done", "tts_streamed": False},
        {"type": "final", "text": "hello", "cancelled": False, "error": False},
    ])
    mock_system.llm_chat.begin_stream.return_value = (stream, 'sid1', 'trinity')
    r = c.post('/api/chat/stream', json={'text': 'hi'}, headers={'X-CSRF-Token': csrf})
    assert r.status_code == 200
    lines = [l for l in r.text.split('\n') if l.startswith('data: ')]
    assert '"type": "content", "text": "hello", "seq": 1' in lines[0]
    assert '"type": "tool_start"' in lines[1] and '"type": "tool_end"' in lines[2]
    assert '"type": "llm_done"' in lines[3] and '"seq": 4' in lines[3]
    assert lines[-1] == 'data: {"done": true, "ephemeral": false, "seq": 6}'   # `final` took 5, off the wire
    assert '"final"' not in r.text
    mock_system.llm_chat.end_stream.assert_called_once_with('sid1', 'trinity')
    mock_system.web_active_dec.assert_called_once()


def test_stream_route_engine_raise_is_an_error_line(client, mock_system):
    c, csrf = client
    stream = _mock_stream([])

    def boom(*a, **k):
        yield {"type": "content", "text": "x"}
        raise RuntimeError("kaboom")
    stream.chat_stream.side_effect = boom
    mock_system.llm_chat.begin_stream.return_value = (stream, 'sid1', 'trinity')
    r = c.post('/api/chat/stream', json={'text': 'hi'}, headers={'X-CSRF-Token': csrf})
    assert r.status_code == 200
    assert r.text.rstrip().endswith('{"error": "kaboom", "seq": 2}')
    mock_system.llm_chat.end_stream.assert_called_once()


def test_attach_route_204_when_nothing_is_live(client, mock_system):
    c, csrf = client
    mock_system.llm_chat.session_manager.get_active_chat_name.return_value = 'trinity'
    mock_system.llm_chat.live_turn.return_value = None
    r = c.post('/api/chat/attach', json={'since': 3}, headers={'X-CSRF-Token': csrf})
    assert r.status_code == 204
    mock_system.llm_chat.live_turn.assert_called_once_with('trinity')


def test_attach_route_refuses_sealed_and_missing(client, mock_system):
    c, csrf = client
    sm = mock_system.llm_chat.session_manager
    sm.is_chat_hidden.return_value = True
    r = c.post('/api/chat/attach', json={'chat': 'secret'}, headers={'X-CSRF-Token': csrf})
    assert r.status_code == 409
    sm.is_chat_hidden.return_value = False
    sm.read_chat_settings.return_value = None
    r = c.post('/api/chat/attach', json={'chat': 'ghost'}, headers={'X-CSRF-Token': csrf})
    assert r.status_code == 404
    mock_system.llm_chat.live_turn.assert_not_called()


def test_attach_route_replays_since_text_only(client, mock_system):
    c, csrf = client
    t, _ = _finished_turn()
    mock_system.llm_chat.session_manager.is_chat_hidden.return_value = False
    mock_system.llm_chat.session_manager.read_chat_settings.return_value = {}
    mock_system.llm_chat.live_turn.return_value = t
    r = c.post('/api/chat/attach', json={'chat': 'trinity', 'since': 4}, headers={'X-CSRF-Token': csrf})
    assert r.status_code == 200
    lines = [l for l in r.text.split('\n') if l.startswith('data: ')]
    assert '"text": "world"' in lines[0] and '"seq": 6' in lines[0]
    assert '"type": "llm_done"' in lines[1]
    assert lines[-1] == 'data: {"done": true, "ephemeral": false, "seq": 8}'
    assert 'tts_chunk' not in r.text and 'tts_stream_start' not in r.text


# ─── client contracts (source tripwires) ────────────────────────────────────

def _src(rel):
    from pathlib import Path
    return (Path(__file__).resolve().parents[1] / 'interfaces' / 'web' / 'static' / rel).read_text(encoding='utf-8')


def test_client_treats_a_dead_feed_as_lost_not_finished():
    api = _src('api.js')
    assert 'gotContent ? finishTurn()' not in api           # H6: EOF-before-done is not success
    assert '_feedLost(' in api and 'export const attachTurn' in api
    assert "handlers.lastSeq = data.seq" in api
    sh = _src('handlers/send-handlers.js')
    assert "from '../features/viewer.js'" in sh and 'e.feedLost && !viewer' in sh
    assert 'if (!viewer) setProc(false)' in sh
    chat = _src('chat.js')
    assert "document.getElementById('streaming-message')" in chat   # refresh hold
    assert chat.count('e.feedLost') == 2                             # regen + continue lanes
    assert 'export const detachStreaming' in _src('ui-streaming.js')
    assert 'releaseOwnership' in _src('core/state.js')
