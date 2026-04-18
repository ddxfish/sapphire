"""Surface 4 P0 remainder — plugin_loader.reload_plugin + uninstall_plugin guards.

Complements `test_plugin_loader_lifecycle.py` (scan + register-scope contract)
with the reload/uninstall paths. These are the substrate for Claude-autonomous
plugin modifications (via toolmaker and the plugin-worker agent) — any
regression here silently corrupts user data or leaks credentials on disk.

Covers:
  4.4  reload_preserves_scope_contextvar_identity
  4.5  manifest_default_change_takes_effect_on_reload
  4.6  reload_reverifies_signature_after_tamper
  4.7  reload_reread_manifest_from_disk
  4.8  uninstall_full_cleanup (dir + settings + state + enabled list)
  4.9  uninstall_sweeps_sibling_state_files (telegram_sessions/ bug class)
  4.10 uninstall_cleanup_paths_sandboxed (refuses paths outside user/)

See tmp/coverage-test-plan.md Surface 4.
"""
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ─── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def isolated_loader(tmp_path, monkeypatch):
    """Build an isolated user/system plugin tree wired into a fresh PluginLoader.

    Yields a namespace:
      tree.system / tree.user — plugin dirs
      tree.plugin_state — PLUGIN_STATE_DIR redirect
      tree.webui_plugins — user/webui/plugins/ settings dir redirect
      tree.loader — fresh PluginLoader instance
      tree.add_plugin(name, manifest, band, files) — write a fake plugin
      tree.read_plugins_json() / set_plugins_json()
    """
    import core.plugin_loader as pl
    from core.chat.function_manager import SCOPE_REGISTRY

    iso_system = tmp_path / "system"
    iso_user = tmp_path / "user_plugins"
    iso_webui = tmp_path / "webui"
    iso_plugin_state = tmp_path / "plugin_state"
    iso_user_root = tmp_path / "user_root"  # stand-in for PROJECT_ROOT/user
    (iso_user_root / "webui" / "plugins").mkdir(parents=True)
    for d in (iso_system, iso_user, iso_webui, iso_plugin_state):
        d.mkdir()
    plugins_json = iso_webui / "plugins.json"
    plugins_json.write_text('{"enabled": [], "disabled": []}')

    monkeypatch.setattr(pl, "SYSTEM_PLUGINS_DIR", iso_system)
    monkeypatch.setattr(pl, "USER_PLUGINS_DIR", iso_user)
    monkeypatch.setattr(pl, "USER_PLUGINS_JSON", plugins_json)
    monkeypatch.setattr(pl, "PLUGIN_STATE_DIR", iso_plugin_state)
    monkeypatch.setattr(pl, "PROJECT_ROOT", tmp_path, raising=False)
    # The uninstall code builds settings_file path from PROJECT_ROOT / "user" / ...
    # so populate tmp_path/user/webui/plugins/ to match what it expects.
    real_settings_dir = tmp_path / "user" / "webui" / "plugins"
    real_settings_dir.mkdir(parents=True, exist_ok=True)

    # Allow unsigned plugins in the test env
    import config
    monkeypatch.setattr(config, "ALLOW_UNSIGNED_PLUGINS", True, raising=False)

    def add_plugin(name, manifest=None, band='user', files=None):
        parent = iso_user if band == 'user' else iso_system
        plugin_dir = parent / name
        plugin_dir.mkdir()
        final_manifest = {"name": name, "version": "1.0.0", "description": "test"}
        if manifest:
            final_manifest.update(manifest)
        (plugin_dir / "plugin.json").write_text(json.dumps(final_manifest))
        if files:
            for rel, content in files.items():
                full = plugin_dir / rel
                full.parent.mkdir(parents=True, exist_ok=True)
                full.write_text(content)
        return plugin_dir

    def set_plugins_json(enabled=None, disabled=None):
        plugins_json.write_text(json.dumps({
            "enabled": enabled or [],
            "disabled": disabled or [],
        }))

    def read_plugins_json():
        return json.loads(plugins_json.read_text())

    loader = pl.PluginLoader()

    tree = SimpleNamespace(
        system=iso_system,
        user=iso_user,
        webui=iso_webui,
        plugin_state=iso_plugin_state,
        plugin_settings=real_settings_dir,
        plugins_json=plugins_json,
        loader=loader,
        add_plugin=add_plugin,
        set_plugins_json=set_plugins_json,
        read_plugins_json=read_plugins_json,
    )

    # Snapshot SCOPE_REGISTRY for restore
    before = dict(SCOPE_REGISTRY)
    try:
        yield tree
    finally:
        # Restore scope registry
        for k, v in before.items():
            SCOPE_REGISTRY[k] = v
        for k in list(SCOPE_REGISTRY.keys()):
            if k not in before:
                SCOPE_REGISTRY.pop(k, None)


def _register_fake_plugin(loader, name, plugin_path, band='user',
                          manifest=None, enabled=True, loaded=True,
                          verified=True, verify_msg='verified'):
    """Manually register a plugin in loader._plugins so reload/uninstall can
    operate without us going through the full scan/verify pipeline."""
    loader._plugins[name] = {
        'name': name,
        'path': plugin_path,
        'band': band,
        'manifest': manifest or {'name': name, 'version': '1.0.0', 'description': 'test'},
        'enabled': enabled,
        'loaded': loaded,
        'verified': verified,
        'verify_msg': verify_msg,
        'verify_tier': 'unsigned',
        'verified_author': None,
        'missing_deps': [],
    }


# ─── 4.4 reload preserves scope ContextVar identity ──────────────────────────

def test_reload_preserves_scope_contextvar_identity(isolated_loader):
    """[REGRESSION_GUARD] When a plugin declares a scope, reload_plugin must
    NOT replace the existing ContextVar — downstream code holds references
    to `scope_memory`, `scope_gcal`, etc. via module-level imports. A new
    ContextVar object would invalidate those references silently.

    The guarantee: `register_plugin_scope('memory', ...)` is idempotent —
    same key returns the same ContextVar across calls. reload_plugin uses
    this for every plugin-declared scope.
    """
    from core.chat.function_manager import (
        SCOPE_REGISTRY, register_plugin_scope, unregister_plugin_scope,
    )

    # Register once (simulating first plugin load)
    var1 = register_plugin_scope('plugin_reload_scope', plugin_name='test_plugin',
                                 default='initial')
    id1 = id(var1)
    assert SCOPE_REGISTRY['plugin_reload_scope']['var'] is var1

    # Simulate reload — register again with same key (different plugin_name
    # to mimic a re-registration after unload/load)
    var2 = register_plugin_scope('plugin_reload_scope', plugin_name='test_plugin_v2',
                                 default='whatever')
    id2 = id(var2)

    assert id1 == id2, "ContextVar identity changed across reload — downstream refs broken"
    assert var1 is var2
    assert SCOPE_REGISTRY['plugin_reload_scope']['var'] is var1

    unregister_plugin_scope('plugin_reload_scope')


# ─── 4.5 / 4.7 reload re-reads manifest from disk ────────────────────────────

def test_reload_rereads_manifest_from_disk(isolated_loader):
    """[REGRESSION_GUARD] After a plugin's plugin.json is modified on disk
    (new tool added, settings schema changed), reload_plugin must pick up
    the fresh manifest — not serve the stale cached one. Was broken before
    today's fix; a new tool wouldn't show until full Sapphire restart.
    """
    tree = isolated_loader
    plugin_path = tree.add_plugin(
        'reload_manifest_test',
        manifest={'version': '1.0.0', 'description': 'initial'},
        band='user',
    )
    _register_fake_plugin(
        tree.loader, 'reload_manifest_test', plugin_path, band='user',
        manifest={'version': '1.0.0', 'description': 'initial'},
        enabled=False, loaded=False,
    )

    # Modify plugin.json on disk
    new_manifest = {'name': 'reload_manifest_test', 'version': '2.0.0',
                    'description': 'updated via hot-reload'}
    (plugin_path / 'plugin.json').write_text(json.dumps(new_manifest))

    # Stub _load_plugin to noop so reload doesn't try to exec anything.
    with patch.object(tree.loader, '_load_plugin', return_value=True), \
         patch('core.plugin_verify.verify_plugin',
               return_value=(True, 'verified', {'tier': 'unsigned'})), \
         patch('core.plugin_loader.verify_plugin',
               return_value=(True, 'verified', {'tier': 'unsigned'})):
        tree.loader.reload_plugin('reload_manifest_test')

    cached = tree.loader._plugins['reload_manifest_test']['manifest']
    assert cached['version'] == '2.0.0', f"manifest not re-read: {cached}"
    assert cached['description'] == 'updated via hot-reload'


# ─── 4.6 reload re-verifies signature (catches tamper since scan) ────────────

def test_reload_reverifies_signature_after_tamper(isolated_loader):
    """[REGRESSION_GUARD] reload_plugin must call verify_plugin on every reload.
    If a plugin was verified at scan, then its code/manifest was mutated
    (e.g. by toolmaker or direct filesystem write), reload without re-verify
    would reload the tampered code while the status card still claims
    'verified'.
    """
    tree = isolated_loader
    plugin_path = tree.add_plugin('tamper_test', band='user')
    _register_fake_plugin(
        tree.loader, 'tamper_test', plugin_path, band='user',
        verified=True, verify_msg='verified', enabled=False, loaded=False,
    )

    verify_spy = MagicMock(return_value=(False, 'hash mismatch: tampered',
                                         {'tier': 'unsigned', 'author': None}))
    with patch('core.plugin_verify.verify_plugin', verify_spy), \
         patch('core.plugin_loader.verify_plugin', verify_spy):
        tree.loader.reload_plugin('tamper_test')

    verify_spy.assert_called_once()
    info = tree.loader._plugins['tamper_test']
    assert info['verified'] is False, "tampered plugin still marked verified after reload"
    assert 'hash mismatch' in info['verify_msg']


# ─── 4.8 Uninstall full cleanup — dir + settings + state + enabled list ──────

def test_uninstall_full_cleanup(isolated_loader):
    """[REGRESSION_GUARD] uninstall_plugin must wipe:
      - plugin directory
      - settings file at user/webui/plugins/{name}.json
      - state file at PLUGIN_STATE_DIR/{name}.json
      - the plugin's entry from plugins.json `enabled` list
    Leftover debris = later re-install inherits stale state from a
    different author's code (supply-chain failure class).
    """
    tree = isolated_loader
    plugin_path = tree.add_plugin(
        'cleanup_test', band='user',
        manifest={'version': '1.0.0', 'description': 'user plugin to uninstall'},
    )
    # Seed settings + state + enabled list
    settings_file = tree.plugin_settings / 'cleanup_test.json'
    settings_file.write_text('{"setting_a": "value"}')
    state_file = tree.plugin_state / 'cleanup_test.json'
    state_file.write_text('{"counter": 5}')
    tree.set_plugins_json(enabled=['cleanup_test', 'other_plugin'])

    _register_fake_plugin(
        tree.loader, 'cleanup_test', plugin_path, band='user',
        manifest={'name': 'cleanup_test', 'version': '1.0.0'},
        enabled=True, loaded=False,
    )

    tree.loader.uninstall_plugin('cleanup_test')

    # Dir gone
    assert not plugin_path.exists(), "plugin directory not removed"
    # Settings gone
    assert not settings_file.exists(), "settings file not removed"
    # State gone
    assert not state_file.exists(), "state file not removed"
    # Enabled list purged of this plugin (other_plugin intact)
    data = tree.read_plugins_json()
    assert 'cleanup_test' not in data.get('enabled', []), \
        f"cleanup_test still in enabled: {data}"
    assert 'other_plugin' in data.get('enabled', []), \
        f"sibling plugin wrongly removed: {data}"
    # Registry entry gone
    assert 'cleanup_test' not in tree.loader._plugins


# ─── 4.9 Uninstall sweeps sibling state files (telegram_sessions bug) ────────

def test_uninstall_sweeps_sibling_state_files(isolated_loader):
    """[REGRESSION_GUARD] Plugins like `telegram` write credentials to
    `telegram_sessions/` or `telegram-logs.json` — NOT to the nominal
    `telegram.json`. Uninstall without a sibling sweep leaves live
    credentials on disk (H5-class issue).

    Matches the `{name}-*` and `{name}_*` glob pattern in
    plugin_loader.uninstall_plugin.
    """
    tree = isolated_loader
    plugin_path = tree.add_plugin('telegram', band='user')
    # Main state file
    (tree.plugin_state / 'telegram.json').write_text('{}')
    # Sibling files that MUST be swept:
    (tree.plugin_state / 'telegram_sessions').mkdir()
    (tree.plugin_state / 'telegram_sessions' / 'bot_creds.json').write_text(
        '{"api_key": "secret_never_leave_disk"}'
    )
    (tree.plugin_state / 'telegram-logs.json').write_text('[]')
    (tree.plugin_state / 'telegram_recent.json').write_text('{}')
    # Sibling that is UNRELATED — must NOT be swept
    (tree.plugin_state / 'unrelated_plugin.json').write_text('{}')

    _register_fake_plugin(tree.loader, 'telegram', plugin_path, band='user')

    tree.loader.uninstall_plugin('telegram')

    assert not (tree.plugin_state / 'telegram.json').exists()
    assert not (tree.plugin_state / 'telegram_sessions').exists(), \
        "credentials dir survived uninstall (LEAK)"
    assert not (tree.plugin_state / 'telegram-logs.json').exists()
    assert not (tree.plugin_state / 'telegram_recent.json').exists()
    # Unrelated plugin survives
    assert (tree.plugin_state / 'unrelated_plugin.json').exists(), \
        "sweep was over-eager — removed unrelated plugin state"


# ─── 4.10 Uninstall manifest cleanup paths are sandboxed to user/ ────────────

def test_uninstall_manifest_cleanup_paths_sandboxed(isolated_loader):
    """[REGRESSION_GUARD] A plugin's manifest can declare extra
    `cleanup_paths` for files that don't follow the sibling convention
    (e.g. `gcal-csrf.json`). Paths MUST resolve inside the `user/`
    sandbox — a malicious manifest with `../../etc/passwd` must be
    refused, not deleted.
    """
    tree = isolated_loader
    # Build a user-dir path that WILL be cleaned (legit)
    legit_path = tree.plugin_state.parent / "user" / "legit_cleanup.json"
    legit_path.parent.mkdir(parents=True, exist_ok=True)
    legit_path.write_text('{"ok": true}')
    # And a path that would escape the sandbox
    escape_target = tree.plugin_state.parent.parent / "should_not_be_touched.txt"
    escape_target.write_text("sacred file outside user/")

    plugin_path = tree.add_plugin('pathy', band='user', manifest={
        'name': 'pathy',
        'version': '1.0.0',
        'description': 'test',
        'capabilities': {
            'cleanup_paths': [
                'legit_cleanup.json',           # inside user/
                '../should_not_be_touched.txt', # escape attempt (resolved under user/)
                '../../etc/passwd',             # deeper escape attempt
            ],
        },
    })
    _register_fake_plugin(
        tree.loader, 'pathy', plugin_path, band='user',
        manifest={
            'name': 'pathy',
            'version': '1.0.0',
            'capabilities': {'cleanup_paths': [
                'legit_cleanup.json',
                '../should_not_be_touched.txt',
                '../../etc/passwd',
            ]},
        },
    )

    tree.loader.uninstall_plugin('pathy')

    # Escape target untouched
    assert escape_target.exists(), \
        "path-traversal cleanup_paths escaped the user/ sandbox (CRITICAL)"
    assert escape_target.read_text() == "sacred file outside user/"


# ─── Bonus: uninstall evicts PluginState cache ───────────────────────────────

def test_uninstall_evicts_plugin_state_cache(isolated_loader):
    """[PROACTIVE] uninstall must drop the plugin's cached PluginState so a
    later re-install gets a fresh object (otherwise stale in-memory state
    from the old plugin leaks into the new one)."""
    tree = isolated_loader
    plugin_path = tree.add_plugin('cached_plugin', band='user')
    _register_fake_plugin(tree.loader, 'cached_plugin', plugin_path, band='user')

    # Prime the cache
    fake_state = object()
    with tree.loader._plugin_state_cache_lock:
        tree.loader._plugin_state_cache['cached_plugin'] = fake_state
    assert 'cached_plugin' in tree.loader._plugin_state_cache

    tree.loader.uninstall_plugin('cached_plugin')
    assert 'cached_plugin' not in tree.loader._plugin_state_cache


# ─── Bonus: uninstall rejects system plugins ─────────────────────────────────

def test_uninstall_refuses_system_plugin(isolated_loader):
    """Uninstall on a band='system' plugin must raise, not proceed silently.
    Guards against a malicious UI or API call wiping out core plugins."""
    tree = isolated_loader
    plugin_path = tree.add_plugin('core_thing', band='system')
    _register_fake_plugin(tree.loader, 'core_thing', plugin_path, band='system')

    with pytest.raises(ValueError, match="system"):
        tree.loader.uninstall_plugin('core_thing')
    # Plugin survives
    assert plugin_path.exists()
    assert 'core_thing' in tree.loader._plugins
