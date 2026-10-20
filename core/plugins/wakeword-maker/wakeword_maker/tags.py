# tags.py - the four tags on a take of your own voice: HOW it was said, WHERE from, what NOISE was on, and WHO.
# Old sidecars carry one `style`; it maps on read ('far away' is a where, 'TV on' is a noise), nothing on disk is
# rewritten. The held-out draw stratifies on them, the judge reports recall by each, a source block filters by them.
HOW = ('normal', 'fast', 'slow', 'soft', 'loud', 'whisper', 'excited', 'tired')
WHERE = ('close', 'across the room', 'next room')
NOISE = ('quiet', 'TV or music', 'busy')
AXES = {'how': HOW, 'where': WHERE, 'noise': NOISE}
DEFAULT = {'how': 'normal', 'where': 'close', 'noise': 'quiet', 'who': ''}
OLD = {'far away': ('where', 'across the room'), 'TV on': ('noise', 'TV or music')}


def of(side):
    """The four tags of a clip with the defaults filled in; an old single `style` maps onto them."""
    t = dict(DEFAULT)
    s = str((side or {}).get('style') or '')
    if s in OLD:
        t[OLD[s][0]] = OLD[s][1]
    elif s in HOW:
        t['how'] = s
    for k in DEFAULT:
        v = (side or {}).get(k)
        if v not in (None, ''):
            t[k] = str(v)
    return t


def clean(fields):
    """Tag fields from a form or a body, known values only; `who` is free text, trimmed. Unknown values are left out."""
    out = {}
    for k, vals in AXES.items():
        v = (fields or {}).get(k)
        if v in vals:
            out[k] = v
    who = (fields or {}).get('who')
    if who is not None:
        out['who'] = str(who).strip()[:60]
    return out


def key(side):
    """What a held-out draw stratifies on, beside the microphone."""
    t = of(side)
    return (t['how'], t['where'], t['noise'], t['who'])


def label(side):
    """The tags that are not the default, as one short string for a clip row: 'fast · next room · Sam'."""
    t = of(side)
    return ' · '.join(t[k] for k in DEFAULT if t[k] and t[k] != DEFAULT[k])
