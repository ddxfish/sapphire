"""
Sapphire Prompt System - Potemkin File

This module maintains backward compatibility by re-exporting all components
from the refactored prompt system:
  - prompt_manager: Core PromptManager class, JSON loading, hot-reload
  - prompt_state: Runtime state management, component manipulation
  - prompt_crud: CRUD operations, single prompt resolver

All original imports continue to work unchanged.
"""

# Import the core manager and singleton
from .prompt_manager import PromptManager, prompt_manager

# Import all state management functions and variables
from .prompt_state import (
    _assembled_state,

    # State management functions
    get_current_state,
    get_active_preset_name,
    set_active_preset_name,
    get_prompt_char_count,
    get_current_prompt,
    is_current_prompt_private,
    reset_to_defaults,
    set_random_spice,
    clear_spice,
    get_current_spice,
    get_next_spice,
    invalidate_spice_picks,
    assemble_prompt,
    is_assembled_mode,
    get_prompt_mode,
    set_transient_piece,
    remove_transient_piece,
    clear_transients,
    expire_transients,
    get_transients,
    apply_scenario,
)

# Import all CRUD functions
from .prompt_crud import (
    list_prompts,
    hidden_prompt_kinds,
    visible_components,
    get_prompt,
    save_prompt,
    delete_prompt,
    save_component,
    delete_component,
    save_components_batch,
    is_vault_prompt,
    VAULT_LOCKED_MSG,
    activate_prompt,
    revalidate_active,
)

# Initialize first spice on module load
set_random_spice()

# Export everything for star imports
__all__ = [
    # Classes
    'PromptManager',
    'prompt_manager',

    # Backward compatibility
    '_assembled_state',

    # State functions
    'get_current_state',
    'get_active_preset_name',
    'set_active_preset_name',
    'get_prompt_char_count',
    'get_current_prompt',
    'is_current_prompt_private',
    'reset_to_defaults',
    'set_random_spice',
    'clear_spice',
    'get_current_spice',
    'get_next_spice',
    'invalidate_spice_picks',
    'assemble_prompt',
    'is_assembled_mode',
    'get_prompt_mode',
    'set_transient_piece',
    'remove_transient_piece',
    'clear_transients',
    'expire_transients',
    'get_transients',
    'apply_scenario',

    # CRUD functions
    'list_prompts',
    'hidden_prompt_kinds',
    'visible_components',
    'get_prompt',
    'save_prompt',
    'delete_prompt',
    'save_component',
    'delete_component',
    'save_components_batch',
    'is_vault_prompt',
    'VAULT_LOCKED_MSG',
    'activate_prompt',
    'revalidate_active',
]
