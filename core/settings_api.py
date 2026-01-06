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
            success = settings.reset_to_defaults()
            
            if not success:
                return jsonify({"error": "Failed to reset settings file"}), 500
            
            return jsonify({
                "status": "success",
                "message": "All settings reset to defaults. Restart required.",
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
            
            # Collect keys that require restart for UI feedback
            restart_keys = [k for k, v in validated.items() if v['tier'] == 'restart']
            
            return jsonify({
                "status": "success",
                "updated": results,
                "count": len(validated),
                "restart_required": restart_required,
                "restart_keys": restart_keys,
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
            help_path = Path(__file__).parent / 'settings_help.json'
            
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
    
    # =========================================================================
    # LLM PROVIDER ENDPOINTS
    # =========================================================================
    
    @bp.route('/llm/providers', methods=['GET'])
    def get_llm_providers():
        """Get all configured LLM providers with metadata for UI."""
        try:
            from core.chat.llm_providers import get_available_providers, PROVIDER_METADATA
            import os
            
            providers_config = settings.get('LLM_PROVIDERS', {})
            fallback_order = settings.get('LLM_FALLBACK_ORDER', [])
            
            providers = get_available_providers(providers_config)
            
            # Add env var status for each provider
            for p in providers:
                key = p['key']
                config = providers_config.get(key, {})
                meta = PROVIDER_METADATA.get(key, {})
                env_var = config.get('api_key_env') or meta.get('api_key_env', '')
                p['env_var'] = env_var
                p['has_env_key'] = bool(env_var and os.environ.get(env_var))
                p['has_config_key'] = bool(config.get('api_key', '').strip())
            
            return jsonify({
                "status": "success",
                "providers": providers,
                "fallback_order": fallback_order,
                "metadata": PROVIDER_METADATA,
                "count": len(providers)
            })
        except Exception as e:
            logger.error(f"Error getting LLM providers: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500
    
    @bp.route('/llm/providers/<provider_key>', methods=['PUT'])
    def update_llm_provider(provider_key):
        """Update a specific LLM provider config."""
        try:
            data = request.json
            if not data:
                return jsonify({"error": "No data provided"}), 400
            
            providers_config = settings.get('LLM_PROVIDERS', {})
            
            if provider_key not in providers_config:
                return jsonify({"error": f"Unknown provider: {provider_key}"}), 404
            
            # Merge updates into existing config
            providers_config[provider_key].update(data)
            settings.set('LLM_PROVIDERS', providers_config, persist=True)
            
            logger.info(f"Updated LLM provider '{provider_key}': {list(data.keys())}")
            
            return jsonify({
                "status": "success",
                "provider": provider_key,
                "config": providers_config[provider_key]
            })
        except Exception as e:
            logger.error(f"Error updating LLM provider: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500
    
    @bp.route('/llm/fallback-order', methods=['PUT'])
    def update_fallback_order():
        """Update LLM fallback order."""
        try:
            data = request.json
            if not data or 'order' not in data:
                return jsonify({"error": "Missing 'order' in request"}), 400
            
            new_order = data['order']
            if not isinstance(new_order, list):
                return jsonify({"error": "'order' must be a list"}), 400
            
            settings.set('LLM_FALLBACK_ORDER', new_order, persist=True)
            
            logger.info(f"Updated LLM fallback order: {new_order}")
            
            return jsonify({
                "status": "success",
                "fallback_order": new_order
            })
        except Exception as e:
            logger.error(f"Error updating fallback order: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500
    
    @bp.route('/llm/test/<provider_key>', methods=['POST'])
    def test_llm_provider(provider_key):
        """Test an LLM provider with a hello round-trip.
        Optionally accepts JSON body with config overrides to test unsaved values."""
        try:
            from core.chat.llm_providers import get_provider_by_key, get_api_key, PROVIDER_METADATA
            import os
            
            providers_config = settings.get('LLM_PROVIDERS', {})
            
            if provider_key not in providers_config:
                return jsonify({"error": f"Unknown provider: {provider_key}"}), 404
            
            # Start with saved config
            provider_config = dict(providers_config[provider_key])
            
            # Override with form values if provided
            form_data = request.json or {}
            if form_data:
                for field in ['base_url', 'api_key', 'model', 'timeout']:
                    if field in form_data and form_data[field]:
                        provider_config[field] = form_data[field]
            
            # Check API key source for debugging
            api_key = get_api_key(provider_config, provider_key)
            api_key_source = "none"
            if provider_config.get('api_key', '').strip():
                api_key_source = "config"
            elif api_key and api_key != 'not-needed':
                meta = PROVIDER_METADATA.get(provider_key, {})
                env_var = provider_config.get('api_key_env', '') or meta.get('api_key_env', '')
                api_key_source = f"env:{env_var}"
            elif api_key == 'not-needed':
                api_key_source = "local"
            
            logger.info(f"Testing provider '{provider_key}': api_key_source={api_key_source}, has_key={bool(api_key)}")
            
            # Temporarily enable for test even if disabled
            test_config = {**providers_config, provider_key: {**provider_config, 'enabled': True}}
            
            provider = get_provider_by_key(
                provider_key, 
                test_config, 
                settings.get('LLM_REQUEST_TIMEOUT', 240.0)
            )
            
            if not provider:
                return jsonify({
                    "status": "error",
                    "error": "Failed to create provider instance",
                    "details": f"API key source: {api_key_source}, has_key: {bool(api_key)}",
                    "api_key_source": api_key_source
                }), 400
            
            # Health check first
            try:
                healthy = provider.health_check()
                if not healthy:
                    return jsonify({
                        "status": "error",
                        "error": "Health check failed",
                        "details": "Provider endpoint not reachable",
                        "api_key_source": api_key_source
                    }), 503
            except Exception as health_err:
                return jsonify({
                    "status": "error",
                    "error": "Health check failed",
                    "details": str(health_err),
                    "api_key_source": api_key_source
                }), 503
            
            # Actual hello test
            try:
                test_messages = [
                    {"role": "user", "content": "Say 'Hello from Sapphire!' and nothing else."}
                ]
                
                response = provider.chat_completion(
                    messages=test_messages,
                    generation_params={"max_tokens": 50, "temperature": 0.1}
                )
                
                reply = response.content or ""
                
                return jsonify({
                    "status": "success",
                    "provider": provider_key,
                    "model": provider.model,
                    "response": reply[:200],
                    "usage": response.usage,
                    "api_key_source": api_key_source
                })
                
            except Exception as chat_err:
                return jsonify({
                    "status": "error",
                    "error": "Chat test failed",
                    "details": str(chat_err)[:300],
                    "api_key_source": api_key_source
                }), 500
                
        except Exception as e:
            logger.error(f"Error testing LLM provider: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500
    
    return bp