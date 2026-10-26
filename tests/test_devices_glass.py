# tests/test_devices_glass.py - a satellite's screen follows its chat (core/devices/glass.py).
# No board and no chat store: the board's answers and the chat are faked.
import io
from unittest.mock import patch

from PIL import Image

from core.devices import glass
from core.event_bus import EventBus


def _png(w, h):
    buf = io.BytesIO()
    Image.new('RGB', (w, h), (255, 0, 0)).save(buf, format='PNG')
    return buf.getvalue()


def test_cover_fills_the_glass_whatever_the_shape():
    for size in ((1920, 1080), (600, 1200), (320, 240)):
        data = glass.cover(_png(*size), 320, 240)
        assert len(data) == 320 * 240 * 2
        assert data[:2] == b'\xf8\x00'              # red, RGB565 big-endian


def test_name_is_cut_on_a_whole_character():
    assert glass._name('default') == 'default'
    assert len(glass._name('é' * 40).encode()) <= glass.NAME_MAX
    glass._name('é' * 40).encode().decode()         # still valid UTF-8


def test_bus_listener_is_called_and_is_not_a_browser_tab():
    bus, seen = EventBus(), []
    bus.on(('chat_switched',), seen.append)
    bus.publish('chat_switched', {'name': 'coding'})
    bus.publish('something_else', {})
    assert [e['type'] for e in seen] == ['chat_switched']
    assert bus.subscriber_count() == 0              # ask_user reads this as "a tab is open"


def test_a_failing_listener_does_not_stop_the_publish():
    bus, seen = EventBus(), []
    bus.on(('x',), lambda e: 1 / 0)
    bus.on(('x',), seen.append)
    bus.publish('x', {})
    assert len(seen) == 1


def W(chat, scene):
    return {'chat': chat, 'scene': scene, 'brain': 'opus', 'trim': '#00ffaa'}


ROW = {'id': 'esp', 'enabled': True, 'parts': [{'driver': 'satellite', 'config': {'url': 'http://b', 'chat': 'coding'}}]}


def _sync(screen, want, scene_bytes=None):
    calls = []
    def call(method, path, config, secrets, **kw):
        calls.append((method, path.split('?')[0], kw.get('json')))
    with patch('core.devices.engine.rows', return_value={'esp': ROW}), \
         patch('core.devices.engine._part_secrets', return_value={'token': 'k'}), \
         patch('core.devices.drivers.satellite._health', return_value={'screen': screen} if screen is not None else {}), \
         patch('core.devices.drivers.satellite._call', side_effect=call), \
         patch.object(glass, 'wanted', return_value=want), \
         patch.object(glass, '_scene_bytes', return_value=scene_bytes):
        did = glass.sync('esp')
    return did, calls


def test_sync_sends_the_name_and_the_scene_when_they_differ():
    did, calls = _sync({'w': 320, 'h': 240, 'chat': '', 'background': ''}, W('coding', 'matrix'), _png(800, 600))
    assert did == ['chat coding (model opus, trim #00ffaa)', 'scene matrix']
    assert calls[0] == ('POST', '/screen', {'chat': 'coding', 'brain': 'opus', 'trim': '#00ffaa'})
    assert calls[1][:2] == ('POST', '/screen/background')


def test_a_board_that_states_no_background_gets_the_name_and_no_scene():
    # the reader (cyd28) draws text only: it states screen.chat and no screen.background
    did, calls = _sync({'w': 320, 'h': 240, 'chat': ''}, W('coding', 'matrix'), _png(8, 8))
    assert [c[:2] for c in calls] == [('POST', '/screen')] and did == ['chat coding (model opus, trim #00ffaa)']
    with patch.object(glass, 'wanted', return_value=W('coding', 'matrix')):
        assert not glass.behind({}, {'chat': 'coding'})
        assert glass.behind({}, {'chat': 'other'})


def test_sync_sends_nothing_when_the_board_already_shows_it():
    did, calls = _sync({'w': 320, 'h': 240, 'chat': 'coding', 'brain': 'opus', 'trim': '#00ffaa', 'background': 'matrix'}, W('coding', 'matrix'), _png(8, 8))
    assert did == [] and calls == []


def test_sync_clears_a_scene_the_chat_no_longer_has():
    did, calls = _sync({'w': 320, 'h': 240, 'chat': 'coding', 'background': 'matrix'}, W('coding', ''))
    assert did == ['scene cleared'] and calls == [('POST', '/screen/background', {'clear': True})]


def test_sync_leaves_alone_a_board_without_a_screen_or_an_older_program():
    assert _sync(None, W('coding', 'matrix')) == ([], [])
    assert _sync({'w': 320, 'h': 240, 'background': ''}, W('coding', 'matrix')) == ([], [])


def test_a_private_chat_is_never_named_on_a_screen():
    class SM:
        def get_active_chat_name(self): return 'secret'
        def get_settings_for(self, chat): return {'private_chat': True, 'background': 'boat'}
    class Sys: llm_chat = type('L', (), {'session_manager': SM()})()
    with patch('core.api_fastapi.get_system', return_value=Sys()):
        assert glass.wanted({'config': {}}) == {'chat': '', 'scene': '', 'brain': '', 'trim': ''}


def test_behind_is_the_level_check():
    with patch.object(glass, 'wanted', return_value=W('coding', 'matrix')):
        assert glass.behind({}, {'chat': '', 'background': ''})
        assert glass.behind({}, {'chat': 'coding', 'background': ''})
        assert not glass.behind({}, {'chat': 'coding', 'background': 'matrix'})       # a 0.5.3 board: no brain, no trim, not compared
        assert glass.behind({}, {'chat': 'coding', 'brain': 'other', 'trim': '#00ffaa', 'background': 'matrix'})
        assert glass.behind({}, {'chat': 'coding', 'brain': 'opus', 'trim': '', 'background': 'matrix'})
        assert not glass.behind({}, {'w': 320})                 # a program before 0.5.3
        assert not glass.behind({}, None)


def test_a_board_that_cannot_be_reached_is_tried_again():
    tries = []
    def sync(device_id):
        tries.append(device_id)
        return None if len(tries) < 3 else []
    with patch.object(glass, 'sync', side_effect=sync), patch.object(glass, 'RETRY_AFTER', (0.01, 0.01)):
        glass._sync_until_told('esp')
    assert len(tries) == 3


def test_saving_a_device_sends_its_screen_settings_only_to_a_board_with_a_screen():
    from core.devices.drivers import satellite as sat
    config = {'url': 'http://b', 'brightness': 60, 'dim': 5, 'dim_after_s': 30, 'off_after_min': 10, 'flip': True, 'invert': True, 'mirror': False}
    for has, expect in ((['screen'], True), (['speaker'], False)):
        calls = []
        with patch.object(sat, '_call', side_effect=lambda m, path, c, s, **kw: calls.append((m, path, kw.get('json')))), \
             patch.object(sat, '_health', return_value={'has': has}), \
             patch.object(glass, 'sync_soon'):
            sat.apply({'id': 'esp'}, config, {'token': 'k'})
        sent = [c for c in calls if c[1] == '/screen/settings']
        assert bool(sent) == expect
        if expect:
            assert sent[0] == ('PUT', '/screen/settings', {'brightness': 60, 'dim': 5, 'dim_after_s': 30, 'off_after_min': 10, 'flip': True, 'invert': True, 'mirror': False})


def test_wanted_names_the_model_and_the_trim_of_the_chat():
    class SM:
        def get_active_chat_name(self): return 'coding'
        def get_settings_for(self, chat): return {'background': 'matrix', 'trim_color': '#00FFAA', 'llm_primary': 'x', 'llm_model': 'm'}
    class Sys: llm_chat = type('L', (), {'session_manager': SM()})()
    with patch('core.api_fastapi.get_system', return_value=Sys()), patch.object(glass, '_brain', return_value='opus'):
        assert glass.wanted({'config': {}}) == {'chat': 'coding', 'scene': 'matrix', 'brain': 'opus', 'trim': '#00ffaa'}


def test_saving_a_device_sends_its_longest_question():
    from core.devices.drivers import satellite as sat
    for mic, expect in (({'gain_db': 30, 'max_s': 30}, True), ({'gain_db': 30}, False)):   # an older program states no max_s
        calls = []
        with patch.object(sat, '_call', side_effect=lambda m, path, c, s, **kw: calls.append((m, path))), \
             patch.object(sat, '_health', return_value={'has': ['mic'], 'mic': mic}), \
             patch.object(glass, 'sync_soon'):
            sat.apply({'id': 'esp'}, {'url': 'http://b', 'question_max_s': 45}, {'token': 'k'})
        assert (('POST', '/mic?max_s=45') in calls) == expect


def test_a_board_is_shown_only_what_it_says_it_takes():
    from core.devices import engine
    from core.devices.drivers import satellite as sat
    stick = {'has': ['screen', 'light'], 'screen': {'flip': False}, 'led': {'looks': ['night']}}
    takes = sat._takes(stick)
    assert set(takes) == {'screen.flip', 'look.night'}
    part = {'takes': takes}
    schema = {f['key']: f for f in sat.DRIVER['config_schema']} if hasattr(sat, 'DRIVER') else None
    fields = schema or {f['key']: f for f in next(v for v in vars(sat).values() if isinstance(v, dict) and 'config_schema' in v)['config_schema']}
    shown = {k for k, f in fields.items() if engine.taken(f, part)}
    assert 'flip' in shown and 'look_night' in shown and 'lights_from' in shown
    assert not shown & {'brightness', 'dim', 'look_resting', 'look_thinking', 'question_max_s'}
    assert all(engine.taken(f, {}) for f in fields.values())            # a device that never said takes everything
    glass_board = {'has': ['screen', 'mic'], 'screen': {'w': 320, 'h': 240, 'format': 'rgb565be', 'brightness': 80}, 'mic': {'max_s': 30}}
    assert {'screen.picture', 'screen.brightness', 'mic.max_s', 'look.resting'} <= set(sat._takes(glass_board))
    assert 'picture' not in sat.describe({'id': 'x', 'takes': takes}, {})['screen']['actions']
    cyd = {'has': ['screen'], 'screen': {'w': 320, 'h': 240, 'format': 'rgb565be', 'rotation': 0, 'invert': False, 'mirror': False}}
    shown = {k for k, f in fields.items() if engine.taken(f, {'takes': sat._takes(cyd)})}
    assert {'rotation', 'invert', 'mirror'} <= shown and 'flip' not in shown   # a CYD: the glass knobs, not the stick's flip
    assert 'picture' in sat.describe({'id': 'x', 'takes': sat._takes(glass_board)}, {})['screen']['actions']


# --- a stream on the glass (2026-10-20) ---------------------------------------

ZONES = [{'name': 'top-left', 'x': 0, 'y': 0, 'w': 72, 'h': 120}, {'name': 'top', 'x': 72, 'y': 0, 'w': 176, 'h': 72}]


def _known_board(fps, taps=False):
    """A board the stream knows without asking it."""
    return {'config': {'url': 'http://board'}, 'secrets': {'token': 'k'}, 'w': 320, 'h': 240, 'fps': fps,
            'taps': taps, 'zones': list(ZONES) if taps else []}


def test_stream_paces_to_the_board_and_the_newest_frame_wins():
    import time
    sent = []
    calls = lambda m, path, c, s, **kw: sent.append((path, kw.get('data')))
    with patch.object(glass, '_known', {'b': _known_board(10)}), patch.object(glass, '_streams', {}), \
         patch('core.devices.drivers.satellite._call', side_effect=calls):
        glass.stream_frame('b', b'f1')               # the first goes at once
        time.sleep(0.05)
        glass.stream_frame('b', b'f2')               # waits for the beat...
        status = glass.stream_frame('b', b'f3')      # ...and is replaced by this one
        time.sleep(0.35)
        assert [d for _, d in sent] == [b'f1', b'f3']
        assert status['replaced'] == 1 and status['fps'] == 10
        assert all(path.startswith('/screen/picture?format=jpeg') for path, _ in sent)
        glass.stream_end('b')
        assert sent[-1][0] == '/screen/picture'      # the board's own page comes back


def test_stream_refuses_a_frame_too_big_and_a_board_without_a_glass():
    import pytest
    with patch.object(glass, '_known', {'b': _known_board(3)}), patch.object(glass, '_streams', {}):
        with pytest.raises(ValueError):
            glass.stream_frame('b', b'x' * (glass.FRAME_MAX + 1))
    with patch.object(glass, '_known', {}), patch.object(glass, '_streams', {}), \
         patch('core.devices.engine.rows', return_value={}):
        with pytest.raises(LookupError):
            glass.stream_frame('nobody', b'f')


def test_stream_counts_a_board_that_does_not_answer_and_keeps_going():
    import time
    from core.devices.drivers.satellite import Problem
    with patch.object(glass, '_known', {'b': _known_board(20)}), patch.object(glass, '_streams', {}), \
         patch('core.devices.drivers.satellite._call', side_effect=Problem("No answer from board within 5s.")):
        glass.stream_frame('b', b'f1')
        time.sleep(0.2)
        status = glass.stream_status('b')
        assert status['failed'] == 1 and 'No answer' in status['last_error'] and status['sent'] == 0
        glass.stream_end('b')


def test_face_boards_is_the_stream_as_a_square():
    with patch.object(glass, 'stream_boards', return_value=[('b', 320, 240, 3), ('c', 240, 240, 3)]):
        assert glass.face_boards() == [('b', 240), ('c', 240)]


# --- a stream that owns the board's taps (firmware 0.9.0) -------------------------

class _Answer:
    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body


def test_learn_reads_the_places_and_whether_the_glass_passes_taps_on():
    said = {'w': 320, 'h': 240, 'jpeg': True, 'fps': 3, 'taps': True,
            'zones': [{'name': 'Top Left!', 'x': 0, 'y': 0, 'w': 72, 'h': 120}, {'name': '', 'x': 1, 'y': 1, 'w': 1, 'h': 1},
                      {'name': 'bottom', 'x': 72, 'y': 168, 'w': 176, 'h': 72}, 'junk']}
    with patch.object(glass, '_known', {}):
        glass._learn(ROW, {}, {}, said)
        t = glass.takes('esp')
    assert t['taps'] is True and t['w'] == 320 and t['max_bytes'] == glass.FRAME_MAX
    assert [z['name'] for z in t['zones']] == ['topleft', 'bottom']
    with patch.object(glass, '_known', {}):
        glass._learn(ROW, {}, {}, {'w': 320, 'h': 240, 'jpeg': True, 'fps': 3})      # 0.8.0: no taps, no zones
        assert glass.takes('esp')['taps'] is False and glass.takes('esp')['zones'] == []


def test_a_claim_needs_a_glass_that_passes_taps_on_and_is_one_at_a_time():
    import pytest
    with patch.object(glass, '_known', {'b': _known_board(3)}), patch.object(glass, '_owners', {}):
        with pytest.raises(LookupError):
            glass.claim('b', lambda z, h: None)
    with patch.object(glass, '_known', {'b': _known_board(3, taps=True)}), patch.object(glass, '_owners', {}), \
         patch.object(glass, '_streams', {}), patch('core.devices.drivers.satellite._call', return_value=None):
        what = glass.claim('b', lambda z, h: None, who='game-room')
        assert what['taps'] and [z['name'] for z in what['zones']] == ['top-left', 'top']
        assert glass.owner('b') == 'game-room'
        with pytest.raises(RuntimeError):
            glass.claim('b', lambda z, h: None, who='other')
        glass.release('b')
        assert glass.owner('b') is None
        glass.release('b')                               # twice is fine


def test_an_owned_stream_asks_for_the_taps_and_holds_the_frame():
    import time
    sent = []
    with patch.object(glass, '_known', {'b': _known_board(10, taps=True)}), patch.object(glass, '_owners', {}), \
         patch.object(glass, '_streams', {}), \
         patch('core.devices.drivers.satellite._call', side_effect=lambda m, path, c, s, **kw: sent.append(path)):
        glass.claim('b', lambda z, h: None, hold=60)
        glass.stream_frame('b', b'f1')
        time.sleep(0.1)
        glass.release('b')
        glass.stream_frame('b', b'f2')                   # unowned again: as before
        time.sleep(0.15)
    assert sent[0] == '/screen/picture?format=jpeg&seconds=60&taps=1'
    assert sent[1] == '/screen/picture'                  # the release brought the page back
    assert sent[2] == '/screen/picture?format=jpeg&seconds=3'


def test_a_press_on_an_owned_board_goes_to_the_owner_and_nowhere_else():
    import threading
    got, seen = [], threading.Event()
    def on_press(zone, how):
        got.append((zone, how))
        seen.set()
    with patch.object(glass, '_known', {'b': _known_board(3, taps=True)}), patch.object(glass, '_owners', {}), \
         patch.object(glass, '_streams', {}), patch('core.devices.drivers.satellite._call', return_value=None):
        assert glass.press('b', 'top-left', 'short') is False       # nobody owns it: the bindings' turn
        glass.claim('b', on_press)
        assert glass.press('b', 'top-left', 'short') is True
        assert seen.wait(1) and got == [('top-left', 'short')]
        glass.release('b')


def test_the_board_saying_hidden_tells_the_owner_once_and_ends_the_claim():
    import time, threading
    gone, told = threading.Event(), []
    def on_gone():
        told.append(1)
        gone.set()
    answers = iter([_Answer({'ok': True, 'hidden': True}), _Answer({'ok': True, 'hidden': True})])
    with patch.object(glass, '_known', {'b': _known_board(20, taps=True)}), patch.object(glass, '_owners', {}), \
         patch.object(glass, '_streams', {}), \
         patch('core.devices.drivers.satellite._call', side_effect=lambda *a, **kw: next(answers, None)):
        glass.claim('b', lambda z, h: None, on_gone=on_gone)
        glass.stream_frame('b', b'f1')
        assert gone.wait(1)
        assert glass.owner('b') is None
        glass.stream_frame('b', b'f2')                   # another hidden, nobody owns it: nothing more is said
        time.sleep(0.15)
        assert told == [1]
        glass.stream_end('b')
