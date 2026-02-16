"""Unit tests for prompt system (prompt_manager, prompt_crud, prompt_state)"""
import pytest
import json
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestPromptManagerLoading:
    """Test prompt data loading methods."""
    
    def test_load_pieces_success(self, tmp_path):
        """_load_pieces should load prompt_pieces.json."""
        from core.modules.system.prompt_manager import PromptManager
        
        prompts_dir = tmp_path / "user" / "prompts"
        prompts_dir.mkdir(parents=True)
        
        pieces = {
            "components": {"character": {"test": "Test persona"}},
            "scenario_presets": {"default": {}}
        }
        (prompts_dir / "prompt_pieces.json").write_text(
            json.dumps(pieces), encoding='utf-8'
        )
        
        with patch.object(PromptManager, '__init__', lambda self: None):
            mgr = PromptManager()
            mgr.USER_DIR = prompts_dir
            mgr._components = {}
            mgr._scenario_presets = {}
            
            mgr._load_pieces()
            
            assert "character" in mgr._components
            assert mgr._components["character"]["test"] == "Test persona"
    
    def test_load_pieces_missing_file(self, tmp_path):
        """_load_pieces should handle missing file gracefully."""
        from core.modules.system.prompt_manager import PromptManager
        
        prompts_dir = tmp_path / "user" / "prompts"
        prompts_dir.mkdir(parents=True)
        
        with patch.object(PromptManager, '__init__', lambda self: None):
            mgr = PromptManager()
            mgr.USER_DIR = prompts_dir
            mgr._components = {}
            mgr._scenario_presets = {}
            
            mgr._load_pieces()  # Should not raise
            
            assert mgr._components == {}
            assert mgr._scenario_presets == {}
    
    def test_load_monoliths_success(self, tmp_path):
        """_load_monoliths should load prompt_monoliths.json."""
        from core.modules.system.prompt_manager import PromptManager
        
        prompts_dir = tmp_path / "user" / "prompts"
        prompts_dir.mkdir(parents=True)
        
        monoliths = {
            "_comment": "skip this",
            "default": "Default prompt text",
            "custom": "Custom prompt"
        }
        (prompts_dir / "prompt_monoliths.json").write_text(
            json.dumps(monoliths), encoding='utf-8'
        )
        
        with patch.object(PromptManager, '__init__', lambda self: None):
            mgr = PromptManager()
            mgr.USER_DIR = prompts_dir
            mgr._monoliths = {}
            
            mgr._load_monoliths()
            
            assert "default" in mgr._monoliths
            assert "custom" in mgr._monoliths
            assert "_comment" not in mgr._monoliths
    
    def test_load_spices_success(self, tmp_path):
        """_load_spices should load prompt_spices.json."""
        from core.modules.system.prompt_manager import PromptManager
        
        prompts_dir = tmp_path / "user" / "prompts"
        prompts_dir.mkdir(parents=True)
        
        spices = {
            "_comment": "skip",
            "humor": ["joke1", "joke2"],
            "emotions": ["happy", "sad"]
        }
        (prompts_dir / "prompt_spices.json").write_text(
            json.dumps(spices), encoding='utf-8'
        )
        
        with patch.object(PromptManager, '__init__', lambda self: None):
            mgr = PromptManager()
            mgr.USER_DIR = prompts_dir
            mgr._spices = {}
            
            mgr._load_spices()
            
            assert "humor" in mgr._spices
            assert "_comment" not in mgr._spices


class TestPromptManagerTemplates:
    """Test template replacement in PromptManager."""
    
    def test_replace_ai_name(self):
        """Should replace {ai_name} placeholder."""
        from core.modules.system.prompt_manager import PromptManager
        
        with patch.object(PromptManager, '__init__', lambda self: None):
            mgr = PromptManager()
            mgr.USER_DIR = Path("/tmp")
            
            # Patch where settings is imported FROM (inside the method)
            with patch('core.settings_manager.settings') as mock_settings:
                mock_settings.get.side_effect = lambda k, d: d

                result = mgr._replace_templates("Hello {ai_name}!")

                assert result == "Hello Sapphire!"
    
    def test_replace_user_name(self):
        """Should replace {user_name} placeholder."""
        from core.modules.system.prompt_manager import PromptManager
        
        with patch.object(PromptManager, '__init__', lambda self: None):
            mgr = PromptManager()
            mgr.USER_DIR = Path("/tmp")
            
            with patch('core.settings_manager.settings') as mock_settings:
                mock_settings.get.side_effect = lambda k, d: "testuser" if k == "DEFAULT_USERNAME" else d
                
                result = mgr._replace_templates("Hello {user_name}!")
                
                assert result == "Hello testuser!"
    
    def test_replace_both_placeholders(self):
        """Should replace both placeholders in one string."""
        from core.modules.system.prompt_manager import PromptManager
        
        with patch.object(PromptManager, '__init__', lambda self: None):
            mgr = PromptManager()
            mgr.USER_DIR = Path("/tmp")
            
            with patch('core.settings_manager.settings') as mock_settings:
                mock_settings.get.side_effect = lambda k, d: "testuser" if k == "DEFAULT_USERNAME" else d

                result = mgr._replace_templates("I am {ai_name}, you are {user_name}")

                assert result == "I am Sapphire, you are testuser"
    
    def test_handles_empty_string(self):
        """Should handle empty string."""
        from core.modules.system.prompt_manager import PromptManager
        
        with patch.object(PromptManager, '__init__', lambda self: None):
            mgr = PromptManager()
            mgr.USER_DIR = Path("/tmp")
            
            assert mgr._replace_templates("") == ""
    
    def test_handles_none(self):
        """Should handle None input."""
        from core.modules.system.prompt_manager import PromptManager
        
        with patch.object(PromptManager, '__init__', lambda self: None):
            mgr = PromptManager()
            mgr.USER_DIR = Path("/tmp")
            
            assert mgr._replace_templates(None) is None


class TestPromptManagerAssembly:
    """Test prompt assembly from components."""
    
    def test_assemble_basic_components(self):
        """Should assemble prompt from component structure."""
        from core.modules.system.prompt_manager import PromptManager
        
        with patch.object(PromptManager, '__init__', lambda self: None):
            mgr = PromptManager()
            mgr.USER_DIR = Path("/tmp")
            mgr._components = {
                "character": {"test": "You are a test AI."},
                "goals": {"helpful": "Be helpful."},
                "location": {"office": "in an office"},
                "relationship": {"friend": "We are friends."},
                "format": {"casual": "Be casual."},
                "scenario": {},
                "extras": {},
                "emotions": {}
            }
            
            components = {
                "character": "test",
                "goals": "helpful",
                "location": "office",
                "relationship": "friend",
                "format": "casual",
                "scenario": "default",
                "extras": [],
                "emotions": []
            }
            
            result = mgr.assemble_from_components(components)
            
            assert "You are a test AI" in result
            assert "Be helpful" in result
    
    def test_assemble_with_extras(self):
        """Should include extras in assembly."""
        from core.modules.system.prompt_manager import PromptManager
        
        with patch.object(PromptManager, '__init__', lambda self: None):
            mgr = PromptManager()
            mgr.USER_DIR = Path("/tmp")
            mgr._components = {
                "character": {"base": "AI assistant"},
                "goals": {},
                "location": {},
                "relationship": {},
                "format": {},
                "scenario": {},
                "extras": {
                    "humor": "Use humor when appropriate.",
                    "concise": "Keep responses brief."
                },
                "emotions": {}
            }
            
            components = {
                "character": "base",
                "extras": ["humor", "concise"],
                "emotions": []
            }
            
            result = mgr.assemble_from_components(components)
            
            assert "humor" in result.lower() or "Use humor" in result
            assert "brief" in result.lower() or "concise" in result.lower()
    
    def test_assemble_with_emotions(self):
        """Should include emotions in assembly."""
        from core.modules.system.prompt_manager import PromptManager
        
        with patch.object(PromptManager, '__init__', lambda self: None):
            mgr = PromptManager()
            mgr.USER_DIR = Path("/tmp")
            mgr._components = {
                "character": {"base": "AI assistant"},
                "goals": {},
                "location": {},
                "relationship": {},
                "format": {},
                "scenario": {},
                "extras": {},
                "emotions": {
                    "happy": "feeling happy",
                    "curious": "feeling curious"
                }
            }
            
            components = {
                "character": "base",
                "extras": [],
                "emotions": ["happy", "curious"]
            }
            
            result = mgr.assemble_from_components(components)
            
            assert "happy" in result.lower() or "Emotions:" in result


class TestPromptManagerSaving:
    """Test prompt save methods."""
    
    def test_save_scenario_presets(self, tmp_path):
        """save_scenario_presets should write to prompt_pieces.json."""
        from core.modules.system.prompt_manager import PromptManager
        
        prompts_dir = tmp_path / "user" / "prompts"
        prompts_dir.mkdir(parents=True)
        
        # Create initial file
        initial = {"components": {}, "scenario_presets": {}}
        pieces_file = prompts_dir / "prompt_pieces.json"
        pieces_file.write_text(json.dumps(initial), encoding='utf-8')
        
        with patch.object(PromptManager, '__init__', lambda self: None):
            mgr = PromptManager()
            mgr.USER_DIR = prompts_dir
            mgr._scenario_presets = {"new_preset": {"character": "test"}}
            
            mgr.save_scenario_presets()
            
            saved = json.loads(pieces_file.read_text(encoding='utf-8'))
            assert "new_preset" in saved["scenario_presets"]
    
    def test_save_monoliths(self, tmp_path):
        """save_monoliths should write to prompt_monoliths.json."""
        from core.modules.system.prompt_manager import PromptManager
        
        prompts_dir = tmp_path / "user" / "prompts"
        prompts_dir.mkdir(parents=True)
        
        with patch.object(PromptManager, '__init__', lambda self: None):
            mgr = PromptManager()
            mgr.USER_DIR = prompts_dir
            mgr._monoliths = {"test_mono": {"content": "Test prompt content", "privacy_required": False}}

            mgr.save_monoliths()

            mono_file = prompts_dir / "prompt_monoliths.json"
            saved = json.loads(mono_file.read_text(encoding='utf-8'))
            assert saved["test_mono"]["content"] == "Test prompt content"
    
    def test_save_components(self, tmp_path):
        """save_components should write to prompt_pieces.json."""
        from core.modules.system.prompt_manager import PromptManager
        
        prompts_dir = tmp_path / "user" / "prompts"
        prompts_dir.mkdir(parents=True)
        
        initial = {"components": {}, "scenario_presets": {}}
        pieces_file = prompts_dir / "prompt_pieces.json"
        pieces_file.write_text(json.dumps(initial), encoding='utf-8')
        
        with patch.object(PromptManager, '__init__', lambda self: None):
            mgr = PromptManager()
            mgr.USER_DIR = prompts_dir
            mgr._components = {"character": {"new": "New persona"}}
            
            mgr.save_components()
            
            saved = json.loads(pieces_file.read_text(encoding='utf-8'))
            assert saved["components"]["character"]["new"] == "New persona"
    
    def test_save_spices(self, tmp_path):
        """save_spices should write to prompt_spices.json."""
        from core.modules.system.prompt_manager import PromptManager
        
        prompts_dir = tmp_path / "user" / "prompts"
        prompts_dir.mkdir(parents=True)
        
        with patch.object(PromptManager, '__init__', lambda self: None):
            mgr = PromptManager()
            mgr.USER_DIR = prompts_dir
            mgr._spices = {"humor": ["joke1", "joke2"]}
            mgr._disabled_categories = set()
            
            mgr.save_spices()
            
            spice_file = prompts_dir / "prompt_spices.json"
            saved = json.loads(spice_file.read_text(encoding='utf-8'))
            assert saved["humor"] == ["joke1", "joke2"]


class TestPromptManagerEncoding:
    """Test UTF-8 encoding in prompt file operations."""
    
    def test_load_pieces_utf8(self, tmp_path):
        """_load_pieces should handle UTF-8 content."""
        from core.modules.system.prompt_manager import PromptManager
        
        prompts_dir = tmp_path / "user" / "prompts"
        prompts_dir.mkdir(parents=True)
        
        pieces = {"components": {"character": {"japanese": "日本語テスト"}}}
        (prompts_dir / "prompt_pieces.json").write_text(
            json.dumps(pieces, ensure_ascii=False), encoding='utf-8'
        )
        
        with patch.object(PromptManager, '__init__', lambda self: None):
            mgr = PromptManager()
            mgr.USER_DIR = prompts_dir
            mgr._components = {}
            mgr._scenario_presets = {}
            
            mgr._load_pieces()
            
            assert mgr._components["character"]["japanese"] == "日本語テスト"
    
    def test_save_monoliths_utf8(self, tmp_path):
        """save_monoliths should write UTF-8 content."""
        from core.modules.system.prompt_manager import PromptManager
        
        prompts_dir = tmp_path / "user" / "prompts"
        prompts_dir.mkdir(parents=True)
        
        with patch.object(PromptManager, '__init__', lambda self: None):
            mgr = PromptManager()
            mgr.USER_DIR = prompts_dir
            mgr._monoliths = {"chinese": {"content": "你好世界", "privacy_required": False}}

            mgr.save_monoliths()

            mono_file = prompts_dir / "prompt_monoliths.json"
            saved = json.loads(mono_file.read_text(encoding='utf-8'))
            assert saved["chinese"]["content"] == "你好世界"


class TestPromptManagerFileWatcher:
    """Test file watcher functionality."""
    
    def test_start_file_watcher(self):
        """start_file_watcher should start background thread."""
        from core.modules.system.prompt_manager import PromptManager
        
        with patch.object(PromptManager, '__init__', lambda self: None):
            mgr = PromptManager()
            mgr._watcher_thread = None
            mgr._watcher_running = False
            mgr.USER_DIR = Path("/tmp")
            mgr._last_mtimes = {}
            
            def mock_loop():
                pass
            mgr._file_watcher_loop = mock_loop
            
            mgr.start_file_watcher()
            
            assert mgr._watcher_running is True
            assert mgr._watcher_thread is not None
            
            mgr._watcher_running = False
    
    def test_stop_file_watcher(self):
        """stop_file_watcher should stop background thread."""
        from core.modules.system.prompt_manager import PromptManager
        
        with patch.object(PromptManager, '__init__', lambda self: None):
            mgr = PromptManager()
            mgr._watcher_running = True
            mgr._watcher_thread = MagicMock()
            mgr._watcher_thread.is_alive.return_value = True
            
            mgr.stop_file_watcher()
            
            assert mgr._watcher_running is False
            mgr._watcher_thread.join.assert_called_once()
    
    def test_reload(self):
        """reload() should reload all data."""
        from core.modules.system.prompt_manager import PromptManager
        
        with patch.object(PromptManager, '__init__', lambda self: None):
            mgr = PromptManager()
            mgr._lock = threading.Lock()
            mgr._load_all = MagicMock()
            
            mgr.reload()
            
            mgr._load_all.assert_called_once()


class TestPromptManagerProperties:
    """Test property accessors."""
    
    def test_components_property(self):
        """components property should return _components."""
        from core.modules.system.prompt_manager import PromptManager
        
        with patch.object(PromptManager, '__init__', lambda self: None):
            mgr = PromptManager()
            mgr._components = {"test": "value"}
            
            assert mgr.components == {"test": "value"}
    
    def test_scenario_presets_property(self):
        """scenario_presets property should return _scenario_presets."""
        from core.modules.system.prompt_manager import PromptManager
        
        with patch.object(PromptManager, '__init__', lambda self: None):
            mgr = PromptManager()
            mgr._scenario_presets = {"preset1": {}}
            
            assert mgr.scenario_presets == {"preset1": {}}
    
    def test_monoliths_property(self):
        """monoliths property should return _monoliths."""
        from core.modules.system.prompt_manager import PromptManager
        
        with patch.object(PromptManager, '__init__', lambda self: None):
            mgr = PromptManager()
            mgr._monoliths = {"mono1": "text"}
            
            assert mgr.monoliths == {"mono1": "text"}
    
    def test_spices_property(self):
        """spices property should return _spices."""
        from core.modules.system.prompt_manager import PromptManager
        
        with patch.object(PromptManager, '__init__', lambda self: None):
            mgr = PromptManager()
            mgr._spices = {"humor": []}
            
            assert mgr.spices == {"humor": []}


class TestPromptCrud:
    """Test prompt CRUD operations."""
    
    def test_list_prompts_includes_monoliths(self):
        """list_prompts should include monolith names."""
        with patch('core.modules.system.prompt_crud.prompt_manager') as mock_mgr:
            mock_mgr.monoliths = {"default": "text", "custom": "text2"}
            mock_mgr.scenario_presets = {}
            
            with patch('core.modules.system.prompt_crud.prompt_state') as mock_state:
                mock_state._user_prompts = {}
                
                from core.modules.system.prompt_crud import list_prompts
                result = list_prompts()
                
                assert "default" in result
                assert "custom" in result
    
    def test_list_prompts_includes_scenarios(self):
        """list_prompts should include scenario preset names."""
        with patch('core.modules.system.prompt_crud.prompt_manager') as mock_mgr:
            mock_mgr.monoliths = {}
            mock_mgr.scenario_presets = {"work_mode": {}, "casual": {}}
            
            with patch('core.modules.system.prompt_crud.prompt_state') as mock_state:
                mock_state._user_prompts = {}
                
                from core.modules.system.prompt_crud import list_prompts
                result = list_prompts()
                
                assert "work_mode" in result
                assert "casual" in result
    
    def test_get_prompt_monolith(self):
        """get_prompt should return monolith with content."""
        with patch('core.modules.system.prompt_crud.prompt_manager') as mock_mgr:
            mock_mgr.monoliths = {"test": {"content": "Test prompt content", "privacy_required": False}}
            mock_mgr.scenario_presets = {}
            
            with patch('core.modules.system.prompt_crud.prompt_state') as mock_state:
                mock_state._user_prompts = {}
                
                from core.modules.system.prompt_crud import get_prompt
                result = get_prompt("test")
                
                assert result["type"] == "monolith"
                assert result["content"] == "Test prompt content"
    
    def test_get_prompt_not_found(self):
        """get_prompt should return None for missing prompt."""
        with patch('core.modules.system.prompt_crud.prompt_manager') as mock_mgr:
            mock_mgr.monoliths = {}
            mock_mgr.scenario_presets = {}
            
            with patch('core.modules.system.prompt_crud.prompt_state') as mock_state:
                mock_state._user_prompts = {}
                
                from core.modules.system.prompt_crud import get_prompt
                result = get_prompt("nonexistent")
                
                assert result is None


class TestPromptState:
    """Test prompt state management."""
    
    def test_get_active_preset_name(self):
        """Should return current active preset name."""
        with patch('core.modules.system.prompt_state.prompt_manager') as mock_mgr:
            mock_mgr._active_preset_name = "custom_prompt"
            
            from core.modules.system.prompt_state import get_active_preset_name
            result = get_active_preset_name()
            
            assert result == "custom_prompt"
    
    def test_clear_spice(self):
        """clear_spice should empty spice field."""
        from core.modules.system import prompt_state
        
        prompt_state._assembled_state = {"spice": "something spicy"}
        
        result = prompt_state.clear_spice()
        
        assert prompt_state._assembled_state["spice"] == ""
        assert "cleared" in result.lower()