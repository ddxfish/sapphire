"""Surface 1 P1/P2 — install replace flow, rescan, reload, check-update, settings CRUD.

Complements:
  test_regression_guards_plugin_routes.py (R1-R4)
  test_plugin_routes_p0.py                (SSRF, re-verify, list merge)
  test_plugin_routes_p1.py                (toggle edges, install security, uninstall safety)

Covers:
  1.25 install without force returns 409 with existing meta
  1.33 uninstall publishes TOOLSET_CHANGED with action='plugin_uninstall'
  1.34 rescan returns added + removed arrays
  1.35 rescan wraps loader exception as 500
  1.36 reload on disabled plugin returns 400
  1.37 reload propagates load failure
  1.38 check-update: newer version → update_available=true
  1.39 check-update: no source_url → update_available=false, reason=no_source
  1.40 check-update: non-numeric version tuples handled gracefully
  1.44 settings CRUD requires known plugin (404 for GET/PUT/DELETE)
  1.45 DELETE settings is idempotent

See tmp/coverage-test-plan.md Surface 1 P1/P2.
"""
import io
import json
import threading
import zipfile
from unittest.mock import MagicMock

import pytest


def _mock_info(name, enabled=True, loaded=True, band='user', version='1.0.0',
               essential=False):
    return {
        "name": name,
        "enabled": enabled,
        "loaded": loaded,
        "verified": True,
        "verify_msg": "verified",
        "verify_tier": "official",
        "verified_author": None,
        "band": band,
        "path": f"/tmp/fake/plugins/{name}",
        "manifest": {
            "name": name, "version": version, "description": "t",
            "author": "sapphire-core", "essential": essential,
        },
        "missing_deps": [],
    }


# ─── 1.25 Install without force → 409 with existing meta ─────────────────────

def test_install_without_force_returns_409_with_existing_meta(
    client, temp_user_dir, monkeypatch,
):
    """[PROACTIVE] POST /api/plugins/install on an existing plugin (no force)
    must return 409 with the existing version/author so the client can show
    a 'replace this?' dialog.
    """
    # Pre-create an existing plugin directory at USER_PLUGINS_DIR/{name}
    existing = temp_user_dir / 'plugins' / 'my_plugin'
    existing.mkdir(parents=True)
    (existing / 'plugin.json').write_text(json.dumps({
        'name': 'my_plugin', 'version': '1.0.0', 'author': 'orig_author',
        'description': 'test',
    }))

    # Patch USER_PLUGINS_DIR in the route module to point at our tmp
    import core.routes.plugins as rp
    monkeypatch.setattr(rp, 'USER_PLUGINS_DIR', temp_user_dir / 'plugins',
                        raising=False)
    # plugin_loader route imports
    import core.plugin_loader as pl
    monkeypatch.setattr(pl, 'USER_PLUGINS_DIR', temp_user_dir / 'plugins')
    mock_pl = MagicMock()
    mock_pl.get_plugin_info.return_value = _mock_info('my_plugin', band='user')
    mock_pl._lock = threading.RLock()
    mock_pl._plugins = {}
    monkeypatch.setattr(pl, 'plugin_loader', mock_pl)

    # Build a new zip for my_plugin (v2.0.0, different author)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('my_plugin/plugin.json', json.dumps({
            'name': 'my_plugin', 'version': '2.0.0', 'author': 'new_author',
            'description': 'replacement',
        }))
    buf.seek(0)

    c, csrf = client
    r = c.post(
        '/api/plugins/install',
        headers={'X-CSRF-Token': csrf},
        files={'file': ('my_plugin.zip', buf.getvalue(), 'application/zip')},
    )
    assert r.status_code == 409, f"expected 409, got {r.status_code}: {r.text}"
    body = r.json()
    # 409 body includes existing + new meta
    assert body.get('name') == 'my_plugin'
    assert body.get('existing_version') == '1.0.0'
    assert body.get('existing_author') == 'orig_author'
    assert body.get('version') == '2.0.0'
    assert body.get('author') == 'new_author'


# ─── 1.33 Uninstall publishes TOOLSET_CHANGED with action=plugin_uninstall ───

def test_uninstall_publishes_toolset_event_with_uninstall_action(
    client, temp_user_dir, mock_system, monkeypatch, event_bus_capture,
):
    """[PROACTIVE] After uninstall, TOOLSET_CHANGED event must carry
    action='plugin_uninstall' so the UI can distinguish from other toolset
    mutations (save/reload/install)."""
    import core.plugin_loader as pl
    mock_pl = MagicMock()
    info = _mock_info('gone_plugin', band='user')
    mock_pl.get_plugin_info.return_value = info
    mock_pl._plugins = {'gone_plugin': info}
    mock_pl._lock = threading.RLock()
    mock_pl.uninstall_plugin = MagicMock()
    monkeypatch.setattr(pl, 'plugin_loader', mock_pl)

    fm = MagicMock()
    fm.current_toolset_name = 'default'
    fm.get_current_toolset_info.return_value = {'name': 'default', 'function_count': 5}
    mock_system.llm_chat.function_manager = fm

    c, csrf = client
    r = c.request('DELETE', '/api/plugins/gone_plugin/uninstall',
                  headers={'X-CSRF-Token': csrf})
    assert r.status_code == 200

    events = [d for ev, d in event_bus_capture.events if ev == 'toolset_changed']
    assert events, f"no toolset_changed event fired; saw: {[e for e,_ in event_bus_capture.events]}"
    last = events[-1]
    assert last.get('action') == 'plugin_uninstall', \
        f"expected action=plugin_uninstall, got {last}"
    assert last.get('function_count') == 5


# ─── 1.34 Rescan returns added + removed arrays ──────────────────────────────

def test_rescan_returns_added_and_removed_arrays(client, temp_user_dir, monkeypatch):
    """[PROACTIVE] POST /api/plugins/rescan response must include both lists
    so the UI can show 'plugin X detected' + 'plugin Y removed' messages."""
    import core.plugin_loader as pl
    mock_pl = MagicMock()
    mock_pl.rescan.return_value = {
        'added': ['shiny_new'],
        'removed': ['was_here'],
    }
    monkeypatch.setattr(pl, 'plugin_loader', mock_pl)

    c, csrf = client
    r = c.post('/api/plugins/rescan', headers={'X-CSRF-Token': csrf})
    assert r.status_code == 200
    body = r.json()
    assert body['added'] == ['shiny_new']
    assert body['removed'] == ['was_here']


def test_rescan_wraps_loader_exception_as_500(client, temp_user_dir, monkeypatch):
    """[PROACTIVE] plugin_loader.rescan() raising → the route surfaces a
    500 with the error detail (not a silent swallow)."""
    import core.plugin_loader as pl
    mock_pl = MagicMock()
    mock_pl.rescan.side_effect = RuntimeError('disk full')
    monkeypatch.setattr(pl, 'plugin_loader', mock_pl)

    c, csrf = client
    r = c.post('/api/plugins/rescan', headers={'X-CSRF-Token': csrf})
    assert r.status_code == 500
    assert 'disk full' in r.text.lower()


# ─── 1.36 Reload on disabled plugin returns 400 ──────────────────────────────

def test_reload_disabled_plugin_returns_400(client, temp_user_dir, monkeypatch):
    """[PROACTIVE] Reload only makes sense for enabled plugins. Calling it on
    a disabled plugin should 400 so the UI can surface the right message
    (instead of silently reloading something that's not running)."""
    import core.plugin_loader as pl
    mock_pl = MagicMock()
    mock_pl.get_plugin_info.return_value = _mock_info('sleeping', enabled=False,
                                                       loaded=False)
    monkeypatch.setattr(pl, 'plugin_loader', mock_pl)

    c, csrf = client
    r = c.post('/api/plugins/sleeping/reload', headers={'X-CSRF-Token': csrf})
    assert r.status_code == 400
    assert 'not enabled' in r.text.lower()


def test_reload_unknown_plugin_returns_404(client, temp_user_dir, monkeypatch):
    import core.plugin_loader as pl
    mock_pl = MagicMock()
    mock_pl.get_plugin_info.return_value = None
    monkeypatch.setattr(pl, 'plugin_loader', mock_pl)

    c, csrf = client
    r = c.post('/api/plugins/nonexistent/reload', headers={'X-CSRF-Token': csrf})
    assert r.status_code == 404


def test_reload_propagates_load_failure(client, temp_user_dir, monkeypatch):
    """[PROACTIVE] If plugin_loader.reload_plugin raises, route returns 500
    with the exception detail (not a silent 'reloaded' lie)."""
    import core.plugin_loader as pl
    mock_pl = MagicMock()
    mock_pl.get_plugin_info.return_value = _mock_info('brokendep', enabled=True)
    mock_pl.reload_plugin.side_effect = ImportError("no module named 'blargh'")
    monkeypatch.setattr(pl, 'plugin_loader', mock_pl)

    c, csrf = client
    r = c.post('/api/plugins/brokendep/reload', headers={'X-CSRF-Token': csrf})
    assert r.status_code == 500
    assert 'blargh' in r.text.lower()


# ─── 1.38 Check-update: newer version → update_available=True ────────────────

def test_check_update_newer_version_returns_true(client, temp_user_dir, monkeypatch):
    """[PROACTIVE] Happy path: local version 1.0.0, remote 1.1.0 → update true."""
    import core.plugin_loader as pl
    mock_pl = MagicMock()
    mock_pl.get_plugin_info.return_value = _mock_info('pm', version='1.0.0')
    fake_state = MagicMock()
    fake_state.get.return_value = 'https://github.com/user/repo'
    mock_pl.get_plugin_state.return_value = fake_state
    monkeypatch.setattr(pl, 'plugin_loader', mock_pl)

    # Patch requests.get — the route does `import requests as req` inside
    # the function, so we patch the module-level attr.
    import requests as req_mod
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {'name': 'pm', 'version': '1.1.0',
                                   'author': 'user'}
    monkeypatch.setattr(req_mod, 'get', lambda url, timeout=10: fake_resp)

    c, csrf = client
    r = c.get('/api/plugins/pm/check-update')
    assert r.status_code == 200
    body = r.json()
    assert body['update_available'] is True
    assert body['current_version'] == '1.0.0'
    assert body['remote_version'] == '1.1.0'


def test_check_update_same_version_returns_false(client, temp_user_dir, monkeypatch):
    import core.plugin_loader as pl
    mock_pl = MagicMock()
    mock_pl.get_plugin_info.return_value = _mock_info('pm', version='1.0.0')
    fake_state = MagicMock()
    fake_state.get.return_value = 'https://github.com/user/repo'
    mock_pl.get_plugin_state.return_value = fake_state
    monkeypatch.setattr(pl, 'plugin_loader', mock_pl)

    import requests as req_mod
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {'name': 'pm', 'version': '1.0.0', 'author': 'user'}
    monkeypatch.setattr(req_mod, 'get', lambda url, timeout=10: fake_resp)

    c, csrf = client
    r = c.get('/api/plugins/pm/check-update')
    assert r.status_code == 200
    assert r.json()['update_available'] is False


# ─── 1.39 Check-update: no installed_from → reason=no_source ─────────────────

def test_check_update_without_source_url_returns_no_source_reason(
    client, temp_user_dir, monkeypatch,
):
    """A plugin installed via toolmaker / zip_upload has no installed_from —
    check-update must return {update_available: false, reason: 'no_source'}
    instead of attempting a doomed fetch."""
    import core.plugin_loader as pl
    mock_pl = MagicMock()
    mock_pl.get_plugin_info.return_value = _mock_info('local_plugin')
    fake_state = MagicMock()
    fake_state.get.return_value = None
    mock_pl.get_plugin_state.return_value = fake_state
    monkeypatch.setattr(pl, 'plugin_loader', mock_pl)

    c, csrf = client
    r = c.get('/api/plugins/local_plugin/check-update')
    assert r.status_code == 200
    body = r.json()
    assert body['update_available'] is False
    assert body['reason'] == 'no_source'


def test_check_update_unsupported_host_returns_unsupported_source(client, temp_user_dir, monkeypatch):
    """Source URL that's neither GitHub nor GitLab → unsupported_source.
    2026-04-26: GitLab was added to the supported set, so this test now
    exercises the genuinely-unknown-host case with bitbucket."""
    import core.plugin_loader as pl
    mock_pl = MagicMock()
    mock_pl.get_plugin_info.return_value = _mock_info('bitbucket_plugin')
    fake_state = MagicMock()
    fake_state.get.return_value = 'https://bitbucket.org/user/repo'
    mock_pl.get_plugin_state.return_value = fake_state
    monkeypatch.setattr(pl, 'plugin_loader', mock_pl)

    c, csrf = client
    r = c.get('/api/plugins/bitbucket_plugin/check-update')
    assert r.status_code == 200
    assert r.json()['reason'] == 'unsupported_source'


def test_check_update_gitlab_source_attempts_fetch(client, temp_user_dir, monkeypatch):
    """GitLab source URL is now SUPPORTED — check_plugin_update should
    attempt to fetch the manifest from gitlab.com/.../-/raw/<branch>/plugin.json
    rather than returning unsupported_source. 2026-04-26 GitLab support."""
    import core.plugin_loader as pl
    mock_pl = MagicMock()
    mock_pl.get_plugin_info.return_value = _mock_info('gitlab_plugin')
    fake_state = MagicMock()
    fake_state.get.return_value = 'https://gitlab.com/group/subgroup/repo'
    mock_pl.get_plugin_state.return_value = fake_state
    monkeypatch.setattr(pl, 'plugin_loader', mock_pl)

    import requests
    fetched = []

    def _fake_get(url, *_, **__):
        fetched.append(url)
        resp = MagicMock()
        resp.status_code = 404
        return resp

    monkeypatch.setattr(requests, 'get', _fake_get)

    c, csrf = client
    r = c.get('/api/plugins/gitlab_plugin/check-update')
    assert r.status_code == 200
    body = r.json()
    # When all branches 404, the route returns fetch_failed (not unsupported_source).
    # That's the proof that the route attempted to fetch — i.e., GitLab IS supported.
    assert body['reason'] == 'fetch_failed', f"GitLab source should attempt fetch, got: {body}"
    # And we should have tried both main and master at the GitLab raw URL pattern.
    assert any('gitlab.com/group/subgroup/repo/-/raw/main/plugin.json' in u for u in fetched), \
        f"Expected GitLab raw URL with subgroup path, fetched: {fetched}"
    assert any('gitlab.com/group/subgroup/repo/-/raw/master/plugin.json' in u for u in fetched), \
        f"Expected fallback to master branch, fetched: {fetched}"


def test_check_update_url_with_query_params_still_matches(client, temp_user_dir, monkeypatch):
    """A stored installed_from URL with a query string / fragment should still
    match the GitHub/GitLab regex. Path-only matching via urlparse. 2026-04-26."""
    import core.plugin_loader as pl
    mock_pl = MagicMock()
    mock_pl.get_plugin_info.return_value = _mock_info('gh_with_qs')
    fake_state = MagicMock()
    fake_state.get.return_value = 'https://github.com/user/repo?utm_source=share#readme'
    mock_pl.get_plugin_state.return_value = fake_state
    monkeypatch.setattr(pl, 'plugin_loader', mock_pl)

    import requests
    fetched = []

    def _fake_get(url, *_, **__):
        fetched.append(url)
        resp = MagicMock()
        resp.status_code = 404
        return resp

    monkeypatch.setattr(requests, 'get', _fake_get)

    c, csrf = client
    r = c.get('/api/plugins/gh_with_qs/check-update')
    assert r.status_code == 200
    body = r.json()
    assert body['reason'] == 'fetch_failed', f"URL with query params should still match GitHub regex, got: {body}"
    assert any('raw.githubusercontent.com/user/repo' in u for u in fetched), \
        f"Expected GitHub raw URL, fetched: {fetched}"


# ─── 1.40 Check-update: non-numeric versions handled gracefully ──────────────

def test_check_update_version_tuple_handles_non_numeric(client, temp_user_dir, monkeypatch):
    """[PROACTIVE] If either version is weird ('unknown', '2.0-beta', ''),
    the comparator must not crash — falls back to (0,)."""
    import core.plugin_loader as pl
    mock_pl = MagicMock()
    mock_pl.get_plugin_info.return_value = _mock_info('exotic', version='2.0-beta')
    fake_state = MagicMock()
    fake_state.get.return_value = 'https://github.com/user/repo'
    mock_pl.get_plugin_state.return_value = fake_state
    monkeypatch.setattr(pl, 'plugin_loader', mock_pl)

    import requests as req_mod
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {'name': 'exotic', 'version': 'unknown',
                                   'author': 'user'}
    monkeypatch.setattr(req_mod, 'get', lambda url, timeout=10: fake_resp)

    c, csrf = client
    r = c.get('/api/plugins/exotic/check-update')
    # Must return 200 with a valid body — not 500
    assert r.status_code == 200
    body = r.json()
    assert 'update_available' in body


# ─── 1.44 Settings CRUD 404 on unknown plugin ────────────────────────────────

def test_get_settings_unknown_plugin_returns_404(client, temp_user_dir, monkeypatch):
    import core.plugin_loader as pl
    import core.routes.plugins as rp
    mock_pl = MagicMock()
    mock_pl.get_plugin_info.return_value = None
    monkeypatch.setattr(pl, 'plugin_loader', mock_pl)
    monkeypatch.setattr(rp, '_get_merged_plugins', lambda: {"plugins": {}, "enabled": []})

    c, csrf = client
    r = c.get('/api/webui/plugins/does_not_exist/settings')
    assert r.status_code == 404


def test_put_settings_unknown_plugin_returns_404(client, temp_user_dir, monkeypatch):
    import core.plugin_loader as pl
    import core.routes.plugins as rp
    mock_pl = MagicMock()
    mock_pl.get_plugin_info.return_value = None
    monkeypatch.setattr(pl, 'plugin_loader', mock_pl)
    monkeypatch.setattr(rp, '_get_merged_plugins', lambda: {"plugins": {}, "enabled": []})

    c, csrf = client
    r = c.put('/api/webui/plugins/does_not_exist/settings',
              headers={'X-CSRF-Token': csrf}, json={'key': 'value'})
    assert r.status_code == 404


def test_delete_settings_unknown_plugin_returns_404(client, temp_user_dir, monkeypatch):
    import core.plugin_loader as pl
    import core.routes.plugins as rp
    mock_pl = MagicMock()
    mock_pl.get_plugin_info.return_value = None
    monkeypatch.setattr(pl, 'plugin_loader', mock_pl)
    monkeypatch.setattr(rp, '_get_merged_plugins', lambda: {"plugins": {}, "enabled": []})

    c, csrf = client
    r = c.request('DELETE', '/api/webui/plugins/does_not_exist/settings',
                  headers={'X-CSRF-Token': csrf})
    assert r.status_code == 404


# ─── 1.45 DELETE settings is idempotent ──────────────────────────────────────

def test_delete_settings_idempotent(client, temp_user_dir, monkeypatch):
    """[PROACTIVE] Calling DELETE /settings twice on the same plugin must
    return 200 both times (not 404 on the second call). Settings files may
    not exist yet (plugin with no configured overrides); delete is a
    'ensure absent' operation, not 'remove specific thing'."""
    import core.plugin_loader as pl
    import core.routes.plugins as rp
    mock_pl = MagicMock()
    mock_pl.get_plugin_info.return_value = _mock_info('plugin_x')
    monkeypatch.setattr(pl, 'plugin_loader', mock_pl)
    monkeypatch.setattr(rp, '_get_merged_plugins', lambda: {
        "plugins": {"plugin_x": {"name": "plugin_x"}}, "enabled": [],
    })

    c, csrf = client
    r1 = c.request('DELETE', '/api/webui/plugins/plugin_x/settings',
                   headers={'X-CSRF-Token': csrf})
    r2 = c.request('DELETE', '/api/webui/plugins/plugin_x/settings',
                   headers={'X-CSRF-Token': csrf})
    assert r1.status_code == 200
    assert r2.status_code == 200
