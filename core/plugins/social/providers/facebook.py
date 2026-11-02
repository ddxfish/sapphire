# core/plugins/social/providers/facebook.py - a Facebook Page through the Graph API
#
# Declared in ../plugin.json (capabilities.social). The person's own Meta app
# (owner role = Standard Access, no App Review for their own Pages). The Page
# token never expires. Personal profiles cannot post by API, Pages can.
import json

from . import _meta
from ._common import iso_ts, need

SCOPES = ['pages_show_list', 'pages_manage_posts', 'pages_read_engagement',
          'pages_read_user_content', 'pages_manage_engagement']


def _page(conn):
    page_id, = need(conn, conn.meta, 'page_id')
    token, = need(conn, conn.secrets, 'page_token')
    return page_id, token


def auth_start(conn, redirect_uri, state):
    return _meta.auth_start(conn, redirect_uri, state, SCOPES)


def auth_finish(conn, redirect_uri, query, stash):
    token = _meta.user_token(conn, redirect_uri, query)
    page = _meta.choose_page(conn, _meta.pages(token))
    return {'secrets': {'page_token': page['access_token']},
            'meta': {'page_id': page['id'], 'identity': page.get('name', ''),
                     'url': f"https://www.facebook.com/{page['id']}"}}


def validate(conn):
    page_id, token = _page(conn)
    d = _meta.get(f"/{page_id}", token, 'Facebook Page', fields='id,name,fan_count,followers_count')
    conn.save_meta({'identity': d.get('name', '')})
    return True, f"Page {d.get('name')}: {d.get('followers_count', d.get('fan_count', '?'))} followers"


def post(conn, text, images, link, disclose):
    page_id, token = _page(conn)
    if not images:
        data = {'message': text}
        if link:
            data['link'] = link
        d = _meta.post(f"/{page_id}/feed", token, 'Facebook post', data=data)
    elif len(images) == 1:
        raw, mt = images[0]
        d = _meta.post(f"/{page_id}/photos", token, 'Facebook photo', data={'message': text},
                       files={'source': ('image', raw, mt)})
    else:
        data = {'message': text}
        for n, (raw, mt) in enumerate(images):
            up = _meta.post(f"/{page_id}/photos", token, 'Facebook photo', data={'published': 'false'},
                            files={'source': ('image', raw, mt)})
            data[f'attached_media[{n}]'] = json.dumps({'media_fbid': up['id']})
        d = _meta.post(f"/{page_id}/feed", token, 'Facebook post', data=data)
    post_id = d.get('post_id') or d.get('id')
    return {'id': post_id, 'url': f"https://www.facebook.com/{post_id}"}


def inbox(conn, since):
    page_id, token = _page(conn)
    items = []
    feed = _meta.get(f"/{page_id}/feed", token, 'Facebook feed', limit=25,
                     fields='id,permalink_url,comments.limit(50){id,message,from,created_time}')
    for p in feed.get('data') or []:
        for c in ((p.get('comments') or {}).get('data') or []):
            ts = iso_ts(c.get('created_time'))
            who = c.get('from') or {}
            if ts < since or str(who.get('id')) == str(page_id):
                continue
            items.append({'id': c['id'], 'kind': 'comment', 'author': who.get('name', '?'),
                          'text': c.get('message', ''), 'ts': ts, 'url': p.get('permalink_url', '')})
    tagged = _meta.get(f"/{page_id}/tagged", token, 'Facebook mentions', limit=25,
                       fields='id,message,from,created_time,permalink_url')
    for t in tagged.get('data') or []:
        ts = iso_ts(t.get('created_time'))
        if ts < since:
            continue
        items.append({'id': t['id'], 'kind': 'mention', 'author': (t.get('from') or {}).get('name', '?'),
                      'text': t.get('message', ''), 'ts': ts, 'url': t.get('permalink_url', '')})
    items.sort(key=lambda i: i['ts'], reverse=True)
    return items


def reply(conn, item_id, text):
    _, token = _page(conn)
    d = _meta.post(f"/{item_id}/comments", token, 'Facebook reply', data={'message': text})
    return {'id': d.get('id')}


def delete(conn, post_id):
    _, token = _page(conn)
    d = _meta.delete(f"/{post_id}", token, 'Facebook delete')
    return bool(d.get('success', True))


def stats(conn):
    page_id, token = _page(conn)
    d = _meta.get(f"/{page_id}", token, 'Facebook Page', fields='fan_count,followers_count')
    return {'followers': d.get('followers_count'), 'likes': d.get('fan_count')}
