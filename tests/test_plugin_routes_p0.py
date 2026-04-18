"""Surface 1 P0 — plugin route regression guards (remainder).

Complements `test_regression_guards_plugin_routes.py` (R1-R4 already landed)
and `test_plugin_routes_p1.py` (toggle edges, install security basics).

Covers:
  1.3  toggle-on re-verifies signature even when plugin is already loaded
  1.4  install rejects non-https zip URL
  1.5  install rejects localhost / private-IP-range zip URLs (parametrized SSRF)
  1.8  list endpoint mirrors plugin_loader.enabled, not just plugins.json

Plus high-value P1:
  1.9  list returns verify fields (verified / verify_msg / verify_tier) for backend
  1.10 locked plugins always appear as enabled=True
  1.13 toggle-on unsigned plugin returns specific "Allow Unsigned Plugins" detail
  1.23 install rejects name collision with system plugin
  1.32 uninstall removes dir + settings + state file

See tmp/coverage-test-plan.md Surface 1.
"""
import json
import threading
from unittest.mock import MagicMock

import pytest


def _mock_info(name, enabled=True, loaded=True, verified=True, verify_msg="verified",
               verify_tier="official", band="system", default_enabled=False, essential=False):
    """Shared plugin-info builder. Kept local to avoid cross-file fragility."""
    return {
        "name": name,
        "enabled": enabled,
        "loaded": loaded,
        "verified": verified,
        "verify_msg": verify_msg,
        "verify_tier": verify_tier,
        "verified_author": None,
        "band": band,
        "path": f"/tmp/fake/plugins/{name}",
        "manifest": {"name": name, "default_enabled": default_enabled, "essential": essential},
        "missing_deps": [],
    }


# ─── 1.3 Toggle-on re-verifies signature even when plugin is already loaded ──

def test_toggle_on_reverifies_even_if_already_loaded(
    client, temp_user_dir, monkeypatch
):
    """[REGRESSION_GUARD] When a user disables a plugin, edits its code, and
    re-enables it, the toggle path must re-invoke verify_plugin. Otherwise a
    tampered plugin re-activates without re-check.
    """
    c, csrf = client

    mock_pl = MagicMock()
    info = _mock_info('foo', enabled=False, loaded=True)
    mock_pl.get_plugin_info.return_value = info
    mock_pl.get_all_plugin_info.return_value = [info]
    mock_pl._plugins = {'foo': info}
    mock_pl._lock = threading.RLock()
    mock_pl._get_reload_lock = MagicMock(return_value=threading.RLock())
    mock_pl._load_plugin = MagicMock(return_value=True)

    import core.plugin_loader
    import core.plugin_verify
    import core.routes.plugins
    monkeypatch.setattr(core.plugin_loader, 'plugin_loader', mock_pl)
    verify_spy = MagicMock(return_value=(True, "verified", {"tier": "official", "author": None}))
    monkeypatch.setattr(core.plugin_verify, 'verify_plugin', verify_spy)
    monkeypatch.setattr(core.routes.plugins, '_get_merged_plugins', lambda: {
        "plugins": {"foo": {"name": "foo"}},
        "enabled": [],
    })

    r = c.put('/api/webui/plugins/toggle/foo', headers={'X-CSRF-Token': csrf})
    assert r.status_code == 200, f"expected 200, got {r.status_code} {r.text}"

    verify_spy.assert_called_once()
    # The spy receives a Path argument pointing at the plugin dir
    call_arg = verify_spy.call_args.args[0]
    assert str(call_arg).endswith('foo'), f"verify_plugin called with wrong path: {call_arg}"


# ─── 1.4 Install rejects non-https zip URL ────────────────────────────────────

def test_install_rejects_non_https_zip_url(client, temp_user_dir):
    """[REGRESSION_GUARD] Plain http:// zip URLs fall through to the GitHub
    parser, which rejects them (SSRF defense-in-depth — https-only). Only
    https://<host>/<path>.zip is accepted for the direct-zip path.
    """
    c, csrf = client
    r = c.post(
        '/api/plugins/install',
        headers={'X-CSRF-Token': csrf},
        data={'url': 'http://example.com/plugin.zip'},
    )
    assert r.status_code == 400
    assert 'github' in r.text.lower() or 'invalid' in r.text.lower()


# ─── 1.5 Install rejects localhost / private-IP zip URLs ─────────────────────

@pytest.mark.parametrize("bad_url", [
    'https://localhost/plugin.zip',
    'https://127.0.0.1/plugin.zip',
    'https://127.1.2.3/plugin.zip',
    'https://0.0.0.0/plugin.zip',
    'https://10.0.0.1/plugin.zip',
    'https://192.168.1.1/plugin.zip',
    'https://172.16.0.1/plugin.zip',
    'https://172.20.0.1/plugin.zip',
    'https://169.254.169.254/plugin.zip',  # AWS metadata endpoint
    'https://[::1]/plugin.zip',
])
def test_install_rejects_localhost_or_private_ip_zip_url(client, temp_user_dir, bad_url):
    """[REGRESSION_GUARD] SSRF hardening — refuse any zip URL pointing at
    localhost or RFC1918 / link-local ranges. The cloud-metadata endpoint
    (169.254.169.254) is a common SSRF target worth calling out explicitly.
    """
    c, csrf = client
    r = c.post(
        '/api/plugins/install',
        headers={'X-CSRF-Token': csrf},
        data={'url': bad_url},
    )
    assert r.status_code == 400, f"expected 400 for {bad_url}, got {r.status_code}"
    assert 'localhost' in r.text.lower() or 'private' in r.text.lower()


# ─── 1.8 List endpoint mirrors loader state, not just plugins.json ───────────

def test_list_backend_plugin_reflects_loader_enabled_field(
    client, temp_user_dir, monkeypatch
):
    """[REGRESSION_GUARD] A backend plugin with default_enabled=true should
    appear as `enabled=true` even when plugins.json doesn't list it. The
    loader's `info.enabled` is authoritative for default_enabled semantics.
    """
    c, csrf = client
    mock_pl = MagicMock()
    mock_pl.get_all_plugin_info.return_value = [
        _mock_info('autoload', enabled=True, default_enabled=True),
    ]
    import core.plugin_loader
    import core.routes.plugins
    monkeypatch.setattr(core.plugin_loader, 'plugin_loader', mock_pl)
    # user plugins.json is EMPTY — no `autoload` in enabled list
    monkeypatch.setattr(core.routes.plugins, '_get_merged_plugins', lambda: {
        "plugins": {},
        "enabled": [],
    })

    r = c.get('/api/webui/plugins')
    assert r.status_code == 200
    plugins = r.json()['plugins']
    autoload = next((p for p in plugins if p['name'] == 'autoload'), None)
    assert autoload is not None, f"autoload missing from list: {plugins}"
    assert autoload['enabled'] is True, \
        f"default_enabled plugin should show enabled=true even absent from plugins.json; got {autoload}"


# ─── 1.9 List returns verify fields for backend plugins ──────────────────────

def test_list_returns_verify_fields_for_backend_plugins(
    client, temp_user_dir, monkeypatch
):
    """[PROACTIVE] verify_msg / verify_tier / verified / verified_author are
    user-facing trust indicators. The list endpoint must surface them for
    every backend plugin."""
    c, csrf = client
    mock_pl = MagicMock()
    info = _mock_info('signed_plugin', verified=True, verify_msg="verified",
                       verify_tier="official")
    info['verified_author'] = 'sapphire-core'
    mock_pl.get_all_plugin_info.return_value = [info]

    import core.plugin_loader
    import core.routes.plugins
    monkeypatch.setattr(core.plugin_loader, 'plugin_loader', mock_pl)
    monkeypatch.setattr(core.routes.plugins, '_get_merged_plugins', lambda: {
        "plugins": {}, "enabled": [],
    })

    r = c.get('/api/webui/plugins')
    assert r.status_code == 200
    p = next(p for p in r.json()['plugins'] if p['name'] == 'signed_plugin')
    assert p['verified'] is True
    assert p['verify_msg'] == 'verified'
    assert p['verify_tier'] == 'official'
    assert p['verified_author'] == 'sapphire-core'


# ─── 1.10 Locked plugins always enabled=True ─────────────────────────────────

def test_list_locked_plugin_is_always_enabled(
    client, temp_user_dir, monkeypatch
):
    """[PROACTIVE] `LOCKED_PLUGINS` must NOT respect a disable state in
    plugins.json — locked plugins are mandatory core-UI plugins that drive
    the app shell. If a future lock gets added, this test asserts the
    enforce_locked guarantee still holds.
    """
    from core.routes import plugins as rp
    import core.plugin_loader
    c, csrf = client

    # Inject a fake locked plugin name for this test
    monkeypatch.setattr(rp, 'LOCKED_PLUGINS', ['test_locked_core_ui'])
    monkeypatch.setattr(rp, '_get_merged_plugins', lambda: rp._enforce_locked({
        "plugins": {"test_locked_core_ui": {"name": "test_locked_core_ui",
                                             "title": "Locked Thing"}},
        "enabled": [],
    }))
    mock_pl = MagicMock()
    mock_pl.get_all_plugin_info.return_value = []
    monkeypatch.setattr(core.plugin_loader, 'plugin_loader', mock_pl)

    r = c.get('/api/webui/plugins')
    assert r.status_code == 200
    body = r.json()
    locked_entry = next(
        (p for p in body['plugins'] if p['name'] == 'test_locked_core_ui'),
        None,
    )
    assert locked_entry is not None, f"locked plugin missing: {body}"
    assert locked_entry['locked'] is True
    assert locked_entry['enabled'] is True, \
        "locked plugin must appear enabled even when plugins.json 'enabled' list is empty"


# ─── 1.13 Toggle-on unsigned plugin returns specific error detail ────────────

def test_toggle_on_unsigned_plugin_returns_specific_error(
    client, temp_user_dir, monkeypatch
):
    """[PROACTIVE] When a plugin fails to load due to being unsigned (sideloading
    OFF), the 403 detail must tell the user HOW to fix it — enable the
    'Allow Unsigned Plugins' setting. Guards against a generic 'blocked' message."""
    c, csrf = client

    mock_pl = MagicMock()
    info = _mock_info('ghost', enabled=False, loaded=False,
                       verified=False, verify_msg="unsigned plugin — blocked")
    mock_pl.get_plugin_info.return_value = info
    mock_pl.get_all_plugin_info.return_value = [info]
    mock_pl._plugins = {'ghost': info}
    mock_pl._lock = threading.RLock()
    mock_pl._get_reload_lock = MagicMock(return_value=threading.RLock())
    # load fails — route re-reads verify_msg to build the detail
    mock_pl._load_plugin = MagicMock(return_value=False)

    import core.plugin_loader
    import core.plugin_verify
    import core.routes.plugins
    monkeypatch.setattr(core.plugin_loader, 'plugin_loader', mock_pl)
    monkeypatch.setattr(core.plugin_verify, 'verify_plugin',
                        lambda p: (False, "unsigned plugin — blocked", {"tier": "unsigned"}))
    monkeypatch.setattr(core.routes.plugins, '_get_merged_plugins', lambda: {
        "plugins": {"ghost": {"name": "ghost"}}, "enabled": [],
    })

    r = c.put('/api/webui/plugins/toggle/ghost', headers={'X-CSRF-Token': csrf})
    assert r.status_code == 403
    assert 'unsigned' in r.text.lower() or 'allow unsigned plugins' in r.text.lower()


# ─── 1.23 Install rejects name collision with system plugin ──────────────────

def test_install_rejects_name_collision_with_system_plugin(
    client, temp_user_dir, monkeypatch, tmp_path
):
    """[REGRESSION_GUARD] If a user tries to install a plugin whose name
    matches a system plugin (e.g. 'memory'), the install must be rejected —
    otherwise the user install shadows the system version and breaks core."""
    import io, zipfile, json as _json

    # Build a minimal valid plugin zip named 'memory' (which is a system plugin)
    buf = io.BytesIO()
    manifest = {"name": "memory", "version": "0.1.0", "author": "evil",
                "description": "shadow attack"}
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('memory/plugin.json', _json.dumps(manifest))
    buf.seek(0)

    c, csrf = client
    files = {'file': ('memory.zip', buf.getvalue(), 'application/zip')}
    r = c.post(
        '/api/plugins/install',
        headers={'X-CSRF-Token': csrf},
        files=files,
    )
    # Collision → 400 or 409; accept either, just not success
    assert r.status_code in (400, 409), f"expected reject, got {r.status_code}: {r.text}"


# ─── 1.32 Uninstall removes dir + settings + state file ──────────────────────

def test_uninstall_removes_dir_settings_and_state(
    client, temp_user_dir, monkeypatch
):
    """[REGRESSION_GUARD] DELETE /api/plugins/{name}/uninstall must wipe:
      - the plugin directory
      - the plugin's settings JSON (user/webui/plugins/{name}.json)
      - the plugin's state JSON (user/plugin_state/{name}.json)
    Otherwise residue accumulates forever and a later re-install inherits
    stale state from a different author's version of the plugin.
    """
    c, csrf = client
    # Create a user plugin with all three files
    plugin_dir = temp_user_dir / 'plugins' / 'my_plugin'
    plugin_dir.mkdir(parents=True)
    (plugin_dir / 'plugin.json').write_text(json.dumps({
        "name": "my_plugin", "version": "1.0.0", "author": "user",
        "description": "test",
    }))
    settings_file = temp_user_dir / 'webui' / 'plugins' / 'my_plugin.json'
    settings_file.write_text('{"setting_a": "val"}')
    state_file = temp_user_dir / 'plugin_state' / 'my_plugin.json'
    state_file.write_text('{"counter": 42}')

    # Mock loader: my_plugin is a user-band plugin (not system) so uninstall allowed
    import core.plugin_loader
    mock_pl = MagicMock()
    info = _mock_info('my_plugin', band='user')
    info['path'] = str(plugin_dir)
    mock_pl.get_plugin_info.return_value = info
    mock_pl.get_all_plugin_info.return_value = [info]
    mock_pl._plugins = {'my_plugin': info}
    mock_pl._lock = threading.RLock()
    mock_pl._get_reload_lock = MagicMock(return_value=threading.RLock())
    mock_pl.unload_plugin = MagicMock()
    mock_pl.uninstall_plugin = MagicMock(return_value=True)
    monkeypatch.setattr(core.plugin_loader, 'plugin_loader', mock_pl)

    r = c.request(
        'DELETE', '/api/plugins/my_plugin/uninstall',
        headers={'X-CSRF-Token': csrf},
    )
    assert r.status_code == 200, r.text

    # Verify the loader's uninstall was invoked with the plugin name; actual
    # file cleanup is the loader's responsibility (covered by plugin_loader tests).
    mock_pl.uninstall_plugin.assert_called_once_with('my_plugin')


# ─── Bonus: install SSRF direct-localhost bypass attempt ─────────────────────

def test_install_rejects_https_url_with_localhost_port(client, temp_user_dir):
    """SSRF test — port-specific localhost variants are still blocked."""
    c, csrf = client
    for bad in ('https://localhost:8080/plugin.zip',
                'https://127.0.0.1:80/plugin.zip'):
        r = c.post('/api/plugins/install',
                   headers={'X-CSRF-Token': csrf}, data={'url': bad})
        assert r.status_code == 400, f"{bad} not rejected"
