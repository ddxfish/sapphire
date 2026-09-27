"""core/attachments.py - files a tool (or a plugin's own turn) hands the user.

ONE marker, a sibling of the image GALLERY marker:
    <!--FILES:{"title": "...", "items": [{"url": "/api/plugin/<name>/...", "name": "song.mp3"}]}-->
The chat renders it as a row: audio items get a player, every item gets a
download button (interfaces/web/static/shared/files-marker.js). It is UI-only:
kept in history for the browser, stripped from every copy the model reads.

URLs are this app's own plugin routes and nothing else. A tool result can carry
text from the open web, so a marker must never be able to point the browser at
a third party. The browser renderer enforces the same rule.
"""
import json
import re

MARKER_RE = re.compile(r'<!--FILES:\{[^\n]*\}-->[ \t]*\n?')
_URL = re.compile(r'^/api/plugin/[A-Za-z0-9_-]+/[A-Za-z0-9_./-]+(\?[A-Za-z0-9_=&-]*)?$')
MAX_ITEMS = 12


def safe_url(url) -> bool:
    url = str(url or '')
    return bool(_URL.match(url)) and '..' not in url and '//' not in url


def marker(title, items) -> str:
    """items: [{'url': plugin route, 'name': file name shown and saved as}].
    Raises ValueError on a url that isn't a plugin route. '' when no items."""
    out = []
    for it in list(items or [])[:MAX_ITEMS]:
        url = str(it.get('url') or '')
        if not safe_url(url):
            raise ValueError(f"attachment url must be a plugin route (/api/plugin/...): {url!r}")
        name = re.sub(r'[\r\n<>"]', '', str(it.get('name') or '')).strip()[:120]
        out.append({'url': url, 'name': name or url.rsplit('/', 1)[-1].split('?')[0]})
    if not out:
        return ''
    title = re.sub(r'[\r\n]+', ' ', str(title or '')).strip()[:120]
    return '<!--FILES:' + json.dumps({'title': title, 'items': out}) + '-->'


def strip(text):
    """The model's copy: the text with every FILES marker removed."""
    if not text or '<!--FILES:' not in text:
        return text
    return MARKER_RE.sub('', text).strip()
