# core/plugins/social/providers/_meta.py - the Meta Graph round trip Facebook and
# Instagram share: one app, user token → long-lived → the Page's own token.
from urllib.parse import urlencode

from core import net
from plugins.social.errors import SocialError
from ._common import check, need

VERSION = 'v26.0'
GRAPH = f'https://graph.facebook.com/{VERSION}'
DIALOG = f'https://www.facebook.com/{VERSION}/dialog/oauth'
TIMEOUT = 30


def auth_start(conn, redirect_uri, state, scopes):
    app_id, = need(conn, conn.config, 'app_id')
    return DIALOG + '?' + urlencode({'client_id': app_id, 'redirect_uri': redirect_uri, 'state': state,
                                     'scope': ','.join(scopes), 'response_type': 'code'})


def user_token(conn, redirect_uri, query):
    """The code → a long-lived user token."""
    app_id, = need(conn, conn.config, 'app_id')
    app_secret, = need(conn, conn.secrets, 'app_secret')
    code = query.get('code')
    if not code:
        raise SocialError('Meta sent no authorization code')
    d = check(net.get(f"{GRAPH}/oauth/access_token", params={
        'client_id': app_id, 'redirect_uri': redirect_uri, 'client_secret': app_secret, 'code': code},
        timeout=TIMEOUT), 'Meta token exchange')
    short = d.get('access_token')
    if not short:
        raise SocialError('Meta token exchange gave no token')
    d2 = check(net.get(f"{GRAPH}/oauth/access_token", params={
        'grant_type': 'fb_exchange_token', 'client_id': app_id, 'client_secret': app_secret,
        'fb_exchange_token': short}, timeout=TIMEOUT), 'Meta long-lived token')
    return d2.get('access_token') or short


def pages(token):
    d = check(net.get(f"{GRAPH}/me/accounts", params={'fields': 'id,name,access_token', 'limit': 100,
                                                      'access_token': token}, timeout=TIMEOUT), 'Meta Pages')
    return [p for p in (d.get('data') or []) if p.get('access_token')]


def choose_page(conn, found):
    if not found:
        raise SocialError('this login manages no Facebook Page with posting rights; '
                          'a Page (not a personal profile) is what the API posts to')
    want = str(conn.config.get('page') or '').strip().lower()
    if not want:
        return found[0]
    for p in found:
        if want in (str(p.get('id')), str(p.get('name', '')).lower()):
            return p
    raise SocialError(f"no Page called '{want}'; you manage: {', '.join(p.get('name', '?') for p in found)}")


def get(path, token, what='Meta', **params):
    params['access_token'] = token
    return check(net.get(f"{GRAPH}{path}", params=params, timeout=TIMEOUT), what)


def post(path, token, what='Meta', data=None, files=None):
    data = dict(data or {})
    data['access_token'] = token
    return check(net.post(f"{GRAPH}{path}", data=data, files=files, timeout=120 if files else TIMEOUT), what)


def delete(path, token, what='Meta'):
    return check(net.delete(f"{GRAPH}{path}", params={'access_token': token}, timeout=TIMEOUT), what)
