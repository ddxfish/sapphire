# core/modules/system/prompts_api.py
"""
Flask blueprint for prompt CRUD operations.
Provides API endpoints for managing system prompts (monoliths and assembled).
"""
import os
import json
import logging
from flask import Blueprint, request, jsonify
from . import prompts
from core.event_bus import publish, Events

logger = logging.getLogger(__name__)

def create_prompts_api(system_instance=None):
    """Create and return the prompts API blueprint."""
    bp = Blueprint('prompts_api', __name__, url_prefix='/api/prompts')
    
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
    
    @bp.route('', methods=['GET'])
    def list_prompts():
        """Get list of all available prompts with metadata."""
        try:
            prompt_list = prompts.list_prompts()
            prompt_metadata = []

            for name in prompt_list:
                prompt_data = prompts.get_prompt(name)
                metadata = {
                    'name': name,
                    'type': prompt_data.get('type', 'unknown') if isinstance(prompt_data, dict) else 'monolith',
                    'char_count': len(prompt_data.get('content', '')) if isinstance(prompt_data, dict) else len(str(prompt_data)),
                    'privacy_required': prompt_data.get('privacy_required', False) if isinstance(prompt_data, dict) else False
                }
                prompt_metadata.append(metadata)

            return jsonify({'prompts': prompt_metadata})
        except Exception as e:
            logger.error(f"Error listing prompts: {e}")
            return jsonify({'error': str(e)}), 500
    
    @bp.route('/<name>', methods=['GET'])
    def get_prompt(name):
        """Get full prompt structure by name."""
        try:
            prompt_data = prompts.get_prompt(name)
            if not prompt_data:
                return jsonify({'error': f"Prompt '{name}' not found"}), 404
            
            if isinstance(prompt_data, dict):
                return jsonify(prompt_data)
            else:
                return jsonify({
                    'name': name,
                    'type': 'monolith',
                    'content': str(prompt_data)
                })
        except Exception as e:
            logger.error(f"Error getting prompt '{name}': {e}")
            return jsonify({'error': str(e)}), 500
    
    @bp.route('/<name>', methods=['PUT'])
    def save_prompt(name):
        """Save or update a prompt."""
        try:
            data = request.json
            if not data:
                return jsonify({'error': 'No data provided'}), 400
            
            prompt_type = data.get('type', 'monolith')
            is_new = request.args.get('new', '').lower() == 'true'
            
            # If creating NEW prompt, block if name exists anywhere (any type)
            if is_new:
                existing = prompts.get_prompt(name)
                if existing:
                    existing_type = existing.get('type', 'unknown')
                    return jsonify({'error': f"Prompt '{name}' already exists ({existing_type})"}), 409
            
            # Cross-type collision check (for updates too)
            if prompt_type == 'monolith':
                if 'content' not in data:
                    return jsonify({'error': 'Monolith prompts require "content" field'}), 400
                if hasattr(prompts.prompt_manager, 'scenario_presets') and name in prompts.prompt_manager.scenario_presets:
                    return jsonify({'error': f"Name '{name}' already exists as assembled prompt"}), 409
                    
            elif prompt_type == 'assembled':
                if 'components' not in data:
                    return jsonify({'error': 'Assembled prompts require "components" field'}), 400
                if hasattr(prompts.prompt_manager, 'monoliths') and name in prompts.prompt_manager.monoliths:
                    return jsonify({'error': f"Name '{name}' already exists as monolith prompt"}), 409
            
            result = prompts.save_prompt(name, data)
            
            if isinstance(result, tuple):
                success, message = result
            else:
                success = result
                message = f"Prompt '{name}' saved" if success else "Failed to save prompt"
            
            if success:
                origin = request.headers.get('X-Session-ID')
                publish(Events.PROMPT_CHANGED, {"name": name, "type": prompt_type, "action": "saved", "origin": origin})
                return jsonify({'status': 'success', 'message': message})
            else:
                return jsonify({'error': message}), 409
        except Exception as e:
            logger.error(f"Error saving prompt '{name}': {e}")
            return jsonify({'error': str(e)}), 500
    
    @bp.route('/<name>', methods=['DELETE'])
    def delete_prompt(name):
        """Delete a prompt."""
        try:
            success = prompts.delete_prompt(name)
            if success:
                origin = request.headers.get('X-Session-ID')
                publish(Events.PROMPT_DELETED, {"name": name, "origin": origin})
                return jsonify({'status': 'success', 'message': f"Prompt '{name}' deleted"})
            else:
                return jsonify({'error': f"Prompt '{name}' not found or cannot be deleted"}), 404
        except Exception as e:
            logger.error(f"Error deleting prompt '{name}': {e}")
            return jsonify({'error': str(e)}), 500
    
    @bp.route('/<name>/load', methods=['POST'])
    def load_prompt(name):
        """Load/activate a prompt for the current session."""
        try:
            prompt_data = prompts.get_prompt(name)
            if not prompt_data:
                return jsonify({'error': f"Prompt '{name}' not found"}), 404

            # Check if prompt requires privacy mode
            privacy_required = prompt_data.get('privacy_required', False) if isinstance(prompt_data, dict) else False
            if privacy_required:
                try:
                    from core.privacy import is_privacy_mode
                    if not is_privacy_mode():
                        return jsonify({
                            'error': f"Prompt '{name}' requires Privacy Mode. Enable it first.",
                            'privacy_required': True
                        }), 403
                except ImportError:
                    pass

            content = prompt_data.get('content') if isinstance(prompt_data, dict) else str(prompt_data)
            
            if system_instance and hasattr(system_instance, 'llm_chat'):
                session_manager = system_instance.llm_chat.session_manager
                success = session_manager.update_chat_settings({'prompt': name})
                if success:
                    logger.info(f"Updated chat JSON: prompt={name}")
                else:
                    logger.warning(f"Failed to update chat JSON for prompt '{name}'")
            
            if system_instance and hasattr(system_instance, 'llm_chat'):
                system_instance.llm_chat.set_system_prompt(content)
                logger.info(f"Applied prompt '{name}' to LLM")
            
            prompts.set_active_preset_name(name)
            
            if hasattr(prompts.prompt_manager, 'scenario_presets') and name in prompts.prompt_manager.scenario_presets:
                prompts.apply_scenario(name)
                logger.info(f"Applied scenario state '{name}'")

            # Notify frontend of prompt change
            origin = request.headers.get('X-Session-ID')
            publish(Events.PROMPT_CHANGED, {"name": name, "action": "loaded", "origin": origin})

            return jsonify({'status': 'success', 'message': f"Loaded prompt '{name}'"})
        except Exception as e:
            logger.error(f"Error loading prompt '{name}': {e}")
            import traceback
            traceback.print_exc()
            return jsonify({'error': str(e)}), 500
    
    @bp.route('/reload', methods=['POST'])
    def reload_prompts():
        """Reload prompts from disk."""
        try:
            prompts.reload()
            return jsonify({'status': 'success', 'message': 'Prompts reloaded'})
        except Exception as e:
            logger.error(f"Error reloading prompts: {e}")
            return jsonify({'error': str(e)}), 500
    
    @bp.route('/components', methods=['GET'])
    def get_components():
        """Get available component options for assembled prompts."""
        try:
            components = {}
            
            if hasattr(prompts.prompt_manager, 'components'):
                components = prompts.prompt_manager.components
            
            return jsonify({'components': components})
        except Exception as e:
            logger.error(f"Error getting components: {e}")
            return jsonify({'error': str(e)}), 500

    @bp.route('/components/<comp_type>/<key>', methods=['PUT'])
    def save_component(comp_type, key):
        """Add or update a component piece."""
        try:
            data = request.json
            if not data or 'value' not in data:
                return jsonify({'error': 'Component value required'}), 400
            
            value = data['value']
            
            valid_types = ['persona', 'location', 'relationship', 'goals', 'format', 'scenario', 'extras', 'emotions']
            if comp_type not in valid_types:
                return jsonify({'error': f'Invalid component type: {comp_type}'}), 400
            
            if not hasattr(prompts.prompt_manager, 'components'):
                return jsonify({'error': 'Components not loaded'}), 500
            
            if comp_type not in prompts.prompt_manager.components:
                prompts.prompt_manager.components[comp_type] = {}
            
            prompts.prompt_manager.components[comp_type][key] = value
            prompts.prompt_manager.save_components()

            # Get origin session for SSE filtering
            origin = request.headers.get('X-Session-ID')
            publish(Events.COMPONENTS_CHANGED, {"type": comp_type, "key": key, "action": "saved", "origin": origin})
            logger.info(f"Saved component {comp_type}.{key}")

            # Return full components so client can update without refetch
            return jsonify({
                'status': 'success',
                'message': f'Component {comp_type}.{key} saved',
                'components': prompts.prompt_manager.components
            })
            
        except Exception as e:
            logger.error(f"Error saving component {comp_type}.{key}: {e}")
            return jsonify({'error': str(e)}), 500
    
    @bp.route('/components/<comp_type>/<key>', methods=['DELETE'])
    def delete_component(comp_type, key):
        """Delete a component piece."""
        try:
            if not hasattr(prompts.prompt_manager, 'components'):
                return jsonify({'error': 'Components not loaded'}), 500
            
            if comp_type not in prompts.prompt_manager.components:
                return jsonify({'error': f'Component type {comp_type} not found'}), 404
            
            if key not in prompts.prompt_manager.components[comp_type]:
                return jsonify({'error': f'Component {comp_type}.{key} not found'}), 404
            
            del prompts.prompt_manager.components[comp_type][key]
            prompts.prompt_manager.save_components()

            # Get origin session for SSE filtering
            origin = request.headers.get('X-Session-ID')
            publish(Events.COMPONENTS_CHANGED, {"type": comp_type, "key": key, "action": "deleted", "origin": origin})
            logger.info(f"Deleted component {comp_type}.{key}")

            # Return full components so client can update without refetch
            return jsonify({
                'status': 'success',
                'message': f'Component {comp_type}.{key} deleted',
                'components': prompts.prompt_manager.components
            })
            
        except Exception as e:
            logger.error(f"Error deleting component {comp_type}.{key}: {e}")
            return jsonify({'error': str(e)}), 500

    @bp.route('/reset', methods=['POST'])
    def reset_prompts():
        """Reset all prompt files to factory defaults (destructive)."""
        try:
            from core.setup import reset_prompt_files
            from core.chat.history import get_user_defaults
            
            success = reset_prompt_files()
            if not success:
                return jsonify({'error': 'Failed to reset prompt files'}), 500
            
            # Reload prompt manager to pick up changes
            prompts.reload()
            
            # Validate active prompt still exists, fallback to user's default if not
            active_name = prompts.get_active_preset_name()
            available = prompts.list_prompts()
            if active_name not in available:
                # Get user's configured default prompt (from chat_defaults.json)
                user_defaults = get_user_defaults()
                fallback_prompt = user_defaults.get('prompt', 'sapphire')
                
                # Make sure the fallback exists, otherwise use first available
                if fallback_prompt not in available and available:
                    fallback_prompt = available[0]
                
                logger.info(f"Active prompt '{active_name}' no longer exists after reset, falling back to '{fallback_prompt}'")
                prompts.set_active_preset_name(fallback_prompt)
                
                # Update chat JSON if system available
                if system_instance and hasattr(system_instance, 'llm_chat'):
                    session_manager = system_instance.llm_chat.session_manager
                    session_manager.update_chat_settings({'prompt': fallback_prompt})
                    
                    # Apply fallback prompt to LLM
                    prompt_data = prompts.get_prompt(fallback_prompt)
                    if prompt_data:
                        content = prompt_data.get('content', '')
                        system_instance.llm_chat.set_system_prompt(content)
            
            publish(Events.PROMPT_CHANGED, {"action": "reset", "bulk": True})
            publish(Events.COMPONENTS_CHANGED, {"action": "reset", "bulk": True})
            publish(Events.SPICE_CHANGED, {"action": "reset", "bulk": True})
            return jsonify({
                'status': 'success',
                'message': 'All prompts reset to factory defaults',
                'files': ['prompt_monoliths.json', 'prompt_pieces.json', 'prompt_spices.json']
            })
        except Exception as e:
            logger.error(f"Error resetting prompts: {e}")
            return jsonify({'error': str(e)}), 500

    @bp.route('/merge', methods=['POST'])
    def merge_prompts():
        """Merge factory defaults into user prompts (core overwrites conflicts)."""
        try:
            from core.setup import merge_prompt_files
            
            results = merge_prompt_files()
            if 'error' in results:
                return jsonify({'error': results['error']}), 500
            
            # Reload prompt manager to pick up changes
            prompts.reload()
            
            publish(Events.PROMPT_CHANGED, {"action": "merge", "bulk": True})
            publish(Events.COMPONENTS_CHANGED, {"action": "merge", "bulk": True})
            return jsonify({
                'status': 'success',
                'message': 'Factory defaults merged into user prompts',
                'results': results
            })
        except Exception as e:
            logger.error(f"Error merging prompts: {e}")
            return jsonify({'error': str(e)}), 500

    @bp.route('/reset-chat-defaults', methods=['POST'])
    def reset_chat_defaults_endpoint():
        """Reset chat_defaults.json to factory defaults."""
        try:
            from core.setup import reset_chat_defaults
            
            success = reset_chat_defaults()
            if not success:
                return jsonify({'error': 'Failed to reset chat defaults'}), 500
            
            return jsonify({
                'status': 'success',
                'message': 'Chat defaults reset to factory settings'
            })
        except Exception as e:
            logger.error(f"Error resetting chat defaults: {e}")
            return jsonify({'error': str(e)}), 500

    return bp