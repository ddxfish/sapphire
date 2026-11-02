# core/plugins/social/providers/youtube.py - a YouTube channel's comments and numbers
#
# Google OAuth with the person's own client (the calendar relay carries only
# the calendar scope). Reads are cheap (1 quota unit), a reply costs 50, the
# day has 10,000. No posting: API uploads stay private until Google audits
# the app, and YouTube policy wants a human choice per write anyway.
import time
from urllib.parse import urlencode

from core import net
from ._common import check, iso_ts, need
from plugins.social.errors import SocialError

AUTH = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN = 'https://oauth2.googleapis.com/token'
API = 'https://www.googleapis.com/youtube/v3'
SCOPE = 'https://www.googleapis.com/auth/youtube.force-ssl'
TIMEOUT = 30


def _h(conn):
    token, = need(conn, conn.secrets, 'access_token')
    return {'Authorization': f'Bearer {token}'}


def _channel(conn, headers):
    d = check(net.get(f"{API}/channels", headers=headers, params={'part': 'snippet,statistics', 'mine': 'true'},
                      timeout=TIMEOUT), 'YouTube channel')
    items = d.get('items') or []
    if not items:
        raise SocialError('this Google account has no YouTube channel')
    return items[0]


def auth_start(conn, redirect_uri, state):
    client_id, = need(conn, conn.config, 'client_id')
    return AUTH + '?' + urlencode({'client_id': client_id, 'redirect_uri': redirect_uri, 'response_type': 'code',
                                   'scope': SCOPE, 'access_type': 'offline', 'prompt': 'consent', 'state': state})


def auth_finish(conn, redirect_uri, query, stash):
    client_id, = need(conn, conn.config, 'client_id')
    client_secret, = need(conn, conn.secrets, 'client_secret')
    code = query.get('code')
    if not code:
        raise SocialError('Google sent no authorization code')
    d = check(net.post(TOKEN, data={'code': code, 'client_id': client_id, 'client_secret': client_secret,
                                    'redirect_uri': redirect_uri, 'grant_type': 'authorization_code'},
                       timeout=TIMEOUT), 'Google token exchange')
    if not d.get('access_token'):
        raise SocialError('Google token exchange gave no token')
    ch = _channel(conn, {'Authorization': f"Bearer {d['access_token']}"})
    title = (ch.get('snippet') or {}).get('title', '')
    return {'secrets': {'access_token': d['access_token'], 'refresh_token': d.get('refresh_token', '')},
            'meta': {'expires_at': time.time() + float(d.get('expires_in') or 3600), 'channel_id': ch.get('id', ''),
                     'identity': title, 'url': f"https://www.youtube.com/channel/{ch.get('id', '')}"}}


def refresh(conn):
    client_id, = need(conn, conn.config, 'client_id')
    client_secret, = need(conn, conn.secrets, 'client_secret')
    rt = conn.secrets.get('refresh_token')
    if not rt:
        raise SocialError("no refresh token stored: Connect again (Google only gives one with prompt=consent)")
    d = check(net.post(TOKEN, data={'client_id': client_id, 'client_secret': client_secret,
                                    'refresh_token': rt, 'grant_type': 'refresh_token'}, timeout=TIMEOUT),
              'Google token refresh')
    conn.save_secrets({'access_token': d['access_token']})
    conn.save_meta({'expires_at': time.time() + float(d.get('expires_in') or 3600)})
    return True


def validate(conn):
    ch = _channel(conn, _h(conn))
    s = ch.get('statistics') or {}
    conn.save_meta({'channel_id': ch.get('id', ''), 'identity': (ch.get('snippet') or {}).get('title', '')})
    return True, f"{(ch.get('snippet') or {}).get('title')}: {s.get('subscriberCount', '?')} subscribers, {s.get('videoCount', '?')} videos"


def inbox(conn, since):
    cid, = need(conn, conn.meta, 'channel_id')
    d = check(net.get(f"{API}/commentThreads", headers=_h(conn), params={
        'part': 'snippet,replies', 'allThreadsRelatedToChannelId': cid, 'order': 'time',
        'maxResults': 50, 'textFormat': 'plainText'}, timeout=TIMEOUT), 'YouTube comments')
    items = []
    for t in d.get('items') or []:
        sn = t.get('snippet') or {}
        vid = sn.get('videoId', '')
        top = sn.get('topLevelComment') or {}
        comments = [top] + list((t.get('replies') or {}).get('comments') or [])
        for c in comments:
            s = c.get('snippet') or {}
            ts = iso_ts(s.get('publishedAt'))
            if ts < since or str((s.get('authorChannelId') or {}).get('value')) == str(cid):
                continue
            items.append({'id': c.get('id'), 'kind': 'comment' if c is top else 'reply',
                          'author': s.get('authorDisplayName', '?'), 'text': s.get('textDisplay', ''), 'ts': ts,
                          'url': f"https://www.youtube.com/watch?v={vid}&lc={c.get('id')}" if vid else ''})
    items.sort(key=lambda i: i['ts'], reverse=True)
    return items


def reply(conn, item_id, text):
    parent = str(item_id).split('.')[0]          # a reply's id is parent.reply; replies hang off the parent
    d = check(net.post(f"{API}/comments", headers=_h(conn), params={'part': 'snippet'},
                       json={'snippet': {'parentId': parent, 'textOriginal': text}}, timeout=TIMEOUT), 'YouTube reply')
    return {'id': d.get('id')}


def delete(conn, post_id):
    r = net.delete(f"{API}/comments", headers=_h(conn), params={'id': post_id}, timeout=TIMEOUT)
    check(r, 'YouTube delete')
    return r.status_code in (200, 204)


def stats(conn):
    s = _channel(conn, _h(conn)).get('statistics') or {}
    return {'subscribers': s.get('subscriberCount'), 'views': s.get('viewCount'), 'videos': s.get('videoCount')}
