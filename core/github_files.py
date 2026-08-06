"""Fetch repo files from GitHub, freshness-first.

raw.githubusercontent.com sits behind a ~5-minute CDN cache, so a version
check right after a push can read the previous release (2026-08-06 incident:
plugin 1.16.0 pushed, "no updates" for minutes). The contents API serves the
current commit, so try it first; the raw URL stays as a fallback for
rate-limiting (60/hr unauthenticated) or API outages.
"""
import logging

import requests

logger = logging.getLogger(__name__)

_API_URL = 'https://api.github.com/repos/{repo}/contents/{path}?ref={branch}'
_RAW_URL = 'https://raw.githubusercontent.com/{repo}/{branch}/{path}'


def fetch_github_file(repo, branch, path, timeout=10):
    """Return the file's text at the branch head, or None if unavailable.

    `repo` is 'owner/name'. Tries the contents API (always fresh), then
    raw.githubusercontent (may lag pushes by a few minutes).
    """
    try:
        resp = requests.get(
            _API_URL.format(repo=repo, path=path, branch=branch),
            headers={'Accept': 'application/vnd.github.raw+json'},
            timeout=timeout,
        )
        if resp.status_code == 200:
            return resp.text
        if resp.status_code != 404:
            logger.debug(f"contents API HTTP {resp.status_code} for {repo}/{path}@{branch}")
    except Exception as e:
        logger.debug(f"contents API fetch failed for {repo}/{path}@{branch}: {e}")
    try:
        resp = requests.get(_RAW_URL.format(repo=repo, branch=branch, path=path), timeout=timeout)
        if resp.status_code == 200:
            return resp.text
    except Exception as e:
        logger.debug(f"raw fetch failed for {repo}/{path}@{branch}: {e}")
    return None
