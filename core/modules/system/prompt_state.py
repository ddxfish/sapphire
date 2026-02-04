import logging
import random
from .prompt_manager import prompt_manager

logger = logging.getLogger(__name__)

# Expose for backward compatibility
PROMPT_COMPONENTS = prompt_manager.components
SCENARIO_PRESETS = prompt_manager.scenario_presets
MONOLITHS = prompt_manager.monoliths
SPICE_POOL = prompt_manager.spices

# Runtime state (not in JSON)
_assembled_state = {
    "persona": "sapphire",
    "location": "default", 
    "relationship": "friend",
    "goals": "none",
    "format": "conversational",
    "scenario": "default",
    "extras": [],
    "emotions": [],
    "spice": "",
    "active_preset": "default"
}

# User prompts cache (used by prompt_crud)
_user_prompts = {}


def get_current_state():
    """Get the current prompt's component state for UI display."""
    active_name = get_active_preset_name()
    if not active_name or active_name == 'unknown':
        return {}
    
    # Look up the prompt
    prompt_data = None
    
    # Check user prompts
    if active_name in _user_prompts:
        prompt_data = _user_prompts[active_name]
    # Check scenario presets
    elif hasattr(prompt_manager, 'scenario_presets') and active_name in prompt_manager.scenario_presets:
        prompt_data = {
            'type': 'assembled',
            'components': prompt_manager.scenario_presets[active_name]
        }
    # Check monoliths
    elif hasattr(prompt_manager, 'monoliths') and active_name in prompt_manager.monoliths:
        prompt_data = {'type': 'monolith'}
    
    if not prompt_data:
        return {}
    
    if prompt_data.get('type') == 'assembled':
        return prompt_data.get('components', {})
    elif prompt_data.get('type') == 'monolith':
        return {
            'persona': 'monolith',
            'location': 'n/a',
            'goals': 'n/a'
        }
    
    return {}


def get_active_preset_name():
    """Get the name of the currently active prompt."""
    if hasattr(prompt_manager, '_active_preset_name'):
        return prompt_manager._active_preset_name
    return 'unknown'


def is_current_prompt_private():
    """Check if the currently active prompt requires privacy mode."""
    preset = get_active_preset_name()
    if not preset or preset == 'unknown':
        return False

    # Check monoliths
    if preset in prompt_manager.monoliths:
        mono = prompt_manager.monoliths[preset]
        if isinstance(mono, dict):
            return mono.get('privacy_required', False)
        return False

    # Check scenario presets
    if preset in prompt_manager.scenario_presets:
        components = prompt_manager.scenario_presets[preset]
        return components.get('_privacy_required', False)

    return False


def set_active_preset_name(name: str):
    """Track which preset is currently active."""
    global _assembled_state
    prompt_manager._active_preset_name = name
    _assembled_state["active_preset"] = name


def get_prompt_char_count():
    """Get character count of active prompt."""
    from .prompt_crud import get_prompt
    
    active_name = get_active_preset_name()
    if not active_name or active_name == 'unknown':
        return 0
    
    prompt_data = get_prompt(active_name)
    if not prompt_data:
        return 0
    
    content = prompt_data.get('content', '')
    return len(content)


def get_current_prompt():
    """Get the currently active prompt (monolith or assembled)."""
    preset = _assembled_state.get("active_preset", "default")

    if preset in prompt_manager.monoliths:
        mono = prompt_manager.monoliths[preset]
        text = mono.get('content', '') if isinstance(mono, dict) else mono
        return {"role": "system", "content": prompt_manager._replace_templates(text)}

    return assemble_prompt()


def generate_random_assembled():
    """Generate random assembled prompt configuration."""
    components = prompt_manager.components
    location_choices = [k for k in components.get("location", {}).keys() if k != "default"]
    scenario_choices = [k for k in components.get("scenario", {}).keys() if k != "default"]
    
    return {
        "persona": random.choice(list(components.get("persona", {}).keys())),
        "location": random.choice(location_choices) if location_choices else "default",
        "relationship": random.choice(list(components.get("relationship", {}).keys())),
        "format": random.choice(list(components.get("format", {}).keys())),
        "goals": random.choice(list(components.get("goals", {}).keys())),
        "scenario": random.choice(scenario_choices) if scenario_choices else "default",
        "extras": random.sample(list(components.get("extras", {}).keys()), k=random.randint(0, 2)),
        "emotions": random.sample(list(components.get("emotions", {}).keys()), k=random.randint(0, 2))
    }


def reset_to_defaults():
    """Reset to default assembled state."""
    global _assembled_state
    _assembled_state = {
        "persona": "sapphire",
        "location": "default", 
        "relationship": "friend",
        "goals": "none",
        "format": "conversational",
        "scenario": "default",
        "extras": [],
        "emotions": [],
        "spice": "",
        "active_preset": "default"
    }
    return "Reset to default state"


def set_random_spice():
    """Set a random spice from enabled categories in the pool."""
    global _assembled_state
    all_spices = prompt_manager.get_enabled_spices()
    
    if all_spices:
        _assembled_state["spice"] = random.choice(all_spices)
        return f"Random spice: {_assembled_state['spice']}"
    
    # Clear spice when no categories enabled
    _assembled_state["spice"] = ""
    return "No spices available"


def clear_spice():
    """Clear the current spice."""
    global _assembled_state
    _assembled_state["spice"] = ""
    return "Spice cleared"


def get_current_spice():
    """Get the currently active spice text, or empty string if none."""
    return _assembled_state.get("spice", "")


def assemble_prompt():
    """Assemble prompt from pieces."""
    components = prompt_manager.components
    
    parts = [
        components.get("persona", {}).get(_assembled_state["persona"], ""),
        f"You are currently {components.get('location', {}).get(_assembled_state['location'], '')}.",
        components.get("relationship", {}).get(_assembled_state["relationship"], ""),
        components.get("goals", {}).get(_assembled_state["goals"], ""),
        components.get("format", {}).get(_assembled_state["format"], "")
    ]
    
    if _assembled_state["scenario"] != "default":
        scenario_text = components.get("scenario", {}).get(_assembled_state["scenario"], "")
        if scenario_text:
            parts.append(scenario_text)
    
    extras = components.get("extras", {})
    for extra in _assembled_state["extras"]:
        if extra in extras:
            parts.append(extras[extra])
    
    emotions = components.get("emotions", {})
    for emotion in _assembled_state["emotions"]:
        if emotion in emotions:
            parts.append(emotions[emotion])
    
    if _assembled_state["spice"]:
        parts.append(f'URGENT ALERT: {_assembled_state["spice"]}')
    
    assembled = "\n".join(filter(None, parts))
    return {"role": "system", "content": prompt_manager._replace_templates(assembled)}


def is_assembled_mode():
    """Check if currently using piece-based assembly."""
    preset = _assembled_state.get("active_preset", "default")
    return preset == "assembled" or preset in prompt_manager.scenario_presets


def get_prompt_mode() -> str:
    """Get current prompt mode as string for tool filtering."""
    return "assembled" if is_assembled_mode() else "monolith"


def set_component(component_type, value):
    """Set a component - only works in assembled mode."""
    global _assembled_state
    
    if not is_assembled_mode():
        return f"Component changes only work in assembled mode. Current mode: {_assembled_state['active_preset']} (monolith)"
    
    components = prompt_manager.components
    if component_type not in components:
        return f"Unknown component: {component_type}"
    
    if component_type in ["extras", "emotions"]:
        if value in components[component_type]:
            if value not in _assembled_state[component_type]:
                _assembled_state[component_type].append(value)
                _assembled_state["active_preset"] = "assembled"
                return f"Added {component_type[:-1]}: {value}"
            return f"{component_type[:-1].title()} '{value}' already active"
        available = list(components[component_type].keys())
        return f"Unknown {component_type[:-1]}: {value}. Available: {', '.join(available)}"
    
    if value in components[component_type]:
        _assembled_state[component_type] = value
        _assembled_state["active_preset"] = "assembled"
        return f"Set {component_type}: {value}"
    
    available = list(components[component_type].keys())
    return f"Unknown {component_type}: {value}. Available: {', '.join(available)}"


def remove_extra(value):
    """Remove an extra from assembled state."""
    global _assembled_state
    if not is_assembled_mode():
        return f"Component changes only work in assembled mode"
    
    if value in _assembled_state["extras"]:
        _assembled_state["extras"].remove(value)
        _assembled_state["active_preset"] = "assembled"
        return f"Removed extra: {value}"
    return f"Extra '{value}' not active"


def remove_emotion(value):
    """Remove an emotion from assembled state."""
    global _assembled_state
    if not is_assembled_mode():
        return f"Component changes only work in assembled mode"
    
    if value in _assembled_state["emotions"]:
        _assembled_state["emotions"].remove(value)
        _assembled_state["active_preset"] = "assembled"
        return f"Removed emotion: {value}"
    return f"Emotion '{value}' not active"


def clear_extras():
    """Clear all extras from assembled state."""
    global _assembled_state
    if not is_assembled_mode():
        return f"Component changes only work in assembled mode"
    
    count = len(_assembled_state["extras"])
    _assembled_state["extras"] = []
    _assembled_state["active_preset"] = "assembled"
    return f"Cleared {count} extras"


def clear_emotions():
    """Clear all emotions from assembled state."""
    global _assembled_state
    if not is_assembled_mode():
        return f"Component changes only work in assembled mode"
    
    count = len(_assembled_state["emotions"])
    _assembled_state["emotions"] = []
    _assembled_state["active_preset"] = "assembled"
    return f"Cleared {count} emotions"


def get_assembled_state():
    """Get info about current prompt state."""
    preset = _assembled_state.get("active_preset", "default")
    
    if preset in prompt_manager.monoliths:
        return f"Current prompt: {preset} (monolith)\nUse 'system prompt status' to see full text"
    
    info = [f"Current prompt: {preset} (assembled from pieces)"]
    for key, value in _assembled_state.items():
        if key == "active_preset":
            continue
        if key in ["extras", "emotions"]:
            info.append(f"  {key}: {', '.join(value) if value else 'none'}")
        else:
            info.append(f"  {key}: {value}")
    return "\n".join(info)


def apply_scenario(scenario_name):
    """Apply a scenario preset (piece-based)."""
    global _assembled_state
    if scenario_name not in prompt_manager.scenario_presets:
        return f"Unknown scenario: {scenario_name}"
    
    scenario = prompt_manager.scenario_presets[scenario_name]
    for component_type, value in scenario.items():
        _assembled_state[component_type] = value.copy() if component_type in ["extras", "emotions"] else value
    
    _assembled_state["active_preset"] = scenario_name
    return f"Applied scenario: {scenario_name}"


def apply_random_assembled():
    """Apply a random assembled configuration."""
    global _assembled_state
    random_config = generate_random_assembled()
    
    for component_type, value in random_config.items():
        _assembled_state[component_type] = value
    
    _assembled_state["active_preset"] = "random"
    extras = ", ".join(_assembled_state["extras"]) if _assembled_state["extras"] else "none"
    emotions = ", ".join(_assembled_state["emotions"]) if _assembled_state["emotions"] else "none"
    return f"Random: {_assembled_state['persona']} in {_assembled_state['location']}, {_assembled_state['goals']} goals, {_assembled_state['scenario']} scenario, extras: {extras}, emotions: {emotions}"