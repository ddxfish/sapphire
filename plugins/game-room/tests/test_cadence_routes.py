# The room's side of the cadence organ (F3, 2026-09-09): the spine becomes
# the arm spec, her cue names the game, the routes arm / pause / poke /
# disarm / end a session by name.
import sys
import types
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).parent.parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

import gameroom_core as gc  # noqa: E402
from routes import play  # noqa: E402


class FakeStore:
    def __init__(self):
        self.d = {}

    def get(self, k):
        return self.d.get(k)

    def save(self, k, v):
        self.d[k] = v


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr(gc, 'store', FakeStore())
    chats = {'tbl': {'mode': 'game', 'game_id': 'towerd', 'game_room': {'cadence_min': 15}},
             'pk': {'mode': 'game', 'game_id': 'poker'},
             'plain': {}}
    monkeypatch.setattr(gc, '_chat_settings', lambda c: chats.get(c))
    import core.games_registry as reg
    monkeypatch.setattr(reg, 'list_games', lambda: [
        {'id': 'towerd', 'room_defaults': {'cadence_mode': 'event'}},
        {'id': 'poker', 'room_defaults': {'cadence_mode': 'turn', 'send_frames': False}}])
    monkeypatch.setattr(gc, 'get_game', lambda gid: ({'title': {'towerd': 'Dark Horse Tower', 'poker': "Hold'em"}.get(gid, gid)}, object()) if gid else (None, None))
    calls = []

    class FakeCadence:
        def arm(self, chat, **kw):
            calls.append(('arm', chat, kw))
            return {'armed': kw.get('mode') != 'turn', 'mode': kw.get('mode'), 'paused': kw.get('paused')}

        def disarm(self, chat, owner=None):
            calls.append(('disarm', chat, owner))
            return True

        def status(self, chat):
            return {'armed': True, 'chat': chat, 'paused': False}

        def pause(self, chat, paused):
            calls.append(('pause', chat, paused))
            return {'armed': True, 'paused': paused}

        def poke(self, chat, note=None, force=False):
            calls.append(('poke', chat, note, force))
            return {'armed': True, 'pending': True}

        def fire_once(self, chat, text, images=None, speak=None, source=None):
            calls.append(('fire_once', chat, text, speak, source))
            return 'summary'
    fake = FakeCadence()
    monkeypatch.setattr(play, '_cadence', lambda: fake)
    return types.SimpleNamespace(calls=calls, chats=chats)


def test_cadence_spec_reads_the_resolved_spine(world):
    spec = gc.cadence_spec('tbl')
    assert spec == {'mode': 'event', 'min_s': 15, 'max_s': 180, 'paused': False,
                    'send_frames': True, 'frames_per_tick': 6, 'every': 1, 'speak': 'browser'}
    assert gc.cadence_spec('pk')['mode'] == 'turn' and gc.cadence_spec('pk')['send_frames'] is False


def test_cadence_prompt_names_the_game_and_the_deposit(world):
    build = gc.cadence_prompt('tbl')
    text = build({'text': 'Wave 4 cleared.', 'frames': []}, {'send_frames': False})
    assert text.startswith('[Dark Horse Tower — your turn while the table is in front of you]\nWave 4 cleared.')
    text2 = build({'text': '', 'frames': [1, 2]}, {'send_frames': True})
    assert 'the screen' in text2 and 'what you see is attached' in text2
    assert 'nothing new on the table' in build(None, {'send_frames': False})
    assert gc.summary_prompt('pk').startswith("[Hold'em — the session is ending]")


def test_arm_route_arms_from_the_spine_and_refuses_non_games(world):
    out = play.cadence_arm(body={'session': 'tbl'})
    assert out['cadence']['armed'] and out['spec']['mode'] == 'event'
    kind, chat, kw = world.calls[-1]
    assert kind == 'arm' and chat == 'tbl' and kw['owner'] == 'game-room' and kw['ttl'] == 90
    assert kw['title'] == 'Dark Horse Tower' and callable(kw['prompt']) and kw['min_s'] == 15
    assert play.cadence_arm(body={'session': 'pk'})['cadence']['armed'] is False     # turn mode never arms
    assert play.cadence_arm(body={'session': 'plain'})[1] == 400
    assert play.cadence_arm(body={'session': 'missing'})[1] == 404
    assert play.cadence_arm(body={})[1] == 400


def test_pause_writes_the_session_layer_and_mirrors(world, monkeypatch):
    written = []
    monkeypatch.setattr(play, 'set_session_settings', lambda body=None, **k: written.append(body) or {'status': 'ok'})
    out = play.cadence_pause(body={'session': 'tbl', 'paused': True})
    assert out['paused'] is True and written == [{'session': 'tbl', 'settings': {'cadence_paused': True}}]
    assert world.calls[-1] == ('pause', 'tbl', True)


def test_poke_deposits_the_note_and_pokes(world):
    from core import perception
    perception.clear('tbl')
    out = play.cadence_poke(body={'session': 'tbl', 'note': 'Wave 7 cleared.'})
    assert out['cadence']['pending'] is True and world.calls[-1] == ('poke', 'tbl', 'Wave 7 cleared.', False)
    assert perception.take('tbl')['text'] == 'Wave 7 cleared.'


def test_session_end_disarms_and_runs_the_summary(world, monkeypatch):
    ran = []
    monkeypatch.setattr(play.threading if hasattr(play, 'threading') else __import__('threading'), 'Thread',
                        lambda target, daemon, name: types.SimpleNamespace(start=lambda: target()))
    out = play.session_end(body={'session': 'tbl'})
    assert out == {'status': 'ok', 'summary': True}
    kinds = [c[0] for c in world.calls]
    assert 'disarm' in kinds and world.calls[-1][0] == 'fire_once'
    assert world.calls[-1][4] == 'session_end' and world.calls[-1][3] == 'browser'
    # summary off → disarm only
    world.chats['tbl']['game_room']['session_end_summary'] = False
    world.calls.clear()
    assert play.session_end(body={'session': 'tbl'}) == {'status': 'ok', 'summary': False}
    assert [c[0] for c in world.calls] == ['disarm']


def test_poke_route_carries_force_and_every_rides_the_spec(world, monkeypatch):
    from routes import play
    play.cadence_poke(body={'session': 'tbl', 'note': 'The castle fell.', 'force': True})
    assert world.calls[-1] == ('poke', 'tbl', 'The castle fell.', True)
    import core.games_registry as reg
    monkeypatch.setattr(reg, 'list_games', lambda: [
        {'id': 'towerd', 'room_defaults': {'cadence_mode': 'event', 'cadence_every': 3}}])
    assert gc.cadence_spec('tbl')['every'] == 3
