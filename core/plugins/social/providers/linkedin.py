# core/plugins/social/providers/linkedin.py - a personal LinkedIn profile, write-only
#
# Self-serve products (Share on LinkedIn + Sign In with OpenID Connect) give
# w_member_social: posts, images, articles, delete. Nothing comes back: no
# comments, no mentions (partner-gated). The token lasts 60 days and has no
# refresh - Connect again. Company pages need a verified-business program.
import time
from urllib.parse import quote, urlencode

from core import net
from ._common import check, need
from plugins.social.errors import SocialError

AUTH = 'https://www.linkedin.com/oauth/v2/authorization'
TOKEN = 'https://www.linkedin.com/oauth/v2/accessToken'
API = 'https://api.linkedin.com'
VERSION = '202610'            # YYYYMM; LinkedIn keeps a version about a year
SCOPES = 'openid profile w_member_social'
TIMEOUT = 30
_ESCAPE = '\\|{}@[]()<>#*_~'


def _h(conn, json=True):
    token, = need(conn, conn.secrets, 'access_token')
    h = {'Authorization': f'Bearer {token}', 'LinkedIn-Version': VERSION, 'X-Restli-Protocol-Version': '2.0.0'}
    if json:
        h['Content-Type'] = 'application/json'
    return h


def _commentary(text):
    """LinkedIn's little text format: these characters must be escaped."""
    return ''.join(('\\' + ch) if ch in _ESCAPE else ch for ch in text)


def auth_start(conn, redirect_uri, state):
    client_id, = need(conn, conn.config, 'client_id')
    return AUTH + '?' + urlencode({'response_type': 'code', 'client_id': client_id, 'redirect_uri': redirect_uri,
                                   'state': state, 'scope': SCOPES})


def auth_finish(conn, redirect_uri, query, stash):
    client_id, = need(conn, conn.config, 'client_id')
    client_secret, = need(conn, conn.secrets, 'client_secret')
    code = query.get('code')
    if not code:
        raise SocialError('LinkedIn sent no authorization code')
    d = check(net.post(TOKEN, data={'grant_type': 'authorization_code', 'code': code, 'redirect_uri': redirect_uri,
                                    'client_id': client_id, 'client_secret': client_secret}, timeout=TIMEOUT),
              'LinkedIn token exchange')
    if not d.get('access_token'):
        raise SocialError('LinkedIn token exchange gave no token')
    me = check(net.get(f"{API}/v2/userinfo", headers={'Authorization': f"Bearer {d['access_token']}"},
                       timeout=TIMEOUT), 'LinkedIn profile')
    if not me.get('sub'):
        raise SocialError('LinkedIn gave no member id (is Sign In with LinkedIn using OpenID Connect added to the app?)')
    return {'secrets': {'access_token': d['access_token']},
            'meta': {'person_urn': f"urn:li:person:{me['sub']}", 'identity': me.get('name', ''),
                     'expires_at': time.time() + float(d.get('expires_in') or 5184000)}}


def validate(conn):
    me = check(net.get(f"{API}/v2/userinfo", headers=_h(conn, json=False), timeout=TIMEOUT), 'LinkedIn profile')
    conn.save_meta({'identity': me.get('name', '')})
    days = int((float(conn.meta.get('expires_at') or 0) - time.time()) / 86400)
    return True, f"{me.get('name')}; token good for {max(days, 0)} more days"


def post(conn, text, images, link, disclose):
    author, = need(conn, conn.meta, 'person_urn')
    body = {'author': author, 'commentary': _commentary(text), 'visibility': 'PUBLIC',
            'distribution': {'feedDistribution': 'MAIN_FEED', 'targetEntities': [], 'thirdPartyDistributionChannels': []},
            'lifecycleState': 'PUBLISHED', 'isReshareDisabledByAuthor': False}
    if images:
        raw, mt = images[0]
        init = check(net.post(f"{API}/rest/images?action=initializeUpload", headers=_h(conn),
                              json={'initializeUploadRequest': {'owner': author}}, timeout=TIMEOUT),
                     'LinkedIn image').get('value') or {}
        if not init.get('uploadUrl') or not init.get('image'):
            raise SocialError('LinkedIn gave no upload slot for the image')
        up = net.put(init['uploadUrl'], data=raw, timeout=120,
                     headers={'Authorization': _h(conn)['Authorization'], 'Content-Type': 'application/octet-stream'})
        if up.status_code >= 400:
            raise SocialError(f"LinkedIn image upload: HTTP {up.status_code}")
        body['content'] = {'media': {'id': init['image'], 'altText': text[:200]}}
    elif link:
        body['content'] = {'article': {'source': link, 'title': text[:100] or link}}
    r = net.post(f"{API}/rest/posts", headers=_h(conn), json=body, timeout=TIMEOUT)
    check(r, 'LinkedIn post')
    urn = r.headers.get('x-restli-id') or r.headers.get('X-RestLi-Id') or ''
    return {'id': urn, 'url': f"https://www.linkedin.com/feed/update/{urn}" if urn else ''}


def delete(conn, post_id):
    r = net.delete(f"{API}/rest/posts/{quote(str(post_id), safe='')}", headers=_h(conn), timeout=TIMEOUT)
    check(r, 'LinkedIn delete')
    return r.status_code in (200, 204)
