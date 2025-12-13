"""
Sapphire Prompt System - Potemkin File

This module maintains backward compatibility by re-exporting all components
from the refactored prompt system:
  - prompt_manager: Core PromptManager class, JSON loading, hot-reload
  - prompt_state: Runtime state management, component manipulation
  - prompt_crud: CRUD operations, user prompt persistence

All original imports continue to work unchanged.
"""

# Import the core manager and singleton
from .prompt_manager import PromptManager, prompt_manager

# Import all state management functions and variables
from .prompt_state import (
    # Backward compatibility exports
    PROMPT_COMPONENTS,
    SCENARIO_PRESETS,
    MONOLITHS,
    SPICE_POOL,
    _assembled_state,
    _user_prompts,
    
    # State management functions
    get_current_state,
    get_active_preset_name,
    set_active_preset_name,
    get_prompt_char_count,
    get_current_prompt,
    generate_random_assembled,
    reset_to_defaults,
    set_random_spice,
    clear_spice,
    assemble_prompt,
    is_assembled_mode,
    set_component,
    remove_extra,
    remove_emotion,
    clear_extras,
    clear_emotions,
    get_assembled_state,
    apply_scenario,
    apply_random_assembled
)

# Import all CRUD functions
from .prompt_crud import (
    list_prompts,
    get_prompt,
    save_prompt,
    delete_prompt,
    load_user_prompts,
    reload
)

# Initialize user prompts on module load
load_user_prompts()

# Export everything for star imports
__all__ = [
    # Classes
    'PromptManager',
    'prompt_manager',
    
    # Backward compatibility
    'PROMPT_COMPONENTS',
    'SCENARIO_PRESETS',
    'MONOLITHS',
    'SPICE_POOL',
    '_assembled_state',
    '_user_prompts',
    
    # State functions
    'get_current_state',
    'get_active_preset_name',
    'set_active_preset_name',
    'get_prompt_char_count',
    'get_current_prompt',
    'generate_random_assembled',
    'reset_to_defaults',
    'set_random_spice',
    'clear_spice',
    'assemble_prompt',
    'is_assembled_mode',
    'set_component',
    'remove_extra',
    'remove_emotion',
    'clear_extras',
    'clear_emotions',
    'get_assembled_state',
    'apply_scenario',
    'apply_random_assembled',
    
    # CRUD functions
    'list_prompts',
    'get_prompt',
    'save_prompt',
    'delete_prompt',
    'load_user_prompts',
    'reload'
]