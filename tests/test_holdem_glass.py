# tests/test_holdem_glass.py - poker's glass table (core/plugins/game-holdem engine.py glass_actions /
# render_glass, 2026-10-25): what the seven places say per street, and the band draws.
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT / 'core' / 'plugins' / 'game-room', ROOT / 'core' / 'plugins' / 'game-holdem' / 'games' / 'poker'):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import engine as pk   # noqa: E402


def _table():
    st = pk.new_session('Krem', 'Sapphire')
    pk.start_round(st)
    if st['hand']['to_act'] == 'ai':
        pk.apply_action(st, 'ai', 'call', {})
    return st


def _labels(acts):
    return {k: v.get('label') for k, v in acts.items()}


def test_no_session_sits_down_and_a_busted_table_starts_over():
    assert pk.glass_actions({'session': None}, {}) == {'middle': {'label': 'SIT DOWN', 'door': 'new-session'}}
    st = _table()
    st['session']['player_chips'] = 0
    assert pk.glass_actions(pk.redact(st), {})['middle']['door'] == 'new-session'


def test_between_hands_the_middle_deals_and_the_corners_are_empty():
    st = _table()
    st['hand']['street'] = 'over'
    st['hand']['to_act'] = None
    acts = pk.glass_actions(pk.redact(st), {})
    assert acts == {'middle': {'label': 'DEAL', 'door': 'start'}}


def test_facing_a_bet_the_corners_fold_call_raise_and_shove():
    st = _table()
    view = pk.redact(st)
    legal = view['hand']['legal']
    acts = pk.glass_actions(view, {})
    assert _labels(acts) == {'bottom': 'size: pot', 'top-left': 'FOLD', 'top-right': f"CALL {legal['to_call']}",
                             'bottom-left': f"RAISE {acts['bottom-left']['args']['amount']}", 'bottom-right': 'ALL-IN'}
    assert acts['top-left'] == {'label': 'FOLD', 'door': 'act', 'action': 'fold'}
    assert acts['top-right']['action'] == 'call'
    assert legal['min_raise_to'] <= acts['bottom-left']['args']['amount'] <= legal['max_raise_to']
    assert acts['bottom-right']['args']['amount'] == legal['max_raise_to']
    assert acts['bottom']['set'] == {'size': 'min'}                      # pot -> min -> half -> pot
    assert pk.glass_actions(view, {'size': 'min'})['bottom-left']['args']['amount'] == legal['min_raise_to']
    assert pk.glass_actions(view, {'size': 'min'})['bottom']['set'] == {'size': 'half'}
    assert 'top' not in acts                                             # EXIT is the host's


def test_with_nothing_to_call_there_is_no_fold_and_check_and_bet_show():
    st = _table()
    pk.apply_action(st, 'player', 'call', {})                            # the blinds are even now
    for _ in range(3):                                                   # her option, then the flop: she checks it round to me
        if st['hand']['to_act'] == 'ai':
            pk.apply_action(st, 'ai', 'check', {})
    view = pk.redact(st)
    assert view['hand']['to_act'] == 'player' and view['hand']['legal']['to_call'] == 0
    acts = pk.glass_actions(view, {})
    assert 'top-left' not in acts and acts['top-right']['action'] == 'check'
    assert acts['bottom-left']['action'] == 'bet' and acts['bottom-left']['label'].startswith('BET ')


def test_waiting_on_her_every_move_is_off_but_the_size_still_cycles():
    st = _table()
    st['hand']['to_act'] = 'ai'
    acts = pk.glass_actions(pk.redact(st), {})
    assert all(acts[z].get('off') for z in ('top-left', 'top-right', 'bottom-left', 'bottom-right'))
    assert acts['bottom']['set'] == {'size': 'min'}


def test_the_band_draws_a_hand_and_a_result():
    for st in (_table(), {'session': None}):
        im = Image.new('RGB', (320, 240), (18, 52, 36))
        view = pk.redact(st) if st.get('session') else st
        pk.render_glass(ImageDraw.Draw(im), (72, 72, 247, 167), view, {})
        assert im.getbbox() is not None
    st = _table()
    st['hand']['street'] = 'over'
    st['hand']['to_act'] = None
    st['hand']['result'] = {'winner': 'ai', 'pot': 40, 'reason': 'fold', 'desc': 'Sapphire wins 40 - Krem folded.', 'revealed': None}
    im = Image.new('RGB', (320, 240), (18, 52, 36))
    pk.render_glass(ImageDraw.Draw(im), (72, 72, 247, 167), pk.redact(st), {})
    assert im.crop((72, 72, 248, 168)).getcolors(10000) and len(im.crop((72, 72, 248, 168)).getcolors(10000)) > 2
