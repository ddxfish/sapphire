"""core/repo_url — plugin source URLs pinned to a branch (2026-10-19).

A fan's plugin lived on a `weather-dashboard` branch; the installer only knew
bare repo URLs and always fetched main. One parser now feeds install,
check-update and the store tool, and the stored URL keeps the branch so
updates follow it.
"""
from unittest.mock import MagicMock

import pytest

from core import repo_url


@pytest.mark.parametrize('url,host,path,branches', [
    ('https://github.com/o/r', 'github', 'o/r', ['main', 'master']),
    ('https://github.com/o/r.git', 'github', 'o/r', ['main', 'master']),
    ('https://github.com/o/r/', 'github', 'o/r', ['main', 'master']),
    ('https://github.com/o/r?utm=x#readme', 'github', 'o/r', ['main', 'master']),
    ('https://github.com/o/r/tree/weather-dashboard', 'github', 'o/r', ['weather-dashboard']),
    ('https://github.com/o/r/tree/feature/x/', 'github', 'o/r', ['feature/x']),
    ('https://github.com/o/r/archive/refs/heads/dev.zip', 'github', 'o/r', ['dev']),
    ('https://gitlab.com/g/sub/r', 'gitlab', 'g/sub/r', ['main', 'master']),
    ('https://gitlab.com/g/sub/r/-/tree/dev', 'gitlab', 'g/sub/r', ['dev']),
])
def test_parse_shapes(url, host, path, branches):
    src = repo_url.parse(url)
    assert (src['host'], src['path'], src['branches']) == (host, path, branches)


@pytest.mark.parametrize('url', [
    'https://github.com/o/r/blob/main/README.md',
    'https://github.com/o/r/tree/../../evil',
    'https://github.com/o/r/tree/a%20b',
    'https://example.com/plugin.zip',
    'https://bitbucket.org/o/r',
    '',
    None,
])
def test_parse_rejects(url):
    assert repo_url.parse(url) is None


def test_archive_urls():
    gh = repo_url.parse('https://github.com/o/r/tree/feature/x')
    assert repo_url.archive_url(gh, 'feature/x') == 'https://github.com/o/r/archive/refs/heads/feature/x.zip'
    gl = repo_url.parse('https://gitlab.com/g/r/-/tree/feature/x')
    assert repo_url.archive_url(gl, 'feature/x') == 'https://gitlab.com/g/r/-/archive/feature/x/r-feature-x.zip'


def _resp(status, location=''):
    r = MagicMock()
    r.status_code = status
    r.headers = {'Location': location}
    return r


def test_open_archive_pinned_branch_only(monkeypatch):
    from core import net
    fetched = []

    def _get(url, **_):
        fetched.append(url)
        return _resp(404)

    monkeypatch.setattr(net, 'get', _get)
    r = repo_url.open_archive(repo_url.parse('https://github.com/o/r/tree/dev'))
    assert r.status_code == 404
    assert fetched == ['https://github.com/o/r/archive/refs/heads/dev.zip']


def test_open_archive_follows_codeload_only(monkeypatch):
    from core import net
    ok = _resp(200)

    def _get(url, **_):
        if url.startswith('https://codeload.github.com/'):
            return ok
        return _resp(302, 'https://codeload.github.com/o/r/zip/refs/heads/main')

    monkeypatch.setattr(net, 'get', _get)
    assert repo_url.open_archive(repo_url.parse('https://github.com/o/r')) is ok

    monkeypatch.setattr(net, 'get', lambda url, **_: _resp(302, 'http://127.0.0.1/x.zip'))
    assert repo_url.open_archive(repo_url.parse('https://github.com/o/r')).status_code == 302


def test_open_archive_falls_back_to_master(monkeypatch):
    from core import net
    fetched = []

    def _get(url, **_):
        fetched.append(url)
        return _resp(200 if 'master' in url else 404)

    monkeypatch.setattr(net, 'get', _get)
    assert repo_url.open_archive(repo_url.parse('https://github.com/o/r')).status_code == 200
    assert [u.rsplit('/', 1)[1] for u in fetched] == ['main.zip', 'master.zip']


def test_fetch_file_pinned_branch(monkeypatch):
    from core import github_files
    seen = []

    def _fetch(repo, branch, path, timeout=10):
        seen.append(branch)
        return '{"version": "2.0.0"}'

    monkeypatch.setattr(github_files, 'fetch_github_file', _fetch)
    text = repo_url.fetch_file(repo_url.parse('https://github.com/o/r/tree/weather-dashboard'), 'plugin.json')
    assert text == '{"version": "2.0.0"}'
    assert seen == ['weather-dashboard']
