"""Regression guards for plugin-route bugs shipped 2026-04-18.

Covered:
  R1 [REGRESSION_GUARD] Toggle-off persists disabled list for default_enabled plugins
                        (plugin-disable-doesn't-stick on reboot bug — commit 75dbc5f)
  R2 [REGRESSION_GUARD] Toggle-on tampered plugin → 403 AND plugins.json preserves enabled
                        (tamper-preserve — later today's fix; was destructive to user intent)
  R3 [REGRESSION_GUARD] Install rejects redirect-to-localhost (SSRF via 302)
                        (allow_redirects=False fix)
  R4 [REGRESSION_GUARD] Plugin settings PUT shallow-merges, doesn't clobber unrelated keys
                        (MCP save-destroys-servers class)

See tmp/coverage-test-plan.md.
"""
import json
import threading
from unittest.mock import MagicMock, patch

import pytest


def _mock_plugin_info(name, enabled=True, loaded=True, verified=True, verify_msg="verified"):
    return {
        "name": name,
        "enabled": enabled,
        "loaded": loaded,
        "verified": verified,
        "verify_msg": verify_msg,
        "verify_tier": "official",
        "band": "system",
        "path": f"/tmp/fake/plugins/{name}",
        "manifest": {"name": name, "default_enabled": False},
        "missing_deps": [],
    }


# ─── R1: Toggle-off persists disabled list for default_enabled plugins ───────

def test_R1_toggle_off_default_enabled_plugin_persists_in_disabled_list(
    client, temp_user_dir, monkeypatch
):
    """[REGRESSION_GUARD] A plugin with `default_enabled: true` in its manifest
    must be recordable in the `disabled` list when the user toggles it off —
    otherwise every reboot re-enables it (the plugin-disable-doesn't-stick bug).

    Before fix: toggle-off just removed name from `enabled` list. On restart,
    scan saw default_enabled=true → re-enabled the plugin.

    After fix: toggle-off also appends name to `disabled` list, which blocks
    default_enabled re-activation.
    """
    c, csrf = client

    # Mock plugin_loader so the route sees 'foo' as a known, currently-enabled plugin
    mock_pl = MagicMock()
    mock_pl.get_plugin_info.return_value = _mock_plugin_info('foo', enabled=True, loaded=True)
    mock_pl.get_all_plugin_info.return_value = [_mock_plugin_info('foo', enabled=True, loaded=True)]
    mock_pl._plugins = {'foo': _mock_plugin_info('foo', enabled=True, loaded=True)}
    mock_pl._lock = threading.RLock()
    mock_pl._get_reload_lock = MagicMock(return_value=threading.RLock())
    mock_pl.unload_plugin = MagicMock(return_value=None)

    import core.plugin_loader
    import core.routes.plugins
    monkeypatch.setattr(core.plugin_loader, 'plugin_loader', mock_pl)
    monkeypatch.setattr(core.routes.plugins, '_get_merged_plugins', lambda: {
        "plugins": {"foo": {"name": "foo"}},
        "enabled": ["foo"],
    })

    r = c.put(f'/api/webui/plugins/toggle/foo', headers={'X-CSRF-Token': csrf})
    assert r.status_code == 200, f"toggle failed: {r.status_code} {r.text}"

    # Read plugins.json from temp_user_dir
    plugins_json_path = temp_user_dir / "webui" / "plugins.json"
    data = json.loads(plugins_json_path.read_text())

    assert 'foo' not in data.get('enabled', []), \
        f"'foo' should be removed from enabled list; got {data}"
    assert 'foo' in data.get('disabled', []), \
        f"'foo' should be in disabled list (prevents default_enabled re-activation on reboot); got {data}"


def test_R1_toggle_on_removes_from_disabled_list(client, temp_user_dir, monkeypatch):
    """Inverse of R1: toggling on a previously-disabled plugin removes it from
    the disabled list (otherwise it'd be stuck off forever)."""
    c, csrf = client

    # Pre-seed plugins.json with foo in disabled list
    plugins_json_path = temp_user_dir / "webui" / "plugins.json"
    plugins_json_path.write_text(json.dumps({"enabled": [], "disabled": ["foo"]}))

    mock_pl = MagicMock()
    mock_pl.get_plugin_info.return_value = _mock_plugin_info('foo', enabled=False, loaded=False)
    mock_pl.get_all_plugin_info.return_value = [_mock_plugin_info('foo', enabled=False, loaded=False)]
    mock_pl._plugins = {'foo': _mock_plugin_info('foo', enabled=False, loaded=False)}
    mock_pl._lock = threading.RLock()
    mock_pl._get_reload_lock = MagicMock(return_value=threading.RLock())
    mock_pl._load_plugin = MagicMock(return_value=True)

    import core.plugin_loader
    import core.routes.plugins
    monkeypatch.setattr(core.plugin_loader, 'plugin_loader', mock_pl)
    monkeypatch.setattr(core.routes.plugins, '_get_merged_plugins', lambda: {
        "plugins": {"foo": {"name": "foo"}},
        "enabled": [],
    })

    r = c.put('/api/webui/plugins/toggle/foo', headers={'X-CSRF-Token': csrf})
    assert r.status_code == 200, f"toggle failed: {r.status_code} {r.text}"

    data = json.loads(plugins_json_path.read_text())
    assert 'foo' in data.get('enabled', []), \
        f"'foo' should be in enabled list; got {data}"
    assert 'foo' not in data.get('disabled', []), \
        f"'foo' should be removed from disabled list; got {data}"


# ─── R2: Toggle-on tampered plugin → 403 + plugins.json unchanged ────────────

def test_R2_toggle_on_tampered_plugin_preserves_enabled_intent(
    client, temp_user_dir, monkeypatch
):
    """[REGRESSION_GUARD] When a user tries to toggle-on a tampered plugin, the
    route must:
      1. Return 403 with a clear signature-error message
      2. Leave plugins.json alone (user intent survives so re-signing + restart
         auto-activates without a second UI click)

    Pre-fix: the revert path explicitly stripped the plugin from plugins.json
    on verification failure, destroying the user's enable intent.
    """
    c, csrf = client

    # Pre-seed plugins.json with foo in enabled (user's surviving intent)
    plugins_json_path = temp_user_dir / "webui" / "plugins.json"
    plugins_json_path.write_text(json.dumps({"enabled": ["foo"], "disabled": []}))

    # Build mock plugin_loader state: foo is in _plugins (known), enabled=False in memory
    # (because tampered), verify_msg signals hash mismatch
    tampered_info = _mock_plugin_info(
        'foo', enabled=False, loaded=False,
        verified=False, verify_msg="hash mismatch: tampered"
    )
    mock_pl = MagicMock()
    mock_pl.get_plugin_info.return_value = tampered_info
    mock_pl.get_all_plugin_info.return_value = [tampered_info]
    mock_pl._plugins = {'foo': tampered_info}
    mock_pl._lock = threading.RLock()
    mock_pl._get_reload_lock = MagicMock(return_value=threading.RLock())
    # _load_plugin returns False — signature blocks it
    mock_pl._load_plugin = MagicMock(return_value=False)

    import core.plugin_loader
    import core.plugin_verify
    import core.routes.plugins
    monkeypatch.setattr(core.plugin_loader, 'plugin_loader', mock_pl)
    # Route re-verifies during toggle — intercept to keep our "tampered" state
    monkeypatch.setattr(
        core.plugin_verify, 'verify_plugin',
        lambda path: (False, "hash mismatch: tampered", {"tier": "unsigned"}),
    )
    monkeypatch.setattr(core.routes.plugins, '_get_merged_plugins', lambda: {
        "plugins": {"foo": {"name": "foo"}},
        "enabled": ["foo"],
    })

    r = c.put('/api/webui/plugins/toggle/foo', headers={'X-CSRF-Token': csrf})

    # Must return 403
    assert r.status_code == 403, f"expected 403 on tampered toggle; got {r.status_code} {r.text}"

    # Error detail should mention tampering / signature
    detail = r.json().get('detail', '').lower()
    assert any(word in detail for word in ('tamper', 'signature', 'modified')), \
        f"403 detail should indicate tampering; got: {r.json()}"

    # The critical assertion: plugins.json STILL has foo in enabled
    data = json.loads(plugins_json_path.read_text())
    assert 'foo' in data.get('enabled', []), (
        f"tamper-preserve broken: 'foo' was stripped from enabled list on load failure. "
        f"User intent lost. plugins.json: {data}"
    )


# ─── R3: Install rejects redirect-to-localhost via allow_redirects=False ─────

def test_R3_install_rejects_302_redirect_to_localhost(client, monkeypatch):
    """[REGRESSION_GUARD] When a zip URL responds with a 302 redirect, requests.get
    must be called with allow_redirects=False so the redirect isn't followed.
    Otherwise an attacker-controlled https URL could 302 into http://127.0.0.1/...
    and bypass the localhost allowlist above.

    Assertions:
      1. HTTP 400 (route rejects the 302 as non-200)
      2. requests.get called with allow_redirects=False
      3. No second requests.get call following the redirect
    """
    c, csrf = client

    # Mock requests.get: first call returns 302 with Location pointing at localhost
    redirect_response = MagicMock()
    redirect_response.status_code = 302
    redirect_response.headers = {'Location': 'http://127.0.0.1:8073/internal/secret.zip'}
    redirect_response.iter_content = MagicMock(return_value=iter([]))

    call_history = []
    def fake_get(url, **kwargs):
        call_history.append({'url': url, 'kwargs': kwargs})
        return redirect_response

    import core.routes.plugins
    # The install function rides the net facade (2026-09-01).
    from core import net
    monkeypatch.setattr(net, 'get', fake_get)

    r = c.post(
        '/api/plugins/install',
        headers={'X-CSRF-Token': csrf},
        data={'url': 'https://totally-legit.example.com/plugin.zip'},
    )

    # Route must reject (302 → status != 200 → 400)
    assert r.status_code == 400, f"expected 400; got {r.status_code} {r.text}"

    # Must have called with allow_redirects=False
    assert len(call_history) >= 1, "requests.get was never called"
    first = call_history[0]
    assert first['kwargs'].get('allow_redirects') is False, (
        f"allow_redirects must be False to prevent redirect-to-localhost SSRF; "
        f"got kwargs={first['kwargs']}"
    )

    # Must NOT have followed to the localhost URL
    localhost_calls = [h for h in call_history if '127.0.0.1' in h['url'] or 'localhost' in h['url']]
    assert localhost_calls == [], (
        f"SSRF broken: route followed redirect into localhost. Calls: {call_history}"
    )


def test_R3_install_rejects_plain_http_zip_url(client):
    """[REGRESSION_GUARD] A plain http:// zip URL must be rejected (falls through
    to GitHub parser which will also fail). Protects against man-in-the-middle
    and cleartext leakage."""
    c, csrf = client
    r = c.post(
        '/api/plugins/install',
        headers={'X-CSRF-Token': csrf},
        data={'url': 'http://example.com/plugin.zip'},
    )
    assert r.status_code == 400


def test_R3_install_rejects_direct_localhost_zip_url(client):
    """[REGRESSION_GUARD] Direct localhost/private-IP zip URL is rejected by
    the URL-level check (not the redirect check — that's R3 above)."""
    c, csrf = client
    for bad_url in [
        'https://localhost/x.zip',
        'https://127.0.0.1/x.zip',
        'https://192.168.1.5/x.zip',
        'https://10.0.0.1/x.zip',
    ]:
        r = c.post(
            '/api/plugins/install',
            headers={'X-CSRF-Token': csrf},
            data={'url': bad_url},
        )
        assert r.status_code == 400, f"{bad_url} should be rejected; got {r.status_code}"


# ─── R4: Settings PUT shallow-merges ──────────────────────────────────────────

def test_R4_settings_put_shallow_merges_preserves_side_channel_keys(
    client, temp_user_dir, monkeypatch
):
    """[REGRESSION_GUARD] Pre-seed a plugin settings file with multiple keys
    (simulating MCP's `servers` list + unrelated `foo` field). PUT an update
    for `foo` only. Assert the resulting file has BOTH `servers` preserved
    AND `foo` updated.

    Pre-fix: partial PUT wrote only the provided keys, wiping unrelated state
    (the MCP save-destroys-servers bug class).
    """
    c, csrf = client

    # Pre-seed settings file for plugin 'mcp_client'
    settings_dir = temp_user_dir / "webui" / "plugins"
    settings_file = settings_dir / "mcp_client.json"
    seeded = {
        "servers": [{"name": "srv1", "url": "http://a"}, {"name": "srv2", "url": "http://b"}],
        "foo": 1,
        "bar": "hello",
    }
    settings_file.write_text(json.dumps(seeded))

    # Mock plugin_loader so _require_known_plugin passes
    info = _mock_plugin_info('mcp_client')
    mock_pl = MagicMock()
    mock_pl.get_plugin_info.return_value = info
    mock_pl.get_all_plugin_info.return_value = [info]
    mock_pl._plugins = {'mcp_client': info}

    import core.plugin_loader
    import core.routes.plugins
    monkeypatch.setattr(core.plugin_loader, 'plugin_loader', mock_pl)
    monkeypatch.setattr(core.routes.plugins, '_get_merged_plugins', lambda: {
        "plugins": {"mcp_client": {"name": "mcp_client"}},
        "enabled": ["mcp_client"],
    })

    # Settings manager stub (managed-mode check)
    mock_sm = MagicMock()
    mock_sm.is_managed.return_value = False
    import core.settings_manager
    monkeypatch.setattr(core.settings_manager, 'settings', mock_sm)

    # PUT with only 'foo' updated
    r = c.put(
        '/api/webui/plugins/mcp_client/settings',
        headers={'X-CSRF-Token': csrf, 'Content-Type': 'application/json'},
        json={'settings': {'foo': 2}},
    )
    assert r.status_code == 200, f"PUT failed: {r.status_code} {r.text}"

    # Read back — servers MUST survive
    final = json.loads(settings_file.read_text())
    assert final.get('servers') == seeded['servers'], (
        f"servers side-channel key was CLOBBERED — MCP save-destroys-servers bug regressed. "
        f"before: {seeded['servers']}, after: {final.get('servers')}"
    )
    assert final.get('bar') == "hello", \
        f"unrelated 'bar' key was clobbered: {final}"
    assert final.get('foo') == 2, \
        f"updated 'foo' should be 2, got {final.get('foo')}"


def test_R4_settings_put_overwrites_provided_keys(client, temp_user_dir, monkeypatch):
    """Inverse of R4: if you PUT a key that already exists, it IS updated.
    Pins the expected behavior so the merge is genuinely a merge, not a no-op."""
    c, csrf = client

    settings_dir = temp_user_dir / "webui" / "plugins"
    settings_file = settings_dir / "testplug.json"
    settings_file.write_text(json.dumps({"foo": 1, "bar": "old"}))

    info = _mock_plugin_info('testplug')
    mock_pl = MagicMock()
    mock_pl.get_plugin_info.return_value = info
    mock_pl.get_all_plugin_info.return_value = [info]
    mock_pl._plugins = {'testplug': info}

    import core.plugin_loader
    import core.routes.plugins
    monkeypatch.setattr(core.plugin_loader, 'plugin_loader', mock_pl)
    monkeypatch.setattr(core.routes.plugins, '_get_merged_plugins', lambda: {
        "plugins": {"testplug": {"name": "testplug"}},
        "enabled": ["testplug"],
    })

    mock_sm = MagicMock()
    mock_sm.is_managed.return_value = False
    import core.settings_manager
    monkeypatch.setattr(core.settings_manager, 'settings', mock_sm)

    r = c.put(
        '/api/webui/plugins/testplug/settings',
        headers={'X-CSRF-Token': csrf, 'Content-Type': 'application/json'},
        json={'settings': {'bar': 'new'}},
    )
    assert r.status_code == 200

    final = json.loads(settings_file.read_text())
    assert final.get('bar') == 'new', f"bar should be updated; got {final}"
    assert final.get('foo') == 1, f"foo should be preserved; got {final}"
