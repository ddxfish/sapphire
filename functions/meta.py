# functions/meta.py
"""
Meta tools for AI to inspect/modify its own system prompt and settings.
Tools are dynamically filtered based on prompt mode (monolith vs assembled).
"""

import logging
import requests
import config as app_config

logger = logging.getLogger(__name__)

ENABLED = True

AVAILABLE_FUNCTIONS = [
    'view_prompt',
    'switch_prompt',
    'reset_chat',
    'edit_prompt',
    'set_piece',
    'remove_piece',
    'create_piece',
    'list_pieces',
    'change_ai_name',
    'change_username',
    'set_tts_voice',
    'list_tools',
]

# Mode-based filtering - function_manager uses this to show/hide tools
MODE_FILTER = {
    "monolith": ['view_prompt', 'switch_prompt', 'reset_chat', 'edit_prompt', 'change_ai_name', 'change_username', 'set_tts_voice', 'list_tools'],
    "assembled": ['view_prompt', 'switch_prompt', 'reset_chat', 'set_piece', 'remove_piece', 'create_piece', 'list_pieces', 'change_ai_name', 'change_username', 'set_tts_voice', 'list_tools'],
}

# Available TTS voices (prefix: am=American Male, af=American Female, bm=British Male, bf=British Female)
TTS_VOICES = [
    'am_adam', 'am_eric', 'am_liam', 'am_michael',
    'af_bella', 'af_nicole', 'af_heart', 'af_jessica', 'af_sarah', 'af_river', 'af_sky',
    'bf_emma', 'bf_isabella', 'bf_alice', 'bf_lily',
    'bm_george', 'bm_daniel', 'bm_lewis',
]

TOOLS = [
    # === Universal tools (both modes) ===
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "view_prompt",
            "description": "View a system prompt. Without name, shows current active prompt.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Optional: name of prompt to view"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "switch_prompt",
            "description": "Switch to a different system prompt. Without name, lists available prompts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Optional: name of prompt to activate"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "reset_chat",
            "description": "Clear all chat history and start fresh.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Reason for resetting"
                    }
                },
                "required": ["reason"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "change_ai_name",
            "description": "Change your AI name. Updates the setting used in prompts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Your new name"
                    }
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "change_username",
            "description": "Change the user's name. Updates the setting used in prompts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The user's new name"
                    }
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "set_tts_voice",
            "description": "Set TTS voice. Without name, lists current/available voices. Prefix indicates accent/gender: am=American Male, af=American Female, bm=British Male, bf=British Female.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Optional: voice name to set"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "list_tools",
            "description": "List available tools. Without scope, shows currently enabled tools. Use 'all' to see every tool including inactive ones.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": ["enabled", "all"],
                        "description": "Optional: 'enabled' (default) or 'all'"
                    }
                },
                "required": []
            }
        }
    },
    # === Monolith-only tools ===
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "edit_prompt",
            "description": "Replace the content of the current monolith prompt.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "New prompt content"
                    }
                },
                "required": ["content"]
            }
        }
    },
    # === Assembled-only tools ===
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "set_piece",
            "description": "Set a prompt component. For persona/location/goals/etc: replaces value. For emotions/extras: adds to list.",
            "parameters": {
                "type": "object",
                "properties": {
                    "component": {
                        "type": "string",
                        "description": "Component type: persona, location, relationship, goals, format, scenario, emotions, extras"
                    },
                    "key": {
                        "type": "string",
                        "description": "The piece key to set/add"
                    }
                },
                "required": ["component", "key"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "remove_piece",
            "description": "Remove a piece from emotions or extras list.",
            "parameters": {
                "type": "object",
                "properties": {
                    "component": {
                        "type": "string",
                        "description": "Component type: emotions or extras"
                    },
                    "key": {
                        "type": "string",
                        "description": "The piece key to remove"
                    }
                },
                "required": ["component", "key"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "create_piece",
            "description": "Create a new prompt piece, save to library, and activate it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "component": {
                        "type": "string",
                        "description": "Component type: persona, location, relationship, goals, format, scenario, emotions, extras"
                    },
                    "key": {
                        "type": "string",
                        "description": "Short identifier (lowercase, no spaces)"
                    },
                    "value": {
                        "type": "string",
                        "description": "The text content"
                    }
                },
                "required": ["component", "key", "value"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "list_pieces",
            "description": "List available pieces for a component type.",
            "parameters": {
                "type": "object",
                "properties": {
                    "component": {
                        "type": "string",
                        "description": "Component type: persona, location, relationship, goals, format, scenario, emotions, extras"
                    }
                },
                "required": ["component"]
            }
        }
    },
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


def _normalize_name(name: str) -> str:
    """Normalize prompt/key name: lowercase, strip, clean punctuation."""
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


def _save_and_activate_assembled(preset_name: str, headers: dict, api_url: str) -> tuple:
    """Save current _assembled_state as a preset and activate it."""
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


def _is_list_component(component: str) -> bool:
    """Check if component is a list type (emotions/extras)."""
    return component in ['emotions', 'extras']


def _get_active_chat(headers: dict, api_url: str) -> str:
    """Get the active chat name."""
    try:
        response = requests.get(
            f"{api_url}/chats/active",
            headers=headers,
            timeout=5
        )
        if response.status_code == 200:
            return response.json().get('active_chat', 'default')
    except Exception as e:
        logger.error(f"Failed to get active chat: {e}")
    return 'default'


def _update_chat_setting(setting_key: str, value, headers: dict, api_url: str) -> tuple:
    """Update a single chat setting (voice, pitch, speed)."""
    chat_name = _get_active_chat(headers, api_url)
    
    try:
        response = requests.put(
            f"{api_url}/chats/{chat_name}/settings",
            json={"settings": {setting_key: value}},
            headers=headers,
            timeout=5
        )
        if response.status_code != 200:
            return False, f"Failed to update {setting_key}: {response.text}"
        return True, "OK"
    except Exception as e:
        return False, f"Failed to update {setting_key}: {e}"


def execute(function_name, arguments, config):
    """Execute meta-related functions."""
    main_api_url = app_config.API_URL
    headers = _get_api_headers()
    
    try:
        # === Universal tools ===
        
        if function_name == "view_prompt":
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

        elif function_name == "switch_prompt":
            from core.modules.system import prompts
            
            name = arguments.get('name')
            
            # No name provided - list available prompts
            if not name:
                try:
                    response = requests.get(
                        f"{main_api_url}/api/prompts",
                        headers=headers,
                        timeout=5
                    )
                    if response.status_code == 200:
                        prompt_list = response.json().get('prompts', [])
                        current = prompts.get_active_preset_name()
                        lines = [f"Current: {current}", "Available prompts:"]
                        for p in prompt_list:
                            marker = " *" if p['name'] == current else ""
                            lines.append(f"  {p['name']} ({p['type']}, {p['char_count']} chars){marker}")
                        return '\n'.join(lines), True
                    else:
                        # Fallback to local list
                        available = prompts.list_prompts()
                        current = prompts.get_active_preset_name()
                        return f"Current: {current}\nAvailable: {', '.join(available)}", True
                except Exception as e:
                    logger.error(f"Failed to list prompts: {e}")
                    available = prompts.list_prompts()
                    current = prompts.get_active_preset_name()
                    return f"Current: {current}\nAvailable: {', '.join(available)}", True
            
            name = _normalize_name(name)
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
            
            prompt_type = prompt_data.get('type', 'monolith') if isinstance(prompt_data, dict) else 'monolith'
            return f"Switched to '{name}' ({prompt_type}).", True

        elif function_name == "reset_chat":
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
            
            return f"Chat reset. Reason: {reason}", True

        elif function_name == "change_ai_name":
            name = arguments.get('name', '').strip()
            if not name:
                return "Name is required.", False
            
            response = requests.put(
                f"{main_api_url}/api/settings/DEFAULT_AI_NAME",
                json={"value": name, "persist": True},
                headers=headers,
                timeout=5
            )
            
            if response.status_code != 200:
                return f"Failed to change AI name: {response.text}", False
            
            logger.info(f"AI name changed to: {name}")
            return f"AI name changed to {name}. This will appear in prompts using {{ai_name}}.", True

        elif function_name == "change_username":
            name = arguments.get('name', '').strip()
            if not name:
                return "Name is required.", False
            
            response = requests.put(
                f"{main_api_url}/api/settings/DEFAULT_USERNAME",
                json={"value": name, "persist": True},
                headers=headers,
                timeout=5
            )
            
            if response.status_code != 200:
                return f"Failed to change username: {response.text}", False
            
            logger.info(f"Username changed to: {name}")
            return f"Username changed to {name}. This will appear in prompts using {{user_name}}.", True

        elif function_name == "set_tts_voice":
            name = arguments.get('name', '').strip().lower()
            
            # No name - list available voices with current
            if not name:
                current_voice = "unknown"
                try:
                    chat_name = _get_active_chat(headers, main_api_url)
                    response = requests.get(
                        f"{main_api_url}/chats/{chat_name}/settings",
                        headers=headers,
                        timeout=5
                    )
                    if response.status_code == 200:
                        current_voice = response.json().get('settings', {}).get('voice', 'af_heart')
                except Exception:
                    pass
                
                lines = [f"Current: {current_voice}", "Available voices (prefix: am=American Male, af=American Female, bm=British Male, bf=British Female):"]
                am = [v for v in TTS_VOICES if v.startswith('am_')]
                af = [v for v in TTS_VOICES if v.startswith('af_')]
                bm = [v for v in TTS_VOICES if v.startswith('bm_')]
                bf = [v for v in TTS_VOICES if v.startswith('bf_')]
                lines.append(f"  American Male: {', '.join(am)}")
                lines.append(f"  American Female: {', '.join(af)}")
                lines.append(f"  British Male: {', '.join(bm)}")
                lines.append(f"  British Female: {', '.join(bf)}")
                return '\n'.join(lines), True
            
            if name not in TTS_VOICES:
                return f"Voice '{name}' not found. Use set_tts_voice without params to list available voices.", False
            
            success, msg = _update_chat_setting('voice', name, headers, main_api_url)
            if not success:
                return msg, False
            
            logger.info(f"TTS voice changed to: {name}")
            return f"Voice changed to {name}.", True

        elif function_name == "list_tools":
            scope = arguments.get('scope', 'enabled').lower().strip()
            
            try:
                if scope == 'all':
                    # Get all functions via API
                    response = requests.get(
                        f"{main_api_url}/api/functions",
                        headers=headers,
                        timeout=5
                    )
                    
                    if response.status_code != 200:
                        return f"Failed to get functions: {response.text}", False
                    
                    data = response.json()
                    modules = data.get('modules', {})
                    
                    lines = [f"All tools ({data.get('total_functions', 0)} total):"]
                    
                    for module_name, module_info in sorted(modules.items()):
                        for func in module_info.get('functions', []):
                            name = func['name']
                            desc = func.get('description', '')[:60]
                            if len(func.get('description', '')) > 60:
                                desc += '...'
                            status = "✓" if func.get('enabled') else "✗"
                            lines.append(f"  [{status}] {name}: {desc}")
                    
                    lines.append(f"\n✓ = enabled, ✗ = inactive")
                    return '\n'.join(lines), True
                else:
                    # Get current ability info via API
                    response = requests.get(
                        f"{main_api_url}/api/abilities/current",
                        headers=headers,
                        timeout=5
                    )
                    
                    if response.status_code != 200:
                        return f"Failed to get current ability: {response.text}", False
                    
                    data = response.json()
                    ability_name = data.get('name', 'unknown')
                    enabled_functions = data.get('enabled_functions', [])
                    
                    lines = [f"Enabled tools ({len(enabled_functions)}) - Ability: {ability_name}"]
                    
                    # Get full function details for descriptions
                    func_response = requests.get(
                        f"{main_api_url}/api/functions",
                        headers=headers,
                        timeout=5
                    )
                    
                    desc_map = {}
                    if func_response.status_code == 200:
                        for module_info in func_response.json().get('modules', {}).values():
                            for func in module_info.get('functions', []):
                                desc_map[func['name']] = func.get('description', '')
                    
                    for name in enabled_functions:
                        desc = desc_map.get(name, '')[:60]
                        if len(desc_map.get(name, '')) > 60:
                            desc += '...'
                        lines.append(f"  {name}: {desc}")
                    
                    if not enabled_functions:
                        lines.append("  (no tools enabled)")
                    
                    return '\n'.join(lines), True
                    
            except Exception as e:
                logger.error(f"Error listing tools: {e}")
                return f"Error listing tools: {e}", False

        # === Monolith-only tools ===
        
        elif function_name == "edit_prompt":
            from core.modules.system import prompts
            
            content = arguments.get('content')
            if not content:
                return "Content is required.", False
            
            # Get current prompt
            current_name = prompts.get_active_preset_name()
            if not current_name:
                return "No active prompt to edit.", False
            
            prompt_data = prompts.get_prompt(current_name)
            if not prompt_data:
                return f"Current prompt '{current_name}' not found.", False
            
            # Verify it's a monolith (though tool shouldn't be visible if not)
            if isinstance(prompt_data, dict) and prompt_data.get('type') == 'assembled':
                return "Cannot edit assembled prompt with edit_prompt. Use set_piece instead.", False
            
            # Update in place
            response = requests.put(
                f"{main_api_url}/api/prompts/{current_name}",
                json={"type": "monolith", "content": content},
                headers=headers,
                timeout=5
            )
            
            if response.status_code != 200:
                return f"Failed to save: {response.text}", False
            
            # Re-activate to apply changes
            response = requests.post(
                f"{main_api_url}/api/prompts/{current_name}/load",
                headers=headers,
                timeout=5
            )
            
            if response.status_code != 200:
                return f"Saved but failed to reload: {response.text}", False
            
            return f"Updated prompt '{current_name}' ({len(content)} chars).", True

        # === Assembled-only tools ===
        
        elif function_name == "set_piece":
            from core.modules.system import prompts
            from core.modules.system.prompt_state import _assembled_state
            
            component = _normalize_component(arguments.get('component', ''))
            key = _normalize_name(arguments.get('key', ''))
            
            if not component or not key:
                return "Both component and key are required.", False
            
            valid = ['persona', 'location', 'relationship', 'goals', 'format', 'scenario', 'emotions', 'extras']
            if component not in valid:
                return f"Invalid component '{component}'. Valid: {', '.join(valid)}", False
            
            # Check piece exists
            if component not in prompts.prompt_manager._components:
                return f"Component type '{component}' not found.", False
            
            if key not in prompts.prompt_manager._components[component]:
                available = list(prompts.prompt_manager._components[component].keys())[:10]
                return f"'{key}' not found in {component}. Available: {', '.join(available)}", False
            
            # Set or add based on component type
            if _is_list_component(component):
                # Add to list
                if key in _assembled_state.get(component, []):
                    return f"'{key}' already in {component}.", True
                if component not in _assembled_state:
                    _assembled_state[component] = []
                _assembled_state[component].append(key)
            else:
                # Set single value
                _assembled_state[component] = key
            
            preset_name = _get_current_preset_name()
            success, msg = _save_and_activate_assembled(preset_name, headers, main_api_url)
            
            if not success:
                return msg, False
            
            status = _build_status_string(preset_name)
            action = "Added" if _is_list_component(component) else "Set"
            return f"{action} {component}='{key}'. {status}", True

        elif function_name == "remove_piece":
            from core.modules.system.prompt_state import _assembled_state
            
            component = _normalize_component(arguments.get('component', ''))
            key = _normalize_name(arguments.get('key', ''))
            
            if not component or not key:
                return "Both component and key are required.", False
            
            if not _is_list_component(component):
                return f"Can only remove from emotions or extras, not '{component}'.", False
            
            if component not in _assembled_state or key not in _assembled_state[component]:
                return f"'{key}' not in current {component}.", False
            
            _assembled_state[component].remove(key)
            
            preset_name = _get_current_preset_name()
            success, msg = _save_and_activate_assembled(preset_name, headers, main_api_url)
            
            if not success:
                return msg, False
            
            status = _build_status_string(preset_name)
            return f"Removed '{key}' from {component}. {status}", True

        elif function_name == "create_piece":
            from core.modules.system import prompts
            from core.modules.system.prompt_state import _assembled_state
            
            component = _normalize_component(arguments.get('component', ''))
            key = _normalize_name(arguments.get('key', ''))
            value = arguments.get('value', '')
            
            if not component or not key or not value:
                return "Component, key, and value are all required.", False
            
            valid = ['persona', 'location', 'relationship', 'goals', 'format', 'scenario', 'emotions', 'extras']
            if component not in valid:
                return f"Invalid component '{component}'. Valid: {', '.join(valid)}", False
            
            # Save to library via API
            response = requests.put(
                f"{main_api_url}/api/prompts/components/{component}/{key}",
                json={"value": value},
                headers=headers,
                timeout=5
            )
            
            if response.status_code != 200:
                return f"Failed to save: {response.text}", False
            
            # Reload components
            prompts.prompt_manager._load_pieces()
            
            # Activate it
            if _is_list_component(component):
                if component not in _assembled_state:
                    _assembled_state[component] = []
                if key not in _assembled_state[component]:
                    _assembled_state[component].append(key)
            else:
                _assembled_state[component] = key
            
            preset_name = _get_current_preset_name()
            success, msg = _save_and_activate_assembled(preset_name, headers, main_api_url)
            
            if not success:
                return msg, False
            
            status = _build_status_string(preset_name)
            return f"Created {component}='{key}'. {status}", True

        elif function_name == "list_pieces":
            from core.modules.system import prompts
            
            component = _normalize_component(arguments.get('component', ''))
            
            if not component:
                return "Component type is required.", False
            
            valid = ['persona', 'location', 'relationship', 'goals', 'format', 'scenario', 'emotions', 'extras']
            if component not in valid:
                return f"Invalid component '{component}'. Valid: {', '.join(valid)}", False
            
            if component not in prompts.prompt_manager._components:
                return f"No {component} available.", True
            
            items = prompts.prompt_manager._components[component]
            if not items:
                return f"No {component} available.", True
            
            lines = [f"Available {component}:"]
            for key, value in items.items():
                preview = value[:50] + '...' if len(value) > 50 else value
                preview = preview.replace('\n', ' ')
                lines.append(f"  {key}: {preview}")
            
            return '\n'.join(lines), True

        else:
            return f"Unknown function: {function_name}", False

    except requests.exceptions.RequestException as e:
        logger.error(f"API request failed for '{function_name}': {e}")
        return f"API request failed: {str(e)}", False
    except Exception as e:
        logger.error(f"Meta function error for '{function_name}': {e}", exc_info=True)
        return f"Error in {function_name}: {str(e)}", False