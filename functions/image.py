# llm_master/functions/image.py

import requests
import logging
import re

logger = logging.getLogger(__name__)

# --- SDXL Image Generation Settings ---
SDXL_API_URL = "http://localhost:5153"
SDXL_NEGATIVE_PROMPT = "ugly, deformed, noisy, blurry, distorted, grainy, low quality, bad anatomy, jpeg artifacts"
SDXL_CHARACTER_DESCRIPTIONS = {
    "sapphire": "A sexy short woman with long brown hair and blue eyes",
    "user": "A tall handsome man with brown hair and brown eyes"
}
SDXL_STATIC_KEYWORDS = "wide shot"
SDXL_DEFAULTS = {
    "height": 1024,
    "width": 1024,
    "steps": 23,
    "cfg_scale": 3.0,
    "scheduler": "dpm++_2m_karras"
}

# Module configuration
ENABLED = True
AVAILABLE_FUNCTIONS = ['generate_scene_image']

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "generate_scene_image",
            "description": "Generate an image by concise 18 word scene description using user and/or Sapphire's names.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scene_description": {
                        "type": "string",
                        "description": "Describe the scene naturally, extremely concise. names, actions, clothes and background."
                    }
                },
                "required": ["scene_description"]
            }
        }
    }
]

def _replace_character_names(prompt):
    """Replace character names with physical descriptions - avoid double replacement."""
    for char_name, description in SDXL_CHARACTER_DESCRIPTIONS.items():
        prompt = re.sub(rf'\b{char_name}\b', description, prompt, count=1, flags=re.IGNORECASE)
    return prompt

def _call_sdxl_api(prompt, original_description):
    """
    Call the SDXL API with processed prompt.
    
    Args:
        prompt: Processed prompt with character replacements
        original_description: Original scene description (for AI feedback)
    
    Returns:
        (message, success, image_id)
    """
    enhanced_prompt = f"{prompt}. {SDXL_STATIC_KEYWORDS}".strip()
    
    payload = {
        'prompt': enhanced_prompt,
        'height': SDXL_DEFAULTS.get('height', 1024),
        'width': SDXL_DEFAULTS.get('width', 1024), 
        'steps': SDXL_DEFAULTS.get('steps', 22),
        'guidance_scale': SDXL_DEFAULTS.get('cfg_scale', -500.0),
        'negative_prompt': SDXL_NEGATIVE_PROMPT,
        'scale': 1.0,
        'scheduler': SDXL_DEFAULTS.get('scheduler', 'euler_a')
    }
    
    logger.info(f"Sending SDXL request: {enhanced_prompt[:100]}...")
    
    try:
        response = requests.post(
            f"{SDXL_API_URL}/generate", 
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
            
            processed_prompt = _replace_character_names(scene_description)
            message, success, image_id = _call_sdxl_api(processed_prompt, scene_description)
            
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