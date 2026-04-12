"""
Plugin manifest validation sweep (Breadth-1).

ONE parameterized test walks every plugin in `plugins/` and `user/plugins/`,
validating shape and structure. Adding a new plugin = free coverage.

This catches the bug class that Chaos Scout #1 found in Phase 4: a malformed
scope declaration in `capabilities.scopes` would 500 the `/api/init` endpoint.
By pinning the structural contract here, we'll fail loud at test time instead
of silently at boot.

PYTEST PRIMER — parametrize
───────────────────────────
`@pytest.mark.parametrize("plugin_dir", discover_plugins(), ids=plugin_id)`
runs the test once per discovered plugin. The `ids` callable controls how
each parameter shows up in test output — without it, you'd see `test_x[0]`,
`test_x[1]`. With it, you see `test_x[memory]`, `test_x[email]`. Much nicer.

The discover function runs at COLLECTION time, not test time. So if you add
a new plugin, you have to re-collect (just rerun pytest) — but you do not
have to write a new test.

Run with: pytest tests/test_plugin_manifests.py -v
"""
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def discover_plugins():
    """Yield (plugin_dir,) for every plugin under plugins/ and user/plugins/.

    Skips:
      - hidden dirs (start with .)
      - dirs without a plugin.json
      - core-ui dirs (frontend-only, no python manifest contract)
    """
    found = []
    for base in [PROJECT_ROOT / "plugins", PROJECT_ROOT / "user" / "plugins"]:
        if not base.exists():
            continue
        for child in sorted(base.iterdir()):
            if not child.is_dir() or child.name.startswith('.'):
                continue
            if (child / "plugin.json").exists():
                found.append(child)
    return found


def plugin_id(p):
    """Friendly test ID — `plugins/memory` or `user/plugins/weather`."""
    return str(p.relative_to(PROJECT_ROOT))


PLUGINS = discover_plugins()


@pytest.mark.parametrize("plugin_dir", PLUGINS, ids=[plugin_id(p) for p in PLUGINS])
def test_manifest_is_valid_json_with_required_fields(plugin_dir):
    """plugin.json must parse and contain the fields the loader actually uses."""
    manifest_path = plugin_dir / "plugin.json"
    raw = manifest_path.read_text(encoding='utf-8')

    # Must parse — JSON syntax errors are unforgivable
    manifest = json.loads(raw)
    assert isinstance(manifest, dict), f"{plugin_dir.name}: manifest is not a dict"

    # `name` is the only field _validate_manifest enforces, but every plugin
    # in this repo declares more — pin them so we don't drift.
    assert "name" in manifest, f"{plugin_dir.name}: missing 'name'"
    assert isinstance(manifest["name"], str) and manifest["name"], \
        f"{plugin_dir.name}: 'name' must be a non-empty string"

    # Recommended but not required by the loader. We assert anyway because
    # every plugin in tree has them and missing them indicates copy-paste rot.
    for field in ("version", "description", "author"):
        assert field in manifest, f"{plugin_dir.name}: missing '{field}'"


@pytest.mark.parametrize("plugin_dir", PLUGINS, ids=[plugin_id(p) for p in PLUGINS])
def test_manifest_capabilities_shape(plugin_dir):
    """If a manifest declares capabilities, each section must be the right shape."""
    manifest = json.loads((plugin_dir / "plugin.json").read_text(encoding='utf-8'))
    caps = manifest.get("capabilities", {})
    assert isinstance(caps, dict), f"{plugin_dir.name}: capabilities must be a dict"

    # tools: list of file paths (strings) — each must point to an existing .py file
    tools = caps.get("tools", [])
    assert isinstance(tools, list), f"{plugin_dir.name}: capabilities.tools must be a list"
    for t in tools:
        assert isinstance(t, str), f"{plugin_dir.name}: tool entry must be a string path, got {t!r}"
        tool_path = plugin_dir / t
        assert tool_path.exists(), f"{plugin_dir.name}: declared tool {t} does not exist"
        assert tool_path.suffix == ".py", f"{plugin_dir.name}: tool {t} must be a .py file"

    # hooks: dict mapping event_name -> handler file path
    # (the loader does `for hook_name, handler_path in hooks.items()`)
    hooks = caps.get("hooks", {})
    assert isinstance(hooks, dict), \
        f"{plugin_dir.name}: capabilities.hooks must be a dict (event_name -> handler path)"
    for hook_name, handler_path in hooks.items():
        assert isinstance(hook_name, str) and hook_name, \
            f"{plugin_dir.name}: hook event name must be a non-empty string"
        assert isinstance(handler_path, str) and handler_path, \
            f"{plugin_dir.name}: hook handler must be a non-empty string path"
        # Handler can be `path.py` or `path.py:func_name` — strip the func part
        file_part = handler_path.split(':', 1)[0]
        full = plugin_dir / file_part
        assert full.exists(), f"{plugin_dir.name}: hook handler file {file_part} does not exist"


@pytest.mark.parametrize("plugin_dir", PLUGINS, ids=[plugin_id(p) for p in PLUGINS])
def test_manifest_scope_declarations_well_formed(plugin_dir):
    """Every scope declaration must be a dict with at least 'key' and 'endpoint'.

    THIS IS THE PHASE 4 REGRESSION GUARD. Chaos Scout #1 found that the
    /api/init endpoint blew up on a malformed scope (string instead of dict,
    missing key, etc.). The defensive .get() in core/routes/chat.py is the fix;
    THIS test makes sure no plugin in tree ships a manifest that would have
    triggered the bug in the first place.
    """
    manifest = json.loads((plugin_dir / "plugin.json").read_text(encoding='utf-8'))
    scopes = manifest.get("capabilities", {}).get("scopes", [])
    assert isinstance(scopes, list), f"{plugin_dir.name}: capabilities.scopes must be a list"

    for scope_def in scopes:
        assert isinstance(scope_def, dict), \
            f"{plugin_dir.name}: scope entry must be a dict, got {type(scope_def).__name__}: {scope_def!r}"
        assert scope_def.get("key"), \
            f"{plugin_dir.name}: scope missing 'key': {scope_def!r}"
        assert scope_def.get("endpoint"), \
            f"{plugin_dir.name}: scope missing 'endpoint': {scope_def!r}"
        # key must be a valid Python identifier — register_plugin_scope rejects others
        assert scope_def["key"].isidentifier(), \
            f"{plugin_dir.name}: scope key {scope_def['key']!r} is not a valid identifier"
