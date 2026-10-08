"""One operator turn per chat — 2026-08-29.

Enter walked past the Stop button (handleKeyDown never checked isProc) and
the server minted a second StreamingChat on the same chat. Both streams
kept their own message lists, so nothing corrupted — history INTERLEAVED:
user2 landed inside turn 1's open tool cycle, and turn 2's final could
land between a tool_use and its tool_result → provider 400 on the next
turn (Claude/OpenAI-strict). Two tabs on one chat opened the same hole.

Fix: `LLMChat.begin_stream(exclusive=True)` refuses (ChatBusy) while the
chat has a live, un-cancelled stream; check + register are one critical
section. `/api/chat/stream` opts in and answers 409 {"error"} before
counting the request. Cancelled streams don't occupy — Stop→immediate-Send
keeps working. The chat() consumer and the phone driver stay non-exclusive.
"""
import threading
from unittest.mock import MagicMock, patch

import json
import pytest

from core.chat.chat import LLMChat, ChatBusy


# ─── registry-level ─────────────────────────────────────────────────────────

@pytest.fixture
def llm():
    """LLMChat with the REAL begin/end/cancel registry, nothing else."""
    with patch.object(LLMChat, '__init__', lambda self: None):
        obj = LLMChat()
    obj._streams_by_id = {}
    obj._streams_by_chat = {}
    obj._streams_lock = threading.Lock()
    obj.tool_engine = MagicMock()          # StreamingChat.__init__ reads it
    obj.session_manager = MagicMock()
    obj.session_manager.get_active_chat_name.return_value = 'trinity'
    return obj


def test_exclusive_refuses_while_chat_has_live_stream(llm):
    llm.begin_stream(exclusive=True)
    with pytest.raises(ChatBusy) as ei:
        llm.begin_stream(exclusive=True)
    assert ei.value.chat_name == 'trinity'
    assert len(llm._streams_by_id) == 1, "refused stream must not register"


def test_exclusive_refuses_even_when_first_was_non_exclusive(llm):
    """A wake/REST turn (chat() lane, non-exclusive) still occupies the chat
    for a typed send — the interleave is the same regardless of the door."""
    llm.begin_stream()
    with pytest.raises(ChatBusy):
        llm.begin_stream(exclusive=True)


def test_cancelled_stream_does_not_occupy(llm):
    """Stop→immediate-Send: handleStop awaits /api/cancel BEFORE aborting the
    fetch, so the old stream is flagged by the time the new send arrives."""
    llm.begin_stream(exclusive=True)
    assert llm.cancel_streams(chat_name='trinity') == 1
    stream2, sid2, _ = llm.begin_stream(exclusive=True)
    assert sid2 in llm._streams_by_id
    assert stream2.cancel_flag is False


def test_exclusive_releases_after_end_stream(llm):
    _, sid, chat = llm.begin_stream(exclusive=True)
    llm.end_stream(sid, chat)
    llm.begin_stream(exclusive=True)      # no raise


def test_exclusive_scoped_per_chat(llm):
    """A live turn on the active chat doesn't block a phone stream on its
    own chat (explicit chat_name), and vice versa."""
    llm.begin_stream(exclusive=True)                      # active: trinity
    llm.begin_stream('phone-line-1', exclusive=True)      # different chat
    assert set(llm._streams_by_chat) == {'trinity', 'phone-line-1'}


def test_non_exclusive_lanes_unaffected(llm):
    """chat() consumer + conversation driver pass no flag — their concurrency
    contract is unchanged this wave."""
    llm.begin_stream()
    llm.begin_stream()
    llm.begin_stream('phone-line-1')
    assert len(llm._streams_by_id) == 3


def test_simultaneous_exclusive_sends_exactly_one_wins(llm):
    """Check + register are one critical section: N racing sends → 1 stream."""
    n = 12
    gate = threading.Barrier(n)
    wins, refusals = [], []

    def go():
        gate.wait()
        try:
            wins.append(llm.begin_stream(exclusive=True)[1])
        except ChatBusy:
            refusals.append(1)

    threads = [threading.Thread(target=go) for _ in range(n)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert len(wins) == 1
    assert len(refusals) == n - 1
    assert len(llm._streams_by_id) == 1


# ─── route-level ────────────────────────────────────────────────────────────

def test_stream_route_queues_when_busy_then_streams(client, mock_system, monkeypatch):
    """Inbox (2026-10-06, wave I-2): a typed turn on a busy chat is no longer
    bounced with a 409 - it waits its turn. The body opens with a `queued`
    event (ticket, position) and carries the turn's events once begin_stream
    lets it in."""
    from core.chat import inbox
    monkeypatch.setattr(inbox, 'BACKOFF_MIN', 0.2)     # the wait must outlast the route's 0.25 s grace
    monkeypatch.setattr(inbox, 'BACKOFF_MAX', 0.2)
    c, csrf = client
    stream = MagicMock()
    stream.cancel_flag = False
    stream.ephemeral = False
    stream.chat_stream.return_value = iter([
        {"type": "content", "text": "later"},
        {"type": "final", "text": "later", "cancelled": False, "error": False},
    ])
    # a live turn wins the race three times, then the chat is free
    mock_system.llm_chat.begin_stream.side_effect = [ChatBusy('trinity'), ChatBusy('trinity'),
                                                     ChatBusy('trinity'), (stream, 'sid1', 'trinity')]
    mock_system.llm_chat.session_manager.get_active_chat_name.return_value = 'trinity'
    mock_system.llm_chat.session_manager.is_chat_hidden.return_value = False
    r = c.post('/api/chat/stream', json={'text': 'hi'}, headers={'X-CSRF-Token': csrf})
    assert r.status_code == 200
    lines = [json.loads(l[6:]) for l in r.text.splitlines() if l.startswith('data: ')]
    # the turn's identity first (a dropped socket reattaches by it), then the queue
    assert lines[0]['type'] == 'ticket' and lines[0]['ticket']
    assert lines[1]['type'] == 'queued' and lines[1]['ticket'] == lines[0]['ticket'] and lines[1]['position'] == 1
    assert any(l.get('type') == 'content' and l.get('text') == 'later' for l in lines)
    assert lines[-1].get('done') is True
    assert mock_system.llm_chat.begin_stream.call_count == 4


def test_stream_route_returns_409_error_json_when_the_inbox_refuses(client, mock_system, monkeypatch):
    """The 409 lane survives for a chat that cannot take the turn at all (a
    sealed chat): {"error"} shape, the frontend toast reads err.error. A FULL
    inbox is no longer one of those for a person: the depth cap counts machine
    items only (Krem: typed-while-busy is never refused; 2026-10-07)."""
    from core.chat import inbox
    c, csrf = client
    monkeypatch.setattr(inbox, 'DEPTH_MAX', 0)
    mock_system.llm_chat.session_manager.get_active_chat_name.return_value = 'trinity'
    mock_system.llm_chat.session_manager.is_chat_hidden.return_value = True
    r = c.post('/api/chat/stream', json={'text': 'hi'}, headers={'X-CSRF-Token': csrf})
    assert r.status_code == 409
    assert 'sealed' in r.json()['error']
    mock_system.web_active_inc.assert_not_called()
    mock_system.llm_chat.end_stream.assert_not_called()


def test_stream_route_opts_into_exclusive(client, mock_system):
    c, csrf = client
    stream = MagicMock()
    stream.cancel_flag = False
    stream.ephemeral = False
    stream.chat_stream.return_value = iter([
        {"type": "content", "text": "hello"},
        {"type": "final", "text": "hello", "cancelled": False, "error": False},
    ])
    mock_system.llm_chat.session_manager.get_active_chat_name.return_value = 'trinity'
    mock_system.llm_chat.begin_stream.return_value = (stream, 'sid1', 'trinity')
    r = c.post('/api/chat/stream', json={'text': 'hi'},
               headers={'X-CSRF-Token': csrf})
    assert r.status_code == 200
    assert '"done": true' in r.text
    # by NAME - the chat the turn was queued on, pointer-bound while it is
    # still the active one, pinned once the pointer moved (2026-10-07)
    mock_system.llm_chat.begin_stream.assert_called_once_with(chat_name='trinity', exclusive=True, operator=True)
    mock_system.llm_chat.end_stream.assert_called_once_with('sid1', 'trinity')
