# core/plugins/social-bluesky/provider.py - Bluesky over plain XRPC (no SDK)
#
# THE worked example of a Social provider: the manifest declares the spec,
# this module exports the functions, the Social plugin does the rest
# (profiles, the Outbox, the tools). docs/plugin-author/social.md says the
# contract. `conn.config` holds handle and server, `conn.secrets` the app
# password; conn.save_meta() keeps what we learn (the DID, the identity).
import re
import time
from datetime import datetime, timezone

from core import net
from plugins.social.errors import SocialError

TIMEOUT = 30
_URL = re.compile(r'https?://[^\s<>()\[\]]+')


def _pds(conn):
    return str(conn.config.get('pds') or 'https://bsky.social').rstrip('/')


def _check(resp, what):
    try:
        data = resp.json() if resp.content else {}
    except ValueError:
        data = {}
    if resp.status_code >= 400:
        raise SocialError(f"{what}: HTTP {resp.status_code} {data.get('message') or data.get('error') or ''}".rstrip())
    return data


def _session(conn):
    """A fresh session each call: app-password logins are cheap and short-lived."""
    handle = conn.config.get('handle')
    pw = conn.secrets.get('app_password')
    if not handle or not pw:
        raise SocialError(f"'{conn.name}' needs a handle and an app password (Settings > Plugins > Social)")
    d = _check(net.post(f"{_pds(conn)}/xrpc/com.atproto.server.createSession",
                        json={'identifier': handle, 'password': pw}, timeout=TIMEOUT), 'Bluesky login')
    return {'did': d['did'], 'headers': {'Authorization': f"Bearer {d['accessJwt']}"},
            'handle': d.get('handle', handle)}


def _get(conn, s, method, **params):
    return _check(net.get(f"{_pds(conn)}/xrpc/{method}", headers=s['headers'], params=params, timeout=TIMEOUT), method)


def _post(conn, s, method, body=None, data=None, content_type=None):
    headers = dict(s['headers'])
    if content_type:
        headers['Content-Type'] = content_type
    return _check(net.post(f"{_pds(conn)}/xrpc/{method}", headers=headers, json=body, data=data, timeout=120),
                  method)


def _ts(s):
    s = str(s or '')
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()
    except ValueError:
        return 0.0


def _facets(text):
    """Links are plain text on Bluesky unless a facet marks their byte range."""
    out = []
    raw = text.encode('utf-8')
    for m in _URL.finditer(text):
        start = len(text[:m.start()].encode('utf-8'))
        out.append({'index': {'byteStart': start, 'byteEnd': start + len(m.group(0).encode('utf-8'))},
                    'features': [{'$type': 'app.bsky.richtext.facet#link', 'uri': m.group(0)}]})
    return out if raw else out


def _url(handle, uri):
    rkey = uri.rsplit('/', 1)[-1]
    return f"https://bsky.app/profile/{handle}/post/{rkey}"


def validate(conn):
    s = _session(conn)
    p = _get(conn, s, 'app.bsky.actor.getProfile', actor=s['did'])
    conn.save_meta({'did': s['did'], 'identity': '@' + s['handle'], 'url': f"https://bsky.app/profile/{s['handle']}"})
    return True, f"@{s['handle']}: {p.get('followersCount', '?')} followers"


def post(conn, text, images, link, disclose):
    s = _session(conn)
    if link and link not in text:
        text = f"{text}\n{link}".strip()
    record = {'$type': 'app.bsky.feed.post', 'text': text,
              'createdAt': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')}
    facets = _facets(text)
    if facets:
        record['facets'] = facets
    if images:
        blobs = []
        for raw, mt in images:
            up = _post(conn, s, 'com.atproto.repo.uploadBlob', data=raw, content_type=mt)
            blobs.append({'image': up['blob'], 'alt': text[:300]})
        record['embed'] = {'$type': 'app.bsky.embed.images', 'images': blobs}
    d = _post(conn, s, 'com.atproto.repo.createRecord',
              body={'repo': s['did'], 'collection': 'app.bsky.feed.post', 'record': record})
    return {'id': d.get('uri'), 'url': _url(s['handle'], d.get('uri', ''))}


def inbox(conn, since):
    s = _session(conn)
    d = _get(conn, s, 'app.bsky.notification.listNotifications', limit=50)
    items = []
    for n in d.get('notifications') or []:
        ts = _ts(n.get('indexedAt'))
        if ts < since or n.get('reason') not in ('mention', 'reply', 'quote'):
            continue
        rec = n.get('record') or {}
        author = n.get('author') or {}
        items.append({'id': n.get('uri'), 'kind': n.get('reason'), 'author': '@' + author.get('handle', '?'),
                      'text': rec.get('text', ''), 'ts': ts, 'url': _url(author.get('handle', ''), n.get('uri', ''))})
    return items


def reply(conn, item_id, text):
    s = _session(conn)
    got = _get(conn, s, 'app.bsky.feed.getPosts', uris=item_id)
    posts = got.get('posts') or []
    if not posts:
        raise SocialError(f"no post {item_id} to reply to")
    parent = {'uri': posts[0]['uri'], 'cid': posts[0]['cid']}
    root = ((posts[0].get('record') or {}).get('reply') or {}).get('root') or parent
    record = {'$type': 'app.bsky.feed.post', 'text': text, 'reply': {'root': root, 'parent': parent},
              'createdAt': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')}
    d = _post(conn, s, 'com.atproto.repo.createRecord',
              body={'repo': s['did'], 'collection': 'app.bsky.feed.post', 'record': record})
    return {'id': d.get('uri'), 'url': _url(s['handle'], d.get('uri', ''))}


def delete(conn, post_id):
    s = _session(conn)
    parts = str(post_id).split('/')
    if len(parts) < 5 or parts[2] != s['did']:
        raise SocialError('only your own posts can be deleted; give the at:// uri from the post result')
    _post(conn, s, 'com.atproto.repo.deleteRecord', body={'repo': s['did'], 'collection': parts[3], 'rkey': parts[4]})
    return True


def stats(conn):
    s = _session(conn)
    p = _get(conn, s, 'app.bsky.actor.getProfile', actor=s['did'])
    return {'followers': p.get('followersCount'), 'following': p.get('followsCount'), 'posts': p.get('postsCount')}
