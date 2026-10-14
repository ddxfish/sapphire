# The landing chat (2026-10-13): where the person's VOICE lands - the wake word, a
# satellite, the browser conversation - apart from the active chat, which is the Chat
# view's chat and the in-memory list. One pointer used to play both parts: a chat room
# had to activate itself for voice to follow and hand the pointer back on exit, and the
# Chat page showed the room twice (Krem, 2026-10-12/13). Message edits go BY NAME to a
# rail bound to a chat that is not the active one.
from unittest.mock import MagicMock

from core.chat.history import ChatSessionManager, landing_chat


def _sm(tmp_path):
    return ChatSessionManager(history_dir=str(tmp_path))


def test_voice_lands_on_the_landing_chat_and_the_active_chat_never_moves(tmp_path):
    sm = _sm(tmp_path)
    assert sm.create_chat('salon') and sm.get_active_chat_name() == 'default'
    assert sm.get_landing_chat_name() == 'default' and landing_chat(sm) == 'default'
    assert sm.set_landing_chat('salon') and sm.get_landing_chat_name() == 'salon' == landing_chat(sm)
    assert sm.get_active_chat_name() == 'default'                          # the Chat view's chat: untouched
    assert not sm.set_landing_chat('nowhere') and sm.get_landing_chat_name() == 'salon'
    ok, new = sm.rename_chat('salon', 'parlour')
    assert ok and sm.get_landing_chat_name() == 'parlour'                  # follows a rename
    assert sm.delete_chat('parlour') and sm.landing_chat_name is None and sm.get_landing_chat_name() == 'default'
    assert sm.create_chat('again') and sm.set_landing_chat('again') and sm.set_landing_chat(None)
    assert sm.get_landing_chat_name() == 'default'
    # the doors ask landing_chat(sm): a store without the landing (a fake, an old one) answers the active chat
    fake = MagicMock()
    fake.get_active_chat_name.return_value = 'open-chat'
    assert landing_chat(fake) == 'open-chat'

    class Old:
        def get_active_chat_name(self):
            return 'old'
    assert landing_chat(Old()) == 'old'


def test_message_edits_go_by_name_to_a_chat_that_is_not_the_active_one(tmp_path):
    sm = _sm(tmp_path)
    assert sm.create_chat('salon')
    rows = [{'role': 'user', 'content': 'q1', 'timestamp': 't1'},
            {'role': 'assistant', 'content': 'a1', 'timestamp': 't2',
             'tool_calls': [{'id': 'c1', 'type': 'function', 'function': {'name': 'f', 'arguments': '{}'}}]},
            {'role': 'tool', 'tool_call_id': 'c1', 'name': 'f', 'content': 'r', 'timestamp': 't3'},
            {'role': 'assistant', 'content': 'a1 final', 'timestamp': 't4'},
            {'role': 'user', 'content': 'q2', 'timestamp': 't5'},
            {'role': 'assistant', 'content': 'a2', 'timestamp': 't6'}]
    assert sm.append_messages_to_chat('salon', rows)
    read = lambda: [m for m in sm._read_store_messages('salon')[0]]
    stored = read()
    assert len(stored) == 6 and sm.get_active_chat_name() == 'default'
    t = lambda i: stored[i].get('timestamp')
    assert sm.edit_chat_rows('salon', 'edit', 'assistant', t(5), '<think>hm</think>a2 edited') == (True, None)
    assert read()[5]['content'] == 'a2 edited' and read()[5]['thinking'] == 'hm'
    assert sm.edit_chat_rows('salon', 'edit', 'user', t(4), 'q2 edited') == (True, None) and read()[4]['content'] == 'q2 edited'
    assert sm.edit_chat_rows('salon', 'remove_last', 1) == (True, None) and len(read()) == 5
    assert sm.edit_chat_rows('salon', 'remove_last_assistant', t(1)) == (True, None)   # the turn's LAST assistant row goes
    assert [m['content'] for m in read()] == ['q1', 'a1', 'r', 'q2 edited']
    assert sm.edit_chat_rows('salon', 'remove_tool_call', 'c1') == (True, None)
    assert not any('c1' in str(m) for m in read())
    assert sm.edit_chat_rows('salon', 'remove_from_user', 'q2 edited') == (True, None)
    assert [m['content'] for m in read()] == ['q1', 'a1'] or [m['content'] for m in read()] == ['q1']
    assert sm.edit_chat_rows('salon', 'remove_from_user', 'nope') == (False, 'not found')
    assert sm.edit_chat_rows('salon', 'remove_last', 50) == (False, 'not found')
    assert sm.edit_chat_rows('nowhere', 'remove_last', 1) == (False, "Chat 'nowhere' not found")
    assert sm.edit_chat_rows('salon', 'bogus', 1) == (False, 'no such edit: bogus')
    # the active chat still goes through the manager's own methods
    assert sm.edit_chat_rows('default', 'remove_last', 1) == (False, 'not found')


def test_the_edit_routes_take_the_rails_chat_by_name():
    from core.routes import chat as routes
    req = MagicMock()
    req.query_params = {'chat': 'salon'}
    system = MagicMock()
    system.llm_chat.get_active_chat.return_value = 'default'
    sm = system.llm_chat.session_manager
    sm.get_active_chat_name.return_value = 'default'
    sm.edit_chat_rows.return_value = (True, None)
    assert routes._edit_target(req, system) == 'salon'
    assert routes._edit(system, 'salon', 'remove_last', 2) == (True, None)
    sm.edit_chat_rows.assert_called_with('salon', 'remove_last', 2)
    sm.remove_last_messages.return_value = True
    assert routes._edit(system, 'default', 'remove_last', 2) == (True, None)   # the active chat: the old path
    sm.remove_last_messages.assert_called_with(2)
    sm.remove_last_messages.return_value = False
    assert routes._edit(system, 'default', 'remove_last', 2) == (False, 'not found')
    req.query_params = {}
    assert routes._edit_target(req, system) == 'default'
