"""Hunt 20: the message buttons against error rows (core/chat/history.py)."""
from core.chat.history import ConversationHistory


def _h(rows):
    h = ConversationHistory(max_history=100)
    h.messages = [dict(r) for r in rows]
    return h


def test_regen_on_the_first_of_two_identical_lines_takes_that_one():
    """'hi' errors, the person re-sends 'hi', it answers. Regen (or trash) on the
    FIRST 'hi' used to match text from the end and delete the retry's turn."""
    rows = [{'role': 'user', 'content': 'hi', 'timestamp': 't1'},
            {'role': 'assistant', 'content': 'X failed', 'timestamp': 't2', 'metadata': {'error': True}},
            {'role': 'user', 'content': 'hi', 'timestamp': 't3'},
            {'role': 'assistant', 'content': 'hello', 'timestamp': 't4'}]
    h = _h(rows)
    assert h.remove_from_user_message('hi', 't1')
    assert h.messages == []
    h = _h(rows)
    assert h.remove_from_user_message('hi')                     # no stamp: the old text fallback
    assert [m['timestamp'] for m in h.messages] == ['t1', 't2']
    h = _h(rows)
    assert h.remove_from_user_message('hi', 'gone')             # a stale stamp falls back to text
    assert len(h.messages) == 2


def test_a_tool_step_left_open_is_closed_before_the_next_user_row():
    """Deleting the error bubble after a tool step left tool -> user on the
    wire; strict servers refused every later send."""
    rows = [{'role': 'user', 'content': 'look it up', 'timestamp': 't1'},
            {'role': 'assistant', 'content': '', 'timestamp': 't2',
             'tool_calls': [{'id': 'c1', 'type': 'function', 'function': {'name': 'web_search', 'arguments': '{}'}}]},
            {'role': 'tool', 'tool_call_id': 'c1', 'name': 'web_search', 'content': 'results', 'timestamp': 't3'},
            {'role': 'user', 'content': 'and?', 'timestamp': 't4'}]
    wire = _h(rows).get_messages_for_llm(context_limit=0)
    roles = [m['role'] for m in wire]
    i = roles.index('tool')
    assert roles[i + 1] == 'assistant' and wire[i + 1]['content'] == '(no response)' and roles[i + 2] == 'user'


def test_a_reply_cut_by_a_failure_is_kept_with_the_failure_as_its_note():
    """Krem D-a: what streamed before a mid-reply failure stays as her reply
    (the person deletes it if they want); the error rides as its note."""
    from unittest.mock import MagicMock
    from core.chat.chat_streaming import StreamingChat
    main = MagicMock()
    main.session_manager._in_tool_cycle = False
    sc = StreamingChat(main)
    e = ConnectionError("peer closed connection without sending complete message body")
    assert sc._keep_partial(e, 'lmstudio', 'The boat leaves at', 'thinking...')
    kw = main.session_manager.add_assistant_final.call_args.kwargs
    assert kw['content'] == 'The boat leaves at' and 'kept' in kw['metadata']['cutoff']
    assert 'error' not in kw['metadata']                      # a real reply: she sees it, Continue can finish it
    assert not sc._keep_partial(e, 'lmstudio', '   ')          # nothing streamed: the error row, as before
    sc.llm_done = True
    assert not sc._keep_partial(e, 'lmstudio', 'already saved')   # her row is written: never twice
