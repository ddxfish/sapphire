"""What changed between two frames of her face, as a few rectangles to send.

The one-stream boards paint each frame as a key (the whole face) or as the regions
that changed. One box round every changed pixel was the first way: a strand of hair
at the top and a shoulder at the bottom made that box the whole face, so an avatar
that sways sent a key nearly every frame (the 3D avatar: 90% keys, 2026-11-12).

Here the face is cut into 16x16 blocks (the JPEG MCU, and the board's region grid),
each judged moved or not over a noise floor, and the moved blocks are covered by up
to `n` rectangles: one box is split along its widest empty band while the blocks it
would stop sending are worth more than another JPEG's header. Pure numpy, no state.
"""
import numpy as np

BLOCK = 16
FLOOR = 8            # a block moved when its mean change is over this (of 255, per channel) ...
PX = 24              # ... or when more than COUNT of its pixels changed by more than this
COUNT = 24           # (the 3D avatar's shading shimmers by a few levels everywhere: at 3 and 8 every frame was a key;
                     #  at 8 and 24 the board's face differs from hers by 0.25 more than JPEG's own 3.5, 2026-11-12)
HEADER = 300         # bytes a region costs before its first pixel (a JPEG's header with optimize)
PER_BLOCK = 40       # bytes a still 16x16 block costs inside a region
KEY_SHARE = 0.6      # regions covering this much of the face: a key instead


def moved(a, b):
    """Blocks that changed between two HxWx3 uint8 arrays (sides multiples of 16): a bool
    map of (H/16, W/16). The largest change of the three channels counts, so a change in
    blue alone is seen (a luma difference missed it)."""
    h, w = a.shape[0] // BLOCK, a.shape[1] // BLOCK
    d = np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=2)
    d = d[:h * BLOCK, :w * BLOCK].reshape(h, BLOCK, w, BLOCK)
    return (d.mean(axis=(1, 3)) > FLOOR) | ((d > PX).sum(axis=(1, 3)) > COUNT)


def _box(m, r):
    """The bounding box (y1, x1, y2, x2) of the moved blocks of `m` inside `r`, or None."""
    y1, x1, y2, x2 = r
    ys, xs = np.nonzero(m[y1:y2, x1:x2])
    if not len(ys):
        return None
    return y1 + ys.min(), x1 + xs.min(), y1 + ys.max() + 1, x1 + xs.max() + 1


def _split(m, r):
    """`r` cut in two along its widest band of still rows or columns: two boxes, or None."""
    y1, x1, y2, x2 = r
    best = None
    for axis, lo, hi in ((0, y1, y2), (1, x1, x2)):
        line = m[y1:y2, x1:x2].any(axis=1 - axis)
        run = start = 0
        for i, busy in enumerate(line):
            if busy:
                if run and start > 0 and (best is None or run > best[0]):
                    best = (run, axis, lo + start, lo + i)
                run = 0
            else:
                if not run:
                    start = i
                run += 1
    if not best:
        return None
    _, axis, a, b = best
    halves = ((y1, x1, a, x2), (b, x1, y2, x2)) if axis == 0 else ((y1, x1, y2, a), (y1, b, y2, x2))
    return [_box(m, h) for h in halves]


def _waste(m, r):
    y1, x1, y2, x2 = r
    return (y2 - y1) * (x2 - x1) - int(m[y1:y2, x1:x2].sum())


def rects(m, n=4):
    """Up to `n` boxes (y1, x1, y2, x2, in blocks) covering every moved block of `m`; []
    when nothing moved. A box is split only while that saves more than a header's bytes."""
    whole = _box(m, (0, 0) + m.shape)
    if whole is None:
        return []
    out = [whole]
    while len(out) < n:
        best = None
        for i, r in enumerate(out):
            parts = _split(m, r)
            if not parts:
                continue
            saved = (_waste(m, r) - sum(_waste(m, p) for p in parts)) * PER_BLOCK - HEADER
            if saved > 0 and (best is None or saved > best[0]):
                best = (saved, i, parts)
        if not best:
            break
        _, i, parts = best
        out[i:i + 1] = parts
    return out


def plan(a, b, n=4):
    """What to send for frame `b` when the board shows `a`: "still", "key", or a list of
    pixel boxes (x, y, w, h), multiples of 16."""
    m = moved(a, b)
    rs = rects(m, n)
    if not rs:
        return "still"
    area = sum((y2 - y1) * (x2 - x1) for y1, x1, y2, x2 in rs)
    if area >= KEY_SHARE * m.size:
        return "key"
    return [(int(x1) * BLOCK, int(y1) * BLOCK, int(x2 - x1) * BLOCK, int(y2 - y1) * BLOCK) for y1, x1, y2, x2 in rs]
