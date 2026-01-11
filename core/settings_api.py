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
            
            # Filter out invalid keys (undefined, null, empty)
            settings_dict = {
                k: v for k, v in settings_dict.items() 
                if k and k not in ('undefined', 'null', 'None', '')
            }
            
            if not settings_dict:
                return jsonify({
                    "status": "success",
                    "updated": {},
                    "count": 0,
                    "restart_required": False,
                    "restart_keys": [],
                    "message": "No valid settings to update"
                })
            
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
            
            for key, info in validated.items():
                results[key] = {
                    'status': 'updated',
                    'tier': info['tier']
                }
                if info['tier'] == 'restart':
                    restart_required = True
            
            logger.info(f"Batch updated {len(validated)} settings")
            
            # Collect keys that require restart for UI feedback
            restart_keys = [k for k, v in validated.items() if v['tier'] == 'restart']
            
            return jsonify({
                "status": "success",
                "updated": results,
                "count": len(validated),
                "restart_required": restart_required,
                "restart_keys": restart_keys,
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
        """
        Test an LLM provider with a single hello request.
        Returns rich error details for common failure modes.
        Accepts JSON body with config overrides to test unsaved values.
        """
        try:
            from core.chat.llm_providers import get_provider_by_key, get_api_key, PROVIDER_METADATA
            import requests as req_lib
            
            providers_config = settings.get('LLM_PROVIDERS', {})
            
            if provider_key not in providers_config:
                return jsonify({
                    "status": "error",
                    "error": "Unknown provider",
                    "details": f"Provider '{provider_key}' not configured"
                }), 404
            
            # Build test config from saved + form overrides
            provider_config = dict(providers_config[provider_key])
            form_data = request.json or {}
            for field in ['base_url', 'api_key', 'model', 'timeout']:
                if field in form_data and form_data[field]:
                    provider_config[field] = form_data[field]
            
            # Check API key status for helpful errors
            api_key = get_api_key(provider_config, provider_key)
            meta = PROVIDER_METADATA.get(provider_key, {})
            is_local = meta.get('is_local', False)
            
            if not is_local and not api_key:
                env_var = provider_config.get('api_key_env', '') or meta.get('api_key_env', '')
                return jsonify({
                    "status": "error",
                    "error": "No API key",
                    "details": f"Set API key in field or {env_var} env var" if env_var else "API key required"
                }), 400
            
            # Check required fields
            if not is_local and not provider_config.get('base_url') and provider_key not in ('claude',):
                return jsonify({
                    "status": "error",
                    "error": "No base URL",
                    "details": "Base URL required for this provider"
                }), 400
            
            if not provider_config.get('model') and meta.get('model_options'):
                return jsonify({
                    "status": "error",
                    "error": "No model selected",
                    "details": "Select a model from dropdown or enter custom"
                }), 400
            
            # Create provider instance
            test_config = {**providers_config, provider_key: {**provider_config, 'enabled': True}}
            
            try:
                provider = get_provider_by_key(
                    provider_key, 
                    test_config, 
                    settings.get('LLM_REQUEST_TIMEOUT', 240.0)
                )
            except ImportError as e:
                return jsonify({
                    "status": "error",
                    "error": "Missing dependency",
                    "details": str(e)
                }), 500
            except Exception as e:
                return jsonify({
                    "status": "error",
                    "error": "Provider init failed",
                    "details": str(e)[:200]
                }), 500
            
            if not provider:
                return jsonify({
                    "status": "error",
                    "error": "Provider creation failed",
                    "details": "Check configuration"
                }), 500
            
            # Single test: send hello message
            test_messages = [{"role": "user", "content": "Say 'Hello from Sapphire!' and nothing else."}]
            
            try:
                response = provider.chat_completion(
                    messages=test_messages,
                    generation_params={"max_tokens": 50, "temperature": 0.1}
                )
                
                return jsonify({
                    "status": "success",
                    "provider": provider_key,
                    "model": provider.model,
                    "response": (response.content or "")[:200],
                    "usage": response.usage
                })
                
            except Exception as e:
                return _parse_provider_error(e, provider_key, provider_config, meta)
                
        except Exception as e:
            logger.error(f"Test endpoint error: {e}", exc_info=True)
            return jsonify({
                "status": "error",
                "error": "Internal error",
                "details": str(e)[:200]
            }), 500
    
    def _parse_provider_error(error, provider_key, config, meta):
        """Parse provider exceptions into user-friendly error responses."""
        import requests as req_lib
        
        error_str = str(error).lower()
        error_full = str(error)[:300]
        
        # Connection errors
        if any(x in error_str for x in ['connection refused', 'failed to establish', 'no route to host', 'name or service not known', 'getaddrinfo failed']):
            url = config.get('base_url', 'endpoint')
            return jsonify({
                "status": "error",
                "error": "Connection failed",
                "details": f"Cannot reach {url} - check URL and network"
            }), 503
        
        # Timeout
        if 'timeout' in error_str or 'timed out' in error_str:
            return jsonify({
                "status": "error",
                "error": "Request timed out",
                "details": "Server too slow to respond - try increasing timeout or check server"
            }), 504
        
        # Auth errors (check error message and common HTTP codes)
        if any(x in error_str for x in ['401', 'unauthorized', 'invalid api key', 'invalid_api_key', 'authentication']):
            return jsonify({
                "status": "error",
                "error": "Authentication failed",
                "details": "API key invalid, expired, or missing permissions"
            }), 401
        
        if '403' in error_str or 'forbidden' in error_str:
            return jsonify({
                "status": "error",
                "error": "Access denied",
                "details": "API key doesn't have permission for this operation"
            }), 403
        
        # Model not found
        if any(x in error_str for x in ['404', 'not found', 'model_not_found', 'does not exist', 'invalid model']):
            model = config.get('model', 'unknown')
            return jsonify({
                "status": "error",
                "error": "Model not found",
                "details": f"Model '{model}' not available - check model name"
            }), 404
        
        # Rate limiting
        if '429' in error_str or 'rate limit' in error_str or 'too many requests' in error_str:
            return jsonify({
                "status": "error",
                "error": "Rate limited",
                "details": "Too many requests - wait a moment and retry"
            }), 429
        
        # Server errors
        if any(x in error_str for x in ['500', '502', '503', 'internal server error', 'bad gateway', 'service unavailable']):
            return jsonify({
                "status": "error",
                "error": "Server error",
                "details": "Provider server error - try again later"
            }), 503
        
        # Overloaded (Claude-specific)
        if 'overloaded' in error_str:
            return jsonify({
                "status": "error",
                "error": "Server overloaded",
                "details": "Provider is overloaded - try again in a few minutes"
            }), 503
        
        # Credit/billing issues
        if any(x in error_str for x in ['insufficient', 'credit', 'billing', 'quota', 'exceeded']):
            return jsonify({
                "status": "error",
                "error": "Billing issue",
                "details": "Check account credits/billing status"
            }), 402
        
        # Fallback - show actual error
        return jsonify({
            "status": "error",
            "error": "Request failed",
            "details": error_full
        }), 500
    
    # =========================================================================
    # Setup Wizard Endpoints
    # =========================================================================
    
    @bp.route('/setup/check-packages', methods=['GET'])
    def check_packages():
        """Check if optional audio packages are installed."""
        import subprocess
        import sys
        
        def check_package(package_name):
            """Check if a package is installed in current environment."""
            try:
                result = subprocess.run(
                    [sys.executable, '-c', f'import {package_name}'],
                    capture_output=True,
                    timeout=5
                )
                return result.returncode == 0
            except Exception:
                return False
        
        # Package mappings: import_name -> (display_name, requirements_file)
        packages = {
            'tts': {
                'import_name': 'kokoro',
                'display_name': 'Kokoro TTS',
                'requirements': 'requirements-tts.txt',
                'description': 'Text-to-speech engine for voice responses'
            },
            'stt': {
                'import_name': 'faster_whisper',
                'display_name': 'Faster Whisper',
                'requirements': 'requirements-stt.txt',
                'description': 'Speech recognition for voice input'
            },
            'wakeword': {
                'import_name': 'openwakeword',
                'display_name': 'OpenWakeWord',
                'requirements': 'requirements-wakeword.txt',
                'description': 'Wake word detection for hands-free activation'
            }
        }
        
        results = {}
        for key, info in packages.items():
            installed = check_package(info['import_name'])
            results[key] = {
                'installed': installed,
                'package': info['display_name'],
                'import_name': info['import_name'],
                'requirements': info['requirements'],
                'description': info['description']
            }
        
        return jsonify({
            'status': 'success',
            'packages': results
        })
    
    @bp.route('/setup/wizard-step', methods=['GET'])
    def get_wizard_step():
        """Get current wizard step."""
        step = settings.get('SETUP_WIZARD_STEP', 0)
        return jsonify({'step': step})
    
    @bp.route('/setup/wizard-step', methods=['PUT'])
    def set_wizard_step():
        """Set wizard step (0-4)."""
        data = request.json
        step = data.get('step', 0)
        if not isinstance(step, int) or step < 0 or step > 4:
            return jsonify({'error': 'Step must be 0-4'}), 400
        
        settings.set('SETUP_WIZARD_STEP', step, persist=True)
        return jsonify({'status': 'success', 'step': step})
    
    return bp