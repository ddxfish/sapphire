"""The computer as a synth: fluidsynth with a General MIDI SoundFont, so a
plain MIDI keyboard (one that makes no sound of its own) has a voice, and
she can play along on the same instrument.

It shows up as a MIDI port named like any synth, so everything that plays to
a hardware synth can play to it.

It runs only while it has something to play for. The device manager's
presence (core/devices/presence.py) says which MIDI sources are here and
count, and keep() makes the synth match: on while one is here, linked to
exactly those, off a little after the last one left. fluidsynth links
nothing by itself.

Linux only. Needs fluidsynth and a SoundFont (Debian and Ubuntu: the packages
fluidsynth and fluid-soundfont-gm).
"""
import logging
import os
import re
import shutil
import signal
import subprocess
import threading
import time

logger = logging.getLogger(__name__)

NAME = 'Sapphire Synth'
FONTS = ('/usr/share/sounds/sf2/default-GM.sf2', '/usr/share/sounds/sf2/FluidR3_GM.sf2',
         '/usr/share/sounds/sf3/default-GM.sf3', '/usr/share/soundfonts/default.sf2',
         '/usr/share/soundfonts/FluidR3_GM.sf2', '/usr/share/sounds/sf2/TimGM6mb.sf2')
AUDIO = ('pipewire', 'pulseaudio', 'alsa')
READY_WAIT = 6             # seconds for its port to appear
LOUDEST = 1.5              # fluidsynth gain at 100 percent
GRACE = 20                 # seconds it stays on after the last source left
PLUMBING = ('System', 'Midi Through', NAME)             # never a source
OURS = ('aplaymidi', 'aseqdump', 'aseqsend', 'aconnect')   # her own playing and listening
SOCKETS = ('/dev/snd/by-id', '/dev/snd/by-path')        # hardware, by a name that holds across a replug
CARDS = '/proc/asound'
_CLIENT = re.compile(r"^client (\d+): '([^']*)' \[type=(\w+)(?:,card=(\d+))?")
_PORT = re.compile(r"^\s+(\d+) '([^']*)'")

# A keyboard's own volume slider sends "channel volume" (controller 7), and a
# pedal sends "expression" (11). At zero they silence the synth, and nothing
# on the screen says why: a user's keyboard sat at zero, and they did not know
# the slider did anything (2026-09-28). Both still work, from quiet to full. They
# can no longer reach silence. Everything else passes as it is.
QUIETEST = 48              # what a slider at the very bottom counts as, of 127
_FLOOR = f"router_par2 0 127 {(127 - QUIETEST) / 127:.2f} {QUIETEST}"
ROUTER = ('router_clear',
          'router_begin note', 'router_end',
          'router_begin cc', 'router_par1 0 6 1 0', 'router_end',
          'router_begin cc', 'router_par1 7 7 1 0', _FLOOR, 'router_end',
          'router_begin cc', 'router_par1 8 10 1 0', 'router_end',
          'router_begin cc', 'router_par1 11 11 1 0', _FLOOR, 'router_end',
          'router_begin cc', 'router_par1 12 127 1 0', 'router_end',
          'router_begin prog', 'router_end',
          'router_begin pbend', 'router_end',
          'router_begin cpress', 'router_end',
          'router_begin kpress', 'router_end')

_lock = threading.RLock()
_proc = None
_idle_since = None         # when the last source left, while it is still on
wanted = {}                # keyboard device id -> ids of the sources that are here and count for it
state = {'instrument': 1, 'loudness': 60, 'font': '', 'audio': '',
         'held_off': False}           # switched off by hand: a source that is here does not start it


class SynthError(Exception):
    """A reason fit to show as it is."""


def font(wanted=''):
    """The SoundFont to use: the one asked for, else the first that is installed."""
    wanted = str(wanted or '').strip()
    if wanted:
        path = os.path.expanduser(wanted)
        if os.path.isfile(path):
            return path
        raise SynthError(f"The SoundFont {wanted} is not there.")
    for path in FONTS:
        if os.path.isfile(path):
            return path
    raise SynthError("No SoundFont is installed, so the computer has no instrument sounds. "
                     "On Debian and Ubuntu the package is fluid-soundfont-gm.")


def missing():
    """What stops the computer from making the sound, or ''."""
    if not shutil.which('fluidsynth'):
        return "fluidsynth is not installed."
    if not shutil.which('aconnect'):
        return "alsa-utils is not installed."
    try:
        font()
    except SynthError as e:
        return str(e)
    return ''


def _ports():
    out = subprocess.run(['aconnect', '-l'], capture_output=True, text=True, timeout=5).stdout
    m = re.search(rf"^client (\d+): '{re.escape(NAME)}'", out, re.M)
    return (f"{m.group(1)}:0" if m else None), out


def port():
    """Its MIDI port like '128:0', or None when it is off."""
    return _ports()[0] if running() else None


def running():
    with _lock:
        return _proc is not None and _proc.poll() is None


def _linked():
    """(its port, the ports that play into it, the whole listing)."""
    ident, out = _ports()
    if not ident:
        return None, [], out
    block = re.search(rf"^client {ident.split(':')[0]}:.*?(?=^client |\Z)", out, re.M | re.S)
    found = re.search(r"Connected From: (.+)", block.group(0) if block else '')
    return ident, [src.strip().split('[')[0] for src in (found.group(1).split(',') if found else [])], out


def keyboards_in():
    """Names of what is connected into it right now."""
    _, linked, out = _linked()
    names = []
    for src in linked:
        m = re.search(rf"^client {src.split(':')[0]}: '([^']+)'", out, re.M)
        if m and m.group(1) not in PLUMBING and m.group(1) not in names:
            names.append(m.group(1))
    return names


# --- what is here ----------------------------------------------------------------

def _ident(name, card):
    """A name for a source that holds across a replug: the hardware's own, or
    the socket it sits in. Anything else goes by its MIDI name."""
    for folder in SOCKETS if card is not None else ():
        try:
            for entry in sorted(os.listdir(folder)):
                if os.path.basename(os.path.realpath(os.path.join(folder, entry))) == f'controlC{card}':
                    return entry
        except OSError:
            pass
    return f'name:{name}'


def sources():
    """Every MIDI source that is here right now: a keyboard on USB, a synth
    over Bluetooth, a program. -> [{'id', 'name', 'kind', 'ports'}]. The
    system's own plumbing and her own players are never one."""
    out = subprocess.run(['aconnect', '-i'], capture_output=True, text=True, timeout=5).stdout
    found, cur, seen = [], None, {}
    for line in out.splitlines():
        m = _CLIENT.match(line)
        if m:
            client, name, kind, card = m.group(1), m.group(2).strip(), m.group(3), m.group(4)
            cur = None
            if not name or name in PLUMBING or name in OURS or name.startswith('PipeWire'):
                continue
            ident = _ident(name, card if kind == 'kernel' else None)
            seen[ident] = seen.get(ident, 0) + 1
            if seen[ident] > 1:                  # two alike that cannot be told apart
                ident = f"{ident}#{seen[ident]}"
            usb = card is not None and os.path.exists(f'{CARDS}/card{card}/usbid')
            cur = {'id': ident, 'name': name, 'client': client, 'ports': [],
                   'kind': 'USB' if usb else 'sound card' if kind == 'kernel' else 'program'}
            found.append(cur)
            continue
        p = _PORT.match(line)
        if p and cur is not None:
            cur['ports'].append(f"{cur['client']}:{p.group(1)}")
            if cur['kind'] == 'program' and p.group(2).strip().endswith('Bluetooth'):
                cur['kind'] = 'Bluetooth'
    return [s for s in found if s['ports']]


def counting_ids():
    """Ids of the sources that count, for all keyboard devices together."""
    return set().union(*wanted.values()) if wanted else set()


def _counting():
    """The sources that are here and count for a keyboard device."""
    ids = counting_ids()
    return [s for s in sources() if s['id'] in ids] if ids else []


def _link():
    """Make its links match: a source that counts plays into it, one that does
    not is taken off. A link of anything else (her own player) is left alone."""
    ident, linked, _ = _linked()
    if not ident:
        return
    ids = counting_ids()
    for s in sources():
        for port in s['ports']:
            if s['id'] in ids and port not in linked:
                subprocess.run(['aconnect', port, ident], capture_output=True, text=True, timeout=5)
            elif s['id'] not in ids and port in linked:
                subprocess.run(['aconnect', '-d', port, ident], capture_output=True, text=True, timeout=5)


def hush():
    """End every note and lift every pedal, on every channel. A keyboard that
    is unplugged with a key or the pedal down never says "up". The instrument
    and the loudness stay as they are."""
    if not running():
        return
    for ch in range(16):
        for line in (f'cc {ch} 64 0', f'cc {ch} 66 0', f'cc {ch} 123 0'):
            _tell(line)


def _let_go():
    logger.info("[midi] a MIDI source left: every note is ended, every pedal lifted")
    hush()


def keep(arrived=False, left=False, busy=False, instrument=None, loudness=None, soundfont=''):
    """Presence's hand on the switch. Safe to call again and again: it also
    brings back a synth that fell over and a link that was cut.
    arrived / left: a source that counts came or went since the last call.
    busy: she is playing, so it stays on though no source is here."""
    global _idle_since
    with _lock:
        if arrived:
            state['held_off'] = False            # plugging one in is asking for sound
        if _counting() and not state['held_off']:
            _idle_since = None
            if not running():
                return start(instrument=instrument, loudness=loudness, soundfont=soundfont)
            if left:
                _let_go()
            return _link()
        if not running():
            return None
        if left:
            _let_go()
        _link()
        if busy:
            _idle_since = None
            return None
        _idle_since = _idle_since or time.monotonic()
        if time.monotonic() - _idle_since >= GRACE:
            logger.info(f"[midi] no MIDI source for {GRACE}s")
            stop(by_hand=False)
        return None


def _tell(line):
    """One line to fluidsynth's own command line."""
    with _lock:
        if not running():
            raise SynthError("The computer's sound is off.")
        try:
            _proc.stdin.write(line + '\n')
            _proc.stdin.flush()
        except (OSError, ValueError) as e:
            raise SynthError(f"The synth did not take the command ({type(e).__name__}).")


def _gain(percent):
    return max(0.0, min(100.0, float(percent))) / 100.0 * LOUDEST


def _strays():
    """Process ids of synths of OURS that are still running: the program is
    fluidsynth itself and one of its arguments is exactly our port name.
    Never a match on text: a shell or an editor whose command line merely
    mentions the synth must not be touched (it killed one, 2026-09-28)."""
    out = subprocess.run(['pgrep', '-x', 'fluidsynth'], capture_output=True, text=True, timeout=5).stdout
    found = []
    for pid in out.split():
        try:
            with open(f'/proc/{int(pid)}/cmdline', 'rb') as f:
                args = f.read().split(b'\0')
        except (OSError, ValueError):
            continue
        if NAME.encode() in args:
            found.append(int(pid))
    return found


def _sweep():
    """End a synth left over from before a plugin reload. It has no command
    line we still hold, so it cannot be steered."""
    try:
        for pid in _strays():
            os.kill(pid, signal.SIGTERM)
    except Exception as e:
        logger.warning(f"[midi] stray synth sweep failed: {e}")


def start(instrument=None, loudness=None, soundfont=''):
    """Switch the computer's sound on. Returns its port. Already on = its port."""
    global _proc
    with _lock:
        state['held_off'] = False
        if running():
            ident = _ports()[0]
            if ident:
                return ident
            stop(by_hand=False)
        why = missing()
        if why:
            raise SynthError(why)
        sf = font(soundfont)
        if instrument is not None:
            state['instrument'] = max(1, min(128, int(instrument)))
        if loudness is not None:
            state['loudness'] = max(0, min(100, int(float(loudness))))
        _sweep()
        problem = ''
        for audio in AUDIO:
            proc = subprocess.Popen(
                ['fluidsynth', '-a', audio, '-m', 'alsa_seq', '-q', '-g', f"{_gain(state['loudness']):.2f}",
                 '-o', 'midi.autoconnect=0', '-p', NAME, sf],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
            end = time.monotonic() + READY_WAIT
            while time.monotonic() < end and proc.poll() is None:
                ident = _ports()[0]
                if ident:
                    _proc = proc
                    state['font'], state['audio'] = sf, audio
                    for rule in ROUTER:
                        _tell(rule)
                    _tell(f"prog 0 {state['instrument'] - 1}")
                    _link()
                    logger.info(f"[midi] the computer's sound is on: port {ident}, {audio}, {os.path.basename(sf)}")
                    return ident
                time.sleep(0.1)
            if proc.poll() is None:
                proc.kill()
            problem = f"fluidsynth did not start with {audio}"
        raise SynthError(f"The computer's sound did not start ({problem}).")


def stop(by_hand=True):
    """Switch it off. True when it was on. by_hand: it stays off though a
    source is here, until it is switched on or one is plugged in."""
    global _proc, _idle_since
    with _lock:
        proc, _proc, _idle_since = _proc, None, None
        state['held_off'] = bool(by_hand)
    if proc is None or proc.poll() is not None:
        return False
    try:
        proc.stdin.write('quit\n')
        proc.stdin.flush()
        proc.wait(timeout=3)
    except Exception:
        proc.kill()
    logger.info("[midi] the computer's sound is off")
    return True


def instrument(number):
    """General MIDI program 1-128, for the keys and for her."""
    number = max(1, min(128, int(number)))
    _tell(f"prog 0 {number - 1}")
    state['instrument'] = number


def loudness(percent):
    percent = max(0, min(100, int(float(percent))))
    _tell(f"gain {_gain(percent):.2f}")
    state['loudness'] = percent
