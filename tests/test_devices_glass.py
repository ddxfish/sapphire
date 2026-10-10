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
    config = {'url': 'http://b', 'brightness': 60, 'dim': 5, 'dim_after_s': 30, 'off_after_min': 10, 'flip': True}
    for has, expect in ((['screen'], True), (['speaker'], False)):
        calls = []
        with patch.object(sat, '_call', side_effect=lambda m, path, c, s, **kw: calls.append((m, path, kw.get('json')))), \
             patch.object(sat, '_health', return_value={'has': has}), \
             patch.object(glass, 'sync_soon'):
            sat.apply({'id': 'esp'}, config, {'token': 'k'})
        sent = [c for c in calls if c[1] == '/screen/settings']
        assert bool(sent) == expect
        if expect:
            assert sent[0] == ('PUT', '/screen/settings', {'brightness': 60, 'dim': 5, 'dim_after_s': 30, 'off_after_min': 10, 'flip': True})


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
    assert 'picture' in sat.describe({'id': 'x', 'takes': sat._takes(glass_board)}, {})['screen']['actions']
