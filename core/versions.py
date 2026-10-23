"""One version parser for the whole app.

Grew out of three rival parsers with three failure semantics (core/updater.py,
core/routes/plugins.py, core/routes/store.py — 2026-08-06 updater hunt, M1).
Semantics here: lenient parse, conservative compare — a version we can't read
never advertises an update; callers surface the parse failure instead.
"""


def parse_version(v):
    """'v1.2.3-rc1' → (1, 2, 3). Returns None when nothing numeric parses.

    A leading 'v'/'V' before a digit is stripped; each dotted part contributes
    its leading digits ('3-rc1' → 3); a part with no leading digit reads as 0.
    """
    s = str(v or '').strip()
    if s[:1] in ('v', 'V') and len(s) > 1 and s[1].isdigit():
        s = s[1:]
    if not s:
        return None
    parts = []
    any_digits = False
    for chunk in s.split('.'):
        num = ''
        for ch in chunk:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
        if num:
            any_digits = True
    return tuple(parts) if any_digits else None


def is_newer(remote, local):
    """True only when BOTH versions parse and remote > local.

    Unparseable input never advertises an update. Compared zero-padded, so
    '1.2' == '1.2.0'.
    """
    r, loc = parse_version(remote), parse_version(local)
    if r is None or loc is None:
        return False
    width = max(len(r), len(loc))
    return r + (0,) * (width - len(r)) > loc + (0,) * (width - len(loc))
