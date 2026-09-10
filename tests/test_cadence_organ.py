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
    def __init__(self, events):
        self.events = events
        self.seen = None
        self.suppress_tts = False
        self.images_ephemeral = False

    def chat_stream(self, text, images=None):
        self.seen = (text, images)
        for ev in self.events:
            yield ev


def _system(events, active='other', busy=False):
    stream = _Stream(events)
    llm = MagicMock()
    llm.begin_stream = MagicMock(side_effect=(ChatBusy('c') if busy else None), return_value=(stream, 'sid', 'c'))
    llm.session_manager.get_active_chat_name.return_value = active
    llm.session_manager.is_streaming.return_value = False
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
    sysobj.tts.speak.assert_called_once_with('Hi')
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
         patch.object(config, 'FORCE_THINKING', False, create=True), \
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
    sysobj, stream, llm = _system(events, active='c')
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
    sysobj.tts.speak.assert_called_once_with('Solid clear.')
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
