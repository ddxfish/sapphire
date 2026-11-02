"""Social plugin core: registry, manifest scan, store (secrets in core's
credentials, wiring in plugin state), the Outbox, the engine's mode gate, the
tools, the routes and the OAuth round trip - all against a fake provider.
No network, no app boot."""
import io
import sys
import time
import types
from types import SimpleNamespace
from unittest.mock import patch

import pytest


@pytest.fixture
def social(tmp_path, monkeypatch):
    from core.chat.function_manager import register_plugin_scope
    register_plugin_scope('social', plugin_name='social')     # plugin tests run under the root conftest
    import core.plugin_loader as pl
    monkeypatch.setattr(pl, 'PLUGIN_STATE_DIR', tmp_path / 'state')
    state = pl.PluginState('social')
    monkeypatch.setattr(pl.plugin_loader, 'get_plugin_state', lambda name: state)
    monkeypatch.setattr(pl.plugin_loader, 'get_plugin_settings', lambda name: {'SOCIAL_DEFAULT_MODE': 'draft'})
    creds_file = tmp_path / 'credentials.json'
    with patch('core.credentials_manager.CREDENTIALS_FILE', creds_file), \
         patch('core.credentials_manager.SCRAMBLE_SALT_FILE', tmp_path / '.salt'), \
         patch('core.credentials_manager.CONFIG_DIR', tmp_path):
        import core.credentials_manager as cm
        mgr = cm.CredentialsManager()
        monkeypatch.setattr(cm, 'credentials', mgr)
        from plugins.social import engine, outbox, registry, store
        monkeypatch.setattr(outbox, 'DIR', tmp_path / 'outbox')
        registry.reset()
        fake = types.ModuleType('plugins.social.providers.fake')
        fake.calls = []

        def post(conn, text, images, link, disclose):
            fake.calls.append(('post', conn.name, text, [mt for _, mt in images], link, disclose))
            return {'id': 'p1', 'url': 'https://example.com/p1'}

        def inbox(conn, since):
            return [{'id': 'c1', 'kind': 'comment', 'author': 'bob', 'text': 'hi there', 'ts': time.time(), 'url': ''}]

        def reply(conn, item_id, text):
            fake.calls.append(('reply', conn.name, item_id, text))
            return {'id': 'r1'}

        fake.post, fake.inbox, fake.reply = post, inbox, reply
        fake.stats = lambda conn: {'followers': 3}
        fake.validate = lambda conn: (True, 'fine')
        monkeypatch.setitem(sys.modules, 'plugins.social.providers.fake', fake)
        spec = {'label': 'Fake', 'icon': 'F', 'module': 'providers/fake.py', 'auth': 'fields',
                'capabilities': ['post', 'image', 'link', 'inbox', 'reply', 'stats'], 'max_chars': 50, 'max_images': 2,
                'config_schema': [{'key': 'handle', 'type': 'string'},
                                  {'key': 'token', 'type': 'password', 'secret': True}]}
        assert registry.register_provider('fake', spec, 'social')
        yield SimpleNamespace(registry=registry, store=store, outbox=outbox, engine=engine, fake=fake,
                              creds=mgr, creds_file=creds_file, state=state, spec=spec, tmp=tmp_path)
        registry.reset()


def _wire(s, mode='draft'):
    """A profile 'anita' with one fake connection 'main'."""
    assert s.store.put_profile('Anita', 'Anita B')[0]
    ok, r = s.store.put_connection('anita', 'main', 'fake', mode=mode, config={'handle': 'anita', 'token': 'tok-1'})
    assert ok, r
    return s.store.get_connection('anita', 'main')


# ── registry ────────────────────────────────────────────────────────────────

def test_registry_validates_and_dedupes(social):
    r, spec = social.registry, social.spec
    gen = r.generation()
    assert r.register_provider('fake', spec, 'social') and r.generation() == gen       # identical: no bump
    assert not r.register_provider('fake', spec, 'other-plugin')                        # owner conflict
    assert not r.register_provider('Bad Id', spec, 'p')
    assert not r.register_provider('a', {**spec, 'module': '../x.py'}, 'p')
    assert not r.register_provider('a', {**spec, 'auth': 'magic'}, 'p')
    assert not r.register_provider('a', {**spec, 'capabilities': ['fly']}, 'p')
    assert not r.register_provider('a', {**spec, 'config_schema': [{'label': 'no key'}]}, 'p')
    assert [p['provider'] for p in r.list_providers()] == ['fake']
    assert r.get_provider('FAKE')['max_chars'] == 50 and r.get_provider('nope') is None


def test_scan_registers_from_manifests_and_drops_gone_plugins(social, monkeypatch):
    from plugins.social import scan
    decl = {'provider': 'bird', 'module': 'provider.py', 'auth': 'fields', 'capabilities': ['post'], 'config_schema': []}
    infos = [{'name': 'social', 'enabled': True, 'loaded': False, 'manifest': {'capabilities': {}}},
             {'name': 'social-bird', 'enabled': True, 'loaded': True, 'manifest': {'capabilities': {'social': [decl]}}},
             {'name': 'off-plugin', 'enabled': False, 'loaded': False, 'manifest': {'capabilities': {'social': [{**decl, 'provider': 'ghost'}]}}}]
    import core.plugin_loader as pl
    monkeypatch.setattr(pl.plugin_loader, 'get_all_plugin_info', lambda: infos)
    assert scan.pull() == 1
    assert social.registry.get_provider('bird')['plugin_name'] == 'social-bird'
    assert social.registry.get_provider('ghost') is None
    infos.pop(1)
    scan.pull()
    assert social.registry.get_provider('bird') is None


# ── store ───────────────────────────────────────────────────────────────────

def test_store_splits_secrets_from_wiring(social):
    conn = _wire(social)
    assert conn.provider == 'fake' and conn.mode == 'draft' and conn.disclose and conn.connected
    assert conn.config == {'handle': 'anita'} and conn.secrets == {'token': 'tok-1'}
    raw = social.creds_file.read_text(encoding='utf-8')
    assert 'tok-1' not in raw and 'anita' in raw
    assert 'tok' not in social.state._path.read_text(encoding='utf-8')
    rows = social.store.list_profiles()
    assert rows[0]['name'] == rows[0]['scope'] == 'anita' and rows[0]['label'] == 'Anita B'
    c = rows[0]['connections'][0]
    assert c['secrets_set'] == ['token'] and c['config'] == {'handle': 'anita'} and 'tok-1' not in str(c)
    # None keeps the secret, a str replaces, '' clears
    social.store.put_connection('anita', 'main', 'fake', config={'token': None, 'handle': 'anita2'})
    assert social.store.get_connection('anita', 'main').secrets == {'token': 'tok-1'}
    social.store.put_connection('anita', 'main', 'fake', config={'token': ''})
    assert not social.store.get_connection('anita', 'main').connected
    assert social.store.put_connection('anita', 'main', 'nope', config={})[0] is False
    assert social.store.put_connection('anita', '!!!', 'fake', config={})[0] is False
    assert social.store.delete_profile('anita')
    assert social.creds.list_plugin_accounts('social') == [] and social.store.profiles() == {}


# ── engine: mode gate, outbox, reads ────────────────────────────────────────

def test_draft_mode_queues_then_approve_posts(social, monkeypatch):
    _wire(social, 'draft')
    notified = []
    monkeypatch.setattr(social.outbox, 'notify_pending', lambda: notified.append(1))
    out = social.engine.post('anita', '', 'hello', [(b'IMG', 'image/png', None)], 'https://x.y/z')
    assert 'queued as draft' in out and notified == [1] and social.fake.calls == []
    row = social.outbox.pending()[0]
    assert row['text'] == 'hello' and row['link'] == 'https://x.y/z' and len(row['images']) == 1
    assert (social.outbox.DIR / row['images'][0]).read_bytes() == b'IMG'
    ok, url = social.engine.approve(row['id'])
    assert ok and url == 'https://example.com/p1'
    assert social.fake.calls == [('post', 'main', 'hello', ['image/png'], 'https://x.y/z', True)]
    after = social.outbox.get(row['id'])
    assert after['status'] == 'posted' and after['result']['url'] == 'https://example.com/p1'
    assert social.engine.approve(row['id']) == (False, f"Draft {row['id']} is already posted.")
    assert social.engine.reject('nope')[0] is False


def test_auto_mode_posts_now_and_logs(social):
    _wire(social, 'auto')
    out = social.engine.post('anita', 'fake', 'hi', [], '')
    assert out == 'main [fake]: posted https://example.com/p1.'
    assert social.outbox.rows('posted')[0]['result']['id'] == 'p1' and social.outbox.pending() == []
    too_long = social.engine.post('anita', 'main', 'x' * 51)
    assert 'too long' in too_long and len(social.fake.calls) == 1
    with pytest.raises(social.engine.SocialError, match='No connection ghost'):
        social.engine.post('anita', 'ghost', 'hi')
    with pytest.raises(social.engine.SocialError, match='nothing to post'):
        social.engine.post('anita', '', '')


def test_failed_auto_post_is_logged_not_raised(social):
    _wire(social, 'auto')

    def boom(conn, text, images, link, disclose):
        raise social.engine.SocialError('platform said no')
    social.fake.post = boom
    out = social.engine.post('anita', '', 'hi')
    assert out == 'main [fake]: platform said no'
    assert social.outbox.rows('failed')[0]['result']['error'] == 'platform said no'


def test_reads_and_reply(social):
    _wire(social, 'auto')
    text = social.engine.inbox('anita', '', 24)
    assert '1 new' in text and '[c1] comment bob' in text and 'hi there' in text
    assert social.engine.stats('anita') == 'main [fake]: followers 3'
    assert social.engine.reply('anita', 'main', 'c1', 'thanks') == 'main [fake]: replied.'
    assert social.fake.calls[-1] == ('reply', 'main', 'c1', 'thanks')
    with pytest.raises(social.engine.SocialError):
        social.engine.delete('anita', 'main', 'p1')          # no delete capability


def test_resolve_profile_follows_the_scope(social):
    from core.chat.function_manager import scope_social
    e = social.engine
    assert e.resolve_profile()[0] is None                   # no profiles
    _wire(social)
    assert e.resolve_profile() == ('anita', None)           # one profile, default scope
    social.store.put_profile('sapphire')
    tok = scope_social.set('default')
    try:
        assert e.resolve_profile()[0] is None               # two profiles, none picked
        scope_social.set('sapphire')
        assert e.resolve_profile() == ('sapphire', None)
        scope_social.set(None)
        assert 'off for this chat' in e.resolve_profile()[1]
        assert e.resolve_profile('Anita') == ('anita', None)
        assert 'No profile' in e.resolve_profile('ghost')[1]
    finally:
        scope_social.reset(tok)
    assert 'anita' in e.list_text() and 'sapphire' in e.list_text()


def test_provider_off_is_said(social):
    conn = _wire(social)
    social.registry.reset()
    with pytest.raises(social.engine.SocialError, match='not loaded'):
        social.engine.module_for(conn)


# ── tools ───────────────────────────────────────────────────────────────────

def _png():
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (200, 120), 'blue').save(buf, 'PNG')
    return buf.getvalue()


def test_tools_post_and_meme(social, monkeypatch):
    import plugins.social.tools.social as tools
    from core import images as ci
    _wire(social, 'auto')
    png = _png()
    monkeypatch.setattr(ci, 'resolve', lambda h, **k: ci.Resolved(png, 'image/png', h, 'chat'))
    msg, ok = tools.execute('social_list', {}, None, {}, None)
    assert ok and 'anita' in msg
    msg, ok = tools.execute('social_post', {'text': 'look', 'image': 'img:abc'}, None, {}, None)
    assert ok and 'posted' in msg and social.fake.calls[-1][3] == ['image/png']
    out = tools.execute('social_meme', {'image': 'img:abc', 'top': 'one does not', 'bottom': 'simply post'}, None, {}, None)
    assert isinstance(out, dict) and out['images'][0]['media_type'] == 'image/jpeg'
    msg, ok = tools.execute('social_post', {'text': 'x', 'profile': 'ghost'}, None, {}, None)
    assert not ok and 'No profile' in msg
    names = {t['function']['name'] for t in tools.get_tools()}
    assert names == set(tools.AVAILABLE_FUNCTIONS)
    assert 'anita' in next(t for t in tools.get_tools() if t['function']['name'] == 'social_post')['function']['description']


# ── routes ──────────────────────────────────────────────────────────────────

def test_manage_routes(social):
    import plugins.social.routes.manage as mg
    assert mg.put_profile('anita', body={'label': 'A'}) == {'success': True, 'name': 'anita'}
    assert mg.put_profile('!!!')[1] == 400
    d = mg.put_connection('anita', 'main', body={'provider': 'fake', 'config': {'handle': 'a', 'token': 't'}})
    assert d['success'] and d['connection']['connected'] and d['connection']['secrets_set'] == ['token']
    assert mg.test_connection('anita', 'main')['detail'] == 'fine'
    assert mg.list_profiles()['profiles'][0]['connections'][0]['name'] == 'main'
    assert [p['provider'] for p in mg.list_providers()['providers']] == ['fake']
    social.engine.post('anita', '', 'draft me', [(b'IMG', 'image/png', None)])
    lo = mg.list_outbox()
    assert lo['pending'] == 1 and lo['rows'][0]['image_count'] == 1 and 'images' not in lo['rows'][0]
    rid = lo['rows'][0]['id']
    assert mg.outbox_image(rid, 0).path.endswith('.png')
    assert mg.outbox_image(rid, 5)[1] == 404
    assert mg.approve(rid)['success'] and mg.list_outbox()['pending'] == 0
    assert mg.clear_outbox()['removed'] == 1
    assert mg.delete_connection('anita', 'main') == {'success': True}
    assert mg.delete_connection('anita', 'main')[1] == 404
    assert mg.delete_profile('anita') == {'success': True}


def test_make_round_builds_a_scoped_task(social, monkeypatch):
    import plugins.social.routes.manage as mg
    _wire(social)
    made = []
    sched = SimpleNamespace(create_task=lambda data: made.append(data) or {'id': 't1'})
    import core.api_fastapi as api
    monkeypatch.setattr(api, 'get_system', lambda: SimpleNamespace(continuity_scheduler=sched))
    d = mg.make_round(body={'profile': 'anita', 'persona': 'Anita', 'time': '7:30', 'chat': 'social'})
    assert d == {'success': True, 'task_id': 't1', 'name': 'Social round: anita', 'time': '07:30'}
    t = made[0]
    assert t['schedule'] == '30 7 * * *' and t['persona'] == 'Anita' and t['social_scope'] == 'anita'
    assert t['chat_target'] == 'social' and 'social_inbox' in t['initial_message']
    assert mg.make_round(body={'profile': 'anita', 'time': '25:00'})[1] == 400
    assert mg.make_round(body={'profile': 'ghost'})[1] == 400


def test_oauth_round_trip(social, monkeypatch):
    import plugins.social.routes.oauth as oa
    monkeypatch.setattr(oa, 'CSRF_PATH', social.tmp / 'csrf.json')
    seen = {}
    social.fake.auth_start = lambda conn, redirect_uri, state: (f"https://vendor/auth?state={state}&r={redirect_uri}", {'verifier': 'v1'})

    def auth_finish(conn, redirect_uri, query, stash):
        seen.update(code=query.get('code'), stash=stash, redirect=redirect_uri)
        return {'secrets': {'access_token': 'at-1'}, 'meta': {'identity': '@anita'}}
    social.fake.auth_finish = auth_finish
    social.registry.reset()
    social.registry.register_provider('fake', {**social.spec, 'auth': 'oauth'}, 'social')
    conn = _wire(social)
    assert not conn.connected                                   # oauth: connected means a finished round trip
    req = SimpleNamespace(base_url='https://localhost:8073/')
    resp = oa.connect('fake', 'anita', 'main', request=req)
    assert resp.status_code == 302 and 'https://vendor/auth?state=' in resp.headers['location']
    state = resp.headers['location'].split('state=')[1].split('&')[0]
    assert 'callback/fake' in resp.headers['location']
    bad = oa.callback('fake', request=req, query={'state': 'wrong', 'code': 'c'})
    assert 'social_error' in bad.headers['location']
    good = oa.callback('fake', request=req, query={'state': state, 'code': 'the-code'})
    assert good.headers['location'] == '/#settings'
    assert seen == {'code': 'the-code', 'stash': {'verifier': 'v1'}, 'redirect': 'https://localhost:8073/api/plugin/social/callback/fake'}
    conn = social.store.get_connection('anita', 'main')
    assert conn.connected and conn.identity == '@anita' and conn.secrets['access_token'] == 'at-1'
    assert social.state.get('last_connect')['ok'] is True
    assert oa.connect('fake', 'anita', 'ghost', request=req)[1] == 404
    assert oa.callback('fake', request=req, query={'state': state, 'code': 'again'}).headers['location'] != '/#settings'
