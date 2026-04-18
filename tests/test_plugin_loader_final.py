"""Surface 4 final — rescan discovery, unload scope purge, enforce_unsigned.

Covers:
  4.16 unload_plugin unregisters scopes declared in manifest capabilities
  4.17 enforce_unsigned_policy scope — only touches unsigned, not tampered
  4.22 rescan discovers newly-added plugin dir
  4.23 rescan detects a removed plugin and unloads it

See tmp/coverage-test-plan.md Surface 4 P1/P2.
"""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def isolated_loader(tmp_path, monkeypatch):
    """Fresh PluginLoader pointed at isolated plugin dirs."""
    import core.plugin_loader as pl
    iso_system = tmp_path / "system"
    iso_user = tmp_path / "user_plugins"
    iso_webui = tmp_path / "webui"
    for d in (iso_system, iso_user, iso_webui):
        d.mkdir()
    plugins_json = iso_webui / "plugins.json"
    plugins_json.write_text('{"enabled": [], "disabled": []}')

    monkeypatch.setattr(pl, "SYSTEM_PLUGINS_DIR", iso_system)
    monkeypatch.setattr(pl, "USER_PLUGINS_DIR", iso_user)
    monkeypatch.setattr(pl, "USER_PLUGINS_JSON", plugins_json)

    import config
    monkeypatch.setattr(config, "ALLOW_UNSIGNED_PLUGINS", True, raising=False)

    def add_plugin(name, manifest=None, band='user'):
        parent = iso_user if band == 'user' else iso_system
        plugin_dir = parent / name
        plugin_dir.mkdir()
        final = {"name": name, "version": "1.0.0", "description": "t"}
        if manifest:
            final.update(manifest)
        (plugin_dir / "plugin.json").write_text(json.dumps(final))
        return plugin_dir

    loader = pl.PluginLoader()
    return SimpleNamespace(
        system=iso_system, user=iso_user, plugins_json=plugins_json,
        loader=loader, add_plugin=add_plugin,
    )


def _register(loader, name, path, band='user', manifest=None,
              enabled=True, loaded=True, verified=True, verify_msg='verified'):
    loader._plugins[name] = {
        'name': name, 'path': path, 'band': band,
        'manifest': manifest or {'name': name, 'version': '1.0.0', 'description': 't'},
        'enabled': enabled, 'loaded': loaded, 'verified': verified,
        'verify_msg': verify_msg, 'verify_tier': 'unsigned',
        'verified_author': None, 'missing_deps': [],
    }


# ─── 4.16 unload purges scopes from registry ─────────────────────────────────

def test_unload_plugin_purges_declared_scopes_from_registry(isolated_loader):
    """[PROACTIVE] A plugin declaring scopes in capabilities must have those
    scopes REMOVED from SCOPE_REGISTRY on unload — otherwise a later reload
    with a different default hits register_plugin_scope's idempotent early-
    return and the default update is silently dropped."""
    from core.chat.function_manager import SCOPE_REGISTRY, register_plugin_scope

    tree = isolated_loader
    plugin_path = tree.add_plugin(
        'scope_owner',
        manifest={'capabilities': {'scopes': [
            {'key': 'temp_test_scope_a', 'setting': 'a_scope', 'default': 'x'},
        ]}},
    )
    _register(
        tree.loader, 'scope_owner', plugin_path, band='user',
        manifest={
            'name': 'scope_owner', 'version': '1.0.0',
            'capabilities': {'scopes': [
                {'key': 'temp_test_scope_a', 'setting': 'a_scope', 'default': 'x'},
            ]},
        },
    )
    # Seed the scope as if registered during load
    register_plugin_scope('temp_test_scope_a', plugin_name='scope_owner', default='x')
    assert 'temp_test_scope_a' in SCOPE_REGISTRY

    tree.loader.unload_plugin('scope_owner')

    assert 'temp_test_scope_a' not in SCOPE_REGISTRY, \
        "unload didn't remove plugin-declared scope from registry"


# ─── 4.17 enforce_unsigned_policy: only unsigned, not tampered ───────────────

def test_enforce_unsigned_policy_leaves_tampered_plugins_alone(isolated_loader):
    """[REGRESSION_GUARD] enforce_unsigned_policy is for the 'disable allow-
    unsigned' flow — it disables plugins whose verify_msg == 'unsigned'.
    Tampered plugins (verify_msg contains 'hash mismatch') are blocked by
    signature verification at load time regardless of sideloading, so they
    MUST not be re-processed here (double-handling = UI state confusion)."""
    tree = isolated_loader
    plugin_path = tree.add_plugin('unsigned_plugin')
    tree.add_plugin('tampered_plugin')
    _register(tree.loader, 'unsigned_plugin', plugin_path, band='user',
              enabled=True, verified=False, verify_msg='unsigned')
    _register(tree.loader, 'tampered_plugin',
              tree.user / 'tampered_plugin', band='user',
              enabled=True, verified=False,
              verify_msg='hash mismatch: content changed')
    _register(tree.loader, 'normal_plugin',
              tree.user / 'normal_plugin', band='user',
              enabled=True, verified=True, verify_msg='verified')

    affected = tree.loader.enforce_unsigned_policy()

    assert affected == ['unsigned_plugin'], \
        f"enforce should only touch unsigned; got {affected}"
    # Tampered plugin's enabled state NOT flipped by this policy
    assert tree.loader._plugins['tampered_plugin']['enabled'] is True
    # Normal plugin also untouched
    assert tree.loader._plugins['normal_plugin']['enabled'] is True


def test_enforce_unsigned_policy_disables_only_enabled_unsigned(isolated_loader):
    """Already-disabled unsigned plugins aren't re-processed."""
    tree = isolated_loader
    p1 = tree.add_plugin('was_disabled')
    _register(tree.loader, 'was_disabled', p1, band='user',
              enabled=False, verified=False, verify_msg='unsigned')

    affected = tree.loader.enforce_unsigned_policy()
    assert affected == [], "already-disabled plugin should not be reported"


# ─── 4.22 Rescan discovers new plugin ────────────────────────────────────────

def test_rescan_discovers_newly_added_plugin(isolated_loader):
    """[REGRESSION_GUARD] Dropping a plugin dir on disk and calling rescan
    must pick it up without a full Sapphire restart."""
    tree = isolated_loader
    # Initial state: one plugin in registry, nothing on disk
    # First scan to populate baseline
    tree.loader.rescan()

    # Now drop a new plugin on disk
    tree.add_plugin('fresh_plugin', band='user')

    result = tree.loader.rescan()
    assert 'fresh_plugin' in result['added'], \
        f"rescan didn't discover new plugin: {result}"
    assert 'fresh_plugin' in tree.loader._plugins


# ─── 4.23 Rescan detects removed plugin ──────────────────────────────────────

def test_rescan_detects_removed_plugin(isolated_loader):
    """[REGRESSION_GUARD] If a plugin dir is deleted, rescan removes it from
    the registry. Guards against ghost plugins persisting in the UI after
    deletion on disk."""
    tree = isolated_loader
    plugin_path = tree.add_plugin('doomed_plugin', band='user')
    tree.loader.rescan()
    assert 'doomed_plugin' in tree.loader._plugins

    # Delete the plugin dir
    import shutil
    shutil.rmtree(plugin_path)

    result = tree.loader.rescan()
    assert 'doomed_plugin' in result['removed']
    assert 'doomed_plugin' not in tree.loader._plugins
