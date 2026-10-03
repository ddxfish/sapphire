"""The MIDI keyboard device and the computer's synth behind it. fluidsynth and
ALSA are faked: no process starts, no sound is made. The pretend machine keeps
its own list of what is plugged in and what is linked, so a test can plug a
keyboard in, pull it out, or cut a link."""
import importlib.util
import json
import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).absolute().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, _ROOT.as_posix())

CANON = 'plugins.midi.tools.midi_tools'
AKM = ('20', 'AKM320', 'type=kernel,card=2', 'AKM320 MIDI 1   ')
FM1 = ('129', 'FM-1_BLE', 'type=user,pid=2469', 'FM-1_BLE Bluetooth')
VMPK = ('131', 'VMPK Output', 'type=user,pid=777', 'out')
HERS = ('132', 'aplaymidi', 'type=user,pid=888', 'aplaymidi')
A, F, V = 'name:AKM320', 'name:FM-1_BLE', 'name:VMPK Output'


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class FakeProc:
    def __init__(self, cmd, dies=False):
        self.cmd, self.told, self.code = cmd, [], (1 if dies else None)
        self.stdin = SimpleNamespace(write=self._write, flush=lambda: None)

    def _write(self, text):
        self.told.append(text.strip())
        if text.strip() == 'quit':
            self.code = 0

    def poll(self):
        return self.code

    def wait(self, timeout=None):
        return self.code

    def kill(self):
        self.code = -9


@pytest.fixture
def rig(tmp_path):
    had = sys.modules.get(CANON)
    (tmp_path / 'cards' / 'card2').mkdir(parents=True)
    (tmp_path / 'cards' / 'card2' / 'usbid').write_text('1acc:3c01\n')
    tools = _load(CANON, _ROOT / 'tools' / 'midi_tools.py')
    drv = _load('fm1_keyboard_driver_under_test', _ROOT / 'keyboard_driver.py')
    synth, midi = tools.synth, tools.midi
    box = SimpleNamespace(procs=[], ran=[], fails=set(), here=[AKM], links=set(), played=[], sent=[],
                          playing=[])

    def alive():
        return any(p.poll() is None for p in box.procs)

    def listing():
        out = ["client 0: 'System' [type=kernel]", "    0 'Timer           '", "    1 'Announce        '",
               "client 14: 'Midi Through' [type=kernel]", "    0 'Midi Through Port-0'"]
        for client, name, tag, port in box.here:
            out += [f"client {client}: '{name}' [{tag}]", f"    0 '{port}'"]
            if alive() and f"{client}:0" in box.links:
                out.append("\tConnecting To: 128:0")
        if alive():
            out += ["client 128: 'Sapphire Synth' [type=user,pid=4242]", "    0 'Sapphire Synth  '"]
            if box.links:
                out.append("\tConnected From: " + ', '.join(sorted(box.links)))
        return '\n'.join(out) + '\n'

    def popen(cmd, **kw):
        proc = FakeProc(cmd, dies=cmd[2] in box.fails)
        box.links.clear()                                   # a new synth has no links yet
        box.procs.append(proc)
        return proc

    def sub_run(cmd, **kw):
        box.ran.append(cmd)
        if cmd[0] == 'aconnect' and cmd[1] == '-d':
            box.links.discard(cmd[2])
        elif cmd[0] == 'aconnect' and cmd[1] not in ('-l', '-i'):
            box.links.add(cmd[1])
        return SimpleNamespace(stdout=listing() if cmd[0] == 'aconnect' else '', stderr='', returncode=0)

    def fake_play(port, text, **kw):
        box.played.append((port, text, kw))
        return {'slot': 'loop' if kw.get('loop_minutes') else 'phrase', 'notes': 3, 'beats': 3.0,
                'bpm': float(kw.get('bpm') or 120), 'low': 'C4', 'high': 'G4', 'reps': 4,
                'seconds': 1.5, 'proc': None}

    settings = {'synth_names': 'FM-1_BLE, FM-1', 'keyboard_names': ''}
    call_tool = lambda name, args=None: tools.execute(name, args or {}, None, settings)
    synth._proc = synth._idle_since = None
    synth.wanted.clear()
    synth.state.update(instrument=1, loudness=60, font='', audio='', held_off=False)
    with patch.object(synth.subprocess, 'Popen', popen), \
         patch.object(synth.subprocess, 'run', sub_run), \
         patch.object(synth.shutil, 'which', lambda n: f"/usr/bin/{n}"), \
         patch.object(synth.os.path, 'isfile', lambda p: p in synth.FONTS[:1] or p == '/home/me/organ.sf2'), \
         patch.object(synth, 'SOCKETS', ()), \
         patch.object(synth, 'CARDS', str(tmp_path / 'cards')), \
         patch.object(synth, 'READY_WAIT', 0.3), \
         patch.object(midi, '_listing', listing), \
         patch.object(midi, 'play', fake_play), \
         patch.object(midi, 'send', lambda port, data: box.sent.append((port, data[:8]))), \
         patch.object(midi, 'stop_all', lambda port=None: box.sent.append(('stop', port))), \
         patch.object(midi, 'playing', lambda: list(box.playing)), \
         patch.object(drv, '_settings', lambda: settings):
        yield SimpleNamespace(drv=drv, tools=tools, synth=synth, midi=midi, box=box, call=call_tool,
                              settings=settings)
    synth._proc = None
    synth.wanted.clear()
    sys.modules.pop('fm1_keyboard_driver_under_test', None)
    if had is not None:
        sys.modules[CANON] = had
    else:
        sys.modules.pop(CANON, None)


DEV = {'id': 'keys', 'label': 'My keyboard', 'location': 'Office'}
CFG = {'instrument': 'organ', 'loudness': 50, 'soundfont': '',
       'sources': {'all': True, 'only': []}}
HUSH = [f'cc {ch} {cc} 0' for ch in range(16) for cc in (64, 66, 123)]


def run(rig, cap, action, value='', cfg=CFG):
    return rig.drv.run(DEV, cap, action, value, cfg, None, rig.call)


def tend(rig, *ids, device=DEV, leaving=False, cfg=CFG):
    """What core does: the things that are here and count, handed to the driver."""
    present = [t for t in rig.drv.discover(cfg) if t['id'] in ids]
    assert len(present) == len(ids), (ids, rig.drv.discover(cfg))
    rig.drv.tend(device, cfg, None, present, leaving)


# --- the declaration -----------------------------------------------------------

def test_the_manifest_declares_both_devices(rig):
    decl = json.loads((_ROOT / 'plugin.json').read_text(encoding='utf-8'))['capabilities']['devices']
    assert [d['driver'] for d in decl] == ['synth', 'midi-keyboard']
    mine = decl[1]
    assert (_ROOT / mine['module']).exists()
    assert sorted(mine['capabilities']) == sorted(rig.drv.describe(DEV, CFG))
    assert not [f for f in mine['config_schema'] if f.get('secret')]
    assert mine['presence'] is True and 'presence' not in decl[0]
    assert [f['key'] for f in mine['config_schema'] if f['type'] == 'found'] == ['sources']
    assert 'always_on' not in [f['key'] for f in mine['config_schema']]
    assert all(callable(getattr(rig.drv, fn)) for fn in ('discover', 'watch', 'tend'))


# --- what is here --------------------------------------------------------------

def test_every_midi_source_that_is_here_is_found(rig):
    rig.box.here = [AKM, FM1, VMPK, HERS, ('133', 'PipeWire-RT-Event', 'type=user,pid=9', 'input')]
    assert rig.drv.discover(CFG) == [
        {'id': A, 'name': 'AKM320', 'kind': 'USB'},
        {'id': F, 'name': 'FM-1_BLE', 'kind': 'Bluetooth'},
        {'id': V, 'name': 'VMPK Output', 'kind': 'program'}]          # never her own player, never plumbing
    rig.box.here = []
    assert rig.drv.discover(CFG) == []
    run(rig, 'sound', 'on')
    assert rig.drv.discover(CFG) == []                                # the synth itself is not a source


def test_hardware_goes_by_a_name_that_holds_across_a_replug(rig, tmp_path):
    by_id, by_path = tmp_path / 'by-id', tmp_path / 'by-path'
    by_id.mkdir()
    by_path.mkdir()
    (tmp_path / 'controlC2').write_text('')
    (tmp_path / 'controlC5').write_text('')
    (by_id / 'usb-MIDIPLUS_AKM320-00').symlink_to(tmp_path / 'controlC2')
    (by_path / 'pci-0000:00:14.0-usb-0:6.2:1.0').symlink_to(tmp_path / 'controlC2')
    (by_path / 'pci-0000:00:14.0-usb-0:6.3:1.0').symlink_to(tmp_path / 'controlC5')

    def ident(name, card):
        with patch.object(rig.synth, 'SOCKETS', (str(by_id), str(by_path), str(tmp_path / 'not-there'))):
            return rig.synth._ident(name, card)
    assert ident('AKM320', '2') == 'usb-MIDIPLUS_AKM320-00'           # its own name and serial
    assert ident('No serial', '5') == 'pci-0000:00:14.0-usb-0:6.3:1.0'    # else the socket it sits in
    assert ident('Unknown', '9') == 'name:Unknown'
    assert ident('FM-1_BLE', None) == 'name:FM-1_BLE'                 # Bluetooth goes by its name
    rig.box.here = [AKM, ('21', 'AKM320', 'type=kernel,card=3', 'AKM320 MIDI 1   ')]
    assert [t['id'] for t in rig.drv.discover(CFG)] == [A, A + '#2']  # two alike are still two


# --- the synth runs only while it has something to play for ----------------------

def test_a_keyboard_that_is_plugged_in_simply_plays(rig):
    rig.box.here = []
    tend(rig)
    assert rig.box.procs == []                                        # nothing here: nothing runs
    rig.box.here = [AKM]
    tend(rig, A)
    proc = rig.box.procs[0]
    assert rig.synth.running() and rig.box.links == {'20:0'}
    assert proc.told[-1] == 'prog 0 19'                               # as the user set it: organ
    assert proc.cmd[proc.cmd.index('-g') + 1] == '0.75'
    tend(rig, A)
    tend(rig, A)
    assert len(rig.box.procs) == 1 and proc.told[-1] == 'prog 0 19'   # told again: nothing to do


def test_two_play_together(rig):
    tend(rig, A)
    rig.box.here = [AKM, FM1]
    tend(rig, A, F)
    assert rig.box.links == {'20:0', '129:0'} and len(rig.box.procs) == 1
    assert rig.synth.keyboards_in() == ['FM-1_BLE', 'AKM320']
    assert HUSH[0] not in rig.box.procs[0].told                       # nobody left: nobody is cut off


def test_the_filter_keeps_one_out(rig):
    rig.box.here = [AKM, FM1]
    tend(rig, A, F)
    assert rig.box.links == {'20:0', '129:0'}
    tend(rig, A)                                                      # the user unticked the FM-1
    assert rig.box.links == {'20:0'}
    assert ['aconnect', '-d', '129:0', '128:0'] in rig.box.ran
    assert rig.box.procs[0].told[-len(HUSH):] == HUSH                 # its held notes end with it


def test_a_link_of_her_own_player_is_left_alone(rig):
    tend(rig, A)
    rig.box.here = [AKM, HERS]
    rig.box.links.add('132:0')
    tend(rig, A)
    assert rig.box.links == {'20:0', '132:0'}


def test_one_that_is_pulled_out_never_leaves_a_note_ringing(rig):
    rig.box.here = [AKM, FM1]
    tend(rig, A, F)
    told = rig.box.procs[0].told
    rig.box.here = [AKM]                                              # pulled out with the pedal down
    rig.box.links.discard('129:0')
    tend(rig, A)
    assert told[-len(HUSH):] == HUSH and rig.synth.running()
    assert 'cc 0 64 0' in HUSH and 'cc 15 123 0' in HUSH              # every pedal, every channel
    assert not [t for t in told if t.startswith(('reset', 'prog')) and told.index(t) > told.index('prog 0 19')]
    count = len(told)
    tend(rig, A)
    assert len(told) == count                                         # once, not on every look


def test_it_goes_off_a_little_after_the_last_one_left(rig):
    tend(rig, A)
    rig.box.here = []
    rig.box.links.clear()
    with patch.object(rig.synth.time, 'monotonic', lambda: 1000.0):
        tend(rig)
        assert rig.synth.running()                                    # a replug is not a restart
        assert rig.box.procs[0].told[-len(HUSH):] == HUSH
    with patch.object(rig.synth.time, 'monotonic', lambda: 1000.0 + rig.synth.GRACE - 1):
        tend(rig)
        assert rig.synth.running()
    with patch.object(rig.synth.time, 'monotonic', lambda: 1000.0 + rig.synth.GRACE):
        tend(rig)
    assert not rig.synth.running() and rig.box.procs[0].told[-1] == 'quit'
    assert rig.synth.state['held_off'] is False                       # nobody switched it off by hand
    rig.box.here = [AKM]
    tend(rig, A)
    assert rig.synth.running() and len(rig.box.procs) == 2            # back in: it plays again


def test_a_replug_inside_the_wait_keeps_the_same_synth(rig):
    tend(rig, A)
    rig.box.here = []
    rig.box.links.clear()
    with patch.object(rig.synth.time, 'monotonic', lambda: 1000.0):
        tend(rig)
    rig.box.here = [AKM]
    with patch.object(rig.synth.time, 'monotonic', lambda: 1005.0):
        tend(rig, A)
    assert rig.box.links == {'20:0'} and len(rig.box.procs) == 1
    rig.box.here = []
    with patch.object(rig.synth.time, 'monotonic', lambda: 1000.0 + rig.synth.GRACE + 1):
        tend(rig)
        assert rig.synth.running()                                    # the wait began anew


def test_it_stays_on_while_she_plays(rig):
    rig.box.here = []
    run(rig, 'notes', 'play', 'C4 E4 G4')                             # her playing switches it on
    assert rig.synth.running()
    rig.box.playing = ['phrase']
    with patch.object(rig.synth.time, 'monotonic', lambda: 1000.0):
        tend(rig)
    with patch.object(rig.synth.time, 'monotonic', lambda: 5000.0):
        tend(rig)
        assert rig.synth.running()
        rig.box.playing = []
        tend(rig)
        assert rig.synth.running()                                    # the wait starts when she ends
    with patch.object(rig.synth.time, 'monotonic', lambda: 5000.0 + rig.synth.GRACE):
        tend(rig)
    assert not rig.synth.running()


def test_a_synth_that_fell_over_is_brought_back(rig):
    tend(rig, A)
    rig.box.procs[0].code = 1
    tend(rig, A)                                                      # the heartbeat
    assert rig.synth.running() and len(rig.box.procs) == 2
    assert rig.box.links == {'20:0'} and rig.box.procs[1].told[-1] == 'prog 0 19'


def test_a_link_that_was_cut_is_restored(rig):
    tend(rig, A)
    rig.box.links.clear()                                             # another program cut it
    tend(rig, A)
    assert rig.box.links == {'20:0'} and len(rig.box.procs) == 1


def test_a_hand_on_the_switch_is_respected(rig):
    tend(rig, A)
    assert run(rig, 'sound', 'off') == (
        'The sound is off. The keys are silent until it is switched on, or a keyboard is plugged in.', True)
    tend(rig, A)
    tend(rig, A)
    assert not rig.synth.running() and len(rig.box.procs) == 1        # the heartbeat leaves it off
    assert rig.drv.status(DEV, CFG, None)['readings'] == {
        'sound': 'off, switched off by hand. Playing switches it on'}
    rig.box.here = [AKM, FM1]
    tend(rig, A, F)                                                   # plugging one in asks for sound
    assert rig.synth.running() and rig.box.links == {'20:0', '129:0'}


def test_every_keyboard_device_shares_the_one_synth(rig):
    second = dict(DEV, id='second')
    rig.box.here = [AKM, FM1]
    tend(rig, A)
    tend(rig, F, device=second)
    assert rig.box.links == {'20:0', '129:0'} and len(rig.box.procs) == 1
    tend(rig, device=second, leaving=True)                            # that device was switched off
    assert rig.synth.running() and rig.box.links == {'20:0'}
    tend(rig, leaving=True)                                           # the last one: nothing calls again
    assert not rig.synth.running() and rig.synth.wanted == {}
    assert rig.box.procs[0].told[-1] == 'quit' and rig.box.procs[0].told[-len(HUSH) - 1:-1] == HUSH
    assert rig.synth.state['held_off'] is False


def test_what_is_missing_is_said_once_not_on_every_heartbeat(rig, caplog):
    with patch.object(rig.synth.shutil, 'which', lambda n: None):
        with caplog.at_level('WARNING'):
            for _ in range(5):
                tend(rig, A)
    said = [r.getMessage() for r in caplog.records if 'fluidsynth is not installed' in r.getMessage()]
    assert said == ['[midi] keys: fluidsynth is not installed.'] and rig.box.procs == []


def test_the_watcher_speaks_only_of_what_matters(rig):
    read_end, write_end = os.pipe()
    proc = SimpleNamespace(stdout=os.fdopen(read_end, 'rb', buffering=0), code=None, ended=[])
    proc.poll = lambda: proc.code
    proc.terminate = lambda: proc.ended.append('terminate')
    proc.wait = lambda timeout=None: 0
    proc.kill = lambda: proc.ended.append('kill')
    started, pokes, stopped = [], [], threading.Event()
    with patch.object(rig.drv.subprocess, 'Popen', lambda cmd, **kw: started.append(cmd) or proc), \
         patch.object(rig.drv.shutil, 'which', lambda n: f"/usr/bin/{n}"):
        t = threading.Thread(target=rig.drv.watch, args=(lambda: pokes.append(1), stopped), daemon=True)
        t.start()
        # her own lookups and players make these all day: they must not start a loop
        os.write(write_end, b"Waiting for data. Press Ctrl+C to end.\n"
                            b"  0:1   Client start               client 132\n"
                            b"  0:1   Client changed             client 132\n"
                            b"  0:1   Client exit                client 132\n")
        time.sleep(0.2)
        assert pokes == []
        for line in (b"  0:1   Port start                 32:0\n", b"  0:1   Port exit                  32:0\n",
                     b"  0:1   Port unsubscribed          32:0 -> 128:0\n", b"  0:1   Port subsc"):
            os.write(write_end, line)
            time.sleep(0.1)
        assert len(pokes) == 3                                        # half a line is not a line yet
        os.write(write_end, b"ribed            32:0 -> 128:0\n")
        time.sleep(0.1)
        assert len(pokes) == 4
        stopped.set()
        t.join(timeout=3)
        assert not t.is_alive() and proc.ended == ['terminate']
    os.close(write_end)
    assert started == [['aseqdump', '-p', '0:1']]


def test_a_watcher_whose_program_ended_hands_back_to_core(rig):
    read_end, write_end = os.pipe()
    os.close(write_end)
    proc = SimpleNamespace(stdout=os.fdopen(read_end, 'rb', buffering=0), poll=lambda: 1,
                           terminate=lambda: None, wait=lambda timeout=None: 1, kill=lambda: None)
    with patch.object(rig.drv.subprocess, 'Popen', lambda cmd, **kw: proc), \
         patch.object(rig.drv.shutil, 'which', lambda n: f"/usr/bin/{n}"):
        began = time.monotonic()
        rig.drv.watch(lambda: None, threading.Event())                # returns: core starts it again
        assert time.monotonic() - began < 2
    stopped = threading.Event()
    with patch.object(rig.drv.shutil, 'which', lambda n: None):       # no aseqdump: the heartbeat does it
        t = threading.Thread(target=rig.drv.watch, args=(lambda: None, stopped), daemon=True)
        t.start()
        time.sleep(0.1)
        assert t.is_alive()
        stopped.set()
        t.join(timeout=2)
        assert not t.is_alive()


# --- the computer's synth ------------------------------------------------------

def test_the_sound_comes_on_as_the_user_set_it(rig):
    rig.synth.wanted['keys'] = {A}
    text, ok = run(rig, 'sound', 'on')
    assert ok and text == 'The sound is on: organ, loudness 50%. AKM320 plays through it.'
    proc = rig.box.procs[0]
    assert proc.cmd[:5] == ['fluidsynth', '-a', 'pipewire', '-m', 'alsa_seq']
    assert proc.cmd[proc.cmd.index('-g') + 1] == '0.75'                 # 50% of the loudest
    assert proc.cmd[-1] == rig.synth.FONTS[0]
    assert 'midi.autoconnect=0' in proc.cmd                             # it links nothing by itself
    assert '-s' not in proc.cmd and '--server' not in proc.cmd          # no network door is opened
    assert proc.told[-1] == 'prog 0 19'                                 # organ is program 20
    assert proc.told[:-1] == list(rig.synth.ROUTER)                     # the slider rule goes in first
    assert run(rig, 'sound', 'on')[0].startswith('The sound was already on: organ')
    assert len(rig.box.procs) == 1                                      # never a second synth
    rig.synth.stop()
    rig.box.here = [AKM, FM1]
    rig.synth.wanted['keys'] = {A, F}
    assert run(rig, 'sound', 'on')[0].endswith('FM-1_BLE and AKM320 play through it.')


def test_the_next_sound_system_is_tried_when_one_fails(rig):
    rig.box.fails = {'pipewire', 'pulseaudio'}
    assert run(rig, 'sound', 'on')[1] is True
    assert [p.cmd[2] for p in rig.box.procs] == ['pipewire', 'pulseaudio', 'alsa']
    assert rig.synth.state['audio'] == 'alsa'
    rig.synth.stop()
    rig.box.fails = {'pipewire', 'pulseaudio', 'alsa'}
    text, ok = run(rig, 'sound', 'on')
    assert not ok and text == "The computer's sound did not start (fluidsynth did not start with alsa)."


def test_instrument_and_loudness(rig):
    assert run(rig, 'sound', 'instrument', 'violin') == ('Instrument 41 violin, for the keys and for you.', True)
    assert rig.synth.running()                                          # asking for one switches it on
    assert rig.box.procs[0].told[-1] == 'prog 0 40'
    assert run(rig, 'sound', 'instrument', '74')[0] == 'Instrument 74 flute, for the keys and for you.'
    assert run(rig, 'sound', 'instrument', 'list')[0].startswith('Instruments: piano, bright piano')
    text, ok = run(rig, 'sound', 'instrument', 'kazoo')
    assert not ok and text.startswith("No instrument called 'kazoo'.")
    assert run(rig, 'sound', 'loudness', '80%') == ('Loudness 80%.', True)
    assert rig.box.procs[0].told[-1] == 'gain 1.20'
    for wrong in ('loud', '140', '-1'):
        assert run(rig, 'sound', 'loudness', wrong)[1] is False
    assert rig.box.procs[0].told[-1] == 'gain 1.20'                     # a bad value changed nothing


def test_off(rig):
    run(rig, 'sound', 'on')
    assert run(rig, 'sound', 'off')[0].startswith('The sound is off. The keys are silent until')
    assert rig.box.procs[0].told[-1] == 'quit' and not rig.synth.running()
    assert run(rig, 'sound', 'off') == ('The sound was already off.', True)


def test_what_is_missing_is_said_plainly(rig):
    with patch.object(rig.synth.shutil, 'which', lambda n: None):
        assert rig.drv.status(DEV, CFG, None) == {'online': False, 'detail': 'fluidsynth is not installed.'}
        assert run(rig, 'sound', 'on') == ('fluidsynth is not installed.', False)
    with patch.object(rig.synth.os.path, 'isfile', lambda p: False):
        text, ok = run(rig, 'sound', 'on')
        assert not ok and text.startswith('No SoundFont is installed')
    assert rig.box.procs == []


def test_a_soundfont_of_the_users_own(rig):
    assert rig.drv.validate(dict(CFG, soundfont='/home/me/organ.sf2')) == \
        (dict(CFG, soundfont='/home/me/organ.sf2'), '')
    assert rig.drv.validate(dict(CFG, soundfont='/nope.sf2'))[1] == 'The SoundFont /nope.sf2 is not there.'
    assert rig.drv.validate(dict(CFG, instrument='kazoo'))[1].startswith("No instrument called 'kazoo'")
    run(rig, 'sound', 'on', cfg=dict(CFG, soundfont='/home/me/organ.sf2'))
    assert rig.box.procs[0].cmd[-1] == '/home/me/organ.sf2'


# --- what she reads --------------------------------------------------------------

def test_asking_how_it_is_changes_nothing(rig):
    st = rig.drv.status(DEV, CFG, None)
    assert st == {'online': True, 'detail': 'Sound by this computer',
                  'readings': {'sound': 'off. It comes on when a keyboard is plugged in, or when you play'}}
    assert rig.box.procs == []                                          # a look never starts a synth
    tend(rig, A)
    assert rig.drv.status(DEV, CFG, None)['readings'] == {       # what is connected is core's to say
        'sound': 'on', 'instrument': 'organ', 'loudness': '50%'}
    rig.box.procs[0].code = 1                                           # it fell over
    assert rig.drv.status(DEV, CFG, None)['readings']['sound'].startswith('off.')
    assert len(rig.box.procs) == 1                                      # bringing it back is tend's work


def test_no_keyboard_plugged_in(rig):
    rig.box.here = []
    text, ok = run(rig, 'sound', 'on')
    assert ok and text.endswith('No keyboard is here. It goes off again by itself.')


# --- she plays on it -----------------------------------------------------------

def test_her_notes_go_to_the_computers_synth(rig):
    text, ok = run(rig, 'notes', 'play', 'C4 E4 G4 bpm=90 velocity=70')
    assert ok and text.startswith("Playing on the computer's synth: 3 notes")
    assert rig.synth.running()                                          # playing switched it on
    port, notes, kw = rig.box.played[0]
    assert (port, notes) == ('128:0', 'C4 E4 G4') and kw['bpm'] == 90.0 and kw['velocity'] == 70
    assert kw['voice'] is None                                          # FM-1 voice numbers never reach it
    text, ok = run(rig, 'notes', 'loop', 'C2 R C2 G2 minutes=2 instrument=bass')
    assert ok and text.startswith("Looping on the computer's synth")
    assert rig.box.procs[0].told[-1] == 'prog 0 33' and rig.box.played[1][2]['loop_minutes'] == 2.0
    assert run(rig, 'notes', 'play', 'C4 bpm=fast') == ("bpm has to be a number, not 'fast'", False)
    assert run(rig, 'notes', 'play', '')[0].startswith('play: the value is the music')


def test_a_synth_she_switched_on_links_the_keyboard_that_is_here(rig):
    tend(rig, A)
    run(rig, 'sound', 'off')
    run(rig, 'notes', 'play', 'C4')                                     # she plays: it is on again
    assert rig.synth.running() and rig.box.links == {'20:0'}            # and the keys sound at once


def test_stop(rig):
    run(rig, 'sound', 'on')
    text, ok = run(rig, 'notes', 'stop')
    assert ok and text.startswith('Stopped.') and rig.box.sent == [('stop', '128:0')]
    assert rig.box.procs[0].told[-len(HUSH):] == HUSH                   # a held pedal ends too


def test_stop_never_switches_the_sound_on(rig):
    """It did: "stop" went through the same door as "play", and that door
    starts the synth (quality check, 2026-09-28)."""
    text, ok = run(rig, 'notes', 'stop')
    assert ok and text.startswith('Stopped.') and 'no synth is connected' in text
    assert rig.box.procs == [] and not rig.synth.running()
    tend(rig, A)
    run(rig, 'sound', 'off')
    run(rig, 'notes', 'stop')
    assert not rig.synth.running() and len(rig.box.procs) == 1          # off by hand stays off
    assert rig.call('midi_stop', {})[1] is True and len(rig.box.procs) == 1


def test_she_hears_every_keyboard_that_counts(rig):
    rig.box.here = [AKM, FM1, VMPK]
    assert rig.tools._keyboards(rig.settings) == ('AKM320', 'VMPK Output')   # the FM-1 is the synth itself
    rig.synth.wanted['keys'] = {A}                                      # a keyboard device's filter holds
    assert rig.tools._keyboards(rig.settings) == ('AKM320',)
    assert rig.tools._listen_ports(rig.settings) == ('129:0,20:0', 'FM-1_BLE and AKM320')
    rig.synth.wanted['keys'] = set()
    assert rig.tools._keyboards(rig.settings) == ()
    assert rig.tools._keyboards(dict(rig.settings, keyboard_names='Casio, AKM320')) == ('Casio', 'AKM320')
    with patch.object(rig.synth, 'sources', lambda: 1 / 0):
        assert rig.tools._keyboards(rig.settings) == ()                 # never an error in her turn


def test_the_fm1_tools_use_the_computer_only_when_the_fm1_is_away(rig):
    """The FM-1 keeps first place. The computer stands in when it is not there."""
    assert rig.tools._synths({}, rig.settings) == ('FM-1_BLE', 'FM-1')            # synth off: FM-1 only
    text, ok = rig.call('midi_play', {'notes': 'C4'})
    assert not ok and 'No synth is connected' in text and rig.box.played == []
    run(rig, 'sound', 'on')
    assert rig.tools._synths({}, rig.settings) == ('FM-1_BLE', 'FM-1', 'Sapphire Synth')
    text, ok = rig.call('midi_play', {'notes': 'C4'})
    assert ok and text.startswith("Playing on the computer's synth")
    assert rig.call('midi_sound', {'voice': 'strings'}) == ("The computer's synth: instrument 49 strings.", True)
    text, ok = rig.call('midi_sound', {'effect': 'reverb'})
    assert not ok and text == "The computer's synth has no effects."
    rig.box.here = [AKM, FM1]
    assert rig.call('midi_play', {'notes': 'C4'})[0].startswith('Playing on FM-1_BLE')   # the FM-1 is back


def test_every_example_in_the_help_really_runs(rig):
    with patch.object(rig.tools, '_calling_chat', lambda: 'trinity'), \
         patch.object(rig.tools, '_start_listen', lambda *a, **k: True):
        for cap, info in rig.drv.describe(DEV, CFG).items():
            for action, a in info['actions'].items():
                told, ok = run(rig, cap, action, a['example'])
                assert ok, (cap, action, told)


def test_nothing_starts_by_itself_when_the_plugin_loads(rig):
    """The old way was a thread that started at import and switched the synth
    on ten seconds later. Presence does that now, and only for a reason."""
    import inspect
    source = inspect.getsource(rig.tools)
    assert '_always_on' not in source and 'always_on' not in inspect.getsource(rig.drv)
    assert not [t for t in threading.enumerate() if t.name == 'fm1-always-on']


def test_only_a_real_synth_of_ours_is_ever_ended(rig, tmp_path):
    """A process that only MENTIONS the synth in its command line is left alone."""
    import builtins
    lines = {'100': b'fluidsynth\0-a\0pipewire\0-p\0Sapphire Synth\0/usr/share/sounds/sf2/default-GM.sf2\0',
             '200': b'fluidsynth\0-a\0pipewire\0-p\0Some other synth\0font.sf2\0',
             '300': b'bash\0-c\0pgrep -a fluidsynth; grep "Sapphire Synth" notes.txt\0'}
    real_open = builtins.open

    def fake_open(path, mode='r', *a, **k):
        pid = str(path).split('/')[2] if str(path).startswith('/proc/') else None
        if pid in lines:
            f = tmp_path / pid
            f.write_bytes(lines[pid])
            return real_open(f, mode)
        if pid:
            raise OSError('gone')
        return real_open(path, mode, *a, **k)

    def sub_run(cmd, **kw):
        assert cmd == ['pgrep', '-x', 'fluidsynth']              # by the program's exact name
        return SimpleNamespace(stdout='100\n200\n999\n', stderr='', returncode=0)

    killed = []
    with patch.object(rig.synth.subprocess, 'run', sub_run), \
         patch.object(builtins, 'open', fake_open), \
         patch.object(rig.synth.os, 'kill', lambda pid, sig: killed.append(pid)):
        assert rig.synth._strays() == [100]
        rig.synth._sweep()
    assert killed == [100]
    import inspect
    assert 'pkill' not in inspect.getsource(rig.synth)


def test_only_a_real_player_of_ours_is_ever_ended(rig, tmp_path):
    import builtins
    import inspect
    lines = {'100': b'aplaymidi\0-p\0128:0\0/tmp/midi_sapphire_loop.mid\0',
             '200': b'aplaymidi\0-p\0128:0\0/home/me/my_own_song.mid\0',
             '300': b'bash\0-c\0echo aplaymidi /tmp/midi_sapphire_loop.mid\0'}
    real_open = builtins.open

    def fake_open(path, mode='r', *a, **k):
        pid = str(path).split('/')[2] if str(path).startswith('/proc/') else None
        if pid in lines:
            f = tmp_path / pid
            f.write_bytes(lines[pid])
            return real_open(f, mode)
        if pid:
            raise OSError('gone')
        return real_open(path, mode, *a, **k)

    with patch.object(rig.midi.subprocess, 'run',
                      lambda cmd, **kw: SimpleNamespace(stdout='100\n200\n', stderr='', returncode=0)), \
         patch.object(builtins, 'open', fake_open):
        assert rig.midi.stray_players() == [100]                  # the user's own song plays on
    assert 'pkill' not in inspect.getsource(rig.midi)


def test_a_keyboards_volume_slider_can_never_silence_it(rig):
    """The slider and the pedal still work. Their lowest position is quiet, not silent."""
    rules = list(rig.synth.ROUTER)
    assert rules[0] == 'router_clear'                       # then only what is listed passes
    floor = 'router_par2 0 127 0.62 48'
    for controller in (7, 11):                              # channel volume, expression
        at = rules.index(f'router_par1 {controller} {controller} 1 0')
        assert rules[at - 1] == 'router_begin cc' and rules[at + 1] == floor and rules[at + 2] == 'router_end'
    assert round(0 * 0.62 + 48) == 48 and round(127 * 0.62 + 48) == 127      # bottom 48, top 127
    # every controller number is let through by exactly one rule
    spans = [tuple(int(n) for n in r.split()[1:3]) for r in rules if r.startswith('router_par1')]
    covered = sorted(n for lo, hi in spans for n in range(lo, hi + 1))
    assert covered == list(range(128))
    for kind in ('note', 'prog', 'pbend', 'cpress', 'kpress'):
        assert f'router_begin {kind}' in rules              # keys, instrument, bend and pressure all pass
    assert rules.count('router_end') == sum(r.startswith('router_begin') for r in rules)
