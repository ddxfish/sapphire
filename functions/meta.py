# functions/meta.py
"""
Meta tools for AI to inspect/modify its own system prompt.
"""

import logging
import requests
import uuid
import config as app_config

logger = logging.getLogger(__name__)

ENABLED = True

AVAILABLE_FUNCTIONS = [
    'get_system_prompt',
    'set_full_system_prompt',
    'load_system_prompt_piece',
    'new_system_prompt_piece',
    'activate_prompt_by_name',
    'add_prompt_emotion',
    'remove_prompt_emotion',
    'create_prompt_emotion',
    'add_prompt_extra',
    'remove_prompt_extra',
    'create_prompt_extra',
    'end_and_reset_chat',
]

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_system_prompt",
            "description": "View a system prompt. Without parameters, shows your current active prompt. With a name, shows that specific prompt.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Optional: name of prompt to view. If omitted, shows current active prompt."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_full_system_prompt",
            "description": "Create a new system prompt with custom content and activate it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name for this prompt (lowercase, no spaces). Auto-generated if not provided."
                    },
                    "content": {
                        "type": "string",
                        "description": "The complete system prompt text."
                    }
                },
                "required": ["content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "load_system_prompt_piece",
            "description": "Load a prompt piece into your assembled prompt by component type and key name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "component": {
                        "type": "string",
                        "description": "Component type: persona, location, relationship, goals, format, scenario"
                    },
                    "key": {
                        "type": "string",
                        "description": "The key name of the piece to load"
                    }
                },
                "required": ["component", "key"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "new_system_prompt_piece",
            "description": "Create a new reusable prompt piece, save it to the library, and activate it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "component": {
                        "type": "string",
                        "description": "Component type: persona, location, relationship, goals, format, scenario"
                    },
                    "key": {
                        "type": "string",
                        "description": "Short identifier for this piece (lowercase, no spaces)"
                    },
                    "value": {
                        "type": "string",
                        "description": "The text content of this piece"
                    }
                },
                "required": ["component", "key", "value"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "activate_prompt_by_name",
            "description": "Switch to a different system prompt by name. Must specify a valid prompt name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The name of the prompt to activate"
                    }
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_prompt_emotion",
            "description": "Add an emotion to your current prompt. Without key parameter, lists available emotions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Optional: emotion key to add. Omit to list available emotions."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "remove_prompt_emotion",
            "description": "Remove an emotion from your current prompt.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "The emotion key to remove"
                    }
                },
                "required": ["key"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_prompt_emotion",
            "description": "Create a new emotion, save it to the library, and add it to your current prompt.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Short identifier for this emotion (lowercase, no spaces)"
                    },
                    "value": {
                        "type": "string",
                        "description": "The emotion description text"
                    }
                },
                "required": ["key", "value"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_prompt_extra",
            "description": "Add an extra to your current prompt. Without key parameter, lists available extras.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Optional: extra key to add. Omit to list available extras."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "remove_prompt_extra",
            "description": "Remove an extra from your current prompt.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "The extra key to remove"
                    }
                },
                "required": ["key"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_prompt_extra",
            "description": "Create a new extra, save it to the library, and add it to your current prompt.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Short identifier for this extra (lowercase, no spaces)"
                    },
                    "value": {
                        "type": "string",
                        "description": "The extra description text"
                    }
                },
                "required": ["key", "value"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "end_and_reset_chat",
            "description": "Terminate and delete the current chat history.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "The reason for ending and resetting the chat."
                    }
                },
                "required": ["reason"]
            }
        }
    }
]


def _get_api_headers():
    """Get headers with API key for internal requests."""
    from core.setup import get_password_hash
    api_key = get_password_hash()
    return {
        'Content-Type': 'application/json',
        'X-API-Key': api_key or ''
    }


def _normalize_component(component: str) -> str:
    """Normalize component name: lowercase, strip, handle plurals."""
    if not component:
        return ""
    
    c = component.lower().strip()
    c = ''.join(ch for ch in c if ch.isalnum())
    
    mappings = {
        'goal': 'goals',
        'emotion': 'emotions',
        'extra': 'extras',
        'locations': 'location',
        'personas': 'persona',
        'relationships': 'relationship',
        'formats': 'format',
        'scenarios': 'scenario',
    }
    
    return mappings.get(c, c)


def _is_assembled_prompt() -> bool:
    """Check if current prompt is assembled type (not monolith)."""
    from core.modules.system import prompts
    
    preset_name = prompts.get_active_preset_name()
    if not preset_name:
        return False
    
    prompt_data = prompts.get_prompt(preset_name)
    if isinstance(prompt_data, dict):
        return prompt_data.get('type') == 'assembled'
    return False


def _require_assembled_prompt() -> tuple | None:
    """
    Check if current prompt is assembled. 
    Returns error tuple if monolith, None if assembled (OK to proceed).
    """
    if not _is_assembled_prompt():
        return (
            "This function only works with ASSEMBLED prompts, not monoliths. "
            "Switch to an assembled prompt first using activate_prompt_by_name, "
            "or create a new assembled prompt with set_full_system_prompt.",
            False
        )
    return None


def _normalize_name(name: str) -> str:
    """Normalize prompt/key name: lowercase, strip, remove punctuation."""
    if not name:
        return ""
    n = name.lower().strip()
    n = ''.join(ch for ch in n if ch.isalnum() or ch in '_- ')
    n = n.replace(' ', '_').replace('-', '_')
    return n


def _get_current_preset_name() -> str:
    """Get current preset name, preferring existing non-generic names."""
    from core.modules.system import prompts
    from core.modules.system.prompt_state import _assembled_state
    
    current = prompts.get_active_preset_name()
    if current and current not in ['assembled', 'unknown', 'random', '']:
        return current
    
    return _assembled_state.get('persona', 'custom')


def _save_and_activate_assembled(preset_name: str, headers: dict, api_url: str) -> tuple:
    """Save current _assembled_state as a scenario preset and activate it."""
    from core.modules.system import prompts
    from core.modules.system.prompt_state import _assembled_state
    
    components = {}
    for key in ['persona', 'location', 'relationship', 'goals', 'format', 'scenario']:
        if key in _assembled_state and _assembled_state[key]:
            components[key] = _assembled_state[key]
    
    if _assembled_state.get('extras'):
        components['extras'] = _assembled_state['extras'].copy()
    if _assembled_state.get('emotions'):
        components['emotions'] = _assembled_state['emotions'].copy()
    
    try:
        response = requests.put(
            f"{api_url}/api/prompts/{preset_name}",
            json={"type": "assembled", "components": components},
            headers=headers,
            timeout=5
        )
        if response.status_code != 200:
            return False, f"Failed to save preset: {response.text}"
    except Exception as e:
        return False, f"Failed to save preset: {e}"
    
    try:
        response = requests.post(
            f"{api_url}/api/prompts/{preset_name}/load",
            headers=headers,
            timeout=5
        )
        if response.status_code != 200:
            return False, f"Saved but failed to activate: {response.text}"
    except Exception as e:
        return False, f"Saved but failed to activate: {e}"
    
    return True, "OK"


def _build_status_string(preset_name: str) -> str:
    """Build status string like 'albert(556): albert, survive, mars'."""
    from core.modules.system import prompts
    from core.modules.system.prompt_state import _assembled_state
    
    prompt_data = prompts.get_current_prompt()
    content = prompt_data.get('content') if isinstance(prompt_data, dict) else str(prompt_data)
    char_count = len(content)
    
    pieces = []
    persona = _assembled_state.get('persona', '')
    if persona:
        pieces.append(persona)
    
    for k in ['goals', 'location', 'scenario']:
        v = _assembled_state.get(k)
        if v and v not in ['default', 'none', '']:
            pieces.append(v)
    
    pieces.extend(_assembled_state.get('extras', []))
    pieces.extend(_assembled_state.get('emotions', []))
    
    pieces_str = ', '.join(p for p in pieces if p)
    return f"{preset_name}({char_count}): {pieces_str}"


def _handle_list_component(component_type: str) -> tuple:
    """List available items for a component type."""
    from core.modules.system import prompts
    
    if component_type not in prompts.prompt_manager._components:
        return f"No {component_type} available.", True
    
    items = prompts.prompt_manager._components[component_type]
    if not items:
        return f"No {component_type} available.", True
    
    lines = [f"Available {component_type}:"]
    for key, value in items.items():
        preview = value[:60] + '...' if len(value) > 60 else value
        lines.append(f"  {key}: {preview}")
    
    return '\n'.join(lines), True


def _handle_add_list_component(component_type: str, key: str, headers: dict, api_url: str) -> tuple:
    """Add an existing component to current prompt (for extras/emotions)."""
    from core.modules.system import prompts
    from core.modules.system.prompt_state import _assembled_state
    
    key = _normalize_name(key)
    
    if component_type not in prompts.prompt_manager._components:
        return f"Component type '{component_type}' not found.", False
    
    if key not in prompts.prompt_manager._components[component_type]:
        return f"'{key}' not found in {component_type}.", False
    
    if key in _assembled_state.get(component_type, []):
        return f"'{key}' already in your {component_type}.", True
    
    if component_type not in _assembled_state:
        _assembled_state[component_type] = []
    _assembled_state[component_type].append(key)
    
    preset_name = _get_current_preset_name()
    success, msg = _save_and_activate_assembled(preset_name, headers, api_url)
    
    if not success:
        return msg, False
    
    status = _build_status_string(preset_name)
    return f"Added {component_type[:-1]} '{key}'. {status}", True


def _handle_remove_list_component(component_type: str, key: str, headers: dict, api_url: str) -> tuple:
    """Remove a component from current prompt (for extras/emotions)."""
    from core.modules.system.prompt_state import _assembled_state
    
    key = _normalize_name(key)
    
    if component_type not in _assembled_state or key not in _assembled_state[component_type]:
        return f"'{key}' not in your current {component_type}.", False
    
    _assembled_state[component_type].remove(key)
    
    preset_name = _get_current_preset_name()
    success, msg = _save_and_activate_assembled(preset_name, headers, api_url)
    
    if not success:
        return msg, False
    
    status = _build_status_string(preset_name)
    return f"Removed {component_type[:-1]} '{key}'. {status}", True


def _handle_create_list_component(component_type: str, key: str, value: str, headers: dict, api_url: str) -> tuple:
    """Create a new component and add it to current prompt."""
    from core.modules.system import prompts
    from core.modules.system.prompt_state import _assembled_state
    
    key = _normalize_name(key)
    
    if not key or not value:
        return "Both key and value are required.", False
    
    # Save to library via API
    response = requests.put(
        f"{api_url}/api/prompts/components/{component_type}/{key}",
        json={"value": value},
        headers=headers,
        timeout=5
    )
    
    if response.status_code != 200:
        return f"Failed to save: {response.text}", False
    
    # Reload components
    prompts.prompt_manager._load_pieces()
    
    # Add to assembled state
    if component_type not in _assembled_state:
        _assembled_state[component_type] = []
    if key not in _assembled_state[component_type]:
        _assembled_state[component_type].append(key)
    
    preset_name = _get_current_preset_name()
    success, msg = _save_and_activate_assembled(preset_name, headers, api_url)
    
    if not success:
        return msg, False
    
    status = _build_status_string(preset_name)
    return f"Created and added {component_type[:-1]} '{key}'. {status}", True


def execute(function_name, arguments, config):
    """Execute meta-related functions."""
    main_api_url = app_config.API_URL
    headers = _get_api_headers()
    
    try:
        if function_name == "get_system_prompt":
            from core.modules.system import prompts
            
            name = arguments.get('name')
            
            if not name:
                prompt_data = prompts.get_current_prompt()
                content = prompt_data.get('content') if isinstance(prompt_data, dict) else str(prompt_data)
                active_name = prompts.get_active_preset_name()
                return f"[Active: {active_name}]\n\n{content}", True
            
            name = _normalize_name(name)
            prompt_data = prompts.get_prompt(name)
            
            if not prompt_data:
                return f"Prompt '{name}' not found.", False
            
            content = prompt_data.get('content') if isinstance(prompt_data, dict) else str(prompt_data)
            prompt_type = prompt_data.get('type', 'unknown') if isinstance(prompt_data, dict) else 'monolith'
            return f"[{name} - {prompt_type}]\n\n{content}", True

        elif function_name == "set_full_system_prompt":
            content = arguments.get('content')
            if not content:
                return "Content is required.", False
            
            name = _normalize_name(arguments.get('name', ''))
            if not name:
                name = f"ai_prompt_{uuid.uuid4().hex[:6]}"
            
            response = requests.put(
                f"{main_api_url}/api/prompts/{name}",
                json={"type": "monolith", "content": content},
                headers=headers,
                timeout=5
            )
            
            if response.status_code != 200:
                return f"Failed to save prompt: {response.text}", False
            
            response = requests.post(
                f"{main_api_url}/api/prompts/{name}/load",
                headers=headers,
                timeout=5
            )
            
            if response.status_code != 200:
                return f"Saved but failed to activate: {response.text}", False
            
            return f"Created and activated prompt '{name}'.", True

        elif function_name == "load_system_prompt_piece":
            component = arguments.get('component')
            key = arguments.get('key')
            
            if not component or not key:
                return "Both component and key are required.", False
            
            # Check if using assembled prompt
            if err := _require_assembled_prompt():
                return err
            
            component = _normalize_component(component)
            key = _normalize_name(key)
            
            valid_components = ['persona', 'location', 'relationship', 'goals', 'format', 'scenario']
            if component not in valid_components:
                return f"Invalid component '{component}'.", False
            
            from core.modules.system import prompts
            from core.modules.system.prompt_state import _assembled_state
            
            if component not in prompts.prompt_manager._components:
                return f"Component type '{component}' not found.", False
            
            if key not in prompts.prompt_manager._components[component]:
                return f"Piece '{key}' not found in {component}.", False
            
            _assembled_state[component] = key
            
            preset_name = _get_current_preset_name()
            success, msg = _save_and_activate_assembled(preset_name, headers, main_api_url)
            
            if not success:
                return msg, False
            
            status = _build_status_string(preset_name)
            return f"Loaded {component}='{key}'. {status}", True

        elif function_name == "new_system_prompt_piece":
            component = arguments.get('component')
            key = arguments.get('key')
            value = arguments.get('value')
            
            if not component or not key or not value:
                return "Component, key, and value are all required.", False
            
            # Check if using assembled prompt
            if err := _require_assembled_prompt():
                return err
            
            component = _normalize_component(component)
            key = _normalize_name(key)
            
            valid_components = ['persona', 'location', 'relationship', 'goals', 'format', 'scenario']
            if component not in valid_components:
                return f"Invalid component '{component}'.", False
            
            response = requests.put(
                f"{main_api_url}/api/prompts/components/{component}/{key}",
                json={"value": value},
                headers=headers,
                timeout=5
            )
            
            if response.status_code != 200:
                return f"Failed to save piece: {response.text}", False
            
            from core.modules.system import prompts
            from core.modules.system.prompt_state import _assembled_state
            
            prompts.prompt_manager._load_pieces()
            _assembled_state[component] = key
            
            preset_name = _get_current_preset_name()
            success, msg = _save_and_activate_assembled(preset_name, headers, main_api_url)
            
            if not success:
                return msg, False
            
            status = _build_status_string(preset_name)
            return f"Created {component}='{key}'. {status}", True

        elif function_name == "activate_prompt_by_name":
            name = arguments.get('name')
            
            if not name:
                return "A prompt name is required.", False
            
            name = _normalize_name(name)
            
            from core.modules.system import prompts
            prompt_data = prompts.get_prompt(name)
            
            if not prompt_data:
                return f"Prompt '{name}' not found.", False
            
            response = requests.post(
                f"{main_api_url}/api/prompts/{name}/load",
                headers=headers,
                timeout=5
            )
            
            if response.status_code != 200:
                return f"Failed to activate '{name}': {response.text}", False
            
            return f"Activated prompt '{name}'.", True

        # Emotion functions
        elif function_name == "add_prompt_emotion":
            if err := _require_assembled_prompt():
                return err
            key = arguments.get('key')
            if not key:
                return _handle_list_component('emotions')
            return _handle_add_list_component('emotions', key, headers, main_api_url)

        elif function_name == "remove_prompt_emotion":
            if err := _require_assembled_prompt():
                return err
            key = arguments.get('key')
            if not key:
                return "Emotion key is required.", False
            return _handle_remove_list_component('emotions', key, headers, main_api_url)

        elif function_name == "create_prompt_emotion":
            if err := _require_assembled_prompt():
                return err
            key = arguments.get('key')
            value = arguments.get('value')
            if not key or not value:
                return "Both key and value are required.", False
            return _handle_create_list_component('emotions', key, value, headers, main_api_url)

        # Extra functions
        elif function_name == "add_prompt_extra":
            if err := _require_assembled_prompt():
                return err
            key = arguments.get('key')
            if not key:
                return _handle_list_component('extras')
            return _handle_add_list_component('extras', key, headers, main_api_url)

        elif function_name == "remove_prompt_extra":
            if err := _require_assembled_prompt():
                return err
            key = arguments.get('key')
            if not key:
                return "Extra key is required.", False
            return _handle_remove_list_component('extras', key, headers, main_api_url)

        elif function_name == "create_prompt_extra":
            if err := _require_assembled_prompt():
                return err
            key = arguments.get('key')
            value = arguments.get('value')
            if not key or not value:
                return "Both key and value are required.", False
            return _handle_create_list_component('extras', key, value, headers, main_api_url)

        elif function_name == "end_and_reset_chat":
            reason = arguments.get('reason')
            if not reason:
                return "A reason is required.", False
            
            logger.info(f"AI INITIATED CHAT RESET - Reason: {reason}")
            
            response = requests.delete(
                f"{main_api_url}/history/messages",
                json={"count": -1},
                headers=headers,
                timeout=5
            )
            response.raise_for_status()
            
            return f"Chat history reset. Reason: {reason}", True

        else:
            return f"Unknown function: {function_name}", False

    except requests.exceptions.RequestException as e:
        logger.error(f"API request failed for '{function_name}': {e}")
        return f"API request failed: {str(e)}", False
    except Exception as e:
        logger.error(f"Meta function error for '{function_name}': {e}", exc_info=True)
        return f"Error in {function_name}: {str(e)}", False