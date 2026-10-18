"""Play speech here (TTS_LOCAL_PLAYBACK, 2026-10-17).

"Make speech" and "play it on this computer" used to be one switch. Now:
OFF = every TTS hook still fires (an avatar plugin takes the text or the
audio) and nothing plays on this machine or in its browser; satellites,
phones and bots are elsewhere and never muted. Alongside it: a plugin that
took a streaming chunk says so with skip_tts in tts_chunk_audio (not counted
as lost), and the browser's whole-blob route goes through the hooked door
(render) instead of the raw primitive, answering 204 when quiet on purpose.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import config
from core.tts import tts_client as tc
from core.tts.stream_pump import StreamingTTSPump


@pytest.fixture
def fresh_hooks():
    from core.hooks import hook_runner
    snapshot = dict(hook_runner._hooks)
    hook_runner._hooks.clear()
    hook_runner._sorted.clear()
    yield hook_runner
    hook_runner._hooks.clear()
    hook_runner._sorted.clear()
    hook_runner._hooks.update(snapshot)


@pytest.fixture
def playback_off(monkeypatch):
    monkeypatch.setattr(config, "TTS_LOCAL_PLAYBACK", False, raising=False)


@pytest.fixture
def playback_on(monkeypatch):
    monkeypatch.setattr(config, "TTS_LOCAL_PLAYBACK", True, raising=False)


def _client(audio=b'OggS....'):
    t = tc.TTSClient.__new__(tc.TTSClient)
    t._provider = SimpleNamespace(generate=MagicMock(return_value=audio), audio_content_type='audio/ogg',
                                  supports_pitch=False, supports_streaming=False)
    t.voice_name, t.speed, t.pitch_shift, t.temp_dir = 'af_heart', 1.0, 1.0, None
    t._generation = 0
    t.audio_available = True
    t.should_stop = MagicMock()
    t.stop = MagicMock()
    t._select_play_target = MagicMock(return_value=lambda *a, **k: None)
    return t


def _recorder(hooks, name):
    seen = []
    hooks.register(name, lambda ev: seen.append(ev.tts_text), plugin_name="avatar")
    return seen


# --- the speakers: speak() / speak_sync() -----------------------------------------

def test_speak_fires_pre_tts_then_stays_quiet_when_off(playback_off, fresh_hooks):
    t = _client()
    seen = _recorder(fresh_hooks, "pre_tts")
    with patch('core.voice_privacy.tts_gate_reason', return_value=''):
        assert t.speak('Hello there, friend.') is False
    assert len(seen) == 1 and 'Hello there' in seen[0]     # the avatar still heard it
    t._select_play_target.assert_not_called()               # these speakers did not


def test_speak_plays_when_on(playback_on, fresh_hooks):
    t = _client()
    with patch('core.voice_privacy.tts_gate_reason', return_value=''):
        assert t.speak('Hello there, friend.') is True
    t._select_play_target.assert_called_once()


def test_speak_sync_stays_quiet_when_off(playback_off, fresh_hooks):
    t = _client()
    seen = _recorder(fresh_hooks, "pre_tts")
    with patch('core.voice_privacy.tts_gate_reason', return_value=''):
        assert t.speak_sync('Hello there, friend.') is False
    assert len(seen) == 1
    t._select_play_target.assert_not_called()


# --- render(): elsewhere by default, here on request --------------------------------

def test_render_for_elsewhere_ignores_the_switch(playback_off):
    t = _client()
    with patch('core.voice_privacy.tts_gate_reason', return_value=''):
        audio, kind = t.render('Hello there, friend.', chat_settings={})
    assert (audio, kind) == (b'OggS....', 'audio/ogg')       # a satellite still speaks


def test_render_for_here_is_quiet_after_pre_tts(playback_off, fresh_hooks):
    t = _client()
    seen = _recorder(fresh_hooks, "pre_tts")
    with patch('core.voice_privacy.tts_gate_reason', return_value=''):
        assert t.render('Hello there, friend.', chat_settings={}, plays_here=True) == (None, tc.NOT_PLAYED_HERE)
    assert len(seen) == 1
    t._provider.generate.assert_not_called()


def test_quiet_reasons_are_silence_on_purpose():
    assert {tc.NOTHING_TO_SAY, tc.CANCELLED_BY_PLUGIN, tc.NOT_PLAYED_HERE} <= tc.QUIET_REASONS
    with patch('core.voice_privacy.tts_gate_reason', return_value=''):
        assert _client(None).render('hello there')[1] not in tc.QUIET_REASONS   # an engine failure is loud


# --- the browser's whole-blob route goes through the hooked door -------------------

class _Req:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def _route(monkeypatch):
    import core.api_fastapi  # noqa: F401 — the route module imports from it; this order avoids the cycle
    import core.routes.tts as tts_routes
    from core import voice_privacy as vp
    monkeypatch.setattr(tts_routes, 'check_endpoint_rate', lambda *a, **k: None)
    monkeypatch.setattr(tts_routes.config, 'TTS_ENABLED', True, raising=False)
    monkeypatch.setattr(vp, 'tts_gate_reason', lambda s=None: '')
    return tts_routes


def _file_mode(tts_routes, system, text='hello world'):
    return asyncio.run(tts_routes.handle_tts_speak(
        _Req({'text': text, 'output_mode': 'file'}), _=None, system=system))


def test_browser_blob_route_renders_through_the_hooked_door(monkeypatch):
    tts_routes = _route(monkeypatch)
    system = MagicMock()
    system.tts.render = MagicMock(return_value=(b'OggS....', 'audio/ogg'))
    resp = _file_mode(tts_routes, system)
    assert resp.status_code == 200 and resp.media_type == 'audio/ogg'
    system.tts.render.assert_called_once()
    assert system.tts.render.call_args.kwargs['plays_here'] is True
    system.tts.generate_audio_data.assert_not_called()      # the raw primitive is for preview only


@pytest.mark.parametrize('reason', [tc.NOT_PLAYED_HERE, tc.CANCELLED_BY_PLUGIN, tc.NOTHING_TO_SAY])
def test_browser_blob_route_answers_204_when_quiet_on_purpose(monkeypatch, reason):
    tts_routes = _route(monkeypatch)
    system = MagicMock()
    system.tts.render = MagicMock(return_value=(None, reason))
    assert _file_mode(tts_routes, system).status_code == 204


def test_browser_blob_route_stays_loud_when_the_engine_fails(monkeypatch):
    from fastapi import HTTPException
    tts_routes = _route(monkeypatch)
    system = MagicMock()
    system.tts.render = MagicMock(return_value=(None, 'The voice engine returned no audio.'))
    with pytest.raises(HTTPException) as err:
        _file_mode(tts_routes, system)
    assert err.value.status_code == 503 and 'no audio' in err.value.detail


# --- the streaming pump: consumed chunks, and here vs elsewhere --------------------

class _Provider:
    audio_content_type = "audio/ogg"
    supports_streaming = True

    def generate(self, text, voice, speed):
        return b"OGGS\x00fake"


def _pump(**kw):
    system = MagicMock()
    system.tts._provider = _Provider()
    system.tts.voice_name, system.tts.speed = "af_heart", 1.0
    return StreamingTTSPump(system=system, **kw)


@pytest.fixture
def enable_streaming(monkeypatch):
    monkeypatch.setattr(config, "TTS_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "TTS_STREAMING_ENABLED", True, raising=False)


def _run(pump):
    pump.push("Hello there, friend. ")
    pump.push("More to say here.")
    out = list(pump.flush_and_close())
    return ([e for e in out if e["type"] == "tts_chunk"],
            [e for e in out if e.get("type") == "notice"])


def test_pump_consumed_chunk_is_not_emitted_and_not_counted_lost(enable_streaming, fresh_hooks, playback_on):
    ends = []
    fresh_hooks.register("tts_chunk_audio", lambda ev: setattr(ev, "skip_tts", True), plugin_name="avatar")
    fresh_hooks.register("tts_stream_end", lambda ev: ends.append(ev.metadata.copy()), plugin_name="avatar")
    chunks, notices = _run(_pump())
    assert chunks == [] and notices == []
    assert ends[0]["chunk_count"] >= 1                      # voiced (the avatar has it), just not here


def test_pump_here_fires_hooks_and_emits_nothing_when_off(enable_streaming, fresh_hooks, playback_off):
    heard, ends = [], []
    fresh_hooks.register("tts_chunk_audio", lambda ev: heard.append(ev.metadata["chunk_text"]), plugin_name="avatar")
    fresh_hooks.register("tts_stream_end", lambda ev: ends.append(ev.metadata.copy()), plugin_name="avatar")
    chunks, notices = _run(_pump())
    assert heard and chunks == [] and notices == []
    assert ends[0]["chunk_count"] == len(heard)


def test_pump_elsewhere_still_emits_when_off(enable_streaming, fresh_hooks, playback_off):
    chunks, notices = _run(_pump(plays_here=False))        # a satellite's or a phone's pump
    assert chunks and notices == []


def test_pump_here_emits_when_on(enable_streaming, fresh_hooks, playback_on):
    chunks, notices = _run(_pump())
    assert chunks and notices == []


# --- conversation mode: a muted local turn ends on its own ------------------------

SR = 16000


def _frame(ms, speech):
    return (b"\x00\x00" * int(SR * ms / 1000), speech)


def _driver(events, chat_name=None):
    from core.conversation.driver import ConversationDriver
    system = MagicMock()
    fake_stream = MagicMock()
    fake_stream.cancel_flag = False
    fake_stream.chat_stream.return_value = iter(events)
    system.llm_chat.begin_stream.return_value = (fake_stream, "sid", "chat")
    sink = MagicMock()
    sink._worker = None
    d = ConversationDriver(system, transcribe_fn=lambda p: "hello sapphire", sink_factory=lambda: sink,
                           chat_name=chat_name, endpoint_silence_ms=300, min_speech_ms=100, barge_hold_ms=90)
    d._spawn = lambda target, *a: target(*a)
    d.engine.arm_barge = MagicMock()
    return d, system, fake_stream, sink


def _turn(d):
    d.push_frame(*_frame(150, True))
    for _ in range(3):
        d.push_frame(*_frame(100, False))


@patch("core.conversation.driver.publish")
def test_local_conversation_turn_ends_cleanly_with_no_audio_when_off(pub, playback_off):
    from core.conversation.engine import IDLE
    events = [{"type": "content", "text": "Hi "}, {"type": "content", "text": "there"}, {"type": "done"}]
    d, system, fs, sink = _driver(events)                   # the pump emitted nothing: no tts_chunk
    _turn(d)
    assert d.engine.state == IDLE
    sink.finish.assert_called_once()
    system.llm_chat.end_stream.assert_called_once()
    d.engine.arm_barge.assert_not_called()                  # nothing to barge into
    assert not isinstance(getattr(fs, "tts_plays_here", True), bool)   # local: never stamped elsewhere


@patch("core.conversation.driver.publish")
def test_phone_conversation_stamps_its_stream_elsewhere(pub, playback_off):
    events = [{"type": "content", "text": "Hi"}, {"type": "tts_chunk", "audio_b64": "x", "stream_id": "s"},
              {"type": "done"}]
    d, system, fs, sink = _driver(events, chat_name="phone-call-1")
    _turn(d)
    assert fs.tts_plays_here is False                       # the caller still hears her
    sink.feed_chunk.assert_called_once()
