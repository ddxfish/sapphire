"""
Tests for core/plugin_loader.py — Plugin discovery and loading.

Run with: pytest tests/test_plugin_loader.py -v
"""
import pytest
import json
import sys
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.hooks import HookRunner, HookEvent
from core.plugin_loader import PluginLoader, PluginState


@pytest.fixture
def temp_dirs():
    """Create temporary plugin and state directories."""
    base = Path(tempfile.mkdtemp())
    plugins_dir = base / "plugins"
    user_plugins_dir = base / "user_plugins"
    state_dir = base / "plugin_state"
    plugins_json = base / "plugins.json"
    plugins_dir.mkdir()
    user_plugins_dir.mkdir()
    state_dir.mkdir()
    yield {
        "base": base,
        "plugins": plugins_dir,
        "user_plugins": user_plugins_dir,
        "state": state_dir,
        "plugins_json": plugins_json,
    }
    shutil.rmtree(base, ignore_errors=True)


@pytest.fixture
def runner():
    """Fresh HookRunner for each test."""
    return HookRunner()


def _make_plugin(plugins_dir, name, manifest, hooks_code=None):
    """Helper: create a plugin directory with manifest and optional hook code."""
    plugin_dir = plugins_dir / name
    plugin_dir.mkdir(exist_ok=True)
    (plugin_dir / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    if hooks_code:
        hooks_dir = plugin_dir / "hooks"
        hooks_dir.mkdir(exist_ok=True)
        for filename, code in hooks_code.items():
            (hooks_dir / filename).write_text(code, encoding="utf-8")
    return plugin_dir


# =============================================================================
# PluginState Tests
# =============================================================================

class TestPluginState:
    def test_save_and_get(self, temp_dirs):
        with patch("core.plugin_loader.PLUGIN_STATE_DIR", temp_dirs["state"]):
            state = PluginState("test_plugin")
            state.save("key1", "value1")
            assert state.get("key1") == "value1"

    def test_get_default(self, temp_dirs):
        with patch("core.plugin_loader.PLUGIN_STATE_DIR", temp_dirs["state"]):
            state = PluginState("test_plugin")
            assert state.get("missing", "default") == "default"

    def test_delete(self, temp_dirs):
        with patch("core.plugin_loader.PLUGIN_STATE_DIR", temp_dirs["state"]):
            state = PluginState("test_plugin")
            state.save("key1", "value1")
            state.delete("key1")
            assert state.get("key1") is None

    def test_clear(self, temp_dirs):
        with patch("core.plugin_loader.PLUGIN_STATE_DIR", temp_dirs["state"]):
            state = PluginState("test_plugin")
            state.save("a", 1)
            state.save("b", 2)
            state.clear()
            assert state.all() == {}

    def test_persists_to_disk(self, temp_dirs):
        with patch("core.plugin_loader.PLUGIN_STATE_DIR", temp_dirs["state"]):
            state1 = PluginState("test_plugin")
            state1.save("persisted", True)
            # New instance reads from disk
            state2 = PluginState("test_plugin")
            assert state2.get("persisted") is True

    def test_complex_values(self, temp_dirs):
        with patch("core.plugin_loader.PLUGIN_STATE_DIR", temp_dirs["state"]):
            state = PluginState("test_plugin")
            state.save("nested", {"list": [1, 2, 3], "dict": {"a": "b"}})
            result = state.get("nested")
            assert result["list"] == [1, 2, 3]
            assert result["dict"]["a"] == "b"


# =============================================================================
# Plugin Discovery Tests
# =============================================================================

class TestPluginDiscovery:
    def test_scan_finds_system_plugins(self, temp_dirs, runner):
        _make_plugin(temp_dirs["plugins"], "test-plugin", {
            "name": "test-plugin",
            "version": "1.0.0",
            "description": "Test plugin"
        })
        # Enable it
        temp_dirs["plugins_json"].write_text(
            json.dumps({"enabled": ["test-plugin"]}), encoding="utf-8"
        )
        loader = PluginLoader()
        with patch("core.plugin_loader.SYSTEM_PLUGINS_DIR", temp_dirs["plugins"]), \
             patch("core.plugin_loader.USER_PLUGINS_DIR", temp_dirs["user_plugins"]), \
             patch("core.plugin_loader.USER_PLUGINS_JSON", temp_dirs["plugins_json"]), \
             patch("core.plugin_loader.hook_runner", runner):
            loader.scan()
            assert "test-plugin" in loader.get_plugin_names()

    def test_scan_finds_user_plugins(self, temp_dirs, runner):
        _make_plugin(temp_dirs["user_plugins"], "user-plugin", {
            "name": "user-plugin",
            "version": "1.0.0",
            "description": "User plugin"
        })
        temp_dirs["plugins_json"].write_text(
            json.dumps({"enabled": ["user-plugin"]}), encoding="utf-8"
        )
        loader = PluginLoader()
        with patch("core.plugin_loader.SYSTEM_PLUGINS_DIR", temp_dirs["plugins"]), \
             patch("core.plugin_loader.USER_PLUGINS_DIR", temp_dirs["user_plugins"]), \
             patch("core.plugin_loader.USER_PLUGINS_JSON", temp_dirs["plugins_json"]), \
             patch("core.plugin_loader.hook_runner", runner):
            loader.scan()
            info = loader.get_plugin_info("user-plugin")
            assert info is not None
            assert info["band"] == "user"

    def test_scan_skips_dirs_without_manifest(self, temp_dirs, runner):
        (temp_dirs["plugins"] / "no-manifest").mkdir()
        loader = PluginLoader()
        with patch("core.plugin_loader.SYSTEM_PLUGINS_DIR", temp_dirs["plugins"]), \
             patch("core.plugin_loader.USER_PLUGINS_DIR", temp_dirs["user_plugins"]), \
             patch("core.plugin_loader.USER_PLUGINS_JSON", temp_dirs["plugins_json"]), \
             patch("core.plugin_loader.hook_runner", runner):
            loader.scan()
            assert len(loader.get_plugin_names()) == 0

    def test_scan_skips_bad_manifest(self, temp_dirs, runner):
        bad_dir = temp_dirs["plugins"] / "bad-plugin"
        bad_dir.mkdir()
        (bad_dir / "plugin.json").write_text("not json!!!", encoding="utf-8")
        loader = PluginLoader()
        with patch("core.plugin_loader.SYSTEM_PLUGINS_DIR", temp_dirs["plugins"]), \
             patch("core.plugin_loader.USER_PLUGINS_DIR", temp_dirs["user_plugins"]), \
             patch("core.plugin_loader.USER_PLUGINS_JSON", temp_dirs["plugins_json"]), \
             patch("core.plugin_loader.hook_runner", runner):
            loader.scan()
            assert len(loader.get_plugin_names()) == 0

    def test_disabled_plugin_found_but_not_loaded(self, temp_dirs, runner):
        _make_plugin(temp_dirs["plugins"], "disabled-plugin", {
            "name": "disabled-plugin",
            "version": "1.0.0"
        })
        temp_dirs["plugins_json"].write_text(
            json.dumps({"enabled": []}), encoding="utf-8"
        )
        loader = PluginLoader()
        with patch("core.plugin_loader.SYSTEM_PLUGINS_DIR", temp_dirs["plugins"]), \
             patch("core.plugin_loader.USER_PLUGINS_DIR", temp_dirs["user_plugins"]), \
             patch("core.plugin_loader.USER_PLUGINS_JSON", temp_dirs["plugins_json"]), \
             patch("core.plugin_loader.hook_runner", runner):
            loader.scan()
            assert "disabled-plugin" in loader.get_plugin_names()
            assert "disabled-plugin" not in loader.get_loaded_plugins()

    def test_missing_plugins_dir_no_error(self, temp_dirs, runner):
        missing = temp_dirs["base"] / "nonexistent"
        loader = PluginLoader()
        with patch("core.plugin_loader.SYSTEM_PLUGINS_DIR", missing), \
             patch("core.plugin_loader.USER_PLUGINS_DIR", temp_dirs["user_plugins"]), \
             patch("core.plugin_loader.USER_PLUGINS_JSON", temp_dirs["plugins_json"]), \
             patch("core.plugin_loader.hook_runner", runner):
            loader.scan()  # should not raise
            assert len(loader.get_plugin_names()) == 0


# =============================================================================
# Hook Loading Tests
# =============================================================================

class TestHookLoading:
    def test_loads_hook_handler(self, temp_dirs, runner):
        _make_plugin(temp_dirs["plugins"], "hook-test", {
            "name": "hook-test",
            "version": "1.0.0",
            "capabilities": {
                "hooks": {
                    "pre_chat": "hooks/intercept.py"
                }
            }
        }, hooks_code={
            "intercept.py": "def pre_chat(event): event.metadata['reached'] = True"
        })
        temp_dirs["plugins_json"].write_text(
            json.dumps({"enabled": ["hook-test"]}), encoding="utf-8"
        )
        loader = PluginLoader()
        with patch("core.plugin_loader.SYSTEM_PLUGINS_DIR", temp_dirs["plugins"]), \
             patch("core.plugin_loader.USER_PLUGINS_DIR", temp_dirs["user_plugins"]), \
             patch("core.plugin_loader.USER_PLUGINS_JSON", temp_dirs["plugins_json"]), \
             patch("core.plugin_loader.hook_runner", runner):
            loader.scan()
            event = HookEvent()
            runner.fire("pre_chat", event)
            assert event.metadata.get("reached") is True

    def test_voice_command_registers_as_pre_chat(self, temp_dirs, runner):
        _make_plugin(temp_dirs["plugins"], "stop", {
            "name": "stop",
            "version": "1.0.0",
            "priority": 1,
            "capabilities": {
                "voice_commands": [{
                    "triggers": ["stop", "halt"],
                    "match": "exact",
                    "bypass_llm": True,
                    "handler": "hooks/stop.py"
                }]
            }
        }, hooks_code={
            "stop.py": (
                "def pre_chat(event):\n"
                "    event.skip_llm = True\n"
                "    event.response = 'Stopped.'\n"
                "    event.stop_propagation = True\n"
            )
        })
        temp_dirs["plugins_json"].write_text(
            json.dumps({"enabled": ["stop"]}), encoding="utf-8"
        )
        loader = PluginLoader()
        with patch("core.plugin_loader.SYSTEM_PLUGINS_DIR", temp_dirs["plugins"]), \
             patch("core.plugin_loader.USER_PLUGINS_DIR", temp_dirs["user_plugins"]), \
             patch("core.plugin_loader.USER_PLUGINS_JSON", temp_dirs["plugins_json"]), \
             patch("core.plugin_loader.hook_runner", runner):
            loader.scan()

            # "stop" should trigger
            event = HookEvent(input="stop")
            runner.fire("pre_chat", event)
            assert event.skip_llm is True
            assert event.response == "Stopped."

            # "hello" should not trigger
            event2 = HookEvent(input="hello")
            runner.fire("pre_chat", event2)
            assert event2.skip_llm is False

    def test_missing_handler_file_no_crash(self, temp_dirs, runner):
        _make_plugin(temp_dirs["plugins"], "bad-handler", {
            "name": "bad-handler",
            "version": "1.0.0",
            "capabilities": {
                "hooks": {
                    "pre_chat": "hooks/nonexistent.py"
                }
            }
        })
        temp_dirs["plugins_json"].write_text(
            json.dumps({"enabled": ["bad-handler"]}), encoding="utf-8"
        )
        loader = PluginLoader()
        with patch("core.plugin_loader.SYSTEM_PLUGINS_DIR", temp_dirs["plugins"]), \
             patch("core.plugin_loader.USER_PLUGINS_DIR", temp_dirs["user_plugins"]), \
             patch("core.plugin_loader.USER_PLUGINS_JSON", temp_dirs["plugins_json"]), \
             patch("core.plugin_loader.hook_runner", runner):
            loader.scan()  # should not raise
            assert not runner.has_handlers("pre_chat")

    def test_broken_handler_code_no_crash(self, temp_dirs, runner):
        _make_plugin(temp_dirs["plugins"], "broken", {
            "name": "broken",
            "version": "1.0.0",
            "capabilities": {
                "hooks": {
                    "pre_chat": "hooks/broken.py"
                }
            }
        }, hooks_code={
            "broken.py": "this is not valid python!!!"
        })
        temp_dirs["plugins_json"].write_text(
            json.dumps({"enabled": ["broken"]}), encoding="utf-8"
        )
        loader = PluginLoader()
        with patch("core.plugin_loader.SYSTEM_PLUGINS_DIR", temp_dirs["plugins"]), \
             patch("core.plugin_loader.USER_PLUGINS_DIR", temp_dirs["user_plugins"]), \
             patch("core.plugin_loader.USER_PLUGINS_JSON", temp_dirs["plugins_json"]), \
             patch("core.plugin_loader.hook_runner", runner):
            loader.scan()  # should not raise
            assert not runner.has_handlers("pre_chat")


# =============================================================================
# Unload / Reload Tests
# =============================================================================

class TestUnloadReload:
    def test_unload_removes_hooks(self, temp_dirs, runner):
        _make_plugin(temp_dirs["plugins"], "unload-test", {
            "name": "unload-test",
            "version": "1.0.0",
            "capabilities": {
                "hooks": {
                    "pre_chat": "hooks/test.py"
                }
            }
        }, hooks_code={
            "test.py": "def pre_chat(event): event.metadata['active'] = True"
        })
        temp_dirs["plugins_json"].write_text(
            json.dumps({"enabled": ["unload-test"]}), encoding="utf-8"
        )
        loader = PluginLoader()
        with patch("core.plugin_loader.SYSTEM_PLUGINS_DIR", temp_dirs["plugins"]), \
             patch("core.plugin_loader.USER_PLUGINS_DIR", temp_dirs["user_plugins"]), \
             patch("core.plugin_loader.USER_PLUGINS_JSON", temp_dirs["plugins_json"]), \
             patch("core.plugin_loader.hook_runner", runner):
            loader.scan()
            assert runner.has_handlers("pre_chat")

            loader.unload_plugin("unload-test")
            assert not runner.has_handlers("pre_chat")

    def test_reload_refreshes_handler(self, temp_dirs, runner):
        _make_plugin(temp_dirs["plugins"], "reload-test", {
            "name": "reload-test",
            "version": "1.0.0",
            "capabilities": {
                "hooks": {
                    "pre_chat": "hooks/test.py"
                }
            }
        }, hooks_code={
            "test.py": "def pre_chat(event): event.metadata['version'] = 1"
        })
        temp_dirs["plugins_json"].write_text(
            json.dumps({"enabled": ["reload-test"]}), encoding="utf-8"
        )
        loader = PluginLoader()
        with patch("core.plugin_loader.SYSTEM_PLUGINS_DIR", temp_dirs["plugins"]), \
             patch("core.plugin_loader.USER_PLUGINS_DIR", temp_dirs["user_plugins"]), \
             patch("core.plugin_loader.USER_PLUGINS_JSON", temp_dirs["plugins_json"]), \
             patch("core.plugin_loader.hook_runner", runner):
            loader.scan()

            # Verify v1
            event = HookEvent()
            runner.fire("pre_chat", event)
            assert event.metadata["version"] == 1

            # Update handler code
            hooks_dir = temp_dirs["plugins"] / "reload-test" / "hooks"
            (hooks_dir / "test.py").write_text(
                "def pre_chat(event): event.metadata['version'] = 2"
            )

            # Reload
            loader.reload_plugin("reload-test")

            event2 = HookEvent()
            runner.fire("pre_chat", event2)
            assert event2.metadata["version"] == 2


# =============================================================================
# Priority Band Tests
# =============================================================================

class TestPriorityBands:
    def test_system_before_user(self, temp_dirs, runner):
        """System plugins should always fire before user plugins."""
        order = []
        _make_plugin(temp_dirs["user_plugins"], "user-hook", {
            "name": "user-hook",
            "version": "1.0.0",
            "priority": 1,
            "capabilities": {
                "hooks": {"pre_chat": "hooks/h.py"}
            }
        }, hooks_code={
            "h.py": "def pre_chat(event): event.metadata.setdefault('order', []).append('user')"
        })
        _make_plugin(temp_dirs["plugins"], "sys-hook", {
            "name": "sys-hook",
            "version": "1.0.0",
            "priority": 99,
            "capabilities": {
                "hooks": {"pre_chat": "hooks/h.py"}
            }
        }, hooks_code={
            "h.py": "def pre_chat(event): event.metadata.setdefault('order', []).append('system')"
        })
        temp_dirs["plugins_json"].write_text(
            json.dumps({"enabled": ["sys-hook", "user-hook"]}), encoding="utf-8"
        )
        loader = PluginLoader()
        with patch("core.plugin_loader.SYSTEM_PLUGINS_DIR", temp_dirs["plugins"]), \
             patch("core.plugin_loader.USER_PLUGINS_DIR", temp_dirs["user_plugins"]), \
             patch("core.plugin_loader.USER_PLUGINS_JSON", temp_dirs["plugins_json"]), \
             patch("core.plugin_loader.hook_runner", runner):
            loader.scan()
            event = HookEvent()
            runner.fire("pre_chat", event)
            # System (priority 99) should fire before user (priority 1+100=101)
            assert event.metadata["order"] == ["system", "user"]


# =============================================================================
# Query Method Tests
# =============================================================================

class TestQueryMethods:
    def test_get_all_plugin_info(self, temp_dirs, runner):
        _make_plugin(temp_dirs["plugins"], "alpha", {"name": "alpha", "version": "1.0.0"})
        _make_plugin(temp_dirs["plugins"], "beta", {"name": "beta", "version": "2.0.0"})
        temp_dirs["plugins_json"].write_text(
            json.dumps({"enabled": ["alpha"]}), encoding="utf-8"
        )
        loader = PluginLoader()
        with patch("core.plugin_loader.SYSTEM_PLUGINS_DIR", temp_dirs["plugins"]), \
             patch("core.plugin_loader.USER_PLUGINS_DIR", temp_dirs["user_plugins"]), \
             patch("core.plugin_loader.USER_PLUGINS_JSON", temp_dirs["plugins_json"]), \
             patch("core.plugin_loader.hook_runner", runner):
            loader.scan()
            all_info = loader.get_all_plugin_info()
            assert len(all_info) == 2
            names = {p["name"] for p in all_info}
            assert names == {"alpha", "beta"}
            alpha = [p for p in all_info if p["name"] == "alpha"][0]
            assert alpha["enabled"] is True
            assert alpha["loaded"] is True
            beta = [p for p in all_info if p["name"] == "beta"][0]
            assert beta["enabled"] is False
            assert beta["loaded"] is False

    def test_get_plugin_info_missing(self, temp_dirs, runner):
        loader = PluginLoader()
        assert loader.get_plugin_info("nonexistent") is None


# =============================================================================
# Plugin Tool Loading Tests (Phase 3E)
# =============================================================================

def _make_tool_file(plugin_dir, rel_path, tools_list, execute_code="return 'ok', True"):
    """Helper: create a tool .py file inside a plugin directory."""
    full_path = plugin_dir / rel_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    tool_defs = repr(tools_list)
    full_path.write_text(
        f"TOOLS = {tool_defs}\n\n"
        f"def execute(function_name, arguments, config):\n"
        f"    {execute_code}\n",
        encoding="utf-8"
    )
    return full_path


def _make_mock_fm():
    """Create a FunctionManager-like object with real register/unregister methods."""
    from core.chat.function_manager import FunctionManager
    fm = object.__new__(FunctionManager)
    fm.function_modules = {}
    fm.all_possible_tools = []
    fm.execution_map = {}
    fm._enabled_tools = []
    fm._network_functions = set()
    fm._is_local_map = {}
    fm._function_module_map = {}
    fm._mode_filters = {}
    return fm


SAMPLE_TOOL = {
    "type": "function",
    "function": {
        "name": "test_tool",
        "description": "A test tool",
        "parameters": {"type": "object", "properties": {}}
    }
}

SAMPLE_TOOL_2 = {
    "type": "function",
    "function": {
        "name": "another_tool",
        "description": "Another test tool",
        "parameters": {"type": "object", "properties": {}}
    }
}


class TestPluginToolRegistration:
    def test_register_plugin_tools(self, temp_dirs):
        fm = _make_mock_fm()
        plugin_dir = _make_plugin(temp_dirs["plugins"], "tool-plugin", {
            "name": "tool-plugin", "version": "1.0.0",
            "capabilities": {"tools": ["tools/my_tool.py"]}
        })
        _make_tool_file(plugin_dir, "tools/my_tool.py", [SAMPLE_TOOL])

        fm.register_plugin_tools("tool-plugin", plugin_dir, ["tools/my_tool.py"])

        assert "test_tool" in fm.execution_map
        tool_names = [t['function']['name'] for t in fm.all_possible_tools]
        assert "test_tool" in tool_names

    def test_register_multiple_tools(self, temp_dirs):
        fm = _make_mock_fm()
        plugin_dir = _make_plugin(temp_dirs["plugins"], "multi-tool", {
            "name": "multi-tool", "version": "1.0.0",
            "capabilities": {"tools": ["tools/combo.py"]}
        })
        _make_tool_file(plugin_dir, "tools/combo.py", [SAMPLE_TOOL, SAMPLE_TOOL_2])

        fm.register_plugin_tools("multi-tool", plugin_dir, ["tools/combo.py"])

        assert "test_tool" in fm.execution_map
        assert "another_tool" in fm.execution_map
        assert len(fm.all_possible_tools) == 2

    def test_unregister_plugin_tools(self, temp_dirs):
        fm = _make_mock_fm()
        plugin_dir = _make_plugin(temp_dirs["plugins"], "unreg-test", {
            "name": "unreg-test", "version": "1.0.0",
            "capabilities": {"tools": ["tools/my_tool.py"]}
        })
        _make_tool_file(plugin_dir, "tools/my_tool.py", [SAMPLE_TOOL])

        fm.register_plugin_tools("unreg-test", plugin_dir, ["tools/my_tool.py"])
        assert "test_tool" in fm.execution_map

        fm.unregister_plugin_tools("unreg-test")
        assert "test_tool" not in fm.execution_map
        assert len(fm.all_possible_tools) == 0

    def test_unregister_clears_enabled_tools(self, temp_dirs):
        fm = _make_mock_fm()
        plugin_dir = _make_plugin(temp_dirs["plugins"], "enabled-test", {
            "name": "enabled-test", "version": "1.0.0",
            "capabilities": {"tools": ["tools/my_tool.py"]}
        })
        _make_tool_file(plugin_dir, "tools/my_tool.py", [SAMPLE_TOOL])

        fm.register_plugin_tools("enabled-test", plugin_dir, ["tools/my_tool.py"])
        # Simulate tools being enabled
        fm._enabled_tools = list(fm.all_possible_tools)
        assert len(fm._enabled_tools) == 1

        fm.unregister_plugin_tools("enabled-test")
        assert len(fm._enabled_tools) == 0

    def test_missing_tool_file_no_crash(self, temp_dirs):
        fm = _make_mock_fm()
        plugin_dir = _make_plugin(temp_dirs["plugins"], "missing-tool", {
            "name": "missing-tool", "version": "1.0.0",
            "capabilities": {"tools": ["tools/nonexistent.py"]}
        })

        fm.register_plugin_tools("missing-tool", plugin_dir, ["tools/nonexistent.py"])
        assert len(fm.all_possible_tools) == 0

    def test_broken_tool_file_no_crash(self, temp_dirs):
        fm = _make_mock_fm()
        plugin_dir = _make_plugin(temp_dirs["plugins"], "broken-tool", {
            "name": "broken-tool", "version": "1.0.0",
            "capabilities": {"tools": ["tools/bad.py"]}
        })
        bad_path = plugin_dir / "tools"
        bad_path.mkdir(exist_ok=True)
        (bad_path / "bad.py").write_text("this is not valid python!!!", encoding="utf-8")

        fm.register_plugin_tools("broken-tool", plugin_dir, ["tools/bad.py"])
        assert len(fm.all_possible_tools) == 0

    def test_tool_without_execute_skipped(self, temp_dirs):
        fm = _make_mock_fm()
        plugin_dir = _make_plugin(temp_dirs["plugins"], "no-exec", {
            "name": "no-exec", "version": "1.0.0",
            "capabilities": {"tools": ["tools/no_exec.py"]}
        })
        tools_dir = plugin_dir / "tools"
        tools_dir.mkdir(exist_ok=True)
        (tools_dir / "no_exec.py").write_text(
            f"TOOLS = {json.dumps([SAMPLE_TOOL])}\n# no execute function\n",
            encoding="utf-8"
        )

        fm.register_plugin_tools("no-exec", plugin_dir, ["tools/no_exec.py"])
        assert len(fm.all_possible_tools) == 0

    def test_duplicate_tool_name_skipped(self, temp_dirs):
        fm = _make_mock_fm()
        plugin_dir = _make_plugin(temp_dirs["plugins"], "dup-test", {
            "name": "dup-test", "version": "1.0.0",
            "capabilities": {"tools": ["tools/t1.py", "tools/t2.py"]}
        })
        _make_tool_file(plugin_dir, "tools/t1.py", [SAMPLE_TOOL])
        _make_tool_file(plugin_dir, "tools/t2.py", [SAMPLE_TOOL])  # same name

        fm.register_plugin_tools("dup-test", plugin_dir, ["tools/t1.py", "tools/t2.py"])
        # Should only have one copy
        assert len(fm.all_possible_tools) == 1

    def test_tool_execution_works(self, temp_dirs):
        fm = _make_mock_fm()
        plugin_dir = _make_plugin(temp_dirs["plugins"], "exec-test", {
            "name": "exec-test", "version": "1.0.0",
            "capabilities": {"tools": ["tools/my_tool.py"]}
        })
        _make_tool_file(plugin_dir, "tools/my_tool.py", [SAMPLE_TOOL],
                        execute_code="return f'executed {function_name}', True")

        fm.register_plugin_tools("exec-test", plugin_dir, ["tools/my_tool.py"])
        executor = fm.execution_map.get("test_tool")
        assert executor is not None
        result, success = executor("test_tool", {}, None)
        assert result == "executed test_tool"
        assert success is True

    def test_network_flag_tracked(self, temp_dirs):
        fm = _make_mock_fm()
        net_tool = {
            "type": "function",
            "function": {"name": "net_tool", "description": "needs network",
                         "parameters": {"type": "object", "properties": {}}},
            "network": True
        }
        plugin_dir = _make_plugin(temp_dirs["plugins"], "net-test", {
            "name": "net-test", "version": "1.0.0",
            "capabilities": {"tools": ["tools/net.py"]}
        })
        _make_tool_file(plugin_dir, "tools/net.py", [net_tool])

        fm.register_plugin_tools("net-test", plugin_dir, ["tools/net.py"])
        assert "net_tool" in fm._network_functions


class TestPluginToolIntegration:
    """Test plugin tools through the full scan() pipeline."""

    def test_scan_loads_plugin_tools(self, temp_dirs, runner):
        plugin_dir = _make_plugin(temp_dirs["plugins"], "scan-tool", {
            "name": "scan-tool", "version": "1.0.0",
            "capabilities": {"tools": ["tools/scan_tool.py"]}
        })
        _make_tool_file(plugin_dir, "tools/scan_tool.py", [SAMPLE_TOOL])
        temp_dirs["plugins_json"].write_text(
            json.dumps({"enabled": ["scan-tool"]}), encoding="utf-8"
        )

        fm = _make_mock_fm()
        loader = PluginLoader()
        with patch("core.plugin_loader.SYSTEM_PLUGINS_DIR", temp_dirs["plugins"]), \
             patch("core.plugin_loader.USER_PLUGINS_DIR", temp_dirs["user_plugins"]), \
             patch("core.plugin_loader.USER_PLUGINS_JSON", temp_dirs["plugins_json"]), \
             patch("core.plugin_loader.hook_runner", runner):
            loader.scan(function_manager=fm)

            assert "test_tool" in fm.execution_map
            assert len(fm.all_possible_tools) == 1

    def test_unload_removes_tools(self, temp_dirs, runner):
        plugin_dir = _make_plugin(temp_dirs["plugins"], "unload-tool", {
            "name": "unload-tool", "version": "1.0.0",
            "capabilities": {"tools": ["tools/my_tool.py"]}
        })
        _make_tool_file(plugin_dir, "tools/my_tool.py", [SAMPLE_TOOL])
        temp_dirs["plugins_json"].write_text(
            json.dumps({"enabled": ["unload-tool"]}), encoding="utf-8"
        )

        fm = _make_mock_fm()
        loader = PluginLoader()
        with patch("core.plugin_loader.SYSTEM_PLUGINS_DIR", temp_dirs["plugins"]), \
             patch("core.plugin_loader.USER_PLUGINS_DIR", temp_dirs["user_plugins"]), \
             patch("core.plugin_loader.USER_PLUGINS_JSON", temp_dirs["plugins_json"]), \
             patch("core.plugin_loader.hook_runner", runner):
            loader.scan(function_manager=fm)
            assert "test_tool" in fm.execution_map

            loader.unload_plugin("unload-tool")
            assert "test_tool" not in fm.execution_map
            assert len(fm.all_possible_tools) == 0

    def test_disabled_plugin_tools_not_loaded(self, temp_dirs, runner):
        plugin_dir = _make_plugin(temp_dirs["plugins"], "disabled-tool", {
            "name": "disabled-tool", "version": "1.0.0",
            "capabilities": {"tools": ["tools/my_tool.py"]}
        })
        _make_tool_file(plugin_dir, "tools/my_tool.py", [SAMPLE_TOOL])
        temp_dirs["plugins_json"].write_text(
            json.dumps({"enabled": []}), encoding="utf-8"
        )

        fm = _make_mock_fm()
        loader = PluginLoader()
        with patch("core.plugin_loader.SYSTEM_PLUGINS_DIR", temp_dirs["plugins"]), \
             patch("core.plugin_loader.USER_PLUGINS_DIR", temp_dirs["user_plugins"]), \
             patch("core.plugin_loader.USER_PLUGINS_JSON", temp_dirs["plugins_json"]), \
             patch("core.plugin_loader.hook_runner", runner):
            loader.scan(function_manager=fm)

            assert len(fm.all_possible_tools) == 0

    def test_plugin_with_hooks_and_tools(self, temp_dirs, runner):
        """Plugin can have both hooks and tools."""
        plugin_dir = _make_plugin(temp_dirs["plugins"], "combo", {
            "name": "combo", "version": "1.0.0",
            "capabilities": {
                "hooks": {"pre_chat": "hooks/intercept.py"},
                "tools": ["tools/my_tool.py"]
            }
        }, hooks_code={
            "intercept.py": "def pre_chat(event): event.metadata['hooked'] = True"
        })
        _make_tool_file(plugin_dir, "tools/my_tool.py", [SAMPLE_TOOL])
        temp_dirs["plugins_json"].write_text(
            json.dumps({"enabled": ["combo"]}), encoding="utf-8"
        )

        fm = _make_mock_fm()
        loader = PluginLoader()
        with patch("core.plugin_loader.SYSTEM_PLUGINS_DIR", temp_dirs["plugins"]), \
             patch("core.plugin_loader.USER_PLUGINS_DIR", temp_dirs["user_plugins"]), \
             patch("core.plugin_loader.USER_PLUGINS_JSON", temp_dirs["plugins_json"]), \
             patch("core.plugin_loader.hook_runner", runner):
            loader.scan(function_manager=fm)

            # Hook works
            event = HookEvent()
            runner.fire("pre_chat", event)
            assert event.metadata.get("hooked") is True

            # Tool registered
            assert "test_tool" in fm.execution_map

    def test_no_function_manager_skips_tools(self, temp_dirs, runner):
        """If no function_manager passed, tools capability is ignored (no crash)."""
        plugin_dir = _make_plugin(temp_dirs["plugins"], "no-fm", {
            "name": "no-fm", "version": "1.0.0",
            "capabilities": {"tools": ["tools/my_tool.py"]}
        })
        _make_tool_file(plugin_dir, "tools/my_tool.py", [SAMPLE_TOOL])
        temp_dirs["plugins_json"].write_text(
            json.dumps({"enabled": ["no-fm"]}), encoding="utf-8"
        )

        loader = PluginLoader()
        with patch("core.plugin_loader.SYSTEM_PLUGINS_DIR", temp_dirs["plugins"]), \
             patch("core.plugin_loader.USER_PLUGINS_DIR", temp_dirs["user_plugins"]), \
             patch("core.plugin_loader.USER_PLUGINS_JSON", temp_dirs["plugins_json"]), \
             patch("core.plugin_loader.hook_runner", runner):
            loader.scan()  # no function_manager — should not crash
            assert "no-fm" in loader.get_loaded_plugins()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
