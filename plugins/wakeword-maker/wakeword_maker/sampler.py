# sampler.py - a small warm server in the plugin env so the page can play any voice saying the phrase without
# starting a job: GET /sample?engine=piper|kokoro&voice=...&text=...[&speaker=n] -> 16 kHz WAV. Started on
# demand by Sapphire (sampler_client), quits by itself after a quarter hour of silence.
import io
import json
import math
import os
import random
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import numpy as np

IDLE_S = 900
_last = time.monotonic()
_lock = threading.Lock()
_piper = _kokoro = None


class _Quiet:
    def step(self, *a, **k): pass
    def update(self, *a, **k): pass


def _engines():
    global _piper, _kokoro
    from . import compat, paths, synth
    compat.apply()
    if _piper is None:
        root = Path(os.environ.get('WWM_DATA_DIR') or '.')
        _piper = synth._Piper(paths.voices_dir(root), max(2, min(8, (os.cpu_count() or 4) // 2)), _Quiet())
        _kokoro = synth._Kokoro(synth._device({'device': os.environ.get('WWM_DEVICE', 'auto')}), _Quiet())
    return _piper, _kokoro


def _wav(audio):
    import numpy as np
    import soundfile as sf
    from . import store
    buf = io.BytesIO()
    sf.write(buf, (np.clip(audio, -1, 1) * 32767).astype(np.int16), store.SAMPLE_RATE, format='WAV', subtype='PCM_16')
    return buf.getvalue()


def sample(engine, voice, text, speaker=None, speed=1.0, blend=None):
    from . import synth
    import numpy as np
    piper, kokoro = _engines()
    rng = random.Random()
    with _lock:
        if engine == 'piper':
            n = piper.speakers(voice)
            spk = int(speaker) if speaker not in (None, '') else (rng.randrange(n) if n > 1 else None)
            audio, sr = piper.say(voice, text, spk, 1.0 / speed, 0.667, 0.8, rng)
            tag = {'speaker': spk, 'speakers': n}
        else:
            pair = None
            if blend:
                other, _, w = blend.partition('@')
                pair = (other, float(w or 0.5))
            audio, sr = kokoro.say(voice, text, speed, pair)
            tag = {'blend': blend} if blend else {}
    audio = synth._finish(audio, sr) if audio is not None else np.zeros(1600, np.float32)
    return _wav(audio), tag


_augs = {}


def variation(root, slug, clip, want=None, seed=None):
    """One draw of the project's recipe on one clip: collection/id, or 'random' for a random synthetic one."""
    from . import augment, store, paths
    pdir = paths.project_dir(root, slug)
    project = store.load_project(root, slug)
    if not project:
        raise ValueError('no such project')
    if clip == 'random' or not clip:
        rows, _ = store.list_clips(pdir, 'positive/synth', 0, 200)
        if not rows:
            raise ValueError('no clips yet')
        side = random.choice(rows)
        clip = f"positive/synth/{side['id']}"
    collection, _, cid = clip.rpartition('/')
    wav, _ = store.clip_paths(pdir, collection, cid)
    audio, sr = store.decode(wav)
    audio = store.to_16k(audio, sr)
    recipe = augment.recipe_of(project)
    key = (slug, json.dumps(recipe, sort_keys=True), len(os.listdir(pdir / 'ambient' / 'recorded')) if (pdir / 'ambient' / 'recorded').is_dir() else 0)
    aug = _augs.get(key)
    if aug is None:
        _augs.clear()
        aug = _augs[key] = augment.Augmenter(root, pdir, recipe)
    out, applied = aug.apply(audio, want=want, seed=int(seed) if seed else None)
    return _wav(out), {'applied': applied, 'clip': clip, 'missing': aug.pools['missing']}


_whisper = None


def _hear(model):
    global _whisper
    if _whisper is None:
        from . import qa, synth
        _whisper = qa._Whisper(model, synth._device({'device': os.environ.get('WWM_DEVICE', 'auto')}))
    return _whisper


def dryrun(root, slug, n=300, whisper_model='base.en'):
    """n draws of the recipe over a sample of the set (your own positives first, then synthetic ones), and the
    numbers that say whether it is making garbage: rejections, voice over noise, and Whisper's hit rate on the
    varied clips against the clean ones. The harshest survivors come back as (clip, seed) to replay."""
    import tempfile
    import soundfile as sf
    from . import augment, holdout, qa, store, paths
    pdir = paths.project_dir(root, slug)
    project = store.load_project(root, slug)
    if not project:
        raise ValueError('no such project')
    held = holdout.ids(pdir)
    own = [r for c in ('positive/recorded', 'positive/uploaded') for r in store.list_clips(pdir, c, 0, 10 ** 6)[0]
           if r['id'] not in held and r.get('verdict') != 'drop']
    syn = [r for r in store.list_clips(pdir, 'positive/synth', 0, 4000)[0] if r.get('verdict') != 'drop']
    rng = random.Random(7)
    rng.shuffle(own)
    rng.shuffle(syn)
    n = max(40, min(600, int(n)))
    picks = [(r, 'own') for r in own[: n // 2]] + [(r, 'synth') for r in syn[: n - min(len(own), n // 2)]]
    if not picks:
        raise ValueError('no clips to draw from')
    recipe = augment.recipe_of(project)
    aug = augment.Augmenter(root, pdir, recipe)
    hear = _hear(whisper_model)
    wanted = [t for t in project.get('spellings') or [] if t.strip()] + [project['phrase']]
    heard_clean = {'own': [0, 0], 'synth': [0, 0]}
    heard_varied = {'own': [0, 0], 'synth': [0, 0]}
    levels, survivors = [], []
    tmp = Path(tempfile.gettempdir()) / f"wwm-dryrun-{os.getpid()}.wav"
    judge_every = max(1, len(picks) // 160)             # Whisper on about 160 of them: enough for a rate, quick enough
    for i, (r, kind) in enumerate(picks):
        wav, _ = store.clip_paths(pdir, r['collection'], r['id'])
        try:
            audio, sr = store.decode(wav)
            audio = store.to_16k(audio, sr)
        except Exception:
            continue
        seed = 1000 + i
        before = len(aug.stats['snr'])
        out, applied = aug.apply(audio, seed=seed)
        snr = aug.stats['snr'][-1] if len(aug.stats['snr']) > before else None
        peak = float(np.abs(out).max()) if len(out) else 0.0
        levels.append(20 * math.log10(max(peak, 1e-6)))
        survivors.append({'clip': f"{r['collection']}/{r['id']}", 'seed': seed, 'snr': snr, 'applied': applied, 'kind': kind})
        if i % judge_every == 0:
            sf.write(str(tmp), (np.clip(out, -1, 1) * 32767).astype(np.int16), store.SAMPLE_RATE)
            said, _ = hear.hear(tmp)
            heard_varied[kind][1] += 1
            heard_varied[kind][0] += int(qa._similar(said, wanted) >= 0.6)
            said0, _ = hear.hear(wav)
            heard_clean[kind][1] += 1
            heard_clean[kind][0] += int(qa._similar(said0, wanted) >= 0.6)
    try:
        tmp.unlink()
    except Exception:
        pass
    rate = lambda d: {k: (round(100.0 * v[0] / v[1]) if v[1] else None) for k, v in d.items()}
    snrs = [x['snr'] for x in survivors if x['snr'] is not None]
    hv, hc = rate(heard_varied), rate(heard_clean)
    # calibrated on a real set 2026-10-04 (Whisper base.en, own clips): gentle 89, natural 67, rough 42, harsh 28.
    # Whisper is a harsher listener than a wake-word model, so these are orientation, and Test has the last word.
    hit = hv['own'] if hv['own'] is not None else hv['synth']
    rej = aug.stats['rejected'] / max(1, aug.stats['draws'])
    if aug.stats['tamed'] or rej > 0.15 or (hit is not None and hit < 35):
        verdict = 'too rough: the phrase drowns in too many draws. Slide down a stop.'
    elif hit is not None and hit < 55:
        verdict = 'rough: the phrase survives about half the draws. Right for a loud house; Test will say.'
    elif hit is not None and hit >= 85:
        verdict = 'mild: nearly every draw is as clear as the clean clip. Teaches less than Natural; a quiet-room model.'
    else:
        verdict = 'fits this set.'
    harshest = sorted([x for x in survivors if x['snr'] is not None], key=lambda x: x['snr'])[:5]
    return {
        'n': len(survivors), 'rejected': aug.stats['rejected'], 'tamed': aug.stats['tamed'], 'snr_floor_now': aug.snr,
        'heard_clean': hc, 'heard_varied': hv,
        'snr_hist': _hist(snrs, -15, 25, 16), 'level_hist': _hist(levels, -48, 0, 16),
        'harshest': harshest, 'verdict': verdict, 'preset': recipe.get('preset'),
        'missing': aug.pools['missing'],
    }


def _hist(values, lo, hi, bins):
    counts = [0] * bins
    w = (hi - lo) / bins
    for v in values:
        counts[max(0, min(bins - 1, int((v - lo) / w)))] += 1
    return counts


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        global _last
        _last = time.monotonic()
        u = urlsplit(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path not in ('/score', '/listen'):
            return self._send(404, b'{"error": "no such door"}', 'application/json')
        try:
            n = int(self.headers.get('Content-Length') or 0)
            data = self.rfile.read(n)
            from . import judge as _judge, store
            audio, sr = store.decode(data)
            audio = store.to_16k(audio, sr)
            root = Path(os.environ.get('WWM_DATA_DIR') or '.')
            rids = [r for r in (q.get('runs') or '').split(',') if r]
            with _lock:
                rep = (_judge.listen if u.path == '/listen' else _judge.score)(root, q.get('project', ''), rids, audio)
            self._send(200, json.dumps({'runs': rep, 'seconds': round(len(audio) / 16000, 2)}).encode(), 'application/json')
        except Exception as e:
            self._send(500, json.dumps({'error': f"{type(e).__name__}: {e}"}).encode(), 'application/json')

    def do_GET(self):
        global _last
        _last = time.monotonic()
        u = urlsplit(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == '/health':
            return self._send(200, b'{"ok": 1}', 'application/json')
        try:
            if u.path == '/sample':
                wav, tag = sample(q.get('engine', 'kokoro'), q.get('voice', 'af_heart'), q.get('text', 'hey sapphire'),
                                  q.get('speaker'), float(q.get('speed', 1.0)), q.get('blend') or None)
            elif u.path == '/variation':
                root = Path(os.environ.get('WWM_DATA_DIR') or '.')
                want = [w for w in (q.get('want') or '').split(',') if w] or None
                wav, tag = variation(root, q.get('project', ''), q.get('clip', 'random'), want, q.get('seed'))
            elif u.path == '/judge':
                from . import judge as _judge
                root = Path(os.environ.get('WWM_DATA_DIR') or '.')
                with _lock:
                    rep = _judge.judge(root, q.get('project', ''), q.get('run', ''), force=q.get('force') == '1')
                return self._send(200, json.dumps(rep).encode(), 'application/json')
            elif u.path == '/dryrun':
                root = Path(os.environ.get('WWM_DATA_DIR') or '.')
                with _lock:
                    rep = dryrun(root, q.get('project', ''), q.get('n', 300), q.get('whisper', 'base.en'))
                return self._send(200, json.dumps(rep).encode(), 'application/json')
            else:
                return self._send(404, b'{"error": "no such door"}', 'application/json')
            self._send(200, wav, 'audio/wav', {'X-Sample': json.dumps(tag)})
        except Exception as e:
            self._send(500, json.dumps({'error': f"{type(e).__name__}: {e}"}).encode(), 'application/json')

    def _send(self, code, body, ctype, extra=None):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)


def main():
    port = int(sys.argv[sys.argv.index('--port') + 1]) if '--port' in sys.argv else 0
    srv = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    print(f"sampler on {srv.server_address[1]}", flush=True)

    def watchdog():
        while True:
            time.sleep(30)
            if time.monotonic() - _last > IDLE_S:
                print('sampler idle, leaving', flush=True)
                os._exit(0)
    threading.Thread(target=watchdog, daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
