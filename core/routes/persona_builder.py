# core/routes/persona_builder.py - Online persona builder for discovering Sapphire
import json
import logging
from pathlib import Path
from fastapi import APIRouter, Request, HTTPException
from core.auth import check_endpoint_rate

logger = logging.getLogger(__name__)

router = APIRouter()

PROJECT_ROOT = Path(__file__).parent.parent.parent


@router.get("/api/persona-builder/config")
async def get_builder_config(request: Request):
    """
    Get persona builder configuration and form schema.
    This endpoint is public (no authentication required).
    """
    try:
        check_endpoint_rate(request, 'persona_builder_config', max_calls=30, window=60)
        
        from core.personas.persona_manager import get_persona_settings_keys
        
        # Available settings that can be configured
        available_settings = get_persona_settings_keys()
        
        # Load available voice options
        voices = _get_available_voices()
        
        # Load available toolsets
        toolsets = _get_available_toolsets()
        
        # Load available spice sets
        spice_sets = _get_available_spice_sets()
        
        return {
            "status": "ok",
            "form_fields": {
                "description": {
                    "type": "textarea",
                    "label": "Describe the companion you want",
                    "placeholder": "E.g., 'A wise mentor who loves history and tells jokes' or 'A cheerful morning buddy'",
                    "max_length": 2000,
                    "required": True
                },
                "notes_file": {
                    "type": "file",
                    "label": "Or upload persona notes (JSON, TXT, or PDF)",
                    "accept": ".json,.txt,.pdf",
                    "required": False
                }
            },
            "available_settings": available_settings,
            "voices": voices,
            "toolsets": toolsets,
            "spice_sets": spice_sets
        }
    except Exception as e:
        logger.error(f"Failed to get builder config: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to load builder configuration")


@router.post("/api/persona-builder/generate")
async def generate_persona(request: Request):
    """
    Generate a persona from user description or uploaded notes.
    This endpoint is public (no authentication required).
    
    Accepts:
    - description: Text description of desired companion
    - notes: Uploaded file content (JSON, TXT, or PDF)
    """
    try:
        check_endpoint_rate(request, 'persona_builder_generate', max_calls=10, window=60)
        
        data = await request.json()
        description = data.get('description', '').strip()
        notes = data.get('notes', '').strip()
        
        if not description and not notes:
            raise HTTPException(status_code=400, detail="Please provide a description or upload notes")
        
        if len(description) > 2000:
            raise HTTPException(status_code=400, detail="Description is too long (max 2000 characters)")
        
        # Generate persona using LLM
        persona = await _generate_persona_with_llm(description, notes)
        
        return {
            "status": "ok",
            "persona": persona
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to generate persona: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate persona")


@router.post("/api/persona-builder/download")
async def download_persona(request: Request):
    """
    Export a generated persona as JSON for download/import.
    This endpoint is public (no authentication required).
    """
    try:
        check_endpoint_rate(request, 'persona_builder_download', max_calls=20, window=60)
        
        data = await request.json()
        persona_data = data.get('persona')
        
        if not persona_data:
            raise HTTPException(status_code=400, detail="No persona data provided")
        
        # Validate persona schema
        if not _validate_persona_schema(persona_data):
            raise HTTPException(status_code=400, detail="Invalid persona schema")
        
        return persona_data
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to download persona: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to prepare persona for download")


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def _get_available_voices():
    """Get list of available TTS voices."""
    try:
        from core.tts.providers.sapphire_router import TtsRouter
        router_instance = TtsRouter()
        voices = router_instance.list_voices()
        return voices
    except Exception as e:
        logger.warning(f"Failed to load voices: {e}")
        return {
            "af_heart": "Heart (Female, warm)",
            "am_adam": "Adam (Male, neutral)",
            "bf_bella": "Bella (Female, bright)"
        }


def _get_available_toolsets():
    """Get list of available toolsets."""
    try:
        from core.toolsets import toolset_manager
        toolsets_dict = toolset_manager.get_all_toolsets()
        result = {}
        for name, toolset in toolsets_dict.items():
            result[name] = {
                "name": name,
                "description": toolset.get('description', ''),
                "tool_count": len(toolset.get('tools', []))
            }
        return result
    except Exception as e:
        logger.warning(f"Failed to load toolsets: {e}")
        return {
            "personality": {
                "name": "personality",
                "description": "Conversational tools for natural dialogue",
                "tool_count": 5
            }
        }


def _get_available_spice_sets():
    """Get list of available spice sets."""
    try:
        spice_dir = PROJECT_ROOT / "core" / "spice_sets"
        spice_sets = {}
        
        if spice_dir.exists():
            for spice_file in spice_dir.glob("*.json"):
                name = spice_file.stem
                try:
                    with open(spice_file, 'r', encoding='utf-8') as f:
                        content = json.load(f)
                    description = content.get('_description', 'Spice set')
                    spice_count = len([k for k in content.keys() if not k.startswith('_')])
                    spice_sets[name] = {
                        "name": name,
                        "description": description,
                        "spice_count": spice_count
                    }
                except Exception as e:
                    logger.warning(f"Failed to load spice set {name}: {e}")
        
        return spice_sets if spice_sets else {
            "companion": {
                "name": "companion",
                "description": "Warm and friendly personality",
                "spice_count": 10
            }
        }
    except Exception as e:
        logger.warning(f"Failed to load spice sets: {e}")
        return {}


async def _generate_persona_with_llm(description: str, notes: str) -> dict:
    """
    Generate a persona using the configured LLM.
    Returns a persona object matching Sapphire's persona schema.
    Falls back gracefully if LLM is unavailable.
    """
    try:
        from core.api_fastapi import get_system
        from fastapi import Request
        
        # Create a minimal request object for get_system()
        class MinimalRequest:
            def __init__(self):
                self.client = type('obj', (object,), {'host': 'builder'})()
                self.session = {}
        
        try:
            system = get_system(MinimalRequest())
        except Exception as e:
            logger.warning(f"Could not get system instance: {e} - using fallback persona")
            return _generate_fallback_persona(description)
        
        if not system or not hasattr(system, 'llm_chat'):
            logger.warning("System or LLM not available - using fallback persona")
            return _generate_fallback_persona(description)
        
        # Build the generation prompt
        generation_prompt = _build_generation_prompt(description, notes)
        
        # Call LLM
        logger.info(f"Generating persona from user input (description length: {len(description)})")
        
        # Use asyncio to run the sync LLM call in a thread
        import asyncio
        
        def _call_llm():
            try:
                response = system.llm_chat.get_response(
                    generation_prompt,
                    tools=[],  # No tools for generation
                    show_thinking=False
                )
                return response
            except Exception as e:
                logger.error(f"LLM call failed: {e}")
                return None
        
        response = await asyncio.to_thread(_call_llm)
        
        if not response:
            logger.warning("LLM returned empty response - using fallback")
            return _generate_fallback_persona(description)
        
        # Parse the LLM response into a persona object
        persona = _parse_llm_response_to_persona(response, description)
        
        return persona
    
    except Exception as e:
        logger.error(f"Persona generation failed: {e}", exc_info=True)
        # Fallback: return a basic persona from the description
        return _generate_fallback_persona(description)


def _build_generation_prompt(description: str, notes: str) -> str:
    """Build the LLM prompt for persona generation."""
    
    user_input = ""
    if description:
        user_input += f"User Description: {description}\n"
    if notes:
        user_input += f"Additional Notes: {notes}\n"
    
    prompt = f"""You are a persona design expert for Sapphire, an AI companion framework.
Your task is to generate a complete persona definition based on user input.

{user_input}

Please generate a JSON persona object with the following structure:
{{
  "name": "persona_name (lowercase, no spaces)",
  "tagline": "One-line description of the persona (max 50 chars)",
  "description": "Detailed description of the persona's personality",
  "settings": {{
    "prompt": "Name of the system prompt to use (e.g., 'sapphire', 'cobalt', or 'custom')",
    "toolset": "Name of toolset to use (e.g., 'personality', 'research')",
    "spice_set": "Name of spice set (e.g., 'companion', 'curious')",
    "voice": "Voice identifier (e.g., 'af_heart', 'am_adam')",
    "pitch": 1.0,
    "speed": 1.0,
    "spice_enabled": true,
    "spice_turns": 3,
    "inject_datetime": false,
    "custom_context": "Optional additional context for the persona"
  }}
}}

Requirements:
1. The persona name should be unique and lowercase (no spaces)
2. The tagline should be catchy and under 50 characters
3. The description should capture the personality vividly
4. Settings should be realistic and compatible with Sapphire
5. Return ONLY valid JSON, no additional text

Generate the persona now:"""
    
    return prompt


def _parse_llm_response_to_persona(response: str, original_description: str) -> dict:
    """
    Parse LLM response into a persona object.
    Attempts to extract JSON from the response.
    """
    try:
        # Try to extract JSON from the response
        import re
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        
        if json_match:
            json_str = json_match.group(0)
            persona = json.loads(json_str)
        else:
            # Fallback if no JSON found
            logger.warning("No JSON found in LLM response, using fallback")
            return _generate_fallback_persona(original_description)
        
        # Ensure required fields exist
        if 'name' not in persona:
            persona['name'] = _generate_persona_name(original_description)
        
        if 'tagline' not in persona:
            persona['tagline'] = original_description[:50]
        
        if 'settings' not in persona:
            persona['settings'] = _get_default_settings()
        
        # Merge with defaults for any missing settings
        persona['settings'] = {**_get_default_settings(), **persona.get('settings', {})}
        
        return persona
    
    except Exception as e:
        logger.error(f"Failed to parse LLM response: {e}")
        return _generate_fallback_persona(original_description)


def _generate_fallback_persona(description: str) -> dict:
    """Generate a basic persona if LLM generation fails."""
    name = _generate_persona_name(description)
    return {
        "name": name,
        "tagline": description[:50],
        "description": description,
        "settings": _get_default_settings()
    }


def _generate_persona_name(description: str) -> str:
    """Generate a unique persona name from description."""
    # Use first few significant words
    words = [w.lower() for w in description.split() if len(w) > 2 and w.isalpha()]
    name = '_'.join(words[:3]) if words else 'custom_persona'
    # Clean up: remove special chars, limit length
    import re
    name = re.sub(r'[^a-z0-9_]', '', name)[:20]
    return name if name else 'custom_companion'


def _get_default_settings() -> dict:
    """Return default persona settings."""
    return {
        "prompt": "sapphire",
        "toolset": "personality",
        "spice_set": "companion",
        "voice": "af_heart",
        "pitch": 1.0,
        "speed": 1.0,
        "spice_enabled": True,
        "spice_turns": 3,
        "inject_datetime": False,
        "custom_context": "",
        "llm_primary": "auto",
        "llm_model": "",
        "memory_scope": "default",
        "goal_scope": "default",
        "knowledge_scope": "default",
        "people_scope": "default",
        "trim_color": "#4a9eff"
    }


def _validate_persona_schema(persona: dict) -> bool:
    """Validate that a persona object matches the Sapphire schema."""
    try:
        if not isinstance(persona, dict):
            return False
        
        # Required top-level fields
        required_fields = ['name', 'tagline', 'settings']
        for field in required_fields:
            if field not in persona:
                return False
        
        # Validate settings
        settings = persona.get('settings', {})
        if not isinstance(settings, dict):
            return False
        
        # Should have at least some core settings
        core_settings = ['prompt', 'toolset', 'voice']
        for setting in core_settings:
            if setting not in settings:
                return False
        
        return True
    
    except Exception as e:
        logger.warning(f"Persona validation failed: {e}")
        return False
