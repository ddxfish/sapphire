"""Songs she writes, kept as files: a .mid always, an .mp3 when this machine
can render one (fluidsynth with a SoundFont, plus ffmpeg). Nothing here needs
a synth. The rendered sound is a General MIDI instrument, whatever is in the room.
"""
import json
import logging
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import midi_core as midi
from midi_midifile import write_mid

logger = logging.getLogger(__name__)

MAX_MINUTES = 10
TAIL_SECONDS = 2.0              # quiet after the last note, so it can ring out
URL_BASE = '/api/plugin/midi/song/'
_FILE = re.compile(r'^[0-9a-f]{10}\.(mid|mp3)$')
INSTRUMENTS = {                 # General MIDI programs, numbered 1-128
    'piano': 1, 'bright piano': 2, 'electric piano': 5, 'fm piano': 6, 'harpsichord': 7, 'celesta': 9,
    'glockenspiel': 10, 'music box': 11, 'vibraphone': 12, 'marimba': 13, 'organ': 20,
    'accordion': 22, 'guitar': 25, 'electric guitar': 28, 'bass': 34, 'violin': 41,
    'cello': 43, 'harp': 47, 'strings': 49, 'choir': 53, 'trumpet': 57, 'sax': 66,
    'oboe': 69, 'clarinet': 72, 'flute': 74, 'pan flute': 76, 'synth lead': 81,
    'warm pad': 90, 'sitar': 105, 'banjo': 106, 'kalimba': 109, 'steel drums': 115,
}


def song_dir():
    """Where songs live. Under plugin_state with the plugin's prefix, so
    backups carry it and an uninstall cleans it."""
    from core.plugin_loader import PROJECT_ROOT
    return PROJECT_ROOT / 'user' / 'plugin_state' / 'midi_songs'


def pick_instrument(want):
    """None, a name, or a General MIDI number 1-128 -> (number, name)"""
    s = str(want or '').strip().lower()
    if not s:
        return 1, 'piano'
    if s.isdigit():
        n = int(s)
        if not 1 <= n <= 128:
            raise midi.MidiError('An instrument number is a General MIDI program from 1 to 128.')
        return n, next((k for k, v in INSTRUMENTS.items() if v == n), f'program {n}')
    if s in INSTRUMENTS:
        return INSTRUMENTS[s], s
    hits = [k for k in INSTRUMENTS if s in k or k in s]
    if hits:
        return INSTRUMENTS[hits[0]], hits[0]
    raise midi.MidiError(f"No instrument called {s!r}. Instruments: {', '.join(INSTRUMENTS)}. "
                       "A General MIDI number 1-128 works too.")


def slug(title):
    return re.sub(r'[^A-Za-z0-9]+', '-', str(title or '')).strip('-').lower()[:60] or 'song'


def renderers_missing():
    return [t for t in ('fluidsynth', 'ffmpeg') if not shutil.which(t)]


def render_mp3(mid, mp3):
    """-> None once the mp3 is written, else the reason in plain words."""
    miss = renderers_missing()
    if miss:
        return f"this machine is missing {' and '.join(miss)}"
    wav = Path(tempfile.gettempdir()) / f'{midi.TMP_TAG}{Path(mp3).stem}.wav'
    try:
        subprocess.run(['fluidsynth', '-ni', '-q', '-F', str(wav), '-r', '44100', str(mid)],
                       capture_output=True, text=True, timeout=180)
        if not wav.exists() or wav.stat().st_size < 1000:
            return 'fluidsynth could not render it (is a SoundFont installed?)'
        # fluidsynth renders quiet (gain 0.2); loudnorm brings it to a normal level
        r = subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', str(wav),
                            '-af', 'loudnorm=I=-16:TP=-1.5:LRA=11', '-ar', '44100',
                            '-codec:a', 'libmp3lame', '-q:a', '4', str(mp3)],
                           capture_output=True, text=True, timeout=180)
        if r.returncode != 0 or not Path(mp3).exists():
            return f'ffmpeg could not encode it ({r.stderr.strip()[:80]})'
        return None
    except subprocess.TimeoutExpired:
        return 'rendering took too long'
    finally:
        wav.unlink(missing_ok=True)


def save(folder, title, notes, bpm=120, instrument=None, velocity=90, render=render_mp3):
    """Write a song from notation. -> its meta dict (also saved as <id>.json)."""
    bpm = float(bpm)
    if not 20 <= bpm <= 400:
        raise midi.MidiError('bpm has to be between 20 and 400.')
    number, iname = pick_instrument(instrument)
    ev, beats, _ = midi.build_events(notes, max(1, min(127, int(velocity))))
    return _write(folder, title, ev, beats, bpm, number, iname, notes, 'song', render)


def save_take(folder, title, played, bpm=120, instrument='fm piano', render=render_mp3):
    """Write a take someone played. `played` is what capture() returns,
    [(start_s, dur_s, note, velocity)], and is written with its own timing and
    touch, not the rounded notation. -> meta dict, or None when nothing was played."""
    if not played:
        return None
    bpm = float(bpm)
    number, iname = pick_instrument(instrument)
    per_s = bpm / 60.0
    ev = [(s * per_s, max(0.02, d * per_s), n, max(1, min(127, int(v))), midi.NOTE_CH)
          for s, d, n, v in played]
    beats = max(s + d for s, d, *_ in ev)
    return _write(folder, title, ev, beats, bpm, number, iname,
                  midi.to_notation(played, bpm), 'take', render)


def _write(folder, title, ev, beats, bpm, number, iname, notation, kind, render):
    title = ' '.join(str(title or '').split())[:80] or 'Untitled'
    seconds = beats * 60.0 / bpm
    if seconds > MAX_MINUTES * 60:
        raise midi.MidiError(f'That is {seconds / 60:.1f} minutes. A saved song can be up to {MAX_MINUTES}.')
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    sid = secrets.token_hex(5)
    mid = folder / f'{sid}.mid'
    write_mid(str(mid), ev, bpm=bpm, program=number - 1, tail=TAIL_SECONDS * bpm / 60.0)
    problem = render(mid, folder / f'{sid}.mp3')
    if problem:
        logger.warning(f"[midi] song {sid} saved without audio: {problem}")
    pitches = [e[2] for e in ev]
    meta = {'id': sid, 'kind': kind, 'title': title, 'notes': notation, 'bpm': bpm, 'instrument': iname,
            'note_count': len(ev), 'beats': round(beats, 2), 'seconds': round(seconds, 1),
            'low': midi.note_name(min(pitches)), 'high': midi.note_name(max(pitches)),
            'audio': not problem, 'audio_problem': problem, 'created': int(time.time())}
    (folder / f'{sid}.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')
    return meta


def find(folder, name):
    """A served file name -> its path, or None. Only <10 hex>.mid|.mp3 passes,
    so nothing outside the song folder can be asked for."""
    if not _FILE.match(str(name or '')):
        return None
    p = Path(folder) / str(name)
    return p if p.is_file() else None


def meta(folder, sid):
    try:
        return json.loads((Path(folder) / f'{sid}.json').read_text(encoding='utf-8'))
    except Exception:
        return {}


def files_marker(m):
    """The chat's player + download row for this song (core/attachments.py).
    '' on a core that has no attachments row yet: the links still work."""
    try:
        from core import attachments
    except ImportError:
        return ''
    name = slug(m['title'])
    items = [{'url': f"{URL_BASE}{m['id']}.mp3", 'name': f'{name}.mp3'}] if m.get('audio') else []
    items.append({'url': f"{URL_BASE}{m['id']}.mid", 'name': f'{name}.mid'})
    return attachments.marker(m['title'], items)


def links(m):
    """Markdown links the chat renders as clickable: play, mp3, midi."""
    name = re.sub(r'[\[\]()]', '', m['title'])
    out = []
    if m.get('audio'):
        out.append(f"[Play {name}]({URL_BASE}{m['id']}.mp3)")
        out.append(f"[Download MP3]({URL_BASE}{m['id']}.mp3?dl=1)")
    out.append(f"[Download MIDI]({URL_BASE}{m['id']}.mid)")
    return out
