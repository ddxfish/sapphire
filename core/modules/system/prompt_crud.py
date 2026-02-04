import logging
import json
from pathlib import Path
from .prompt_manager import prompt_manager
from . import prompt_state

logger = logging.getLogger(__name__)


def _get_prompts_dir():
    """Get user prompts directory (same as prompt_manager uses)."""
    return prompt_manager.USER_DIR


def prompt_name_exists(name: str, exclude_type: str = None) -> tuple[bool, str]:
    """
    Check if prompt name exists in any storage.
    
    Args:
        name: Prompt name to check
        exclude_type: Optional type to exclude from check ('monolith' or 'assembled')
    
    Returns:
        (exists: bool, existing_type: str or None)
    """
    # Check monoliths
    if exclude_type != 'monolith':
        if name in prompt_manager.monoliths:
            return True, 'monolith'
    
    # Check scenario presets (assembled)
    if exclude_type != 'assembled':
        if name in prompt_manager.scenario_presets:
            return True, 'assembled'
    
    # Check user prompts cache
    if name in prompt_state._user_prompts:
        user_type = prompt_state._user_prompts[name].get('type', 'unknown')
        if exclude_type != user_type:
            return True, user_type
    
    return False, None


def list_prompts():
    """List all available prompts (monoliths + scenario presets + user-created)."""
    all_prompts = []
    
    # Get monolith prompts
    if prompt_manager.monoliths:
        all_prompts.extend(list(prompt_manager.monoliths.keys()))
    
    # Get scenario presets (assembled prompts)
    if prompt_manager.scenario_presets:
        all_prompts.extend(list(prompt_manager.scenario_presets.keys()))
    
    # Get user-created prompts
    all_prompts.extend(list(prompt_state._user_prompts.keys()))
    
    # Remove duplicates and filter out internal keys
    all_prompts = [p for p in set(all_prompts) if not p.startswith('_')]
    
    return sorted(all_prompts)


def get_prompt(name: str):
    """Get a prompt by name and return it with 'content' always present."""
    # Check user prompts first
    if name in prompt_state._user_prompts:
        prompt_data = prompt_state._user_prompts[name]
        if prompt_data.get('type') == 'assembled':
            assembled_text = prompt_manager.assemble_from_components(prompt_data['components'])
            return {
                'name': name,
                'type': 'assembled',
                'components': prompt_data['components'],
                'content': assembled_text
            }
        return prompt_data
    
    # Check monoliths
    if name in prompt_manager.monoliths:
        mono = prompt_manager.monoliths[name]
        return {
            'name': name,
            'type': 'monolith',
            'content': mono.get('content', ''),
            'privacy_required': mono.get('privacy_required', False)
        }
    
    # Check scenario presets
    if name in prompt_manager.scenario_presets:
        components = prompt_manager.scenario_presets[name]
        privacy_required = components.get('_privacy_required', False)
        # Filter out metadata for assembly
        clean_components = {k: v for k, v in components.items() if not k.startswith('_')}
        assembled_text = prompt_manager.assemble_from_components(clean_components)
        return {
            'name': name,
            'type': 'assembled',
            'components': clean_components,
            'content': assembled_text,
            'privacy_required': privacy_required
        }
    
    return None


def save_prompt(name: str, data: dict, allow_overwrite: bool = True) -> tuple[bool, str]:
    """
    Save a prompt - updates user JSON files (monoliths or scenario_presets).
    
    Args:
        name: Prompt name
        data: Prompt data with 'type' and 'content'/'components'
        allow_overwrite: If True, allows overwriting same-type prompts
    
    Returns:
        (success: bool, message: str)
    """
    try:
        prompt_type = data.get('type', 'monolith')
        
        # Check for name collision with opposite type
        if prompt_type == 'monolith':
            if name in prompt_manager.scenario_presets:
                msg = f"Name '{name}' already exists as assembled prompt"
                logger.warning(msg)
                return False, msg
        
        elif prompt_type == 'assembled':
            if name in prompt_manager.monoliths:
                msg = f"Name '{name}' already exists as monolith prompt"
                logger.warning(msg)
                return False, msg
        
        # Proceed with save
        if prompt_type == 'monolith':
            prompt_manager._monoliths[name] = {
                'content': data['content'],
                'privacy_required': data.get('privacy_required', False)
            }
            prompt_manager.save_monoliths()
            logger.info(f"Saved monolith '{name}'")
            return True, f"Saved monolith '{name}'"

        elif prompt_type == 'assembled':
            components = data['components'].copy()
            # Store privacy_required at top level of preset
            components['_privacy_required'] = data.get('privacy_required', False)
            prompt_manager._scenario_presets[name] = components
            prompt_manager.save_scenario_presets()
            logger.info(f"Saved assembled prompt '{name}'")
            return True, f"Saved assembled '{name}'"
        
        else:
            msg = f"Unknown prompt type: {prompt_type}"
            logger.error(msg)
            return False, msg
            
    except Exception as e:
        logger.error(f"Failed to save prompt '{name}': {e}")
        import traceback
        traceback.print_exc()
        return False, str(e)


def delete_prompt(name: str) -> bool:
    """Delete a prompt from storage."""
    try:
        deleted = False
        
        # Delete from monoliths if present
        if name in prompt_manager.monoliths:
            del prompt_manager._monoliths[name]
            prompt_manager.save_monoliths()
            logger.info(f"Deleted monolith '{name}'")
            deleted = True
        
        # Delete from scenario_presets if present
        if name in prompt_manager.scenario_presets:
            del prompt_manager._scenario_presets[name]
            prompt_manager.save_scenario_presets()
            logger.info(f"Deleted assembled prompt '{name}'")
            deleted = True
        
        # Delete from user_prompts cache if present
        if name in prompt_state._user_prompts:
            del prompt_state._user_prompts[name]
            deleted = True
        
        # Delete individual file if it exists (legacy support)
        prompts_dir = _get_prompts_dir()
        file_path = prompts_dir / f"{name}.json"
        if file_path.exists():
            file_path.unlink()
            logger.info(f"Deleted prompt file '{name}.json'")
            deleted = True
        
        if not deleted:
            logger.warning(f"Prompt '{name}' not found in any storage")
            return False
        
        return True
        
    except Exception as e:
        logger.error(f"Failed to delete prompt '{name}': {e}")
        return False


def load_user_prompts():
    """Load all user-created prompts from disk (legacy individual files)."""
    prompt_state._user_prompts = {}
    
    # System JSON files to skip
    SYSTEM_FILES = {
        'prompt_monoliths.json', 
        'prompt_pieces.json', 
        'prompt_spices.json'
    }
    
    try:
        prompts_dir = _get_prompts_dir()
        
        for file_path in prompts_dir.glob('*.json'):
            if file_path.name in SYSTEM_FILES:
                continue
                
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    name = data.get('name', file_path.stem)
                    prompt_state._user_prompts[name] = data
                    logger.info(f"Loaded user prompt: {name}")
            except Exception as e:
                logger.error(f"Failed to load prompt from {file_path}: {e}")
    except Exception as e:
        logger.error(f"Error loading user prompts: {e}")


def reload():
    """Reload all prompts from disk."""
    load_user_prompts()
    prompt_manager.reload()
    logger.info("Prompts reloaded")