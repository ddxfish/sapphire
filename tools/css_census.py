#!/usr/bin/env python3
"""CSS census for style.css — live / suspect / dead per class, per family.

  live    — exact class name appears as a literal anywhere in the corpus
  suspect — corpus contains name-minus-last-segment + '-' (dynamic suffix)
  dead    — neither

Usage:
  python css_census.py            # report only
  python css_census.py --prune    # rebuild style.css minus dead-only rules
"""
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path('/home/bander/sapphire/sapphire')
CSS = ROOT / 'interfaces/web/static/style.css'
SCRATCH = Path(__file__).parent

CORPUS_GLOBS = [
    ('interfaces', '**/*.js'), ('interfaces', '**/*.html'),
    ('plugins', '**/*.js'), ('plugins', '**/*.html'),
    ('user/plugins', '**/*.js'), ('user/plugins', '**/*.html'),
    ('core', '**/*.py'),
]

PROTECTED = {'active', 'open', 'closed', 'hidden', 'visible', 'show', 'hide',
             'disabled', 'selected', 'dragging', 'drag-over', 'error',
             'success', 'warning', 'busy', 'loading', 'collapsed', 'expanded',
             'on', 'off', 'dark', 'light'}

CLASS_RE = re.compile(r'\.(-?[A-Za-z_][A-Za-z0-9_-]*)')
LEAD_WS_COMMENTS = re.compile(r'^(?:\s|/\*.*?\*/)*', re.S)


def strip_comments(text):
    return re.sub(r'/\*.*?\*/', '', text, flags=re.S)


def parse_rules(css):
    """Leaf rules only, with exact [start, end) spans that EXCLUDE any
    leading comments/whitespace (banners survive pruning)."""
    rules = []
    i, n = 0, len(css)
    stack = []
    seg_start = 0
    while i < n:
        c = css[i]
        if css[i:i + 2] == '/*':
            j = css.find('*/', i + 2)
            i = n if j < 0 else j + 2
            continue
        if c in '"\'':
            j = i + 1
            while j < n and css[j] != c:
                j += 2 if css[j] == '\\' else 1
            i = j + 1
            continue
        if c == '{':
            stack.append((seg_start, i))
            seg_start = i + 1
        elif c == '}':
            if stack:
                s0, s1 = stack.pop()
                seg = css[s0:s1]
                sel = strip_comments(seg).strip()
                body = css[s1 + 1:i]
                if sel and not sel.startswith('@') and \
                        '{' not in strip_comments(body):
                    lead = s0 + LEAD_WS_COMMENTS.match(seg).end()
                    rules.append({'start': lead, 'end': i + 1, 'sel': sel})
            seg_start = i + 1
        i += 1
    return rules


def main():
    prune = '--prune' in sys.argv
    css = CSS.read_text(encoding='utf-8')
    all_classes = set(CLASS_RE.findall(strip_comments(css)))

    corpus = []
    for base, glob in CORPUS_GLOBS:
        for f in (ROOT / base).glob(glob):
            if '__pycache__' in str(f):
                continue
            try:
                corpus.append(f.read_text(encoding='utf-8', errors='replace'))
            except OSError:
                pass
    blob = '\n'.join(corpus)

    # Hand-verified: zero exact frontend refs for the whole family (Story
    # Engine v1 remnant, removed from core 2026-04-18).
    FORCE_DEAD_PREFIXES = ('story-',)

    live, suspect, dead = set(), set(), set()
    for cls in all_classes:
        if cls.startswith(FORCE_DEAD_PREFIXES):
            dead.add(cls)
            continue
        if cls in PROTECTED or cls in blob:
            live.add(cls)
            continue
        parent = cls.rsplit('-', 1)[0]
        if parent != cls and (parent + '-') in blob:
            suspect.add(cls)
        else:
            dead.add(cls)

    famstat = defaultdict(lambda: [0, 0, 0])
    for c in all_classes:
        famstat[c.split('-')[0]][0 if c in live else 1 if c in suspect else 2] += 1
    print(f"classes: {len(all_classes)}  live {len(live)}  "
          f"suspect {len(suspect)}  dead {len(dead)}")
    print(f"{'family':16} {'live':>5} {'susp':>5} {'dead':>5}")
    for f, (l, s, d) in sorted(famstat.items(), key=lambda x: -x[1][2]):
        if d or s:
            print(f"{f:16} {l:5} {s:5} {d:5}")
    (SCRATCH / 'census_dead.txt').write_text(
        '\n'.join(sorted(dead)) + '\n', encoding='utf-8')

    if not prune:
        return

    rules = parse_rules(css)
    spans = []
    for r in rules:
        parts = [p.strip() for p in r['sel'].split(',') if p.strip()]
        keep = False
        for p in parts:
            pcls = set(CLASS_RE.findall(p))
            if not pcls or not (pcls & dead):
                keep = True
                break
        if not keep:
            spans.append((r['start'], r['end']))
    if not spans:
        print('nothing to prune')
        return
    spans.sort()
    out, pos = [], 0
    for s, e in spans:
        out.append(css[pos:s])
        pos = e
        while pos < len(css) and css[pos] == '\n' and out and \
                out[-1].endswith('\n\n'):
            pos += 1
    out.append(css[pos:])
    new = re.sub(r'\n{4,}', '\n\n\n', ''.join(out))
    backup = SCRATCH / 'style.css.pre-prune'
    backup.write_text(css, encoding='utf-8')
    CSS.write_text(new, encoding='utf-8')
    print(f"pruned {len(spans)} rules · {len(css.splitlines())} → "
          f"{len(new.splitlines())} lines · backup: {backup}")


if __name__ == '__main__':
    main()
