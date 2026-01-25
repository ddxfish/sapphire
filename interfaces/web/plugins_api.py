# plugins_api.py - Flask Blueprint for plugin management
# Handles plugin enable/disable and per-plugin settings

import os
import json
import logging
from functools import wraps
from flask import Blueprint, jsonify, request, session

logger = logging.getLogger(__name__)

plugins_bp = Blueprint('plugins', __name__)

# Paths
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATIC_PLUGINS_JSON = os.path.join(PROJECT_ROOT, 'interfaces', 'web', 'static', 'plugins', 'plugins.json')
USER_WEBUI_DIR = os.path.join(PROJECT_ROOT, 'user', 'webui')
USER_PLUGINS_JSON = os.path.join(USER_WEBUI_DIR, 'plugins.json')
USER_PLUGIN_SETTINGS_DIR = os.path.join(USER_WEBUI_DIR, 'plugins')

# Plugins that cannot be disabled
LOCKED_PLUGINS = ['settings-modal', 'plugins-modal']


def require_login(f):
    """Simple login check for blueprint routes."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


def ensure_user_dirs():
    """Create user directories if they don't exist."""
    os.makedirs(USER_WEBUI_DIR, exist_ok=True)
    os.makedirs(USER_PLUGIN_SETTINGS_DIR, exist_ok=True)


def load_static_plugins():
    """Load the static plugins.json (shipped with app)."""
    try:
        with open(STATIC_PLUGINS_JSON, 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load static plugins.json: {e}")
        return {"enabled": [], "plugins": {}}


def load_user_plugins():
    """Load user's plugins.json override (if exists)."""
    if not os.path.exists(USER_PLUGINS_JSON):
        return None
    try:
        with open(USER_PLUGINS_JSON, 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load user plugins.json: {e}")
        return None


def save_user_plugins(data):
    """Save user's plugins.json override."""
    ensure_user_dirs()
    try:
        with open(USER_PLUGINS_JSON, 'w') as f:
            json.dump(data, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Failed to save user plugins.json: {e}")
        return False


def get_merged_plugins():
    """
    Merge static and user plugins.json.
    User overrides enabled list, static provides defaults for new plugins.
    """
    static = load_static_plugins()
    user = load_user_plugins()
    
    if user is None:
        return static
    
    # Start with static as base
    merged = {
        "enabled": user.get("enabled", static.get("enabled", [])),
        "plugins": dict(static.get("plugins", {}))
    }
    
    # Merge in any user plugin metadata overrides
    if "plugins" in user:
        merged["plugins"].update(user["plugins"])
    
    # Ensure locked plugins are always enabled
    for locked in LOCKED_PLUGINS:
        if locked not in merged["enabled"]:
            merged["enabled"].append(locked)
    
    return merged


def load_plugin_settings(plugin_name):
    """Load settings for a specific plugin."""
    settings_file = os.path.join(USER_PLUGIN_SETTINGS_DIR, f"{plugin_name}.json")
    if not os.path.exists(settings_file):
        return {}
    try:
        with open(settings_file, 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load settings for {plugin_name}: {e}")
        return {}


def save_plugin_settings(plugin_name, settings):
    """Save settings for a specific plugin."""
    ensure_user_dirs()
    settings_file = os.path.join(USER_PLUGIN_SETTINGS_DIR, f"{plugin_name}.json")
    try:
        with open(settings_file, 'w') as f:
            json.dump(settings, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Failed to save settings for {plugin_name}: {e}")
        return False


# =============================================================================
# ROUTES
# =============================================================================

@plugins_bp.route('/api/webui/plugins', methods=['GET'])
@require_login
def list_plugins():
    """
    List all plugins with their enabled status and metadata.
    Returns merged view of static + user config.
    """
    merged = get_merged_plugins()
    enabled_set = set(merged.get("enabled", []))
    
    result = []
    for name, meta in merged.get("plugins", {}).items():
        result.append({
            "name": name,
            "enabled": name in enabled_set,
            "locked": name in LOCKED_PLUGINS,
            "title": meta.get("title", name),
            "showInSidebar": meta.get("showInSidebar", True),
            "collapsible": meta.get("collapsible", True)
        })
    
    return jsonify({
        "plugins": result,
        "locked": LOCKED_PLUGINS
    })


@plugins_bp.route('/api/webui/plugins/toggle/<plugin_name>', methods=['PUT'])
@require_login
def toggle_plugin(plugin_name):
    """Toggle a plugin's enabled status."""
    if plugin_name in LOCKED_PLUGINS:
        return jsonify({"error": f"Cannot disable locked plugin: {plugin_name}"}), 403
    
    merged = get_merged_plugins()
    static = load_static_plugins()
    
    # Check plugin exists
    if plugin_name not in merged.get("plugins", {}):
        return jsonify({"error": f"Unknown plugin: {plugin_name}"}), 404
    
    enabled = list(merged.get("enabled", []))
    
    if plugin_name in enabled:
        enabled.remove(plugin_name)
        new_state = False
    else:
        enabled.append(plugin_name)
        new_state = True
    
    # Save user override
    user_data = load_user_plugins() or {}
    user_data["enabled"] = enabled
    
    if save_user_plugins(user_data):
        return jsonify({
            "status": "success",
            "plugin": plugin_name,
            "enabled": new_state,
            "reload_required": True
        })
    else:
        return jsonify({"error": "Failed to save plugin state"}), 500


@plugins_bp.route('/api/webui/plugins/<plugin_name>/settings', methods=['GET'])
@require_login
def get_plugin_settings(plugin_name):
    """Get settings for a specific plugin."""
    settings = load_plugin_settings(plugin_name)
    return jsonify({
        "plugin": plugin_name,
        "settings": settings
    })


@plugins_bp.route('/api/webui/plugins/<plugin_name>/settings', methods=['PUT'])
@require_login
def update_plugin_settings(plugin_name):
    """Update settings for a specific plugin."""
    data = request.json
    if data is None:
        return jsonify({"error": "No JSON body provided"}), 400
    
    settings = data.get("settings", data)
    
    if save_plugin_settings(plugin_name, settings):
        return jsonify({
            "status": "success",
            "plugin": plugin_name,
            "settings": settings
        })
    else:
        return jsonify({"error": "Failed to save settings"}), 500


@plugins_bp.route('/api/webui/plugins/<plugin_name>/settings', methods=['DELETE'])
@require_login
def reset_plugin_settings(plugin_name):
    """Delete/reset settings for a specific plugin."""
    settings_file = os.path.join(USER_PLUGIN_SETTINGS_DIR, f"{plugin_name}.json")
    
    if os.path.exists(settings_file):
        try:
            os.remove(settings_file)
            return jsonify({
                "status": "success",
                "plugin": plugin_name,
                "message": "Settings reset"
            })
        except Exception as e:
            return jsonify({"error": f"Failed to delete settings: {e}"}), 500
    else:
        return jsonify({
            "status": "success",
            "plugin": plugin_name,
            "message": "No settings to reset"
        })


@plugins_bp.route('/api/webui/plugins/config', methods=['GET'])
@require_login
def get_plugins_config():
    """
    Get the full merged plugins config.
    Used by frontend to load plugins.json equivalent.
    """
    return jsonify(get_merged_plugins())


@plugins_bp.route('/api/webui/plugins/image-gen/test-connection', methods=['POST'])
@require_login
def test_sdxl_connection():
    """Test connection to SDXL server."""
    import requests as req
    
    data = request.json or {}
    url = data.get('url', '').strip()
    
    if not url:
        return jsonify({"success": False, "error": "No URL provided"}), 400
    
    # Basic URL validation
    if not url.startswith(('http://', 'https://')):
        return jsonify({"success": False, "error": "URL must start with http:// or https://"}), 400
    
    try:
        # Try to hit the SDXL server's root or a known endpoint
        response = req.get(url, timeout=5)
        
        return jsonify({
            "success": True,
            "status_code": response.status_code,
            "message": f"Connected successfully (HTTP {response.status_code})"
        })
    except req.exceptions.Timeout:
        return jsonify({"success": False, "error": "Connection timed out (5s)"}), 200
    except req.exceptions.ConnectionError as e:
        return jsonify({"success": False, "error": f"Cannot connect: {str(e)[:100]}"}), 200
    except Exception as e:
        return jsonify({"success": False, "error": f"Error: {str(e)[:100]}"}), 200


@plugins_bp.route('/api/webui/plugins/image-gen/defaults', methods=['GET'])
@require_login
def get_image_gen_defaults():
    """Get default settings for image-gen plugin from backend source of truth."""
    try:
        from functions.image import DEFAULTS
        return jsonify(DEFAULTS)
    except ImportError as e:
        logger.error(f"Failed to import image.py DEFAULTS: {e}")
        return jsonify({"error": "Could not load defaults"}), 500


# =============================================================================
# HOME ASSISTANT PLUGIN ROUTES
# =============================================================================

@plugins_bp.route('/api/webui/plugins/homeassistant/defaults', methods=['GET'])
@require_login
def get_ha_defaults():
    """Get default settings for Home Assistant plugin."""
    return jsonify({
        "url": "http://homeassistant.local:8123",
        "blacklist": ["cover.*", "lock.*"]
    })


@plugins_bp.route('/api/webui/plugins/homeassistant/test-connection', methods=['POST'])
@require_login
def test_ha_connection():
    """Test connection to Home Assistant with token validation."""
    import requests as req
    from core.credentials_manager import credentials
    
    data = request.json or {}
    url = data.get('url', '').strip().rstrip('/')
    token = data.get('token', '').strip()
    
    logger.info(f"HA test-connection: url={url}, token_from_request={bool(token)}, token_len={len(token) if token else 0}")
    
    # Use provided token or fall back to stored
    if not token:
        token = credentials.get_ha_token()
        logger.info(f"HA test-connection: fetched from credentials, found={bool(token)}, len={len(token) if token else 0}")
        # Debug: check raw credentials state
        raw_ha = credentials._credentials.get('homeassistant', {})
        logger.info(f"HA test-connection: raw credentials homeassistant section exists={bool(raw_ha)}, has_token_key={'token' in raw_ha}")
    
    if not url:
        return jsonify({"success": False, "error": "No URL provided"}), 400
    
    if not token:
        return jsonify({"success": False, "error": "No API token found. Enter token in form or check stored credentials."}), 400
    
    # Validate token length - HA LLA tokens are typically 180+ chars
    if len(token) < 100:
        logger.warning(f"HA test-connection: token seems too short ({len(token)} chars), HA tokens are usually 180+")
        return jsonify({"success": False, "error": f"Token too short ({len(token)} chars). HA Long-Lived Access Tokens are ~180+ characters. Did you copy the full token?"}), 400
    
    if not url.startswith(('http://', 'https://')):
        return jsonify({"success": False, "error": "URL must start with http:// or https://"}), 400
    
    try:
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        logger.info(f"HA test-connection: calling {url}/api/")
        response = req.get(f"{url}/api/", headers=headers, timeout=10)
        
        logger.info(f"HA test-connection: response status={response.status_code}")
        
        if response.status_code == 200:
            resp_data = response.json()
            # HA /api/ returns {"message": "API running."} - no version
            message = resp_data.get('message', 'Connected')
            return jsonify({
                "success": True,
                "message": message
            })
        elif response.status_code == 401:
            return jsonify({"success": False, "error": "Invalid API token (401 Unauthorized). Regenerate token in HA."}), 200
        elif response.status_code == 403:
            return jsonify({"success": False, "error": "Access forbidden (403) - check token permissions"}), 200
        else:
            return jsonify({"success": False, "error": f"HTTP {response.status_code}"}), 200
            
    except req.exceptions.Timeout:
        return jsonify({"success": False, "error": "Connection timed out (10s)"}), 200
    except req.exceptions.ConnectionError as e:
        error_msg = str(e)
        if 'getaddrinfo failed' in error_msg or 'Name or service not known' in error_msg:
            return jsonify({"success": False, "error": f"Cannot resolve hostname. Check URL."}), 200
        return jsonify({"success": False, "error": f"Cannot connect: {error_msg[:100]}"}), 200
    except Exception as e:
        logger.error(f"HA test-connection error: {e}")
        return jsonify({"success": False, "error": f"Error: {str(e)[:100]}"}), 200


@plugins_bp.route('/api/webui/plugins/homeassistant/entities', methods=['POST'])
@require_login
def get_ha_entities():
    """Fetch entities from Home Assistant with blacklist filtering."""
    import requests as req
    from core.credentials_manager import credentials
    import fnmatch
    
    data = request.json or {}
    url = data.get('url', '').strip().rstrip('/')
    token = data.get('token', '').strip()
    blacklist = data.get('blacklist', [])
    
    if not token:
        token = credentials.get_ha_token()
    
    if not url or not token:
        return jsonify({"error": "URL and token required"}), 400
    
    try:
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        response = req.get(f"{url}/api/states", headers=headers, timeout=15)
        
        if response.status_code != 200:
            return jsonify({"error": f"HA API error: HTTP {response.status_code}"}), 200
        
        all_entities = response.json()
        
        # Get areas using template API (works on all HA versions)
        areas_list = []
        entity_areas = {}
        
        try:
            # Get all area names
            template_resp = req.post(
                f"{url}/api/template",
                headers=headers,
                json={"template": "{% for area in areas() %}{{ area_name(area) }}||{% endfor %}"},
                timeout=10
            )
            if template_resp.status_code == 200:
                area_text = template_resp.text.strip()
                areas_list = [a.strip() for a in area_text.split('||') if a.strip()]
                logger.info(f"HA entities preview - areas: {areas_list}")
        except Exception as e:
            logger.warning(f"HA areas template error: {e}")
        
        # Get area for each relevant entity using template API
        relevant_entities = [
            e.get('entity_id') for e in all_entities 
            if e.get('entity_id', '').startswith(('light.', 'switch.', 'scene.', 'script.', 'climate.'))
        ]
        
        if relevant_entities and areas_list:
            try:
                # Process in batches
                batch_size = 50
                for i in range(0, len(relevant_entities), batch_size):
                    batch = relevant_entities[i:i+batch_size]
                    template_parts = []
                    for eid in batch:
                        template_parts.append(f"{eid}:{{{{ area_name(area_id('{eid}')) or '' }}}}")
                    
                    template = "||".join(template_parts)
                    
                    resp = req.post(
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
            except Exception as e:
                logger.warning(f"HA entity areas template error: {e}")
        
        # Filter entities
        filtered = {"lights": [], "switches": [], "scenes": [], "scripts": [], "climate": [], "areas": areas_list}
        
        for entity in all_entities:
            entity_id = entity.get('entity_id', '')
            friendly_name = entity.get('attributes', {}).get('friendly_name', entity_id)
            domain = entity_id.split('.')[0] if '.' in entity_id else ''
            entity_area = entity_areas.get(entity_id, '')
            
            # Check blacklist
            blocked = False
            for pattern in blacklist:
                if pattern.startswith('area:'):
                    area_name = pattern[5:]
                    if entity_area.lower() == area_name.lower():
                        blocked = True
                        break
                elif fnmatch.fnmatch(entity_id, pattern):
                    blocked = True
                    break
            
            if blocked:
                continue
            
            entry = {"id": entity_id, "name": friendly_name, "area": entity_area}
            
            if domain == 'light':
                filtered['lights'].append(entry)
            elif domain == 'switch':
                filtered['switches'].append(entry)
            elif domain == 'scene':
                filtered['scenes'].append(entry)
            elif domain == 'script':
                filtered['scripts'].append(entry)
            elif domain == 'climate':
                filtered['climate'].append(entry)
        
        return jsonify({
            "success": True,
            "entities": filtered,
            "counts": {k: len(v) for k, v in filtered.items() if k != 'areas'},
            "areas": areas_list
        })
        
    except req.exceptions.Timeout:
        return jsonify({"error": "Connection timed out"}), 200
    except Exception as e:
        logger.error(f"HA entities fetch error: {e}")
        return jsonify({"error": str(e)[:200]}), 200


@plugins_bp.route('/api/webui/plugins/homeassistant/token', methods=['PUT'])
@require_login
def set_ha_token():
    """Store Home Assistant token via credentials manager."""
    from core.credentials_manager import credentials
    
    data = request.json or {}
    token = data.get('token', '').strip()
    
    logger.info(f"HA token PUT: received token len={len(token) if token else 0}")
    
    if credentials.set_ha_token(token):
        # Verify it was saved
        verify = credentials.get_ha_token()
        logger.info(f"HA token PUT: saved, verify len={len(verify) if verify else 0}")
        return jsonify({"success": True, "has_token": bool(token), "saved_len": len(token)})
    else:
        logger.error("HA token PUT: save failed")
        return jsonify({"error": "Failed to save token"}), 500


@plugins_bp.route('/api/webui/plugins/homeassistant/token', methods=['GET'])
@require_login
def get_ha_token_status():
    """Check if HA token is stored (doesn't return actual token)."""
    from core.credentials_manager import credentials
    has_token = credentials.has_ha_token()
    token_len = len(credentials.get_ha_token()) if has_token else 0
    logger.info(f"HA token GET: has_token={has_token}, len={token_len}")
    return jsonify({"has_token": has_token, "token_length": token_len})