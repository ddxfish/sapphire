# holdout.py - the slice that judges: about 25% of your own positives (spread over mics and tags), of your own
# negatives (near misses and ordinary apart), and of the room takes (whole takes), chosen once per wake word and
# frozen in <project>/holdout.json. Never trained on, never varied, never used as background. Test grades on it.
# A take recorded "for the judge" (sidecar `judge`) is always in the slice, on top of the draw, from the moment it
# is recorded: the way to make a thin tag (ten takes from the next room) a judge instead of a tenth of one.
import json
import random
import threading
import time
from collections import defaultdict
from pathlib import Path

from . import store, tags
from .paths import write_json

FRACTION = 0.25
PARTS = {'positive': ('positive/recorded', 'positive/uploaded'), 'near': ('negative/recorded', 'negative/uploaded'),
         'negative': ('negative/recorded', 'negative/uploaded')}


def part_of(collection, near=False):
    """Which part of the slice a clip of this collection belongs to; None for a collection that is never held out."""
    if collection.startswith('positive/'):
        return 'positive'
    if collection.startswith('negative/'):
        return 'near' if near else 'negative'
    return None


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


_lock = threading.Lock()      # make and earmark both rewrite holdout.json: one at a time in this process


def make(pdir, frac=FRACTION, seed=None, root=None):
    with _lock:
        return _make(pdir, frac=frac, seed=seed, root=root)


def earmark(pdir, part, cid, on=True):
    with _lock:
        return _earmark(pdir, part, cid, on=on)


def _make(pdir, frac=FRACTION, seed=None, root=None):
    """With `root`, the shared room library's pieces (minus those dropped for this wake word) are in the draw too;
    room takes are held out whole, about `frac` of the minutes of EVERY microphone, so each mic has a judge."""
    rng = random.Random(seed)
    ok = lambda r: r.get('verdict') != 'drop'          # a clip the Check stage dropped must not judge anyone
    pos = [r for c in ('positive/recorded', 'positive/uploaded') for r in store.list_clips(pdir, c, 0, 10 ** 7)[0] if ok(r)]
    neg = [r for c in ('negative/recorded', 'negative/uploaded') for r in store.list_clips(pdir, c, 0, 10 ** 7)[0] if ok(r)]
    amb = [r for c in ('ambient/recorded', 'ambient/uploaded') for r in store.list_clips(pdir, c, 0, 10 ** 7)[0] if ok(r)]
    if root is not None:
        slug = (store.load_project_at(pdir) or {}).get('slug') or Path(pdir).name
        amb += store.list_room(root, slug=slug)
    marked = lambda rows: sorted(r['id'] for r in rows if r.get('judge'))          # recorded for the judge: always in
    draw = lambda rows, key: _stratified([r for r in rows if not r.get('judge')], key, frac, rng)
    near_rows, neg_rows = [r for r in neg if r.get('near')], [r for r in neg if not r.get('near')]
    out = {
        'when': time.time(), 'fraction': frac,
        'positive': sorted(marked(pos) + draw(pos, lambda r: (r.get('device'),) + tags.key(r))),
        'near': sorted(marked(near_rows) + draw(near_rows, lambda r: r.get('device'))),
        'negative': sorted(marked(neg_rows) + draw(neg_rows, lambda r: r.get('device'))),
        'ambient': [],
    }
    # room takes: whole takes until about frac of the minutes, per microphone, shuffled so one giant take is not everything
    total = sum(float(r.get('seconds') or 0) for r in amb)
    got = 0.0
    by_dev = defaultdict(list)
    for r in amb:
        by_dev[r.get('device') or 'unknown'].append(r)
    for takes in by_dev.values():
        rng.shuffle(takes)
        mine = sum(float(r.get('seconds') or 0) for r in takes)
        had = 0.0
        for r in takes:
            if mine and had / mine >= frac:
                break
            if len(takes) >= 2:
                out['ambient'].append(r['id'])
                had += float(r.get('seconds') or 0)
        got += had
    out['ambient'].sort()
    out['ambient_seconds'] = round(got)
    out['counts'] = {'positive': [len(out['positive']), len(pos)], 'near': [len(out['near']), len([r for r in neg if r.get('near')])],
                     'negative': [len(out['negative']), len([r for r in neg if not r.get('near')])], 'ambient': [len(out['ambient']), len(amb)],
                     'ambient_minutes': [round(got / 60, 1), round(total / 60, 1)]}
    write_json(path(pdir), out, indent=1)
    return out


def _earmark(pdir, part, cid, on=True):
    """A clip recorded for the judge joins the frozen slice now (or leaves it); without a slice yet, make() will
    take it. Returns whether the slice changed."""
    h = read(pdir)
    if not h or part not in PARTS:
        return False
    have = list(h.get(part) or [])
    if on == (cid in have):
        return False
    have = sorted(set(have) | {cid}) if on else [x for x in have if x != cid]
    h[part] = have
    c = (h.get('counts') or {}).get(part)
    if isinstance(c, list) and len(c) == 2:
        c[0] = len(have)
        c[1] = max(c[1] + (1 if on else -1), c[0])
    write_json(path(pdir), h, indent=1)
    return True


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
    for part, cols in PARTS.items():
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
