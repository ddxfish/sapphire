# holdout.py - the slice that judges: about 15% of your own positives (spread over mics and styles), of your own
# negatives (near misses and ordinary apart), and of the room takes (whole takes), chosen once per wake word and
# frozen in <project>/holdout.json. Never trained on, never varied, never used as background. Test grades on it.
import json
import random
import time
from collections import defaultdict
from pathlib import Path

from . import store
from .paths import write_json

FRACTION = 0.15


def path(pdir):
    return Path(pdir) / 'holdout.json'


def read(pdir):
    try:
        return json.loads(path(pdir).read_text(encoding='utf-8'))
    except Exception:
        return None


def _stratified(rows, key, frac, rng):
    """At least one from every group that has three or more, about frac of each overall."""
    groups = defaultdict(list)
    for r in rows:
        groups[key(r)].append(r['id'])
    picked = []
    for ids in groups.values():
        rng.shuffle(ids)
        n = round(len(ids) * frac)
        if len(ids) >= 3:
            n = max(1, n)
        picked += ids[:n]
    return sorted(picked)


def make(pdir, frac=FRACTION, seed=None):
    rng = random.Random(seed)
    ok = lambda r: r.get('verdict') != 'drop'          # a clip the Check stage dropped must not judge anyone
    pos = [r for c in ('positive/recorded', 'positive/uploaded') for r in store.list_clips(pdir, c, 0, 10 ** 7)[0] if ok(r)]
    neg = [r for c in ('negative/recorded', 'negative/uploaded') for r in store.list_clips(pdir, c, 0, 10 ** 7)[0] if ok(r)]
    amb = [r for c in ('ambient/recorded', 'ambient/uploaded') for r in store.list_clips(pdir, c, 0, 10 ** 7)[0]]
    out = {
        'when': time.time(), 'fraction': frac,
        'positive': _stratified(pos, lambda r: (r.get('device'), r.get('style') or 'normal'), frac, rng),
        'near': _stratified([r for r in neg if r.get('near')], lambda r: r.get('device'), frac, rng),
        'negative': _stratified([r for r in neg if not r.get('near')], lambda r: r.get('device'), frac, rng),
        'ambient': [],
    }
    # room takes: whole takes until about frac of the minutes, longest-first mixed so one giant take is not everything
    total = sum(float(r.get('seconds') or 0) for r in amb)
    rng.shuffle(amb)
    got = 0.0
    for r in amb:
        if total and got / total >= frac:
            break
        if len(amb) >= 2:
            out['ambient'].append(r['id'])
            got += float(r.get('seconds') or 0)
    out['ambient_seconds'] = round(got)
    out['counts'] = {'positive': [len(out['positive']), len(pos)], 'near': [len(out['near']), len([r for r in neg if r.get('near')])],
                     'negative': [len(out['negative']), len([r for r in neg if not r.get('near')])], 'ambient': [len(out['ambient']), len(amb)],
                     'ambient_minutes': [round(got / 60, 1), round(total / 60, 1)]}
    write_json(path(pdir), out, indent=1)
    return out


def ids(pdir):
    """Every held-out clip id, for the browsers and the trainers."""
    h = read(pdir)
    if not h:
        return set()
    return set(h.get('positive', []) + h.get('near', []) + h.get('negative', []) + h.get('ambient', []))


def key(pdir):
    """A short hash of the judging set as it stands: the held-out ids that are not dropped, and when they were drawn.
    A verdict change or a reshuffle changes it; a judge verdict carrying another key is stale."""
    import hashlib
    h = read(pdir)
    if not h:
        return None
    live = []
    for part, cols in (('positive', ('positive/recorded', 'positive/uploaded')), ('near', ('negative/recorded', 'negative/uploaded')),
                       ('negative', ('negative/recorded', 'negative/uploaded'))):
        for cid in h.get(part, []):
            for c in cols:
                sp = Path(pdir) / c / f"{cid}.json"
                if sp.is_file():
                    try:
                        side = json.loads(sp.read_text(encoding='utf-8'))
                    except Exception:
                        side = {}
                    if side.get('verdict') != 'drop':
                        live.append(f"{part}:{cid}")
                    break
    return hashlib.sha1((json.dumps(sorted(live)) + str(h.get('when'))).encode()).hexdigest()[:12]
