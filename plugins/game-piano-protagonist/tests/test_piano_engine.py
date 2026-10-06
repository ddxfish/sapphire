"""Piano Protagonist, server half: the engine contract and the shipped content."""
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).absolute().parent.parent
_ENGINE = _ROOT / 'games' / 'piano-protagonist' / 'engine.py'
_CONTENT = _ROOT / 'app' / 'pp' / 'content.json'

NOTE = re.compile(r'^([A-G])([#b]?)(-?\d)(?::(\d+(?:\.\d+)?))?$')
CHORD = re.compile(r'^([A-G][#b]?(?:m|7|m7|dim)?)(?::(\d+(?:\.\d+)?))?$')
REST = re.compile(r'^R(?::(\d+(?:\.\d+)?))?$')
PITCH = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}


def _num(name):
    m = NOTE.match(name)
    return PITCH[m[1]] + {'#': 1, 'b': -1, '': 0}[m[2]] + 12 * (int(m[3]) + 1)


@pytest.fixture(scope='module')
def eng():
    spec = importlib.util.spec_from_file_location('pp_engine_under_test', _ENGINE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope='module')
def content():
    return json.loads(_CONTENT.read_text(encoding='utf-8'))


def test_new_session_shape(eng):
    st = eng.new_session(cfg={})
    assert st['round'] == {'phase': 'idle'} and st['talk'] == [] and st['talk_seq'] == 0
    assert st['session'] == {'player_name': 'Player', 'ai_name': 'Sapphire'}
    assert 'history' not in st and 'best' not in st            # nothing of the playing is kept
    assert eng.whose_turn(st) == 'player' and eng.can_start(st) is None
    assert eng.redact(st) == st


def test_the_board_has_no_verbs(eng):
    st = eng.new_session()
    before = dict(st)
    eng.apply_action(st, 'player', 'noop', {})
    assert st == before
    for verb in ('setup', 'begin', 'finish', 'cheat'):
        with pytest.raises(Exception, match='unknown action'):
            eng.apply_action(st, 'player', verb, {'lesson': 'twinkle', 'score': 90})
    assert st == before


def test_she_has_no_part_yet(eng):
    """No ghost line, no table talk, no seat: the host finds nothing to hand
    her. A build that brings her back adds these on purpose."""
    for name in ('build_ghost', 'view_public', 'view_between', 'view_for_ai',
                 'build_banter_msg', 'BANTER_CONTRACT', 'CONTRACT'):
        assert not hasattr(eng, name), name


def test_manifest_keeps_her_turns_off():
    spec = json.loads((_ROOT / 'plugin.json').read_text(encoding='utf-8'))['capabilities']['games'][0]
    assert spec['id'] == 'piano-protagonist'
    assert spec['room_defaults']['cadence_mode'] == 'off'
    assert spec['room_defaults']['session_end_summary'] is False
    assert 'cadence_event' not in spec and 'moments' not in spec


def test_settings_schema(eng):
    keys = {s['key'] for s in eng.SETTINGS}
    assert keys == {'user_songs'}
    assert all(s.get('tab') for s in eng.SETTINGS)


# ── the shipped content ───────────────────────────────────────────────────────

def test_every_lesson_parses_and_names_known_chords(content):
    chords = content['chords']
    ids = [l['id'] for l in content['lessons']]
    assert len(ids) == len(set(ids))
    for lesson in content['lessons']:
        assert lesson['kind'] in ('song', 'chords') and lesson['tier'] in ('easy', 'medium', 'hard')
        assert 20 <= lesson['bpm'] <= 400
        beats = 0.0
        for tok in lesson['notes'].split():
            if NOTE.match(tok):
                beats += float(NOTE.match(tok)[4] or 1)
            elif REST.match(tok):
                beats += float(REST.match(tok)[1] or 1)
            else:
                m = CHORD.match(tok)
                assert m and m[1] in chords, f"{lesson['id']}: bad token {tok!r}"
                beats += float(m[2] or 1)
        assert beats > 0


def test_chord_voicings_fit_a_25_key_keyboard(content):
    for name, notes in content['chords'].items():
        nums = [_num(n) for n in notes]
        assert all(48 <= n <= 72 for n in nums), f"{name} leaves C3-C5: {notes}"
        assert nums == sorted(nums) and len(nums) in (3, 4)


def test_songs_mostly_sit_in_the_middle_two_octaves(content):
    for lesson in content['lessons']:
        if lesson['kind'] != 'song':
            continue
        nums = [_num(t.split(':')[0]) for t in lesson['notes'].split() if NOTE.match(t)]
        assert min(nums) >= 48, lesson['id']
        assert max(nums) <= 76, lesson['id']            # Für Elise reaches E5
