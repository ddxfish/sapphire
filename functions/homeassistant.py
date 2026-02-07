# functions/homeassistant.py - Home Assistant Integration

import requests
import logging
import json
import os
import fnmatch

logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS_FILE = os.path.join(PROJECT_ROOT, 'user', 'webui', 'plugins', 'homeassistant.json')

DEFAULTS = {
    'url': 'http://homeassistant.local:8123',
    'blacklist': ['cover.*', 'lock.*'],
    'notify_service': ''  # e.g., 'mobile_app_pixel_7'
}

ENABLED = True

AVAILABLE_FUNCTIONS = [
    'ha_list_scenes_and_scripts',
    'ha_activate',
    'ha_list_areas',
    'ha_area_light',
    'ha_area_color',
    'ha_get_thermostat',
    'ha_set_thermostat',
    'ha_list_lights_and_switches',
    'ha_set_light',
    'ha_set_switch',
    'ha_notify',
    'ha_house_status'
]

TOOLS = [
    {
        "type": "function",
        "is_local": "endpoint",
        "function": {
            "name": "ha_list_scenes_and_scripts",
            "description": "List all available Home Assistant scenes and scripts. Returns names with type label.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "is_local": "endpoint",
        "function": {
            "name": "ha_activate",
            "description": "Activate a Home Assistant scene or run a script by name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Scene or script name (e.g., 'movie_night', 'bedtime')"
                    }
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "is_local": "endpoint",
        "function": {
            "name": "ha_list_areas",
            "description": "List all available Home Assistant areas/rooms. Use this to find valid area names for ha_area_light and ha_area_color.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "is_local": "endpoint",
        "function": {
            "name": "ha_area_light",
            "description": "Set brightness for all lights in an area. 0 = off, 100 = full brightness.",
            "parameters": {
                "type": "object",
                "properties": {
                    "area": {
                        "type": "string",
                        "description": "Area name (e.g., 'living room', 'bedroom')"
                    },
                    "brightness": {
                        "type": "integer",
                        "description": "Brightness 0-100 (0 = off)"
                    }
                },
                "required": ["area", "brightness"]
            }
        }
    },
    {
        "type": "function",
        "is_local": "endpoint",
        "function": {
            "name": "ha_area_color",
            "description": "Set color for all RGB lights in an area.",
            "parameters": {
                "type": "object",
                "properties": {
                    "area": {
                        "type": "string",
                        "description": "Area name"
                    },
                    "r": {"type": "integer", "description": "Red 0-255"},
                    "g": {"type": "integer", "description": "Green 0-255"},
                    "b": {"type": "integer", "description": "Blue 0-255"}
                },
                "required": ["area", "r", "g", "b"]
            }
        }
    },
    {
        "type": "function",
        "is_local": "endpoint",
        "function": {
            "name": "ha_get_thermostat",
            "description": "Get current thermostat temperature.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "is_local": "endpoint",
        "function": {
            "name": "ha_set_thermostat",
            "description": "Set thermostat target temperature.",
            "parameters": {
                "type": "object",
                "properties": {
                    "temp": {
                        "type": "number",
                        "description": "Target temperature"
                    }
                },
                "required": ["temp"]
            }
        }
    },
    {
        "type": "function",
        "is_local": "endpoint",
        "function": {
            "name": "ha_list_lights_and_switches",
            "description": "List all available lights and switches with their type.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "is_local": "endpoint",
        "function": {
            "name": "ha_set_light",
            "description": "Control a specific light by name. Brightness 0 = off.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Light name (friendly name or entity_id)"
                    },
                    "brightness": {
                        "type": "integer",
                        "description": "Brightness 0-100 (0 = off)"
                    },
                    "r": {"type": "integer", "description": "Optional red 0-255"},
                    "g": {"type": "integer", "description": "Optional green 0-255"},
                    "b": {"type": "integer", "description": "Optional blue 0-255"}
                },
                "required": ["name", "brightness"]
            }
        }
    },
    {
        "type": "function",
        "is_local": "endpoint",
        "function": {
            "name": "ha_set_switch",
            "description": "Turn a switch on or off by name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Switch name"
                    },
                    "state": {
                        "type": "string",
                        "enum": ["on", "off"],
                        "description": "Desired state"
                    }
                },
                "required": ["name", "state"]
            }
        }
    },
    {
        "type": "function",
        "is_local": "endpoint",
        "function": {
            "name": "ha_notify",
            "description": "Send a notification to the user's phone via Home Assistant mobile app.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Notification message body"
                    },
                    "title": {
                        "type": "string",
                        "description": "Optional notification title"
                    }
                },
                "required": ["message"]
            }
        }
    },
    {
        "type": "function",
        "is_local": "endpoint",
        "function": {
            "name": "ha_house_status",
            "description": "Get a snapshot of the home status: presence, climate, lights by area, door/window/motion sensors, and active scenes.",
            "parameters": {
                "type": "object",
                "properties": {}
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
        
        settings = DEFAULTS.copy()
        settings['url'] = user_settings.get('url', DEFAULTS['url']).rstrip('/')
        settings['blacklist'] = user_settings.get('blacklist', DEFAULTS['blacklist'])
        settings['notify_service'] = user_settings.get('notify_service', DEFAULTS.get('notify_service', ''))
        return settings
    except Exception as e:
        logger.warning(f"Failed to load HA settings: {e}")
        return DEFAULTS.copy()


def _get_token():
    """Get HA token from credentials manager."""
    try:
        from core.credentials_manager import credentials
        return credentials.get_ha_token()
    except Exception as e:
        logger.error(f"Failed to get HA token: {e}")
        return ''


def _get_headers():
    """Get authorization headers for HA API."""
    token = _get_token()
    if not token:
        return None
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }


def _is_blacklisted(entity_id: str, entity_area: str, blacklist: list) -> bool:
    """Check if entity is blacklisted."""
    for pattern in blacklist:
        if pattern.startswith('area:'):
            area_name = pattern[5:]
            if entity_area.lower() == area_name.lower():
                return True
        elif fnmatch.fnmatch(entity_id, pattern):
            return True
    return False


def _get_all_entities(settings: dict) -> dict:
    """Fetch all entities from HA with area mappings."""
    url = settings['url']
    headers = _get_headers()
    
    if not headers:
        return {"error": "No HA token configured"}
    
    try:
        # Get all states
        response = requests.get(f"{url}/api/states", headers=headers, timeout=15)
        if response.status_code != 200:
            return {"error": f"HA API error: HTTP {response.status_code}"}
        
        all_entities = response.json()
        logger.info(f"HA _get_all_entities: fetched {len(all_entities)} entities")
        
        # Get areas using template API (works on all HA versions)
        areas_list = []
        entity_areas = {}
        
        try:
            # Get all area names
            template_resp = requests.post(
                f"{url}/api/template",
                headers=headers,
                json={"template": "{% for area in areas() %}{{ area_name(area) }}||{% endfor %}"},
                timeout=10
            )
            if template_resp.status_code == 200:
                area_text = template_resp.text.strip()
                areas_list = [a.strip() for a in area_text.split('||') if a.strip()]
                logger.info(f"HA areas via template: {areas_list}")
            else:
                logger.warning(f"HA template API failed: {template_resp.status_code}")
        except Exception as e:
            logger.error(f"HA areas template error: {e}")
        
        # Get area for each light/switch entity using template API
        light_switch_entities = [
            e.get('entity_id') for e in all_entities 
            if e.get('entity_id', '').startswith(('light.', 'switch.', 'scene.', 'script.', 'climate.'))
        ]
        
        if light_switch_entities and areas_list:
            try:
                # Build a template that returns entity_id:area_name pairs
                # Process in batches to avoid huge templates
                batch_size = 50
                for i in range(0, len(light_switch_entities), batch_size):
                    batch = light_switch_entities[i:i+batch_size]
                    template_parts = []
                    for eid in batch:
                        template_parts.append(f"{eid}:{{{{ area_name(area_id('{eid}')) or '' }}}}")
                    
                    template = "||".join(template_parts)
                    
                    resp = requests.post(
                        f"{url}/api/template",
                        headers=headers,
                        json={"template": template},
                        timeout=15
                    )
                    
                    if resp.status_code == 200:
                        pairs = resp.text.strip().split('||')
                        for pair in pairs:
                            if ':' in pair:
                                eid, area = pair.split(':', 1)
                                if area.strip():
                                    entity_areas[eid.strip()] = area.strip()
                
                logger.info(f"HA entity_areas count: {len(entity_areas)}")
                sample = dict(list(entity_areas.items())[:5])
                logger.info(f"HA entity_areas sample: {sample}")
                
            except Exception as e:
                logger.error(f"HA entity areas template error: {e}")
        
        return {
            "entities": all_entities,
            "entity_areas": entity_areas,
            "areas": areas_list
        }
        
    except requests.exceptions.Timeout:
        return {"error": "HA connection timed out"}
    except Exception as e:
        logger.error(f"HA _get_all_entities error: {e}")
        return {"error": str(e)}


def _find_entity(name: str, domain: str, settings: dict) -> tuple:
    """
    Find entity by friendly name or entity_id.
    Returns (entity_id, friendly_name) or (None, error_message).
    """
    data = _get_all_entities(settings)
    if "error" in data:
        return None, data["error"]
    
    blacklist = settings.get('blacklist', [])
    name_lower = name.lower().strip()
    
    for entity in data["entities"]:
        entity_id = entity.get('entity_id', '')
        if not entity_id.startswith(f"{domain}."):
            continue
        
        friendly_name = entity.get('attributes', {}).get('friendly_name', entity_id)
        entity_area = data["entity_areas"].get(entity_id, '')
        
        if _is_blacklisted(entity_id, entity_area, blacklist):
            continue
        
        # Match by friendly name or entity_id
        if (friendly_name.lower() == name_lower or 
            entity_id.lower() == name_lower or
            entity_id.lower() == f"{domain}.{name_lower}"):
            return entity_id, friendly_name
    
    return None, f"Device not found: {name}"


def _call_ha_service(domain: str, service: str, data: dict, settings: dict, allow_timeout: bool = False) -> tuple:
    """Call a Home Assistant service.
    
    Args:
        allow_timeout: If True, treat timeout as success (for slow operations like scenes)
    """
    url = settings['url']
    headers = _get_headers()
    
    if not headers:
        return "No HA token configured", False
    
    try:
        response = requests.post(
            f"{url}/api/services/{domain}/{service}",
            headers=headers,
            json=data,
            timeout=10
        )
        
        if response.status_code == 200:
            return "OK", True
        else:
            return f"HA error: HTTP {response.status_code}", False
            
    except requests.exceptions.Timeout:
        if allow_timeout:
            # Scene/script activations can take 20+ seconds, but HA is processing
            logger.info(f"HA service {domain}/{service} timed out but assuming success")
            return "OK (processing)", True
        return "HA connection timed out", False
    except Exception as e:
        return f"HA error: {e}", False


# =============================================================================
# FUNCTION IMPLEMENTATIONS
# =============================================================================

def _list_scenes_and_scripts(settings: dict) -> tuple:
    """List all scenes and scripts."""
    data = _get_all_entities(settings)
    if "error" in data:
        return data["error"], False
    
    blacklist = settings.get('blacklist', [])
    items = []
    
    for entity in data["entities"]:
        entity_id = entity.get('entity_id', '')
        domain = entity_id.split('.')[0] if '.' in entity_id else ''
        
        if domain not in ('scene', 'script'):
            continue
        
        friendly_name = entity.get('attributes', {}).get('friendly_name', entity_id)
        entity_area = data["entity_areas"].get(entity_id, '')
        
        if _is_blacklisted(entity_id, entity_area, blacklist):
            continue
        
        # Use the part after the dot as the callable name
        short_name = entity_id.split('.', 1)[1] if '.' in entity_id else entity_id
        items.append(f"{short_name} ({domain})")
    
    if not items:
        return "No scenes or scripts found", True
    
    return ", ".join(sorted(items)), True


def _activate(name: str, settings: dict) -> tuple:
    """Activate a scene or run a script."""
    data = _get_all_entities(settings)
    if "error" in data:
        return data["error"], False
    
    blacklist = settings.get('blacklist', [])
    name_lower = name.lower().strip()
    
    # Search for matching scene or script
    for entity in data["entities"]:
        entity_id = entity.get('entity_id', '')
        domain = entity_id.split('.')[0] if '.' in entity_id else ''
        
        if domain not in ('scene', 'script'):
            continue
        
        entity_area = data["entity_areas"].get(entity_id, '')
        if _is_blacklisted(entity_id, entity_area, blacklist):
            continue
        
        short_name = entity_id.split('.', 1)[1] if '.' in entity_id else entity_id
        friendly_name = entity.get('attributes', {}).get('friendly_name', entity_id)
        
        if (short_name.lower() == name_lower or 
            friendly_name.lower() == name_lower or
            entity_id.lower() == name_lower):
            
            if domain == 'scene':
                result, success = _call_ha_service('scene', 'turn_on', {"entity_id": entity_id}, settings, allow_timeout=True)
                if success:
                    return f"Activated scene: {friendly_name}", True
                return result, False
            else:  # script
                result, success = _call_ha_service('script', 'turn_on', {"entity_id": entity_id}, settings, allow_timeout=True)
                if success:
                    return f"Running script: {friendly_name}", True
                return result, False
    
    return f"Scene or script not found: {name}", False


def _list_areas(settings: dict) -> tuple:
    """List all available areas."""
    data = _get_all_entities(settings)
    if "error" in data:
        return data["error"], False
    
    # Get areas from registry
    areas = data.get("areas", [])
    
    # Also collect unique areas from entity_areas mapping
    entity_area_names = set(data.get("entity_areas", {}).values())
    entity_area_names.discard('')
    
    # Combine both sources
    all_areas = set(areas) | entity_area_names
    
    if not all_areas:
        return "No areas found in Home Assistant. Devices may not be assigned to areas.", False
    
    return f"Available areas: {', '.join(sorted(all_areas))}", True


def _normalize_area(name: str) -> str:
    """Normalize area name for comparison: lowercase, strip, collapse whitespace."""
    import re
    return re.sub(r'\s+', ' ', name.lower().strip())


def _area_light(area: str, brightness: int, settings: dict) -> tuple:
    """Set brightness for all lights in an area."""
    data = _get_all_entities(settings)
    if "error" in data:
        return data["error"], False
    
    blacklist = settings.get('blacklist', [])
    area_normalized = _normalize_area(area)
    brightness = max(0, min(100, brightness))
    
    # Debug: log available areas
    all_areas = set(data.get("entity_areas", {}).values())
    all_areas.discard('')
    logger.info(f"HA area_light: looking for '{area}' (normalized: '{area_normalized}')")
    logger.info(f"HA area_light: available areas from entity_areas: {all_areas}")
    logger.info(f"HA area_light: areas from registry: {data.get('areas', [])}")
    
    affected = []
    for entity in data["entities"]:
        entity_id = entity.get('entity_id', '')
        if not entity_id.startswith('light.'):
            continue
        
        entity_area = data["entity_areas"].get(entity_id, '')
        entity_area_normalized = _normalize_area(entity_area)
        
        if entity_area_normalized != area_normalized:
            continue
        
        if _is_blacklisted(entity_id, entity_area, blacklist):
            continue
        
        friendly_name = entity.get('attributes', {}).get('friendly_name', entity_id)
        
        if brightness == 0:
            result, success = _call_ha_service('light', 'turn_off', {"entity_id": entity_id}, settings)
        else:
            ha_brightness = int(brightness * 2.55)  # Convert 0-100 to 0-255
            result, success = _call_ha_service('light', 'turn_on', 
                {"entity_id": entity_id, "brightness": ha_brightness}, settings)
        
        if success:
            affected.append(friendly_name)
    
    if not affected:
        # Provide helpful error with available areas
        if all_areas:
            return f"No lights found in area: {area}. Available areas: {', '.join(sorted(all_areas))}", False
        else:
            return f"No lights found in area: {area}. (No areas detected - check HA area assignments)", False
    
    action = "off" if brightness == 0 else f"{brightness}%"
    return f"Set {len(affected)} lights in {area} to {action}: {', '.join(affected)}", True


def _area_color(area: str, r: int, g: int, b: int, settings: dict) -> tuple:
    """Set color for RGB lights in an area."""
    data = _get_all_entities(settings)
    if "error" in data:
        return data["error"], False
    
    blacklist = settings.get('blacklist', [])
    area_normalized = _normalize_area(area)
    r = max(0, min(255, r))
    g = max(0, min(255, g))
    b = max(0, min(255, b))
    
    # Debug: log available areas
    all_areas = set(data.get("entity_areas", {}).values())
    all_areas.discard('')
    logger.info(f"HA area_color: looking for '{area}' (normalized: '{area_normalized}')")
    
    affected = []
    for entity in data["entities"]:
        entity_id = entity.get('entity_id', '')
        if not entity_id.startswith('light.'):
            continue
        
        entity_area = data["entity_areas"].get(entity_id, '')
        entity_area_normalized = _normalize_area(entity_area)
        
        if entity_area_normalized != area_normalized:
            continue
        
        if _is_blacklisted(entity_id, entity_area, blacklist):
            continue
        
        # Check if light supports RGB
        supported = entity.get('attributes', {}).get('supported_color_modes', [])
        if 'rgb' not in supported and 'hs' not in supported and 'xy' not in supported:
            continue
        
        friendly_name = entity.get('attributes', {}).get('friendly_name', entity_id)
        
        result, success = _call_ha_service('light', 'turn_on',
            {"entity_id": entity_id, "rgb_color": [r, g, b]}, settings)
        
        if success:
            affected.append(friendly_name)
    
    if not affected:
        if all_areas:
            return f"No RGB lights found in area: {area}. Available areas: {', '.join(sorted(all_areas))}", False
        else:
            return f"No RGB lights found in area: {area}. (No areas detected)", False
    
    return f"Set color ({r},{g},{b}) on {len(affected)} lights in {area}", True


def _get_thermostat(settings: dict) -> tuple:
    """Get current thermostat temperature."""
    data = _get_all_entities(settings)
    if "error" in data:
        return data["error"], False
    
    blacklist = settings.get('blacklist', [])
    
    for entity in data["entities"]:
        entity_id = entity.get('entity_id', '')
        if not entity_id.startswith('climate.'):
            continue
        
        entity_area = data["entity_areas"].get(entity_id, '')
        if _is_blacklisted(entity_id, entity_area, blacklist):
            continue
        
        attrs = entity.get('attributes', {})
        current_temp = attrs.get('current_temperature', 'unknown')
        target_temp = attrs.get('temperature', attrs.get('target_temp_high', 'unknown'))
        unit = attrs.get('unit_of_measurement', '°F')
        friendly_name = attrs.get('friendly_name', entity_id)
        
        return f"{friendly_name}: {current_temp}{unit} (target: {target_temp}{unit})", True
    
    return "No thermostat found", False


def _set_thermostat(temp: float, settings: dict) -> tuple:
    """Set thermostat temperature."""
    data = _get_all_entities(settings)
    if "error" in data:
        return data["error"], False
    
    blacklist = settings.get('blacklist', [])
    
    for entity in data["entities"]:
        entity_id = entity.get('entity_id', '')
        if not entity_id.startswith('climate.'):
            continue
        
        entity_area = data["entity_areas"].get(entity_id, '')
        if _is_blacklisted(entity_id, entity_area, blacklist):
            continue
        
        friendly_name = entity.get('attributes', {}).get('friendly_name', entity_id)
        
        result, success = _call_ha_service('climate', 'set_temperature',
            {"entity_id": entity_id, "temperature": temp}, settings)
        
        if success:
            return f"Set {friendly_name} to {temp}°", True
        return result, False
    
    return "No thermostat found", False


def _list_lights_and_switches(settings: dict) -> tuple:
    """List all available lights and switches."""
    data = _get_all_entities(settings)
    if "error" in data:
        return data["error"], False
    
    blacklist = settings.get('blacklist', [])
    items = []
    
    for entity in data["entities"]:
        entity_id = entity.get('entity_id', '')
        domain = entity_id.split('.')[0] if '.' in entity_id else ''
        
        if domain not in ('light', 'switch'):
            continue
        
        friendly_name = entity.get('attributes', {}).get('friendly_name', entity_id)
        entity_area = data["entity_areas"].get(entity_id, '')
        
        if _is_blacklisted(entity_id, entity_area, blacklist):
            continue
        
        items.append(f"{friendly_name} ({domain})")
    
    if not items:
        return "No lights or switches found", True
    
    return ", ".join(sorted(items)), True


def _set_light(name: str, brightness: int, r: int = None, g: int = None, b: int = None, settings: dict = None) -> tuple:
    """Control a specific light."""
    entity_id, result = _find_entity(name, 'light', settings)
    if not entity_id:
        return result, False
    
    brightness = max(0, min(100, brightness))
    
    if brightness == 0:
        result, success = _call_ha_service('light', 'turn_off', {"entity_id": entity_id}, settings)
        if success:
            return f"Turned off {result}", True
        return result, False
    
    service_data = {
        "entity_id": entity_id,
        "brightness": int(brightness * 2.55)
    }
    
    if r is not None and g is not None and b is not None:
        service_data["rgb_color"] = [
            max(0, min(255, r)),
            max(0, min(255, g)),
            max(0, min(255, b))
        ]
    
    result, success = _call_ha_service('light', 'turn_on', service_data, settings)
    if success:
        color_str = f" with color ({r},{g},{b})" if r is not None else ""
        return f"Set {name} to {brightness}%{color_str}", True
    return result, False


def _set_switch(name: str, state: str, settings: dict) -> tuple:
    """Turn a switch on or off."""
    entity_id, result = _find_entity(name, 'switch', settings)
    if not entity_id:
        return result, False
    
    service = 'turn_on' if state.lower() == 'on' else 'turn_off'
    result, success = _call_ha_service('switch', service, {"entity_id": entity_id}, settings)
    
    if success:
        return f"Turned {state} {name}", True
    return result, False


def _notify(message: str, title: str, settings: dict) -> tuple:
    """Send notification to user's phone via HA mobile app."""
    notify_service = settings.get('notify_service', '').strip()
    
    if not notify_service:
        return "Notify service not configured. Set it in Plugins > Home Assistant settings.", False
    
    # Strip 'notify.' prefix if user included it
    if notify_service.startswith('notify.'):
        notify_service = notify_service[7:]
    
    # Build service data
    service_data = {"message": message}
    if title:
        service_data["title"] = title
    
    # Call notify service
    url = settings['url']
    headers = _get_headers()
    
    if not headers:
        return "No HA token configured", False
    
    try:
        endpoint = f"{url}/api/services/notify/{notify_service}"
        logger.info(f"HA notify: calling {endpoint}")
        
        response = requests.post(
            endpoint,
            headers=headers,
            json=service_data,
            timeout=10
        )
        
        logger.info(f"HA notify: response status={response.status_code}")
        
        if response.status_code == 200:
            return f"Notification sent: {message[:50]}{'...' if len(message) > 50 else ''}", True
        elif response.status_code == 404:
            return f"Notify service 'notify.{notify_service}' not found. Check service name in HA Developer Tools > Actions.", False
        elif response.status_code == 401:
            return "HA token invalid or expired", False
        else:
            logger.warning(f"HA notify failed: {response.status_code} - {response.text[:200]}")
            return f"HA notification error: HTTP {response.status_code}", False
            
    except requests.exceptions.Timeout:
        return "HA connection timed out", False
    except Exception as e:
        logger.error(f"HA notify error: {e}")
        return f"Notification error: {e}", False


def _house_status(settings: dict) -> tuple:
    """Get comprehensive house status snapshot."""
    data = _get_all_entities(settings)
    if "error" in data:
        return data["error"], False
    
    blacklist = settings.get('blacklist', [])
    entities = data.get("entities", [])
    entity_areas = data.get("entity_areas", {})
    
    status_parts = []
    
    # --- Climate/Thermostat ---
    climate_info = []
    for entity in entities:
        entity_id = entity.get('entity_id', '')
        if not entity_id.startswith('climate.'):
            continue
        if _is_blacklisted(entity_id, entity_areas.get(entity_id, ''), blacklist):
            continue
        
        attrs = entity.get('attributes', {})
        current = attrs.get('current_temperature')
        target = attrs.get('temperature', attrs.get('target_temp_high'))
        unit = attrs.get('unit_of_measurement', '°F')
        name = attrs.get('friendly_name', entity_id.split('.')[1])
        
        if current is not None:
            temp_str = f"{name}: {current}{unit}"
            if target:
                temp_str += f" (target: {target})"
            climate_info.append(temp_str)
    
    if climate_info:
        status_parts.append(f"Climate: {'; '.join(climate_info)}")
    
    # --- Presence (person.*) ---
    presence_info = []
    for entity in entities:
        entity_id = entity.get('entity_id', '')
        if not entity_id.startswith('person.'):
            continue
        
        name = entity.get('attributes', {}).get('friendly_name', entity_id.split('.')[1])
        state = entity.get('state', 'unknown')
        presence_info.append(f"{name}={state}")
    
    if presence_info:
        status_parts.append(f"Presence: {', '.join(presence_info)}")
    
    # --- Lights by area ---
    area_lights = {}  # area -> {'on': count, 'off': count}
    for entity in entities:
        entity_id = entity.get('entity_id', '')
        if not entity_id.startswith('light.'):
            continue
        
        entity_area = entity_areas.get(entity_id, 'Unknown')
        if _is_blacklisted(entity_id, entity_area, blacklist):
            continue
        
        state = entity.get('state', 'off')
        
        if entity_area not in area_lights:
            area_lights[entity_area] = {'on': 0, 'off': 0}
        
        if state == 'on':
            area_lights[entity_area]['on'] += 1
        else:
            area_lights[entity_area]['off'] += 1
    
    if area_lights:
        light_summaries = []
        for area, counts in sorted(area_lights.items()):
            if counts['on'] > 0:
                light_summaries.append(f"{area}={counts['on']} on")
            else:
                light_summaries.append(f"{area}=off")
        status_parts.append(f"Lights: {', '.join(light_summaries)}")
    
    # --- Door/Window/Motion sensors ---
    sensors = {'door': [], 'window': [], 'motion': [], 'occupancy': []}
    for entity in entities:
        entity_id = entity.get('entity_id', '')
        if not entity_id.startswith('binary_sensor.'):
            continue
        
        entity_area = entity_areas.get(entity_id, '')
        if _is_blacklisted(entity_id, entity_area, blacklist):
            continue
        
        attrs = entity.get('attributes', {})
        device_class = attrs.get('device_class', '')
        
        if device_class in sensors:
            name = attrs.get('friendly_name', entity_id.split('.')[1])
            state = entity.get('state', 'unknown')
            
            # Translate states for readability
            if device_class in ('door', 'window'):
                state_str = 'open' if state == 'on' else 'closed'
            elif device_class in ('motion', 'occupancy'):
                state_str = 'detected' if state == 'on' else 'clear'
            else:
                state_str = state
            
            sensors[device_class].append(f"{name}={state_str}")
    
    for sensor_type, items in sensors.items():
        if items:
            status_parts.append(f"{sensor_type.title()}: {', '.join(items)}")
    
    # --- Active scenes ---
    active_scenes = []
    for entity in entities:
        entity_id = entity.get('entity_id', '')
        if not entity_id.startswith('scene.'):
            continue
        
        # Scenes are "stateless" in HA but some integrations track last activated
        # We'll include scenes that have been activated recently (have a last_changed)
        state = entity.get('state', '')
        
        # In HA, scenes show as 'scening' briefly or have timestamp
        # Skip for now since scenes are always "off" - just list available count
    
    # Instead, show switch states for important switches (non-light)
    switches_on = []
    for entity in entities:
        entity_id = entity.get('entity_id', '')
        if not entity_id.startswith('switch.'):
            continue
        
        entity_area = entity_areas.get(entity_id, '')
        if _is_blacklisted(entity_id, entity_area, blacklist):
            continue
        
        state = entity.get('state', 'off')
        if state == 'on':
            name = entity.get('attributes', {}).get('friendly_name', entity_id.split('.')[1])
            switches_on.append(name)
    
    if switches_on:
        status_parts.append(f"Switches on: {', '.join(switches_on)}")
    
    if not status_parts:
        return "No status data available", True
    
    return '\n'.join(status_parts), True


# =============================================================================
# EXECUTE ROUTER
# =============================================================================

def execute(function_name: str, arguments: dict, config) -> tuple:
    """Execute Home Assistant function. Returns (result_string, success_bool)."""
    settings = _load_settings()
    
    try:
        if function_name == "ha_list_scenes_and_scripts":
            return _list_scenes_and_scripts(settings)
        
        elif function_name == "ha_activate":
            name = arguments.get("name", "")
            if not name:
                return "Missing name parameter", False
            return _activate(name, settings)
        
        elif function_name == "ha_list_areas":
            return _list_areas(settings)
        
        elif function_name == "ha_area_light":
            area = arguments.get("area", "")
            brightness = arguments.get("brightness", 100)
            if not area:
                return "Missing area parameter", False
            return _area_light(area, brightness, settings)
        
        elif function_name == "ha_area_color":
            area = arguments.get("area", "")
            r = arguments.get("r", 255)
            g = arguments.get("g", 255)
            b = arguments.get("b", 255)
            if not area:
                return "Missing area parameter", False
            return _area_color(area, r, g, b, settings)
        
        elif function_name == "ha_get_thermostat":
            return _get_thermostat(settings)
        
        elif function_name == "ha_set_thermostat":
            temp = arguments.get("temp")
            if temp is None:
                return "Missing temp parameter", False
            return _set_thermostat(temp, settings)
        
        elif function_name == "ha_list_lights_and_switches":
            return _list_lights_and_switches(settings)
        
        elif function_name == "ha_set_light":
            name = arguments.get("name", "")
            brightness = arguments.get("brightness", 100)
            r = arguments.get("r")
            g = arguments.get("g")
            b = arguments.get("b")
            if not name:
                return "Missing name parameter", False
            return _set_light(name, brightness, r, g, b, settings)
        
        elif function_name == "ha_set_switch":
            name = arguments.get("name", "")
            state = arguments.get("state", "")
            if not name or not state:
                return "Missing name or state parameter", False
            return _set_switch(name, state, settings)
        
        elif function_name == "ha_notify":
            message = arguments.get("message", "")
            title = arguments.get("title", "")
            if not message:
                return "Missing message parameter", False
            return _notify(message, title, settings)
        
        elif function_name == "ha_house_status":
            return _house_status(settings)
        
        else:
            return f"Unknown function: {function_name}", False
            
    except Exception as e:
        logger.error(f"HA function error for '{function_name}': {e}")
        return f"Home Assistant error: {str(e)}", False