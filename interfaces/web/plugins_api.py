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