# watch.py - a room watch: a microphone listens for a long while, every chunk is scored by the chosen models, and
# every fire is kept with its audio for the user to sort: a false alarm becomes a negative, "that was me" a
# positive, the rest is discarded. Runs as a thread inside the app (satellite listening needs the device engine),
# scoring goes to the warm sampler process. State on disk: <project>/watch/<id>/watch.json + <fire>.wav/json.
# Two ways the sound comes: pulled (the browser posts chunks, a Pi is asked a minute at a time) or pushed (an
# ESP32 with the capture door, firmware 0.7.6, posts every piece to core's room door; `sink` queues it, the loop
# `take`s it). A pushed piece carries the board's own fires, through its own front end: they count as a model
# of their own, `board:<model>`. With `keep`, every piece becomes a room take in ambient/recorded.
import json
import queue
import threading
import time
from pathlib import Path

import numpy as np

from . import store
from .paths import read_json, write_json

FLOOR = 0.2          # peaks below this are not kept at all
CUT_BEFORE = 2.0     # seconds of audio kept before a fire's peak
CUT_AFTER = 1.0
_live = {}           # slug -> {'thread', 'stop', 'id', 'device', 'disarm'}
_lock = threading.Lock()
_inbox = {}          # device id -> Queue of (audio, meta): pieces a board pushed, drained by the watch on it
_halts = {}          # device id -> Event: a take() in progress returns when the watch stops or pauses
INBOX = 4            # pieces held while nobody drains (a plugin reload): a couple of minutes, then the oldest go


def wdir(pdir, wid=None):
    d = Path(pdir) / 'watch'
    return d / wid if wid else d


def _write(pdir, st):
    d = wdir(pdir, st['id'])
    d.mkdir(parents=True, exist_ok=True)
    write_json(d / 'watch.json', st)


def read(pdir, wid):
    try:
        return json.loads((wdir(pdir, wid) / 'watch.json').read_text(encoding='utf-8'))
    except Exception:
        return None


def latest(pdir, slug):
    d = wdir(pdir)
    ids = sorted([e.name for e in d.iterdir() if (e / 'watch.json').is_file()], reverse=True) if d.is_dir() else []
    st = read(pdir, ids[0]) if ids else None
    if st:
        st['running'] = slug in _live and _live[slug]['id'] == st['id'] and _live[slug]['thread'].is_alive()
    return st


def running(slug):
    w = _live.get(slug)
    return bool(w and w['thread'].is_alive())


def sink(device_id, audio_bytes, meta=None):
    """voice.room_sink: a board pushed a piece of its room. Decoded and queued for the watch on that device;
    `accepted` false when no watch runs for it, which ends the board's capture."""
    if not any(w.get('device') == device_id and w['thread'].is_alive() for w in list(_live.values())):
        return {'ok': True, 'accepted': False, 'error': 'no room watch is running for this device'}
    try:
        audio, sr = store.decode(audio_bytes, (meta or {}).get('suffix') or '.wav')
        audio = store.to_16k(audio, sr)
    except Exception as e:
        return {'ok': False, 'error': f"could not read the piece: {e}"}
    q = _inbox.setdefault(device_id, queue.Queue(INBOX))
    while True:
        try:
            q.put_nowait((audio, dict(meta or {})))
            return {'ok': True, 'accepted': True}
        except queue.Full:                      # nobody is draining: the oldest piece goes
            try:
                q.get_nowait()
            except queue.Empty:
                pass


def take(device_id, timeout):
    """The next pushed piece for a device: (audio, meta); None when the watch was stopped or paused meanwhile.
    Raises when nothing arrives within `timeout`."""
    q = _inbox.setdefault(device_id, queue.Queue(INBOX))
    halt = _halts.setdefault(device_id, threading.Event())
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if halt.is_set():
            return None
        try:
            return q.get(timeout=0.5)
        except queue.Empty:
            continue
    raise RuntimeError(f"no piece of the room arrived from {device_id} in {int(timeout)} s")


def chunk(pdir, st, audio, device, listen_fn, meta=None):
    """One chunk of audio: score it, keep the fires. `listen_fn(wav_bytes) -> {rid: {peaks, threshold}}`.
    `meta` rides with a pushed piece: the board's own fires ([seconds, score]), its model, the piece number."""
    n = len(audio) / 16000.0
    if n < 1.0:
        return st
    meta = meta or {}
    import io
    import soundfile as sf
    buf = io.BytesIO()
    sf.write(buf, (np.clip(audio, -1, 1) * 32767).astype(np.int16), 16000, format='WAV', subtype='PCM_16')
    rep = listen_fn(buf.getvalue())
    t0 = st['seconds']
    events = {}          # peak time -> {run: score}
    for rid, r in (rep or {}).items():
        if r.get('error'):
            st['errors'] = (st.get('errors') or [])[-4:] + [f"{rid}: {r['error']}"]
            continue
        thr = float(r.get('threshold') or 0.5)
        for t, v in r.get('peaks') or []:
            if v < FLOOR:
                continue
            slot = next((k for k in events if abs(k - t) < 1.5), None)
            if slot is None:
                events[t] = {}
                slot = t
            events[slot][rid] = {'score': v, 'fires': v >= thr}
    # the board's own fires: what the model on the chip said, through the chip's own front end
    if meta.get('model'):
        st['board'] = str(meta['model'])[:48]
    board = f"board:{st.get('board') or 'model'}"
    listed = [f for f in (meta.get('fires') or []) if isinstance(f, (list, tuple)) and len(f) == 2]
    for t, v in listed:
        t, v = float(t), float(v)
        slot = next((k for k in events if abs(k - t) < 1.5), None)
        if slot is None:
            events[t] = {}
            slot = t
        events[slot][board] = {'score': v, 'fires': True}
    st['board_fires'] = int(st.get('board_fires') or 0) + max(len(listed), int(meta.get('fire_count') or 0))
    for t, by in sorted(events.items()):
        if not any(x['fires'] for x in by.values()):
            continue
        a, b = int(max(0, t - CUT_BEFORE) * 16000), int(min(n, t + CUT_AFTER) * 16000)
        fid = store.new_id()
        d = wdir(pdir, st['id'])
        store.write_wav(d / f"{fid}.wav", audio[a:b])
        fire = {'id': fid, 'at': round(t0 + t, 1), 'when': time.time(), 'device': device, 'runs': by,
                'score': max(x['score'] for x in by.values()), 'seconds': round((b - a) / 16000, 2), 'fate': None}
        write_json(d / f"{fid}.json", fire)
        st['fires'].append(fire)
    d = wdir(pdir, st['id'])
    for f in st['fires']:                       # a fire sorted on the page while this chunk was scored: its file is the truth
        cur = read_json(d / f"{f['id']}.json")
        if cur:
            f['fate'], f['clip'] = cur.get('fate'), cur.get('clip')
    if st.get('keep'):                         # every piece is a room take: hours of this room, through this microphone
        side = store.add_clip(pdir, 'ambient/recorded', audio, {
            'device': device, 'via': 'capture', 'watch': st['id'], 'piece': meta.get('chunk'),
            'note': f"room watch {st['id']}" + (', board test mode' if meta.get('test') else ''),
            'board_fires': len(listed) or None})
        st['kept'] = int(st.get('kept') or 0) + 1
        st['kept_seconds'] = round(float(st.get('kept_seconds') or 0) + float(side.get('seconds') or n), 1)
    st['seconds'] = round(t0 + n, 1)
    st['chunks'] += 1
    _write(pdir, st)
    return st


def start(pdir, slug, device, minutes, runs, listen_fn, capture_fn=None, chunk_seconds=60, keep=False, test=False, arm=None, disarm=None):
    """Begin a watch. With `capture_fn(seconds) -> float32 audio` (or `(audio, meta)` for a pushed piece) a thread
    takes chunks from a satellite until `minutes` are up or stop() is called; without it, the browser posts chunks
    itself (see chunk()). `keep`: every chunk becomes a room take. `arm(seconds)` tells a pushing board to begin
    (a refusal raises here, before any thread exists) and `disarm()` to stop; `test` is for the record."""
    with _lock:
        if running(slug):
            raise RuntimeError('a watch is already running for this wake word')
        wid = time.strftime('%Y%m%d-%H%M%S')
        st = {'id': wid, 'device': device, 'minutes': minutes, 'runs': runs, 'started': time.time(), 'seconds': 0.0, 'chunks': 0,
              'fires': [], 'ended': None, 'error': None, 'browser': capture_fn is None, 'keep': bool(keep), 'test': bool(test),
              'pushed': arm is not None, 'kept': 0, 'kept_seconds': 0.0, 'board_fires': 0, 'board': None}
        _write(pdir, st)
        if capture_fn is None:
            _live[slug] = {'thread': threading.Thread(target=lambda: None), 'stop': threading.Event(), 'id': wid, 'device': device}
            return st
        if arm is not None:
            _halts.setdefault(device, threading.Event()).clear()
            with _inbox.setdefault(device, queue.Queue(INBOX)).mutex:
                _inbox[device].queue.clear()
            arm(minutes * 60)
        _spawn(pdir, slug, st, device, listen_fn, capture_fn, chunk_seconds, st['started'] + minutes * 60, disarm)
        return st


def _spawn(pdir, slug, st, device, listen_fn, capture_fn, chunk_seconds, end, disarm=None):
    """The satellite loop. `stop` ends the watch; `pause` (a plugin reload or shutdown) leaves it resumable."""
    stop, pause = threading.Event(), threading.Event()

    def loop():
        try:
            while not stop.is_set() and not pause.is_set() and time.time() < end:
                want = int(min(chunk_seconds, max(5, end - time.time())))
                try:
                    got = capture_fn(want)
                except Exception as e:
                    st['errors'] = (st.get('errors') or [])[-4:] + [f"{device}: {e}"]
                    _write(pdir, st)
                    if stop.wait(5) or pause.is_set():
                        break
                    continue
                if pause.is_set() or got is None:
                    continue
                audio, meta = got if isinstance(got, tuple) else (got, None)
                try:
                    chunk(pdir, st, audio, device, listen_fn, meta)
                except Exception as e:                      # a scoring hiccup loses one chunk, not the watch
                    st['errors'] = (st.get('errors') or [])[-4:] + [f"scoring: {e}"]
                    _write(pdir, st)
        finally:
            if pause.is_set() and not stop.is_set():
                st['paused'] = time.time()
            else:
                st['ended'] = time.time()
                if disarm is not None:                      # the board stops pushing; harmless when it already has
                    try:
                        disarm()
                    except Exception as e:
                        st['errors'] = (st.get('errors') or [])[-4:] + [f"{device}: could not end its capture: {e}"]
            _write(pdir, st)

    th = threading.Thread(target=loop, name=f"wwm-watch-{slug}", daemon=True)
    _live[slug] = {'thread': th, 'stop': stop, 'pause': pause, 'id': st['id'], 'device': device, 'disarm': disarm}
    th.start()


def resume(pdir, slug, make_fns):
    """After a reload or a restart: pick the latest satellite watch back up if it still has time left.
    `make_fns(device, runs, st) -> (listen_fn, capture_fn, chunk_seconds[, arm, disarm])`; a pushing board is
    armed again for the time left (one already capturing is left alone by the caller's arm)."""
    with _lock:
        st = latest(pdir, slug)
        if not st or st.get('browser') or st.get('ended') or st.get('running'):
            return None
        if any(t.name == f"wwm-watch-{slug}" and t.is_alive() for t in threading.enumerate()):
            return 'busy'                 # the loop from before the reload is finishing its chunk; ask again
        end = st['started'] + st['minutes'] * 60
        if end - time.time() < 30:
            st['ended'] = time.time()
            _write(pdir, st)
            return None
        listen_fn, capture_fn, chunk_seconds, *rest = make_fns(st['device'], st['runs'], st)
        arm, disarm = (list(rest) + [None, None])[:2]
        st['resumed'] = int(st.get('resumed') or 0) + 1
        st.pop('paused', None)
        if arm is not None:
            _halts.setdefault(st['device'], threading.Event()).clear()
            try:
                arm(int(end - time.time()))
            except Exception as e:
                st['errors'] = (st.get('errors') or [])[-4:] + [f"{st['device']}: could not begin its capture again: {e}"]
        _write(pdir, st)
        _spawn(pdir, slug, st, st['device'], listen_fn, capture_fn, chunk_seconds, end, disarm)
        return st


def pause_all():
    """The plugin is going down: let every satellite loop finish its chunk and leave the watch resumable."""
    with _lock:
        for w in _live.values():
            if w.get('pause'):
                w['pause'].set()
            if w.get('device') in _halts:
                _halts[w['device']].set()          # a take() in progress returns now; the board keeps pushing into the inbox


def stop(pdir, slug):
    w = _live.get(slug)
    if not w:
        return None
    w['stop'].set()
    if w.get('device') in _halts:
        _halts[w['device']].set()
    if w.get('disarm') is not None:                  # at once, not when the loop gets round to it
        try:
            w['disarm']()
        except Exception:
            pass
    st = read(pdir, w['id'])
    if st and st.get('browser') and not st.get('ended'):
        st['ended'] = time.time()
        _write(pdir, st)
    return st


def fate(pdir, wid, fid, what, meta):
    """What a fire was: 'negative' keeps it as a recorded negative, 'positive' as a recorded phrase take,
    'discard' deletes its audio. Returns the fire."""
    d = wdir(pdir, wid)
    fj = d / f"{fid}.json"
    if not fj.is_file() or '/' in fid or '..' in fid:
        raise ValueError('no such fire')
    fire = json.loads(fj.read_text(encoding='utf-8'))
    wav = d / f"{fid}.wav"
    if what in ('negative', 'positive') and wav.is_file():
        audio, sr = store.decode(wav.read_bytes())
        audio = store.to_16k(audio, sr)
        side = store.add_clip(pdir, f"{what}/recorded", audio, {**meta, 'source': 'watch', 'watch': wid, 'note': meta.get('note') or ('room watch: false alarm' if what == 'negative' else 'room watch: the phrase')})
        fire['clip'] = side['id']
    if wav.is_file():
        wav.unlink()
    fire['fate'] = what
    write_json(fj, fire)
    st = read(pdir, wid)
    if st:
        st['fires'] = [fire if f['id'] == fid else f for f in st['fires']]
        _write(pdir, st)
    return fire
