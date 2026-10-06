# synth.py - the phrase in thousands of voices. Piper on the CPU (its multi-speaker voices carry the diversity:
# libritts_r alone is 904 readers), Kokoro on the GPU (28 English voices, blends of two make new ones, the
# other-language packs speak English with an accent), both at once on two threads. Every clip gets speed and
# voice-noise jitter drawn for it alone; the room, the mic and the distance come later from augmentation.
# Idempotent: the target is the total number of synthesized positives; a rerun makes only the difference.
import math
import os
import random
import threading
import time
from pathlib import Path

import numpy as np

from . import compat, negatives, paths, store, voices
from .paths import project_dir

VARIATION = {   # length/speed range, piper noise_scale range, piper noise_w range, kokoro speed range
    'low': ((0.95, 1.05), (0.60, 0.72), (0.75, 0.85), (0.95, 1.05)),
    'normal': ((0.80, 1.25), (0.50, 0.90), (0.60, 1.00), (0.80, 1.25)),
    'high': ((0.70, 1.40), (0.40, 1.00), (0.50, 1.20), (0.70, 1.40)),
}
NEG_SHARE = 0.10       # of the positive target, spread over the user's own sound-alike phrases
NEG_MIN = 30


def _device(args):
    want = str(args.get('device') or 'auto')
    if want == 'cpu':
        return 'cpu'
    try:
        import torch
        if torch.cuda.is_available():
            return 'cuda'
    except Exception:
        pass
    return 'cpu'


def _level(audio):
    """Keep it a natural level: never past 0.95, never a whisper."""
    peak = float(np.abs(audio).max()) if len(audio) else 0.0
    if peak <= 1e-5:
        return audio
    if peak > 0.95:
        audio = audio * (0.95 / peak)
    elif peak < 0.2:
        audio = audio * (0.5 / peak)
    return audio.astype(np.float32)


def _finish(audio, sr):
    audio = store.to_16k(np.asarray(audio, dtype=np.float32), sr)
    audio = store.trim(audio, thresh_db=-45.0, pad_ms=120)
    return _level(audio)


# --- the plan: who says what, how many times -------------------------------------------------------------

def _allocate(plan, n_total, rng):
    """[(engine, voice, n)] for n_total clips. Piper voices weigh by speakers**0.6 so the big ones carry the set
    without drowning the single voices; Kokoro voices share their part equally."""
    piper_names = [v for v in plan.get('piper', []) if any(v == p[0] for p in voices.PIPER_EN)]
    kok = list(plan.get('kokoro', []))
    if plan.get('kokoro_other'):
        kok += [v for v in (plan.get('kokoro_other_voices') or voices.KOKORO_OTHER) if v in voices.KOKORO_OTHER]
    share = float(plan.get('share_kokoro', 0.35)) if kok else 0.0
    if not piper_names:
        share = 1.0 if kok else 0.0
    n_k = int(round(n_total * share))
    n_p = n_total - n_k
    out = []
    if piper_names and n_p:
        spk = {p[0]: p[1] for p in voices.PIPER_EN}
        w = np.array([spk[v] ** 0.6 for v in piper_names], dtype=float)
        counts = np.floor(w / w.sum() * n_p).astype(int)
        for i in rng.sample(range(len(piper_names)), int(n_p - counts.sum())):
            counts[i] += 1
        out += [('piper', v, int(c)) for v, c in zip(piper_names, counts) if c]
    if kok and n_k:
        base, extra = divmod(n_k, len(kok))
        out += [('kokoro', v, base + (1 if i < extra else 0)) for i, v in enumerate(kok) if base + (1 if i < extra else 0)]
    return out


# --- piper -------------------------------------------------------------------------------------------

class _Piper:
    def __init__(self, voices_dir, threads, progress):
        self.dir = Path(voices_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.threads = threads
        self.progress = progress
        self._cache = {}

    def voice(self, name):
        v = self._cache.get(name)
        if v is not None:
            return v
        from piper import PiperVoice
        import onnxruntime as ort
        mp = self.dir / f"{name}.onnx"
        if not mp.exists() or not (self.dir / f"{name}.onnx.json").exists():
            self.progress.step('voices', f"downloading piper voice {name}")
            from piper.download_voices import download_voice
            download_voice(name, self.dir)
        v = PiperVoice.load(str(mp))
        so = ort.SessionOptions()
        so.intra_op_num_threads = self.threads
        so.inter_op_num_threads = 1
        v.session = ort.InferenceSession(str(mp), sess_options=so, providers=['CPUExecutionProvider'])
        self._cache.clear()          # one voice in memory at a time: the big ones are 75 MB and allocation is per voice
        self._cache[name] = v
        return v

    def say(self, name, text, speaker, length_scale, noise_scale, noise_w, rng):
        from piper import SynthesisConfig
        v = self.voice(name)
        ids_key = (name, text)
        ids = getattr(self, '_ids', {}).get(ids_key)
        if ids is None:
            phon = v.phonemize(text)
            ids = v.phonemes_to_ids(phon[0]) if phon else []
            self._ids = {ids_key: ids}
        cfg = SynthesisConfig(speaker_id=speaker, length_scale=length_scale, noise_scale=noise_scale, noise_w_scale=noise_w,
                              normalize_audio=True)
        audio = v.phoneme_ids_to_audio(ids, syn_config=cfg)
        if isinstance(audio, tuple):
            audio = audio[0]
        return np.asarray(audio, dtype=np.float32).reshape(-1), v.config.sample_rate

    def speakers(self, name):
        return int(self.voice(name).config.num_speakers or 1)


# --- kokoro ----------------------------------------------------------------------------------------------

class _Kokoro:
    def __init__(self, device, progress):
        self.progress = progress
        self.device = device
        self.pipe = None
        self._packs = {}

    def _ready(self):
        if self.pipe is None:
            self.progress.step('voices', 'loading Kokoro')
            from kokoro import KPipeline
            self.pipe = KPipeline(lang_code='a', device=self.device)

    def pack(self, name):
        self._ready()
        if name not in self._packs:
            self._packs[name] = self.pipe.load_voice(name)
        return self._packs[name]

    def say(self, name, text, speed, blend=None):
        self._ready()
        voice = self.pack(name)
        if blend:
            other, w = blend
            voice = voice * (1 - w) + self.pack(other) * w
        pieces = [np.asarray(r.audio, dtype=np.float32) for r in self.pipe(text, voice=voice, speed=speed) if r.audio is not None]
        if not pieces:
            return None, 24000
        return np.concatenate(pieces), 24000


def _allowed(plan):
    kok = set(plan.get('kokoro', []))
    if plan.get('kokoro_other'):
        kok |= {v for v in (plan.get('kokoro_other_voices') or voices.KOKORO_OTHER) if v in voices.KOKORO_OTHER}
    return set(plan.get('piper', [])), kok, bool(plan.get('blends', True))


def _prune(pdir, plan, progress):
    """The set equals the plan: synthetic clips from voices no longer ticked (or blends when blending is off) go."""
    piper_ok, kok_ok, blends_ok = _allowed(plan)
    gone = 0
    for collection in ('positive/synth', 'negative/synth'):
        rows, _ = store.list_clips(pdir, collection, 0, 10 ** 7)
        for side in rows:
            v, eng = side.get('voice'), side.get('engine')
            keep = (v in piper_ok) if eng == 'piper' else (v in kok_ok)
            if keep and eng == 'kokoro' and side.get('blend'):
                keep = blends_ok and side['blend'].split('@')[0] in kok_ok
            if not keep:
                store.delete_clip(pdir, collection, side['id'])
                gone += 1
    if gone:
        progress.step('prune', f"removed {gone:,} clips from voices no longer in the plan")
    return gone


# --- the job --------------------------------------------------------------------------------------------

def run(root, args, progress):
    compat.apply()
    slug = args.get('project')
    project = store.load_project(root, slug)
    if not project:
        raise ValueError(f"no project {slug}")
    pdir = project_dir(root, slug)
    plan = {**voices.default_plan(), **(project.get('settings') or {}).get('synth', {})}
    rng = random.Random()
    preview = int(args.get('preview') or 0)
    device = _device(args)
    texts = [t for t in project.get('spellings') or [project['phrase']] if t.strip()] or [project['phrase']]

    have = store.counts(pdir)
    if preview:
        for side in store.list_clips(pdir, paths.PREVIEWS, 0, 1000)[0]:      # the row shows this preview only
            store.delete_clip(pdir, paths.PREVIEWS, side['id'])
        work = [(paths.PREVIEWS, texts, preview)]
    else:
        pruned = _prune(pdir, plan, progress)
        have = store.counts(pdir)
        target = int(plan.get('target') or 20000)
        work = [('positive/synth', texts, max(0, target - have.get('positive/synth', 0)))]
        negs = [t for t in project.get('negatives') or [] if t.strip()]
        if project.get('negatives_auto') is None:               # first time: find them; after that the user's list stands
            auto = negatives.sound_alikes(project['phrase'], texts, n=40)
            fresh = store.load_project(root, slug) or project     # the page may have edited spellings meanwhile
            fresh['negatives_auto'] = auto
            store.save_project(pdir, fresh)
        else:
            auto = list(project.get('negatives_auto') or [])
        negs = list(dict.fromkeys(negs + auto))
        if negs:
            per = max(NEG_MIN, int(target * NEG_SHARE / len(negs)))
            want = per * len(negs) - have.get('negative/synth', 0)
            if want > 0:
                work.append(('negative/synth', negs, want))
    total = sum(n for _, _, n in work)
    if total <= 0:
        progress.done({'made': 0, 'note': 'nothing to make: the set is already at its target'})
        return

    threads = max(2, min(8, (os.cpu_count() or 4) // 2))
    piper = _Piper(paths.voices_dir(root), threads, progress)
    kokoro = _Kokoro(device, progress)
    lr, nr, wr, kr = VARIATION.get(plan.get('variation', 'normal'), VARIATION['normal'])
    made = {'piper': 0, 'kokoro': 0, 'failed': 0}
    lock = threading.Lock()
    stop = threading.Event()
    t0 = time.time()

    def tick(engine, ok=True):
        with lock:
            made[engine if ok else 'failed'] += 1
            n = made['piper'] + made['kokoro']
        rate = n / max(1e-6, time.time() - t0)
        left = (total - n) / rate if rate > 0 else 0
        progress.update(100.0 * n / total, f"{n:,} of {total:,} clips · {rate * 60:,.0f}/min · {left / 60:.0f} min left",
                        piper=made['piper'], kokoro=made['kokoro'], failed=made['failed'])

    def piper_items(collection, text_list, alloc):
        for engine, name, n in alloc:
            if engine != 'piper':
                continue
            try:
                n_spk = piper.speakers(name)
            except Exception as e:
                progress.step('synth', f"piper voice {name} unusable: {e}")
                with lock:
                    made['failed'] += n
                continue
            for _ in range(n):
                if stop.is_set():
                    return
                text = rng.choice(text_list)
                spk = rng.randrange(n_spk) if n_spk > 1 else None
                ls, ns, nw = rng.uniform(*lr), rng.uniform(*nr), rng.uniform(*wr)
                try:
                    audio, sr = piper.say(name, text, spk, ls, ns, nw, rng)
                    audio = _finish(audio, sr)
                    if len(audio) < paths.SAMPLE_RATE * 0.3:
                        raise ValueError('came out empty')
                    store.add_clip(pdir, collection, audio, {'source': 'synth', 'engine': 'piper', 'voice': name, 'speaker': spk,
                                                             'text': text, 'speed': round(1 / ls, 2), 'noise': round(ns, 2),
                                                             'preview': bool(preview) or None})
                    tick('piper')
                except Exception as e:
                    tick('piper', ok=False)
                    if made['failed'] <= 3:
                        progress.step('synth', f"piper {name}: {e}")

    def kokoro_items(collection, text_list, alloc):
        kok_names = [name for engine, name, _ in alloc if engine == 'kokoro']
        for engine, name, n in alloc:
            if engine != 'kokoro':
                continue
            for _ in range(n):
                if stop.is_set():
                    return
                text = rng.choice(text_list)
                speed = rng.uniform(*kr)
                blend = None
                if plan.get('blends') and len(kok_names) > 1 and rng.random() < 0.35:
                    other = rng.choice([k for k in kok_names if k != name])
                    blend = (other, round(rng.uniform(0.3, 0.6), 2))
                try:
                    audio, sr = kokoro.say(name, text, speed, blend)
                    if audio is None:
                        raise ValueError('came out empty')
                    audio = _finish(audio, sr)
                    if len(audio) < paths.SAMPLE_RATE * 0.3:
                        raise ValueError('came out empty')
                    store.add_clip(pdir, collection, audio, {'source': 'synth', 'engine': 'kokoro', 'voice': name,
                                                             'blend': f"{blend[0]}@{blend[1]}" if blend else None, 'text': text,
                                                             'speed': round(speed, 2), 'preview': bool(preview) or None})
                    tick('kokoro')
                except Exception as e:
                    tick('kokoro', ok=False)
                    if made['failed'] <= 3:
                        progress.step('synth', f"kokoro {name}: {e}")

    progress.step('synth', f"making {total:,} clips on {device} ({threads} piper threads)")
    for collection, text_list, n in work:
        if n <= 0:
            continue
        alloc = _allocate(plan, n, rng)
        if preview:   # a taste of each engine
            alloc = _allocate({**plan, 'share_kokoro': 0.5 if plan.get('kokoro') else 0.0}, n, rng)
        errors = []

        def guard(fn):
            try:
                fn(collection, text_list, alloc)
            except Exception as e:
                errors.append(e)
                stop.set()
        t_p = threading.Thread(target=guard, args=(piper_items,), name='wwm-piper', daemon=True)
        t_p.start()
        guard(kokoro_items)
        t_p.join()
        if errors:
            raise errors[0]
    progress.done({'made': made['piper'] + made['kokoro'], **made, 'seconds': round(time.time() - t0),
                   'pruned': 0 if preview else pruned, 'counts': store.counts(pdir)})
