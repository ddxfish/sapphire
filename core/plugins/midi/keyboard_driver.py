# MIDI keyboard device driver (tmp/device-manager-plan.md). Declared in this
# plugin's manifest and run by the device engine in core.
#
# A plain MIDI keyboard makes no sound of its own. This device gives it one:
# the computer becomes the synth (softsynth.py), every MIDI source that counts
# plays through it, and she can play on the same instrument.
#
# It keeps presence: discover() is every MIDI source that is here, the
# device's filter says which count, and tend() has the synth match. So the
# synth runs only while it has something to play for.
#
# Playing and listening are this plugin's own tools, run through call_tool
# with on='computer'. The synth itself is one module, the one the tools hold.
import importlib
import logging
import os
import re
import select
import shutil
import subprocess

logger = logging.getLogger(__name__)

PLAY_OPTS = ('bpm', 'velocity', 'listen', 'minutes', 'instrument')
_PAIR = re.compile(r'^([a-z_]+)=(\S+)$', re.I)
HERE = 'computer'
CHANGES = (b'Port start', b'Port exit', b'Port subscribed', b'Port unsubscribed')

_problem = ''              # the last reason the synth did not start, said once


def _tools():
    return importlib.import_module('plugins.midi.tools.midi_tools')


def _settings():
    from core.plugin_loader import plugin_loader
    return plugin_loader.get_plugin_settings('midi') or {}


def _split(value, keys):
    words, opts = [], {}
    for tok in str(value or '').split():
        m = _PAIR.match(tok)
        if m and m.group(1).lower() in keys:
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


def _wanted(config):
    """(instrument number, its name, loudness) as the user set them."""
    tools = _tools()
    try:
        number, name = tools.songs.pick_instrument(str(config.get('instrument') or 'piano'))
    except tools.midi.MidiError:
        number, name = tools.songs.pick_instrument('piano')
    try:
        loud = max(0, min(100, int(float(config.get('loudness', 60)))))
    except (TypeError, ValueError):
        loud = 60
    return number, name, loud


def _switch_on(config):
    tools = _tools()
    number, name, loud = _wanted(config)
    fresh = not tools.synth.running()
    tools.synth.start(instrument=number if fresh else None, loudness=loud if fresh else None,
                      soundfont=str(config.get('soundfont') or ''))
    return tools.synth


def _instrument_name(tools, number):
    return next((k for k, v in tools.songs.INSTRUMENTS.items() if v == number), f"program {number}")


def validate(config):
    tools = _tools()
    want = str(config.get('instrument') or '').strip()
    if want:
        try:
            tools.songs.pick_instrument(want)
        except tools.midi.MidiError as e:
            return config, str(e)
    sf = str(config.get('soundfont') or '').strip()
    if sf:
        try:
            tools.synth.font(sf)
        except tools.synth.SynthError as e:
            return config, str(e)
    return config, ''


# --- presence ------------------------------------------------------------------

def discover(config):
    """Every MIDI source that is here right now."""
    return [{'id': s['id'], 'name': s['name'], 'kind': s['kind']} for s in _tools().synth.sources()]


def watch(changed, stopped):
    """The system says when a MIDI port comes, goes, or is linked. One quiet
    process reads that, so nothing is polled. Her own players come and go
    too: that costs one look, which finds nothing to do."""
    if not shutil.which('aseqdump'):
        logger.warning("[midi] aseqdump is not installed, so a keyboard that is plugged in is "
                       "found by the heartbeat, a little later")
        return stopped.wait()
    proc = subprocess.Popen(['aseqdump', '-p', '0:1'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    fd, buf = proc.stdout.fileno(), b''
    try:
        while not stopped.is_set():
            if not select.select([fd], [], [], 0.5)[0]:
                if proc.poll() is not None:
                    return
                continue
            chunk = os.read(fd, 4096)
            if not chunk:
                return
            buf += chunk
            *lines, buf = buf.split(b'\n')
            if any(word in line for line in lines for word in CHANGES):
                changed()
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        proc.stdout.close()


def tend(device, config, secrets, present, leaving):
    """The synth is one, shared by every keyboard device. Each device says
    what is here for it, and the synth plays for all of them together."""
    global _problem
    tools = _tools()
    synth = tools.synth
    number, _, loud = _wanted(config)
    with synth._lock:                            # two devices may be tended at once
        was = synth.counting_ids()
        if leaving:
            synth.wanted.pop(device['id'], None)
            if not synth.wanted:                 # the last one: nothing calls again
                synth.hush()
                synth.stop(by_hand=False)
                return
        else:
            synth.wanted[device['id']] = {t['id'] for t in present}
        now = synth.counting_ids()
        try:
            synth.keep(arrived=bool(now - was), left=bool(was - now), busy=bool(tools.midi.playing()),
                       instrument=number, loudness=loud, soundfont=str(config.get('soundfont') or ''))
            _problem = ''
        except synth.SynthError as e:
            if str(e) != _problem:
                _problem = str(e)
                logger.warning(f"[midi] {device['id']}: {e}")


def describe(device, config):
    return {
        'sound': {'label': 'Sound', 'help': "its instrument. This computer's synth makes the sound",
                  'actions': {
            'instrument': {'help': "pick its instrument. 'list' names all 128", 'example': 'organ',
                           'values': '<name | 1-128 | list>'},
            'loudness': {'help': "the instrument's own level. The computer's volume is on top",
                         'example': '60', 'values': '<0-100>'},
            'on': {'help': 'switch the instrument on. It comes on by itself when a keyboard is plugged in',
                   'example': ''},
            'off': {'help': 'switch it off. The keys go silent', 'example': ''},
        }},
        'notes': {'label': 'Notes', 'help': 'play that instrument yourself, and hear the keys', 'actions': {
            'play': {'help': 'notes with octave, :beats for length, R a rest', 'example': 'C4 E4 G4 C5:2 bpm=120',
                     'values': '<notes> [bpm=]'},
            'loop': {'help': 'repeat it as backing', 'example': 'C2 R C2 G2 minutes=2 bpm=90',
                     'values': '<notes> [minutes= bpm=]'},
            'listen': {'help': 'hear the keys for that long', 'example': '30', 'values': '[seconds]'},
            'stop': {'help': 'silence everything', 'example': ''},
        }},
    }


def status(device, config, secrets):
    tools = _tools()
    synth, midi = tools.synth, tools.midi
    why = synth.missing()
    if why:
        return {'online': False, 'detail': why}
    on = synth.running()
    # "off" must never read as "you cannot play": her playing switches it on
    # (she took it that way, 2026-09-28)
    readings = {'sound': 'on' if on else 'off, switched off by hand. Playing switches it on'
                if synth.state['held_off'] else
                'off. It comes on when a keyboard is plugged in, or when you play'}
    if on:
        readings['instrument'] = _instrument_name(tools, synth.state['instrument'])
        readings['loudness'] = f"{synth.state['loudness']}%"
    sounding = midi.playing()
    if sounding:
        readings['she is playing'] = ', '.join(sounding)
    return {'online': True, 'readings': readings, 'detail': 'Sound by this computer'}


def _usage(action):
    tools = _tools()
    extra = ("minutes=N is how long the loop runs, up to 15. " if action == 'loop' else
             "listen=N hears the keys for N seconds once the phrase ends. ")
    return (f"{action}: the value is the music, then options. {tools._NOTATION}. "
            f"Options: bpm=120 velocity=90 instrument=organ. {extra}")


def run(device, capability, action, value, config, secrets, call_tool):
    tools = _tools()
    synth = tools.synth
    try:
        if capability == 'sound':
            if action == 'off':
                was = synth.stop()
                return ("The sound is off. The keys are silent until it is switched on, or a "
                        "keyboard is plugged in." if was else "The sound was already off."), True
            if action == 'on':
                was = synth.running()
                _switch_on(config)
                heard = synth.keyboards_in()
                keys = f" {' and '.join(heard)} {'play' if len(heard) > 1 else 'plays'} through it." \
                    if heard else " No keyboard is here. It goes off again by itself."
                name = _instrument_name(tools, synth.state['instrument'])
                return (f"The sound {'was already' if was else 'is'} on: {name}, "
                        f"loudness {synth.state['loudness']}%.{keys}"), True
            if action == 'instrument':
                want = str(value or '').strip()
                if not want:
                    return "instrument: the value is a name, a number 1-128, or list. Example: organ", True
                if want.lower() == 'list':
                    return 'Instruments: ' + ', '.join(tools.songs.INSTRUMENTS) + \
                        '. Any General MIDI number 1-128 works too.', True
                number, name = tools.songs.pick_instrument(want)
                _switch_on(config).instrument(number)
                return f"Instrument {number} {name}, for the keys and for you.", True
            if action == 'loudness':
                if not str(value or '').strip():
                    return "loudness: the value is 0 to 100. Example: 60", True
                try:
                    level = float(str(value).strip().rstrip('%'))
                except ValueError:
                    return f"loudness is a number from 0 to 100, not '{value}'.", False
                if not 0 <= level <= 100:
                    return f"loudness is a number from 0 to 100, not {level:g}.", False
                _switch_on(config).loudness(level)
                return f"Loudness {synth.state['loudness']}%.", True
        if capability == 'notes':
            if action == 'stop':
                return call_tool('midi_stop', {'on': HERE})
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
                args = {'notes': notes, 'on': HERE}
                if 'bpm' in opts:
                    args['bpm'] = _num(opts, 'bpm')
                if 'velocity' in opts:
                    args['velocity'] = _num(opts, 'velocity', whole=True)
                if 'instrument' in opts:
                    number, _ = tools.songs.pick_instrument(opts['instrument'])
                    _switch_on(config).instrument(number)
                if action == 'loop':
                    args['loop_minutes'] = _num(opts, 'minutes') if 'minutes' in opts else 1
                if 'listen' in opts:
                    args['then_listen'] = _num(opts, 'listen')
                _switch_on(config)
                return call_tool('midi_play', args)
    except ValueError as e:
        return str(e), False
    except (synth.SynthError, tools.midi.MidiError) as e:
        return str(e), False
    return f"A keyboard has no {capability} / {action}.", False
