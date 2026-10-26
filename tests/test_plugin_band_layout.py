"""The shipped plugin band lives at core/plugins/ (moved from the root plugins/
on 2026-10-17) but is imported as plugins.<name>, both bands alike. These guard
the pieces that make that true, and the boot tripwires that keep the move's
silent-failure class loud: a counted .parent chain one level short lands on
core/ (a real folder) and quietly builds core/user/...; a hand-made plugin left
in the old root plugins/ is simply never scanned; a plugin imported by its disk
path (core.plugins.<x>) is a second module object with its own state."""
import json
import logging
import re
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from core.hooks import HookRunner
from core.paths import PROJECT_ROOT, SYSTEM_PLUGINS_DIR, USER_PLUGINS_DIR
from core.plugin_loader import PluginLoader


def test_the_bands_sit_at_the_same_depth_under_the_root():
    assert SYSTEM_PLUGINS_DIR == PROJECT_ROOT / "core" / "plugins"
    assert USER_PLUGINS_DIR == PROJECT_ROOT / "user" / "plugins"
    assert SYSTEM_PLUGINS_DIR.is_dir()
    assert (PROJECT_ROOT / "sapphire.py").is_file()     # the marker the standalone walkers look for


def test_plugins_namespace_resolves_into_the_shipped_band():
    import plugins
    assert [Path(p) for p in plugins.__path__] == [SYSTEM_PLUGINS_DIR, USER_PLUGINS_DIR]   # band-neutral, shipped first
    import plugins.memory.tools.memory_tools as m
    assert m.__name__ == "plugins.memory.tools.memory_tools"
    assert Path(m.__file__).is_relative_to(SYSTEM_PLUGINS_DIR)


def test_the_shipped_band_is_a_plain_folder_to_pytest():
    """No __init__.py here: pytest would collect core/plugins as a Package,
    import that file itself (as `plugins`, shadowing the shim) and put core/
    at sys.path[0] for every plugin test — nine plugin test dirs (556 tests)
    errored when run alone before this was learned (2026-10-17)."""
    assert not (SYSTEM_PLUGINS_DIR / "__init__.py").exists()


_DISK_PATH_IMPORT = re.compile(
    r"^\s*(?:from|import)\s+core\.plugins\b"      # from core.plugins.x import y / import core.plugins.x
    r"|^\s*from\s+core\s+import\s+plugins\b"       # from core import plugins
    r"|['\"]core\.plugins[.'\"]",                    # import_module("core.plugins.x"), f"core.plugins.{x}", spec_from_file_location("core.plugins...")
    re.M)
_EXEMPT = {"core/plugin_loader.py"}      # spells the name only to refuse it at boot


def test_nothing_imports_a_plugin_by_its_disk_path():
    """The disk path is not the import path: core.plugins.<x> would be a second
    module object next to plugins.<x>, with its own locks and state."""
    offenders = []
    tops = [PROJECT_ROOT / t for t in ("core", "tests", "tools", "functions")] + [USER_PLUGINS_DIR]
    files = [p for top in tops if top.is_dir() for p in top.rglob("*.py")]
    files += [PROJECT_ROOT / n for n in ("sapphire.py", "main.py", "plugins.py")]
    for f in files:
        if "__pycache__" in f.parts or f.name == Path(__file__).name or not f.exists():
            continue
        if str(f.relative_to(PROJECT_ROOT)) in _EXEMPT:
            continue
        if _DISK_PATH_IMPORT.search(f.read_text(encoding="utf-8", errors="replace")):
            offenders.append(str(f.relative_to(PROJECT_ROOT)))
    assert not offenders, f"import shipped plugins as plugins.<name>, never core.plugins.<name>: {offenders}"


def test_one_collision_rule_guards_every_install_door():
    """A user plugin may not take a shipped plugin's name: it would shadow it at
    the next boot (or erase it, if blocked as unsigned). The REST installer had
    this rule, the store tool — Sapphire's own door — did not (2026-10-17)."""
    from core.plugin_loader import core_name_collision
    assert "core/plugins/memory" in core_name_collision("memory")
    assert "retired" in core_name_collision("setup-wizard")          # the core-ui class is gone; its names stay reserved
    a_function = next(p.stem for p in sorted((PROJECT_ROOT / "functions").glob("*.py"))
                      if not p.stem.startswith("_") and not (SYSTEM_PLUGINS_DIR / p.stem).is_dir())
    assert "core function" in core_name_collision(a_function)
    assert core_name_collision("definitely-not-a-plugin-name") is None
    for door in ("core/routes/plugins.py",
                 "core/plugins/sapphire-store/tools/store_tools.py",
                 "core/plugins/toolmaker/tools/toolmaker.py"):
        assert "core_name_collision(" in (PROJECT_ROOT / door).read_text(encoding="utf-8"), door


# --- boot tripwires -----------------------------------------------------------

@pytest.fixture
def fake_root(tmp_path):
    (tmp_path / "core" / "plugins").mkdir(parents=True)
    (tmp_path / "user" / "plugins").mkdir(parents=True)
    pj = tmp_path / "user" / "webui" / "plugins.json"
    pj.parent.mkdir(parents=True)
    pj.write_text('{"enabled": []}', encoding="utf-8")
    return tmp_path


def _scan(root, caplog):
    loader = PluginLoader()
    _scan.last_loader = loader
    with patch("core.plugin_loader.PROJECT_ROOT", root), \
         patch("core.plugin_loader.SYSTEM_PLUGINS_DIR", root / "core" / "plugins"), \
         patch("core.plugin_loader.USER_PLUGINS_DIR", root / "user" / "plugins"), \
         patch("core.plugin_loader.USER_PLUGINS_JSON", root / "user" / "webui" / "plugins.json"), \
         patch("core.plugin_loader.hook_runner", HookRunner()), \
         caplog.at_level(logging.WARNING, logger="core.plugin_loader"):
        loader.scan()
    return caplog.text


def test_boot_warns_about_a_plugin_left_in_the_old_root(fake_root, caplog):
    stray = fake_root / "plugins" / "my-avatar-bridge"
    stray.mkdir(parents=True)
    (stray / "plugin.json").write_text(json.dumps({"name": "my-avatar-bridge"}), encoding="utf-8")
    text = _scan(fake_root, caplog)
    assert "no longer scanned" in text
    assert "my-avatar-bridge" in text and "user/plugins/" in text


def test_boot_tells_a_zip_overlay_user_to_delete_old_shipped_copies(fake_root, caplog):
    (fake_root / "core" / "plugins" / "memory").mkdir()
    (fake_root / "core" / "plugins" / "memory" / "plugin.json").write_text(json.dumps({"name": "memory"}), encoding="utf-8")
    old = fake_root / "plugins" / "memory"
    old.mkdir(parents=True)
    (old / "plugin.json").write_text(json.dumps({"name": "memory"}), encoding="utf-8")
    text = _scan(fake_root, caplog)
    assert "old copies of 1 shipped plugin" in text and "do NOT move" in text
    assert "your own plugins" not in text


def test_boot_never_tells_a_user_to_delete_the_folder_holding_their_own_plugin(fake_root, caplog):
    (fake_root / "core" / "plugins" / "memory").mkdir()
    (fake_root / "core" / "plugins" / "memory" / "plugin.json").write_text("{}", encoding="utf-8")
    for name in ("memory", "my-avatar-bridge"):
        d = fake_root / "plugins" / name
        d.mkdir(parents=True)
        (d / "plugin.json").write_text(json.dumps({"name": name}), encoding="utf-8")
    text = _scan(fake_root, caplog)
    assert "delete those folders" in text and "['memory']" in text
    assert "my-avatar-bridge" in text and "your own plugins" in text
    assert "delete the root" not in text


def test_boot_survives_an_unreadable_stray_folder(fake_root, caplog):
    import os
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root reads anything")
    locked = fake_root / "plugins" / "locked"
    locked.mkdir(parents=True)
    locked.chmod(0o000)
    try:
        _scan(fake_root, caplog)            # must not raise: one bad folder never takes every plugin down
    finally:
        locked.chmod(0o700)


def test_boot_errors_when_the_plugins_namespace_is_not_the_shim(fake_root, caplog, monkeypatch):
    import plugins
    monkeypatch.setattr(plugins, "__file__", str(fake_root / "plugins" / "__init__.py"))
    text = _scan(fake_root, caplog)
    assert "not the shim" in text


def test_boot_errors_when_something_built_user_user(fake_root, caplog):
    (fake_root / "user" / "user").mkdir()
    assert "one level short" in _scan(fake_root, caplog)


def test_boot_puts_the_stray_plugin_on_the_toast_lane(fake_root, caplog):
    stray = fake_root / "plugins" / "my-avatar-bridge"
    stray.mkdir(parents=True)
    (stray / "plugin.json").write_text("{}", encoding="utf-8")
    _scan(fake_root, caplog)
    errs = [e for e in _scan.last_loader._load_errors if e.get("plugin") == "my-avatar-bridge"]
    assert errs and "user/plugins/my-avatar-bridge" in errs[0]["hint"]


def test_boot_errors_when_a_package_sits_at_the_old_root(fake_root, caplog):
    (fake_root / "plugins").mkdir()
    (fake_root / "plugins" / "__init__.py").write_text("", encoding="utf-8")
    text = _scan(fake_root, caplog)
    assert "shadows the plugins.py shim" in text


def test_boot_stays_quiet_for_the_skeleton_git_pull_leaves_behind(fake_root, caplog):
    (fake_root / "plugins" / "discord" / "__pycache__").mkdir(parents=True)
    text = _scan(fake_root, caplog)
    assert "no longer scanned" not in text


def test_boot_errors_when_something_built_core_user(fake_root, caplog):
    (fake_root / "core" / "user").mkdir()
    text = _scan(fake_root, caplog)
    assert "one level short" in text


def test_boot_errors_when_a_plugin_was_imported_by_disk_path(fake_root, caplog, monkeypatch):
    monkeypatch.setitem(sys.modules, "core.plugins.ghost", types.ModuleType("core.plugins.ghost"))
    text = _scan(fake_root, caplog)
    assert "imported by disk path" in text and "core.plugins.ghost" in text


def test_boot_is_quiet_on_a_clean_tree(fake_root, caplog):
    text = _scan(fake_root, caplog)
    for bad in ("one level short", "no longer scanned", "imported by disk path", "not the shim", "shadows the plugins.py shim"):
        assert bad not in text
