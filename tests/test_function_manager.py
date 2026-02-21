"""
Phase 1: Function Manager Tests

Tests tool loading, ability resolution, execution dispatch, and mode filtering.
Focus on catching refactor breakages, not exhaustive edge cases.

Run with: pytest tests/test_function_manager.py -v
"""
import pytest
import sys
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add project root before any imports
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Import the module we're testing - this triggers all dependencies
from core.chat.function_manager import FunctionManager


# =============================================================================
# Ability Resolution Tests
# =============================================================================

class TestAbilityResolution:
    """Test ability name to function list resolution."""
    
    def test_ability_all_enables_everything(self):
        """'all' ability should enable all possible tools."""
        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()
            mgr._tools_lock = threading.Lock()
            mgr.function_modules = {}
            mgr.all_possible_tools = [
                {'function': {'name': 'func1'}},
                {'function': {'name': 'func2'}},
                {'function': {'name': 'func3'}},
            ]
            mgr._enabled_tools = []
            mgr._mode_filters = {}
            mgr.current_toolset_name = "none"

            with patch('core.chat.function_manager.toolset_manager') as mock_ts:
                mock_ts.toolset_exists.return_value = False
                mgr.update_enabled_functions(['all'])
            
            assert mgr.current_toolset_name == "all"
            assert len(mgr._enabled_tools) == 3
    
    def test_ability_none_disables_everything(self):
        """'none' ability should disable all tools."""
        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()
            mgr._tools_lock = threading.Lock()
            mgr.all_possible_tools = [{'function': {'name': 'func1'}}]
            mgr._enabled_tools = mgr.all_possible_tools.copy()
            mgr._mode_filters = {}
            mgr.function_modules = {}
            mgr.current_toolset_name = "all"
            
            with patch('core.chat.function_manager.toolset_manager') as mock_ts:
                mock_ts.toolset_exists.return_value = False
                mgr.update_enabled_functions(['none'])
            
            assert mgr.current_toolset_name == "none"
            assert len(mgr._enabled_tools) == 0
    
    def test_ability_module_name_loads_module_functions(self):
        """Module name ability should load that module's functions."""
        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()
            mgr._tools_lock = threading.Lock()
            mgr.function_modules = {
                'web': {'available_functions': ['search', 'fetch']},
                'meta': {'available_functions': ['view_prompt', 'reset_chat']},
            }
            mgr.all_possible_tools = [
                {'function': {'name': 'search'}},
                {'function': {'name': 'fetch'}},
                {'function': {'name': 'view_prompt'}},
                {'function': {'name': 'reset_chat'}},
            ]
            mgr._enabled_tools = []
            mgr._mode_filters = {}
            mgr.current_toolset_name = "none"
            
            with patch('core.chat.function_manager.toolset_manager') as mock_ts:
                mock_ts.toolset_exists.return_value = False
                mgr.update_enabled_functions(['web'])
            
            assert mgr.current_toolset_name == "web"
            enabled_names = [t['function']['name'] for t in mgr._enabled_tools]
            assert 'search' in enabled_names
            assert 'fetch' in enabled_names
            assert 'view_prompt' not in enabled_names
    
    def test_ability_toolset_name_loads_toolset_functions(self):
        """Toolset name ability should load toolset's function list."""
        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()
            mgr._tools_lock = threading.Lock()
            mgr.function_modules = {}
            mgr.all_possible_tools = [
                {'function': {'name': 'test_func'}},
                {'function': {'name': 'network_func'}},
                {'function': {'name': 'other_func'}},
            ]
            mgr._enabled_tools = []
            mgr._mode_filters = {}
            mgr.current_toolset_name = "none"
            
            with patch('core.chat.function_manager.toolset_manager') as mock_ts:
                mock_ts.toolset_exists.return_value = True
                mock_ts.get_toolset_functions.return_value = ['test_func']
                mgr.update_enabled_functions(['basic'])
            
            assert mgr.current_toolset_name == "basic"
            enabled_names = [t['function']['name'] for t in mgr._enabled_tools]
            assert 'test_func' in enabled_names
            assert len(enabled_names) == 1
    
    def test_ability_custom_list(self):
        """Custom function list should enable only those functions."""
        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()
            mgr._tools_lock = threading.Lock()
            mgr.function_modules = {}
            mgr.all_possible_tools = [
                {'function': {'name': 'func_a'}},
                {'function': {'name': 'func_b'}},
                {'function': {'name': 'func_c'}},
            ]
            mgr._enabled_tools = []
            mgr._mode_filters = {}
            mgr.current_toolset_name = "none"
            
            with patch('core.chat.function_manager.toolset_manager') as mock_ts:
                mock_ts.toolset_exists.return_value = False
                mgr.update_enabled_functions(['func_a', 'func_c'])
            
            assert mgr.current_toolset_name == "custom"
            enabled_names = [t['function']['name'] for t in mgr._enabled_tools]
            assert enabled_names == ['func_a', 'func_c']


# =============================================================================
# Validation Tests  
# =============================================================================

class TestValidation:
    """Test ability validation methods."""
    
    def test_is_valid_toolset_special_names(self):
        """'all' and 'none' should always be valid."""
        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()
            mgr.function_modules = {}
            
            with patch('core.chat.function_manager.toolset_manager') as mock_ts:
                mock_ts.toolset_exists.return_value = False
                assert mgr.is_valid_toolset('all') is True
                assert mgr.is_valid_toolset('none') is True
    
    def test_is_valid_toolset_module_name(self):
        """Module names should be valid abilities."""
        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()
            mgr.function_modules = {'web': {}, 'meta': {}}
            
            with patch('core.chat.function_manager.toolset_manager') as mock_ts:
                mock_ts.toolset_exists.return_value = False
                assert mgr.is_valid_toolset('web') is True
                assert mgr.is_valid_toolset('meta') is True
                assert mgr.is_valid_toolset('nonexistent') is False
    
    def test_is_valid_toolset_toolset_name(self):
        """Toolset names should be valid abilities."""
        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()
            mgr.function_modules = {}
            
            with patch('core.chat.function_manager.toolset_manager') as mock_ts:
                mock_ts.toolset_exists.side_effect = lambda n: n in ['basic', 'research']
                assert mgr.is_valid_toolset('basic') is True
                assert mgr.is_valid_toolset('research') is True
                assert mgr.is_valid_toolset('fake_toolset') is False
    
    def test_get_available_toolsets(self):
        """Should return all valid ability names."""
        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()
            mgr.function_modules = {'web': {}, 'meta': {}}
            
            with patch('core.chat.function_manager.toolset_manager') as mock_ts:
                mock_ts.get_toolset_names.return_value = ['basic', 'research']
                abilities = mgr.get_available_toolsets()
            
            assert 'all' in abilities
            assert 'none' in abilities
            assert 'web' in abilities
            assert 'meta' in abilities
            assert 'basic' in abilities
            assert 'research' in abilities


# =============================================================================
# Execution Tests
# =============================================================================

class TestExecution:
    """Test function execution dispatch."""
    
    def test_execute_function_dispatches_correctly(self):
        """execute_function should call the right executor."""
        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()

            mock_executor = MagicMock(return_value=("result_data", True))

            mgr._enabled_tools = [{'function': {'name': 'test_func'}}]
            mgr._mode_filters = {}
            mgr._story_engine = None
            mgr._story_engine_enabled = False
            mgr.execution_map = {'test_func': mock_executor}
            mgr._is_local_map = {'test_func': True}
            mgr._function_module_map = {}
            mgr.tool_history = []
            mgr.tool_history_file = '/tmp/test.json'

            with patch('core.chat.function_manager.config') as mock_cfg:
                mock_cfg.TOOL_HISTORY_MAX_ENTRIES = 0
                result = mgr.execute_function('test_func', {'arg': 'value'})

            assert result == "result_data"
            mock_executor.assert_called_once()
    
    def test_execute_function_rejects_disabled_function(self):
        """Should reject execution of non-enabled functions."""
        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()

            mgr._enabled_tools = [{'function': {'name': 'allowed_func'}}]
            mgr._mode_filters = {}
            mgr._story_engine = None
            mgr._story_engine_enabled = False
            mgr.execution_map = {'disabled_func': MagicMock()}
            mgr._is_local_map = {}
            mgr._function_module_map = {}
            mgr.tool_history = []
            mgr.tool_history_file = '/tmp/test.json'
            
            with patch('core.chat.function_manager.config') as mock_cfg:
                mock_cfg.TOOL_HISTORY_MAX_ENTRIES = 0
                result = mgr.execute_function('disabled_func', {})
            
            assert "not currently available" in result
    
    def test_execute_function_handles_missing_executor(self):
        """Should handle case where executor is not found."""
        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()

            mgr._enabled_tools = [{'function': {'name': 'orphan_func'}}]
            mgr._mode_filters = {}
            mgr._story_engine = None
            mgr._story_engine_enabled = False
            mgr.execution_map = {}
            mgr._is_local_map = {'orphan_func': True}
            mgr._function_module_map = {}
            mgr.tool_history = []
            mgr.tool_history_file = '/tmp/test.json'

            with patch('core.chat.function_manager.config') as mock_cfg:
                mock_cfg.TOOL_HISTORY_MAX_ENTRIES = 0
                result = mgr.execute_function('orphan_func', {})
            
            assert "no execution logic" in result.lower()
    
    def test_execute_function_handles_executor_exception(self):
        """Should catch and report executor exceptions."""
        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()

            def failing_executor(*args):
                raise ValueError("Executor crashed!")

            mgr._enabled_tools = [{'function': {'name': 'crashy_func'}}]
            mgr._mode_filters = {}
            mgr._story_engine = None
            mgr._story_engine_enabled = False
            mgr.execution_map = {'crashy_func': failing_executor}
            mgr._is_local_map = {'crashy_func': True}
            mgr._function_module_map = {}
            mgr.tool_history = []
            mgr.tool_history_file = '/tmp/test.json'
            
            with patch('core.chat.function_manager.config') as mock_cfg:
                mock_cfg.TOOL_HISTORY_MAX_ENTRIES = 0
                result = mgr.execute_function('crashy_func', {})
            
            assert "error" in result.lower()


# =============================================================================
# Network Tool Detection Tests
# =============================================================================

class TestNetworkToolDetection:
    """Test network-requiring tool detection."""
    
    def test_has_network_tools_enabled_true(self):
        """Should detect when network tools are enabled."""
        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()

            mgr._enabled_tools = [
                {'function': {'name': 'local_func'}},
                {'function': {'name': 'web_search'}},
            ]
            mgr._mode_filters = {}
            mgr._story_engine = None
            mgr._story_engine_enabled = False
            mgr._network_functions = {'web_search', 'web_fetch'}

            assert mgr.has_network_tools_enabled() is True

    def test_has_network_tools_enabled_false(self):
        """Should return False when no network tools enabled."""
        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()

            mgr._enabled_tools = [
                {'function': {'name': 'local_func'}},
                {'function': {'name': 'another_local'}},
            ]
            mgr._mode_filters = {}
            mgr._story_engine = None
            mgr._story_engine_enabled = False
            mgr._network_functions = {'web_search', 'web_fetch'}

            assert mgr.has_network_tools_enabled() is False
    
    def test_get_network_functions(self):
        """Should return list of network function names."""
        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()
            mgr._network_functions = {'web_search', 'web_fetch', 'api_call'}
            
            network_funcs = mgr.get_network_functions()
            
            assert set(network_funcs) == {'web_search', 'web_fetch', 'api_call'}


# =============================================================================
# Mode Filtering Tests
# =============================================================================

class TestModeFiltering:
    """Test prompt mode-based tool filtering."""
    
    def test_mode_filter_monolith(self):
        """Monolith mode should only show monolith-allowed tools."""
        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()

            mgr._enabled_tools = [
                {'function': {'name': 'mono_only'}},
                {'function': {'name': 'assembled_only'}},
                {'function': {'name': 'both_modes'}},
                {'function': {'name': 'no_filter'}},
            ]
            mgr._mode_filters = {
                'test_module': {
                    'monolith': ['mono_only', 'both_modes'],
                    'assembled': ['assembled_only', 'both_modes'],
                }
            }
            mgr._story_engine = None
            mgr._story_engine_enabled = False
            mgr.function_modules = {
                'test_module': {'available_functions': ['mono_only', 'assembled_only', 'both_modes']},
                'other_module': {'available_functions': ['no_filter']},
            }

            with patch.object(mgr, '_get_current_prompt_mode', return_value='monolith'):
                filtered = mgr.enabled_tools
            
            names = [t['function']['name'] for t in filtered]
            assert 'mono_only' in names
            assert 'both_modes' in names
            assert 'no_filter' in names
            assert 'assembled_only' not in names
    
    def test_mode_filter_assembled(self):
        """Assembled mode should only show assembled-allowed tools."""
        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()

            mgr._enabled_tools = [
                {'function': {'name': 'mono_only'}},
                {'function': {'name': 'assembled_only'}},
                {'function': {'name': 'both_modes'}},
            ]
            mgr._mode_filters = {
                'test_module': {
                    'monolith': ['mono_only', 'both_modes'],
                    'assembled': ['assembled_only', 'both_modes'],
                }
            }
            mgr._story_engine = None
            mgr._story_engine_enabled = False
            mgr.function_modules = {
                'test_module': {'available_functions': ['mono_only', 'assembled_only', 'both_modes']},
            }

            with patch.object(mgr, '_get_current_prompt_mode', return_value='assembled'):
                filtered = mgr.enabled_tools
            
            names = [t['function']['name'] for t in filtered]
            assert 'assembled_only' in names
            assert 'both_modes' in names
            assert 'mono_only' not in names
    
    def test_no_mode_filters_returns_all(self):
        """No mode filters should return all enabled tools."""
        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()

            mgr._enabled_tools = [
                {'function': {'name': 'func_a'}},
                {'function': {'name': 'func_b'}},
            ]
            mgr._mode_filters = {}
            mgr._story_engine = None
            mgr._story_engine_enabled = False
            mgr.function_modules = {}

            filtered = mgr.enabled_tools

            assert len(filtered) == 2


# =============================================================================
# Enabled Function Names Tests
# =============================================================================

class TestEnabledFunctionNames:
    """Test enabled function name retrieval."""
    
    def test_get_enabled_function_names(self):
        """Should return list of enabled function names."""
        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()

            mgr._enabled_tools = [
                {'function': {'name': 'func_a'}},
                {'function': {'name': 'func_b'}},
                {'function': {'name': 'func_c'}},
            ]
            mgr._mode_filters = {}
            mgr._story_engine = None
            mgr._story_engine_enabled = False

            names = mgr.get_enabled_function_names()

            assert names == ['func_a', 'func_b', 'func_c']

    def test_get_enabled_function_names_empty(self):
        """Should return empty list when no tools enabled."""
        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()

            mgr._enabled_tools = []
            mgr._mode_filters = {}
            mgr._story_engine = None
            mgr._story_engine_enabled = False

            names = mgr.get_enabled_function_names()

            assert names == []


# =============================================================================
# Current Ability Info Tests
# =============================================================================

class TestAbilityInfo:
    """Test ability info reporting."""
    
    def test_get_current_toolset_info_basic(self):
        """Should return structured info about current ability."""
        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()

            mgr.current_toolset_name = "web"
            mgr._enabled_tools = [
                {'function': {'name': 'search'}},
                {'function': {'name': 'fetch'}},
            ]
            mgr._mode_filters = {}
            mgr._story_engine = None
            mgr._story_engine_enabled = False
            mgr.function_modules = {
                'web': {'available_functions': ['search', 'fetch']}
            }
            mgr.all_possible_tools = mgr._enabled_tools

            with patch.object(mgr, '_get_current_prompt_mode', return_value='monolith'):
                with patch('core.chat.function_manager.toolset_manager') as mock_ts:
                    mock_ts.toolset_exists.return_value = False
                    info = mgr.get_current_toolset_info()
            
            assert info['name'] == 'web'
            assert info['function_count'] == 2
            assert info['prompt_mode'] == 'monolith'
            assert 'status' in info


# =============================================================================
# Integration Tests
# =============================================================================

class TestIntegration:
    """Integration tests with real FunctionManager."""
    
    def test_function_manager_imports(self):
        """FunctionManager should import without errors."""
        assert FunctionManager is not None
    
    def test_real_function_manager_has_expected_methods(self):
        """FunctionManager should have all expected public methods."""
        expected_methods = [
            'update_enabled_functions',
            'is_valid_toolset',
            'get_available_toolsets',
            'execute_function',
            'get_enabled_function_names',
            'has_network_tools_enabled',
            'get_network_functions',
            'get_current_toolset_info',
        ]
        
        for method in expected_methods:
            assert hasattr(FunctionManager, method), f"Missing method: {method}"
    
    def test_enabled_tools_is_property(self):
        """enabled_tools should be a property (for mode filtering)."""
        assert isinstance(
            getattr(FunctionManager, 'enabled_tools', None), 
            property
        ), "enabled_tools should be a property"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])