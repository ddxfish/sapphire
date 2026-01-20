"""Unit tests for core/settings_manager.py"""
import pytest
import json
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open


class TestPlatformDetection:
    """Test platform detection for cross-platform compatibility."""
    
    def test_is_windows_detection(self):
        """IS_WINDOWS should match sys.platform."""
        from core.settings_manager import IS_WINDOWS
        expected = sys.platform == 'win32'
        assert IS_WINDOWS == expected


class TestSettingsFlattening:
    """Test dict flattening logic."""
    
    def test_flatten_simple_nested(self, tmp_path, settings_defaults):
        """Nested dicts should flatten to top-level keys."""
        from core.settings_manager import SettingsManager
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr.BASE_DIR = tmp_path
            
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


class TestApplyConstruction:
    """Test programmatic construction of settings."""
    
    def test_constructs_stt_server_url(self):
        """Should construct STT_SERVER_URL from host and port."""
        from core.settings_manager import SettingsManager
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr.BASE_DIR = Path("/tmp")
            mgr._defaults = {
                "STT_HOST": "localhost",
                "STT_SERVER_PORT": 8765
            }
            
            mgr._apply_construction()
            
            assert mgr._defaults["STT_SERVER_URL"] == "http://localhost:8765"
    
    def test_adds_base_dir(self):
        """Should add BASE_DIR to defaults."""
        from core.settings_manager import SettingsManager
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr.BASE_DIR = Path("/my/project")
            mgr._defaults = {}
            
            mgr._apply_construction()
            
            assert mgr._defaults["BASE_DIR"] == "/my/project"
    
    def test_applies_linux_audio_devices(self):
        """Should use Linux audio devices on non-Windows."""
        from core.settings_manager import SettingsManager
        
        with patch('core.settings_manager.IS_WINDOWS', False):
            with patch.object(SettingsManager, '__init__', lambda self: None):
                mgr = SettingsManager()
                mgr.BASE_DIR = Path("/tmp")
                mgr._defaults = {
                    "RECORDER_PREFERRED_DEVICES_LINUX": ["pipewire", "pulse"],
                    "RECORDER_PREFERRED_DEVICES_WINDOWS": ["default"]
                }
                
                mgr._apply_construction()
                
                assert mgr._defaults["RECORDER_PREFERRED_DEVICES"] == ["pipewire", "pulse"]
    
    def test_applies_windows_audio_devices(self):
        """Should use Windows audio devices on Windows."""
        from core.settings_manager import SettingsManager
        
        with patch('core.settings_manager.IS_WINDOWS', True):
            with patch.object(SettingsManager, '__init__', lambda self: None):
                mgr = SettingsManager()
                mgr.BASE_DIR = Path("/tmp")
                mgr._defaults = {
                    "RECORDER_PREFERRED_DEVICES_LINUX": ["pipewire", "pulse"],
                    "RECORDER_PREFERRED_DEVICES_WINDOWS": ["default", "speakers"]
                }
                
                mgr._apply_construction()
                
                assert mgr._defaults["RECORDER_PREFERRED_DEVICES"] == ["default", "speakers"]
    
    def test_fallback_to_default_if_missing(self):
        """Should use ['default'] if platform list missing."""
        from core.settings_manager import SettingsManager
        
        with patch('core.settings_manager.IS_WINDOWS', False):
            with patch.object(SettingsManager, '__init__', lambda self: None):
                mgr = SettingsManager()
                mgr.BASE_DIR = Path("/tmp")
                mgr._defaults = {}  # No platform-specific lists
                
                mgr._apply_construction()
                
                assert mgr._defaults["RECORDER_PREFERRED_DEVICES"] == ["default"]


class TestSettingsGetSet:
    """Test get/set operations."""
    
    def test_get_returns_value(self):
        """get() should return config value."""
        from core.settings_manager import SettingsManager
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr._lock = threading.Lock()
            mgr._config = {"TEST_KEY": "test_value"}
            
            assert mgr.get("TEST_KEY") == "test_value"
    
    def test_get_returns_default(self):
        """get() should return default if key missing."""
        from core.settings_manager import SettingsManager
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr._lock = threading.Lock()
            mgr._config = {}
            
            assert mgr.get("MISSING", "fallback") == "fallback"
    
    def test_set_without_persist(self):
        """set() without persist should only update memory."""
        from core.settings_manager import SettingsManager
        
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
    
    def test_set_triggers_callback(self):
        """set() should trigger registered callback."""
        from core.settings_manager import SettingsManager
        
        callback = MagicMock()
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr._config = {}
            mgr._user = {}
            mgr._lock = threading.Lock()
            mgr._reload_callbacks = {"TEST_KEY": callback}
            mgr.save = MagicMock()
            
            mgr.set("TEST_KEY", "new_value", persist=False)
            
            callback.assert_called_once_with("new_value")


class TestSetMany:
    """Test batch setting operations."""
    
    def test_set_many_updates_all(self):
        """set_many should update multiple settings."""
        from core.settings_manager import SettingsManager
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr._config = {}
            mgr._user = {}
            mgr._lock = threading.Lock()
            mgr._reload_callbacks = {}
            mgr.save = MagicMock()
            
            mgr.set_many({"KEY1": "val1", "KEY2": "val2"}, persist=False)
            
            assert mgr._config["KEY1"] == "val1"
            assert mgr._config["KEY2"] == "val2"
            mgr.save.assert_not_called()
    
    def test_set_many_with_persist(self):
        """set_many with persist should save once."""
        from core.settings_manager import SettingsManager
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr._config = {}
            mgr._user = {}
            mgr._lock = threading.Lock()
            mgr._reload_callbacks = {}
            mgr.save = MagicMock()
            
            mgr.set_many({"KEY1": "val1", "KEY2": "val2"}, persist=True)
            
            assert mgr._user["KEY1"] == "val1"
            assert mgr._user["KEY2"] == "val2"
            mgr.save.assert_called_once()


class TestSettingsTiers:
    """Test tier validation."""
    
    def test_hot_reload_tier(self):
        """Hot-reload settings should return 'hot'."""
        from core.settings_manager import SettingsManager
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            
            assert mgr.validate_tier("DEFAULT_USERNAME") == "hot"
    
    def test_restart_tier_for_tts_stt(self):
        """TTS/STT settings require restart (no component tier anymore)."""
        from core.settings_manager import SettingsManager
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            
            # These used to be 'component' tier but now require full restart
            assert mgr.validate_tier("TTS_ENABLED") == "restart"
            assert mgr.validate_tier("STT_ENABLED") == "restart"
    
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
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr._user = {}
            mgr._lock = threading.Lock()
            
            result = mgr.remove_user_override("NONEXISTENT")
            
            assert result is False
    
    def test_get_user_overrides_returns_copy(self):
        """get_user_overrides should return a copy."""
        from core.settings_manager import SettingsManager
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr._user = {"KEY": "value"}
            
            overrides = mgr.get_user_overrides()
            overrides["KEY"] = "modified"
            
            assert mgr._user["KEY"] == "value"


class TestReload:
    """Test settings reload functionality."""
    
    def test_reload_reloads_user_settings(self):
        """reload() should reload from disk."""
        from core.settings_manager import SettingsManager
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr._lock = threading.Lock()
            mgr._load_user_settings = MagicMock()
            mgr._merge_settings = MagicMock()
            mgr._update_mtime = MagicMock()
            
            mgr.reload()
            
            mgr._load_user_settings.assert_called_once()
            mgr._merge_settings.assert_called_once()
            mgr._update_mtime.assert_called_once()
    
    def test_reset_to_defaults(self):
        """reset_to_defaults() should clear user overrides."""
        import threading
        from pathlib import Path
        from core.settings_manager import SettingsManager
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr._defaults = {"KEY": "default"}
            mgr._user = {"KEY": "user", "OTHER": "other"}
            mgr._config = {}
            mgr._lock = threading.RLock()
            mgr._merge_settings = lambda: setattr(mgr, '_config', {**mgr._defaults, **mgr._user})
            mgr._update_mtime = lambda: None
            mgr.BASE_DIR = Path('/tmp/test_settings_reset')
            
            # Create temp dir so file ops work
            mgr.BASE_DIR.mkdir(exist_ok=True)
            (mgr.BASE_DIR / 'user').mkdir(exist_ok=True)
            
            mgr.reset_to_defaults()
            
            assert mgr._user == {}


class TestDeepUpdate:
    """Test nested dict update logic."""
    
    def test_find_category_for_key(self):
        """Should find correct category for nested key."""
        from core.settings_manager import SettingsManager
        
        nested = {
            "identity": {"DEFAULT_USERNAME": "test"},
            "llm": {"CONTEXT_LIMIT": 4000}
        }
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            
            assert mgr._find_category_for_key(nested, "DEFAULT_USERNAME") == "identity"
            assert mgr._find_category_for_key(nested, "CONTEXT_LIMIT") == "llm"
            assert mgr._find_category_for_key(nested, "NONEXISTENT") is None
    
    def test_remove_from_nested(self):
        """Should remove key from nested structure."""
        from core.settings_manager import SettingsManager
        
        nested = {
            "identity": {"DEFAULT_USERNAME": "test", "OTHER": "keep"}
        }
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            
            result = mgr._remove_from_nested(nested, "DEFAULT_USERNAME")
            
            assert result is True
            assert "DEFAULT_USERNAME" not in nested["identity"]
            assert nested["identity"]["OTHER"] == "keep"
    
    def test_remove_from_nested_cleans_empty_categories(self):
        """Should remove empty categories after key removal."""
        from core.settings_manager import SettingsManager
        
        nested = {
            "identity": {"DEFAULT_USERNAME": "test"}
        }
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            
            mgr._remove_from_nested(nested, "DEFAULT_USERNAME")
            
            assert "identity" not in nested
    
    def test_remove_from_root_level(self):
        """Should remove keys at root level."""
        from core.settings_manager import SettingsManager
        
        nested = {
            "ROOT_KEY": "value",
            "identity": {"NESTED": "test"}
        }
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            
            result = mgr._remove_from_nested(nested, "ROOT_KEY")
            
            assert result is True
            assert "ROOT_KEY" not in nested


class TestMagicMethods:
    """Test magic method implementations."""
    
    def test_getattr_returns_config_value(self):
        """settings.KEY should return config value."""
        from core.settings_manager import SettingsManager
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr._lock = threading.Lock()
            mgr._config = {"TEST_KEY": "test_value"}
            
            assert mgr.TEST_KEY == "test_value"
    
    def test_getattr_raises_for_missing(self):
        """settings.MISSING should raise AttributeError (enables getattr defaults)."""
        from core.settings_manager import SettingsManager
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr._lock = threading.Lock()
            mgr._config = {}
            
            with pytest.raises(AttributeError):
                _ = mgr.MISSING
    
    def test_contains(self):
        """'key in settings' should work."""
        from core.settings_manager import SettingsManager
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr._lock = threading.Lock()
            mgr._config = {"EXISTS": "yes"}
            
            assert "EXISTS" in mgr
            assert "MISSING" not in mgr
    
    def test_repr(self):
        """repr should show count."""
        from core.settings_manager import SettingsManager
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr._config = {"A": 1, "B": 2, "C": 3}
            
            assert "3 settings" in repr(mgr)


class TestFileOperationsEncoding:
    """Test UTF-8 encoding in file operations."""
    
    def test_load_defaults_uses_utf8(self, tmp_path):
        """_load_defaults should use UTF-8 encoding."""
        from core.settings_manager import SettingsManager
        
        defaults_dir = tmp_path / "core"
        defaults_dir.mkdir()
        defaults_file = defaults_dir / "settings_defaults.json"
        defaults_file.write_text('{"identity": {"NAME": "テスト"}}', encoding='utf-8')
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr.BASE_DIR = tmp_path
            mgr._defaults = {}
            
            mgr._load_defaults()
            
            assert mgr._defaults["NAME"] == "テスト"
    
    def test_load_user_settings_uses_utf8(self, tmp_path):
        """_load_user_settings should use UTF-8 encoding."""
        from core.settings_manager import SettingsManager
        
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        settings_file = user_dir / "settings.json"
        settings_file.write_text('{"NAME": "日本語"}', encoding='utf-8')
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr.BASE_DIR = tmp_path
            mgr._user = {}
            
            mgr._load_user_settings()
            
            assert mgr._user["NAME"] == "日本語"
    
    def test_save_uses_utf8(self, tmp_path):
        """save() should write UTF-8 encoded files."""
        from core.settings_manager import SettingsManager
        
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        core_dir = tmp_path / "core"
        core_dir.mkdir()
        
        defaults = {"identity": {"NAME": "default"}}
        (core_dir / "settings_defaults.json").write_text(json.dumps(defaults), encoding='utf-8')
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr.BASE_DIR = tmp_path
            mgr._user = {"NAME": "中文"}
            mgr._update_mtime = MagicMock()
            
            def mock_deep_update(nested, flat):
                nested["identity"] = {"NAME": flat["NAME"]}
                return nested
            mgr._deep_update_from_flat = mock_deep_update
            
            result = mgr.save()
            
            assert result is True
            saved = json.loads((user_dir / "settings.json").read_text(encoding='utf-8'))
            assert saved["identity"]["NAME"] == "中文"


class TestFileWatcher:
    """Test file watcher functionality."""
    
    def test_start_file_watcher(self):
        """start_file_watcher should start background thread."""
        from core.settings_manager import SettingsManager
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr._watcher_thread = None
            mgr._watcher_running = False
            mgr.BASE_DIR = Path("/tmp")
            
            # Mock the loop to exit immediately
            def mock_loop():
                pass
            mgr._file_watcher_loop = mock_loop
            
            mgr.start_file_watcher()
            
            assert mgr._watcher_running is True
            assert mgr._watcher_thread is not None
            
            # Cleanup
            mgr._watcher_running = False
            time.sleep(0.1)
    
    def test_stop_file_watcher(self):
        """stop_file_watcher should stop background thread."""
        from core.settings_manager import SettingsManager
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr._watcher_running = True
            mgr._watcher_thread = MagicMock()
            mgr._watcher_thread.is_alive.return_value = True
            
            mgr.stop_file_watcher()
            
            assert mgr._watcher_running is False
            mgr._watcher_thread.join.assert_called_once()
    
    def test_stop_file_watcher_no_thread(self):
        """stop_file_watcher should handle None thread."""
        from core.settings_manager import SettingsManager
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr._watcher_thread = None
            
            mgr.stop_file_watcher()  # Should not raise
    
    def test_double_start_prevented(self):
        """Starting watcher twice should be prevented."""
        from core.settings_manager import SettingsManager
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr._watcher_thread = MagicMock()
            mgr._watcher_thread.is_alive.return_value = True
            mgr._watcher_running = True
            
            # Should return early without starting another thread
            mgr.start_file_watcher()
            
            # Thread should not have changed
            assert mgr._watcher_thread.is_alive.called


class TestCallbackRegistration:
    """Test reload callback registration."""
    
    def test_register_reload_callback(self):
        """Should register callback for key."""
        from core.settings_manager import SettingsManager
        
        callback = MagicMock()
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr._reload_callbacks = {}
            
            mgr.register_reload_callback("MY_KEY", callback)
            
            assert "MY_KEY" in mgr._reload_callbacks
            assert mgr._reload_callbacks["MY_KEY"] == callback
    
    def test_callback_error_handling(self):
        """Callback errors should be caught."""
        from core.settings_manager import SettingsManager
        
        def bad_callback(value):
            raise ValueError("Test error")
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr._config = {}
            mgr._user = {}
            mgr._lock = threading.Lock()
            mgr._reload_callbacks = {"KEY": bad_callback}
            mgr.save = MagicMock()
            
            # Should not raise
            mgr.set("KEY", "value", persist=False)


class TestConfigObjectDetection:
    """Test config object detection logic."""
    
    def test_is_config_object(self):
        """Should identify config objects correctly."""
        from core.settings_manager import SettingsManager
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            
            assert mgr._is_config_object("LLM_PRIMARY") is True
            assert mgr._is_config_object("LLM_FALLBACK") is True
            assert mgr._is_config_object("GENERATION_DEFAULTS") is True
            assert mgr._is_config_object("FASTER_WHISPER_VAD_PARAMETERS") is True
            assert mgr._is_config_object("SOME_OTHER_KEY") is False


class TestGetAllSettings:
    """Test get_all_settings functionality."""
    
    def test_returns_copy(self):
        """get_all_settings should return a copy."""
        from core.settings_manager import SettingsManager
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr._config = {"KEY": "value"}
            
            settings = mgr.get_all_settings()
            settings["KEY"] = "modified"
            
            assert mgr._config["KEY"] == "value"


class TestGetAttrFallback:
    """Test __getattr__ behavior for missing keys.
    
    Critical: This tests the fix for the getattr(config, 'KEY', default) trap.
    Before the fix, missing keys returned None instead of raising AttributeError,
    which caused Python's getattr to think it got a valid value and skip the default.
    """
    
    def test_getattr_returns_existing_key(self):
        """__getattr__ should return value for existing keys."""
        from core.settings_manager import SettingsManager
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr._lock = threading.Lock()
            mgr._config = {"EXISTING_KEY": "test_value"}
            
            assert mgr.EXISTING_KEY == "test_value"
    
    def test_getattr_raises_for_missing_key(self):
        """__getattr__ should raise AttributeError for missing keys."""
        from core.settings_manager import SettingsManager
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr._lock = threading.Lock()
            mgr._config = {}
            
            with pytest.raises(AttributeError) as exc_info:
                _ = mgr.NONEXISTENT_KEY
            
            assert "NONEXISTENT_KEY" in str(exc_info.value)
    
    def test_getattr_with_default_uses_default_for_missing(self):
        """getattr(settings, 'MISSING', default) should return default."""
        from core.settings_manager import SettingsManager
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr._lock = threading.Lock()
            mgr._config = {}
            
            result = getattr(mgr, "MISSING_KEY", "fallback_value")
            assert result == "fallback_value"
    
    def test_getattr_with_default_uses_value_for_existing(self):
        """getattr(settings, 'EXISTS', default) should return actual value."""
        from core.settings_manager import SettingsManager
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr._lock = threading.Lock()
            mgr._config = {"EXISTS": "real_value"}
            
            result = getattr(mgr, "EXISTS", "fallback_value")
            assert result == "real_value"
    
    def test_getattr_private_key_delegation(self):
        """Keys starting with _ should use object.__getattribute__."""
        from core.settings_manager import SettingsManager
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr._lock = threading.Lock()
            mgr._config = {"PUBLIC": "value"}
            mgr._private = "private_value"
            
            # Should access instance attribute, not _config
            assert mgr._private == "private_value"
    
    def test_none_value_is_valid(self):
        """A key explicitly set to None should return None, not raise."""
        from core.settings_manager import SettingsManager
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr._lock = threading.Lock()
            mgr._config = {"NULLABLE_KEY": None}
            
            # Should return None, not raise AttributeError
            assert mgr.NULLABLE_KEY is None
    
    def test_false_value_is_valid(self):
        """A key set to False should return False, not trigger default."""
        from core.settings_manager import SettingsManager
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr._lock = threading.Lock()
            mgr._config = {"DISABLED_FEATURE": False}
            
            result = getattr(mgr, "DISABLED_FEATURE", True)
            assert result is False
    
    def test_empty_list_is_valid(self):
        """A key set to [] should return [], not trigger default."""
        from core.settings_manager import SettingsManager
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr._lock = threading.Lock()
            mgr._config = {"EMPTY_LIST": []}
            
            result = getattr(mgr, "EMPTY_LIST", ["default"])
            assert result == []


class TestConfigProxyChain:
    """Test the full config.py -> settings_manager chain.
    
    This tests the actual usage pattern throughout Sapphire:
    getattr(config, 'SETTING', default)
    """
    
    def test_config_proxy_existing_key(self, tmp_path):
        """config.KEY should return value for existing keys."""
        # Create minimal defaults file
        core_dir = tmp_path / "core"
        core_dir.mkdir()
        defaults = {"test": {"TEST_SETTING": "test_value"}}
        (core_dir / "settings_defaults.json").write_text(json.dumps(defaults))
        
        # Create user dir (required by SettingsManager)
        (tmp_path / "user").mkdir()
        
        # Patch BASE_DIR and reimport
        with patch('core.settings_manager.Path') as mock_path:
            mock_path.return_value.parent.parent = tmp_path
            mock_path.__class__ = Path
            
            # Create fresh manager with our tmp_path
            from core.settings_manager import SettingsManager
            mgr = SettingsManager()
            mgr.BASE_DIR = tmp_path
            mgr._load_defaults()
            mgr._merge_settings()
            
            assert mgr.TEST_SETTING == "test_value"
    
    def test_config_proxy_getattr_fallback(self):
        """getattr(config_proxy, 'MISSING', default) should return default."""
        from core.settings_manager import SettingsManager
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr._config = {"EXISTING": "value"}
            
            # This is the exact pattern used throughout Sapphire
            result = getattr(mgr, "RECORDER_BLOCKSIZE_FALLBACKS", [1024, 512])
            assert result == [1024, 512]
    
    def test_real_world_blocksize_pattern(self):
        """Test the exact pattern that was broken before the fix."""
        from core.settings_manager import SettingsManager
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr._config = {
                "RECORDER_SAMPLE_RATES": [44100, 48000]
                # Note: RECORDER_BLOCKSIZE_FALLBACKS intentionally missing
            }
            
            # This pattern exists in recorder.py and audio_recorder.py
            blocksizes = getattr(mgr, 'RECORDER_BLOCKSIZE_FALLBACKS', [1024, 512, 2048, 4096])
            
            # Before fix: would return None (iteration would fail)
            # After fix: returns the default list
            assert blocksizes == [1024, 512, 2048, 4096]
            assert isinstance(blocksizes, list)
    
    def test_iteration_over_fallback_list(self):
        """Ensure fallback list is actually iterable (the crash scenario)."""
        from core.settings_manager import SettingsManager
        
        with patch.object(SettingsManager, '__init__', lambda self: None):
            mgr = SettingsManager()
            mgr._config = {}  # Empty - key missing
            
            blocksizes = getattr(mgr, 'RECORDER_BLOCKSIZE_FALLBACKS', [1024, 512])
            
            # This would raise TypeError before the fix
            result = []
            for blocksize in blocksizes:
                result.append(blocksize)
            
            assert result == [1024, 512]