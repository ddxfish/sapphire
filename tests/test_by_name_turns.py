"""By-name turns — F1 of the Game Room foundation (2026-09-08).
Record: tmp/gameroom-foundation-20260908.md (S1/S4/S5 boards + Krem's rulings).

Rooms used to exist by grabbing the server's ACTIVE-CHAT pointer: the
stream and history routes only knew "the active chat", so a second tab, a
phone turn, a wake word, or a daemon moving the pointer repainted the
room's borrowed rail and redirected its next typed turn. Now:

- `/api/chat/stream {chat}` and `/api/history?chat=` address a chat BY NAME.
  begin_stream(operator=True): the named chat IS the active one → the turn
  stays pointer-bound (today's path); the pointer moved elsewhere → an A1
  override is pinned to the named chat (the phone-call lane).
- The stream counter is per chat: pointer-bound streams (`_is_streaming`)
  block pointer switches; pinned by-name streams don't; every by-name
  mutation guard asks `is_streaming(chat)`; append waits on the chat's OWN
  idle event.
- A by-name turn whose brain can't be built REFUSES instead of falling
  through to the operator's chat.
- `foreign` on AI_TYPING_* means chat != active (one definition), payload
  carries the chat.
- `/api/chats?kind=&slim=` filters server-side; POST /api/chats {settings}
  stamps at birth; delete of the active chat publishes CHAT_SWITCHED; the
  message-edit routes refuse a `?chat=` that isn't the active chat.
"""
import threading
from unittest.mock import MagicMock, patch

import pytest

from core.chat.chat import LLMChat, ChatBusy
from core.chat.history import ChatSessionManager
from core.chat.chat_streaming import StreamingChat, stamp_private_if_unlocked


# ─── per-chat stream registry (ChatSessionManager) ──────────────────────────

@pytest.fixture
def sm():
    with patch.object(ChatSessionManager, '__init__', lambda self: None):
        obj = ChatSessionManager()
    obj._lock = threading.RLock()
    obj.active_chat_name = 'trinity'
    obj._streaming_count = 0
    obj._pointer_streams = 0
    obj._chat_streams = {}
    obj._chat_idle = {}
    obj._no_streams_event = threading.Event()
    obj._no_streams_event.set()
    obj._rows_state = None
    return obj


def test_pointer_stream_blocks_pointer_and_marks_its_chat(sm):
    sm.begin_streaming(None, pinned=False)
    assert sm._is_streaming is True                    # pointer switch must refuse
    assert sm.is_streaming('trinity') is True
    assert sm.chat_idle_event('trinity').is_set() is False
    assert sm._no_streams_event.is_set() is False
    sm.end_streaming(None, pinned=False)
    assert sm._is_streaming is False
    assert sm.is_streaming('trinity') is False
    assert sm.chat_idle_event('trinity').is_set() is True
    assert sm._no_streams_event.is_set() is True


def test_pinned_stream_never_blocks_the_pointer(sm):
    """A room's by-name turn on a non-active session (or a phone call) runs
    while the operator is free to switch chats — the S1 F4 class (a Twilio
    turn blocking every room open) is gone."""
    sm.begin_streaming('story_x', pinned=True)
    assert sm._is_streaming is False
    assert sm.is_streaming('story_x') is True
    assert sm.is_streaming('trinity') is False
    assert sm.chat_idle_event('story_x').is_set() is False
    assert sm.chat_idle_event('trinity').is_set() is True   # append to trinity needn't wait
    sm.end_streaming('story_x', pinned=True)
    assert sm.is_streaming('story_x') is False
    assert sm.chat_idle_event('story_x').is_set() is True


def test_counts_nest_and_floor(sm):
    sm.begin_streaming('story_x', pinned=True)
    sm.begin_streaming('story_x', pinned=True)
    sm.end_streaming('story_x', pinned=True)
    assert sm.is_streaming('story_x') is True
    sm.end_streaming('story_x', pinned=True)
    sm.end_streaming('story_x', pinned=True)            # double decrement: silent
    assert sm.is_streaming('story_x') is False
    assert sm._streaming_count == 0
    assert sm._pointer_streams == 0


def test_legacy_bool_setter_keeps_both_views_consistent(sm):
    sm._is_streaming = True
    assert sm._is_streaming is True
    assert sm.is_streaming('trinity') is True
    sm._is_streaming = False
    assert sm.is_streaming('trinity') is False
    assert sm._no_streams_event.is_set() is True


# ─── begin_stream operator decision (LLMChat) ───────────────────────────────

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


def test_operator_on_active_chat_stays_pointer_bound(llm):
    stream, sid, chat = llm.begin_stream(chat_name='trinity', exclusive=True, operator=True)
    assert chat == 'trinity'
    assert stream.target_chat is None          # today's path, in-memory brain
    assert stream.operator_lane is True
    assert sid in llm._streams_by_chat['trinity']


def test_operator_on_other_chat_is_pinned(llm):
    stream, _, chat = llm.begin_stream(chat_name='story_x', exclusive=True, operator=True)
    assert chat == 'story_x'
    assert stream.target_chat == 'story_x'     # A1 override, phone-call lane
    assert 'story_x' in llm._streams_by_chat


def test_driver_lane_keeps_unconditional_override(llm):
    """The phone driver relies on the override even when the operator is
    watching the call's chat (2026-07-03 Alfred greeting)."""
    stream, _, _ = llm.begin_stream(chat_name='trinity', exclusive=False)
    assert stream.target_chat == 'trinity'
    assert stream.operator_lane is False


def test_plain_web_turn_unchanged(llm):
    stream, _, chat = llm.begin_stream(exclusive=True)
    assert chat == 'trinity'
    assert stream.target_chat is None


def test_exclusive_is_per_chat_across_operator_and_driver(llm):
    llm.begin_stream(chat_name='story_x', exclusive=True, operator=True)
    with pytest.raises(ChatBusy):
        llm.begin_stream(chat_name='story_x', exclusive=True, operator=True)
    llm.begin_stream(exclusive=True)           # trinity is free


# ─── foreign = chat != active, one definition ───────────────────────────────

def _stream(active, target):
    main = MagicMock()
    main.session_manager.get_active_chat_name.return_value = active
    s = StreamingChat(main)
    s.target_chat = target
    return s


def test_typing_payload_pointer_bound():
    assert _stream('trinity', None)._typing_payload() == {"foreign": False, "chat": 'trinity'}


def test_typing_payload_target_is_active_is_not_foreign():
    """A phone turn on the chat the operator is watching used to be
    'foreign' by construction — the operator's Stop button lied."""
    assert _stream('trinity', 'trinity')._typing_payload() == {"foreign": False, "chat": 'trinity'}


def test_typing_payload_target_elsewhere_is_foreign():
    assert _stream('trinity', 'story_x')._typing_payload() == {"foreign": True, "chat": 'story_x'}


# ─── talk-stamp goes to THIS turn's chat ────────────────────────────────────

def _vault_open(monkeypatch):
    from core import prompt_vault
    monkeypatch.setattr(prompt_vault, 'vault_status', lambda: {'exists': True, 'unlocked': True})
    from core.settings_manager import settings as sm_settings
    monkeypatch.setattr(sm_settings, 'is_managed', lambda: False)


def test_stamp_by_name_when_pointer_is_elsewhere(monkeypatch):
    _vault_open(monkeypatch)
    sm = MagicMock()
    sm.get_active_chat_name.return_value = 'trinity'
    sm._effective_chat_name.return_value = 'story_x'
    sm.get_chat_settings.return_value = {}
    sm.set_named_chat_settings.return_value = True
    with patch('core.chat.chat_streaming.publish') as pub:
        stamp_private_if_unlocked(sm)
    sm.set_named_chat_settings.assert_called_once_with('story_x', {'private_chat': True})
    sm.update_chat_settings.assert_not_called()
    assert pub.call_args[0][1]['chat'] == 'story_x'


def test_stamp_on_active_keeps_expected_active_guard(monkeypatch):
    _vault_open(monkeypatch)
    sm = MagicMock()
    sm.get_active_chat_name.return_value = 'trinity'
    sm._effective_chat_name.return_value = 'trinity'
    sm.get_chat_settings.return_value = {}
    sm.update_chat_settings.return_value = True
    with patch('core.chat.chat_streaming.publish'):
        stamp_private_if_unlocked(sm)
    sm.update_chat_settings.assert_called_once_with({'private_chat': True}, expected_active='trinity')
    sm.set_named_chat_settings.assert_not_called()


def test_stamp_skips_mode_tagged_sessions(monkeypatch):
    _vault_open(monkeypatch)
    sm = MagicMock()
    sm.get_active_chat_name.return_value = 'trinity'
    sm._effective_chat_name.return_value = 'poker_1'
    sm.get_chat_settings.return_value = {'mode': 'game'}
    stamp_private_if_unlocked(sm)
    sm.set_named_chat_settings.assert_not_called()
    sm.update_chat_settings.assert_not_called()


# ─── surface derived from mode ──────────────────────────────────────────────

def test_surface_for_derives_game_from_mode():
    from core.hooks import surface_for
    assert surface_for({'mode': 'game', 'game_id': 'poker'}) == 'game'
    assert surface_for({'mode': 'game', 'surface': 'chat'}) == 'chat'   # explicit wins
    assert surface_for({}) == 'chat'
    assert surface_for(None) == 'chat'


# ─── routes ─────────────────────────────────────────────────────────────────

def _stream_mock():
    stream = MagicMock()
    stream.cancel_flag = False
    stream.ephemeral = False
    stream.llm_done = False
    stream.chat_stream.return_value = iter([
        {"type": "content", "text": "hello"},
        {"type": "final", "text": "hello", "cancelled": False, "error": False},
    ])
    return stream


def test_stream_route_by_name_calls_operator_door(client, mock_system):
    c, csrf = client
    sm = mock_system.llm_chat.session_manager
    sm.is_chat_hidden.return_value = False
    sm.read_chat_settings.return_value = {}
    mock_system.llm_chat.begin_stream.return_value = (_stream_mock(), 'sid1', 'story_x')
    r = c.post('/api/chat/stream', json={'text': 'hi', 'chat': 'story_x'},
               headers={'X-CSRF-Token': csrf})
    assert r.status_code == 200
    mock_system.llm_chat.begin_stream.assert_called_once_with(
        chat_name='story_x', exclusive=True, operator=True)
    mock_system.llm_chat.end_stream.assert_called_once_with('sid1', 'story_x')


def test_stream_route_without_chat_is_unchanged(client, mock_system):
    c, csrf = client
    mock_system.llm_chat.begin_stream.return_value = (_stream_mock(), 'sid1', 'trinity')
    r = c.post('/api/chat/stream', json={'text': 'hi'}, headers={'X-CSRF-Token': csrf})
    assert r.status_code == 200
    mock_system.llm_chat.begin_stream.assert_called_once_with(exclusive=True)


def test_stream_route_refuses_sealed_and_missing_chats(client, mock_system):
    c, csrf = client
    sm = mock_system.llm_chat.session_manager
    sm.is_chat_hidden.return_value = True
    r = c.post('/api/chat/stream', json={'text': 'hi', 'chat': 'secret'},
               headers={'X-CSRF-Token': csrf})
    assert r.status_code == 409 and 'error' in r.json()
    sm.is_chat_hidden.return_value = False
    sm.read_chat_settings.return_value = None
    r = c.post('/api/chat/stream', json={'text': 'hi', 'chat': 'ghost'},
               headers={'X-CSRF-Token': csrf})
    assert r.status_code == 404 and 'error' in r.json()
    mock_system.llm_chat.begin_stream.assert_not_called()


def test_history_by_name_reads_the_store(client, mock_system):
    c, _ = client
    sm = mock_system.llm_chat.session_manager
    sm.get_active_chat_name.return_value = 'trinity'
    sm.get_display_messages_for.return_value = [
        {"role": "user", "content": "deal", "timestamp": "t1"},
        {"role": "assistant", "content": "dealt", "timestamp": "t2"},
    ]
    sm.read_chat_settings.return_value = {}
    sm.is_chat_degraded.return_value = None
    r = c.get('/api/history?chat=story_x')
    assert r.status_code == 200
    body = r.json()
    assert body['chat_name'] == 'story_x'
    assert len(body['messages']) == 2
    sm.get_display_messages_for.assert_called_once_with('story_x')
    sm.get_messages_for_display.assert_not_called()


def test_history_by_name_404s_unknown_chat(client, mock_system):
    c, _ = client
    sm = mock_system.llm_chat.session_manager
    sm.get_active_chat_name.return_value = 'trinity'
    sm.get_display_messages_for.return_value = None
    assert c.get('/api/history?chat=ghost').status_code == 404


def test_history_naming_the_active_chat_uses_the_live_singleton(client, mock_system):
    c, _ = client
    sm = mock_system.llm_chat.session_manager
    sm.get_active_chat_name.return_value = 'trinity'
    sm.get_messages_for_display.return_value = []
    sm.is_chat_degraded.return_value = None
    mock_system.llm_chat.current_system_prompt = ''
    r = c.get('/api/history?chat=trinity')
    assert r.status_code == 200 and r.json()['chat_name'] == 'trinity'
    sm.get_display_messages_for.assert_not_called()


def test_chat_list_kind_filter_and_slim(client, mock_system):
    c, _ = client
    sm = mock_system.llm_chat.session_manager
    sm.list_chat_files.return_value = [
        {"name": "poker_1", "kind": "game", "game_id": "poker", "archived": False,
         "settings": {"mode": "game", "game_id": "poker", "custom_context": "x" * 5000,
                      "ghost_context": "y" * 5000, "persona": "sapphire"}},
        {"name": "trinity", "kind": "chat", "game_id": "", "archived": False,
         "settings": {"persona": "sapphire"}},
    ]
    mock_system.llm_chat.get_active_chat.return_value = 'trinity'
    r = c.get('/api/chats?kind=game&slim=1')
    assert r.status_code == 200
    chats = r.json()['chats']
    assert [ch['name'] for ch in chats] == ['poker_1']
    assert 'custom_context' not in chats[0]['settings']
    assert chats[0]['settings']['game_id'] == 'poker'
    # legacy alias still filters
    assert [ch['name'] for ch in c.get('/api/chats?type=game').json()['chats']] == ['poker_1']
    # no filter = everything, settings intact
    full = c.get('/api/chats').json()['chats']
    assert len(full) == 2 and 'custom_context' in full[0]['settings']


def test_create_chat_stamps_settings_at_birth(client, mock_system):
    c, csrf = client
    mock_system.llm_chat.create_chat.return_value = True
    r = c.post('/api/chats', json={'name': 'poker_9', 'settings': {'mode': 'game', 'game_id': 'poker'}},
               headers={'X-CSRF-Token': csrf})
    assert r.status_code == 200
    mock_system.llm_chat.create_chat.assert_called_once_with(
        'poker_9', settings={'mode': 'game', 'game_id': 'poker'})
    r = c.post('/api/chats', json={'name': 'bad', 'settings': 'nope'},
               headers={'X-CSRF-Token': csrf})
    assert r.status_code == 400


def test_bound_belt_refuses_edit_when_pointer_moved(client, mock_system):
    c, csrf = client
    mock_system.llm_chat.get_active_chat.return_value = 'trinity'
    r = c.post('/api/history/messages/edit?chat=story_x',
               json={'role': 'user', 'timestamp': 't1', 'new_content': 'x'},
               headers={'X-CSRF-Token': csrf})
    assert r.status_code == 409
    mock_system.llm_chat.session_manager.edit_message_by_timestamp.assert_not_called()
    # bound to the active chat → passes through
    mock_system.llm_chat.session_manager.edit_message_by_timestamp.return_value = True
    r = c.post('/api/history/messages/edit?chat=trinity',
               json={'role': 'user', 'timestamp': 't1', 'new_content': 'x'},
               headers={'X-CSRF-Token': csrf})
    assert r.status_code == 200


def test_delete_active_chat_publishes_switch(client, mock_system):
    c, csrf = client
    mock_system.llm_chat.get_active_chat.return_value = 'poker_1'
    mock_system.llm_chat.delete_chat.return_value = True
    mock_system._conversation_manager = None
    with patch('core.routes.chat._apply_chat_settings'), \
         patch('core.routes.chat.publish') as pub:
        r = c.delete('/api/chats/poker_1', headers={'X-CSRF-Token': csrf})
    assert r.status_code == 200
    names = [call.args[0] for call in pub.call_args_list]
    from core.event_bus import Events
    assert Events.CHAT_DELETED in names and Events.CHAT_SWITCHED in names
    assert names.index(Events.CHAT_DELETED) < names.index(Events.CHAT_SWITCHED)


def test_delete_other_chat_does_not_publish_switch(client, mock_system):
    c, csrf = client
    mock_system.llm_chat.get_active_chat.return_value = 'trinity'
    mock_system.llm_chat.delete_chat.return_value = True
    mock_system._conversation_manager = None
    with patch('core.routes.chat.publish') as pub:
        r = c.delete('/api/chats/poker_1', headers={'X-CSRF-Token': csrf})
    assert r.status_code == 200
    from core.event_bus import Events
    assert Events.CHAT_SWITCHED not in [call.args[0] for call in pub.call_args_list]


# ─── override seed = RAW store, first save appends (real DB) ────────────────

def _real_sm(tmp_path):
    return ChatSessionManager(history_dir=str(tmp_path))


def test_stream_session_seed_is_raw_and_first_save_appends(tmp_path):
    """Before F1 the override history was seeded from the LLM view (trimmed,
    image blocks dropped) and the first override save on a rows chat was a
    full resync from that seed — a by-name turn on an image-bearing chat
    rewrote the store without its images."""
    sm = _real_sm(tmp_path)
    assert sm.create_chat('story_x')
    img = {"role": "user", "content": [{"type": "text", "text": "look"},
                                       {"type": "image", "data": "QUFB", "media_type": "image/png"}]}
    assert sm.append_messages_to_chat('story_x', [img, {"role": "assistant", "content": "seen"}])
    sess = sm.make_stream_session('story_x')
    assert sess is not None
    seed = sess['history'].messages
    assert seed[0]['content'][1]['type'] == 'image', "seed must be the raw store, not the LLM view"
    assert sm._rows_state['story_x'] == {"offset": 0, "count": 2}
    from core.chat import stream_brain
    tok = stream_brain.set_override(sess)
    try:
        sm.add_user_message("next")
        sm.add_assistant_final("reply")
    finally:
        stream_brain.reset_override(tok)
    data = sm.export_chat('story_x')
    assert [m['role'] for m in data['messages']] == ['user', 'assistant', 'user', 'assistant']
    assert data['messages'][0]['content'][1]['type'] == 'image', "image row must survive the override save"
    assert sm._rows_state['story_x'] == {"offset": 0, "count": 4}


def test_stream_session_refuses_chat_with_unreadable_rows(tmp_path):
    import sqlite3
    sm = _real_sm(tmp_path)
    assert sm.create_chat('broken')
    assert sm.append_messages_to_chat('broken', [{"role": "user", "content": "ok"}])
    with sqlite3.connect(str(sm._db_path)) as conn:
        conn.execute("INSERT INTO chat_messages (chat_name, seq, role, message_json) VALUES (?, ?, ?, ?)",
                     ('broken', 1, 'assistant', 'not json at all'))
        conn.commit()
    assert sm.make_stream_session('broken') is None
    # agent workers latch read-only instead of refusing outright
    ov = sm.make_agent_override('broken')
    assert ov['history'].messages and ov['history'].messages[0]['content'] == 'ok'
    assert sm.is_chat_degraded('broken')
