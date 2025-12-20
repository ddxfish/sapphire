# settings_api.py
import os
import json
import logging
from pathlib import Path
from flask import Blueprint, request, jsonify
from core.settings_manager import settings

logger = logging.getLogger(__name__)

# Path for user's chat defaults
CHAT_DEFAULTS_PATH = Path("user/settings/chat_defaults.json")

def create_settings_api():
    """Create and return a Blueprint with settings API routes."""
    bp = Blueprint('settings_api', __name__)
    
    @bp.before_request
    def check_api_key():
        """Require API key for all routes in this blueprint (fail-secure)."""
        from core.setup import get_password_hash
        expected_key = get_password_hash()
        if not expected_key:
            return jsonify({"error": "Setup required"}), 503
        provided_key = request.headers.get('X-API-Key')
        if not provided_key or provided_key != expected_key:
            return jsonify({"error": "Unauthorized"}), 401
    
    @bp.route('/settings', methods=['GET'])
    def get_all_settings():
        """Get all current settings (merged defaults + user overrides)"""
        try:
            all_settings = settings.get_all_settings()
            user_overrides = settings.get_user_overrides()
            
            return jsonify({
                "settings": all_settings,
                "user_overrides": list(user_overrides.keys()),
                "count": len(all_settings)
            })
        except Exception as e:
            logger.error(f"Error getting settings: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500
    
    @bp.route('/settings/<key>', methods=['GET'])
    def get_setting(key):
        """Get a specific setting value"""
        try:
            value = settings.get(key)
            
            if value is None:
                return jsonify({"error": f"Setting '{key}' not found"}), 404
            
            tier = settings.validate_tier(key)
            is_user_override = key in settings.get_user_overrides()
            
            return jsonify({
                "key": key,
                "value": value,
                "tier": tier,
                "user_override": is_user_override
            })
        except Exception as e:
            logger.error(f"Error getting setting '{key}': {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500
    
    @bp.route('/settings/<key>', methods=['PUT'])
    def update_setting(key):
        """Update a specific setting"""
        try:
            data = request.json
            
            if data is None or 'value' not in data:
                return jsonify({"error": "Missing 'value' in request body"}), 400
            
            value = data['value']
            persist = data.get('persist', True)
            
            tier = settings.validate_tier(key)
            settings.set(key, value, persist=persist)
            
            logger.info(f"Updated setting '{key}' to {value} (tier: {tier}, persist: {persist})")
            
            return jsonify({
                "status": "success",
                "key": key,
                "value": value,
                "tier": tier,
                "persisted": persist,
                "message": f"Setting '{key}' updated" + (f" (requires {tier})" if tier != 'hot' else "")
            })
        
        except Exception as e:
            logger.error(f"Error updating setting '{key}': {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500
    
    @bp.route('/settings/<key>', methods=['DELETE'])
    def delete_setting(key):
        """Remove user override for a setting (revert to default)"""
        try:
            if settings.remove_user_override(key):
                default_value = settings.get(key)
                return jsonify({
                    "status": "success",
                    "key": key,
                    "message": f"Removed override for '{key}'",
                    "reverted_to": default_value
                })
            else:
                return jsonify({
                    "error": f"No user override exists for '{key}'"
                }), 404
        
        except Exception as e:
            logger.error(f"Error deleting override for '{key}': {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500
    
    @bp.route('/settings/reload', methods=['POST'])
    def reload_settings():
        """Manually reload settings from disk"""
        try:
            settings.reload()
            return jsonify({
                "status": "success",
                "message": "Settings reloaded from disk",
                "count": len(settings.get_all_settings())
            })
        except Exception as e:
            logger.error(f"Error reloading settings: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500
    
    @bp.route('/settings/reset', methods=['POST'])
    def reset_settings():
        """Reset all settings to defaults (clear user overrides)"""
        try:
            settings.reset_to_defaults()
            settings.save()
            
            return jsonify({
                "status": "success",
                "message": "All settings reset to defaults",
                "count": len(settings.get_all_settings())
            })
        except Exception as e:
            logger.error(f"Error resetting settings: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500
    
    @bp.route('/settings/tiers', methods=['GET'])
    def get_tiers():
        """Get tier classification for all settings"""
        try:
            all_settings = settings.get_all_settings()
            tiers = {
                'hot': [],
                'component': [],
                'restart': []
            }
            
            for key in all_settings.keys():
                tier = settings.validate_tier(key)
                tiers[tier].append(key)
            
            return jsonify({
                "tiers": tiers,
                "counts": {k: len(v) for k, v in tiers.items()}
            })
        except Exception as e:
            logger.error(f"Error getting tiers: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500
    
    @bp.route('/settings/batch', methods=['PUT'])
    def update_settings_batch():
        """Update multiple settings at once"""
        try:
            data = request.json
            
            if data is None or 'settings' not in data:
                return jsonify({"error": "Missing 'settings' in request body"}), 400
            
            settings_dict = data['settings']
            
            if not isinstance(settings_dict, dict):
                return jsonify({"error": "'settings' must be a dictionary"}), 400
            
            validated = {}
            errors = []
            
            for key, value in settings_dict.items():
                try:
                    tier = settings.validate_tier(key)
                    validated[key] = {'value': value, 'tier': tier}
                except Exception as e:
                    errors.append(f"{key}: {str(e)}")
            
            if errors:
                return jsonify({
                    "error": "Validation failed",
                    "details": errors
                }), 400
            
            settings.set_many(settings_dict, persist=True)
            
            results = {}
            restart_required = False
            component_reload_required = False
            
            for key, info in validated.items():
                results[key] = {
                    'status': 'updated',
                    'tier': info['tier']
                }
                if info['tier'] == 'restart':
                    restart_required = True
                elif info['tier'] == 'component':
                    component_reload_required = True
            
            logger.info(f"Batch updated {len(validated)} settings")
            
            return jsonify({
                "status": "success",
                "updated": results,
                "count": len(validated),
                "restart_required": restart_required,
                "component_reload_required": component_reload_required,
                "message": f"Updated {len(validated)} settings"
            })
        
        except Exception as e:
            logger.error(f"Error in batch update: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500
    
    @bp.route('/settings/help', methods=['GET'])
    def get_settings_help():
        """Get help text for all settings"""
        try:
            help_path = Path(__file__).parent / 'settings_help.json'
            
            if not help_path.exists():
                return jsonify({"error": "Help file not found"}), 404
            
            with open(help_path, 'r', encoding='utf-8') as f:
                help_data = json.load(f)
            
            return jsonify({
                "help": help_data,
                "count": len(help_data)
            })
        except Exception as e:
            logger.error(f"Error loading settings help: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500
    
    @bp.route('/settings/help/<key>', methods=['GET'])
    def get_setting_help(key):
        """Get help text for a specific setting"""
        try:
            help_path = Path(__file__).parent.parent / 'settings_help.json'
            
            if not help_path.exists():
                return jsonify({"error": "Help file not found"}), 404
            
            with open(help_path, 'r', encoding='utf-8') as f:
                help_data = json.load(f)
            
            if key not in help_data:
                return jsonify({"error": f"No help available for '{key}'"}), 404
            
            return jsonify({
                "key": key,
                "help": help_data[key]
            })
        except Exception as e:
            logger.error(f"Error loading help for '{key}': {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500
    
    @bp.route('/settings/chat-defaults', methods=['GET'])
    def get_chat_defaults():
        """Get user's chat defaults (or system defaults if none set)"""
        try:
            from core.history import SYSTEM_DEFAULTS, get_user_defaults
            
            user_defaults = get_user_defaults()
            has_custom = CHAT_DEFAULTS_PATH.exists()
            
            return jsonify({
                "defaults": user_defaults,
                "has_custom_defaults": has_custom,
                "system_defaults": SYSTEM_DEFAULTS
            })
        except Exception as e:
            logger.error(f"Error getting chat defaults: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500
    
    @bp.route('/settings/chat-defaults', methods=['PUT'])
    def save_chat_defaults():
        """Save user's chat defaults for new chats"""
        try:
            data = request.json
            if not data:
                return jsonify({"error": "No data provided"}), 400
            
            # Ensure directory exists
            CHAT_DEFAULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
            
            # Save the defaults
            with open(CHAT_DEFAULTS_PATH, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            
            logger.info(f"Saved chat defaults: {list(data.keys())}")
            return jsonify({
                "status": "success",
                "message": "Chat defaults saved",
                "defaults": data
            })
        except Exception as e:
            logger.error(f"Error saving chat defaults: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500
    
    @bp.route('/settings/chat-defaults', methods=['DELETE'])
    def reset_chat_defaults():
        """Reset chat defaults to system defaults"""
        try:
            if CHAT_DEFAULTS_PATH.exists():
                CHAT_DEFAULTS_PATH.unlink()
                logger.info("Deleted user chat defaults")
                return jsonify({
                    "status": "success",
                    "message": "Chat defaults reset to system defaults"
                })
            else:
                return jsonify({
                    "status": "success",
                    "message": "Already using system defaults"
                })
        except Exception as e:
            logger.error(f"Error resetting chat defaults: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500
    
    @bp.route('/settings/wakeword-models', methods=['GET'])
    def get_wakeword_models():
        """Get available wakeword models (builtin + custom from user/wakeword/models/)"""
        try:
            from core.wakeword import get_available_models
            models = get_available_models()
            
            return jsonify({
                "status": "success",
                "builtin": models['builtin'],
                "custom": models['custom'],
                "all": models['all'],
                "count": len(models['all'])
            })
        except Exception as e:
            logger.error(f"Error getting wakeword models: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500
    
    return bp