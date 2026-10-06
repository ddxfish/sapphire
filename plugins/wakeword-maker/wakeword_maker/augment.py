# augment.py - the Variations recipe: what happens to a clip on its way into training. Rooms (real impulse responses
# when the MIT set is here, synthetic ones until then), background sound (your own room recordings, the music set,
# coloured noise), pitch, speed, EQ, a notch, distortion, level, and "next room" (muffled, quiet, echoing).
# Both trainers draw a fresh variation for every clip on every pass; the page previews a draw so you can hear it.
import glob
import math
import os
import random
from pathlib import Path

import numpy as np

from . import catalog, paths

SR = paths.SAMPLE_RATE

NORM_PEAK = 0.125       # -18 dBFS: every clip is brought here before any variation, so a hot synthetic clip and a
                        # quiet real one start equal and the Level step treats them alike
DEFAULT = {
    'preset': 'natural',
    'room':       {'on': True, 'p': 0.5},
    'background': {'on': True, 'p': 0.7, 'snr': [0, 18], 'ambient': True, 'music': True, 'noise': True},
    'pitch':      {'on': True, 'p': 0.25, 'semitones': 3},
    'speed':      {'on': True, 'p': 0.25, 'range': [0.9, 1.1]},
    'eq':         {'on': True, 'p': 0.25, 'db': 6},
    'notch':      {'on': True, 'p': 0.15},
    'distortion': {'on': True, 'p': 0.1, 'max': 0.08},
    'level':      {'on': True, 'range': [-24, 0], 'norm': True},
    'far':        {'on': True, 'p': 0.2},
}
# the slider's four stops; "custom" is any of them with a row touched
PRESETS = {
    'gentle': {**DEFAULT, 'preset': 'gentle',
               'room': {'on': True, 'p': 0.3}, 'background': {'on': True, 'p': 0.5, 'snr': [6, 22], 'ambient': True, 'music': True, 'noise': True},
               'pitch': {'on': True, 'p': 0.15, 'semitones': 2}, 'speed': {'on': True, 'p': 0.15, 'range': [0.95, 1.05]},
               'eq': {'on': True, 'p': 0.15, 'db': 4}, 'notch': {'on': True, 'p': 0.05}, 'distortion': {'on': False, 'p': 0.05, 'max': 0.04},
               'level': {'on': True, 'range': [-15, 0], 'norm': True}, 'far': {'on': True, 'p': 0.1}},
    'natural': DEFAULT,
    'rough': {**DEFAULT, 'preset': 'rough',
              'room': {'on': True, 'p': 0.7}, 'background': {'on': True, 'p': 0.85, 'snr': [-5, 12], 'ambient': True, 'music': True, 'noise': True},
              'pitch': {'on': True, 'p': 0.35, 'semitones': 4}, 'speed': {'on': True, 'p': 0.35, 'range': [0.85, 1.15]},
              'eq': {'on': True, 'p': 0.35, 'db': 8}, 'notch': {'on': True, 'p': 0.25}, 'distortion': {'on': True, 'p': 0.2, 'max': 0.15},
              'level': {'on': True, 'range': [-30, 0], 'norm': True}, 'far': {'on': True, 'p': 0.3}},
    'off': {k: ({'on': False, **v} if isinstance(v, dict) else v) for k, v in {**DEFAULT, 'preset': 'off'}.items()},
}


def _mid(a, b):
    """Halfway between two recipe rows: numbers and ranges averaged, a step that is on in either stays on."""
    if isinstance(a, dict):
        return {k: _mid(a[k], b[k]) for k in a}
    if isinstance(a, list):
        return [round((x + y) / 2, 3) for x, y in zip(a, b)]
    if isinstance(a, bool):
        return a or b
    if isinstance(a, (int, float)):
        return round((a + b) / 2, 3)
    return a


# the first 18-run spread (2026-10-05) put every good model between gentle and rough and nothing near harsh, so the
# slider now spends its stops on that stretch: two new ones halfway, harsh gone
PRESETS['mild'] = {**_mid(PRESETS['gentle'], DEFAULT), 'preset': 'mild'}
PRESETS['firm'] = {**_mid(DEFAULT, PRESETS['rough']), 'preset': 'firm'}
STOPS = ['gentle', 'mild', 'natural', 'firm', 'rough']
OLD_STOPS = {'harsh': 'rough'}
GUARD_BELOW_FLOOR = 4.0 # a draw may land this far under the recipe's own background floor before it is redrawn
MUSIC_ABOVE = 3.0       # music sits this much higher than the room floor; broadband static higher still
STATIC_ABOVE = 8.0
REJECT_TAME_AT = 0.15   # above this share of rejected draws the background floor is raised 3 dB and said so
MAX_REDRAWS = 3


def recipe_of(project):
    """The project's recipe with every key present."""
    saved = (project.get('settings') or {}).get('augment') or {}
    base = PRESETS.get(OLD_STOPS.get(saved.get('preset'), saved.get('preset', 'natural')), DEFAULT)
    out = {}
    for k, v in base.items():
        out[k] = {**v, **saved.get(k, {})} if isinstance(v, dict) else saved.get(k, v)
    out['preset'] = OLD_STOPS.get(out.get('preset'), out.get('preset'))
    return out


def pools(root, pdir):
    """Where the sound comes from. Missing pools are reported, not invented."""
    out = {'rir': [], 'ambient': [], 'music': [], 'missing': []}
    rdir = catalog.dataset_dir(root, 'rirs')
    if catalog.receipt(root, 'rirs'):
        out['rir'] = sorted(glob.glob(str(rdir / '**' / '*.wav'), recursive=True))
    if not out['rir']:
        out['missing'].append('rirs')
    if catalog.receipt(root, 'music'):
        mdir = catalog.dataset_dir(root, 'music')
        out['music'] = sorted(f for ext in ('wav', 'mp3', 'flac', 'ogg') for f in glob.glob(str(mdir / '**' / f'*.{ext}'), recursive=True))
    if not out['music']:
        out['missing'].append('music')
    for c in ('ambient/recorded', 'ambient/uploaded'):
        d = Path(pdir) / c
        if d.is_dir():
            out['ambient'] += sorted(str(p) for p in d.glob('*.wav'))
    return out


def _synthetic_rir(rng, seconds=None):
    """A room made of decaying noise: early reflections then a tail. Crude, varied, better than nothing."""
    seconds = seconds or rng.uniform(0.15, 0.9)
    n = int(SR * seconds)
    t = np.arange(n) / SR
    tail = np.random.default_rng(rng.getrandbits(32)).standard_normal(n).astype(np.float32) * np.exp(-6.9 * t / seconds)
    rir = np.zeros(n, np.float32)
    rir[0] = 1.0
    for _ in range(rng.randrange(2, 6)):
        at = rng.randrange(int(SR * 0.003), int(SR * 0.04))
        rir[at] += rng.uniform(0.2, 0.6) * rng.choice([-1, 1])
    rir += tail * rng.uniform(0.05, 0.25)
    return rir / (np.abs(rir).max() + 1e-9)


class Augmenter:
    """Build once per recipe; apply(audio, rng) returns a new array (length may change with speed)."""

    def __init__(self, root, pdir, recipe, seed=None):
        import audiomentations as A
        self.recipe = recipe
        self.pools = pools(root, pdir)
        self.rng = random.Random(seed)
        r = recipe
        self.room = None
        if r['room']['on'] and self.pools['rir']:
            self.room = A.ApplyImpulseResponse(ir_path=self.pools['rir'], p=1.0, leave_length_unchanged=True)
        self.backgrounds = []
        self.snr = list(r['background'].get('snr', [-5, 15]))
        self.stats = {'draws': 0, 'rejected': 0, 'tamed': 0, 'snr': []}
        self._build_backgrounds()
        self.pitch = A.PitchShift(min_semitones=-r['pitch']['semitones'], max_semitones=r['pitch']['semitones'], p=1.0) if r['pitch']['on'] else None
        self.speed = A.TimeStretch(min_rate=r['speed']['range'][0], max_rate=r['speed']['range'][1], leave_length_unchanged=False, p=1.0) if r['speed']['on'] else None
        self.eq = A.SevenBandParametricEQ(min_gain_db=-r['eq']['db'], max_gain_db=r['eq']['db'], p=1.0) if r['eq']['on'] else None
        self.notch = A.BandStopFilter(min_center_freq=300, max_center_freq=3000, min_bandwidth_fraction=0.2, max_bandwidth_fraction=0.6, p=1.0) if r['notch']['on'] else None
        dmax = float(r['distortion'].get('max', 0.08))
        self.distortion = A.TanhDistortion(min_distortion=min(0.005, dmax), max_distortion=max(0.01, dmax), p=1.0) if r['distortion']['on'] else None
        self.far_lp = A.LowPassFilter(min_cutoff_freq=1200, max_cutoff_freq=3500, p=1.0)

    def _build_backgrounds(self):
        import audiomentations as A
        b = self.recipe['background']
        self.backgrounds = []
        if not b['on']:
            return
        lo, hi = self.snr
        if b.get('ambient') and self.pools['ambient']:
            amb = A.AddBackgroundNoise(sounds_path=self.pools['ambient'], min_snr_db=lo, max_snr_db=hi, p=1.0)
            self.backgrounds += [amb, amb]                 # your own room, drawn twice as often as the canned sources
        if b.get('music') and self.pools['music']:
            self.backgrounds.append(A.AddBackgroundNoise(sounds_path=self.pools['music'], min_snr_db=lo + MUSIC_ABOVE, max_snr_db=hi + MUSIC_ABOVE, p=1.0))
        if b.get('noise'):                                 # static masks speech far worse than music at the same number
            self.backgrounds.append(A.AddColorNoise(min_snr_db=lo + STATIC_ABOVE, max_snr_db=hi + STATIC_ABOVE + 6, p=1.0))

    def _tame(self):
        """Too many draws drowned the voice: raise the background floor 3 dB and build the sources again."""
        self.snr = [self.snr[0] + 3, max(self.snr[1], self.snr[0] + 6)]
        self.stats['tamed'] += 1
        self.stats['rejected'] = 0
        self.stats['draws'] = 0
        self._build_backgrounds()

    def apply(self, audio, rng=None, want=None, seed=None):
        """One variation. want: step names to force (the preview's 'hear just this one'); else the recipe's chances.
        seed: makes the draw repeatable (the dry run's harshest survivors). Returns (audio, applied steps)."""
        if seed is not None:
            rng = random.Random(seed)
            np.random.seed(int(seed) & 0xFFFFFFFF)
            random.seed(int(seed))
        rng = rng or self.rng
        for attempt in range(MAX_REDRAWS + 1):
            out, applied, ok = self._draw(audio, rng, want, mild=attempt == MAX_REDRAWS)
            if ok:
                return out, applied
            self.stats['rejected'] += 1
            if self.stats['draws'] >= 200 and self.stats['rejected'] / max(1, self.stats['draws']) > REJECT_TAME_AT:
                self._tame()
        return out, applied

    def _draw(self, audio, rng, want, mild=False):
        r = self.recipe
        self.stats['draws'] += 1
        x = np.asarray(audio, dtype=np.float32).copy()
        if r['level'].get('norm', True):
            peak = float(np.abs(x).max()) if len(x) else 0.0
            if peak > 1e-5:
                x = x * (NORM_PEAK / peak)
        on = (lambda k: k in want) if want else (lambda k: r[k]['on'] and rng.random() < r[k].get('p', 1.0))
        if mild:                                           # the last resort after redraws: room or nothing
            on = lambda k: k == 'level'
        applied = []
        # order: pitch/speed on the dry voice, then the room, then what the mic hears
        if self.pitch and on('pitch'):
            x = self.pitch(samples=x, sample_rate=SR); applied.append('pitch')
        if self.speed and on('speed'):
            x = self.speed(samples=x, sample_rate=SR); applied.append('speed')
        far = r['far']['on'] and on('far')
        if far or (r['room']['on'] and on('room')):
            if self.room and not (far and rng.random() < 0.3):
                x = self.room(samples=x, sample_rate=SR)
            else:
                rir = _synthetic_rir(rng, seconds=rng.uniform(0.4, 1.2) if far else None)
                x = np.convolve(x, rir)[: len(x)].astype(np.float32)
            applied.append('far' if far else 'room')
        if far:
            x = self.far_lp(samples=x, sample_rate=SR)
            x = x * 10 ** (rng.uniform(-20, -8) / 20)
        if self.eq and on('eq'):
            x = self.eq(samples=x, sample_rate=SR); applied.append('eq')
        if self.notch and on('notch'):
            x = self.notch(samples=x, sample_rate=SR); applied.append('notch')
        if self.distortion and on('distortion'):
            x = self.distortion(samples=x, sample_rate=SR); applied.append('distortion')
        snr_db = None
        if self.backgrounds and on('background'):
            dry = x
            x = rng.choice(self.backgrounds)(samples=x, sample_rate=SR); applied.append('background')
            n = min(len(dry), len(x))
            noise = x[:n] - dry[:n]
            v = float(np.sqrt((dry[:n] ** 2).mean()) + 1e-9)
            nz = float(np.sqrt((noise ** 2).mean()) + 1e-9)
            snr_db = 20 * math.log10(v / nz)
            self.stats['snr'].append(snr_db)
            if len(self.stats['snr']) > 2000:
                del self.stats['snr'][:1000]
        if r['level']['on'] and (not want or 'level' in want):
            lo, hi = r['level']['range']
            x = x * 10 ** (rng.uniform(lo, hi) / 20); applied.append('level')
        peak = float(np.abs(x).max()) if len(x) else 0.0
        if peak > 1.0:
            x = x / peak
        secs = len(x) / SR
        ok = (snr_db is None or snr_db >= self.snr[0] - GUARD_BELOW_FLOOR) and (0.3 <= secs <= 3.5 or len(audio) / SR > 3.0)
        if want:
            ok = True                                      # the ear asked for this step on purpose
        return x.astype(np.float32), applied, ok
