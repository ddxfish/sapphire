"""
Memory plugin integration test (Tier 1 — biggest single-test ROI).

This is the heaviest test in the suite, and the highest-leverage. It loads
the memory plugin through the REAL PluginLoader and FunctionManager (no
mocks of the load path) and asserts the full chain works:

  1. PluginLoader.scan() discovers plugins/memory
  2. The plugin's manifest is parsed and validated
  3. Scope ContextVars (memory/goal/knowledge/people) get registered
  4. Tool files (memory_tools, knowledge_tools, goals_tools) get exec'd
  5. Tools end up in FunctionManager.execution_map
  6. The exec'd modules end up in sys.modules under their canonical names
     (this is the Phase 4 sys.modules idempotency contract — without it,
     a parallel `from plugins.memory.tools import memory_tools` would
     create a SECOND module instance with split DB lock state)
  7. reload_plugin('memory') re-runs the chain cleanly without losing tools

ISOLATION STRATEGY
──────────────────
We don't run the real plugin scan against the full plugins/ tree because
that loads ~30 plugins with their own dependencies. Instead, we:

  1. Create a temp directory `tmp_path/iso_plugins/`
  2. Symlink `plugins/memory` into it as the only entry
  3. Monkey-patch core.plugin_loader.SYSTEM_PLUGINS_DIR to point at the temp
  4. Monkey-patch USER_PLUGINS_DIR to point at an empty temp dir

This gives us the real loader exercising real code, but only against the
memory plugin. Fast and deterministic.

PYTEST PRIMER — monkeypatch
───────────────────────────
The `monkeypatch` fixture is built into pytest. `monkeypatch.setattr(obj,
'attr', new_value)` replaces an attribute and AUTOMATICALLY restores the
original when the test ends — no try/finally needed. It's the cleanest way
to swap in fakes for module-level constants.

Run with: pytest tests/test_memory_plugin_integration.py -v
"""
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def isolated_memory_loader(tmp_path, monkeypatch):
    """Build a PluginLoader that sees ONLY the memory plugin.

    Creates two temp dirs (one for system plugins, one for user plugins),
    symlinks plugins/memory into the system dir, and monkey-patches the
    plugin_loader module constants to use them.

    Yields a fresh (PluginLoader, FunctionManager) pair. Both are isolated
    from any other test — they have their own state and clean up at test end.
    """
    import core.plugin_loader as pl
    from core.plugin_loader import PluginLoader
    from core.chat.function_manager import FunctionManager

    # Build the isolated plugin tree
    iso_system = tmp_path / "iso_system_plugins"
    iso_user = tmp_path / "iso_user_plugins"
    iso_system.mkdir()
    iso_user.mkdir()

    # Symlink memory plugin in. Symlinks resolve transparently for json/exec.
    memory_src = PROJECT_ROOT / "plugins" / "memory"
    (iso_system / "memory").symlink_to(memory_src, target_is_directory=True)

    # Monkey-patch the module-level constants. monkeypatch.setattr restores
    # them automatically when the test ends.
    monkeypatch.setattr(pl, "SYSTEM_PLUGINS_DIR", iso_system)
    monkeypatch.setattr(pl, "USER_PLUGINS_DIR", iso_user)

    # Allow unsigned plugins for the test environment, in case the symlinked
    # memory plugin's signature doesn't validate against its symlinked path.
    # We're testing the load chain, not the signature gate.
    import config
    monkeypatch.setattr(config, "ALLOW_UNSIGNED_PLUGINS", True, raising=False)

    loader = PluginLoader()
    fm = FunctionManager()

    yield loader, fm

    # Best-effort sys.modules cleanup so other tests start clean
    for key in list(sys.modules.keys()):
        if key.startswith("plugins.memory.tools."):
            sys.modules.pop(key, None)


def test_scan_discovers_and_loads_memory_plugin(isolated_memory_loader):
    """The full discover→load chain must succeed for memory plugin."""
    loader, fm = isolated_memory_loader
    loader.scan(function_manager=fm)

    assert "memory" in loader._plugins, "memory plugin not discovered"
    info = loader._plugins["memory"]
    assert info["loaded"], f"memory plugin not loaded: {info.get('verify_msg')}"
    assert info["enabled"], "memory plugin not enabled (default_enabled should be True)"


def test_memory_plugin_registers_expected_tools(isolated_memory_loader):
    """After loading memory, FunctionManager.execution_map must contain the
    memory plugin's tools.

    We check for a specific tool from each of the three tool files instead
    of asserting an exact list — that way if a new memory tool is added the
    test still passes (it's a regression guard, not an exhaustive enumeration).
    """
    loader, fm = isolated_memory_loader
    loader.scan(function_manager=fm)

    # One sentinel tool from each of the three memory plugin tool files
    expected_tools = [
        "save_memory",      # plugins/memory/tools/memory_tools.py
        "search_knowledge", # plugins/memory/tools/knowledge_tools.py
        "create_goal",      # plugins/memory/tools/goals_tools.py
    ]
    for tool_name in expected_tools:
        assert tool_name in fm.execution_map, \
            f"Memory plugin tool '{tool_name}' not registered in execution_map. " \
            f"Got: {sorted(fm.execution_map.keys())[:20]}..."


def test_memory_plugin_installs_canonical_modules_in_sys_modules(isolated_memory_loader):
    """Phase 4 sys.modules idempotency contract.

    After register_plugin_tools runs, the memory plugin's tool modules must
    exist in sys.modules under their canonical names. This is what makes
    `from plugins.memory.tools import memory_tools` from a route handler
    return the SAME module object the loader exec'd — preventing the
    split-state hazard the race scout caught in the post-Phase-4 hunt.
    """
    loader, fm = isolated_memory_loader
    loader.scan(function_manager=fm)

    expected_modules = [
        "plugins.memory.tools.memory_tools",
        "plugins.memory.tools.knowledge_tools",
        "plugins.memory.tools.goals_tools",
    ]
    for mod_name in expected_modules:
        assert mod_name in sys.modules, \
            f"Canonical module {mod_name} not installed in sys.modules — " \
            f"split-state hazard! Phase 4 idempotency fix may be regressed."


def test_memory_plugin_tools_are_callable(isolated_memory_loader):
    """The execution_map entries must be actual callable executors, not stale
    None or wrapper objects."""
    loader, fm = isolated_memory_loader
    loader.scan(function_manager=fm)

    for tool_name in ("save_memory", "search_knowledge", "create_goal"):
        executor = fm.execution_map.get(tool_name)
        assert callable(executor), \
            f"execution_map['{tool_name}'] is not callable: {executor!r}"


def test_reload_memory_plugin_keeps_tools_registered(isolated_memory_loader):
    """Hot-reload safety: reloading the memory plugin must leave its tools
    in execution_map.

    Bug class this catches: an unregister/re-register sequence that drops a
    tool name (due to ordering, async, or sys.modules state corruption).
    Without this test, a future refactor of reload_plugin could silently
    leave the chat without tools after a hot-reload.
    """
    loader, fm = isolated_memory_loader
    loader.scan(function_manager=fm)

    # Snapshot tool count before reload
    tools_before = {k for k in fm.execution_map.keys()
                    if k in ("save_memory", "search_knowledge", "create_goal")}
    assert len(tools_before) == 3, \
        f"Expected all 3 memory tools before reload, got: {tools_before}"

    # Reload
    loader.reload_plugin("memory")

    tools_after = {k for k in fm.execution_map.keys()
                   if k in ("save_memory", "search_knowledge", "create_goal")}
    assert tools_after == tools_before, \
        f"Tools changed across reload. Before: {tools_before}, After: {tools_after}"


def test_reload_memory_plugin_refreshes_sys_modules(isolated_memory_loader):
    """After reload, the canonical sys.modules entries must still be present.

    unregister_plugin_tools purges sys.modules; the subsequent _load_plugin
    re-installs them. If the purge or re-install regresses, this test catches
    it.
    """
    loader, fm = isolated_memory_loader
    loader.scan(function_manager=fm)

    loader.reload_plugin("memory")

    for mod_name in ("plugins.memory.tools.memory_tools",
                     "plugins.memory.tools.knowledge_tools",
                     "plugins.memory.tools.goals_tools"):
        assert mod_name in sys.modules, \
            f"After reload, {mod_name} missing from sys.modules"


def test_unload_memory_plugin_removes_tools(isolated_memory_loader):
    """Unloading must drop the memory plugin's tools from execution_map.

    The inverse of the registration test — the unload path is just as
    important as the load path, and historically this is where leaks happen.
    """
    loader, fm = isolated_memory_loader
    loader.scan(function_manager=fm)
    assert "save_memory" in fm.execution_map  # baseline

    loader.unload_plugin("memory")

    for tool_name in ("save_memory", "search_knowledge", "create_goal"):
        assert tool_name not in fm.execution_map, \
            f"Tool '{tool_name}' still in execution_map after unload"
