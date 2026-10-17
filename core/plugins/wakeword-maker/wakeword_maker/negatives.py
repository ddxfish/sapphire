# negatives.py - sound-alikes: phrases that share sounds with the wake phrase and must not wake her. openWakeWord's
# generator walks CMUdict for phoneme neighbours ("hey salisbury", "sapphire hader"); we add the partials (each
# word alone) and a few plain swaps. Anything containing the whole phrase is poison for a negative and is dropped.
import re

SWAPS = ['a', 'the', 'say', 'okay', 'play', 'my', 'they']


def _norm(t):
    return ' '.join(re.sub(r"[^\w\s']", ' ', str(t or '').lower()).split())


def sound_alikes(phrase, spellings=(), n=40):
    phrase = _norm(phrase)
    words = phrase.split()
    full = {_norm(s) for s in spellings} | {phrase}
    out = []
    try:
        from . import compat
        compat.apply()
        from openwakeword.data import generate_adversarial_texts
        out += [_norm(t) for t in generate_adversarial_texts(phrase, n, include_partial_phrase=1.0, include_input_words=0.2)]
    except Exception:
        pass
    if len(words) > 1:
        out += words                                   # each word alone
        out += [' '.join([s] + words[1:]) for s in SWAPS]
        out += [' '.join(words[:-1])]
    seen, keep = set(), []
    for t in out:
        if not t or t in seen or any(f and f in t for f in full):
            continue
        seen.add(t)
        keep.append(t)
    return keep[: n + len(SWAPS) + len(words)]
