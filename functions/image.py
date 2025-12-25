# functions/image.py - SDXL Image Generation

import requests
import logging
import json
import os
import re

logger = logging.getLogger(__name__)

# Path to user settings
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS_FILE = os.path.join(PROJECT_ROOT, 'user', 'webui', 'plugins', 'image-gen.json')

# Fallback defaults (used if no user settings exist)
DEFAULTS = {
    'api_url': 'http://localhost:5153',
    'negative_prompt': 'ugly, deformed, noisy, blurry, distorted, grainy, low quality, bad anatomy, jpeg artifacts',
    'static_keywords': 'wide shot',
    'character_descriptions': {
        'me': 'A sexy short woman with long brown hair and blue eyes',
        'you': 'A tall handsome man with brown hair and brown eyes'
    },
    'defaults': {
        'height': 1024,
        'width': 1024,
        'steps': 23,
        'cfg_scale': 3.0,
        'scheduler': 'dpm++_2m_karras'
    }
}

# Module configuration
ENABLED = True
AVAILABLE_FUNCTIONS = ['generate_scene_image']

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "generate_scene_image",
            "description": "Generate an image with a concise 18 word scene description. Write from your perspective: use 'me' for yourself and 'you' for the human.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scene_description": {
                        "type": "string",
                        "description": "Describe from your perspective: 'me' = yourself, 'you' = the human. Include actions, clothes, background."
                    }
                },
                "required": ["scene_description"]
            }
        }
    }
]


def _load_settings():
    """Load settings from user config, falling back to defaults."""
    if not os.path.exists(SETTINGS_FILE):
        return DEFAULTS.copy()
    
    try:
        with open(SETTINGS_FILE, 'r') as f:
            user_settings = json.load(f)
        
        # Merge with defaults (user settings override)
        settings = DEFAULTS.copy()
        settings['api_url'] = user_settings.get('api_url', DEFAULTS['api_url'])
        settings['negative_prompt'] = user_settings.get('negative_prompt', DEFAULTS['negative_prompt'])
        settings['static_keywords'] = user_settings.get('static_keywords', DEFAULTS['static_keywords'])
        
        # Merge character descriptions
        settings['character_descriptions'] = DEFAULTS['character_descriptions'].copy()
        if 'character_descriptions' in user_settings:
            settings['character_descriptions'].update(user_settings['character_descriptions'])
        
        # Merge generation defaults
        settings['defaults'] = DEFAULTS['defaults'].copy()
        if 'defaults' in user_settings:
            settings['defaults'].update(user_settings['defaults'])
        
        return settings
    except Exception as e:
        logger.warning(f"Failed to load image-gen settings, using defaults: {e}")
        return DEFAULTS.copy()


def _replace_character_names(prompt, character_descriptions):
    """
    Replace character markers with physical descriptions.
    Uses word boundary matching - \b works for regular words like 'me' and 'you'.
    Case-insensitive, only first occurrence of each.
    """
    for char_name, description in character_descriptions.items():
        # Word boundary ensures 'me' doesn't match 'mesh', 'you' doesn't match 'your'
        pattern = rf'\b{re.escape(char_name)}\b'
        prompt = re.sub(pattern, description, prompt, count=1, flags=re.IGNORECASE)
    return prompt


def _call_sdxl_api(prompt, original_description, settings):
    """
    Call the SDXL API with processed prompt.
    
    Args:
        prompt: Processed prompt with character replacements
        original_description: Original scene description (for AI feedback)
        settings: Loaded settings dict
    
    Returns:
        (message, success, image_id)
    """
    static_keywords = settings.get('static_keywords', '')
    if static_keywords:
        enhanced_prompt = f"{prompt}. {static_keywords}".strip()
    else:
        enhanced_prompt = prompt
    
    gen_defaults = settings.get('defaults', {})
    
    payload = {
        'prompt': enhanced_prompt,
        'height': gen_defaults.get('height', 1024),
        'width': gen_defaults.get('width', 1024),
        'steps': gen_defaults.get('steps', 23),
        'guidance_scale': gen_defaults.get('cfg_scale', 3.0),
        'negative_prompt': settings.get('negative_prompt', ''),
        'scale': 1.0,
        'scheduler': gen_defaults.get('scheduler', 'dpm++_2m_karras')
    }
    
    api_url = settings.get('api_url', 'http://localhost:5153')
    logger.info(f"Sending SDXL request to {api_url}: {enhanced_prompt[:100]}...")
    
    try:
        response = requests.post(
            f"{api_url}/generate",
            json=payload,
            timeout=5
        )
        
        if response.status_code != 200:
            return f"SDXL API error: {response.status_code} - {response.text}", False, None
        
        result_data = response.json()
        image_id = result_data.get('image_id')
        
        if not image_id:
            return "SDXL API did not return image_id", False, None
        
        message = f"Your image tool call was successful for: {original_description}. This is your tool call success confirmation. Don't make any more, just think about what to say to the user now."
        return message, True, image_id
        
    except requests.exceptions.Timeout:
        return "SDXL API request timed out", False, None
    except Exception as e:
        return f"SDXL API error: {str(e)}", False, None


def execute(function_name, arguments, config):
    """Execute image generation functions."""
    try:
        if function_name == "generate_scene_image":
            scene_description = arguments.get("scene_description", "")
            
            if not scene_description:
                return "No scene description provided", False
            
            # Load settings fresh each call (allows hot-reload of settings)
            settings = _load_settings()
            
            # Replace character names with descriptions
            char_descriptions = settings.get('character_descriptions', {})
            processed_prompt = _replace_character_names(scene_description, char_descriptions)
            
            message, success, image_id = _call_sdxl_api(processed_prompt, scene_description, settings)
            
            if success and image_id:
                logger.info(f"Image generated: {scene_description[:50]}... -> {image_id}")
                return f"<<IMG::{image_id}>>\n{message}", True
            else:
                logger.error(f"Image generation failed: {message}")
                return message, False
        else:
            return f"Unknown image function: {function_name}", False
            
    except Exception as e:
        logger.error(f"Image function execution error for '{function_name}': {e}")
        return f"Image generation failed: {str(e)}", False