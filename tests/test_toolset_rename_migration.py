# tests/test_toolset_rename_migration.py - _migrate_renamed_tools (core/toolsets/toolset_manager.py)
#
# The precedent (the a365e37 meta-tool rename) never had a test. Agents v2
# (2026-10-06) made the map one-to-many: any one of the old agent / trinity /
# code_session tool names grants all four new agent tools, so a toolset that
# could only spawn can also peek and answer.
from core.toolsets.toolset_manager import ToolsetManager

FOUR = ['agent_list', 'agent_peek', 'agent_spawn', 'agent_action']


def _mgr(toolsets):
    m = ToolsetManager.__new__(ToolsetManager)
    m._toolsets = toolsets
    return m


def test_one_old_agent_tool_grants_all_four_in_order_without_repeats():
    m = _mgr({'sapphire': {'functions': ['web_search', 'spawn_agent', 'check_agents', 'save_memory', 'trinity_open']},
              'simple': {'functions': ['code_session']},
              'untouched': {'functions': ['web_search', 'custom_user_tool']}})
    assert m._migrate_renamed_tools() is True
    assert m._toolsets['sapphire']['functions'] == ['web_search'] + FOUR + ['save_memory']
    assert m._toolsets['simple']['functions'] == FOUR
    assert m._toolsets['untouched']['functions'] == ['web_search', 'custom_user_tool']


def test_the_old_one_to_one_renames_still_apply_and_a_clean_file_reports_no_change():
    m = _mgr({'t': {'functions': ['view_prompt', 'set_piece', 'remove_piece', 'web_search']}})
    assert m._migrate_renamed_tools() is True
    assert m._toolsets['t']['functions'] == ['prompt_view', 'prompt_pieces', 'web_search']
    assert m._migrate_renamed_tools() is False


def test_non_list_functions_are_left_alone():
    m = _mgr({'odd': {'functions': 'all'}, 'noneish': {}})
    assert m._migrate_renamed_tools() is False
