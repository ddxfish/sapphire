"""Mic ⏹ targeting — 2026-09-08 (record: tmp/llm-done-split-plan.md, part B).

The web stop route muted every stream on the active chat. With turn A's
audio still playing while turn B thinks, ⏹ muted B's pump before it had
spoken a word: B streamed in silent and fell back to a whole-blob at the
end. Now the browser names the pump it's hearing (stream_id); without one
the chat-scoped mute reaches only pumps that have STARTED.
"""
import threading
from unittest.mock import MagicMock, patch

import pytest

from core.chat.chat import LLMChat


@pytest.fixture
def llm():
    with patch.object(LLMChat, '__init__', lambda self: None):
        obj = LLMChat()
    obj._streams_by_id = {}
    obj._streams_by_chat = {}
    obj._streams_lock = threading.Lock()
    obj.tool_engine = MagicMock()
    obj.session_manager = MagicMock()
    obj.session_manager.get_active_chat_name.return_value = 'trinity'
    return obj


def _pump(stream_id, started):
    p = MagicMock()
    p._stream_id = stream_id
    p._stream_started = started
    p._skip_turn = False
    return p


def test_stop_by_stream_id_mutes_only_that_pump(llm):
    a, _, _ = llm.begin_stream()
    b, _, _ = llm.begin_stream()
    a.tts_pump = _pump('A', started=True)
    b.tts_pump = _pump('B', started=True)
    assert llm.stop_tts_streams(stream_id='A') == 1
    assert a.tts_stopped is True and a.tts_pump._skip_turn is True
    assert b.tts_stopped is False and b.tts_pump._skip_turn is False


def test_chat_scoped_stop_skips_pumps_that_have_not_started(llm):
    """The bug: A's tail is audible, B is thinking. ⏹ must mute A only."""
    a, _, _ = llm.begin_stream()
    b, _, _ = llm.begin_stream()
    a.tts_pump = _pump('A', started=True)
    b.tts_pump = _pump('B', started=False)      # exists from turn start, unstarted
    assert llm.stop_tts_streams(chat_name='trinity') == 1
    assert a.tts_stopped is True
    assert b.tts_stopped is False, "next turn's voice must survive a stop aimed at the last one"


def test_chat_scoped_stop_with_no_pump_is_noop(llm):
    s, _, _ = llm.begin_stream()
    s.tts_pump = None
    assert llm.stop_tts_streams(chat_name='trinity') == 0
    assert s.tts_stopped is False


def test_stop_by_id_respects_phone_exclusion(llm):
    p, _, _ = llm.begin_stream('phone-line-1')
    p.tts_pump = _pump('P', started=True)
    assert llm.stop_tts_streams(stream_id='P', exclude_chats={'phone-line-1'}) == 0
    assert p.tts_stopped is False


def test_stop_by_unknown_id_is_noop(llm):
    a, _, _ = llm.begin_stream()
    a.tts_pump = _pump('A', started=True)
    assert llm.stop_tts_streams(stream_id='nope') == 0
    assert a.tts_stopped is False


def test_stop_route_passes_stream_id(client, mock_system):
    c, csrf = client
    r = c.post('/api/tts/stop', json={'stream_id': 'abc123'},
               headers={'X-CSRF-Token': csrf})
    assert r.status_code == 200
    kw = mock_system.llm_chat.stop_tts_streams.call_args.kwargs
    assert kw.get('stream_id') == 'abc123'


def test_stop_route_without_body_is_chat_scoped(client, mock_system):
    c, csrf = client
    mock_system.llm_chat.session_manager.get_active_chat_name.return_value = 'trinity'
    r = c.post('/api/tts/stop', headers={'X-CSRF-Token': csrf})
    assert r.status_code == 200
    kw = mock_system.llm_chat.stop_tts_streams.call_args.kwargs
    assert kw.get('chat_name') == 'trinity'
    assert not kw.get('stream_id')
