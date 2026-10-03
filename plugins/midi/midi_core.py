"""MIDI over ALSA: find a port, play notation, capture the keys.

Notation, the same in both directions:  "C4 E4 G4 C5:2 | [C4 E4 G4]:4 R:1"
  token = note[:beats] | [note note ...][:beats] | R[:beats]   (default 1 beat)
  note  = letter + optional #/b + octave. C4 = middle C. '|' barlines are ignored.

Linux only. Needs alsa-utils: aconnect, aplaymidi, aseqsend, aseqdump.
A synth is found by its ALSA port NAME, so no Bluetooth address lives here.
Nothing here is about one make of synth: what a particular synth calls its
voices and how its effects are switched lives in synths.py (profiles).
Nothing in this file knows about Sapphire; the tools file does the talking.
"""
import logging
import math
import os
import re
import select
import shutil
import subprocess
import tempfile
import threading
import time

from midi_midifile import write_mid

logger = logging.getLogger(__name__)

NEEDED = ('aconnect', 'aplaymidi', 'aseqsend', 'aseqdump')
NOTE_CH = 0                     # notes go on channel 1
MAX_LOOP_MIN = 15
TMP_TAG = 'midi_sapphire_'      # our temp .mid files; stop_all() finds stray players by it
_PITCH = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}
_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
_EV = re.compile(r'Note (on|off)\s+(\d+), note (\d+)(?:, velocity (\d+))?')

_lock = threading.Lock()
_players = {}                   # slot ('phrase' | 'loop') -> {'proc', 'notes'}
state = {'bpm': 120.0}          # the last tempo she played at; listen's default grid


class MidiError(Exception):
    """A problem worth telling her in plain words."""


# -- the port ---------------------------------------------------------------

def missing_tools():
    return [t for t in NEEDED if not shutil.which(t)]


def _listing():
    miss = missing_tools()
    if miss:
        raise MidiError(f"This machine is missing {', '.join(miss)} (install alsa-utils). "
                       "The MIDI tools are Linux only.")
    return subprocess.run(['aconnect', '-l'], capture_output=True, text=True, timeout=5).stdout


def find_ports(names, listing=None):
    """Every name that is connected -> [('20:0', 'AKM320'), ...]. May be empty."""
    if listing is None:
        listing = _listing()
    found = []
    for name in names:
        m = re.search(rf"^client (\d+): '{re.escape(name)}'", listing, re.M)
        if m:
            found.append((f'{m.group(1)}:0', name))
    return found


def find_port(names, listing=None):
    """-> ('128:0', 'FM-1_BLE'). The first name that is connected wins."""
    found = find_ports(names, listing)
    if found:
        return found[0]
    raise MidiError("No synth is connected. One has to be switched on and linked over Bluetooth "
                   "(or plugged in by USB) on the machine I run on. "
                   f"I look for: {', '.join(names) or 'nothing — set the synth port names'}.")


def send(port, hexbytes):
    """Send raw MIDI now, e.g. send(port, '90 3C 64')."""
    out = subprocess.run(['aseqsend', '-v', '-p', port, hexbytes],
                         capture_output=True, text=True, timeout=10).stdout
    n = len(hexbytes.split())
    if f'Sent : {n} bytes' not in out:          # aseqsend exits 0 on every failure
        raise MidiError(f'The synth did not take the message ({out.strip()[:80]!r}).')


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
    """notation -> (events for write_mid, total beats)"""
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
    end to end, so one player process carries the whole thing."""
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

def _end(proc):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
        return True
    return False


def _stop_slot(port, slot):
    with _lock:
        p = _players.pop(slot, None)
    if not p or not _end(p['proc']):
        return False
    try:                                        # a killed player leaves its notes hanging
        send(port, ' '.join(f'8{NOTE_CH:X} {n:02X} 00' for n in sorted(p['notes'])))
    except Exception as e:
        logger.warning(f"[midi] note-offs after stopping the {slot} failed: {e}")
    return True


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
    _stop_slot(port, slot)
    path = os.path.join(tempfile.gettempdir(), f'{TMP_TAG}{slot}.mid')
    write_mid(path, ev, bpm=bpm, program=None if voice is None else int(voice) - 1)
    proc = subprocess.Popen(['aplaymidi', '-p', port, path],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with _lock:
        _players[slot] = {'proc': proc, 'notes': {e[2] for e in ev}}
        state['bpm'] = bpm
    pitches = [e[2] for e in ev]
    return {'slot': slot, 'notes': len(ev) // reps, 'reps': reps, 'beats': beats, 'bpm': bpm,
            'low': note_name(min(pitches)), 'high': note_name(max(pitches)),
            'seconds': beats * reps * 60.0 / bpm, 'proc': proc}


def playing():
    """Slots that are sounding right now."""
    with _lock:
        return sorted(s for s, p in _players.items() if p['proc'].poll() is None)


def stray_players():
    """Process ids of players of OURS that are still running: the program is
    aplaymidi itself and one of its arguments is one of our temp files.
    Never a match on text: a process whose command line merely mentions a
    player must not be touched."""
    out = subprocess.run(['pgrep', '-x', 'aplaymidi'], capture_output=True, text=True, timeout=5).stdout
    found = []
    for pid in out.split():
        try:
            with open(f'/proc/{int(pid)}/cmdline', 'rb') as f:
                args = f.read().split(b'\0')
        except (OSError, ValueError):
            continue
        if any(os.path.basename(a.decode('utf-8', 'replace')).startswith(TMP_TAG) for a in args[1:]):
            found.append(int(pid))
    return found


def stop_all(port=None):
    """End her playback and her loop, then silence every note. Also ends
    players left over from before a plugin reload (found by the temp file tag)."""
    with _lock:
        procs = [p['proc'] for p in _players.values()]
        _players.clear()
    for proc in procs:
        _end(proc)
    try:
        for pid in stray_players():
            os.kill(pid, 15)
    except Exception as e:
        logger.warning(f"[midi] stray player sweep failed: {e}")
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

def parse_dump_line(line):
    """One aseqdump line -> ('on' | 'off', note, velocity), or None."""
    m = _EV.search(line)
    if not m:
        return None
    kind, note, vel = m[1], int(m[3]), int(m[4] or 0)
    if kind == 'on' and vel == 0:               # a note-on at zero is a release
        kind = 'off'
    return kind, note, (vel if kind == 'on' else 0)


def capture(port, max_seconds=30, first_wait=60, quiet_end=5, cancel=None):
    """Record the keys. `port` may name several, comma separated ('128:0,20:0').
    Waits up to first_wait for the first note, then records
    up to max_seconds, ending early once nothing is held and quiet_end seconds
    pass. -> [(start_s, dur_s, note, velocity)], where 0 is the first note."""
    proc = subprocess.Popen(['aseqdump', '-p', port],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    fd = proc.stdout.fileno()
    began = time.monotonic()
    first = last = None
    held, notes, buf = {}, [], b''

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
            if not select.select([fd], [], [], 0.2)[0]:
                if proc.poll() is not None:
                    break
                continue
            chunk = os.read(fd, 4096)           # raw read: no hidden buffer to starve select
            if not chunk:
                break
            now = time.monotonic()
            buf += chunk
            *lines, buf = buf.split(b'\n')
            for raw in lines:
                ev = parse_dump_line(raw.decode('ascii', 'replace'))
                if not ev:
                    continue
                kind, note, vel = ev
                if first is None:
                    if kind != 'on':
                        continue
                    first = now
                last = now
                if kind == 'on':
                    if note in held:            # struck again before its release arrived
                        close(note, now)
                    held[note] = (now - first, vel)
                elif note in held:
                    close(note, now)
    finally:
        _end(proc)
        try:
            proc.stdout.close()
        except Exception:
            pass
    now = time.monotonic()
    for note in list(held):
        close(note, now)
    return sorted(notes)
