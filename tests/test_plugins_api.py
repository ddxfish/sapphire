#!/usr/bin/env python3
"""
Test script for plugins_api.py
Run from sapphire root: python tests/test_plugins_api.py
"""

import os
import sys
import json
import shutil

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from interfaces.web.plugins_api import (
    load_static_plugins,
    load_user_plugins,
    save_user_plugins,
    get_merged_plugins,
    load_plugin_settings,
    save_plugin_settings,
    ensure_user_dirs,
    USER_WEBUI_DIR,
    USER_PLUGINS_JSON,
    USER_PLUGIN_SETTINGS_DIR,
    LOCKED_PLUGINS
)

def test_ensure_dirs():
    """Test directory creation."""
    ensure_user_dirs()
    assert os.path.isdir(USER_WEBUI_DIR), "user/webui should exist"
    assert os.path.isdir(USER_PLUGIN_SETTINGS_DIR), "user/webui/plugins should exist"
    print("✓ ensure_user_dirs")

def test_load_static():
    """Test loading static plugins.json."""
    static = load_static_plugins()
    assert "enabled" in static, "Static should have enabled list"
    assert "plugins" in static, "Static should have plugins dict"
    assert len(static["plugins"]) > 0, "Should have some plugins"
    print(f"✓ load_static_plugins ({len(static['plugins'])} plugins)")

def test_merge_logic():
    """Test merge of static + user config."""
    # First, no user config
    merged = get_merged_plugins()
    static = load_static_plugins()
    
    # Should match static when no user override
    if not os.path.exists(USER_PLUGINS_JSON):
        assert merged == static, "Should match static when no user override"
        print("✓ get_merged_plugins (no user override)")
    
    # Create a user override
    user_override = {"enabled": ["prompt-manager", "settings-modal", "plugins-modal"]}
    save_user_plugins(user_override)
    
    merged = get_merged_plugins()
    assert "prompt-manager" in merged["enabled"], "Should include user-enabled plugin"
    # Locked plugins should always be present
    for locked in LOCKED_PLUGINS:
        assert locked in merged["enabled"], f"Locked plugin {locked} should always be enabled"
    print("✓ get_merged_plugins (with user override)")

def test_plugin_settings():
    """Test plugin-specific settings CRUD."""
    test_plugin = "test-plugin"
    test_settings = {
        "api_url": "http://localhost:5153",
        "negative_prompt": "ugly, blurry",
        "defaults": {"width": 1024, "height": 1024}
    }
    
    # Save settings
    assert save_plugin_settings(test_plugin, test_settings), "Should save settings"
    
    # Load settings
    loaded = load_plugin_settings(test_plugin)
    assert loaded == test_settings, "Loaded should match saved"
    print("✓ save/load_plugin_settings")
    
    # Verify file exists
    settings_file = os.path.join(USER_PLUGIN_SETTINGS_DIR, f"{test_plugin}.json")
    assert os.path.exists(settings_file), "Settings file should exist"
    
    # Cat the file
    print(f"\n--- {settings_file} ---")
    with open(settings_file) as f:
        print(f.read())
    print("---")
    
    # Clean up test file
    os.remove(settings_file)
    print("✓ cleanup test settings file")

def cleanup():
    """Remove test artifacts."""
    if os.path.exists(USER_PLUGINS_JSON):
        os.remove(USER_PLUGINS_JSON)
        print("✓ cleanup user plugins.json")

def main():
    print("\n=== Plugin API Tests ===\n")
    
    try:
        test_ensure_dirs()
        test_load_static()
        test_merge_logic()
        test_plugin_settings()
        print("\n✓ All tests passed!\n")
    except AssertionError as e:
        print(f"\n✗ Test failed: {e}\n")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Error: {e}\n")
        sys.exit(1)
    finally:
        cleanup()  # Always cleanup, even on failure

if __name__ == "__main__":
    main()