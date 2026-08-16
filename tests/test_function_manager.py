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
            mgr._settings_gates = {}
            mgr._hidden_tools = set()
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
            mgr._settings_gates = {}
            mgr._hidden_tools = set()
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
            mgr._settings_gates = {}
            mgr._hidden_tools = set()
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
            mgr._settings_gates = {}
            mgr._hidden_tools = set()
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
            mgr._settings_gates = {}
            mgr._hidden_tools = set()
            mgr.current_toolset_name = "none"

            with patch('core.chat.function_manager.toolset_manager') as mock_ts:
                mock_ts.toolset_exists.return_value = False
                mgr.update_enabled_functions(['func_a', 'func_c'])

            assert mgr.current_toolset_name == "custom"
            enabled_names = [t['function']['name'] for t in mgr._enabled_tools]
            assert enabled_names == ['func_a', 'func_c']


# =============================================================================
# Toolset Re-Apply Regression Tests (2026-05-16)
# =============================================================================
# Bug #1: plugin toggle / reload code paths re-passed current_toolset_name
# back into update_enabled_functions. When that value was "custom" (the
# sentinel for ad-hoc selection), the filter step would intersect
# all_possible_tools with the literal string "custom" → zero matches →
# _enabled_tools silently emptied. User symptom: tools in toolset, AI
# can't use them, swapping to "All" works.
#
# Bug #2: register_plugin_tools (and register_dynamic_tools) only auto-
# added new tools to _enabled_tools when current_toolset_name was "all".
# If the user had a SAVED toolset active that already listed the
# newly-registering tool by name, the tool ended up in all_possible_tools
# but NOT _enabled_tools — LLM never saw it.


class TestCustomSentinelReApply:
    """Re-passing 'custom' as a toolset name must not zero out the
    currently-enabled tools. It should re-derive the function list from
    the current _enabled_tools state."""

    def _make_mgr_with_custom_selection(self):
        """Build a FunctionManager mid-state: current=custom with 2 tools enabled."""
        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()
            mgr._tools_lock = threading.Lock()
            mgr.function_modules = {}
            mgr.all_possible_tools = [
                {'function': {'name': 'func_a'}},
                {'function': {'name': 'func_b'}},
                {'function': {'name': 'func_c'}},
            ]
            mgr._enabled_tools = [
                {'function': {'name': 'func_a'}},
                {'function': {'name': 'func_c'}},
            ]
            mgr._mode_filters = {}
            mgr._settings_gates = {}
            mgr._hidden_tools = set()
            mgr.current_toolset_name = "custom"
            return mgr

    def test_custom_sentinel_preserves_enabled_tools(self):
        """Bug #1: update_enabled_functions(['custom']) must NOT empty enabled_tools."""
        mgr = self._make_mgr_with_custom_selection()
        with patch('core.chat.function_manager.toolset_manager') as mock_ts:
            mock_ts.toolset_exists.return_value = False
            mgr.update_enabled_functions(['custom'])
        enabled_names = sorted(t['function']['name'] for t in mgr._enabled_tools)
        assert enabled_names == ['func_a', 'func_c'], (
            f"Expected ['func_a', 'func_c'] preserved, got {enabled_names}. "
            f"This is the silent-wipe regression — passing 'custom' must re-derive "
            f"from current _enabled_tools, not filter against the literal string."
        )
        assert mgr.current_toolset_name == "custom"

    def test_custom_sentinel_with_empty_enabled_stays_empty(self):
        """Edge case: passing 'custom' with nothing currently enabled should
        produce empty _enabled_tools and not error."""
        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()
            mgr._tools_lock = threading.Lock()
            mgr.function_modules = {}
            mgr.all_possible_tools = [{'function': {'name': 'func_a'}}]
            mgr._enabled_tools = []
            mgr._mode_filters = {}
            mgr._settings_gates = {}
            mgr._hidden_tools = set()
            mgr.current_toolset_name = "custom"
            with patch('core.chat.function_manager.toolset_manager') as mock_ts:
                mock_ts.toolset_exists.return_value = False
                mgr.update_enabled_functions(['custom'])
            assert mgr._enabled_tools == []
            assert mgr.current_toolset_name == "custom"


class TestNewToolAutoJoinsActiveSavedToolset:
    """Registering new tools (plugin or dynamic) should auto-add them to
    _enabled_tools when the active saved toolset references them by name —
    not just when 'all' is the active toolset."""

    def _make_mgr(self, current_toolset, toolset_funcs):
        """Build a FunctionManager with a saved toolset active."""
        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()
            mgr._tools_lock = threading.Lock()
            mgr.function_modules = {}
            mgr.all_possible_tools = []
            mgr._enabled_tools = []
            mgr._mode_filters = {}
            mgr._settings_gates = {}
            mgr._hidden_tools = set()
            mgr._network_functions = set()
            mgr._is_local_map = {}
            mgr._function_module_map = {}
            mgr.execution_map = {}
            mgr.current_toolset_name = current_toolset
            mgr._toolset_funcs = toolset_funcs  # used by mock below
            return mgr

    def test_dynamic_tools_auto_added_to_active_saved_toolset(self):
        """Bug #2: when active toolset is a saved one whose function list
        includes the newly-registered tool, the tool must land in _enabled_tools."""
        mgr = self._make_mgr(current_toolset='evelyn',
                             toolset_funcs=['body_speak', 'memory_save', 'body_see'])
        new_tools = [
            {'type': 'function', 'function': {'name': 'body_speak'}},
            {'type': 'function', 'function': {'name': 'body_see'}},
            {'type': 'function', 'function': {'name': 'unrelated_tool'}},
        ]
        with patch('core.chat.function_manager.toolset_manager') as mock_ts:
            mock_ts.toolset_exists.return_value = True
            mock_ts.get_toolset_functions.return_value = mgr._toolset_funcs
            mgr.register_dynamic_tools('test_module', new_tools, executor=lambda *a, **k: None)

        enabled_names = sorted(t['function']['name'] for t in mgr._enabled_tools)
        assert 'body_speak' in enabled_names, (
            "body_speak is in the active toolset's function list — must be enabled"
        )
        assert 'body_see' in enabled_names, (
            "body_see is in the active toolset's function list — must be enabled"
        )
        assert 'unrelated_tool' not in enabled_names, (
            "unrelated_tool is NOT in the toolset — should not be auto-enabled"
        )

    def test_dynamic_tools_not_added_when_toolset_omits_them(self):
        """If active saved toolset doesn't reference the new tools, they
        register in all_possible_tools but stay out of _enabled_tools."""
        mgr = self._make_mgr(current_toolset='minimal', toolset_funcs=['only_this_one'])
        new_tools = [
            {'type': 'function', 'function': {'name': 'something_new'}},
        ]
        with patch('core.chat.function_manager.toolset_manager') as mock_ts:
            mock_ts.toolset_exists.return_value = True
            mock_ts.get_toolset_functions.return_value = mgr._toolset_funcs
            mgr.register_dynamic_tools('test_module', new_tools, executor=lambda *a, **k: None)

        # Tool lands in all_possible_tools
        assert any(t['function']['name'] == 'something_new' for t in mgr.all_possible_tools)
        # But NOT in _enabled_tools (toolset didn't reference it)
        assert not any(t['function']['name'] == 'something_new' for t in mgr._enabled_tools)

    def test_dynamic_tools_skip_auto_add_when_current_is_custom(self):
        """When current_toolset_name is 'custom' (ad-hoc selection), don't
        try to consult toolset_manager — there is no saved toolset to query."""
        mgr = self._make_mgr(current_toolset='custom', toolset_funcs=[])
        new_tools = [{'type': 'function', 'function': {'name': 'fresh_tool'}}]
        with patch('core.chat.function_manager.toolset_manager') as mock_ts:
            mock_ts.toolset_exists.return_value = False
            mgr.register_dynamic_tools('test_module', new_tools, executor=lambda *a, **k: None)
        # Should land in all_possible_tools but NOT _enabled_tools
        assert any(t['function']['name'] == 'fresh_tool' for t in mgr.all_possible_tools)
        assert not any(t['function']['name'] == 'fresh_tool' for t in mgr._enabled_tools)
        # Confirm we never tried to look up a saved toolset named "custom"
        mock_ts.toolset_exists.assert_not_called()


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
            mgr._settings_gates = {}
            mgr._hidden_tools = set()
            mgr._story_engine = None
            mgr._story_engine_enabled = False
            mgr._settings_gates = {}
            mgr._hidden_tools = set()
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
            mgr._settings_gates = {}
            mgr._hidden_tools = set()
            mgr._story_engine = None
            mgr._story_engine_enabled = False
            mgr._settings_gates = {}
            mgr._hidden_tools = set()
            mgr.execution_map = {'disabled_func': MagicMock()}
            mgr._is_local_map = {}
            mgr._function_module_map = {}
            mgr.tool_history = []
            mgr.tool_history_file = '/tmp/test.json'
            
            with patch('core.chat.function_manager.config') as mock_cfg:
                mock_cfg.TOOL_HISTORY_MAX_ENTRIES = 0
                result = mgr.execute_function('disabled_func', {})

            # Error message must be LLM-actionable — explicitly say the tool
            # isn't in the toolset AND tell the LLM what to do next. The old
            # terse "not currently available" caused models to bail to empty
            # content, hitting the canned "I have completed the requested
            # actions" fallback (the silent-conk-out symptom). 2026-05-16.
            assert 'disabled_func' in result
            assert 'not in the active toolset' in result
            assert 'respond directly to the user' in result
    
    def test_execute_function_handles_missing_executor(self):
        """Should handle case where executor is not found."""
        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()

            mgr._enabled_tools = [{'function': {'name': 'orphan_func'}}]
            mgr._mode_filters = {}
            mgr._settings_gates = {}
            mgr._hidden_tools = set()
            mgr._story_engine = None
            mgr._story_engine_enabled = False
            mgr._settings_gates = {}
            mgr._hidden_tools = set()
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
            mgr._settings_gates = {}
            mgr._hidden_tools = set()
            mgr._story_engine = None
            mgr._story_engine_enabled = False
            mgr._settings_gates = {}
            mgr._hidden_tools = set()
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
            mgr._settings_gates = {}
            mgr._hidden_tools = set()
            mgr._story_engine = None
            mgr._story_engine_enabled = False
            mgr._settings_gates = {}
            mgr._hidden_tools = set()
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
            mgr._settings_gates = {}
            mgr._hidden_tools = set()
            mgr._story_engine = None
            mgr._story_engine_enabled = False
            mgr._settings_gates = {}
            mgr._hidden_tools = set()
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
            mgr._settings_gates = {}
            mgr._hidden_tools = set()
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
            mgr._settings_gates = {}
            mgr._hidden_tools = set()
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
            mgr._settings_gates = {}
            mgr._hidden_tools = set()
            mgr._story_engine = None
            mgr._story_engine_enabled = False
            mgr._settings_gates = {}
            mgr._hidden_tools = set()
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
            mgr._settings_gates = {}
            mgr._hidden_tools = set()
            mgr._story_engine = None
            mgr._story_engine_enabled = False
            mgr._settings_gates = {}
            mgr._hidden_tools = set()

            names = mgr.get_enabled_function_names()

            assert names == ['func_a', 'func_b', 'func_c']

    def test_get_enabled_function_names_empty(self):
        """Should return empty list when no tools enabled."""
        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()

            mgr._enabled_tools = []
            mgr._mode_filters = {}
            mgr._settings_gates = {}
            mgr._hidden_tools = set()
            mgr._story_engine = None
            mgr._story_engine_enabled = False
            mgr._settings_gates = {}
            mgr._hidden_tools = set()

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
            mgr._settings_gates = {}
            mgr._hidden_tools = set()
            mgr._story_engine = None
            mgr._story_engine_enabled = False
            mgr._settings_gates = {}
            mgr._hidden_tools = set()
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


class TestPrivateUnflaggedToggle:
    """PRIVATE_ALLOW_UNFLAGGED_TOOLS (Settings > Privacy, 2026-08-15):
    plugins that predate the per-tool is_local flag were blocked wholesale
    in private chats (fail-closed). The opt-in treats a MISSING flag as
    local; tools that explicitly declare non-local stay blocked either way."""

    def _gate(self, is_local_map):
        from core.chat.function_manager import FunctionManager
        mgr = FunctionManager.__new__(FunctionManager)
        mgr._is_local_map = is_local_map
        return mgr

    def _check(self, is_local_map, name, toggle, monkeypatch):
        import config
        from core.chat import function_manager as fm
        monkeypatch.setattr(config, 'PRIVATE_ALLOW_UNFLAGGED_TOOLS', toggle,
                            raising=False)
        tok = fm.scope_private.set(True)
        try:
            return self._gate(is_local_map)._check_privacy_allowed(name)
        finally:
            fm.scope_private.reset(tok)

    def test_unflagged_blocked_by_default(self, monkeypatch):
        ok, msg = self._check({}, 'mystery_tool', False, monkeypatch)
        assert not ok
        assert 'no locality flag' in msg
        assert 'Settings > Privacy' in msg   # the hint that the toggle exists

    def test_unflagged_allowed_with_toggle(self, monkeypatch):
        ok, msg = self._check({}, 'mystery_tool', True, monkeypatch)
        assert ok and msg is None

    def test_declared_nonlocal_blocked_despite_toggle(self, monkeypatch):
        ok, _ = self._check({'web_tool': False}, 'web_tool', True, monkeypatch)
        assert not ok
        ok, _ = self._check({'ep_tool': 'endpoint'}, 'ep_tool', True, monkeypatch)
        assert not ok

    def test_declared_local_allowed_regardless(self, monkeypatch):
        ok, _ = self._check({'local_tool': True}, 'local_tool', False, monkeypatch)
        assert ok

    def test_public_chat_never_gated(self, monkeypatch):
        import config
        from core.chat import function_manager as fm
        monkeypatch.setattr(config, 'PRIVATE_ALLOW_UNFLAGGED_TOOLS', False,
                            raising=False)
        assert fm.scope_private.get() is False
        ok, _ = self._gate({})._check_privacy_allowed('mystery_tool')
        assert ok


if __name__ == '__main__':
    pytest.main([__file__, '-v'])