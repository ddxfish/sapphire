# core/plugins/social/providers/instagram.py - an Instagram Business/Creator account
# through the Graph API, by way of the Facebook Page it is linked to.
#
# Instagram takes no bytes: every image is fetched from a public URL, so the
# engine hands this provider URLs (the drop folder). A post is a container
# then a publish. `disclose` sets the official AI label (is_ai_generated).
import time

from . import _meta
from ._common import iso_ts, need
from plugins.social.errors import SocialError

SCOPES = ['pages_show_list', 'pages_read_engagement', 'instagram_basic',
          'instagram_content_publish', 'instagram_manage_comments']


def _ig(conn):
    ig_id, = need(conn, conn.meta, 'ig_user_id')
    token, = need(conn, conn.secrets, 'page_token')
    return ig_id, token


def auth_start(conn, redirect_uri, state):
    return _meta.auth_start(conn, redirect_uri, state, SCOPES)


def auth_finish(conn, redirect_uri, query, stash):
    token = _meta.user_token(conn, redirect_uri, query)
    page = _meta.choose_page(conn, _meta.pages(token))
    d = _meta.get(f"/{page['id']}", page['access_token'], 'Instagram account',
                  fields='instagram_business_account{id,username}')
    ig = d.get('instagram_business_account') or {}
    if not ig.get('id'):
        raise SocialError(f"the Page '{page.get('name')}' has no Instagram Business or Creator account linked "
                          "(Instagram → Settings → Linked accounts → Facebook)")
    user = ig.get('username', '')
    return {'secrets': {'page_token': page['access_token']},
            'meta': {'ig_user_id': ig['id'], 'page_id': page['id'], 'identity': f"@{user}" if user else '',
                     'username': user, 'url': f"https://www.instagram.com/{user}/" if user else ''}}


def validate(conn):
    ig_id, token = _ig(conn)
    d = _meta.get(f"/{ig_id}", token, 'Instagram account', fields='username,followers_count,media_count')
    conn.save_meta({'identity': f"@{d.get('username', '')}", 'username': d.get('username', '')})
    return True, f"@{d.get('username')}: {d.get('followers_count', '?')} followers, {d.get('media_count', '?')} posts"


def post(conn, text, images, link, disclose):
    ig_id, token = _ig(conn)
    if not images:
        raise SocialError('Instagram needs an image with every post')
    data = {'image_url': images[0], 'caption': text}
    if disclose:
        data['is_ai_generated'] = 'true'
    made = _meta.post(f"/{ig_id}/media", token, 'Instagram container', data=data)
    cid = made.get('id')
    if not cid:
        raise SocialError('Instagram gave no container id')
    for _ in range(10):
        st = _meta.get(f"/{cid}", token, 'Instagram container', fields='status_code,status')
        code = st.get('status_code')
        if code == 'FINISHED':
            break
        if code == 'ERROR':
            raise SocialError(f"Instagram could not take the image: {st.get('status', '')}")
        time.sleep(2)
    pub = _meta.post(f"/{ig_id}/media_publish", token, 'Instagram publish', data={'creation_id': cid})
    mid = pub.get('id')
    url = ''
    try:
        url = _meta.get(f"/{mid}", token, 'Instagram post', fields='permalink').get('permalink', '')
    except SocialError:
        pass
    return {'id': mid, 'url': url}


def inbox(conn, since):
    ig_id, token = _ig(conn)
    me = str(conn.meta.get('username') or '').lower()
    items = []
    d = _meta.get(f"/{ig_id}/media", token, 'Instagram media', limit=20,
                  fields='id,permalink,comments.limit(50){id,text,username,timestamp}')
    for m in d.get('data') or []:
        for c in ((m.get('comments') or {}).get('data') or []):
            ts = iso_ts(c.get('timestamp'))
            if ts < since or str(c.get('username', '')).lower() == me:
                continue
            items.append({'id': c['id'], 'kind': 'comment', 'author': '@' + str(c.get('username', '?')),
                          'text': c.get('text', ''), 'ts': ts, 'url': m.get('permalink', '')})
    items.sort(key=lambda i: i['ts'], reverse=True)
    return items


def reply(conn, item_id, text):
    _, token = _ig(conn)
    d = _meta.post(f"/{item_id}/replies", token, 'Instagram reply', data={'message': text})
    return {'id': d.get('id')}


def stats(conn):
    ig_id, token = _ig(conn)
    d = _meta.get(f"/{ig_id}", token, 'Instagram account', fields='followers_count,follows_count,media_count')
    return {'followers': d.get('followers_count'), 'following': d.get('follows_count'), 'posts': d.get('media_count')}
