"""FM-1 device driver: every action lands in the plugin's REAL tool handlers
(the tool door), with the synth and ALSA faked. No sound, no MIDI, no Sapphire."""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).absolute().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

CANON = 'plugins.midi.tools.midi_tools'


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def rig():
    """The real tools module under its canonical name (where the function
    manager puts it), the driver, and a call_tool that is the tools' execute."""
    had = sys.modules.get(CANON)
    tools = _load(CANON, _ROOT / 'tools' / 'midi_tools.py')
    drv = _load('synth_driver_under_test', _ROOT / 'synth_driver.py')
    midi = tools.midi
    played, sent = [], []

    def fake_play(port, text, **kw):
        played.append((port, text, kw))
        slot = 'loop' if kw.get('loop_minutes') else 'phrase'
        return {'slot': slot, 'notes': 3, 'beats': 3.0, 'bpm': float(kw.get('bpm') or 120),
                'low': 'C4', 'high': 'G4', 'reps': 4, 'seconds': 1.5, 'proc': None}

    settings = {'synth_names': 'FM-1_BLE, FM-1', 'keyboard_names': 'AKM320'}
    call_tool = lambda name, args=None: tools.execute(name, args or {}, None, settings)
    with patch.object(midi, 'find_port', lambda names=None, listing=None: ('128:0', 'FM-1_BLE')), \
         patch.object(midi, 'find_ports', lambda names, listing=None:
                      [('128:0', n) for n in names if n == 'FM-1_BLE'] + [('20:0', n) for n in names if n == 'AKM320']), \
         patch.object(midi, 'play', fake_play), \
         patch.object(midi, 'program', lambda port, n: sent.append(('voice', n))), \
         patch.object(midi, 'fx', lambda port, effects, ch, effect, on, levels: sent.append(('fx', effect, on, levels)) or levels), \
         patch.object(midi, 'stop_all', lambda port=None: sent.append(('stop', port))), \
         patch.object(midi, 'playing', lambda: ['loop']), \
         patch.object(drv, '_settings', lambda: settings):
        yield SimpleNamespace(drv=drv, tools=tools, midi=midi, played=played, sent=sent, call=call_tool)
    sys.modules.pop('synth_driver_under_test', None)
    if had is not None:
        sys.modules[CANON] = had
    else:
        sys.modules.pop(CANON, None)


DEV = {'id': 'fm1', 'label': 'FM-1'}
run = lambda rig, cap, action, value='': rig.drv.run(DEV, cap, action, value, {}, None, rig.call)


def test_describe_matches_the_manifest(rig):
    import json
    told = rig.drv.describe(DEV, {})
    decl = json.loads((_ROOT / 'plugin.json').read_text(encoding='utf-8'))['capabilities']['devices'][0]
    assert list(told) == decl['capabilities'] == ['notes', 'sound']
    assert decl['driver'] == 'synth' and (_ROOT / decl['module']).exists()
    assert list(told['notes']['actions']) == ['play', 'loop', 'listen', 'stop']
    assert list(told['sound']['actions']) == ['voice', 'effect']


def test_every_example_in_the_help_really_runs(rig):
    """The help is a promise: each example must be accepted as written."""
    with patch.object(rig.tools, '_calling_chat', lambda: 'trinity'), \
         patch.object(rig.tools, '_start_listen', lambda *a, **k: True):
        for cap, info in rig.drv.describe(DEV, {}).items():
            for action, a in info['actions'].items():
                text, ok = run(rig, cap, action, a['example'])
                assert ok, (cap, action, text)


def test_play_splits_music_from_options(rig):
    text, ok = run(rig, 'notes', 'play', 'C4 E4 [C4 E4 G4]:2 R:1 bpm=90 voice=11 velocity=70')
    assert ok and text.startswith('Playing on FM-1_BLE')
    port, notes, kw = rig.played[0]
    assert port == '128:0' and notes == 'C4 E4 [C4 E4 G4]:2 R:1'
    assert kw == {'bpm': 90.0, 'voice': 11, 'velocity': 70, 'loop_minutes': 0}


def test_loop_defaults_to_one_minute(rig):
    text, ok = run(rig, 'notes', 'loop', 'C2 R C2 G2')
    assert ok and text.startswith('Looping on FM-1_BLE') and rig.played[0][2]['loop_minutes'] == 1
    run(rig, 'notes', 'loop', 'C2 R minutes=2.5')
    assert rig.played[1][2]['loop_minutes'] == 2.5


def test_no_music_prints_the_notation(rig):
    text, ok = run(rig, 'notes', 'play', '')
    assert ok and 'C4 is middle C' in text and 'bpm=120' in text and rig.played == []
    text, ok = run(rig, 'notes', 'loop', 'bpm=90')
    assert ok and 'minutes=N' in text and rig.played == []


def test_a_bad_number_is_said_plainly(rig):
    text, ok = run(rig, 'notes', 'play', 'C4 bpm=fast')
    assert not ok and text == "bpm has to be a number, not 'fast'" and rig.played == []
    text, ok = run(rig, 'notes', 'play', 'C4 bpm=9000')          # the tool's own range check
    assert not ok or rig.played                                   # either refused or passed on, never a crash


def test_stop_reaches_the_same_stop(rig):
    text, ok = run(rig, 'notes', 'stop')
    assert ok and text.startswith('Stopped.') and rig.sent == [('stop', '128:0')]


def test_listen(rig):
    started = []
    with patch.object(rig.tools, '_calling_chat', lambda: 'trinity'), \
         patch.object(rig.tools, '_start_listen', lambda *a, **k: started.append(a) or True):
        text, ok = run(rig, 'notes', 'listen', '45 bpm=100')
    assert ok and text.startswith('Listening on') and started[0][2] == 45.0 and started[0][3] == 100.0


def test_voice_and_effect(rig):
    text, ok = run(rig, 'sound', 'voice', '11')
    assert ok and rig.sent[-1] == ('voice', 11)
    text, ok = run(rig, 'sound', 'effect', 'reverb on mix=60 decay=40')
    assert ok and rig.sent[-1] == ('fx', 'reverb', True, {'mix': 60.0, 'decay': 40.0})
    text, ok = run(rig, 'sound', 'effect', 'Reverb OFF')
    assert ok and rig.sent[-1] == ('fx', 'reverb', False, {})
    assert run(rig, 'sound', 'voice', '')[0].startswith('voice:')
    assert 'reverb' in run(rig, 'sound', 'effect', '')[0]


def test_unknown_action(rig):
    assert run(rig, 'notes', 'explode') == ('The synth has no notes / explode.', False)


def test_status_online_reads_the_tools_own_state(rig):
    st = rig.drv.status(DEV, {}, None)
    assert st == {'online': True, 'detail': 'FM-1_BLE over Bluetooth, port 128:0',
                  'readings': {'keyboards': 'AKM320', 'synth': 'M-VAVE FM-1', 'playing': 'loop'}}
    assert rig.drv._tools() is rig.tools and rig.drv._tools().midi is rig.midi


def test_status_offline(rig):
    with patch.object(rig.midi, 'find_ports', lambda names, listing=None: []):
        st = rig.drv.status(DEV, {}, None)
    assert st == {'online': False, 'detail': rig.drv.OFFLINE, 'readings': {}}

    def no_alsa(names, listing=None):
        raise rig.midi.MidiError('This machine is missing aconnect (install alsa-utils).')
    with patch.object(rig.midi, 'find_ports', no_alsa):
        assert rig.drv.status(DEV, {}, None) == {
            'online': False, 'detail': 'This machine is missing aconnect (install alsa-utils).'}


def test_the_driver_holds_no_midi_code():
    src = (_ROOT / 'synth_driver.py').read_text(encoding='utf-8')
    for banned in ('subprocess', 'aplaymidi', 'aseqsend', 'aseqdump', 'import midi_core', 'mido'):
        assert banned not in src, banned


def test_a_synth_without_a_profile_plays_gm_and_has_no_effects(rig):
    """The generic path: a synth nobody wrote a profile for gets General MIDI
    names and a plain answer about effects, never an FM-1 error."""
    with patch.object(rig.midi, 'find_port', lambda names=None, listing=None: ('24:0', 'Minilogue')), \
         patch.object(rig.midi, 'find_ports', lambda names, listing=None: [('24:0', 'Minilogue')]):
        text, ok = run(rig, 'sound', 'voice', 'organ')
        assert ok and rig.sent[-1] == ('voice', 20) and 'Voice 20 organ' in text
        text, ok = run(rig, 'sound', 'voice', 'list')
        assert ok and 'not a synth I know' in text and '1 piano' in text and 'No effects' in text
        text, ok = run(rig, 'sound', 'effect', 'reverb on')
        assert not ok and 'Minilogue has no effects I know how to switch' in text
        st = rig.drv.status(DEV, {}, None)
        assert st['online'] and st['readings']['synth'].startswith('Minilogue (no profile')
    text, ok = run(rig, 'sound', 'voice', 'list')
    assert ok and text.startswith('M-VAVE FM-1 voices') and 'Effects: filter, reverb' in text
