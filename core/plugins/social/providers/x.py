# core/plugins/social/providers/x.py - X through the v2 API, OAuth 2.0 with PKCE
#
# The person's own app (developer.x.com, attached to a Project). Pay-per-use:
# every post and every read costs money, so the inbox asks for mentions only.
# The "Automated" label is set by the human in X's own settings; the API has
# no switch for it. Tokens last two hours; offline.access gives the refresh.
import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode

from core import net
from ._common import check, iso_ts, iso_utc, need
from plugins.social.errors import SocialError

AUTH = 'https://x.com/i/oauth2/authorize'
TOKEN = 'https://api.x.com/2/oauth2/token'
API = 'https://api.x.com/2'
SCOPES = 'tweet.read tweet.write users.read offline.access media.write'
TIMEOUT = 30


def _basic(conn):
    secret = conn.secrets.get('client_secret')
    return (conn.config.get('client_id', ''), secret) if secret else None


def _h(conn):
    token, = need(conn, conn.secrets, 'access_token')
    return {'Authorization': f'Bearer {token}'}


def auth_start(conn, redirect_uri, state):
    client_id, = need(conn, conn.config, 'client_id')
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    url = AUTH + '?' + urlencode({'response_type': 'code', 'client_id': client_id, 'redirect_uri': redirect_uri,
                                  'scope': SCOPES, 'state': state, 'code_challenge': challenge,
                                  'code_challenge_method': 'S256'})
    return url, {'verifier': verifier}


def _tokens(conn, d):
    conn.save_secrets({'access_token': d['access_token'], 'refresh_token': d.get('refresh_token') or None})
    conn.save_meta({'expires_at': time.time() + float(d.get('expires_in') or 7200)})


def auth_finish(conn, redirect_uri, query, stash):
    client_id, = need(conn, conn.config, 'client_id')
    code = query.get('code')
    if not code:
        raise SocialError('X sent no authorization code')
    d = check(net.post(TOKEN, data={'grant_type': 'authorization_code', 'code': code, 'redirect_uri': redirect_uri,
                                    'client_id': client_id, 'code_verifier': stash.get('verifier', '')},
                       auth=_basic(conn), timeout=TIMEOUT), 'X token exchange')
    if not d.get('access_token'):
        raise SocialError('X token exchange gave no token')
    me = check(net.get(f"{API}/users/me", headers={'Authorization': f"Bearer {d['access_token']}"},
                       timeout=TIMEOUT), 'X profile').get('data') or {}
    return {'secrets': {'access_token': d['access_token'], 'refresh_token': d.get('refresh_token', '')},
            'meta': {'expires_at': time.time() + float(d.get('expires_in') or 7200),
                     'user_id': me.get('id', ''), 'username': me.get('username', ''),
                     'identity': f"@{me.get('username', '')}" if me.get('username') else '',
                     'url': f"https://x.com/{me.get('username', '')}"}}


def refresh(conn):
    client_id, = need(conn, conn.config, 'client_id')
    rt = conn.secrets.get('refresh_token')
    if not rt:
        raise SocialError("no refresh token stored: Connect again")
    d = check(net.post(TOKEN, data={'grant_type': 'refresh_token', 'refresh_token': rt, 'client_id': client_id},
                       auth=_basic(conn), timeout=TIMEOUT), 'X token refresh')
    _tokens(conn, d)
    return True


def validate(conn):
    me = check(net.get(f"{API}/users/me", headers=_h(conn), params={'user.fields': 'public_metrics'},
                       timeout=TIMEOUT), 'X profile').get('data') or {}
    conn.save_meta({'user_id': me.get('id', ''), 'username': me.get('username', ''),
                    'identity': f"@{me.get('username', '')}"})
    return True, f"@{me.get('username')}: {(me.get('public_metrics') or {}).get('followers_count', '?')} followers"


def post(conn, text, images, link, disclose):
    media_ids = []
    for raw, mt in images:
        d = check(net.post(f"{API}/media/upload", headers=_h(conn), data={'media_category': 'tweet_image'},
                           files={'media': ('image', raw, mt)}, timeout=120), 'X media upload')
        media_ids.append(str((d.get('data') or {}).get('id') or d.get('media_id_string') or ''))
    body = {'text': f"{text} {link}".strip() if link else text}
    if media_ids:
        body['media'] = {'media_ids': [m for m in media_ids if m]}
    d = check(net.post(f"{API}/tweets", headers=_h(conn), json=body, timeout=TIMEOUT), 'X post')
    tid = (d.get('data') or {}).get('id')
    return {'id': tid, 'url': f"https://x.com/{conn.meta.get('username') or 'i'}/status/{tid}"}


def inbox(conn, since):
    uid, = need(conn, conn.meta, 'user_id')
    d = check(net.get(f"{API}/users/{uid}/mentions", headers=_h(conn), params={
        'start_time': iso_utc(max(since, time.time() - 6.9 * 86400)), 'max_results': 50,
        'tweet.fields': 'created_at,author_id', 'expansions': 'author_id', 'user.fields': 'username'},
        timeout=TIMEOUT), 'X mentions')
    users = {u.get('id'): u.get('username', '?') for u in (d.get('includes') or {}).get('users') or []}
    return [{'id': t['id'], 'kind': 'mention', 'author': '@' + users.get(t.get('author_id'), '?'),
             'text': t.get('text', ''), 'ts': iso_ts(t.get('created_at')),
             'url': f"https://x.com/i/status/{t['id']}"} for t in d.get('data') or []]


def reply(conn, item_id, text):
    d = check(net.post(f"{API}/tweets", headers=_h(conn), json={'text': text, 'reply': {'in_reply_to_tweet_id': str(item_id)}},
                       timeout=TIMEOUT), 'X reply')
    tid = (d.get('data') or {}).get('id')
    return {'id': tid, 'url': f"https://x.com/i/status/{tid}" if tid else ''}


def delete(conn, post_id):
    d = check(net.delete(f"{API}/tweets/{post_id}", headers=_h(conn), timeout=TIMEOUT), 'X delete')
    return bool((d.get('data') or {}).get('deleted'))


def stats(conn):
    me = check(net.get(f"{API}/users/me", headers=_h(conn), params={'user.fields': 'public_metrics'},
                       timeout=TIMEOUT), 'X profile').get('data') or {}
    pm = me.get('public_metrics') or {}
    return {'followers': pm.get('followers_count'), 'following': pm.get('following_count'), 'posts': pm.get('tweet_count')}
