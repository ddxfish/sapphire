"""Plugin routes — P1 tier coverage (beyond regression guards).

These guard behavior that should be stable but isn't yet tested at the HTTP
level. Install security surface, toggle edge cases, uninstall safety.

See tmp/coverage-test-plan.md Surface 1 P1.
"""
import io
import json
import threading
import zipfile
from unittest.mock import MagicMock, patch

import pytest


def _mock_plugin_info(name, enabled=True, loaded=True, band="system"):
    return {
        "name": name,
        "enabled": enabled,
        "loaded": loaded,
        "verified": True,
        "verify_msg": "verified",
        "verify_tier": "official",
        "band": band,
        "path": f"/tmp/fake/{name}",
        "manifest": {"name": name},
        "missing_deps": [],
    }


def _make_zip(entries, manifest=None):
    """Build an in-memory zip from a list of (path, content_bytes) entries.
    Optionally prepends a plugin.json manifest."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        if manifest is not None:
            zf.writestr('plugin.json', json.dumps(manifest))
        for path, content in entries:
            zf.writestr(path, content)
    buf.seek(0)
    return buf


def _make_zip_with_symlink(symlink_path='plugin.json'):
    """Build a zip whose entry is marked as a symlink via Unix mode bits."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        # Symlink mode = 0o120000
        info = zipfile.ZipInfo(symlink_path)
        info.external_attr = 0o120777 << 16
        zf.writestr(info, '/etc/passwd')
    buf.seek(0)
    return buf


# =============================================================================
# TOGGLE EDGE CASES
# =============================================================================

def test_toggle_unknown_plugin_returns_404(client, temp_user_dir, monkeypatch):
    """Unknown plugin in toggle path must 404, not 500."""
    c, csrf = client

    mock_pl = MagicMock()
    mock_pl.get_plugin_info.return_value = None
    mock_pl.get_all_plugin_info.return_value = []

    import core.plugin_loader
    import core.routes.plugins
    monkeypatch.setattr(core.plugin_loader, 'plugin_loader', mock_pl)
    monkeypatch.setattr(core.routes.plugins, '_get_merged_plugins', lambda: {
        "plugins": {}, "enabled": [],
    })

    r = c.put('/api/webui/plugins/toggle/does-not-exist', headers={'X-CSRF-Token': csrf})
    assert r.status_code == 404
    assert 'Unknown plugin' in r.json().get('detail', '')


def test_toggle_locked_plugin_returns_403(client, temp_user_dir, monkeypatch):
    """Locked plugins (memory, continuity, backup, etc.) cannot be toggled off."""
    c, csrf = client

    # LOCKED_PLUGINS is a module-level set; inject a test name
    import core.routes.plugins as rp
    monkeypatch.setattr(rp, 'LOCKED_PLUGINS', {'locked-test-plugin'})

    r = c.put('/api/webui/plugins/toggle/locked-test-plugin', headers={'X-CSRF-Token': csrf})
    assert r.status_code == 403
    assert 'locked' in r.json().get('detail', '').lower()


def test_toggle_publishes_toolset_changed_event(
    client, temp_user_dir, monkeypatch, event_bus_capture
):
    """Toggling a plugin must publish TOOLSET_CHANGED with action='plugin_toggle'
    so the frontend resyncs the sidebar tool pills immediately."""
    c, csrf = client

    info = _mock_plugin_info('foo', enabled=True, loaded=True)
    mock_pl = MagicMock()
    mock_pl.get_plugin_info.return_value = info
    mock_pl.get_all_plugin_info.return_value = [info]
    mock_pl._plugins = {'foo': info}
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

    # Ensure get_system has a function_manager attribute the route can poke
    mock_sys = MagicMock()
    mock_sys.llm_chat.function_manager.get_current_toolset_info.return_value = {
        "name": "custom", "function_count": 5,
    }
    monkeypatch.setattr(core.routes.plugins, 'get_system', lambda: mock_sys)

    r = c.put('/api/webui/plugins/toggle/foo', headers={'X-CSRF-Token': csrf})
    assert r.status_code == 200

    # Look for the TOOLSET_CHANGED event
    toolset_events = [
        d for (ev, d) in event_bus_capture.events
        if (ev == 'toolset_changed' or ev == 'TOOLSET_CHANGED')
        or (isinstance(ev, str) and 'toolset' in ev.lower())
    ]
    assert toolset_events, (
        f"TOOLSET_CHANGED not published on toggle; captured events: "
        f"{[ev for ev, _ in event_bus_capture.events]}"
    )


# =============================================================================
# INSTALL — ZIP SECURITY
# =============================================================================

def test_install_rejects_path_traversal_in_zip(client, temp_user_dir, monkeypatch):
    """Zip containing a `..` entry must be rejected before extraction."""
    c, csrf = client

    zip_buf = _make_zip(
        entries=[('../evil.py', b'import os; os.system("pwn")')],
        manifest={'name': 'badplug', 'version': '1.0', 'description': 'x'},
    )

    r = c.post(
        '/api/plugins/install',
        headers={'X-CSRF-Token': csrf},
        files={'file': ('plug.zip', zip_buf, 'application/zip')},
    )
    assert r.status_code == 400, f"got {r.status_code} {r.text}"
    assert 'unsafe path' in r.json().get('detail', '').lower() or \
           '..' in r.json().get('detail', ''), f"got: {r.json()}"


def test_install_rejects_absolute_path_in_zip(client, temp_user_dir, monkeypatch):
    """Zip containing an absolute path (starts with /) is rejected."""
    c, csrf = client

    zip_buf = _make_zip(
        entries=[('/etc/passwd', b'evil')],
        manifest={'name': 'badplug', 'version': '1.0', 'description': 'x'},
    )

    r = c.post(
        '/api/plugins/install',
        headers={'X-CSRF-Token': csrf},
        files={'file': ('plug.zip', zip_buf, 'application/zip')},
    )
    assert r.status_code == 400
    assert 'unsafe path' in r.json().get('detail', '').lower()


def test_install_rejects_symlink_in_zip(client, temp_user_dir, monkeypatch):
    """Zip with a symlink entry is rejected (symlinks are a path-traversal vector
    even if the path itself looks relative)."""
    c, csrf = client

    zip_buf = _make_zip_with_symlink('some_file.py')
    r = c.post(
        '/api/plugins/install',
        headers={'X-CSRF-Token': csrf},
        files={'file': ('plug.zip', zip_buf, 'application/zip')},
    )
    assert r.status_code == 400
    assert 'symlink' in r.json().get('detail', '').lower()


def test_install_rejects_invalid_zip(client, temp_user_dir):
    """Non-zip content must be rejected with 400 'Not a valid zip file'."""
    c, csrf = client
    r = c.post(
        '/api/plugins/install',
        headers={'X-CSRF-Token': csrf},
        files={'file': ('plug.zip', io.BytesIO(b'not a zip'), 'application/zip')},
    )
    assert r.status_code == 400
    assert 'valid zip' in r.json().get('detail', '').lower()


def test_install_rejects_missing_manifest(client, temp_user_dir):
    """Zip without plugin.json must 400 'No plugin.json found in zip'."""
    c, csrf = client
    zip_buf = _make_zip(entries=[('README.md', b'# not a plugin')])
    r = c.post(
        '/api/plugins/install',
        headers={'X-CSRF-Token': csrf},
        files={'file': ('plug.zip', zip_buf, 'application/zip')},
    )
    assert r.status_code == 400
    assert 'plugin.json' in r.json().get('detail', '').lower()


def test_install_rejects_invalid_name_chars(client, temp_user_dir):
    """Plugin name with slashes/dots is rejected — path traversal defense."""
    c, csrf = client
    zip_buf = _make_zip(
        entries=[('dummy.py', b'x')],
        manifest={'name': '../../etc', 'version': '1.0', 'description': 'x'},
    )
    r = c.post(
        '/api/plugins/install',
        headers={'X-CSRF-Token': csrf},
        files={'file': ('plug.zip', zip_buf, 'application/zip')},
    )
    assert r.status_code == 400
    assert 'invalid plugin name' in r.json().get('detail', '').lower()


def test_install_rejects_missing_required_manifest_fields(client, temp_user_dir):
    """plugin.json missing name/version/description → 400."""
    c, csrf = client
    zip_buf = _make_zip(
        entries=[('dummy.py', b'x')],
        manifest={'name': 'foo'},  # missing version + description
    )
    r = c.post(
        '/api/plugins/install',
        headers={'X-CSRF-Token': csrf},
        files={'file': ('plug.zip', zip_buf, 'application/zip')},
    )
    assert r.status_code == 400
    detail = r.json().get('detail', '').lower()
    assert 'name' in detail and 'version' in detail and 'description' in detail


def test_install_blocks_zip_upload_in_managed_mode(client, temp_user_dir, monkeypatch):
    """Managed mode (Docker / locked deployment) must refuse zip upload — users
    can only install plugins via the controlled signing pipeline."""
    c, csrf = client

    mock_sm = MagicMock()
    mock_sm.is_managed.return_value = True
    import core.settings_manager
    monkeypatch.setattr(core.settings_manager, 'settings', mock_sm)

    zip_buf = _make_zip(
        entries=[('dummy.py', b'x')],
        manifest={'name': 'foo', 'version': '1.0', 'description': 'y'},
    )
    r = c.post(
        '/api/plugins/install',
        headers={'X-CSRF-Token': csrf},
        files={'file': ('plug.zip', zip_buf, 'application/zip')},
    )
    assert r.status_code == 403
    assert 'managed' in r.json().get('detail', '').lower()


# =============================================================================
# UNINSTALL
# =============================================================================

def test_uninstall_rejects_system_plugin(client, temp_user_dir, monkeypatch):
    """System plugins (shipped in-tree) cannot be uninstalled via API —
    only user plugins can. Protects against install → toggle off the wrong
    thing → whoops-memory-is-gone scenarios."""
    c, csrf = client

    sys_info = _mock_plugin_info('memory', band='system')
    mock_pl = MagicMock()
    mock_pl.get_plugin_info.return_value = sys_info
    mock_pl.uninstall_plugin.side_effect = ValueError("Cannot uninstall system plugin: memory")

    import core.plugin_loader
    monkeypatch.setattr(core.plugin_loader, 'plugin_loader', mock_pl)

    r = c.delete('/api/plugins/memory/uninstall', headers={'X-CSRF-Token': csrf})
    # Route may raise ValueError as 400 or 403 — both are acceptable "refused"
    assert r.status_code in (400, 403), f"got {r.status_code} {r.text}"


def test_uninstall_unknown_plugin_returns_404(client, temp_user_dir, monkeypatch):
    """Uninstalling a plugin that doesn't exist must 404, not 500."""
    c, csrf = client

    mock_pl = MagicMock()
    mock_pl.get_plugin_info.return_value = None
    mock_pl.uninstall_plugin.side_effect = ValueError("Unknown plugin: ghost")

    import core.plugin_loader
    monkeypatch.setattr(core.plugin_loader, 'plugin_loader', mock_pl)

    r = c.delete('/api/plugins/ghost/uninstall', headers={'X-CSRF-Token': csrf})
    # Accept 404 or 400 (route converts ValueError)
    assert r.status_code in (400, 404)


# =============================================================================
# INSTALL — NAME COLLISION + CLEANUP SAFETY
# =============================================================================
# Note: Zip-bomb tests (total + per-file uncompressed-size caps) were attempted
# but the test harness returns 503 before reaching the size check — likely
# because zipfile.writestr normalizes ZipInfo.file_size to actual content
# length. A real bomb test needs genuine 100MB+ compressible content, which
# is too heavy for a unit test. TODO: add as a slow-marker integration test.

def test_install_rejects_name_collision_with_system_plugin(client, temp_user_dir, monkeypatch):
    """Installing a plugin named 'memory' must fail with 409 — 'memory' is a
    system plugin shipped in core/plugins/ and must not be overwritten by a user
    install. Same guard applies to any system-plugin name."""
    c, csrf = client

    zip_buf = _make_zip(
        entries=[('dummy.py', b'x = 1')],
        manifest={'name': 'memory', 'version': '99.0', 'description': 'malicious overlay'},
    )

    r = c.post(
        '/api/plugins/install',
        headers={'X-CSRF-Token': csrf},
        files={'file': ('plug.zip', zip_buf, 'application/zip')},
    )
    assert r.status_code == 409, f"got {r.status_code} {r.text}"
    detail = r.json().get('detail', '').lower()
    assert 'system plugin' in detail or 'conflict' in detail, f"got: {r.json()}"


def test_install_rejects_retired_core_name(client, temp_user_dir):
    """'setup-wizard' was a core-ui plugin (the class retired 2026-10-25): an old
    install's plugins.json may still enable the name, so no user plugin may take it."""
    c, csrf = client

    zip_buf = _make_zip(
        entries=[('dummy.py', b'x')],
        manifest={'name': 'setup-wizard', 'version': '1.0', 'description': 'clash'},
    )

    r = c.post(
        '/api/plugins/install',
        headers={'X-CSRF-Token': csrf},
        files={'file': ('plug.zip', zip_buf, 'application/zip')},
    )
    assert r.status_code == 409


def test_install_cleans_tmp_on_extract_failure(client, temp_user_dir, monkeypatch, tmp_path):
    """If extraction raises mid-way through, the tmp zip + tmp dir must be
    cleaned up — no /tmp/ litter after a failed install. Shell out tests via
    tracking tmp-path sentinels."""
    c, csrf = client

    # Patch zipfile.ZipFile.extractall to raise AFTER initial validation
    import zipfile as _zf
    def _boom(self, path=None, members=None, pwd=None):
        raise RuntimeError("simulated extract failure")
    monkeypatch.setattr(_zf.ZipFile, 'extractall', _boom, raising=True)

    zip_buf = _make_zip(
        entries=[('hello.py', b'print(1)')],
        manifest={'name': 'normalplug', 'version': '1.0', 'description': 'clean'},
    )

    import tempfile as _tempfile
    created_paths = []
    orig_mkdtemp = _tempfile.mkdtemp

    def _tracking_mkdtemp(*a, **kw):
        p = orig_mkdtemp(*a, **kw)
        created_paths.append(p)
        return p

    monkeypatch.setattr(_tempfile, 'mkdtemp', _tracking_mkdtemp)

    r = c.post(
        '/api/plugins/install',
        headers={'X-CSRF-Token': csrf},
        files={'file': ('plug.zip', zip_buf, 'application/zip')},
    )
    # Route surfaces the error as 500 or 400 — either is acceptable failure mode
    assert r.status_code >= 400

    # The tmp dirs created via mkdtemp should all be gone (cleanup in finally)
    import os
    still_present = [p for p in created_paths if os.path.exists(p)]
    assert still_present == [], (
        f"tmp dirs leaked after failed install: {still_present}"
    )
