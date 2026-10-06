"""The engine through the REAL Game Room host (routes/play.py): a session
opens and saves on the chat row, the old verbs are refused by the host's own
IllegalAction, and the host finds no ghost line to hand her. The chat store is
a hermetic ChatSessionManager in a temp dir — the live DB is never touched."""
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

HERE = Path(__file__).absolute().parent
PLUGIN = HERE.parent
# The app root is wherever the host is, whether this plugin sits in plugins/
# or user/plugins/ — a fixed depth skipped this whole file in silence.
ROOT = next((d for d in PLUGIN.parents if (d / 'plugins' / 'game-room' / 'gameroom_core.py').exists()), None)
if ROOT is None:
    pytest.skip('game-room host not found above this plugin', allow_module_level=True)
HOST = ROOT / 'plugins' / 'game-room'
for p in (str(ROOT), str(HOST)):
    if p not in sys.path:
        sys.path.insert(0, p)

import gameroom_core as gc  # noqa: E402
from routes import play  # noqa: E402

TEST_DEFAULTS = {'prompt': 'default'}
GAME = 'piano-protagonist'


@pytest.fixture
def engine():
    spec = importlib.util.spec_from_file_location(f'gameroom_game_{GAME}', PLUGIN / 'games' / GAME / 'engine.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def host(tmp_path, monkeypatch, engine):
    with patch('core.chat.history.get_system_defaults', side_effect=lambda: dict(TEST_DEFAULTS)), \
         patch('core.chat.history.get_user_defaults', side_effect=lambda: dict(TEST_DEFAULTS)):
        from core.chat.history import ChatSessionManager
        sm = ChatSessionManager(history_dir=str(tmp_path / 'chatdb'))
    from core.plugin_loader import PluginChatState

    class AutoChatState(PluginChatState):
        _sm = staticmethod(lambda: sm)

        def put(self, chat, key, value):
            sm.create_chat(chat)
            return super().put(chat, key, value)

    cs = AutoChatState('game-room')
    monkeypatch.setattr(gc, 'chat_store', cs, raising=False)
    monkeypatch.setattr(gc, 'get_game', lambda gid: ({'title': 'Piano Protagonist'}, engine) if gid == GAME else (None, None))
    monkeypatch.setattr(gc, 'session_cfg', lambda s: {'provider': 'x', 'model': 'm'})
    monkeypatch.setattr(gc, 'game_settings', lambda g: {})
    monkeypatch.setattr(gc, 'room_config', lambda: {})
    monkeypatch.setattr(gc, 'append_table', lambda session, rows: False)
    monkeypatch.setattr(gc, 'run_ai_turns', lambda e, st, c, g: None)
    monkeypatch.setattr(gc, 'provider_info', lambda *a, **k: {'provider': 'x', 'model': 'm'})
    return sm


def test_engine_raises_the_hosts_own_illegal_action(engine):
    """The shim swap: once gameroom_core is importable the engine's errors ARE
    gc.IllegalAction, which is the class play.act catches."""
    assert engine.IllegalActionShim is gc.IllegalAction


def test_a_session_opens_and_saves_on_the_chat_row(host, engine):
    fresh = play.new_session(GAME, body={'session': 'piano-1'})
    assert fresh['round'] == {'phase': 'idle'}
    saved = gc.load_state(GAME, 'piano-1')
    assert saved['round'] == {'phase': 'idle'} and saved['session']['ai_name'] == 'Sapphire'
    assert 'history' not in saved and 'best' not in saved
    out = play.act(GAME, body={'session': 'piano-1', 'action': '_noop'})
    assert 'seat' not in out and out['last'] is None               # silent: nothing on the transcript


def test_she_is_handed_nothing(host, engine):
    """A chat turn in the piano room carries no table state."""
    play.new_session(GAME, body={'session': 'piano-2'})
    assert gc.ghost_block(GAME, 'piano-2') is None
    assert gc.public_view(engine, gc.load_state(GAME, 'piano-2')) is None


def test_old_verbs_are_refused_and_nothing_saved(host, engine):
    play.new_session(GAME, body={'session': 'piano-3'})
    before = gc.load_state(GAME, 'piano-3')
    card = {'lesson': 'twinkle', 'title': 'Twinkle', 'difficulty': 'easy', 'score': 90}
    for verb in ('_finish', '_begin', '_setup'):
        out = play.act(GAME, body={'session': 'piano-3', 'action': verb, 'args': card})
        assert out == ({'error': f"unknown action '{verb[1:]}'"}, 400)
    assert gc.load_state(GAME, 'piano-3') == before
