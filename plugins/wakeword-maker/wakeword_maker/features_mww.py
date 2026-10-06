# features_mww.py - the microWakeWord feature set: the same kept clips and the same Variations recipe as the
# openWakeWord set, turned into 40-bin micro-frontend spectrograms (pymicro-features, 10 ms steps: the exact
# frontend the ESP32 runs) and written as RaggedMmaps in the folder shape microWakeWord's loader expects:
#   <project>/features/mww/<key>/positive/{training,validation,testing}/wakeword_mmap/
#   <project>/features/mww/<key>/adversarial/{training,validation,testing}/wakeword_mmap/
#   <project>/features/mww/<key>/room/{training,validation_ambient,testing_ambient}/room_mmap/
# The microWakeWord source itself is fetched once (a pinned tarball, no git) into <root>/tools/.
import hashlib
import json
import os
import random
import tarfile
import time
import urllib.request
from multiprocessing import Pool
from pathlib import Path

import numpy as np

from . import augment, compat, features, holdout, paths, store
from .paths import read_json, write_json
from .features import recipe_for

MWW_SHA = '4665173cd35f1cff9a61e06fc427f124766c488e'
MWW_URL = f"https://github.com/kahrendt/microWakeWord/archive/{MWW_SHA}.tar.gz"
STEP_MS = 10
TRAIN_SLIDES = 3          # each training spectrogram also yielded shifted by 1 and 2 frames (upstream uses 10 on 1k clips)
TAIL_MS = (150, 250)      # silence after the word, as upstream's jitter; the loader keeps the end of the spectrogram
_aug = None


def source_dir(root):
    """The pinned microWakeWord tree, fetched on first use."""
    d = Path(root) / 'tools' / f"microWakeWord-{MWW_SHA}"
    if (d / 'microwakeword' / 'model_train_eval.py').is_file():
        return d
    d.parent.mkdir(parents=True, exist_ok=True)
    tgz = d.parent / 'mww.tgz'
    with urllib.request.urlopen(MWW_URL, timeout=60) as r, open(tgz, 'wb') as fh:
        fh.write(r.read())
    with tarfile.open(tgz) as t:
        t.extractall(d.parent)
    tgz.unlink()
    if not (d / 'microwakeword').is_dir():
        raise RuntimeError('the microWakeWord source did not unpack where expected')
    return d


def _spectrogram(audio):
    import sys
    from microwakeword.audio.audio_utils import generate_features_for_clip
    return generate_features_for_clip((np.clip(audio, -1, 1) * 32767).astype(np.int16), step_ms=STEP_MS)


def _pool_init(root, pdir, recipe, seed, src):
    global _aug
    import sys
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    compat.apply()
    _aug = augment.Augmenter(root, pdir, recipe, seed=seed)


def _vary_spec(args):
    """Worker: one clip, K variations (or the clean clip), each as a spectrogram with a short silent tail."""
    wav, k, seed, vary = args
    rng = random.Random(seed)
    try:
        audio, sr = store.decode(wav)
        audio = store.to_16k(audio, sr)
    except Exception:
        return []
    out = []
    for i in range(k):
        x = audio
        if vary:
            x, _ = _aug.apply(audio, rng=random.Random(seed * 31 + i))
        else:
            peak = float(np.abs(x).max()) if len(x) else 0.0
            if peak > 1e-5:
                x = x * (augment.NORM_PEAK / peak)
        tail = np.zeros(int(16000 * rng.uniform(*TAIL_MS) / 1000), np.float32)
        spec = _spectrogram(np.concatenate([x, tail]))
        if len(spec):
            out.append(spec.astype(np.float32))
    return out


def _room_spec(wav):
    try:
        audio, sr = store.decode(wav)
        audio = store.to_16k(audio, sr)
    except Exception:
        return None
    peak = float(np.abs(audio).max()) if len(audio) else 0.0
    if peak > 1e-5:
        audio = audio * min(1.0, augment.NORM_PEAK * 4 / peak)
    return _spectrogram(audio).astype(np.float32)


def _write(out_dir, name, gen, batch=100):
    from mmap_ninja.ragged import RaggedMmap
    out_dir.mkdir(parents=True, exist_ok=True)
    RaggedMmap.from_generator(out_dir=str(out_dir / name), sample_generator=gen, batch_size=batch, verbose=False)


def build(root, args, progress):
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
    sets = features._clips(pdir, held, synth, exclude_near)
    h = hashlib.sha1()
    h.update(json.dumps(recipe, sort_keys=True).encode())
    h.update(json.dumps({n: sorted(p for p, _ in v) for n, v in sets.items()}, sort_keys=True).encode())
    h.update(f"mww k={k} holdout={use_holdout} slides={TRAIN_SLIDES} step={STEP_MS} v2".encode())
    key = h.hexdigest()[:12]
    out = pdir / 'features' / 'mww' / key
    meta_path = out / 'meta.json'
    meta = read_json(meta_path)                         # a half-written meta (full disk, a kill) means: build again
    if meta and not args.get('rebuild'):
        progress.done({'key': key, 'reused': True, **meta})
        return key
    progress.step('mww', 'fetching the microWakeWord source' if not (Path(root) / 'tools' / f"microWakeWord-{MWW_SHA}").exists() else 'microWakeWord source on disk')
    src = source_dir(root)
    import sys
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    out.mkdir(parents=True, exist_ok=True)
    workers = max(2, min(12, (os.cpu_count() or 4) - 2))
    t0 = time.time()
    counts = {}

    def spec_rows(items, vary, slides, name):
        """Yield spectrograms for a set, with the training slides, reporting progress."""
        jobs = [(wav, (k if vary else 1) * rep, seed * 7919 + i, vary) for i, (wav, rep) in enumerate(items)]
        n = 0
        with Pool(workers, initializer=_pool_init, initargs=(str(root), str(pdir), recipe, seed, str(src))) as pool:
            for specs in pool.imap_unordered(_vary_spec, jobs, chunksize=8):
                for spec in specs:
                    if slides > 1 and spec.shape[0] > slides + 20:
                        for s in range(slides):
                            yield spec[: spec.shape[0] - s]
                            n += 1
                    else:
                        yield spec
                        n += 1
                if n % 500 < len(specs) * slides:
                    progress.update(min(95, 100.0 * n / max(1, len(jobs) * (k if vary else 1) * slides)), f"{name}: {n:,} spectrograms · {time.time() - t0:.0f}s")
        counts[name] = n

    progress.step('mww', f"positives: {len(sets['positive']):,} clips × {k} variations × {TRAIN_SLIDES} slides")
    _write(out / 'positive' / 'training', 'wakeword_mmap', spec_rows(sets['positive'], True, TRAIN_SLIDES, 'positive/training'))
    if sets['val_positive']:
        _write(out / 'positive' / 'validation', 'wakeword_mmap', spec_rows(sets['val_positive'], False, 1, 'positive/validation'))
        _write(out / 'positive' / 'testing', 'wakeword_mmap', spec_rows(sets['val_positive'], False, 1, 'positive/testing'))
    progress.step('mww', f"sound-alikes and other negatives: {len(sets['adversarial']):,} clips")
    _write(out / 'adversarial' / 'training', 'adversarial_mmap', spec_rows(sets['adversarial'], True, 1, 'adversarial/training'))
    if sets['val_negative']:
        _write(out / 'adversarial' / 'validation', 'adversarial_mmap', spec_rows(sets['val_negative'], False, 1, 'adversarial/validation'))
        _write(out / 'adversarial' / 'testing', 'adversarial_mmap', spec_rows(sets['val_negative'], False, 1, 'adversarial/testing'))
    progress.step('mww', f"your room: {len(sets['ambient'])} training take(s), {len(sets['val_ambient'])} held out")
    room_train = [s for s in (_room_spec(w) for w, _ in sets['ambient']) if s is not None and len(s) > 200]
    if room_train:
        _write(out / 'room' / 'training', 'room_mmap', iter(room_train))
    room_val = [s for s in (_room_spec(w) for w, _ in sets['val_ambient']) if s is not None and len(s) > 200]
    if room_val:
        _write(out / 'room' / 'validation_ambient', 'room_mmap', iter(room_val))
        _write(out / 'room' / 'testing_ambient', 'room_mmap', iter(room_val))
    counts['room_training_seconds'] = int(sum(len(s) for s in room_train) * STEP_MS / 1000)
    counts['room_heldout_seconds'] = int(sum(len(s) for s in room_val) * STEP_MS / 1000)
    meta = {'key': key, 'trainer': 'mww', 'rounds': k, 'holdout': use_holdout, 'recipe': recipe.get('preset'), 'synth': synth, 'exclude_near': exclude_near, 'slides': TRAIN_SLIDES,
            'counts': counts, 'seconds': round(time.time() - t0), 'when': time.time(), 'holdout_when': (holdout.read(pdir) or {}).get('when'),
            'source': str(src)}
    write_json(meta_path, meta, indent=1)
    features.prune_sets(pdir, 'mww')
    progress.done(meta)
    return key


def latest(pdir):
    d = pdir / 'features' / 'mww'
    if not d.is_dir():
        return None
    best = None
    for sub in d.iterdir():
        meta = read_json(sub / 'meta.json')
        if meta:
            if best is None or meta.get('when', 0) > best.get('when', 0):
                best = {**meta, 'dir': str(sub)}
    return best
