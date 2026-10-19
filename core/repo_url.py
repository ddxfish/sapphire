"""Plugin source URLs: a GitHub/GitLab repo, optionally pinned to a branch.

One parser for every place that installs or update-checks from a repo URL
(install route, check-update, the store tool). Accepted shapes:
  github.com/<owner>/<repo>[.git]
  github.com/<owner>/<repo>/tree/<branch>
  github.com/<owner>/<repo>/archive/refs/heads/<branch>.zip
  gitlab.com/<group>[/<subgroup>...]/<repo>[.git]
  gitlab.com/<group>[/<subgroup>...]/<repo>/-/tree/<branch>
No branch → main, then master. A pinned branch is the only one tried, and
since the full URL is what gets stored as `installed_from`, updates keep
following that branch. Everything after /tree/ is the branch (slashes
allowed), so /tree/<branch>/<subdir> is not supported - nor is a subdir
install anywhere else.
"""
import re
from urllib.parse import urlparse

_GH = re.compile(r'https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?'
                 r'(?:/tree/(.+?)|/archive/refs/heads/(.+?)\.zip)?/?$')
_GL = re.compile(r'https?://gitlab\.com/(.+?)/([^/]+?)(?:\.git)?(?:/-/tree/(.+?))?/?$')
_BRANCH = re.compile(r'[\w.\-/]+')
_REDIRECTS = (301, 302, 303, 307, 308)


def parse(url):
    """Return {host, label, path, repo, branches} or None if not a repo URL.

    Query strings and fragments are ignored for matching."""
    p = urlparse((url or '').strip())
    bare = f"{p.scheme}://{p.netloc}{p.path}"
    m = _GH.match(bare)
    if m:
        host, path, repo, branch = 'github', f"{m.group(1)}/{m.group(2)}", m.group(2), m.group(3) or m.group(4)
    else:
        m = _GL.match(bare)
        if not m:
            return None
        host, path, repo, branch = 'gitlab', f"{m.group(1)}/{m.group(2)}", m.group(2), m.group(3)
    if branch is not None:
        branch = branch.strip('/')
        if not _BRANCH.fullmatch(branch) or '..' in branch:
            return None
    return {'host': host, 'label': 'GitHub' if host == 'github' else 'GitLab',
            'path': path, 'repo': repo,
            'branches': [branch] if branch else ['main', 'master']}


def archive_url(src, branch):
    if src['host'] == 'github':
        return f"https://github.com/{src['path']}/archive/refs/heads/{branch}.zip"
    return f"https://gitlab.com/{src['path']}/-/archive/{branch}/{src['repo']}-{branch.replace('/', '-')}.zip"


def open_archive(src):
    """GET the repo zip from the first branch that exists. Returns the
    streaming response; the caller checks status_code. Redirects are only
    followed to the host's own archive server (SSRF guard)."""
    from core import net
    allowed = 'https://codeload.github.com/' if src['host'] == 'github' else 'https://gitlab.com/'
    for branch in src['branches']:
        r = net.get(archive_url(src, branch), stream=True, timeout=30, allow_redirects=False)
        if r.status_code in _REDIRECTS:
            loc = r.headers.get('Location', '')
            if loc.startswith(allowed):
                r = net.get(loc, stream=True, timeout=30, allow_redirects=False)
        if r.status_code != 404:
            break
    return r


def fetch_file(src, path):
    """Text of a repo file on the first branch that has it, or None."""
    if src['host'] == 'github':
        # Contents-API-first: raw.githubusercontent lags pushes by ~5 min.
        from core.github_files import fetch_github_file
        for branch in src['branches']:
            text = fetch_github_file(src['path'], branch, path)
            if text is not None:
                return text
        return None
    from core import net
    for branch in src['branches']:
        try:
            r = net.get(f"https://gitlab.com/{src['path']}/-/raw/{branch}/{path}", timeout=10)
            if r.status_code == 200:
                return r.text
        except Exception:
            continue
    return None
