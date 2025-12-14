"""Unit tests for core/settings_manager.py"""
import pytest
import json
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestSettingsFlattening:
    """Test dict flattening logic."""
    
    def test_flatten_simple_nested(self, tmp_path, settings_defaults):
        """Nested dicts should flatten to top-level keys."""
        from core.settings_manager import SettingsManager
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr.BASE_DIR = tmp_path
            
            # Test flatten
            flat = mgr._flatten_dict(settings_defaults)
        
        assert "DEFAULT_USERNAME" in flat
        assert flat["DEFAULT_USERNAME"] == "TestUser"
        assert "MODULES_ENABLED" in flat
        assert "identity" not in flat  # Category keys removed
    
    def test_flatten_preserves_config_objects(self, tmp_path, settings_defaults):
        """Config objects like LLM_PRIMARY should NOT be flattened."""
        from core.settings_manager import SettingsManager
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr.BASE_DIR = tmp_path
            
            flat = mgr._flatten_dict(settings_defaults)
        
        # LLM_PRIMARY should remain as dict
        assert "LLM_PRIMARY" in flat
        assert isinstance(flat["LLM_PRIMARY"], dict)
        assert flat["LLM_PRIMARY"]["base_url"] == "http://test:1234"
    
    def test_flatten_skips_comments(self):
        """Keys starting with _ should be skipped."""
        from core.settings_manager import SettingsManager
        
        data = {
            "_comment": "This should be skipped",
            "identity": {
                "NAME": "Test"
            }
        }
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr.BASE_DIR = Path("/tmp")
            flat = mgr._flatten_dict(data)
        
        assert "_comment" not in flat
        assert "NAME" in flat


class TestSettingsGetSet:
    """Test get/set operations."""
    
    def test_get_returns_value(self):
        """get() should return config value."""
        from core.settings_manager import SettingsManager
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr._config = {"TEST_KEY": "test_value"}
            
            assert mgr.get("TEST_KEY") == "test_value"
    
    def test_get_returns_default(self):
        """get() should return default if key missing."""
        from core.settings_manager import SettingsManager
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr._config = {}
            
            assert mgr.get("MISSING", "fallback") == "fallback"
    
    def test_set_without_persist(self):
        """set() without persist should only update memory."""
        from core.settings_manager import SettingsManager
        import threading
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr._config = {}
            mgr._user = {}
            mgr._lock = threading.Lock()
            mgr._reload_callbacks = {}
            mgr.save = MagicMock()
            
            mgr.set("NEW_KEY", "new_value", persist=False)
            
            assert mgr._config["NEW_KEY"] == "new_value"
            assert "NEW_KEY" not in mgr._user
            mgr.save.assert_not_called()
    
    def test_set_with_persist(self):
        """set() with persist should update user dict and save."""
        from core.settings_manager import SettingsManager
        import threading
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr._config = {}
            mgr._user = {}
            mgr._lock = threading.Lock()
            mgr._reload_callbacks = {}
            mgr.save = MagicMock()
            
            mgr.set("NEW_KEY", "new_value", persist=True)
            
            assert mgr._config["NEW_KEY"] == "new_value"
            assert mgr._user["NEW_KEY"] == "new_value"
            mgr.save.assert_called_once()


class TestSettingsTiers:
    """Test tier validation."""
    
    def test_hot_reload_tier(self):
        """Hot-reload settings should return 'hot'."""
        from core.settings_manager import SettingsManager
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            
            assert mgr.validate_tier("DEFAULT_USERNAME") == "hot"
            assert mgr.validate_tier("TTS_VOICE_NAME") == "hot"
            assert mgr.validate_tier("TTS_SPEED") == "hot"
    
    def test_component_tier(self):
        """Component-reload settings should return 'component'."""
        from core.settings_manager import SettingsManager
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            
            assert mgr.validate_tier("TTS_ENABLED") == "component"
            assert mgr.validate_tier("STT_ENABLED") == "component"
    
    def test_restart_tier(self):
        """Unknown settings should return 'restart'."""
        from core.settings_manager import SettingsManager
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            
            assert mgr.validate_tier("SOME_RANDOM_SETTING") == "restart"
            assert mgr.validate_tier("SOCKS_HOST") == "restart"


class TestUserOverrides:
    """Test user override management."""
    
    def test_remove_user_override(self):
        """remove_user_override should delete from _user and remerge."""
        from core.settings_manager import SettingsManager
        import threading
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr._defaults = {"KEY": "default_value"}
            mgr._user = {"KEY": "user_value"}
            mgr._config = {"KEY": "user_value"}
            mgr._lock = threading.Lock()
            mgr._remove_key_from_file = MagicMock()
            mgr._merge_settings = lambda: setattr(mgr, '_config', {**mgr._defaults, **mgr._user})
            
            result = mgr.remove_user_override("KEY")
            
            assert result is True
            assert "KEY" not in mgr._user
            assert mgr._config["KEY"] == "default_value"
    
    def test_remove_nonexistent_override(self):
        """remove_user_override should return False if no override exists."""
        from core.settings_manager import SettingsManager
        import threading
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr._user = {}
            mgr._lock = threading.Lock()
            
            result = mgr.remove_user_override("NONEXISTENT")
            
            assert result is False