# store.py - projects and clips on disk. A clip is <id>.wav (16 kHz mono int16) beside <id>.json, the sidecar:
#   {id, collection, source, seconds, peak, rms, created, ...tags (device, style, voice, engine, speed, text), qa}
# Writers append nothing to shared indexes: counting is a scandir, a page is the newest files with their sidecars,
# so 50k clips in a folder stay cheap and nothing corrupts when a job dies.
import io
import json
import math
import os
import re
import secrets
import shutil
import time
import zipfile
from pathlib import Path

import numpy as np

from . import paths
from .paths import write_json
from .paths import COLLECTIONS, SAMPLE_RATE, collection_dir, project_dir, projects_dir, slug_of

AUDIO_EXT = ('.wav', '.flac', '.ogg', '.mp3', '.aiff', '.aif')
MIN_SECONDS = 0.25
MAX_UPLOAD_SECONDS = 60 * 60     # ambient recordings can be long; a clip longer than an hour is a mistake


# --- audio ---------------------------------------------------------------------------------------

def decode(data, name=''):
    """Bytes or a path -> (float32 mono at its own rate, rate). soundfile reads wav, flac, ogg and (libsndfile >= 1.1) mp3."""
    import soundfile as sf
    src = io.BytesIO(data) if isinstance(data, (bytes, bytearray)) else str(data)
    audio, sr = sf.read(src, dtype='float32', always_2d=True)
    return audio.mean(axis=1), int(sr)


def to_16k(audio, sr):
    if sr == SAMPLE_RATE:
        return audio.astype(np.float32, copy=False)
    try:
        from scipy.signal import resample_poly
    except ImportError:                           # Sapphire's own env has numpy but no scipy: the Record tab must still work
        return _to_16k_np(audio, sr)
    g = math.gcd(int(sr), SAMPLE_RATE)
    return resample_poly(audio, SAMPLE_RATE // g, int(sr) // g).astype(np.float32)


def _to_16k_np(audio, sr, taps=95):
    """Windowed-sinc low-pass at the new Nyquist, then linear interpolation. Not scipy's polyphase, but clean enough
    for takes that go on to be rated and trained; the jobs' own env has scipy for everything heavy."""
    audio = np.asarray(audio, dtype=np.float32)
    if sr > SAMPLE_RATE and len(audio) > taps:
        fc = 0.5 * SAMPLE_RATE / sr
        n = np.arange(taps) - (taps - 1) / 2
        h = 2 * fc * np.sinc(2 * fc * n) * np.hamming(taps)
        audio = np.convolve(audio, (h / h.sum()).astype(np.float32), mode='same')
    n_out = max(1, int(round(len(audio) * SAMPLE_RATE / sr)))
    return np.interp(np.linspace(0, len(audio) - 1, n_out), np.arange(len(audio)), audio).astype(np.float32)


def trim(audio, thresh_db=-40.0, pad_ms=150, frame_ms=10):
    """Cut leading and trailing quiet, keep a pad each side. The threshold is relative to the loudest frame."""
    n = int(SAMPLE_RATE * frame_ms / 1000)
    if len(audio) < 2 * n:
        return audio
    frames = audio[: len(audio) // n * n].reshape(-1, n)
    rms = np.sqrt((frames ** 2).mean(axis=1) + 1e-12)
    top = rms.max()
    if top <= 1e-6:
        return audio
    loud = np.flatnonzero(20 * np.log10(rms / top) > thresh_db)
    pad = int(SAMPLE_RATE * pad_ms / 1000)
    a = max(0, loud[0] * n - pad)
    b = min(len(audio), (loud[-1] + 1) * n + pad)
    return audio[a:b]


def trim_energy(audio, pad_ms=150, frame_ms=10):
    """Energy trim that knows the floor: the cut sits 40 dB under the loudest frame or 10 dB above the quietest
    tenth, whichever is higher. A noisy mic (the Pi) no longer keeps a second of hiss either side."""
    n = int(SAMPLE_RATE * frame_ms / 1000)
    if len(audio) < 2 * n:
        return audio
    frames = audio[: len(audio) // n * n].reshape(-1, n)
    db = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1) + 1e-12))
    top, floor = float(db.max()), float(np.percentile(db, 10))
    if top < -70:
        return audio
    cut = max(top - 40.0, floor + 10.0)
    loud = np.flatnonzero(db > cut)
    if not len(loud):
        return audio
    pad = int(SAMPLE_RATE * pad_ms / 1000)
    return audio[max(0, loud[0] * n - pad): min(len(audio), (loud[-1] + 1) * n + pad)]


_vad = None


def _silero():
    """Sapphire's own Silero VAD when this runs inside Sapphire; None in the plugin env."""
    global _vad
    if _vad is None:
        try:
            from core.stt.silero_vad import SileroVAD
            _vad = SileroVAD(sample_rate=SAMPLE_RATE)
        except Exception:
            _vad = False
    return _vad or None


def trim_voice(audio, pad_before_ms=200, pad_after_ms=250, threshold=0.5):
    """Cut to the voice: Silero marks the speech, a little room stays either side. Falls back to the energy trim
    when no VAD is here or it hears no speech (then the energy trim decides, or the clip stays whole)."""
    vad = _silero()
    if vad is None or len(audio) < 1600:
        return trim_energy(audio)
    try:
        vad.reset()
        pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
        n = 512
        probs = []
        for i in range(0, len(pcm) - n + 1, n):
            probs.append(float(vad.score_chunk(pcm[i:i + n])))
        probs = np.array(probs)
    except Exception:
        return trim_energy(audio)
    speech = np.flatnonzero(probs >= threshold)
    if len(speech) < 4:                         # under ~130 ms of voice: not a word; let energy decide
        return trim_energy(audio)
    a = max(0, speech[0] * n - int(SAMPLE_RATE * pad_before_ms / 1000))
    b = min(len(audio), (speech[-1] + 1) * n + int(SAMPLE_RATE * pad_after_ms / 1000))
    return audio[a:b]


def measure(audio):
    peak = float(np.abs(audio).max()) if len(audio) else 0.0
    rms = float(np.sqrt((audio ** 2).mean())) if len(audio) else 0.0
    return {'seconds': round(len(audio) / SAMPLE_RATE, 3), 'peak': round(peak, 4), 'rms': round(rms, 4),
            'clipped': bool(peak >= 0.999)}


def floor_db(audio, frame_ms=20):
    """The noise floor: the quietest tenth of the 20 ms frames, in dBFS. -60 is a quiet room on a good mic,
    -35 is audible hiss, -25 is a mic with its gain cranked."""
    n = int(SAMPLE_RATE * frame_ms / 1000)
    if len(audio) < 5 * n:
        return None
    frames = audio[: len(audio) // n * n].reshape(-1, n)
    rms = np.sqrt((frames ** 2).mean(axis=1) + 1e-12)
    q = float(np.percentile(rms, 10))
    return round(20 * np.log10(max(q, 1e-6)), 1)


FAINT = 0.03            # peak under this (-30 dBFS) is a whisper from the next room, not a sample
SNR_MIN = 20.0          # dB between the speech peak and the noise floor; under this the hiss competes


def clip_flags(side):
    """What is wrong with a clip, from its sidecar. [] when nothing is."""
    flags = []
    peak = float(side.get('peak') or 0)
    floor = side.get('floor_db')
    sec = float(side.get('seconds') or 0)
    ambient = str(side.get('collection', '')).startswith('ambient/')
    if side.get('clipped'):
        flags.append('clipped')
    if not ambient and peak and peak < FAINT:
        flags.append('faint')
    if isinstance(floor, (int, float)) and peak > 0:
        snr = 20 * math.log10(max(peak, 1e-6)) - floor
        if not ambient and snr < SNR_MIN:
            flags.append('noisy')
    phrase_like = str(side.get('collection', '')).startswith('positive/') or side.get('near') or \
        (str(side.get('collection', '')) == 'negative/synth')
    if phrase_like and sec and (sec < 0.5 or sec > 2.5):          # a sentence of ordinary talk may be long
        flags.append('short' if sec < 0.5 else 'long')
    if side.get('verdict') == 'drop':
        flags.append('dropped')
    return flags


def mic_report(audio):
    """What a take says about the microphone, before any trimming."""
    peak = float(np.abs(audio).max()) if len(audio) else 0.0
    fl = floor_db(audio)
    peak_db = round(20 * np.log10(max(peak, 1e-6)), 1)
    if fl is None:
        verdict, advice = 'short', 'too short to judge'
    elif fl > -28:
        verdict, advice = 'bad', 'the mic is very noisy or its gain is cranked: lower its input volume in the system sound settings, or use another mic for the phrase'
    elif fl > -38:
        verdict, advice = 'noisy', 'audible hiss under every clip: lower the input volume a little, or keep this mic for Room sound'
    elif peak >= 0.999:
        verdict, advice = 'clipped', 'too loud: back off the mic or lower its input volume'
    elif peak < 0.08:
        verdict, advice = 'quiet', 'very quiet: raise the input volume or come closer'
    else:
        verdict, advice = 'good', 'clean floor, healthy level'
    return {'floor_db': fl, 'peak_db': peak_db, 'peak': round(peak, 3), 'verdict': verdict, 'advice': advice}


def write_wav(path, audio):
    import soundfile as sf
    pcm = np.clip(audio, -1.0, 1.0)
    sf.write(str(path), (pcm * 32767).astype(np.int16), SAMPLE_RATE, subtype='PCM_16')


# --- clips ---------------------------------------------------------------------------------------

def new_id():
    return time.strftime('%Y%m%d-%H%M%S') + '-' + secrets.token_hex(2)


def add_clip(pdir, collection, audio, meta):
    """audio: float32 mono at 16 kHz, already trimmed if wanted. Returns the sidecar."""
    cdir = collection_dir(pdir, collection)
    cdir.mkdir(parents=True, exist_ok=True)
    cid = new_id()
    while (cdir / f"{cid}.wav").exists():
        cid = new_id()
    side = {'id': cid, 'collection': collection, 'created': time.time(), **measure(audio)}
    side.update({k: v for k, v in (meta or {}).items() if v is not None})
    side['source'] = side.get('source') or collection.split('/')[1]
    write_wav(cdir / f"{cid}.wav", audio)
    write_json(cdir / f"{cid}.json", side)
    return side


def read_side(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        return None


def clip_paths(pdir, collection, cid):
    cdir = collection_dir(pdir, collection)
    if not cid or '/' in cid or '\\' in cid or '..' in cid:
        raise ValueError('bad clip id')
    return cdir / f"{cid}.wav", cdir / f"{cid}.json"


def list_clips(pdir, collection, offset=0, limit=50, newest_first=True):
    cdir = collection_dir(pdir, collection)
    if not cdir.is_dir():
        return [], 0
    names = [e.name[:-4] for e in os.scandir(cdir) if e.name.endswith('.wav')]
    names.sort(reverse=newest_first)          # ids start with a timestamp
    page = names[offset: offset + limit]
    rows = []
    for cid in page:
        side = read_side(cdir / f"{cid}.json") or {'id': cid, 'collection': collection}
        rows.append(side)
    return rows, len(names)


def counts(pdir):
    """{collection: n} for every collection, plus per-source totals."""
    out = {}
    for c in COLLECTIONS:
        d = Path(pdir) / c
        out[c] = sum(1 for e in os.scandir(d) if e.name.endswith('.wav')) if d.is_dir() else 0
    return out


def delete_clip(pdir, collection, cid):
    wav, side = clip_paths(pdir, collection, cid)
    gone = False
    for p in (wav, side):
        if p.exists():
            p.unlink()
            gone = True
    return gone


def update_side(pdir, collection, cid, **fields):
    wav, sp = clip_paths(pdir, collection, cid)
    side = read_side(sp) or {'id': cid, 'collection': collection}
    side.update(fields)
    write_json(sp, side)
    return side


# --- taking audio in ---------------------------------------------------------------------------------

BOOST_BELOW = 0.1       # peak under -20 dBFS...
BOOST_SNR = 25.0        # ...with the voice this far above the floor: a quiet, clean take is lifted
BOOST_TO = 0.125        # to -18 dBFS, where a normal take through a good desk mic lands


def boost_if_faint(audio, floor):
    """A digital lift for a quiet but clean take (a satellite mic at modest gain). Returns (audio, gain_db)."""
    peak = float(np.abs(audio).max()) if len(audio) else 0.0
    if peak <= 0 or peak >= BOOST_BELOW or not isinstance(floor, (int, float)):
        return audio, 0.0
    if 20 * math.log10(peak) - floor < BOOST_SNR:
        return audio, 0.0
    g = BOOST_TO / peak
    return (audio * g).astype(np.float32), round(20 * math.log10(g), 1)


def take_recording(pdir, collection, data, meta, do_trim=True, drop_head_ms=0, drop_tail_ms=0, boost=True):
    """A recording from a browser or a satellite: decode, mono, 16 kHz, drop the key press at either end, trim,
    store. Raises ValueError when it is no sample."""
    audio, sr = decode(data)
    audio = to_16k(audio, sr)
    if len(audio) == 0 or float(np.abs(audio).max()) < 0.005:
        raise ValueError('nothing was heard (silence)')
    mic = mic_report(audio)
    meta = {**(meta or {}), 'floor_db': mic['floor_db'], 'mic_verdict': mic['verdict']}
    # the lift is for satellites, whose processing chain is quiet by design; a person's soft, whispered or
    # far-away take is quiet on purpose and stays so
    quiet_style = str(meta.get('style') or '') in ('soft', 'whisper', 'far away')
    if boost and meta.get('via') == 'satellite' and not quiet_style and not collection.startswith('ambient/'):
        audio, gain = boost_if_faint(audio, mic['floor_db'])
        if gain:
            meta['boost_db'] = gain
            meta['floor_db'] = round(mic['floor_db'] + gain, 1)
    if not collection.startswith('ambient/'):
        head, tail = int(SAMPLE_RATE * max(0, drop_head_ms) / 1000), int(SAMPLE_RATE * max(0, drop_tail_ms) / 1000)
        if len(audio) > head + tail + SAMPLE_RATE * MIN_SECONDS:
            audio = audio[head: len(audio) - tail if tail else len(audio)]
    if do_trim and not collection.startswith('ambient/'):
        audio = trim_voice(audio)
    if len(audio) < SAMPLE_RATE * MIN_SECONDS:
        raise ValueError('too short to be a sample (under a quarter second after trimming the silence)')
    if len(audio) > SAMPLE_RATE * MAX_UPLOAD_SECONDS:
        raise ValueError('longer than an hour')
    return add_clip(pdir, collection, audio, {**(meta or {}), 'source': collection.split('/')[1]})


def take_upload(pdir, collection, filename, data):
    """One uploaded file: audio, or a zip of audio. Returns (added sidecars, [(name, why) skipped])."""
    added, skipped = [], []
    name = Path(filename or 'upload').name
    if name.lower().endswith('.zip'):
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            return [], [(name, 'not a zip file')]
        for info in z.infolist():
            if info.is_dir() or not info.filename.lower().endswith(AUDIO_EXT) or Path(info.filename).name.startswith('.'):
                continue
            a, s = take_upload(pdir, collection, Path(info.filename).name, z.read(info))
            added += a
            skipped += s
        return added, skipped
    if not name.lower().endswith(AUDIO_EXT):
        return [], [(name, 'not an audio file (wav, flac, ogg, mp3, aiff, or a zip of them)')]
    try:
        side = take_recording(pdir, collection, data, {'original_name': name, 'source': 'uploaded'},
                              do_trim=not collection.startswith('ambient/'))
    except Exception as e:
        return [], [(name, str(e))]
    return [side], []


# --- projects ---------------------------------------------------------------------------------------

def create_project(root, phrase, folder=None):
    """folder: None = <root>/projects/<slug>; a path ending in a separator = that folder/<slug>; else that very folder."""
    phrase = ' '.join(re.sub(r"[_\-]+", ' ', str(phrase or '')).split())    # "hey_anita" is said "hey anita"
    if not phrase:
        raise ValueError('a phrase is needed')
    slug = slug_of(phrase)
    if load_project(root, slug) or (projects_dir(root) / slug / 'project.json').is_file():
        raise ValueError(f"a project for '{phrase}' already exists ({slug})")
    folder = str(folder or '').strip()
    if folder:
        pdir = Path(folder).expanduser()
        if folder.endswith(('/', '\\')):
            pdir = pdir / slug
        _claimable(root, pdir)
    else:
        pdir = projects_dir(root) / slug
    if (pdir / 'project.json').is_file():
        raise ValueError(f"{pdir} already holds a wake word")
    pdir.mkdir(parents=True, exist_ok=True)
    if pdir != projects_dir(root) / slug:
        paths.set_project_folder(root, slug, pdir)
    project = {'slug': slug, 'phrase': phrase, 'spellings': [phrase], 'created': time.time(),
               'negatives': [], 'rounds': [], 'settings': {}}
    save_project(pdir, project)
    return {**project, 'folder': str(pdir)}


def load_project(root, slug):
    pdir = project_dir(root, slug)
    p = pdir / 'project.json'
    if not p.is_file():
        return None
    pr = json.loads(p.read_text(encoding='utf-8'))
    pr['folder'] = str(pdir)
    return pr


def save_project(pdir, project):
    project = {k: v for k, v in project.items() if k not in ('folder', 'counts', 'progress', 'jobs')}   # derived, not stored
    write_json(Path(pdir) / 'project.json', project, indent=2)


def list_projects(root):
    out, seen = [], set()
    d = projects_dir(root)
    slugs = []
    if d.is_dir():
        slugs += [e.name for e in sorted(os.scandir(d), key=lambda e: e.name) if e.is_dir()]
    slugs += [s for s in sorted(paths.projects_index(root)) if s not in slugs]
    for slug in slugs:
        if slug in seen:
            continue
        seen.add(slug)
        pr = load_project(root, slug)
        if pr:
            pr['counts'] = counts(pr['folder'])
            out.append(pr)
    return out


def _claimable(root, pdir):
    """A folder the user typed may become a wake word's only when it is new or empty, and never the data folder, a
    folder above it, or a stranger inside it. Everything in a project folder is then ours, so delete may clear it."""
    root = Path(root).absolute()
    pdir = Path(pdir).absolute()
    if pdir == root or pdir in root.parents:
        raise ValueError('that is the data folder itself (or above it); pick a folder of its own')
    if root in pdir.parents and projects_dir(root).absolute() not in pdir.parents:
        raise ValueError(f"inside the data folder a wake word lives under {projects_dir(root)}")
    if pdir.exists():
        if not pdir.is_dir():
            raise ValueError(f"{pdir} is a file, not a folder")
        if any(pdir.iterdir()):
            raise ValueError(f"{pdir} is not empty; a wake word needs a folder of its own")


def delete_project(root, slug):
    """Remove the wake word's folder. It was empty when it became one (create and move both insist), so everything
    in it is ours; the data folder itself is never removed."""
    pdir = project_dir(root, slug)
    root_abs = Path(root).absolute()
    ok = False
    project = load_project(root, slug)
    if project and project.get('slug') == slug and pdir.is_dir() and pdir.absolute() != root_abs and pdir.absolute() not in root_abs.parents:
        shutil.rmtree(pdir)
        ok = True
    paths.set_project_folder(root, slug, None)
    return ok


def move_project(root, slug, folder):
    """Move a wake word's folder. Same filesystem: a rename. Across filesystems shutil copies, which can take a while
    for a big set; the route runs that case as a job."""
    src = project_dir(root, slug)
    dst = Path(str(folder).strip()).expanduser()
    if not (src / 'project.json').is_file():
        raise ValueError('no such wake word')
    if dst == src:
        return str(dst)
    _claimable(root, dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.is_dir():
        os.rmdir(dst)                           # empty: shutil.move would otherwise tuck src inside it
    shutil.move(str(src), str(dst))
    paths.set_project_folder(root, slug, dst)
    return str(dst)
