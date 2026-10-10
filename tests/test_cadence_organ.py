"""The cadence organ + perception inbox (Game Room F3, 2026-09-09).

Her unprompted turns: armed per chat with a TTL, a random gap in [min, max]
or a game event, one real turn through THE engine door (begin_stream by
name + chat_stream, VOICE_TURN events), never two turns on one chat, frames
from the inbox for that turn only.
"""
import threading
import time
import types
from unittest.mock import MagicMock, patch

import pytest

from core import cadence, perception
from core.chat.chat import ChatBusy


@pytest.fixture(autouse=True)
def clean():
    cadence._records.clear()
    perception._inbox.clear()
    cadence._system = None
    yield
    cadence._records.clear()
    perception._inbox.clear()
    cadence._system = None


# ── the inbox ───────────────────────────────────────────────────────────────

def test_deposit_latest_wins_and_take_clears():
    assert perception.deposit('c', text='first') == {'frames': 0, 'text': 5}
    perception.deposit('c', text='second')
    got = perception.take('c')
    assert got['text'] == 'second' and got['frames'] == []
    assert perception.take('c') is None
    assert perception.deposit('c') is None and perception.deposit('', text='x') is None


def test_clean_frames_strips_data_urls_and_refuses_junk():
    b64 = 'iVBORw0KGgo='
    fr = perception.clean_frames([
        {'data': f'data:image/png;base64,{b64}'},
        {'data': b64, 'media_type': 'image/jpeg'},
        {'data': b64, 'media_type': 'image/gif'},          # not a vision type here
        {'data': 'not base64 !!', 'media_type': 'image/png'},
        {'data': 'x' * (perception.MAX_FRAME_BYTES + 1), 'media_type': 'image/png'},
        'nope',
    ])
    assert fr == [{'data': b64, 'media_type': 'image/png'}, {'data': b64, 'media_type': 'image/jpeg'}]
    # the cap keeps the LATEST frames (after junk is dropped)
    many = perception.clean_frames([{'data': b64, 'media_type': 'image/png'}] + [{'data': b64, 'media_type': 'image/webp'}] * 20)
    assert len(many) == perception.MAX_FRAMES and all(f['media_type'] == 'image/webp' for f in many)


def test_stale_deposit_is_dropped(monkeypatch):
    perception.deposit('c', text='old')
    perception._inbox['c']['at'] -= perception.TTL_S + 1
    assert perception.take('c') is None


# ── the registry ────────────────────────────────────────────────────────────

def test_arm_timer_sets_a_gap_and_keepalive_extends_ttl(monkeypatch):
    monkeypatch.setattr(cadence.random, 'uniform', lambda lo, hi: hi)
    st = cadence.arm('c', mode='timer', min_s=10, max_s=20, owner='t', ttl=5)
    assert st['armed'] and st['mode'] == 'timer' and 19 < st['next_in'] <= 20
    first_next = cadence._records['c']['next_at']
    st2 = cadence.arm('c', mode='timer', min_s=10, max_s=20, owner='t', ttl=50)   # keepalive
    assert cadence._records['c']['next_at'] == first_next                      # the clock is kept
    assert st2['ttl_in'] > 40
    cadence.arm('c', mode='timer', min_s=10, max_s=30, owner='t')               # range change → new gap
    assert cadence._records['c']['next_at'] != first_next


def test_turn_mode_disarms_and_event_mode_has_no_clock():
    cadence.arm('c', mode='timer', min_s=5, max_s=5)
    assert cadence.arm('c', mode='turn')['armed'] is False
    st = cadence.arm('c', mode='event', min_s=5, max_s=5)
    assert st['armed'] and st['next_in'] is None and st['pending'] is False


def test_pause_resume_and_poke(monkeypatch):
    monkeypatch.setattr(cadence.random, 'uniform', lambda lo, hi: lo)
    cadence.arm('c', mode='timer', min_s=100, max_s=100)
    assert cadence.pause('c', True)['paused'] is True and cadence.status('c')['next_in'] is None
    cadence._records['c']['next_at'] = time.time() - 5     # would have fired while paused
    st = cadence.pause('c', False)
    assert st['paused'] is False and st['next_in'] > 90    # resume = a fresh gap, not an instant turn
    st = cadence.poke('c', 'wave 3 cleared')
    assert st['pending'] is True and st['next_in'] < 1     # a poke pulls a timer's turn to the floor
    assert cadence.poke('nope') is None


def test_due_rules():
    now = time.time()
    rec = {'paused': False, 'running': False, 'last_at': None, 'min_s': 10, 'mode': 'event',
           'pending': None, 'next_at': None}
    assert not cadence._due(rec, now)
    rec['pending'] = True
    assert cadence._due(rec, now)                          # first event: no last turn to gap from
    rec['last_at'] = now - 3
    assert not cadence._due(rec, now)                      # inside the min gap
    rec['last_at'] = now - 11
    assert cadence._due(rec, now)
    rec.update(mode='timer', pending=None, next_at=now - 1)
    assert cadence._due(rec, now)
    rec['paused'] = True
    assert not cadence._due(rec, now)


def test_tick_expires_dead_records_and_fires_due_ones(monkeypatch):
    fired = []
    monkeypatch.setattr(cadence, '_fire', lambda rec: fired.append(rec['chat']))
    monkeypatch.setattr(cadence.threading, 'Thread', lambda target, args, daemon, name: types.SimpleNamespace(start=lambda: target(*args)))
    cadence.arm('dead', mode='timer', min_s=1, max_s=1, ttl=0.01)
    cadence.arm('live', mode='timer', min_s=1, max_s=1, ttl=60)
    cadence._records['live']['next_at'] = time.time() - 1
    time.sleep(0.02)
    cadence._tick()
    assert 'dead' not in cadence._records and fired == ['live']


# ── the turn door ───────────────────────────────────────────────────────────

class _Stream:
    def __init__(self, events, rows=0):
        self.events = events
        self.rows = rows            # rows this turn "persists" on the live list
        self.sink = None
        self.seen = None
        self.suppress_tts = False
        self.images_ephemeral = False

    def chat_stream(self, text, images=None):
        self.seen = (text, images)
        if self.sink is not None:
            self.sink.extend([{'role': 'row'}] * self.rows)
        for ev in self.events:
            yield ev


def _system(events, active='other', busy=False, rows=0):
    stream = _Stream(events, rows)
    llm = MagicMock()
    llm.begin_stream = MagicMock(side_effect=(ChatBusy('c') if busy else None), return_value=(stream, 'sid', 'c'))
    llm.session_manager.get_active_chat_name.return_value = active
    llm.session_manager.is_streaming.return_value = False
    stream.sink = llm.session_manager.current_chat.messages = [{'role': 'user'}, {'role': 'assistant'}]
    sysobj = types.SimpleNamespace(llm_chat=llm, tts=MagicMock())
    return sysobj, stream, llm


def test_run_turn_uses_the_engine_door_and_publishes_voice_turn():
    sysobj, stream, llm = _system([{'type': 'content', 'text': 'Nice '}, {'type': 'content', 'text': 'wave.'},
                                   {'type': 'final', 'text': 'Nice wave.', 'cancelled': False}])
    cadence._system = sysobj
    published = []
    with patch('core.cadence.publish', side_effect=lambda et, data=None: published.append((et, data))):
        out = cadence.run_turn('c', 'the cue', images=[{'data': 'x', 'media_type': 'image/jpeg'}], speak='browser')
    assert out == 'Nice wave.'
    llm.begin_stream.assert_called_once_with('c', exclusive=True)
    assert stream.suppress_tts is True and stream.images_ephemeral is True
    assert stream.seen == ('the cue', [{'data': 'x', 'media_type': 'image/jpeg'}])
    llm.end_stream.assert_called_once_with('sid', 'c')
    names = [et for et, _ in published]
    assert names == ['voice_turn_start', 'voice_turn_chunk', 'voice_turn_chunk', 'voice_turn_end']
    start, end = published[0][1], published[-1][1]
    assert start['chat'] == 'c' and start['foreign'] is True and start['user_text'] == 'the cue' and start['source'] == 'cadence'
    assert end['text'] == 'Nice wave.' and end['speak'] == 'browser'
    sysobj.tts.speak.assert_not_called()


def test_run_turn_speakers_lane_and_cancel():
    sysobj, stream, llm = _system([{'type': 'content', 'text': 'Hi'}, {'type': 'final', 'text': 'Hi', 'cancelled': False}], active='c')
    cadence._system = sysobj
    with patch('core.cadence.publish') as pub:
        cadence.run_turn('c', 'cue', speak='speakers')
    # speak() now carries the SESSION chat's settings for the privacy gate
    # (broadsword H3) — the text is still the answer.
    assert sysobj.tts.speak.call_count == 1 and sysobj.tts.speak.call_args.args == ('Hi',)
    assert 'chat_settings' in sysobj.tts.speak.call_args.kwargs
    assert pub.call_args_list[0][0][1]['foreign'] is False            # armed on the active chat = not foreign
    sysobj2, stream2, llm2 = _system([{'type': 'content', 'text': 'partial'}, {'type': 'final', 'text': 'partial', 'cancelled': True}])
    cadence._system = sysobj2
    with patch('core.cadence.publish', side_effect=lambda et, data=None: None):
        cadence.run_turn('c', 'cue', speak='speakers')
    sysobj2.tts.speak.assert_not_called()                              # cancelled = not spoken


def test_fire_skips_and_stretches_when_the_chat_is_busy(monkeypatch):
    monkeypatch.setattr(cadence.random, 'uniform', lambda lo, hi: lo)
    sysobj, stream, llm = _system([], busy=True)
    cadence._system = sysobj
    cadence.arm('c', mode='timer', min_s=10, max_s=100)
    rec = cadence._records['c']
    rec['running'] = True
    cadence._fire(rec)
    assert rec['skips'] == 1 and rec['running'] is False
    assert 14 < rec['next_at'] - time.time() <= 15.1                    # 10 * (1 + 0.5*1)
    # her own live stream on the chat = the same skip, before the door
    sysobj2, _, llm2 = _system([{'type': 'final', 'text': 'x'}])
    llm2.session_manager.is_streaming.return_value = True
    cadence._system = sysobj2
    cadence._fire(rec)
    assert rec['skips'] == 2 and not llm2.begin_stream.called


def test_fire_takes_the_inbox_and_uses_the_arming_prompt(monkeypatch):
    sysobj, stream, llm = _system([{'type': 'content', 'text': 'Ha.'}, {'type': 'final', 'text': 'Ha.', 'cancelled': False}])
    cadence._system = sysobj
    seen = {}

    def prompt(percept, rec):
        seen['percept'] = percept
        return f"cue: {percept['text']}"
    cadence.arm('c', mode='timer', min_s=1, max_s=1, send_frames=True, frames_per_tick=2, prompt=prompt)
    perception.deposit('c', frames=[{'data': 'aGk=', 'media_type': 'image/jpeg'}] * 5, text='wave 4')
    rec = cadence._records['c']
    with patch('core.cadence.publish'):
        cadence._fire(rec)
    assert seen['percept']['text'] == 'wave 4'
    assert stream.seen[0] == 'cue: wave 4' and len(stream.seen[1]) == 2          # last N frames only
    assert rec['fired'] == 1 and rec['skips'] == 0 and rec['last_at'] is not None
    assert perception.take('c') is None                                          # taken, not left behind
    # frames off → none reach the model even when deposited
    cadence.arm('c', mode='timer', min_s=1, max_s=1, send_frames=False, prompt=prompt)
    perception.deposit('c', frames=[{'data': 'aGk=', 'media_type': 'image/jpeg'}], text='t')
    with patch('core.cadence.publish'):
        cadence._fire(cadence._records['c'])
    assert stream.seen[1] is None


def test_default_prompt_carries_the_deposit_text():
    assert cadence.default_prompt({'text': 'wave 9'}).startswith('wave 9\n(')
    assert cadence.default_prompt(None).startswith('(Your turn')


# ── the engine flag: frames reach the model this turn only ─────────────────

def test_images_ephemeral_persists_the_words_only():
    import config
    from core.chat.chat_streaming import StreamingChat
    mock_main = MagicMock()
    mock_main.system = None
    fm = mock_main.function_manager
    fm.snapshot_scopes.return_value = {}
    fm.snapshot_executors.return_value = {}
    fm.enabled_tools = []
    fm.last_dangling_toolset = None
    sm = mock_main.session_manager
    sm.get_chat_settings.return_value = {}
    sm.get_active_chat_name.return_value = 't'
    sm._effective_chat_name.return_value = 't'
    sm._in_tool_cycle = False
    mock_main._build_base_messages.return_value = [{'role': 'user', 'content': 'hi'}]
    provider = MagicMock()
    provider.provider_name = 'test'
    provider.model = 'm'
    provider.chat_completion_stream.return_value = iter([{'type': 'content', 'text': 'ok'}, {'type': 'done', 'response': None}])
    mock_main._select_provider.return_value = ('test', provider, '')
    mock_main.tool_engine.extract_function_call_from_text.return_value = None
    frames = [{'data': 'aGk=', 'media_type': 'image/jpeg'}]
    with patch('core.chat.chat_streaming.get_generation_params', return_value={}), \
         patch.object(config, 'TTS_ENABLED', False, create=True), \
         patch.object(config, 'TTS_STREAMING_ENABLED', False, create=True), \
         patch('core.voice_privacy.tts_gate_reason', return_value=''), \
         patch('core.chat.chat_streaming.publish'):
        sc = StreamingChat(mock_main)
        sc.images_ephemeral = True
        list(sc.chat_stream('the cue', images=frames))
        mock_main._build_base_messages.assert_called_with('the cue', images=frames, files=None)   # the model saw them
        sm.add_user_message.assert_called_once_with('the cue')                                    # the row did not
        sm.reset_mock(); mock_main._build_base_messages.reset_mock()
        provider.chat_completion_stream.return_value = iter([{'type': 'content', 'text': 'ok'}, {'type': 'done', 'response': None}])
        sc2 = StreamingChat(mock_main)
        list(sc2.chat_stream('typed', images=frames))
        row = sm.add_user_message.call_args[0][0]
        assert isinstance(row, list) and row[1]['type'] == 'image'                                # the classic lane persists


# ── the thinking budget + no-answer turns (Krem's 8K think loop, 2026-09-09) ─

def test_visible_text_strips_every_think_shape():
    assert cadence.visible_text('<think>hm</think> Nice wave.') == 'Nice wave.'
    assert cadence.visible_text('<think>never closes and keeps going') == ''
    assert cadence.visible_text('tail of a block</think>Real words.') == 'Real words.'
    assert cadence.visible_text('<seed:think>x</seed:think>Hi') == 'Hi'
    assert cadence.visible_text('') == '' and cadence.visible_text(None) == ''


def test_unfinished_thinking_counts_only_the_open_block():
    assert cadence._unfinished_thinking('<think>abc') == 3
    assert cadence._unfinished_thinking('<think>abc</think>done') == 0
    assert cadence._unfinished_thinking('plain') == 0
    assert cadence._unfinished_thinking('<think>a</think><think>bcd') == 3


def test_think_loop_is_cancelled_and_the_pair_dropped():
    big = '<think>' + 'x' * (cadence.THINK_BUDGET_CHARS + 10)
    events = [{'type': 'content', 'text': big[:3000]}, {'type': 'content', 'text': big[3000:]},
              {'type': 'content', 'text': 'more thinking'}, {'type': 'final', 'text': big + 'more thinking', 'cancelled': True}]
    sysobj, stream, llm = _system(events, active='c', rows=2)
    llm.session_manager.remove_last_messages.return_value = True
    cadence._system = sysobj
    published = []
    with patch('core.cadence.publish', side_effect=lambda et, data=None: published.append((et, data))):
        out = cadence.run_turn('c', 'cue', speak='browser')
    assert out == '' and stream.cancel_flag is True
    llm.session_manager.remove_last_messages.assert_called_once_with(2)     # the cue + the think-only row
    end = published[-1][1]
    assert end['text'] == '' and end['speak'] is None and end['dropped'] is True
    sysobj.tts.speak.assert_not_called()


def test_think_only_answer_is_a_no_answer_and_stays_when_not_live():
    sysobj, stream, llm = _system([{'type': 'content', 'text': '<think>all thinking, no words</think>'},
                                   {'type': 'final', 'text': '<think>all thinking, no words</think>', 'cancelled': False}],
                                  active='elsewhere')
    cadence._system = sysobj
    with patch('core.cadence.publish', side_effect=lambda et, data=None: None):
        out = cadence.run_turn('c', 'cue', speak='speakers')
    assert out == ''
    llm.session_manager.remove_last_messages.assert_not_called()             # not the live chat: left in place, logged
    sysobj.tts.speak.assert_not_called()


def test_answer_with_thinking_speaks_only_the_words():
    sysobj, stream, llm = _system([{'type': 'content', 'text': '<think>reasoning</think>'},
                                   {'type': 'content', 'text': 'Solid clear.'},
                                   {'type': 'final', 'text': '<think>reasoning</think>Solid clear.', 'cancelled': False}])
    cadence._system = sysobj
    published = []
    with patch('core.cadence.publish', side_effect=lambda et, data=None: published.append((et, data))):
        out = cadence.run_turn('c', 'cue', speak='speakers')
    assert out == 'Solid clear.' and published[-1][1]['text'] == 'Solid clear.'
    assert sysobj.tts.speak.call_count == 1 and sysobj.tts.speak.call_args.args == ('Solid clear.',)
    llm.session_manager.remove_last_messages.assert_not_called()


def test_event_every_n_pokes_and_force():
    """Every-N (Dark Horse 2026-09-10): only the Nth poke since the last one
    that became her turn is pending; `force` = a terminal moment that skips
    the count; keepalive re-arms keep the count."""
    cadence.arm('e', mode='event', min_s=1, max_s=1, every=3)
    assert cadence.status('e')['every'] == 3 and cadence.status('e')['pokes'] == 0
    assert cadence.poke('e', 'w1')['pending'] is False and cadence.status('e')['pokes'] == 1
    assert cadence.poke('e', 'w2')['pending'] is False
    st = cadence.poke('e', 'w3')
    assert st['pending'] is True and st['pokes'] == 0                  # the 3rd is hers
    cadence._records['e']['pending'] = None                            # (fired)
    assert cadence.poke('e', 'w4')['pending'] is False
    assert cadence.poke('e', 'the castle fell', force=True)['pending'] is True
    cadence._records['e']['pending'] = None
    cadence.poke('e', 'w5')
    cadence.arm('e', mode='event', min_s=1, max_s=1, every=3)          # keepalive
    assert cadence.status('e')['pokes'] == 1
    # every=1 (the default) = every poke, as before
    cadence.arm('f', mode='event', min_s=1, max_s=1)
    assert cadence.poke('f', 'x')['pending'] is True


def test_off_disarms_and_event_mode_owns_its_floor():
    """One lever per nature (2026-09-10): 'off' disarms like 'turn'; event
    mode ignores the caller's min/max — its clock is the organ's floor."""
    cadence.arm('c', mode='timer', min_s=100, max_s=100)
    assert cadence.arm('c', mode='off')['armed'] is False and 'c' not in cadence._records
    st = cadence.arm('c', mode='event', min_s=60, max_s=180, every=2)
    assert st['min_s'] == st['max_s'] == cadence.EVENT_FLOOR_S and st['every'] == 2
    st = cadence.arm('c', mode='event', min_s=5, max_s=5, every=2)      # keepalive with other numbers
    assert st['min_s'] == cadence.EVENT_FLOOR_S and st['next_in'] is None


def test_drop_counts_the_rows_this_turn_added_not_a_fixed_two():
    """A think loop after a tool call persisted FOUR rows (cue, assistant+
    tool_calls, tool, think-only) — dropping two orphaned the tool_call
    (provider 400 next turn). The drop is the live list's growth."""
    big = '<think>' + 'x' * (cadence.THINK_BUDGET_CHARS + 10)
    events = [{'type': 'tool_start', 'name': 'x'}, {'type': 'tool_end', 'name': 'x'},
              {'type': 'content', 'text': big}, {'type': 'final', 'text': big, 'cancelled': True}]
    sysobj, stream, llm = _system(events, active='c', rows=4)
    llm.session_manager.remove_last_messages.return_value = True
    cadence._system = sysobj
    with patch('core.cadence.publish'):
        cadence.run_turn('c', 'cue')
    llm.session_manager.remove_last_messages.assert_called_once_with(4)
    # nothing measurable (not the live chat) = nothing touched
    sysobj2, _, llm2 = _system(events, active='elsewhere', rows=4)
    cadence._system = sysobj2
    with patch('core.cadence.publish'):
        cadence.run_turn('c', 'cue')
    llm2.session_manager.remove_last_messages.assert_not_called()


def test_tool_only_turn_is_an_answer_not_a_drop():
    """She moved (a tool call) and said nothing: keep the rows, speak nothing."""
    events = [{'type': 'tool_start', 'name': 'poker_move'}, {'type': 'tool_end', 'name': 'poker_move'},
              {'type': 'final', 'text': '', 'cancelled': False}]
    sysobj, stream, llm = _system(events, active='c', rows=4)
    cadence._system = sysobj
    published = []
    with patch('core.cadence.publish', side_effect=lambda et, data=None: published.append((et, data))):
        out = cadence.run_turn('c', 'cue', speak='speakers')
    assert out == ''
    llm.session_manager.remove_last_messages.assert_not_called()
    end = published[-1][1]
    assert end['dropped'] is False and end['speak'] is None and end['text'] == ''
    sysobj.tts.speak.assert_not_called()


def test_refused_turn_is_never_her_answer():
    """A by-name turn the engine refuses (🔒 chat unreachable) yields its
    notice as content + final error — it was captioned and SPOKEN as hers."""
    notice = "🔒 Chat 'c' isn't reachable right now (missing or sealed) — the turn was not run."
    events = [{'type': 'content', 'text': notice}, {'type': 'final', 'text': notice, 'cancelled': False, 'error': True}]
    sysobj, stream, llm = _system(events, active='c', rows=0)
    cadence._system = sysobj
    published = []
    with patch('core.cadence.publish', side_effect=lambda et, data=None: published.append((et, data))), \
            pytest.raises(RuntimeError):
        cadence.run_turn('c', 'cue', speak='speakers')
    end = published[-1][1]
    assert end['text'] == '' and end['speak'] is None
    sysobj.tts.speak.assert_not_called()
    llm.session_manager.remove_last_messages.assert_not_called()


# ── the device lane (satellites, 2026-09-27) ────────────────────────────────

def test_run_turn_device_lane_speaks_on_that_device():
    sysobj, stream, llm = _system([{'type': 'final', 'text': 'It is noon.', 'cancelled': False}])
    llm.session_manager.get_settings_for.return_value = {'private_chat': False, 'tts': 'x'}
    cadence._system = sysobj
    published = []
    with patch('core.cadence.publish', side_effect=lambda et, data=None: published.append((et, data))), \
         patch('core.devices.voice.say', return_value=('Said there', True)) as say:
        out = cadence.run_turn('kitchen-chat', 'what time is it', speak='device:kitchen', source='device:kitchen')
    assert out == 'It is noon.'
    say.assert_called_once_with('kitchen', 'It is noon.',
                                chat_settings={'private_chat': False, 'tts': 'x'})
    sysobj.tts.speak.assert_not_called()                 # never on this machine's speakers too
    assert published[0][1]['source'] == 'device:kitchen'
    assert published[-1][1]['speak'] == 'device:kitchen'


def test_run_turn_shows_its_own_events_to_the_hook():
    events = [{'type': 'tool_start', 'name': 'web_search'}, {'type': 'tool_end', 'name': 'web_search'},
              {'type': 'final', 'text': 'Done.', 'cancelled': False}]
    sysobj, stream, llm = _system(events)
    cadence._system = sysobj
    seen = []
    with patch('core.cadence.publish'):
        assert cadence.run_turn('c', 'cue', on_event=seen.append) == 'Done.'
    assert [e['type'] for e in seen] == ['tool_start', 'tool_end', 'final']
    sysobj2, _, _ = _system(events)
    cadence._system = sysobj2

    def broken(event):
        raise RuntimeError('hook fell over')
    with patch('core.cadence.publish'):
        assert cadence.run_turn('c', 'cue', on_event=broken) == 'Done.'     # the turn still stands


def test_an_unreachable_chat_is_refused_before_anything_is_published():
    # A missing, sealed or unreadable chat (settings None) used to get its
    # text published as VOICE_TURN_START and only then be refused inside the
    # engine (scout C, 2026-10-06). Now it is refused first, quietly.
    sysobj, stream, llm = _system([{'type': 'final', 'text': 'Hi', 'cancelled': False}])
    llm.session_manager.get_settings_for.return_value = None       # a sealed chat
    cadence._system = sysobj
    with patch('core.cadence.publish') as pub, pytest.raises(RuntimeError, match="isn't reachable"):
        cadence.run_turn('c', 'cue', speak='device:pi')
    pub.assert_not_called()
    llm.begin_stream.assert_not_called()


def test_device_lane_fails_closed_and_quietly():
    sysobj, stream, llm = _system([{'type': 'final', 'text': 'Hi', 'cancelled': False}])
    llm.session_manager.get_settings_for.return_value = {'private_chat': True}   # a private chat
    cadence._system = sysobj
    with patch('core.cadence.publish'), \
         patch('core.devices.voice.say', side_effect=RuntimeError('satellite fell over')) as say:
        assert cadence.run_turn('c', 'cue', speak='device:pi') == 'Hi'      # the turn still stands
    assert say.call_args.kwargs['chat_settings'] == {'private_chat': True}
    sysobj2, _, _ = _system([{'type': 'final', 'text': 'partial', 'cancelled': True}])
    cadence._system = sysobj2
    with patch('core.cadence.publish'), patch('core.devices.voice.say') as say2:
        cadence.run_turn('c', 'cue', speak='device:pi')
    say2.assert_not_called()                                           # cancelled = not spoken


# --- the device lane, sentence by sentence -------------------------------------------

def test_stream_speech_makes_her_voice_as_she_writes_and_skips_the_whole_say():
    events = [{'type': 'content', 'text': 'It is noon. '},
              {'type': 'tts_chunk', 'audio_b64': 'QUJD', 'content_type': 'audio/ogg', 'index': 0},
              {'type': 'content', 'text': 'The sun is out.'},
              {'type': 'tts_chunk', 'audio_b64': 'REVG', 'content_type': 'audio/ogg', 'index': 1},
              {'type': 'final', 'text': 'It is noon. The sun is out.', 'cancelled': False}]
    sysobj, stream, llm = _system(events)
    llm.session_manager.get_settings_for.return_value = {'private_chat': False, 'tts': 'x'}
    cadence._system = sysobj
    seen = []
    with patch('core.cadence.publish'), patch('core.devices.voice.say') as say:
        out = cadence.run_turn('kitchen-chat', 'what time is it', speak='device:kitchen',
                               source='device:kitchen', on_event=seen.append, stream_speech=True)
    assert out == 'It is noon. The sun is out.'
    assert stream.suppress_tts is False                  # the pump runs for this turn
    assert stream.tts_split_override == 'sentence'
    assert stream.tts_force is True
    assert stream.tts_chat_settings == {'private_chat': False, 'tts': 'x'}   # THIS chat gates it
    assert [e['type'] for e in seen if e['type'] == 'tts_chunk'] == ['tts_chunk', 'tts_chunk']
    say.assert_not_called()                              # already spoken, sentence by sentence


def test_stream_speech_falls_back_to_the_whole_reply_when_no_sentence_was_made():
    sysobj, stream, llm = _system([{'type': 'content', 'text': 'Hi.'},
                                   {'type': 'final', 'text': 'Hi.', 'cancelled': False}])
    llm.session_manager.get_settings_for.return_value = {'private_chat': True}
    cadence._system = sysobj
    with patch('core.cadence.publish'), \
         patch('core.devices.voice.say', return_value=('Said there', True)) as say:
        assert cadence.run_turn('c', 'cue', speak='device:pi', stream_speech=True) == 'Hi.'
    say.assert_called_once_with('pi', 'Hi.', chat_settings={'private_chat': True})


def test_stream_speech_means_nothing_off_a_device_lane():
    sysobj, stream, llm = _system([{'type': 'final', 'text': 'Hi.', 'cancelled': False}])
    cadence._system = sysobj
    with patch('core.cadence.publish'):
        cadence.run_turn('c', 'cue', speak='speakers', stream_speech=True)
    assert stream.suppress_tts is True                   # the speakers lane speaks whole, as before
    sysobj.tts.speak.assert_called_once()


def test_a_turn_with_tools_answers_with_every_round_of_her_prose():
    """Round one says 'let me look', a tool runs, round two has the answer.
    The engine's `final` carries round two only; her message is both (over
    MCP, Blue saw only Purple's second half - 2026-10-03)."""
    events = [{'type': 'content', 'text': 'Let me check '}, {'type': 'content', 'text': 'the weather.'},
              {'type': 'tool_start', 'name': 'web_search'}, {'type': 'tool_end', 'name': 'web_search'},
              {'type': 'tool_start', 'name': 'get_website'}, {'type': 'tool_end', 'name': 'get_website'},
              {'type': 'content', 'text': 'It is 64 and clear.'},
              {'type': 'final', 'text': 'It is 64 and clear. (hooked)', 'cancelled': False}]
    sysobj, stream, llm = _system(events)
    cadence._system = sysobj
    with patch('core.cadence.publish') as publish:
        out = cadence.run_turn('c', 'weather?')
    assert out == 'Let me check the weather.\n\nIt is 64 and clear. (hooked)'      # final stands for the last round only
    assert publish.call_args.args[1]['text'] == out


def test_a_tool_round_with_no_words_adds_nothing():
    events = [{'type': 'tool_start', 'name': 'x'}, {'type': 'tool_end', 'name': 'x'},
              {'type': 'content', 'text': '<think>hm</think>'}, {'type': 'tool_start', 'name': 'y'}, {'type': 'tool_end', 'name': 'y'},
              {'type': 'content', 'text': 'Done.'}, {'type': 'final', 'text': 'Done.', 'cancelled': False}]
    sysobj, stream, llm = _system(events)
    cadence._system = sysobj
    with patch('core.cadence.publish'):
        assert cadence.run_turn('c', 'go') == 'Done.'


def test_a_refused_turn_keeps_none_of_the_rounds():
    events = [{'type': 'content', 'text': 'Starting.'}, {'type': 'tool_start', 'name': 'x'}, {'type': 'tool_end', 'name': 'x'},
              {'type': 'final', 'text': 'refused', 'cancelled': False, 'error': True}]
    sysobj, stream, llm = _system(events)
    cadence._system = sysobj
    with patch('core.cadence.publish'), pytest.raises(RuntimeError, match='refused'):
        cadence.run_turn('c', 'go')


def test_keep_prompt_keeps_the_row_when_the_answer_is_empty_or_stopped():
    """A door's text item (an agent's report, an MCP message, a call
    transcript) rides run_turn through the inbox; its prompt row IS the
    record. Stop on her reply used to erase the report itself (day-ruiner
    scout, 2026-10-07). Cadence's own prompts keep the old rule."""
    events = [{'type': 'final', 'text': '', 'cancelled': True}]
    sysobj, stream, llm = _system(events, active='c', rows=2)
    llm.session_manager.remove_last_messages.return_value = True
    cadence._system = sysobj
    with patch('core.cadence.publish'):
        out = cadence.run_turn('c', '[Agent Forge (claude_code) — done in 2m; not typed by the user]\nAll tests pass.',
                               speak=None, source='agent:claude_code', keep_prompt=True)
    assert out == ''
    llm.session_manager.remove_last_messages.assert_not_called()
    # the inbox's text items ask for it
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / 'core' / 'chat' / 'inbox.py').read_text(encoding='utf-8')
    assert 'keep_prompt=True' in src


def test_run_turn_publishes_its_tool_calls_so_the_tab_paints_them_live():
    """Krem, 2026-10-08: an agent-report turn showed think › think › prose
    live; the tool calls between them appeared only after a refresh. The engine's
    tool_start/tool_end reach run_turn; they ride the bus now, the same gate
    (chat, foreign) as the chunks, ephemeral like them."""
    sysobj, stream, llm = _system([{'type': 'content', 'text': 'checking '},
                                   {'type': 'tool_start', 'id': 'c1', 'name': 'agent_peek', 'args': {'agent': 'Delta'}},
                                   {'type': 'tool_end', 'id': 'c1', 'name': 'agent_peek', 'result': 'done', 'error': False},
                                   {'type': 'content', 'text': 'all good.'},
                                   {'type': 'final', 'text': 'all good.', 'cancelled': False}])
    cadence._system = sysobj
    published = []
    with patch('core.cadence.publish', side_effect=lambda et, data=None: published.append((et, data))):
        cadence.run_turn('c', 'the report')
    names = [et for et, _ in published]
    assert names == ['voice_turn_start', 'voice_turn_chunk', 'voice_turn_tool', 'voice_turn_tool',
                     'voice_turn_chunk', 'voice_turn_end']
    start, end = published[2][1], published[3][1]
    assert start == {'message_id': published[0][1]['message_id'], 'chat': 'c', 'foreign': True, 'phase': 'start',
                     'id': 'c1', 'name': 'agent_peek', 'args': {'agent': 'Delta'}}
    assert end['phase'] == 'end' and end['result'] == 'done' and end['error'] is False and end['chat'] == 'c'
    from core.event_bus import EventBus
    assert 'voice_turn_tool' in EventBus._EPHEMERAL_TYPES, 'tool args/results are chat content: never replayed'
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / 'interfaces' / 'web' / 'static' / 'main.js').read_text(encoding='utf-8')
    handler = js.split("eventBus.on('voice_turn_tool'")[1].split("eventBus.on('voice_turn_end'")[0]
    assert '_notMine(data) || !_voiceTurnActive' in handler and 'ui.startTool(' in handler and 'ui.endTool(' in handler


# ── touch: a player turn restarts her clock (2026-10-10) ────────────────────

def test_touch_restarts_the_clock_and_ignores_her_own_turn():
    cadence.arm('c', mode='timer', min_s=100, max_s=100)
    rec = cadence._records['c']
    rec['next_at'] = cadence._now() + 1          # about to fire
    rec['skips'] = 3
    st = cadence.touch('c')
    assert st['armed'] and 99 <= st['next_in'] <= 100
    assert rec['skips'] == 0 and rec['last_at'] is not None
    # her own turn in flight: not a touch — _fire owns that clock
    rec['running'] = True
    rec['next_at'] = cadence._now() + 1
    assert cadence.touch('c') is None and rec['next_at'] - cadence._now() <= 1
    assert cadence.touch('nobody') is None
    # a pending moment waits the min gap from the touch too
    rec['running'] = False
    cadence.touch('c')
    cadence.poke('c', 'a moment')
    assert not cadence._due(rec, cadence._now())


def test_turn_end_on_the_bus_touches_only_armed_chats():
    cadence.arm('c', mode='timer', min_s=100, max_s=100)
    rec = cadence._records['c']
    rec['next_at'] = cadence._now() + 1
    cadence._on_turn_end({'type': 'ai_typing_end', 'data': {'chat': 'other', 'foreign': True}})
    assert rec['next_at'] - cadence._now() <= 1
    cadence._on_turn_end({'type': 'ai_typing_end', 'data': {'chat': 'c', 'foreign': False}})
    assert rec['next_at'] - cadence._now() > 90
    cadence._on_turn_end({'data': {}})
    cadence._on_turn_end(None)


def test_start_registers_the_turn_end_hook_once(monkeypatch):
    calls = []

    class Bus:
        def on(self, types, fn):
            calls.append((tuple(types), fn))

    class T:
        def __init__(self, *a, **k): pass
        def start(self): pass
        def is_alive(self): return True

    monkeypatch.setattr('core.event_bus.get_event_bus', lambda: Bus())
    monkeypatch.setattr(cadence.threading, 'Thread', T)
    monkeypatch.setattr(cadence, '_listening', False)
    monkeypatch.setattr(cadence, '_thread', None)
    cadence.start(object())
    cadence.start(object())
    assert calls == [((cadence.Events.AI_TYPING_END,), cadence._on_turn_end)]
