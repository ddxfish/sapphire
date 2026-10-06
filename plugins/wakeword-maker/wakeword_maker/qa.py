# qa.py - rate every clip. A positive must say the phrase (Whisper hears it, the transcript is compared with
# the spellings) and be a usable recording (length, peak, not clipped, not a whisper). A negative must NOT say
# the phrase. Score 0..100 in the sidecar under `qa`, a verdict (keep/drop) the user can overrule from the page,
# and two files for the Check tab: qa/summary.json and qa/worst.json.
import difflib
import json
import os
import re
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from . import compat, paths, store
from .paths import write_json
from .paths import project_dir

RATE = ('positive/synth', 'positive/recorded', 'positive/uploaded', 'negative/synth', 'negative/recorded',
        'negative/uploaded', 'negative/mined')
WORST = 60


def _norm(t):
    return ' '.join(re.sub(r"[^\w\s']", ' ', str(t or '').lower()).split())


def _similar(said, wanted):
    said = _norm(said)
    best = 0.0
    for w in wanted:
        w = _norm(w)
        if not said or not w:
            continue
        if w in said:
            return 1.0
        best = max(best, difflib.SequenceMatcher(None, said, w).ratio())
    return best


def _says_phrase(said, wanted):
    """For a negative: does the transcript carry the whole phrase, word for word, in order? Sound-alikes are close
    by design ("they marcus" is one letter from "hey marcus"), so only the real words count."""
    said_words = _norm(said).split()
    for w in wanted:
        words = _norm(w).split()
        if not words or len(words) > len(said_words):
            continue
        for i in range(len(said_words) - len(words) + 1):
            if said_words[i:i + len(words)] == words:
                return True
    return False


def _syllables(text):
    """A rough count: vowel groups. 'hey marcus' -> 3."""
    return max(1, len(re.findall(r"[aeiouy]+", _norm(text))))


def _voiced_span(audio):
    """(first, last) second of the part within 20 dB of the loudest 10 ms frame, or None when there is nothing."""
    if len(audio) < 1600:
        return None
    frames = audio[: len(audio) // 160 * 160].reshape(-1, 160)
    db = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-9)
    hot = np.where(db > db.max() - 20)[0]
    if not len(hot):
        return None
    return hot[0] * 0.01, (hot[-1] + 1) * 0.01


def _audio_quality(side, audio, phrase=None):
    """0..40 and the reasons. Length, clipping, silence, how far the voice stands above the mic's noise, and for a
    phrase take: whether the voice starts at the very first sample (a cut word) or is too brief to hold the phrase.
    A whisper with a clean floor is a good sample; quiet is only a fault when the noise competes."""
    pts, why = 40, []
    sec = len(audio) / paths.SAMPLE_RATE
    peak = float(np.abs(audio).max()) if len(audio) else 0.0
    floor = store.floor_db(audio)
    if phrase and str(side.get('collection', '')).startswith('positive/') and side.get('source') != 'synth':
        span = _voiced_span(audio)
        if span:
            if span[0] < 0.03:
                pts -= 20; why.append('starts mid-word (the beginning was cut off)')
            if span[1] - span[0] < 0.13 * _syllables(phrase):
                pts -= 20; why.append('too brief to hold the phrase')
    snr = (20 * np.log10(max(peak, 1e-6)) - floor) if floor is not None else None
    long_ok = str(side.get('collection', '')).startswith('negative/') and not side.get('near') and side.get('source') != 'synth'
    if sec < 0.4:
        pts -= 20; why.append('too short')
    elif sec > 3.0 and not long_ok:
        pts -= 10; why.append('long')
    if peak >= 0.999:
        pts -= 10; why.append('clipped')
    if peak < 0.01:
        pts -= 20; why.append('silent')
    elif snr is not None and snr < 15:
        pts -= 15; why.append('voice barely above the mic noise')
    elif snr is not None and snr < 20:
        pts -= 5; why.append('noisy')
    if sec >= 0.4:
        frames = audio[: len(audio) // 160 * 160].reshape(-1, 160)
        e = np.sqrt((frames ** 2).mean(axis=1) + 1e-12)
        if (e > e.max() * 0.1).mean() < 0.25:
            pts -= 5; why.append('mostly silence')
    return max(0, pts), why


class _Whisper:
    def __init__(self, model, device):
        from faster_whisper import WhisperModel
        kw = {'device': device, 'compute_type': 'float16' if device == 'cuda' else 'int8'}
        try:
            self.m = WhisperModel(model, **kw)
        except Exception:
            self.m = WhisperModel(model, device='cpu', compute_type='int8')

    def hear(self, path):
        segs, _info = self.m.transcribe(str(path), beam_size=1, language='en', vad_filter=False,
                                         condition_on_previous_text=False, without_timestamps=True)
        text, nsp = [], []
        for s in segs:
            text.append(s.text)
            nsp.append(float(getattr(s, 'no_speech_prob', 0.0) or 0.0))
        return ' '.join(text).strip(), (min(nsp) if nsp else 1.0)


def run(root, args, progress):
    compat.apply()
    slug = args.get('project')
    project = store.load_project(root, slug)
    if not project:
        raise ValueError(f"no project {slug}")
    pdir = project_dir(root, slug)
    bar = int(args.get('bar') or (project.get('settings') or {}).get('qa_bar') or 60)
    rerate = bool(args.get('rerate'))
    only_dropped = bool(args.get('only_dropped'))
    own_only = str(args.get('scope') or '') == 'own'
    wanted = [t for t in project.get('spellings') or [] if t.strip()] + [project['phrase']]
    device = 'cpu' if str(args.get('device')) == 'cpu' else 'cuda'
    if device == 'cuda':
        try:
            import torch
            if not torch.cuda.is_available():
                device = 'cpu'
        except Exception:
            device = 'cpu'

    todo = []
    for c in RATE:
        cdir = pdir / c
        if not cdir.is_dir() or (own_only and c.endswith('/synth')):
            continue
        for e in os.scandir(cdir):
            if not e.name.endswith('.json'):
                continue
            side = store.read_side(e.path)
            if not side:
                continue
            if only_dropped:
                if side.get('verdict') == 'drop' and side.get('verdict_by') != 'user':
                    todo.append((c, side))
            elif rerate or 'qa' not in side:
                todo.append((c, side))
    progress.step('rate', f"{len(todo):,} clips to rate" + (' (everything again)' if rerate else ''))
    whisper = None
    if todo:
        progress.step('whisper', f"loading whisper {args.get('whisper') or 'base.en'} on {device}")
        whisper = _Whisper(str(args.get('whisper') or 'base.en'), device)

    def settle(sp, side, qa, verdict):
        """Write the rating into the sidecar as it is NOW, not as it was when the list was made: a keep, drop or
        note the user set while this ran stays theirs, and a clip deleted meanwhile stays deleted."""
        now = store.read_side(sp)
        if not now:
            return
        now['qa'] = qa
        if now.get('verdict_by') != 'user':
            now['verdict'] = verdict
        write_json(sp, now)

    t0 = time.time()
    for i, (c, side) in enumerate(todo):
        wav, sp = store.clip_paths(pdir, c, side['id'])
        try:
            audio, sr = store.decode(wav)
            audio = store.to_16k(audio, sr)
        except Exception as e:
            settle(sp, side, {'score': 0, 'reasons': [f'unreadable: {e}'], 'when': time.time()}, 'drop')
            continue
        q, why = _audio_quality(side, audio, project['phrase'])
        said, nsp = whisper.hear(wav)
        sim = _similar(said, wanted)
        positive = c.startswith('positive/')
        if positive:
            score = q + round(60 * sim)
            if sim < 0.6:
                why.append('does not sound like the phrase' if sim < 0.35 else 'phrase unclear')
            if nsp > 0.6:
                score -= 10; why.append('no speech heard')
        else:
            score = int(q * 2.5)
            if _says_phrase(said, wanted):
                score = min(score, 10); why.append('says the phrase (a negative must not)')
        score = int(max(0, min(100, score)))
        settle(sp, side, {'score': score, 'transcript': said[:120], 'similarity': round(sim, 3), 'no_speech': round(nsp, 3),
                          'reasons': why, 'when': time.time()}, 'drop' if score < bar else 'keep')
        if i % 10 == 0:
            rate = (i + 1) / max(1e-6, time.time() - t0)
            progress.update(100.0 * (i + 1) / len(todo), f"{i + 1:,} of {len(todo):,} rated · {rate * 60:,.0f}/min")

    # summary over everything rated, not only this run
    progress.step('summary', 'writing the report')
    cols, vox, worst = {}, defaultdict(lambda: {'n': 0, 'sum': 0, 'dropped': 0}), []
    for c in RATE:
        cdir = pdir / c
        if not cdir.is_dir():
            continue
        rated = kept = dropped = 0
        scores, reasons = [], Counter()
        for e in os.scandir(cdir):
            if not e.name.endswith('.json'):
                continue
            side = store.read_side(e.path)
            if not side or 'qa' not in side:
                continue
            rated += 1
            s = side['qa'].get('score', 0)
            scores.append(s)
            if side.get('verdict') == 'drop':
                dropped += 1
                for r in side['qa'].get('reasons', []):
                    reasons[r] += 1
            else:
                kept += 1
            v = side.get('voice') or side.get('device') or side.get('source')
            if v:
                vox[v]['n'] += 1; vox[v]['sum'] += s; vox[v]['dropped'] += int(side.get('verdict') == 'drop')
            worst.append((s, c, side))
        if rated:
            cols[c] = {'rated': rated, 'kept': kept, 'dropped': dropped, 'mean_score': float(np.mean(scores)),
                       'reasons': reasons.most_common(6)}
    worst.sort(key=lambda x: x[0])
    qdir = pdir / 'qa'
    qdir.mkdir(exist_ok=True)
    write_json(qdir / 'summary.json', {
        'when': time.time(), 'bar': bar, 'rated_now': len(todo), 'collections': cols,
        'voices': {v: {'n': x['n'], 'mean_score': x['sum'] / x['n'], 'dropped': x['dropped']} for v, x in vox.items() if x['n'] >= 3},
    }, indent=1)
    write_json(qdir / 'worst.json', [{**side, 'collection': c} for _, c, side in worst[:WORST]])
    progress.done({'rated': len(todo), 'collections': cols})
