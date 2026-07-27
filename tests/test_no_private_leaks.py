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
            '.yml', '.yaml', '.toml', '.cfg', '.ini', '.service', '.sql'}
ALWAYS_SKIP = {'.git', '__pycache__', 'node_modules', '.pytest_cache',
               '.claude'}
# Usernames too generic to be a meaningful beacon (CI boxes, containers).
GENERIC_USERS = {'user', 'admin', 'root', 'ubuntu', 'debian', 'sapphire'}
MAX_BYTES = 2_000_000


def _gitignored_dirs():
    """Top-level directory names from .gitignore — the simple subset only
    (plain names, optionally with a trailing '/' or '/*'). Wildcard and
    nested patterns are file-level noise for this sweep's purposes."""
    out = set()
    gi = REPO / '.gitignore'
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
    if not needles:
        return   # nothing derivable to hunt with on this box

    skip = ALWAYS_SKIP | _gitignored_dirs()
    hits = []
    for path in sorted(REPO.rglob('*')):
        rel = path.relative_to(REPO)
        if any(part in skip for part in rel.parts):
            continue
        if not path.is_file() or path.suffix.lower() not in SCAN_EXT:
            continue
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
            hits.append(f"{rel}:{line_no} — {kind}")
    assert not hits, (
        "Developer-environment strings found in shippable files — scrub "
        "before pushing:\n  " + "\n  ".join(hits))
