# tests for gameroom_glass.py: a table on a satellite's touch screen (2026-10-25).
# No board: core.devices.glass is faked (claim/release/stream_frame), the route
# doors are faked, her voice is faked. A fake engine draws the glass.
import io
import sys
import threading
import time
import types
from pathlib import Path
from unittest.mock import patch

from PIL import Image

PLUGIN_DIR = Path(__file__).parent.parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

import gameroom_core as gc   # noqa: E402
import gameroom_glass as gg  # noqa: E402

ZONES = [{'name': 'top-left', 'x': 0, 'y': 0, 'w': 72, 'h': 120}, {'name': 'top-right', 'x': 248, 'y': 0, 'w': 72, 'h': 120},
         {'name': 'bottom-left', 'x': 0, 'y': 120, 'w': 72, 'h': 120}, {'name': 'bottom-right', 'x': 248, 'y': 120, 'w': 72, 'h': 120},
         {'name': 'top', 'x': 72, 'y': 0, 'w': 176, 'h': 72}, {'name': 'bottom', 'x': 72, 'y': 168, 'w': 176, 'h': 72},
         {'name': 'middle', 'x': 72, 'y': 72, 'w': 176, 'h': 96}]
TAKES = {'w': 320, 'h': 240, 'fps': 3, 'max_bytes': 256 * 1024, 'taps': True, 'zones': ZONES}


def _engine(state):
    drawn = []

    def glass_actions(view, mem):
        return {'top-left': {'label': 'FOLD', 'door': 'act', 'action': 'fold'},
                'bottom': {'label': f"size: {mem.get('size', 'pot')}", 'set': {'size': 'min'}},
                'bottom-right': {'label': 'ALL-IN', 'off': True},
                'middle': {'label': 'DEAL', 'door': 'start'}}

    def render_glass(draw, box, view, mem, line):
        drawn.append(box)
        e.lines.append(line)
        draw.text((box[0] + 4, box[1] + 4), 'table', fill=(255, 255, 255))

    e = types.SimpleNamespace(redact=lambda st: dict(st), glass_actions=glass_actions, render_glass=render_glass, drawn=drawn, lines=[])
    return e


class Rig:
    """A claimed board that records frames and presses, with a fake act/start door."""
    def __init__(self, state=None):
        self.frames, self.doors, self.said, self.events = [], [], [], []
        self.frame_seen = threading.Event()
        self.state = state if state is not None else {'session': {'player_chips': 100}, 'talk': [], 'hand': None}
        self.engine = _engine(self.state)
        self.claimed = {}

    def claim(self, device, on_press, on_gone=None, hold=60, who=''):
        if device in self.claimed:
            raise RuntimeError('taken')
        self.claimed[device] = (on_press, on_gone)
        return dict(TAKES)

    def release(self, device):
        self.claimed.pop(device, None)

    def stream_frame(self, device, jpeg):
        self.frames.append(jpeg)
        self.frame_seen.set()
        return {}

    def door(self, name):
        def go(game, body=None, **_):
            self.doors.append((name, body))
            self.state['talk'] = self.state.get('talk', []) + [{'who': 'ai', 'text': f'quip {len(self.doors)}', 'seq': len(self.doors)}]
            return {'ok': True}
        return go

    def wait_frames(self, n, timeout=3):
        end = time.monotonic() + timeout
        while len(self.frames) < n and time.monotonic() < end:
            time.sleep(0.02)
        return len(self.frames) >= n

    def __enter__(self):
        self.patches = [
            patch('core.devices.glass.claim', self.claim), patch('core.devices.glass.release', self.release),
            patch('core.devices.glass.stream_frame', self.stream_frame),
            patch.object(gc, 'get_game', lambda game: ({'title': 'Fake'}, self.engine) if game == 'fake' else (None, None)),
            patch.object(gc, '_chat_settings', lambda s: {'private_chat': s.startswith('private')} if s != 'gone' else None),
            patch.object(gc, 'load_state', lambda game, session=None: self.state),
            patch.object(gc, 'session_cfg', lambda s=None: {'voice': 'her'}),
            patch.object(gg, '_DOORS', {'act': self.door('act'), 'start': self.door('start'), 'new-session': self.door('new-session')}),
            patch.object(gg, '_glasses', {}),
            patch('core.devices.voice.say', lambda device, text, cs=None, voice=None, **kw: self.said.append((device, text, voice)) or ('', True)),
            patch('core.event_bus.publish', lambda name, data=None, ephemeral=False: self.events.append((name, data, ephemeral))),
        ]
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *a):
        gg.end_all()
        for p in self.patches:
            p.stop()


def test_a_private_session_a_gameless_engine_and_a_taken_session_are_refused():
    import pytest
    with Rig() as rig:
        with pytest.raises(PermissionError):
            gg.begin('fake', 'private table', 'esp')
        with pytest.raises(PermissionError):
            gg.begin('fake', 'gone', 'esp')
        with pytest.raises(ValueError):
            gg.begin('nope', 'table', 'esp')
        plain = types.SimpleNamespace(redact=lambda s: s)
        with patch.object(gc, 'get_game', lambda g: ({'title': 'Plain'}, plain)), pytest.raises(ValueError):
            gg.begin('plain', 'table', 'esp')
        gg.begin('fake', 'table', 'esp')
        with pytest.raises(RuntimeError):
            gg.begin('fake', 'table', 'esp')
        with pytest.raises(RuntimeError):
            gg.begin('fake', 'other', 'esp')          # the board is taken (glass.claim says so)
        assert gg.active('table') == 'esp' and gg.active('other') is None
        assert rig.events[0][0] == 'game_state_changed' and rig.events[0][1]['glass'] == 'esp' and rig.events[0][2] is True


def test_a_label_sits_inside_the_hit_box_of_its_place_at_the_glass_edge():
    h = 240
    assert gg._label_box({'x': 0, 'y': 0, 'w': 72, 'h': 120}, h) == (0, 0, 71, 21)            # top-left: the row along the top edge, its own width
    assert gg._label_box({'x': 248, 'y': 120, 'w': 72, 'h': 120}, h) == (248, 218, 319, 239)  # bottom-right: the row along the bottom edge
    assert gg._label_box({'x': 72, 'y': 0, 'w': 176, 'h': 72}, h) == (72, 0, 247, 21)          # top
    assert gg._label_box({'x': 72, 'y': 168, 'w': 176, 'h': 72}, h) == (72, 218, 247, 239)     # bottom
    assert gg._label_box({'x': 10, 'y': 50, 'w': 20, 'h': 20}, h) == (10, 50, 29, 69)          # a place touching neither edge keeps its own box


def test_the_first_frame_is_a_jpeg_of_the_glass_with_the_places_drawn():
    with Rig() as rig:
        gg.begin('fake', 'table', 'esp')
        assert rig.wait_frames(1)
        im = Image.open(io.BytesIO(rig.frames[0]))
        assert im.size == (320, 240) and im.format == 'JPEG' and len(rig.frames[0]) < TAKES['max_bytes']
        assert rig.engine.drawn == [(32, 22, 287, 217)]      # the picture: 80% of the width, from the top row of labels to the bottom row
        assert rig.engine.lines == [(72, 109, 247, 130)]     # the host's own row across the middle place's centre (y 72..168), told to the game


def test_a_press_on_a_move_goes_through_the_door_and_she_speaks_on_the_board():
    with Rig() as rig:
        gg.begin('fake', 'table', 'esp')
        assert rig.wait_frames(1)
        on_press, _ = rig.claimed['esp']
        on_press('top-left', 'short')
        assert rig.wait_frames(3)                              # thinking, then the result
        assert rig.doors == [('act', {'session': 'table', 'action': 'fold', 'args': {}})]
        time.sleep(0.1)
        assert rig.said == [('esp', 'quip 1', 'her')]
        assert [e[1]['session'] for e in rig.events] == ['table', 'table']
        on_press('middle', 'short')
        assert rig.wait_frames(5)
        assert rig.doors[-1] == ('start', {'session': 'table'})
        time.sleep(0.1)
        assert rig.said[-1][1] == 'quip 2'                     # only the fresh line, never the first again


def test_a_set_press_only_redraws_and_an_off_or_unknown_place_does_nothing():
    with Rig() as rig:
        gg.begin('fake', 'table', 'esp')
        assert rig.wait_frames(1)
        on_press, _ = rig.claimed['esp']
        on_press('bottom', 'short')
        assert rig.wait_frames(2)
        assert rig.doors == [] and gg._glasses['table'].mem == {'size': 'min'}
        on_press('bottom-right', 'short')
        on_press('nowhere', 'short')
        assert rig.wait_frames(4)
        assert rig.doors == [] and rig.said == []


def test_exit_and_the_players_long_press_end_the_table_and_free_the_board():
    with Rig() as rig:
        gg.begin('fake', 'table', 'esp')
        assert rig.wait_frames(1)
        on_press, _ = rig.claimed['esp']
        on_press('top', 'short')
        time.sleep(0.2)
        assert gg.active('table') is None and 'esp' not in rig.claimed
        assert rig.events[-1][1] == {'game': 'fake', 'session': 'table', 'glass': None}
        gg.begin('fake', 'table', 'esp')
        _, on_gone = rig.claimed['esp']
        on_gone()
        time.sleep(0.2)
        assert gg.active('table') is None
        assert gg.end('table') is False                        # twice is fine


def test_a_refused_move_redraws_and_says_nothing():
    with Rig() as rig:
        gg.begin('fake', 'table', 'esp')
        assert rig.wait_frames(1)
        with patch.object(gg, '_DOORS', {'act': lambda game, body=None, **_: ({'error': 'Not your turn.'}, 400)}):
            rig.claimed['esp'][0]('top-left', 'short')
            assert rig.wait_frames(3)
        time.sleep(0.1)
        assert rig.said == [] and len(rig.events) == 1


def test_the_routes_say_what_the_stage_bar_needs():
    import importlib.util
    spec = importlib.util.spec_from_file_location('play_for_glass_test', PLUGIN_DIR / 'routes' / 'play.py')
    play = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(play)
    with Rig() as rig, patch.object(gg, 'boards', lambda: [{'id': 'esp', 'label': 'Kitchen', 'busy': None}]):
        got = play.glass_get(query={'game': 'fake', 'session': 'table'})
        assert got == {'can': True, 'boards': [{'id': 'esp', 'label': 'Kitchen', 'busy': None}], 'device': None}
        assert play.glass_get(query={'game': 'nope'}) == {'can': False, 'boards': [], 'device': None}
        assert play.glass_begin(body={'game': 'fake', 'session': 'private x', 'device': 'esp'})[1] == 403
        assert play.glass_begin(body={'game': 'fake', 'session': 'table', 'device': 'esp'}) == {'status': 'ok', 'device': 'esp'}
        assert play.glass_begin(body={'game': 'fake', 'session': 'table', 'device': 'esp'})[1] == 409
        assert play.glass_get(query={'game': 'fake', 'session': 'table'})['device'] == 'esp'
        assert play.glass_end(body={'session': 'table'}) == {'status': 'ok', 'ended': True}
        assert play.glass_end(body={})[1] == 400


def test_an_empty_middle_is_talk_and_the_words_ride_the_next_move():
    heard = ['Heard: "you are bluffing"', 'Listened, and heard no speech.']
    runs = []
    def run(device, cap, action, value, owner=False):
        runs.append((device, cap, action, value, owner))
        return heard.pop(0), True
    with Rig() as rig, patch('core.devices.engine.run', run):
        rig.engine.glass_actions = lambda view, mem: {'top-left': {'label': 'FOLD', 'door': 'act', 'action': 'fold'}}
        gg.begin('fake', 'table', 'esp')
        assert rig.wait_frames(1)
        g = gg._glasses['table']
        assert g._slots({})['middle'] == {'label': 'TALK', 'talk': True}
        on_press, _ = rig.claimed['esp']
        on_press('middle', 'short')
        assert rig.wait_frames(3)                          # listening..., then the words in the strip
        assert runs == [('esp', 'mic', 'listen', str(gg.LISTEN_S), True)] and g.say == 'you are bluffing'
        assert g._slots({})['middle']['label'] == 'TALK  "you are bluffing"'
        on_press('top-left', 'short')
        assert rig.wait_frames(5)
        assert rig.doors == [('act', {'session': 'table', 'action': 'fold', 'args': {}, 'say': 'you are bluffing'})]
        assert g.say == ''
        on_press('middle', 'short')
        assert rig.wait_frames(7)
        assert g.say == '' and g.note == ''                 # nothing heard: said in the strip once, nothing queued
        on_press('top-left', 'short')
        assert rig.wait_frames(9)
        assert 'say' not in rig.doors[-1][1]


def test_a_label_too_wide_for_its_line_is_cut_short_not_spilled():
    from PIL import Image, ImageDraw
    im = Image.new('RGB', (320, 240))
    d = ImageDraw.Draw(im)
    gg._centred(d, (32, 190, 287, 212), 'TALK  "' + 'a very long sentence ' * 6 + '"', gg._font(14), (255, 255, 255))
    assert im.crop((0, 190, 31, 212)).getbbox() is None and im.crop((289, 190, 320, 212)).getbbox() is None


def _slow(rig, name, delay=0.3, talk=None):
    """A door that takes its time, as her seat's turn does."""
    door = rig.door(name)

    def go(game, body=None, **_):
        time.sleep(delay)
        if talk is not None:
            rig.state['talk'] = talk
        return door(game, body=body)
    return go


def test_a_press_on_a_frame_that_is_gone_is_dropped_and_exit_always_works():
    with Rig() as rig, patch.object(gg, '_DOORS', {'act': _slow(rig, 'act'), 'start': rig.door('start'), 'new-session': rig.door('new-session')}):
        gg.begin('fake', 'table', 'esp')
        assert rig.wait_frames(1)
        on_press, _ = rig.claimed['esp']
        on_press('top-left', 'short')
        time.sleep(0.1)                                        # her seat thinks, the labels are dim
        on_press('top-left', 'short')                          # a double tap: never replayed on the next frame
        assert rig.wait_frames(3)                              # thinking, then the result
        time.sleep(0.2)
        assert len(rig.doors) == 1
        on_press('top-left', 'short')                          # a tap on the new frame goes through
        assert rig.wait_frames(5)
        time.sleep(0.1)
        assert len(rig.doors) == 2
        on_press('top-left', 'short')
        time.sleep(0.1)
        on_press('top', 'short')                               # EXIT while she thinks: the table leaves anyway
        time.sleep(0.6)
        assert gg.active('table') is None and len(rig.doors) == 3


def test_a_refused_move_is_said_on_the_middle_row_and_the_words_wait():
    shown = []

    def refused(game, body=None, **_):
        return ({'error': 'Not your turn.'}, 400)
    with Rig() as rig, patch.object(gg, '_DOORS', {'act': refused, 'start': rig.door('start'), 'new-session': rig.door('new-session')}), \
         patch.object(gg, 'frame', lambda engine, takes, view, slots, mem, thinking=False: shown.append(dict(slots)) or b'jpeg'):
        gg.begin('fake', 'table', 'esp')
        assert rig.wait_frames(1)
        g = gg._glasses['table']
        g.say = 'you are bluffing'
        on_press, _ = rig.claimed['esp']
        on_press('top-left', 'short')
        assert rig.wait_frames(3)
        time.sleep(0.1)
        assert shown[-1]['middle'] == {'label': 'Not your turn.', 'off': True}    # the glass says why
        assert g.say == 'you are bluffing' and rig.said == []                     # the words ride the next try


def test_a_move_in_the_browser_pokes_the_glass_and_she_speaks_there():
    with Rig() as rig:
        gg.begin('fake', 'table', 'esp')
        assert rig.wait_frames(1)
        rig.state['talk'] = [{'who': 'ai', 'text': 'nice flop', 'seq': 1}]       # a browser move left her line in the talk
        gg.poke('table')
        assert rig.wait_frames(2)
        time.sleep(0.1)
        assert rig.said == [('esp', 'nice flop', 'her')]
        gg.poke('nope')                                                           # no table on a glass: nothing


def test_work_landing_after_the_table_left_stays_off_the_board():
    with Rig() as rig, patch.object(gg, '_DOORS', {'act': _slow(rig, 'act', talk=[{'who': 'ai', 'text': 'late', 'seq': 9}]),
                                                   'start': rig.door('start'), 'new-session': rig.door('new-session')}):
        gg.begin('fake', 'table', 'esp')
        assert rig.wait_frames(1)
        on_press, _ = rig.claimed['esp']
        on_press('top-left', 'short')
        assert rig.wait_frames(2)                                                 # thinking
        gg.end('table', why='test')
        time.sleep(0.5)
        assert len(rig.frames) == 2 and rig.said == []                            # no ghost frame, no late line


def test_forgetting_a_session_takes_its_table_off_the_glass():
    import importlib.util
    spec = importlib.util.spec_from_file_location('play_for_glass_forget', PLUGIN_DIR / 'routes' / 'play.py')
    play = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(play)
    with Rig() as rig, patch.object(gc, 'chat_store', types.SimpleNamespace(delete=lambda *a, **k: None)), \
         patch.object(gc, 'store', types.SimpleNamespace(delete=lambda *a, **k: None)):
        gg.begin('fake', 'table', 'esp')
        assert rig.wait_frames(1)
        assert play.forget('fake', body={'session': 'table'}) == {'status': 'ok'}
        assert gg.active('table') is None and 'esp' not in rig.claimed
