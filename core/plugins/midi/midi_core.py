"""MIDI: find a port, play notation, capture the keys. One library, every OS.

Notation, the same in both directions:  "C4 E4 G4 C5:2 | [C4 E4 G4]:4 R:1"
  token = note[:beats] | [note note ...][:beats] | R[:beats]   (default 1 beat)
  note  = letter + optional #/b + octave. C4 = middle C. '|' barlines are ignored.

Ports are python-rtmidi's: WinMM on Windows, the ALSA sequencer on Linux,
CoreMIDI on macOS. It is the plugin's optional package, installed from its
card in Settings > Plugins; no system package is needed. A synth is found by
its own name as the system reports it, so no address lives here. What a
particular synth calls its voices and how its effects are switched lives in
synths.py (profiles). Nothing in this file knows about Sapphire; the tools
file does the talking.

Windows allows one program at a time on a MIDI port, so a port is owned only
while something of ours sounds or listens: outputs are shared and counted,
inputs have one reader each that every listener subscribes to.
"""
import importlib
import logging
import math
import platform
import queue
import re
import threading
import time

logger = logging.getLogger(__name__)

CLIENT = 'Sapphire MIDI'        # what the system calls our own ports
PLUMBING = ('Midi Through', 'System', CLIENT)                  # the system's own, and ours: never a device
PROGRAMS = ('aplaymidi', 'aseqdump', 'aseqsend', 'aconnect', 'PipeWire')   # programs the computer's synth runs
NOTE_CH = 0                     # notes go on channel 1
MAX_LOOP_MIN = 15
TICK = 0.1                      # how often a waiting player looks at its stop flag
NOT_INSTALLED = ("The MIDI library (python-rtmidi) is not installed on this machine. "
                 "Settings > Plugins > MIDI has an Install button for it; no system package is needed.")
_API = {'Linux': 'API_LINUX_ALSA', 'Windows': 'API_WINDOWS_MM', 'Darwin': 'API_MACOSX_CORE'}
_PITCH = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}
_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

_lock = threading.Lock()        # players and outputs
_hub_lock = threading.Lock()    # readers and their subscribers
_players = {}                   # slot ('phrase' | 'loop') -> Player
_outputs = {}                   # port -> _Output, shared and counted
_readers = {}                   # port -> _Reader, one per open input
_backend = ''                   # 'alsa' | 'winmm' | 'coremidi' | ..., learned from the first listing
state = {'bpm': 120.0}          # the last tempo she played at; listen's default grid


class MidiError(Exception):
    """A problem worth telling her in plain words."""


# -- the library ------------------------------------------------------------

def _rtmidi():
    """The library, imported when first needed: an install from the plugin's
    card needs no restart."""
    try:
        return importlib.import_module('rtmidi')
    except ImportError:
        raise MidiError(NOT_INSTALLED)


def missing():
    """Why no MIDI can happen on this machine, or ''."""
    try:
        _rtmidi()
    except MidiError as e:
        return str(e)
    return ''


def _api(rt):
    """This OS's own MIDI system when the library has it, else its default.
    Never JACK by accident: the Linux build carries it too."""
    want = getattr(rt, _API.get(platform.system(), ''), None)
    return want if want in rt.get_compiled_api() else rt.API_UNSPECIFIED


def _new(direction):
    rt = _rtmidi()
    cls = rt.MidiOut if direction == 'out' else rt.MidiIn
    return cls(_api(rt), name=CLIENT)


def _close(obj):
    try:
        if obj.is_port_open():
            obj.close_port()
    except Exception:
        pass
    try:
        obj.delete()
    except Exception:
        pass


# -- ports and names --------------------------------------------------------

def ports(direction='out'):
    """Every port of that direction as the system lists them, right now.
    'out' is a destination she can play to; 'in' is a source she can hear."""
    global _backend
    obj = _new(direction)
    try:
        _backend = _rtmidi().get_api_name(obj.get_current_api())
        return list(obj.get_ports())
    finally:
        _close(obj)


def client_name(port):
    """A port as the system lists it -> the device's own name.
    ALSA   'FM-1_BLE:FM-1_BLE Bluetooth 129:0' -> 'FM-1_BLE'
    WinMM  'FM-1 0' -> 'FM-1' (Windows appends the port number)
    others as they are."""
    if _backend == 'alsa':
        return port.split(':', 1)[0].strip()
    if _backend == 'winmm':
        return re.sub(r' \d+$', '', port).strip()
    return port.strip()


def find_ports(names, direction='out', listing=None):
    """Every name that is here -> [(port, name), ...] in the order of names,
    the name in the caller's own spelling. May be empty. A name matches a
    device's own name exactly, whatever the case: 'FM-1' is not 'FM-1_BLE'.
    Windows dresses a name up ('2- FM-1' the second time it meets a device,
    'MIDIOUT2 (FM-1)' for a second port), so when nothing is equal to the
    name, the name as a whole word inside a device's name is still it."""
    if listing is None:
        listing = ports(direction)
    found = []
    for name in names:
        want = str(name).strip().lower()
        hit = next((p for p in listing if client_name(p).lower() == want), None)
        if hit is None and want:
            inside = re.compile(r'(?<![\w-])' + re.escape(want) + r'(?![\w-])', re.I)
            hit = next((p for p in listing if inside.search(client_name(p))), None)
        if hit is not None:
            found.append((hit, name))
    return found


def find_port(names, direction='out', listing=None):
    """-> (port, 'FM-1_BLE'). The first name that is here wins."""
    found = find_ports(names, direction, listing)
    if found:
        return found[0]
    raise MidiError("No synth is connected. One has to be switched on and linked over Bluetooth "
                   "(or plugged in by USB) on the machine I run on. "
                   f"I look for: {', '.join(names) or 'nothing: set the synth port names'}.")


def _device(name):
    return bool(name) and not name.startswith(PLUMBING) and not name.startswith(PROGRAMS)


def names(direction='out'):
    """The devices that are here, by the names the system gives them, without
    the system's plumbing and ours. What a Status tab shows when the synth
    she looks for is not among them."""
    out = []
    for port in ports(direction):
        n = client_name(port)
        if _device(n) and n not in out:
            out.append(n)
    return out


def sources():
    """Every MIDI source that is here right now: a keyboard on USB, a synth
    over Bluetooth, a program. -> [{'id', 'name', 'port'}]. Two alike cannot
    be told apart, so the second is 'name#2'."""
    found, seen = [], {}
    for port in ports('in'):
        name = client_name(port)
        if not _device(name):
            continue
        seen[name] = seen.get(name, 0) + 1
        found.append({'id': name if seen[name] == 1 else f'{name}#{seen[name]}', 'name': name, 'port': port})
    return found


def present(wanted, direction='in'):
    """True while every one of these ports is still listed."""
    return set(wanted) <= set(ports(direction))


def _open(obj, port):
    """Open `port` on a fresh MidiIn/MidiOut, or raise MidiError and free it."""
    listing = obj.get_ports()
    if port not in listing:
        _close(obj)
        raise MidiError(f"{client_name(port)} is not connected any more.")
    try:
        obj.open_port(listing.index(port))
    except Exception as e:
        _close(obj)
        busy = (" On Windows one program at a time may use a MIDI port: close whatever else has it."
                if _backend == 'winmm' else '')
        raise MidiError(f"{client_name(port)} did not open ({e}).{busy}")
    return obj


# -- outputs: shared, owned only while in use --------------------------------

class _Output:
    def __init__(self, port):
        self.port, self.users = port, 0
        self.out = _open(_new('out'), port)


class _Held:
    """`with _hold(port) as out:` borrows the one output for that port. It is
    opened by the first user and closed when the last one lets go."""

    def __init__(self, port):
        self.port, self.o = port, None

    def __enter__(self):
        with _lock:
            o = _outputs.get(self.port)
            if o is None:
                o = _outputs[self.port] = _Output(self.port)
            o.users += 1
            self.o = o
        return o.out

    def __exit__(self, *exc):
        with _lock:
            self.o.users -= 1
            if self.o.users <= 0 and _outputs.get(self.port) is self.o:
                del _outputs[self.port]
                _close(self.o.out)
        return False


def _hold(port):
    return _Held(port)


def _messages(hexbytes):
    """'B1 04 01 B1 07 3C' -> [[0xB1, 4, 1], [0xB1, 7, 0x3C]]: a new message
    at every status byte."""
    msgs = []
    for b in (int(x, 16) for x in str(hexbytes).split()):
        if b & 0x80 or not msgs:
            msgs.append([b])
        else:
            msgs[-1].append(b)
    return msgs


def send(port, hexbytes):
    """Send raw MIDI now, e.g. send(port, '90 3C 64'). Several messages may
    follow one another in the one string."""
    msgs = _messages(hexbytes)
    if not msgs:
        return
    with _hold(port) as out:
        for m in msgs:
            try:
                out.send_message(m)
            except Exception as e:
                raise MidiError(f"{client_name(port)} did not take the message ({e}).")


# -- notation ---------------------------------------------------------------

def note_num(name):
    m = re.fullmatch(r'([A-Ga-g])([#b]?)(-?\d)', name)
    if not m:
        raise MidiError(f'Bad note {name!r}. Notes look like C4, F#3, Bb5.')
    n = _PITCH[m[1].upper()] + {'#': 1, 'b': -1, '': 0}[m[2]] + 12 * (int(m[3]) + 1)
    if not 0 <= n <= 127:
        raise MidiError(f'Note {name!r} is outside the MIDI range.')
    return n


def note_name(n):
    return f'{_NAMES[n % 12]}{n // 12 - 1}'


def parse(text, vel=90):
    """notation -> (events (start_beat, dur_beats, note, vel, channel), total beats)"""
    ev, t = [], 0.0
    for tok in re.findall(r'\[[^\]]*\]\S*|\S+', str(text).replace('|', ' ')):
        body, _, beats = tok.rpartition(':') if ':' in tok else (tok, '', '')
        try:
            dur = float(beats) if beats else 1.0
        except ValueError:
            raise MidiError(f'Bad length in {tok!r}. Lengths are beats, like C4:2 or R:0.5.')
        if dur <= 0:
            raise MidiError(f'Length must be above zero in {tok!r}.')
        if body.upper() != 'R':
            for n in body.strip('[]').replace(',', ' ').split():
                ev.append((t, dur * 0.95, note_num(n), vel, NOTE_CH))
        t += dur
    return ev, t


def build_events(text, velocity=90, loop_seconds=0, bpm=120):
    """-> (events, phrase beats, repeats). A loop is the phrase written out
    end to end, so one player carries the whole thing."""
    ev, beats = parse(text, velocity)
    if not ev or beats <= 0:
        raise MidiError('There are no notes in that. Example: C4 E4 G4 [C4 E4 G4]:2')
    reps = 1
    if loop_seconds > 0:
        loop_seconds = min(float(loop_seconds), MAX_LOOP_MIN * 60)
        reps = max(1, math.ceil(loop_seconds / (beats * 60.0 / bpm)))
        ev = [(s + r * beats, d, n, v, ch) for r in range(reps) for (s, d, n, v, ch) in ev]
    return ev, beats, reps


def _beats(b):
    return '' if abs(b - 1.0) < 1e-9 else f':{b:g}'


def _tok(nums, beats):
    names = [note_name(n) for n in sorted(set(nums))]
    body = names[0] if len(names) == 1 else '[' + ' '.join(names) + ']'
    return body + _beats(beats)


def to_notation(notes, bpm=120, grid=0.25, chord_s=0.06):
    """Captured notes [(start_s, dur_s, note, vel)] -> notation on a bpm grid.
    Keys struck within chord_s of each other read as one chord. A key let go
    more than a beat before the next one reads as the note plus a rest."""
    if not notes:
        return ''
    spb = 60.0 / float(bpm)
    groups = []                                 # [start, [notes], latest end]
    for s, d, n, _ in sorted(notes):
        if groups and s - groups[-1][0] <= chord_s:
            groups[-1][1].append(n)
            groups[-1][2] = max(groups[-1][2], s + d)
        else:
            groups.append([s, [n], s + d])

    def q(seconds):
        return max(grid, round(seconds / spb / grid) * grid)

    out = []
    for i, (s, nums, end) in enumerate(groups):
        held = end - s
        gap = groups[i + 1][0] - s if i + 1 < len(groups) else held
        if gap - held > spb:
            out.append(_tok(nums, q(held)))
            rest = round((gap - held) / spb / grid) * grid
            if rest >= grid:
                out.append('R' + _beats(rest))
        else:
            out.append(_tok(nums, q(gap)))
    return ' '.join(out)


def heard_text(notes, bpm=120, waited=60):
    """What she is handed when a listen finishes."""
    if not notes:
        return (f"[Keys: nothing was played in the {waited:g}s you were listening. "
                "This is the instrument reporting, not typed text.]")
    length = max(s + d for s, d, _, _ in notes)
    vel = sum(v for _, _, _, v in notes) / len(notes)
    touch = 'soft' if vel < 55 else 'hard' if vel > 100 else 'medium'
    lo, hi = min(n for _, _, n, _ in notes), max(n for _, _, n, _ in notes)
    return (f"[Keys: {len(notes)} notes over {length:.1f}s were just played for you on the keyboard. "
            f"This is what you heard, not typed text. Written on a {float(bpm):g} bpm grid, "
            "timing rounded to 16th notes.]\n"
            f"{to_notation(notes, bpm)}\n"
            f"Range {note_name(lo)} to {note_name(hi)}. Touch: {touch} (average velocity {vel:.0f} of 127).")


# -- her playing ------------------------------------------------------------

def _schedule(ev, bpm, program=None):
    """events -> [(seconds, message)] in time order; at one time, note-offs go
    before note-ons and a program change before everything."""
    spb = 60.0 / float(bpm)
    msgs = []
    if program is not None:
        msgs.append((0.0, 0, [0xC0 | NOTE_CH, int(program)]))
    for start, dur, note, vel, ch in ev:
        msgs.append((start * spb, 2, [0x90 | ch, note, vel]))
        msgs.append(((start + dur) * spb, 1, [0x80 | ch, note, 0]))
    msgs.sort(key=lambda m: (m[0], m[1]))
    return [(t, m) for t, _, m in msgs]


def _wait_until(t, stop):
    """Sleep until monotonic time t. False if `stop` was set first. The last
    stretch is a plain sleep, which keeps its time on Windows where an
    Event's wait rounds to the system timer."""
    while True:
        left = t - time.monotonic()
        if left <= 0:
            return True
        if left > 0.02:
            if stop.wait(min(left - 0.015, TICK)):
                return False
        else:
            time.sleep(left)


class Player:
    """One phrase or loop, played by a thread of its own on a port it holds
    for the duration. `done` is set when it ends, however it ends."""

    def __init__(self, slot, port, msgs, notes):
        self.slot, self.port, self.msgs, self.notes = slot, port, msgs, notes
        self.stop, self.done = threading.Event(), threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name=f'midi-{slot}')

    def start(self):
        self.thread.start()
        return self

    def alive(self):
        return self.thread.is_alive()

    def _run(self):
        held = set()
        try:
            with _hold(self.port) as out:
                began = time.monotonic()
                for at, msg in self.msgs:
                    if not _wait_until(began + at, self.stop):
                        break
                    out.send_message(msg)
                    kind = msg[0] & 0xF0
                    if kind == 0x90 and msg[2]:
                        held.add(msg[1])
                    elif kind in (0x80, 0x90):
                        held.discard(msg[1])
                for n in sorted(held):              # ended early: nothing rings on
                    out.send_message([0x80 | NOTE_CH, n, 0])
        except Exception as e:
            logger.warning(f"[midi] the {self.slot} player ended: {e}")
        finally:
            self.done.set()

    def end(self):
        """Stop it and wait a moment for its note-offs. True if it was playing."""
        was = self.alive()
        self.stop.set()
        if was:
            self.thread.join(timeout=2)
        return was


def _stop_slot(slot):
    with _lock:
        p = _players.pop(slot, None)
    return bool(p and p.end())


def play(port, text, bpm=120, voice=None, velocity=90, loop_minutes=0):
    """Start playing in the background. A loop and a phrase are separate
    players, so she can play a phrase over her own loop. A new loop replaces
    the old loop, a new phrase replaces the old phrase."""
    bpm = float(bpm)
    if not 20 <= bpm <= 400:
        raise MidiError('bpm has to be between 20 and 400.')
    if voice is not None and not 1 <= int(voice) <= 128:
        raise MidiError('voice is a number from 1 to 128.')
    velocity = max(1, min(127, int(velocity)))
    loop_minutes = max(0.0, float(loop_minutes or 0))
    ev, beats, reps = build_events(text, velocity, loop_minutes * 60, bpm)
    slot = 'loop' if loop_minutes > 0 else 'phrase'
    _stop_slot(slot)
    msgs = _schedule(ev, bpm, None if voice is None else int(voice) - 1)
    player = Player(slot, port, msgs, {e[2] for e in ev})
    with _lock:
        _players[slot] = player
        state['bpm'] = bpm
    player.start()
    pitches = [e[2] for e in ev]
    return {'slot': slot, 'notes': len(ev) // reps, 'reps': reps, 'beats': beats, 'bpm': bpm,
            'low': note_name(min(pitches)), 'high': note_name(max(pitches)),
            'seconds': beats * reps * 60.0 / bpm, 'player': player}


def playing():
    """Slots that are sounding right now."""
    with _lock:
        return sorted(s for s, p in _players.items() if p.alive())


def stop_all(port=None):
    """End her phrase and her loop (each lets go of its own notes), then
    silence every note on `port`."""
    with _lock:
        players = list(_players.values())
        _players.clear()
    for p in players:
        p.end()
    if port:
        send(port, ' '.join(f'8{NOTE_CH:X} {n:02X} 00' for n in range(128)))


# -- the sound --------------------------------------------------------------

def pick_voice(names, want):
    """'11', 11, or part of a name -> (number, name, other matching numbers),
    against a synth's own voice list (a profile's, or General MIDI's)."""
    s = str(want).strip()
    if s.isdigit():
        n = int(s)
        if not 1 <= n <= len(names):
            raise MidiError(f'voice is a number from 1 to {len(names)}.')
        return n, names[n - 1], []
    hits = [i + 1 for i, name in enumerate(names) if s.lower() in name.lower()]
    if not hits:
        raise MidiError(f'No voice has {s!r} in its name. The voices:\n'
                       + '\n'.join(f'{i + 1} {name}' for i, name in enumerate(names)))
    return hits[0], names[hits[0] - 1], hits[1:]


def program(port, n):
    """Program change: voice n (1-128) on the note channel. Plain MIDI."""
    send(port, f'C{NOTE_CH:X} {int(n) - 1:02X}')


def fx(port, effects, channel, effect, on=True, levels=None):
    """Switch one of a synth's effects, as its profile maps them:
    effects = {name: {'switch': cc, 'levels': {level: [cc, max]}}} on `channel`.
    fx(port, prof['effects'], prof['fx_channel'], 'reverb', True, {'mix': 60}).
    Returns the levels that were set."""
    if effect not in effects:
        raise MidiError(f"Unknown effect {effect!r}. Effects: {', '.join(effects)}.")
    table = effects[effect]
    msgs = [f'B{channel:X} {int(table["switch"]):02X} {1 if on else 0:02X}']
    done = {}
    for k, v in (levels or {}).items():
        if k not in table['levels']:
            raise MidiError(f"{effect} has no level {k!r}. It has: {', '.join(table['levels'])}.")
        cc, mx = table['levels'][k]
        done[k] = max(0, min(int(mx), int(float(v))))
        msgs.append(f'B{channel:X} {int(cc):02X} {done[k]:02X}')
    send(port, ' '.join(msgs))
    return done


# -- listening to the keys --------------------------------------------------

def parse_message(msg):
    """One MIDI message -> ('on' | 'off', note, velocity), or None for
    anything that is not a key. A note-on at zero is a release."""
    if len(msg) < 3:
        return None
    kind = msg[0] & 0xF0
    if kind == 0x90 and msg[2]:
        return 'on', msg[1], msg[2]
    if kind in (0x80, 0x90):
        return 'off', msg[1], 0
    return None


class _Reader:
    """One open input, shared by everyone listening to it. The library's own
    thread calls _on for every message; it stamps the time and hands the key
    to each subscriber's queue."""

    def __init__(self, port):
        self.port, self.subs = port, set()
        self.obj = _open(_new('in'), port)
        self.obj.set_callback(self._on)

    def _on(self, event, _data=None):
        ev = parse_message(event[0])
        if not ev:
            return
        t = time.monotonic()
        with _hub_lock:
            subs = list(self.subs)
        for q in subs:
            q.put((t,) + ev)

    def close(self):
        try:
            self.obj.cancel_callback()
        except Exception:
            pass
        _close(self.obj)


class Subscription:
    """The keys of one or more ports, as a queue of (t, 'on' | 'off', note,
    velocity), t in time.monotonic() seconds. close() when done: the last
    subscriber of a port closes it."""

    def __init__(self, ports):
        self.q, self.ports = queue.Queue(), list(ports)
        try:
            with _hub_lock:
                for port in self.ports:
                    r = _readers.get(port)
                    if r is None:
                        r = _readers[port] = _Reader(port)
                    r.subs.add(self.q)
        except MidiError:
            self.close()
            raise

    def get(self, timeout=0.2):
        """The next key, or queue.Empty after `timeout` seconds."""
        return self.q.get(timeout=timeout)

    def close(self):
        with _hub_lock:
            for port in self.ports:
                r = _readers.get(port)
                if r and self.q in r.subs:
                    r.subs.discard(self.q)
                    if not r.subs:
                        del _readers[port]
                        r.close()


def subscribe(ports):
    """Hear the keys on these ports (a list, or one). -> Subscription."""
    return Subscription([ports] if isinstance(ports, str) else ports)


def capture(ports, max_seconds=30, first_wait=60, quiet_end=5, cancel=None):
    """Record the keys on `ports` (a list, or one). Waits up to first_wait
    for the first note, then records up to max_seconds, ending early once
    nothing is held and quiet_end seconds pass.
    -> [(start_s, dur_s, note, velocity)], where 0 is the first note."""
    sub = subscribe(ports)
    began = time.monotonic()
    first = last = None
    held, notes = {}, []

    def close(note, now):
        s, v = held.pop(note)
        notes.append((s, max(0.01, now - first - s), note, v))

    try:
        while not (cancel is not None and cancel.is_set()):
            now = time.monotonic()
            if first is None:
                if now - began > first_wait:
                    break
            elif now - first > max_seconds or (not held and now - last > quiet_end):
                break
            try:
                t, kind, note, vel = sub.get(timeout=0.2)
            except queue.Empty:
                continue
            if first is None:
                if kind != 'on':
                    continue
                first = t
            last = t
            if kind == 'on':
                if note in held:                # struck again before its release arrived
                    close(note, t)
                held[note] = (t - first, vel)
            elif note in held:
                close(note, t)
    finally:
        sub.close()
    now = time.monotonic()
    for note in list(held):
        close(note, now)
    return sorted(notes)


# -- leaving ----------------------------------------------------------------

def shutdown():
    """The plugin is going away: end every player, close every port. A
    listener still waiting gets nothing more and ends on its own timers."""
    stop_all()
    with _hub_lock:
        readers = list(_readers.values())
        _readers.clear()
    for r in readers:
        r.close()
    with _lock:
        outs = list(_outputs.values())
        _outputs.clear()
    for o in outs:
        _close(o.out)
