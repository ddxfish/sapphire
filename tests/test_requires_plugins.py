"""Manifest `requires_plugins` — an add-on does not load without its host.

S0 doors (2026-09-21): discord-personality rides the discord plugin's hooks
and api facade. A missing or disabled host = the add-on stays enabled but
unloaded with one load_errors line (same shape as missing pip deps); boot
loads hosts before add-ons regardless of name order; toggling the host on
later retries the add-on.
"""
import json
from types import SimpleNamespace

import pytest


@pytest.fixture
def tree(tmp_path, monkeypatch):
    import core.plugin_loader as pl

    system = tmp_path / "system"
    user = tmp_path / "user"
    webui = tmp_path / "webui"
    for d in (system, user, webui):
        d.mkdir()
    plugins_json = webui / "plugins.json"
    plugins_json.write_text('{"enabled": [], "disabled": []}')
    monkeypatch.setattr(pl, "SYSTEM_PLUGINS_DIR", system)
    monkeypatch.setattr(pl, "USER_PLUGINS_DIR", user)
    monkeypatch.setattr(pl, "USER_PLUGINS_JSON", plugins_json)
    import config
    monkeypatch.setattr(config, "ALLOW_UNSIGNED_PLUGINS", True, raising=False)

    def add(name, **manifest):
        d = system / name
        d.mkdir()
        m = {"name": name, "version": "1.0.0", "description": "t"}
        m.update(manifest)
        (d / "plugin.json").write_text(json.dumps(m))
        return d

    def enabled(*names):
        plugins_json.write_text(json.dumps({"enabled": list(names), "disabled": []}))

    return SimpleNamespace(add=add, enabled=enabled, loader=pl.PluginLoader())


def _errors_for(loader, name):
    return [e for e in loader.get_load_errors() if e.get("plugin") == name]


def test_addon_loads_after_its_host_even_when_it_sorts_first(tree):
    tree.add("aaa-addon", requires_plugins=["zzz-host"])
    tree.add("zzz-host")
    tree.enabled("aaa-addon", "zzz-host")

    tree.loader.scan()

    assert tree.loader.get_plugin_info("zzz-host")["loaded"] is True
    assert tree.loader.get_plugin_info("aaa-addon")["loaded"] is True
    assert _errors_for(tree.loader, "aaa-addon") == []


def test_addon_without_host_stays_enabled_but_unloaded(tree):
    tree.add("addon", requires_plugins=["host"])
    tree.enabled("addon")

    tree.loader.scan()

    info = tree.loader.get_plugin_info("addon")
    assert info["enabled"] is True and info["loaded"] is False
    assert info["missing_plugins"] == ["host"]
    errs = _errors_for(tree.loader, "addon")
    assert len(errs) == 1 and "Requires plugin(s) not loaded: host" in errs[0]["error"]
    assert "Enable host first" in errs[0]["hint"]


def test_disabled_host_counts_as_missing(tree):
    tree.add("addon", requires_plugins=["host"])
    tree.add("host")
    tree.enabled("addon")           # host present on disk, not enabled

    tree.loader.scan()

    assert tree.loader.get_plugin_info("host")["loaded"] is False
    assert tree.loader.get_plugin_info("addon")["loaded"] is False
    assert _errors_for(tree.loader, "addon")


def test_host_loading_later_retries_the_addon(tree):
    tree.add("addon", requires_plugins=["host"])
    tree.add("host")
    tree.enabled("addon")
    tree.loader.scan()
    assert tree.loader.get_plugin_info("addon")["loaded"] is False

    # The toggle-on lane: routes call _load_plugin on the host directly.
    tree.loader._plugins["host"]["enabled"] = True
    assert tree.loader._load_plugin("host") is True

    addon = tree.loader.get_plugin_info("addon")
    assert addon["loaded"] is True
    assert addon["missing_plugins"] == []
    assert _errors_for(tree.loader, "addon") == []


def test_plugins_without_the_key_are_untouched(tree):
    tree.add("plain")
    tree.enabled("plain")
    tree.loader.scan()
    info = tree.loader.get_plugin_info("plain")
    assert info["loaded"] is True and info["missing_plugins"] == []
