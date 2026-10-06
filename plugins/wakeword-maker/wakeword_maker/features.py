# features.py - the set a trainer eats, built from the project: the kept clips (never the held-out ones), each
# varied K times by the Variations recipe in a pool of workers, then turned into the trainer's features and
# written as memmaps under <project>/features/<trainer>/<key>/. The key hashes everything that shaped the set
# (recipe, clip ids, K, holdout), so a second training run or a sweep reuses it instead of building it again.
#
# openWakeWord features: a 2 s window of 16 kHz audio -> (16, 96) speech embeddings via the two small onnx
# models from the oww_models dataset. Negatives come from four places: the sound-alikes (synthetic and yours),
# your ordinary negatives, false accepts mined later, and your own room takes cut into windows.
import hashlib
import json
import math
import os
import random
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np

from . import augment, catalog, compat, holdout, paths, store
from .paths import read_json, write_json

SR = paths.SAMPLE_RATE
WINDOW_S = 2.0                                  # oww rows are 16 frames of 80 ms: two seconds
WINDOW = int(SR * WINDOW_S)
OWN_REPEAT = 8                                  # your own positives are varied this many extra times: 166 clips
                                                # against 20,000 synthetic ones would otherwise barely be heard
_aug = None


def recipe_for(project, args):
    """The recipe a run uses: the one named in its args (a queued run keeps the slider's value at the time), else the
    project's current one. A named stop, or 'custom' = the project's fine-tuned rows."""
    name = str((args or {}).get('recipe') or '')
    if name in augment.PRESETS:
        r = {k: (dict(v) if isinstance(v, dict) else v) for k, v in augment.PRESETS[name].items()}
        r['preset'] = name
        return r
    return augment.recipe_of(project)


def _pool_init(root, pdir, recipe, seed):
    global _aug
    compat.apply()
    _aug = augment.Augmenter(root, pdir, recipe, seed=seed)
    random.seed(seed)
    np.random.seed(seed % (2 ** 32))


def _place(audio, rng):
    """The clip in a fixed window, ending 0..200 ms before the end (as upstream does); long clips keep an end."""
    out = np.zeros(WINDOW, np.float32)
    if len(audio) >= WINDOW:
        return (audio[:WINDOW] if rng.random() < 0.5 else audio[-WINDOW:]).astype(np.float32)
    jitter = int(rng.uniform(0, 0.2) * SR)
    start = max(0, WINDOW - len(audio) - jitter)
    out[start:start + len(audio)] = audio
    return out


def _vary_one(args):
    """Worker: one clip, K variations, placed in the window. Returns int16 rows."""
    wav, k, seed, vary = args
    rng = random.Random(seed)
    try:
        audio, sr = store.decode(wav)
        audio = store.to_16k(audio, sr)
    except Exception:
        return np.zeros((0, WINDOW), np.int16)
    rows = []
    for i in range(k):
        x = audio
        if vary:
            x, _ = _aug.apply(audio, rng=random.Random(seed * 31 + i))
        else:
            peak = float(np.abs(x).max()) if len(x) else 0.0
            if peak > 1e-5:
                x = x * (augment.NORM_PEAK / peak)
        rows.append((np.clip(_place(x, rng), -1, 1) * 32767).astype(np.int16))
    return np.stack(rows)


def _ambient_windows(wav, stride_s=1.0):
    """A room take cut into 2 s windows a second apart: negatives from your own house."""
    try:
        audio, sr = store.decode(wav)
        audio = store.to_16k(audio, sr)
    except Exception:
        return np.zeros((0, WINDOW), np.int16)
    peak = float(np.abs(audio).max()) if len(audio) else 0.0
    if peak > 1e-5:
        audio = audio * min(1.0, augment.NORM_PEAK * 4 / peak)      # a room is quieter than speech; do not blow it up
    step = int(SR * stride_s)
    out = [audio[i:i + WINDOW] for i in range(0, len(audio) - WINDOW + 1, step)]
    if not out:
        return np.zeros((0, WINDOW), np.int16)
    return (np.clip(np.stack(out), -1, 1) * 32767).astype(np.int16)


class Embedder:
    """openWakeWord's two feature models, on the CPU (onnxruntime)."""

    def __init__(self, root, threads):
        from openwakeword.utils import AudioFeatures
        d = catalog.dataset_dir(root, 'oww_models')
        mel, emb = d / 'melspectrogram.onnx', d / 'embedding_model.onnx'
        if not (mel.exists() and emb.exists()):
            raise RuntimeError('the openWakeWord feature models are not downloaded (Settings → Datasets → openWakeWord feature models)')
        self.f = AudioFeatures(melspec_model_path=str(mel), embedding_model_path=str(emb), inference_framework='onnx', device='cpu', ncpu=threads)
        self.shape = tuple(self.f.get_embedding_shape(WINDOW_S))
        self.threads = threads

    def embed(self, pcm_int16, batch=256):
        return self.f.embed_clips(pcm_int16, batch_size=batch, ncpu=self.threads).astype(np.float32)


def synth_ok(r, synth='all'):
    """Does this clip pass the run's synthetic-voice filter? 'all', one engine ('piper', 'kokoro'), 'none' (your own
    recordings only), or a list of voice names (a saved set). Recorded and uploaded clips always pass."""
    if r.get('source') != 'synth' or synth in (None, '', 'all'):
        return True
    if synth == 'none':
        return False
    if isinstance(synth, (list, tuple, set)):
        return r.get('voice') in synth
    return r.get('engine') == synth


def _clips(pdir, held, synth='all', exclude_near=()):
    """What goes where. Returns dict of lists of (wav path, repeat). `exclude_near`: sound-alike texts kept out of
    training (they stay in the held-out slice, so the judge still reports them)."""
    skip = {' '.join(str(t).lower().split()) for t in (exclude_near or ())}

    def rows(c):
        out = [r for r in store.list_clips(pdir, c, 0, 10 ** 7)[0] if r.get('verdict') != 'drop' and synth_ok(r, synth)]
        if skip and c.startswith('negative/'):
            out = [r for r in out if r['id'] in held or ' '.join(str(r.get('text') or '').lower().split()) not in skip]
        return out
    sets = {'positive': [], 'val_positive': [], 'adversarial': [], 'val_negative': [], 'ambient': [], 'val_ambient': []}
    for c in ('positive/synth', 'positive/recorded', 'positive/uploaded'):
        for r in rows(c):
            wav, _ = store.clip_paths(pdir, c, r['id'])
            own = r.get('source') != 'synth'
            if r['id'] in held:
                sets['val_positive'].append((str(wav), 1))
            else:
                sets['positive'].append((str(wav), OWN_REPEAT if own else 1))
    for c in ('negative/synth', 'negative/recorded', 'negative/uploaded', 'negative/mined'):
        for r in rows(c):
            wav, _ = store.clip_paths(pdir, c, r['id'])
            own = r.get('source') in ('recorded', 'uploaded')
            if r['id'] in held:
                sets['val_negative'].append((str(wav), 1))
            else:
                sets['adversarial'].append((str(wav), 4 if own else 1))
    for c in ('ambient/recorded', 'ambient/uploaded'):
        for r in rows(c):
            wav, _ = store.clip_paths(pdir, c, r['id'])
            (sets['val_ambient'] if r['id'] in held else sets['ambient']).append((str(wav), 1))
    return sets


def key_of(project, recipe, sets, k, use_holdout):
    h = hashlib.sha1()
    h.update(json.dumps(recipe, sort_keys=True).encode())
    h.update(json.dumps({n: sorted(p for p, _ in v) for n, v in sets.items()}, sort_keys=True).encode())
    h.update(f"k={k} holdout={use_holdout} window={WINDOW} own={OWN_REPEAT} v3".encode())
    return h.hexdigest()[:12]


def build_oww(root, args, progress):
    """The features job for openWakeWord. args: project, rounds (K), holdout (bool), seed, device."""
    compat.apply()
    slug = args['project']
    project = store.load_project(root, slug)
    if not project:
        raise ValueError(f"no project {slug}")
    pdir = paths.project_dir(root, slug)
    use_holdout = args.get('holdout', True) not in (False, 'false', 0, '0')
    k = max(1, min(6, int(args.get('rounds') or 2)))
    seed = int(args.get('seed') or 1)
    recipe = recipe_for(project, args)
    held = holdout.ids(pdir) if use_holdout else set()
    if use_holdout and not held:
        holdout.make(pdir)
        held = holdout.ids(pdir)
    synth = args.get('synth') or 'all'
    exclude_near = [t for t in (args.get('exclude_near') or []) if str(t).strip()]
    sets = _clips(pdir, held, synth, exclude_near)
    key = key_of(project, recipe, sets, k, use_holdout)
    out = pdir / 'features' / 'oww' / key
    meta_path = out / 'meta.json'
    meta = read_json(meta_path)                         # a half-written meta (full disk, a kill) means: build again
    if meta and not args.get('rebuild'):
        progress.done({'key': key, 'reused': True, **meta})
        return key
    out.mkdir(parents=True, exist_ok=True)
    threads = max(2, (os.cpu_count() or 4) // 2)
    workers = max(2, min(12, (os.cpu_count() or 4) - 2))
    emb = Embedder(root, threads)
    progress.step('features', f"embedding shape {emb.shape}, {workers} workers varying, {threads} threads embedding")

    plan = [('positive', True), ('adversarial', True), ('val_positive', False), ('val_negative', False)]
    counts = {}
    t0 = time.time()
    with Pool(workers, initializer=_pool_init, initargs=(str(root), str(pdir), recipe, seed)) as pool:
        for name, vary in plan:
            items = sets[name]
            if not items:
                counts[name] = 0
                continue
            jobs = [(wav, (k if vary else 1) * rep, seed * 7919 + i, vary) for i, (wav, rep) in enumerate(items)]
            total_rows = sum(j[1] for j in jobs)
            mm = np.lib.format.open_memmap(out / f"{name}.npy", mode='w+', dtype=np.float32, shape=(total_rows, *emb.shape))
            at = 0
            buf = []
            buf_n = 0
            progress.step('features', f"{name}: {len(items):,} clips → {total_rows:,} rows")
            def flush():
                nonlocal at, buf, buf_n
                if not buf:
                    return
                pcm = np.concatenate(buf)
                feats = emb.embed(pcm)
                mm[at:at + len(feats)] = feats
                at += len(feats)
                buf, buf_n = [], 0
            done_clips = 0
            for rows in pool.imap_unordered(_vary_one, jobs, chunksize=8):
                if len(rows):
                    buf.append(rows)
                    buf_n += len(rows)
                done_clips += 1
                if buf_n >= 2048:
                    flush()
                if done_clips % 50 == 0:
                    pct = 100.0 * done_clips / len(jobs)
                    progress.update(pct, f"{name}: {done_clips:,} of {len(jobs):,} clips · {at:,} rows embedded · {time.time() - t0:.0f}s")
            flush()
            mm.flush()
            del mm
            if at < total_rows:                       # a few clips failed to read: trim the file
                arr = np.load(out / f"{name}.npy", mmap_mode='r')[:at]
                np.save(out / f"{name}.tmp.npy", np.ascontiguousarray(arr))
                os.replace(out / f"{name}.tmp.npy", out / f"{name}.npy")
            counts[name] = at
    # the room: windows, embedded straight (variation would be odd on a room)
    for name in ('ambient', 'val_ambient'):
        items = sets[name]
        if not items:
            counts[name] = 0
            continue
        progress.step('features', f"{name}: {len(items)} take(s) into 2 s windows")
        chunks = [_ambient_windows(wav) for wav, _ in items]
        pcm = np.concatenate([c for c in chunks if len(c)]) if any(len(c) for c in chunks) else np.zeros((0, WINDOW), np.int16)
        feats = emb.embed(pcm) if len(pcm) else np.zeros((0, *emb.shape), np.float32)
        np.save(out / f"{name}.npy", feats)
        counts[name] = len(feats)
        counts[name + '_seconds'] = int(sum(len(c) for c in chunks) * 1.0 + (WINDOW_S - 1.0) * len([c for c in chunks if len(c)]))
    meta = {'key': key, 'trainer': 'oww', 'shape': list(emb.shape), 'rounds': k, 'holdout': use_holdout, 'recipe': recipe.get('preset'), 'synth': synth, 'exclude_near': exclude_near,
            'counts': counts, 'seconds': round(time.time() - t0), 'when': time.time(), 'own_repeat': OWN_REPEAT,
            'holdout_when': (holdout.read(pdir) or {}).get('when')}
    write_json(meta_path, meta, indent=1)
    prune_sets(pdir, 'oww')
    progress.done(meta)
    return key


KEEP_SETS = 3      # feature sets kept per family and wake word: every clip added makes a new one, and they are big


def prune_sets(pdir, fam, keep=KEEP_SETS, stale_hours=24):
    """Drop the oldest finished feature sets beyond `keep`, and half-built ones (no meta) older than a day. The
    sets a run trained on are not needed again: the run keeps its model and its numbers."""
    import shutil
    d = Path(pdir) / 'features' / fam
    if not d.is_dir():
        return 0
    done, gone = [], 0
    for sub in d.iterdir():
        if not sub.is_dir():
            continue
        meta = read_json(sub / 'meta.json')
        if meta:
            done.append((meta.get('when') or 0, sub))
        elif time.time() - sub.stat().st_mtime > stale_hours * 3600:
            shutil.rmtree(sub, ignore_errors=True)
            gone += 1
    for _, sub in sorted(done, reverse=True)[keep:]:
        shutil.rmtree(sub, ignore_errors=True)
        gone += 1
    return gone


def latest_oww(pdir):
    """The newest feature set on disk, with its meta."""
    d = pdir / 'features' / 'oww'
    if not d.is_dir():
        return None
    best = None
    for sub in d.iterdir():
        meta = read_json(sub / 'meta.json')
        if meta:
            if best is None or meta.get('when', 0) > best.get('when', 0):
                best = {**meta, 'dir': str(sub)}
    return best
