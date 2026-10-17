# Hardware synth device driver (tmp/device-manager-plan.md). Declared in this
# plugin's manifest (capabilities.devices) and run by the device engine in core.
#
# It holds no MIDI code. Every action runs one of this plugin's own tools
# through call_tool, so a loop started by midi_play and a stop sent through
# device_action meet in the same place. Status reads the tools' own helpers
# from the module the function manager loaded - never a second copy.
#
# Which synth it is comes from its port name and the profiles in synths/:
# a profiled synth (the FM-1) has its own voice names and effects; any other
# plays with General MIDI names and no effects.
import importlib
import re

PLAY_OPTS = ('bpm', 'voice', 'velocity', 'listen', 'minutes')
_PAIR = re.compile(r'^([a-z_]+)=(\S+)$', re.I)

OFFLINE = "No synth is connected. Switch it on and link it over Bluetooth, or plug it in by USB."


def _tools():
    return importlib.import_module('plugins.midi.tools.midi_tools')


def _settings():
    from core.plugin_loader import plugin_loader
    return plugin_loader.get_plugin_settings('midi') or {}


def _split(value, keys=None):
    """'C4 E4 bpm=90' -> ('C4 E4', {'bpm': '90'}). keys=None takes any key."""
    words, opts = [], {}
    for tok in str(value or '').split():
        m = _PAIR.match(tok)
        if m and (keys is None or m.group(1).lower() in keys):
            opts[m.group(1).lower()] = m.group(2)
        else:
            words.append(tok)
    return ' '.join(words), opts


def _num(opts, key, whole=False):
    try:
        n = float(opts[key])
    except ValueError:
        raise ValueError(f"{key} has to be a number, not {opts[key]!r}")
    return int(n) if whole else n


def describe(device, config):
    return {
        'notes': {'label': 'Notes', 'help': 'play and hear notes', 'actions': {
            'play': {'help': 'notes with octave, :beats for length, R a rest', 'example': 'C4 E4 G4 C5:2 bpm=120',
                     'values': '<notes> [bpm=]'},
            'loop': {'help': 'repeat it as backing', 'example': 'C2 R C2 G2 minutes=2 bpm=90',
                     'values': '<notes> [minutes= bpm=]'},
            'listen': {'help': 'hear the keys for that long', 'example': '30', 'values': '[seconds]'},
            'stop': {'help': 'silence everything', 'example': ''},
        }},
        'sound': {'label': 'Sound', 'help': 'voice and effects', 'actions': {
            'voice': {'help': "pick its voice. 'list' names them, and the effects it has", 'example': 'list',
                      'values': '<name | number | list>'},
            'effect': {'help': 'switch an effect on or off, with its settings (synths I know, like the FM-1)',
                       'example': 'reverb on mix=60 decay=40', 'values': '<name> <on|off> [setting=...]'},
        }},
    }


def status(device, config, secrets):
    tools = _tools()
    midi, synths = tools.midi, tools.synths
    settings = _settings()
    try:
        found = midi.find_ports(tools._names(settings))
        keys = midi.find_ports(tools._keyboards(settings))
    except midi.MidiError as e:
        return {'online': False, 'detail': str(e)}
    readings = {}
    if keys:
        readings['keyboards'] = ', '.join(name for _, name in keys)
    if not found:
        return {'online': False, 'detail': OFFLINE, 'readings': readings}
    port, name = found[0]
    prof = synths.for_port(name)
    link = 'Bluetooth' if 'BLE' in name.upper() else 'USB'
    readings['synth'] = prof['name'] if prof else f'{name} (no profile: General MIDI voices, no effects)'
    sounding = midi.playing()
    if sounding:
        readings['playing'] = ', '.join(sounding)
    return {'online': True, 'detail': f"{name} over {link}, port {port}", 'readings': readings}


def _usage(action):
    tools = _tools()
    if action in ('play', 'loop'):
        extra = ("minutes=N is how long the loop runs, up to 15. " if action == 'loop' else
                 "listen=N hears the keys for N seconds once the phrase ends. ")
        return (f"{action}: the value is the music, then options. {tools._NOTATION}. "
                f"Options: bpm=120 voice=11 velocity=90. {extra}")
    if action == 'voice':
        return "voice: the value is a number 1-128, part of a name like piano, or list."
    return ("effect: the value is an effect, then on or off, then levels. 'voice list' names the "
            "effects this synth has. Example: reverb on mix=60 decay=40")


def run(device, capability, action, value, config, secrets, call_tool):
    try:
        if capability == 'notes':
            if action == 'stop':
                return call_tool('midi_stop', {})
            if action == 'listen':
                text, opts = _split(value, ('bpm',))
                args = {}
                if text:
                    args['seconds'] = _num({'seconds': text.split()[0]}, 'seconds')
                if 'bpm' in opts:
                    args['bpm'] = _num(opts, 'bpm')
                return call_tool('midi_listen', args)
            if action in ('play', 'loop'):
                notes, opts = _split(value, PLAY_OPTS)
                if not notes:
                    return _usage(action), True
                args = {'notes': notes}
                if 'bpm' in opts:
                    args['bpm'] = _num(opts, 'bpm')
                if 'voice' in opts:
                    args['voice'] = _num(opts, 'voice', whole=True)
                if 'velocity' in opts:
                    args['velocity'] = _num(opts, 'velocity', whole=True)
                if action == 'loop':
                    args['loop_minutes'] = _num(opts, 'minutes') if 'minutes' in opts else 1
                if 'listen' in opts:
                    args['then_listen'] = _num(opts, 'listen')
                return call_tool('midi_play', args)
        if capability == 'sound':
            if action == 'voice':
                want = str(value or '').strip()
                return call_tool('midi_sound', {'voice': want}) if want else (_usage(action), True)
            if action == 'effect':
                text, opts = _split(value)
                words = text.lower().split()
                if not words:
                    return _usage(action), True
                args = {'effect': words[0], 'on': 'off' not in words[1:]}
                if opts:
                    args['levels'] = {k: _num(opts, k) for k in opts}
                return call_tool('midi_sound', args)
    except ValueError as e:
        return str(e), False
    return f"The synth has no {capability} / {action}.", False
