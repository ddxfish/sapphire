"""Slash commands (core/commands.py): /remove by the person and by her slash_command."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import commands
from core.chat import budget
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
        self.rows, self.streaming, self.slot, self.seal = rows, False, None, False

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

    undo_turn_cut = _C.undo_turn_cut

    def plugin_data_put(self, plugin, chat, key, value):
        if self.seal:
            raise RuntimeError('sealed')
        self.slot = json.loads(json.dumps(value))

    def plugin_data_get(self, plugin, chat, key, default=None):
        return self.slot or default

    def plugin_data_delete(self, plugin, chat, key=None):
        self.slot = None


@pytest.fixture
def sm(monkeypatch):
    fake = FakeSM(_turn(1) + _turn(2, tools=True) + _turn(3))
    monkeypatch.setattr(commands, '_sm', lambda: fake)
    monkeypatch.setattr('core.rooms.is_room', lambda chat: False)
    monkeypatch.setattr('core.chat.history.publish', lambda *a, **k: None)
    monkeypatch.setattr(commands, '_tell', lambda *a, **k: None)
    monkeypatch.setattr(commands, '_call_chats', lambda: set())
    monkeypatch.setattr(commands, '_asking_streams', lambda chat: [])
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


def test_her_remove_waits_and_takes_her_own_turn_too(sm):
    sm.streaming = True
    out = commands.run('/remove 2', 'c', by='ai')
    assert out['ok'] and _users(sm) == ['u1', 'u2', 'u3']
    sm.rows = sm.rows + [{'role': 'assistant', 'content': 'done'}]   # her reply lands after the plan
    commands.finish_turn(sm, 'c')                                     # a stream is still live: holds
    assert _users(sm) == ['u1', 'u2', 'u3']
    sm.streaming = False
    commands.finish_turn(sm, 'c')
    assert sm.rows == []                                              # two before hers, and hers


def test_her_cut_leaves_a_turn_queued_after_hers(sm):
    commands.run('/remove 1', 'c', by='ai')
    sm.rows = sm.rows + _turn(4)
    commands.finish_turn(sm, 'c')
    assert _users(sm) == ['u1', 'u4']


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
    asking = SimpleNamespace(cancel_flag=False)
    commands._asking_streams = lambda chat: [asking]
    commands.run('/remove 1', 'c', by='ai')
    asking.cancel_flag = True
    commands.finish_turn(sm, 'c')
    assert sm.rows == before and not commands._pending


def test_the_next_turns_clean_end_does_not_run_a_stopped_turns_cut(sm):
    before = list(sm.rows)
    stopped = SimpleNamespace(cancel_flag=False)
    commands._asking_streams = lambda chat: [stopped]
    commands.run('/remove 1', 'c', by='ai')
    stopped.cancel_flag = True
    sm.streaming = True                  # the next turn is running
    commands.finish_turn(sm, 'c')
    sm.streaming = False                 # and ends clean
    commands.finish_turn(sm, 'c')
    assert sm.rows == before


def test_stopping_a_later_turn_keeps_a_clean_turns_cut(sm):
    commands._asking_streams = lambda chat: [SimpleNamespace(cancel_flag=False)]
    commands.run('/remove 1', 'c', by='ai')
    commands.finish_turn(sm, 'c')        # the later stopped stream isn't an owner
    assert _users(sm) == ['u1']


def test_her_cap_refuses_without_naming_a_number(sm, monkeypatch):
    monkeypatch.setattr('config.AI_REMOVE_MAX_TURNS', 1, raising=False)
    out = commands.run('/remove 2', 'c', by='ai')
    assert not out['ok'] and not any(ch.isdigit() for ch in out['say']) and not commands._pending
    out = commands.run('/remove 999', 'c', by='ai')
    assert not out['ok'] and not any(ch.isdigit() for ch in out['say'])
    monkeypatch.setattr('config.AI_REMOVE_MAX_TURNS', 5, raising=False)
    out = commands.run('/remove 99', 'c', by='ai')          # under no cap: still no count leaks
    assert not out['ok'] and not any(ch.isdigit() for ch in out['say'])


def test_the_cap_is_hers_alone(sm, monkeypatch):
    monkeypatch.setattr('config.AI_REMOVE_MAX_TURNS', 1, raising=False)
    assert commands.run('/remove 3', 'c')['ok']


def _her_cut(sm, n=1):
    sm.rows = sm.rows + [{'role': 'user', 'content': 'please'}, {'role': 'assistant', 'content': 'ok'}]
    assert commands.run(f'/remove {n}', 'c', by='ai')['ok']
    commands.finish_turn(sm, 'c')


def test_undo_puts_her_cut_back_in_place_and_only_once(sm):
    before = list(sm.rows) + [{'role': 'user', 'content': 'please'}, {'role': 'assistant', 'content': 'ok'}]
    _her_cut(sm, 2)
    assert _users(sm) == ['u1']
    sm.rows = sm.rows + [{'role': 'user', 'content': 'later'}]
    assert commands.run('/undo', 'c')['ok']
    assert sm.rows == before + [{'role': 'user', 'content': 'later'}]
    assert not commands.run('/undo', 'c')['ok']


def test_undo_refuses_when_what_came_before_changed(sm):
    _her_cut(sm)
    sm.rows[0] = {'role': 'user', 'content': 'edited'}
    before = list(sm.rows)
    assert not commands.run('/undo', 'c')['ok'] and sm.rows == before


def test_undo_never_doubles_rows_when_the_cut_did_not_land(sm):
    before = list(sm.rows)
    plan = sm.plan_turn_cut('c', 1)[1]
    sm.slot = {'rows': before[plan['start']:plan['end']], 'start': plan['start'], 'turns': 1,
               'digest': sm.messages_digest(before[:plan['start']])}
    assert commands.run('/undo', 'c')['ok'] and sm.rows == before and sm.slot is None


def test_no_undo_copy_means_no_cut(sm):
    sm.seal = True
    before = list(sm.rows)
    commands.run('/remove 1', 'c', by='ai')
    commands.finish_turn(sm, 'c')
    assert sm.rows == before


def test_she_cannot_call_undo_and_the_persons_own_remove_keeps_no_copy(sm):
    assert not commands.run('/undo', 'c', by='ai')['ok']
    commands.run('/remove 1', 'c')
    assert sm.slot is None


def test_remove_keeps_the_game_and_phone_call_rules(sm, monkeypatch):
    before = list(sm.rows)
    monkeypatch.setattr(commands, '_call_chats', lambda: {'c'})
    assert not commands.run('/remove 1', 'c')['ok'] and sm.rows == before
    monkeypatch.setattr(commands, '_call_chats', lambda: set())
    monkeypatch.setattr(sm, 'read_chat_settings', lambda chat: {'mode': 'game'})
    assert not commands.run('/remove 1', 'c', by='ai')['ok'] and not commands._pending


@pytest.mark.parametrize('line', ['/status', '/context', '/remove', '/remove 1'])
def test_a_chat_with_a_bad_row_points_at_repair(sm, monkeypatch, line):
    monkeypatch.setattr(sm, 'export_chat', lambda chat: None)
    assert '/repair' in commands.run(line, 'c')['say']


def test_a_failed_cut_never_names_a_private_chat(sm, monkeypatch):
    told = []
    monkeypatch.setattr(commands, '_tell', lambda chat, title, level='info': told.append(title))
    commands.run('/remove 1', 'c', by='ai')
    sm.rows[0] = {'role': 'user', 'content': 'edited'}
    commands.finish_turn(sm, 'c')
    assert told == ['AI could not remove turns']


def test_a_sealed_chats_row_stays_out_of_the_notification_center(monkeypatch):
    seen = {}
    monkeypatch.setattr(commands, '_sm', lambda: SimpleNamespace(read_chat_settings=lambda chat: None))
    monkeypatch.setattr('core.notify.notify', lambda title, level, **k: seen.update(k))
    commands._tell('c', 'x')
    assert seen['private'] is True


def test_slash_command_is_hidden_until_turned_on():
    from functions import slash_command
    assert slash_command.SETTINGS_GATED == {'slash_command': 'AI_SLASH_COMMANDS_ENABLED'}
    d = json.loads((ROOT / 'core/settings_defaults.json').read_text(encoding='utf-8'))
    assert d['tools']['AI_SLASH_COMMANDS_ENABLED'] is False and d['tools']['AI_REMOVE_MAX_TURNS'] == 5


def test_slash_command_refuses_when_off(monkeypatch):
    from functions import slash_command
    monkeypatch.setattr('config.AI_SLASH_COMMANDS_ENABLED', False, raising=False)
    assert slash_command.execute('slash_command', {'command': '/remove 1'})[1] is False


def test_odd_tool_call_shapes_do_not_kill_a_turn():
    rows = [{'role': 'assistant', 'content': '', 'tool_calls': [{'id': 't', 'function': 'look'}]},
            {'role': 'tool', 'tool_call_id': 't', 'content': 'r'}]
    assert budget._parts(rows, None, 'patches')['calls'] > 0
    budget._expire(rows, 0, lambda name: None)


def test_parallel_results_land_on_their_own_tools():
    rows = [{'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'a', 'function': {'name': 'one'}},
                                                                {'id': 'b', 'function': {'name': 'two'}}]},
            {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'a', 'content': 'short'},
                                         {'type': 'tool_result', 'tool_use_id': 'b', 'content': 'much longer words ' * 20}]}]
    by = budget._parts(rows, None, 'patches')['by_tool']
    assert by['one']['runs'] == by['two']['runs'] == 1 and by['two']['results'] > by['one']['results'] > 0


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
    assert {'compact', 'context', 'continue', 'help', 'prompt', 'remove', 'repair', 'replay', 'retry', 'status',
            'trim', 'undo'} <= set(names) and names == sorted(names)


@pytest.mark.parametrize('name', ['retry', 'continue', 'replay'])
def test_page_actions_leave_the_history_alone_and_she_cannot_call_them(sm, name):
    before = list(sm.rows)
    assert commands.run(f'/{name}', 'c') == {'ok': True, 'do': name}
    assert not commands.run(f'/{name}', 'c', by='ai')['ok']
    assert sm.rows == before


def test_she_still_only_has_remove():
    assert set(commands.ai_commands()) == {'remove'}


def test_a_command_with_no_args_refuses_them(sm):
    assert not commands.run('/retry please', 'c')['ok']


@pytest.mark.parametrize('name', ['trim', 'repair', 'prompt'])
def test_dialogs_open_for_this_chat(sm, name):
    assert commands.run(f'/{name}', 'c') == {'ok': True, 'open': name, 'chat': 'c'}


@pytest.mark.parametrize('name', ['trim', 'compact'])
def test_a_games_transcript_is_off_limits(sm, monkeypatch, name):
    monkeypatch.setattr(sm, 'read_chat_settings', lambda chat: {'mode': 'game'})
    assert not commands.run(f'/{name}', 'c')['ok']


def test_help_lists_all_or_one(sm):
    assert 'remove' in [c['name'] for c in commands.run('/help', 'c')['commands']]
    assert [c['name'] for c in commands.run('/help /remove', 'c')['commands']] == ['remove']
    assert not commands.run('/help nope', 'c')['ok']


def test_status_reads_the_chat_it_was_asked_about(sm, monkeypatch):
    monkeypatch.setattr(sm, 'read_chat_settings', lambda chat: {'persona': 'p1', 'llm_primary': 'claude'})
    out = commands.run('/status', 'c')
    assert (out['open'], out['chat'], out['persona'], out['provider']) == ('status', 'c', 'p1', 'claude')
    assert out['turns'] == 3 and out['messages'] == len(sm.rows) and out['context']['used'] > 0


def _wire():
    return [{'role': 'system', 'content': 'be kind'},
            {'role': 'user', 'content': 'hello there'},
            {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 't1', 'function': {'name': 'look', 'arguments': '{}'}}],
             'thinking_raw': [{'type': 'thinking', 'thinking': 'hmm'}]},
            {'role': 'tool', 'tool_call_id': 't1', 'name': 'look', 'content': 'a long result ' * 50},
            {'role': 'user', 'content': 'ghost words', '_inject': True},
            {'role': 'assistant', 'content': 'a reply'}]


_TOOLS = [{'type': 'function', 'function': {'name': 'look', 'description': 'look around'}},
          {'type': 'function', 'function': {'name': 'idle', 'description': 'never run'}}]


def test_context_buckets_add_up_to_what_the_budget_counts():
    p = budget._parts(_wire(), _TOOLS, 'patches')
    raw = budget.fit(_wire(), _TOOLS, limit=100000).raw
    assert sum(v for k, v in p.items() if k not in ('by_tool', 'image_count')) == raw
    assert p['by_tool']['look']['runs'] == 1 and p['by_tool']['look']['results'] == p['results']
    assert p['by_tool']['idle']['schema'] > 0 and p['by_tool']['idle']['runs'] == 0
    assert all(p[k] for k in ('system', 'schemas', 'you', 'her', 'calls', 'results', 'injected', 'thinking'))


def test_context_reports_the_last_send_with_the_biggest_tool_first(monkeypatch):
    budget.forget('ctx')
    try:
        budget.fit(_wire(), _TOOLS, chat='ctx', limit=100000)
        r = budget.report('ctx', _wire())
        assert r['meter']['measured'] and r['tools'][0]['name'] == 'look'
        assert sum(r['parts'].values()) <= r['meter']['used'] and r['parts']['schemas'] > 0
    finally:
        budget.forget('ctx')


def test_context_before_a_send_reads_the_stored_chat_and_its_thinking():
    budget.forget('ctx')
    rows = [{'role': 'user', 'content': 'hi'}, {'role': 'assistant', 'content': 'hey', 'thinking': 'a long thought'}]
    r = budget.report('ctx', rows)
    assert not r['meter']['measured'] and r['parts']['schemas'] == 0
    assert r['thinking_stored'] > 0 and r['parts']['thinking'] == 0


def test_thinking_held_in_both_fields_counts_once():
    budget.forget('ctx')
    rows = [{'role': 'assistant', 'content': 'hey', 'thinking': 'one thought',
             'thinking_raw': [{'type': 'thinking', 'thinking': 'one thought'}]}]
    r = budget.report('ctx', rows)
    assert r['thinking_stored'] == budget.count_tokens(json.dumps(rows[0]['thinking_raw'], ensure_ascii=False, default=str))
    assert r['parts']['thinking'] == 0


def test_a_picture_counts_as_an_image_not_as_text():
    p = budget._parts([{'role': 'user', 'content': [{'type': 'text', 'text': 'see'}, {'type': 'image', 'data': 'AAAA'}]}],
                      None, 'patches')
    assert p['image_count'] == 1 and p['images'] > 0 and p['you'] == budget.count_tokens('see')


def test_context_refuses_a_chat_that_is_not_there(sm):
    assert not commands.run('/context', 'gone')['ok']


def test_plugin_commands_register_refuse_collisions_and_leave_with_the_plugin(sm):
    try:
        assert commands.register('lights', lambda c, a, b: {'ok': True, 'say': a}, args='x', plugin='p1')
        assert commands.run('/lights on', 'c') == {'ok': True, 'say': 'on'}
        assert not commands.register('lights', lambda c, a, b: None, plugin='p2')
        assert not commands.register('remove', lambda c, a, b: None, plugin='p2')
        assert not commands.register('bad name', lambda c, a, b: None, plugin='p2')
        assert not commands.run('/lights on', 'c', by='ai')['ok']
        assert commands._commands['remove']['plugin'] is None
    finally:
        commands.unregister_plugin('p1')
    assert 'lights' not in commands._commands


def test_a_plugin_handler_returning_junk_still_answers(sm):
    try:
        commands.register('junk', lambda c, a, b: 'words', plugin='p1')
        assert commands.run('/junk', 'c') == {'ok': True}
    finally:
        commands.unregister_plugin('p1')


def test_one_compress_dialog_and_the_menu_is_wired():
    static = ROOT / 'interfaces/web/static'
    manage = (static / 'views/chat-manage.js').read_text(encoding='utf-8')
    assert 'cm-c-go' not in manage and 'cm-trim-go' not in manage and '_llmCache' not in manage
    assert "compact: ({ chat }) => openCompressModal(chat)" in (static / 'handlers/commands.js').read_text(encoding='utf-8')
    src = (static / 'handlers/send-handlers.js').read_text(encoding='utf-8')
    assert 'if (onCommandKey(e)) return;' in src and 'onCommandInput(input)' in src


def test_composer_catches_commands_before_the_turn():
    src = (ROOT / 'interfaces/web/static/handlers/send-handlers.js').read_text(encoding='utf-8')
    assert src.index('isCommand(txt)') < src.index('dispatch(Events.USER_SENT')
    assert 'input.value = typed' in src


def test_a_regen_resends_what_the_message_carried():
    src = (ROOT / 'interfaces/web/static/chat.js').read_text(encoding='utf-8')
    assert src.index('picturesToResend(userRow?.images)') < src.index('await api.removeFromUserMessage(userMessage')


@pytest.fixture
def store(tmp_path, monkeypatch):
    """The real store, one chat of three turns; the second carries a stored picture."""
    from unittest.mock import patch
    defaults = {'prompt': 'default'}
    monkeypatch.setattr('core.chat.history.publish', lambda *a, **k: None)
    with patch('core.chat.history.get_system_defaults', side_effect=lambda: dict(defaults)), \
         patch('core.chat.history.get_user_defaults', side_effect=lambda: dict(defaults)):
        from core.chat.history import ChatSessionManager
        m = ChatSessionManager(history_dir=str(tmp_path))
        m.create_chat('s')
        rows = _turn(1) + _turn(2) + _turn(3)
        rows[3]['content'] = 'see <<IMG::tool:pic1>>'
        assert m.replace_messages('s', rows)[0]
        with m._get_connection() as conn:
            conn.execute("INSERT INTO tool_images (id, chat_name, data, created_at) VALUES ('pic1', 's', x'00', 'now')")
            conn.commit()
        yield m


def _pictures(m):
    with m._get_connection() as conn:
        return [r['id'] for r in conn.execute('SELECT id FROM tool_images')]


def test_undo_on_the_real_store_keeps_rows_and_their_pictures(store):
    before = store.export_chat('s')['messages']
    ok, plan = store.plan_turn_cut('s', 1, keep_last=1)
    assert ok and store.apply_turn_cut('s', plan, undo=True, with_own_turn=True)[0]
    assert len(store.export_chat('s')['messages']) == len(before) - 4
    store._prune_orphaned_tool_images('s')
    assert _pictures(store) == ['pic1']                   # /undo still owes it
    assert store.undo_turn_cut('s') == (2, None)
    assert store.export_chat('s')['messages'] == before
    assert store.undo_turn_cut('s')[0] == 0


def test_the_undo_copy_goes_with_the_chat(store):
    ok, plan = store.plan_turn_cut('s', 1, keep_last=1)
    assert ok and store.apply_turn_cut('s', plan, undo=True)[0]
    store.delete_chat('s')
    store.create_chat('s')
    assert store.undo_turn_cut('s')[0] == 0
