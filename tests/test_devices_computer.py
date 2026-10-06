# tests/test_devices_computer.py - the "This computer" driver (core/devices/drivers).
# Every program it would run is faked: no volume moves, nothing is switched
# off, no picture is taken. The replies below are what the real programs print.
import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.devices import engine
from core.devices.drivers import computer as pc

DEV = {'id': 'computer', 'label': 'This computer', 'location': 'Office'}
CFG = {'step': 5, 'loudest': 100}

WPCTL_STATUS = """PipeWire 'pipewire-0' [1.2.7, user@box, cookie:1]
Audio
 ├─ Devices:
 │      40. Built-in Audio                      [alsa]
 │  
 ├─ Sinks:
 │  *   44. Built-in Audio Digital Stereo (IEC958) [vol: 0.45]
 │      54. HDA NVidia Digital Stereo (HDMI)    [vol: 0.40]
 │  
 ├─ Sources:
 │  *   45. Built-in Audio Analog Stereo        [vol: 1.00]
 │  
 └─ Streams:
Video
"""


class Box:
    """A pretend machine: which programs it has, its volume, what was run."""

    def __init__(self, has=('wpctl', 'systemctl', 'busctl'), level=45, muted=False, power='yes'):
        self.has, self.level, self.muted, self.power = set(has), level, muted, power
        self.default = '44'
        self.ran = []

    def which(self, name):
        return f"/usr/bin/{name}" if name in self.has else None

    def sh(self, cmd, timeout=6, text_in=None):
        self.ran.append(list(cmd))
        prog, args = cmd[0], cmd[1:]
        if prog not in self.has:
            return 127, '', f"{prog} is not installed"
        if prog == 'wpctl':
            if args[0] == 'get-volume':
                return 0, f"Volume: {self.level / 100:.2f}" + (' [MUTED]' if self.muted else '') + '\n', ''
            if args[0] == 'set-volume':
                self.level = int(args[-1].rstrip('%'))
            elif args[0] == 'set-mute':
                self.muted = args[-1] == '1'
            elif args[0] == 'set-default':
                self.default = args[-1]
            elif args[0] == 'status':
                text = WPCTL_STATUS
                if self.default == '54':
                    text = text.replace('*   44.', '    44.').replace('    54.', '*   54.')
                return 0, text, ''
            return 0, '', ''
        if prog == 'pactl':
            if args[0] == 'get-sink-volume':
                return 0, f"Volume: front-left: 29491 /  {self.level}% / -20.81 dB,   front-right: 29491 /  {self.level}% / -20.81 dB\n", ''
            if args[0] == 'get-sink-mute':
                return 0, f"Mute: {'yes' if self.muted else 'no'}\n", ''
            if args[0] == 'set-sink-volume':
                self.level = int(args[-1].rstrip('%'))
            elif args[0] == 'set-sink-mute':
                self.muted = args[-1] == '1'
            elif args[0] == 'get-default-sink':
                return 0, 'alsa_output.hdmi\n', ''
            elif args[0] == 'list':
                return 0, ("Sink #0\n\tState: IDLE\n\tName: alsa_output.analog\n\tDescription: Built-in Analog\n"
                           "Sink #1\n\tState: RUNNING\n\tName: alsa_output.hdmi\n\tDescription: HDMI Screen\n"), ''
            return 0, '', ''
        if prog == 'amixer':
            if args[0] == 'sget':
                return 0, f"  Mono: Playback 39 [{self.level}%] [-18.00dB] [{'off' if self.muted else 'on'}]\n", ''
            if args[-1] in ('mute', 'unmute'):
                self.muted = args[-1] == 'mute'
            else:
                self.level = int(args[-1].rstrip('%'))
            return 0, '', ''
        if prog == 'powershell':
            if 'Round' in text_in:
                return 0, f"{self.level} {int(self.muted)}\r\n", ''
            if '::Volume =' in text_in:
                self.level = int(round(float(text_in.strip().rsplit('=', 1)[1]) * 100))
            if '::Mute =' in text_in:
                self.muted = text_in.strip().endswith('$true')
            return 0, '', ''
        if prog == 'busctl':
            return 0, f's "{self.power}"\n', ''
        return 0, '', ''


@pytest.fixture
def box():
    b = Box()
    with patch.object(pc, '_sh', lambda cmd, timeout=6, text_in=None: b.sh(cmd, timeout, text_in)), \
         patch.object(pc.shutil, 'which', lambda n: b.which(n)), \
         patch.object(pc, '_windows', lambda: False), \
         patch.object(pc, '_can_see', lambda: True), \
         patch.object(pc, 'POWER_DELAY', 0):
        yield b


def run(cap, action, value='', cfg=CFG, call_tool=None):
    return pc.run(DEV, cap, action, value, cfg, None, call_tool)


# --- the declaration -----------------------------------------------------------

def test_it_is_a_core_driver_with_power_locked():
    import core.devices.registry as reg
    importlib.reload(reg)
    assert 'computer' in reg.CORE_DRIVERS
    assert reg.register_driver('computer', pc.SPEC, 'core', builtin=True)
    spec = reg.get_driver('computer')
    assert spec['capabilities'] == ['sound', 'screen', 'power', 'storage']
    assert spec['locked_by_default'] == ['power']             # she may not switch the user's machine off
    assert spec['uses_tools'] == ['get_screenshot']
    assert not [f for f in spec['config_schema'] if f.get('secret')]
    # a plugin may not hand itself another plugin's tools, nor take this name
    assert reg.register_driver('sneaky', dict(pc.SPEC, module='d.py', uses_tools=['ssh_run']), 'someplugin')
    assert reg.get_driver('sneaky')['uses_tools'] == []
    assert not reg.register_driver('computer', dict(pc.SPEC, module='d.py'), 'someplugin')
    importlib.reload(reg)


# --- sound ---------------------------------------------------------------------

def test_volume(box):
    assert run('sound', 'read') == ('Volume 45%.', True)
    assert run('sound', 'set', '40') == ('Volume 40%.', True)
    assert run('sound', 'set', ' 62.4% ') == ('Volume 62%.', True)
    assert run('sound', 'up') == ('Volume 67%.', True)
    assert run('sound', 'down', cfg=dict(CFG, step=10)) == ('Volume 57%.', True)
    assert run('sound', 'mute') == ('Volume 57%, muted.', True)
    assert run('sound', 'unmute') == ('Volume 57%.', True)
    assert ['wpctl', 'set-volume', '-l', '1.0', '@DEFAULT_AUDIO_SINK@', '40%'] in box.ran


def test_volume_stays_inside_what_it_may_be(box):
    box.level = 98
    assert run('sound', 'up') == ('Volume 100%.', True)                 # never past the top
    box.level = 2
    assert run('sound', 'down') == ('Volume 0%.', True)
    for wrong in ('loud', '140', '-5', '4 0'):
        text, ok = run('sound', 'set', wrong)
        assert not ok and text.startswith('The volume is a number from 0 to 100')
    assert box.level == 0                                                # a bad value moved nothing
    assert run('sound', 'set', '') == ('set: the value is the volume, 0 to 100. Example: 40', True)


def test_she_is_held_to_the_loudest_the_user_allows(box):
    quiet = dict(CFG, loudest=60)
    assert run('sound', 'set', '90', cfg=quiet) == \
        ('Volume 60%. 60% is the loudest I am allowed here.', True)
    box.level = 58
    assert run('sound', 'up', cfg=quiet)[0].startswith('Volume 60%. 60% is the loudest')
    assert run('sound', 'set', '30', cfg=quiet) == ('Volume 30%.', True)
    assert pc._limit({'loudest': 'x'}) == 100 and pc._limit({'loudest': 3}) == 10


def test_outputs(box):
    assert run('sound', 'outputs') == ('Outputs:\n'
                                       '  1. Built-in Audio Digital Stereo (IEC958)  (in use)\n'
                                       '  2. HDA NVidia Digital Stereo (HDMI)', True)
    assert run('sound', 'use', '2') == ('Sound now comes out of HDA NVidia Digital Stereo (HDMI). '
                                        'Volume 45%.', True)
    assert box.default == '54'
    assert run('sound', 'use', 'built-in')[1] is True and box.default == '44'     # part of a name
    before = len(box.ran)
    assert run('sound', 'use', '1')[1] is True                                     # already in use
    assert ['wpctl', 'set-default', '44'] not in box.ran[before:]
    text, ok = run('sound', 'use', 'stereo')
    assert not ok and text.startswith("'stereo' matches 2 outputs. Use its number.")
    assert run('sound', 'use', 'zzz')[0].startswith("No output matches 'zzz'. Outputs: 1. Built-in")
    assert run('sound', 'use', '')[1] is False


def test_each_way_of_setting_the_volume(box):
    for has, name in ((('pactl',), 'pactl'), (('amixer',), 'amixer')):
        box.has, box.level, box.muted = set(has), 45, False
        assert pc._sound().name == name
        assert run('sound', 'set', '30') == ('Volume 30%.', True)
        assert run('sound', 'mute') == ('Volume 30%, muted.', True)
        assert run('sound', 'unmute') == ('Volume 30%.', True)
    box.has = {'pactl'}
    assert run('sound', 'outputs') == ('Outputs:\n  1. Built-in Analog\n  2. HDMI Screen  (in use)', True)
    box.has = {'amixer'}
    assert run('sound', 'outputs') == ("This computer's outputs cannot be listed from here.", False)


def test_a_sound_program_that_is_installed_but_not_running_is_passed_over(box):
    box.has = {'wpctl', 'amixer'}
    real = box.sh
    box.sh = lambda cmd, timeout=6, text_in=None: \
        (1, '', 'Could not connect to PipeWire') if cmd[0] == 'wpctl' else real(cmd, timeout, text_in)
    assert pc._sound().name == 'amixer'
    box.has = set()
    text, ok = run('sound', 'read')
    assert not ok and text.startswith('This computer has no sound control I can use.')


def test_windows(box):
    box.has = {'powershell', 'shutdown'}
    with patch.object(pc, '_windows', lambda: True):
        assert pc._sound().name == 'windows'
        assert run('sound', 'set', '35') == ('Volume 35%.', True)
        assert run('sound', 'mute') == ('Volume 35%, muted.', True)
        told = pc.describe(DEV, CFG)
        assert 'outputs' not in told['sound']['actions'] and 'sleep' not in told['power']['actions']
        text, ok = run('power', 'restart')
        assert ok and 'in 30 seconds' in text and 'shutdown /a' in text
        assert ['shutdown', '/r', '/t', '30'] in box.ran
        assert run('power', 'sleep') == ('This computer has no power / sleep.', False)
    sent = [c for c in box.ran if c[0] == 'powershell']
    assert sent and all(c[1:4] == ['-NoProfile', '-NonInteractive', '-Command'] for c in sent)
    assert pc._WINDOWS_AUDIO.isascii()


# --- the screen ----------------------------------------------------------------

def test_the_screen_is_seen_through_the_screenshot_plugin(box):
    shot = ({'text': 'The screen.', 'images': [{'data': 'QUJD', 'media_type': 'image/png'}]}, True)
    door = MagicMock(return_value=shot)
    assert run('screen', 'look', call_tool=door) == shot
    door.assert_called_once_with('get_screenshot', {'source': 'local'})
    with patch.object(pc, '_can_see', lambda: False):
        assert 'screen' not in pc.describe(DEV, CFG)
        assert run('screen', 'look', call_tool=door) == \
            ('The screenshot plugin is off, so I cannot see this screen.', False)
    door.assert_called_once()


def test_a_core_driver_runs_only_the_tools_it_names():
    fm = MagicMock()
    fm.tool_plugin.side_effect = lambda name: {'get_screenshot': 'screenshot', 'ssh_run': 'ssh'}.get(name)
    fm.execute_function.return_value = ('done', True)
    with patch.object(engine, '_function_manager', lambda: fm):
        door = engine._call_tool_for('core', ['get_screenshot'])
        assert door('get_screenshot', {'source': 'local'}) == ('done', True)
        fm.execute_function.assert_called_once_with('get_screenshot', {'source': 'local'},
                                                    allowed_tools={'get_screenshot'}, with_success=True)
        assert door('ssh_run', {'command': 'id'})[1] is False            # not named in its SPEC
        assert door('gone_tool')[1] is False
        assert engine._call_tool_for('core', ['gone_tool'])('gone_tool') == \
            ("'gone_tool' is not there to be used. Its plugin may be switched off.", False)
        # a plugin's driver keeps the old rule: its own plugin's tools, nothing else
        assert engine._call_tool_for('ssh')('get_screenshot')[1] is False
        assert engine._call_tool_for('ssh', ['get_screenshot'])('get_screenshot')[1] is False
    assert fm.execute_function.call_count == 1


# --- power ---------------------------------------------------------------------

def test_power(box):
    started = []
    with patch.object(pc.threading, 'Thread',
                      lambda target, args, daemon, name: SimpleNamespace(start=lambda: started.append(args[0]))):
        text, ok = run('power', 'restart')
        assert ok and text == ('This computer will restart in 0 seconds. I run on it, so I go down '
                               'with it. I come back when it has started again.')
        assert 'Someone has to switch it on again' in run('power', 'shutdown')[0]
        assert run('power', 'sleep')[0].startswith('This computer goes to sleep')
        assert started == [['systemctl', 'reboot'], ['systemctl', 'poweroff'], ['systemctl', 'suspend']]
        assert run('power', 'explode') == ('This computer has no power / explode.', False)

        box.power = 'challenge'                               # the machine wants a password
        started.clear()
        assert run('power', 'restart') == \
            ('This computer will not let me restart it without a password.', False)
        assert started == []


def test_going_down_runs_the_command_once(box):
    pc._go_down(['systemctl', 'suspend'])
    assert box.ran[-1] == ['systemctl', 'suspend']


# --- what she reads ------------------------------------------------------------

def test_describe_and_status(box):
    told = pc.describe(DEV, CFG)
    assert list(told) == ['sound', 'screen', 'power']
    assert list(told['sound']['actions']) == ['set', 'read', 'up', 'down', 'mute', 'unmute', 'outputs', 'use']
    for cap in told.values():
        assert len(cap['help']) <= 120 and all(len(a['help']) <= 120 for a in cap['actions'].values())
    st = pc.status(DEV, CFG, None)
    assert st['online'] is True
    assert st['readings'] == {'volume': '45%', 'sound comes out of': 'Built-in Audio Digital Stereo (IEC958)',
                              'screen': 'can be seen'}
    box.has = set()
    st = pc.status(DEV, CFG, None)
    assert st['online'] is True and st['readings']['sound'].startswith('This computer has no sound control')


def test_every_example_in_the_help_really_runs(box):
    door = MagicMock(return_value=('The screen.', True))
    with patch.object(pc.threading, 'Thread', lambda **kw: SimpleNamespace(start=lambda: None)):
        for cap, info in pc.describe(DEV, CFG).items():
            for action, a in info['actions'].items():
                told, ok = run(cap, action, a['example'], call_tool=door)
                assert ok, (cap, action, told)
