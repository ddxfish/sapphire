"""Slash commands (core/commands.py): /remove by the person and by her self_command."""
from pathlib import Path

import pytest

from core import commands
from core.chat.history import turn_spans

ROOT = Path(__file__).absolute().parent.parent


def _turn(n, tools=False):
    rows = [{'role': 'user', 'content': f'u{n}'}]
    if tools:
        rows += [{'role': 'assistant', 'content': '', 'tool_calls': [{'id': f't{n}'}]},
                 {'role': 'tool', 'tool_call_id': f't{n}', 'content': 'r'}]
    return rows + [{'role': 'assistant', 'content': f'a{n}'}]


class FakeSM:
    """The store's contract, in memory: refuses while streaming, like replace_messages."""
    def __init__(self, rows):
        self.rows, self.streaming = rows, False

    from core.chat.history import ChatSessionManager as _C
    plan_turn_cut = _C.plan_turn_cut
    apply_turn_cut = _C.apply_turn_cut
    messages_digest = _C.messages_digest

    def export_chat(self, chat):
        return {'messages': [dict(m) for m in self.rows]} if chat == 'c' else None

    def is_streaming(self, chat):
        return self.streaming

    def replace_messages(self, chat, rows, expected_count=None, expected_digest=None):
        if self.streaming:
            return False, 'streaming'
        self.rows = rows
        return True, ''

    def _prune_orphaned_tool_images(self, chat):
        pass

    def read_chat_settings(self, chat):
        return {}


@pytest.fixture
def sm(monkeypatch):
    fake = FakeSM(_turn(1) + _turn(2, tools=True) + _turn(3))
    monkeypatch.setattr(commands, '_sm', lambda: fake)
    monkeypatch.setattr('core.rooms.is_room', lambda chat: False)
    monkeypatch.setattr('core.chat.history.publish', lambda *a, **k: None)
    monkeypatch.setattr(commands, '_tell', lambda *a, **k: None)
    commands._pending.clear()
    yield fake
    commands._pending.clear()


def _users(fake):
    return [m['content'] for m in fake.rows if m['role'] == 'user']


def test_remove_cuts_whole_turns_including_tool_chains(sm):
    out = commands.run('/remove 2', 'c')
    assert out['ok'] and _users(sm) == ['u1']
    assert sm.rows == _turn(1)


def test_bare_remove_asks_for_the_dialog_and_touches_nothing(sm):
    assert commands.run('/remove', 'c') == {'ok': True, 'open': 'remove', 'turns': 3}
    assert len(turn_spans(sm.rows)) == 3


@pytest.mark.parametrize('line', ['/remove 4', '/remove 0', '/remove two', '/remove -1', '/remove 1 2'])
def test_bad_counts_refuse_and_remove_nothing(sm, line):
    before = list(sm.rows)
    assert not commands.run(line, 'c')['ok']
    assert sm.rows == before


def test_unknown_and_chatless(sm):
    assert not commands.run('/nope', 'c')['ok']
    assert not commands.run('/remove 1', '')['ok']


def test_user_remove_refused_during_a_live_turn(sm):
    sm.streaming = True
    before = list(sm.rows)
    assert not commands.run('/remove 1', 'c')['ok']
    assert sm.rows == before


def test_her_remove_waits_and_spares_her_own_turn(sm):
    sm.streaming = True
    out = commands.run('/remove 2', 'c', by='ai')
    assert out['ok'] and _users(sm) == ['u1', 'u2', 'u3']
    sm.rows = sm.rows + [{'role': 'assistant', 'content': 'done'}]   # her reply lands after the plan
    commands.finish_turn(sm, 'c')                                     # a stream is still live: holds
    assert _users(sm) == ['u1', 'u2', 'u3']
    sm.streaming = False
    commands.finish_turn(sm, 'c')
    assert sm.rows == _turn(3) + [{'role': 'assistant', 'content': 'done'}]


def test_her_turn_cannot_be_counted(sm):
    assert not commands.run('/remove 3', 'c', by='ai')['ok']          # 3 turns, one is hers
    assert not commands._pending


def test_her_bare_remove_opens_nothing(sm):
    out = commands.run('/remove', 'c', by='ai')
    assert not out['ok'] and 'open' not in out


def test_one_removal_per_turn(sm):
    assert commands.run('/remove 1', 'c', by='ai')['ok']
    assert not commands.run('/remove 1', 'c', by='ai')['ok']


def test_stop_drops_her_waiting_removal(sm):
    before = list(sm.rows)
    commands.run('/remove 1', 'c', by='ai')
    commands.finish_turn(sm, 'c', cancelled=True)
    assert sm.rows == before and not commands._pending


def test_changed_rows_refuse_the_waiting_cut(sm):
    told = []
    commands._tell = lambda chat, title, level='info': told.append(level)
    commands.run('/remove 1', 'c', by='ai')
    sm.rows[0] = {'role': 'user', 'content': 'edited'}
    before = list(sm.rows)
    commands.finish_turn(sm, 'c')
    assert sm.rows == before and told == ['error']


def test_ai_cannot_call_a_person_only_command(sm):
    commands.register('secret', lambda chat, args, by: {'ok': True})
    try:
        assert not commands.run('/secret', 'c', by='ai')['ok']
        assert commands.run('/secret', 'c')['ok']
    finally:
        commands._commands.pop('secret')


def test_compact_opens_the_dialog_for_this_chat_and_she_cannot_call_it(sm):
    assert commands.run('/compact', 'c') == {'ok': True, 'open': 'compact', 'chat': 'c'}
    assert not commands.run('/compact', 'c', by='ai')['ok']
    assert 'compact' not in commands.ai_commands()


def test_compact_refuses_a_chat_that_is_not_there(sm, monkeypatch):
    monkeypatch.setattr(sm, 'read_chat_settings', lambda chat: None)
    assert not commands.run('/compact', 'gone')['ok']


def test_the_menu_lists_every_command():
    names = [c['name'] for c in commands.all_commands()]
    assert {'compact', 'remove'} <= set(names) and names == sorted(names)


def test_one_compress_dialog_and_the_menu_is_wired():
    static = ROOT / 'interfaces/web/static'
    assert 'cm-c-go' not in (static / 'views/chat-manage.js').read_text(encoding='utf-8')
    assert "compact: ({ chat }) => openCompressModal(chat)" in (static / 'handlers/commands.js').read_text(encoding='utf-8')
    src = (static / 'handlers/send-handlers.js').read_text(encoding='utf-8')
    assert 'if (onCommandKey(e)) return;' in src and 'onCommandInput(input)' in src


def test_composer_catches_commands_before_the_turn():
    src = (ROOT / 'interfaces/web/static/handlers/send-handlers.js').read_text(encoding='utf-8')
    assert src.index('isCommand(txt)') < src.index('dispatch(Events.USER_SENT')
