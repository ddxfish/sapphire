"""core/github_files.py — freshness-first GitHub file fetch (2026-08-06 hunt, H1).

raw.githubusercontent lags pushes ~5 min; the contents API serves the current
commit. Contract: API first, raw fallback, None when both fail.
"""
from unittest.mock import MagicMock

import requests

from core.github_files import fetch_github_file


def _resp(status, text=''):
    r = MagicMock()
    r.status_code = status
    r.text = text
    return r


def test_contents_api_first_no_raw_call(monkeypatch):
    fetched = []

    def fake_get(url, timeout=10, headers=None):
        fetched.append(url)
        return _resp(200, '1.17.0\n')

    monkeypatch.setattr(requests, 'get', fake_get)
    assert fetch_github_file('owner/repo', 'main', 'VERSION') == '1.17.0\n'
    assert len(fetched) == 1
    assert 'api.github.com/repos/owner/repo/contents/VERSION' in fetched[0]
    assert 'ref=main' in fetched[0]


def test_falls_back_to_raw_on_api_failure(monkeypatch):
    fetched = []

    def fake_get(url, timeout=10, headers=None):
        fetched.append(url)
        if 'api.github.com' in url:
            return _resp(403)  # rate-limited
        return _resp(200, '1.17.0')

    monkeypatch.setattr(requests, 'get', fake_get)
    assert fetch_github_file('owner/repo', 'main', 'VERSION') == '1.17.0'
    assert len(fetched) == 2
    assert 'raw.githubusercontent.com/owner/repo/main/VERSION' in fetched[1]


def test_falls_back_on_api_exception(monkeypatch):
    def fake_get(url, timeout=10, headers=None):
        if 'api.github.com' in url:
            raise requests.ConnectionError('api down')
        return _resp(200, 'text')

    monkeypatch.setattr(requests, 'get', fake_get)
    assert fetch_github_file('owner/repo', 'main', 'VERSION') == 'text'


def test_both_fail_returns_none(monkeypatch):
    monkeypatch.setattr(requests, 'get', lambda url, timeout=10, headers=None: _resp(404))
    assert fetch_github_file('owner/repo', 'nope', 'VERSION') is None


def test_both_raise_returns_none(monkeypatch):
    def fake_get(url, timeout=10, headers=None):
        raise requests.ConnectionError('offline')

    monkeypatch.setattr(requests, 'get', fake_get)
    assert fetch_github_file('owner/repo', 'main', 'VERSION') is None
