"""Unit tests for prompt system (prompt_manager, prompt_crud, prompt_state)"""
import pytest
import json
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestPromptManagerTemplates:
    """Test template replacement in PromptManager."""
    
    def test_replace_ai_name(self):
        """Should replace {ai_name} placeholder."""
        from core.modules.system.prompt_manager import PromptManager
        
        with patch.object(PromptManager, '__init__', lambda self: None):
            mgr = PromptManager()
            mgr.USER_DIR = Path("/tmp")
            
            with patch('core.settings_manager.settings') as mock_settings:
                mock_settings.get.side_effect = lambda k, d: "Sapphire" if k == "DEFAULT_AI_NAME" else d
                
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
                def get_setting(k, d):
                    if k == "DEFAULT_AI_NAME": return "Sapphire"
                    if k == "DEFAULT_USERNAME": return "testuser"
                    return d
                mock_settings.get.side_effect = get_setting
                
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
                "persona": {"test": "You are a test AI."},
                "goals": {"helpful": "Be helpful."},
                "location": {"office": "in an office"},
                "relationship": {"friend": "We are friends."},
                "format": {"casual": "Be casual."},
                "scenario": {},
                "extras": {},
                "emotions": {}
            }
            
            components = {
                "persona": "test",
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
                "persona": {"base": "AI assistant"},
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
                "persona": "base",
                "extras": ["humor", "concise"],
                "emotions": []
            }
            
            result = mgr.assemble_from_components(components)
            
            assert "humor" in result.lower() or "Use humor" in result
            assert "brief" in result.lower() or "concise" in result.lower()


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
            mock_mgr.monoliths = {"test": "Test prompt content"}
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