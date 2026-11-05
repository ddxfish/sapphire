"""Pre-push megashark sweep — no developer-environment leaks in shipped code.

The beacon pattern: you can't watch for every private string, so ping the
ones the development environment itself defines — the current username and
home directory. A hit in a shippable file means a shark's been near (an
absolute path, a hardcoded handle, a pasted terminal line). Everything is
derived at runtime, so the test carries no personal string itself and works
for ANY developer's checkout. Gitignored top-level dirs (user/, tmp/,
infra/, …) are excluded via a simple .gitignore subset parse — no git
invocation needed, so it also runs on a bare source extract.
"""
import getpass
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

SCAN_EXT = {'.py', '.js', '.json', '.md', '.html', '.css', '.sh', '.txt',
            '.yml', '.yaml', '.toml', '.cfg', '.ini', '.service', '.sql',
            '.c', '.h'}                                   # the firmware ships too (scouts 2026-11-05)
ALWAYS_SKIP = {'.git', '__pycache__', 'node_modules', '.pytest_cache',
               '.claude'}
# Usernames too generic to be a meaningful beacon (CI boxes, containers).
GENERIC_USERS = {'user', 'admin', 'root', 'ubuntu', 'debian', 'sapphire'}
# Placeholder names docs legitimately use in example paths.
GENERIC_PATH_PLACEHOLDERS = {'you', 'yourname', 'your-name', 'your_name', 'username', 'name',
                             'me', 'pi', 'example', 'someone', 'owner', 'operator', 'dev', 'krem',
                             'yourusername', 'your-username', 'your_username', 'myuser', 'myname'}
MAX_BYTES = 2_000_000


def _gitignored_dirs(base=None):
    """Top-level directory names from .gitignore — the simple subset only
    (plain names, optionally with a trailing '/' or '/*'). Wildcard and
    nested patterns are file-level noise for this sweep's purposes.
    `base`: another root with a .gitignore of its own (the firmware repo)."""
    out = set()
    gi = (base or REPO) / '.gitignore'
    if not gi.exists():
        return out
    for line in gi.read_text(encoding='utf-8', errors='ignore').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        line = line.lstrip('/')
        for suffix in ('/*', '/'):
            if line.endswith(suffix):
                line = line[:-len(suffix)]
        # Dots are legal in dir names (user.old) — only wildcards and
        # nested paths disqualify an entry. A gitignored FILE name landing
        # in the set is harmless: it's skipped, and it wasn't shippable.
        if line and '*' not in line and '/' not in line:
            out.add(line)
    return out


# Plugins that are wiring to THIS machine's own hardware/LAN and are not
# distribution units — a home path in them is configuration, not a leak.
# Deliberately a short, visible list: adding a name here is a claim that the
# plugin never leaves this box. If one of these ever ships, remove it here
# FIRST and scrub what the sweep then reports.
LOCAL_ONLY_PLUGINS = {
    'body',    # SSHes to the author's own Pi (sapph-body)
    'nova',    # SSHes to the author's own LAN host + key path
}


def _shipped_outside_git():
    """Plugin dirs that SHIP publicly but live under a gitignored path.

    user/ is gitignored, so user/plugins/* fell entirely outside this sweep —
    yet most of those plugins ship: each carries its own git repo or goes to
    the plugin store, taking any leaked absolute path with it. A plugin is a
    distribution unit if it has a plugin.json and isn't declared local-only.
    (Tier 5 blind spot, war campaign 2026-08-05 — the same gap was flagged
    for the Discord plugin, and story-titanic ships from right here.)"""
    out = []
    for band in (REPO / 'user' / 'plugins',):
        if not band.is_dir():
            continue
        for child in sorted(band.iterdir()):
            if child.name in LOCAL_ONLY_PLUGINS:
                continue
            if child.is_dir() and (child / 'plugin.json').exists():
                out.append(child)
    fw = REPO / 'firmware'                   # its own public repo (gitignored here): the boards' sources ship from it, their build/ and vendor dirs by its own .gitignore
    if fw.is_dir():
        out.append(fw)
    return out


def _parse_needles(text):
    """One beacon per line, '#' comments allowed. A plain line matches
    case-insensitively on word boundaries. A line starting with "re:" is a
    Python regex matched as written (add (?i) for case-insensitive): phone
    shapes, a LAN host range, a name only when capitalized."""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith('#'):
            continue
        if s.startswith('re:'):
            pat = s[3:].strip()
            out.append((pat, f'owner beacon /{pat}/', re.compile(pat)))
            continue
        out.append((s, f'owner beacon "{s}"',
                    re.compile(rf'(?<![A-Za-z0-9_]){re.escape(s)}'
                               rf'(?![A-Za-z0-9_])', re.IGNORECASE)))
    return out


def _owner_needles():
    """Owner-defined beacons — user/megashark_needles.txt. The file lives in
    gitignored user/ so the shipped test never carries the strings themselves
    (same principle as the derived-at-runtime needles: the beacon must not be
    the shark)."""
    path = REPO / 'user' / 'megashark_needles.txt'
    if not path.exists():
        return []
    return _parse_needles(path.read_text(encoding='utf-8', errors='ignore'))


def test_owner_needles_parse_plain_and_regex_lines():
    """Plain lines: whole words, any case. "re:" lines: the regex as written,
    so a shape (a phone number) or a capitalized-only name can be a beacon
    without flagging every lowercase command that shares the letters."""
    needles = _parse_needles("# comment\n\nzebrabeacon\nre:\\bZ\\d{4}\\b\nre:\\bWalrus\\b\n")
    assert [n[0] for n in needles] == ['zebrabeacon', '\\bZ\\d{4}\\b', '\\bWalrus\\b']
    plain, shape, cased = (n[2] for n in needles)
    assert plain.search('a ZebraBeacon here') and not plain.search('zebrabeacons')
    assert shape.search('box Z1234 ok') and not shape.search('Z12345')
    assert cased.search('Walrus the dog') and not cased.search('walrus command')


def test_no_developer_environment_leaks():
    needles = []
    home = str(Path.home())
    if home not in ('', '/', '/root'):
        needles.append((home, 'absolute home path', None))
    try:
        user = getpass.getuser()
    except Exception:
        user = ''
    if user and len(user) >= 4 and user.lower() not in GENERIC_USERS:
        needles.append((user, 'developer username',
                        re.compile(rf'(?<![A-Za-z0-9_]){re.escape(user)}'
                                   rf'(?![A-Za-z0-9_])')))
    needles.extend(_owner_needles())
    # Generic home-path literals (D7, 2026-09-13): the beacon above only sees
    # THIS box's home. A path from any developer's machine is the same shark.
    generic = '|'.join(sorted(GENERIC_USERS | GENERIC_PATH_PLACEHOLDERS))
    needles.append(('/home/<name>/', 'home path literal',
                    re.compile(rf'/home/(?!(?:{generic})/)[A-Za-z0-9_.-]+/', re.I)))
    needles.append(('/Users/<name>/', 'macOS home path literal',
                    re.compile(rf'/Users/(?!(?:{generic})/)[A-Za-z0-9_.-]+/', re.I)))
    needles.append(('C:\\Users\\<name>\\', 'Windows home path literal',
                    re.compile(rf'[A-Za-z]:\\Users\\(?!(?:{generic})\\)[A-Za-z0-9_.-]+\\', re.I)))
    if not needles:
        return   # nothing derivable to hunt with on this box

    skip = ALWAYS_SKIP | _gitignored_dirs()
    hits = []
    # Generic home-path literals in user-band plugins (they ship on their own,
    # but their scrub is their owner's call) — reported as a warning, not a
    # red suite. The real beacon (this box's home / username) stays strict.
    soft_kinds = {'home path literal', 'macOS home path literal', 'Windows home path literal'}
    soft_hits = []
    roots = [REPO] + _shipped_outside_git()
    seen = set()
    for root in roots:
        root_skip = ALWAYS_SKIP | (_gitignored_dirs(root) if root is not REPO else set())
        for path in sorted(root.rglob('*')):
            rel = path.relative_to(REPO)
            # Plugins under a gitignored dir ship on their OWN (their own git
            # repo, the plugin store) — they are outside .gitignore's net but
            # very much public, so they get scanned explicitly.
            if root is REPO and any(part in skip for part in rel.parts):
                continue
            if root is not REPO and any(part in root_skip for part in path.relative_to(root).parts):
                continue                     # an extra root keeps its own ignores (the firmware's build/, managed_components/, backups/)
            if not path.is_file() or path.suffix.lower() not in SCAN_EXT:
                continue
            if any(part in ALWAYS_SKIP for part in rel.parts):
                continue
            # Dedup AFTER the skip decision: marking a path seen while the
            # REPO pass was skipping it would make the extra roots skip it
            # too, silently scanning nothing (caught by a planted beacon).
            if path in seen:
                continue
            seen.add(path)
            try:
                if path.stat().st_size > MAX_BYTES:
                    continue
                text = path.read_text(encoding='utf-8', errors='ignore')
            except OSError:
                continue
            for needle, kind, rx in needles:
                if rx is not None:
                    m = rx.search(text)
                    pos = m.start() if m else -1
                else:
                    pos = text.find(needle)
                if pos < 0:
                    continue
                line_no = text.count('\n', 0, pos) + 1
                if root is not REPO and kind in soft_kinds:
                    soft_hits.append(f"{rel}:{line_no} — {kind}")
                    continue
                hits.append(f"{rel}:{line_no} — {kind}")
    if soft_hits:
        import warnings
        warnings.warn("home-path literals in user-band plugins (owner's scrub):\n  " + "\n  ".join(soft_hits))
    assert not hits, (
        "Developer-environment strings found in shippable files — scrub "
        "before pushing:\n  " + "\n  ".join(hits))
