# core/abilities_api.py
import os
import logging
from flask import Blueprint, request, jsonify
from core.modules.system.toolsets import toolset_manager
from core.event_bus import publish, Events

logger = logging.getLogger(__name__)

def create_abilities_api(system_instance):
    """Create and return a Blueprint with abilities API routes."""
    bp = Blueprint('abilities_api', __name__)
    
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
    
    @bp.route('/abilities', methods=['GET'])
    def list_abilities():
        """List all available ability sets with type classification."""
        try:
            function_manager = system_instance.llm_chat.function_manager
            
            abilities_set = set()
            abilities_set.update(function_manager.get_available_abilities())
            abilities_set.update(toolset_manager.get_toolset_names())
            
            abilities = sorted(list(abilities_set))
            
            # Get all network functions for checking
            network_functions = set(function_manager.get_network_functions())
            
            ability_details = []
            for ability_name in abilities:
                if ability_name in ['all', 'none']:
                    ability_type = 'builtin'
                elif ability_name in function_manager.function_modules:
                    ability_type = 'module'
                elif toolset_manager.toolset_exists(ability_name):
                    ability_type = 'user'
                else:
                    ability_type = 'unknown'
                
                function_list = []
                if ability_name == "all":
                    function_count = len(function_manager.all_possible_tools)
                    function_list = [tool['function']['name'] for tool in function_manager.all_possible_tools]
                elif ability_name == "none":
                    function_count = 0
                    function_list = []
                elif ability_name in function_manager.function_modules:
                    function_count = len(function_manager.function_modules[ability_name]['available_functions'])
                    function_list = function_manager.function_modules[ability_name]['available_functions']
                elif toolset_manager.toolset_exists(ability_name):
                    function_list = toolset_manager.get_toolset_functions(ability_name)
                    function_count = len(function_list)
                else:
                    function_count = 0
                    function_list = []
                
                # Check if this ability includes any network tools
                has_network = bool(set(function_list) & network_functions)
                
                ability_details.append({
                    "name": ability_name,
                    "function_count": function_count,
                    "type": ability_type,
                    "functions": function_list,
                    "has_network_tools": has_network
                })
            
            return jsonify({
                "abilities": ability_details,
                "count": len(ability_details)
            })
        except Exception as e:
            logger.error(f"Error listing abilities: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500
    
    @bp.route('/abilities/current', methods=['GET'])
    def get_current_ability():
        """Get current ability info."""
        try:
            function_manager = system_instance.llm_chat.function_manager
            ability_info = function_manager.get_current_ability_info()
            enabled_functions = function_manager.get_enabled_function_names()
            has_network = function_manager.has_network_tools_enabled()
            
            return jsonify({
                "name": ability_info.get("name", "custom"),
                "function_count": ability_info.get("function_count", 0),
                "enabled_functions": enabled_functions,
                "has_network_tools": has_network
            })
        except Exception as e:
            logger.error(f"Error getting current ability: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500
    
    @bp.route('/abilities/<ability_name>/activate', methods=['POST'])
    def activate_ability(ability_name):
        """Activate an ability set and save to chat settings."""
        try:
            function_manager = system_instance.llm_chat.function_manager
            
            if not function_manager.is_valid_ability(ability_name):
                return jsonify({"error": f"Ability '{ability_name}' not found"}), 404
            
            function_manager.update_enabled_functions([ability_name])
            
            # Save to chat settings JSON
            if hasattr(system_instance.llm_chat, 'session_manager'):
                system_instance.llm_chat.session_manager.update_chat_settings({'ability': ability_name})
            
            ability_info = function_manager.get_current_ability_info()
            
            logger.info(f"Activated ability: {ability_name}")
            publish(Events.ABILITY_CHANGED, {"name": ability_name, "action": "activated"})
            return jsonify({
                "status": "success",
                "message": f"Activated ability: {ability_name}",
                "name": ability_info.get("name", ability_name),
                "function_count": ability_info.get("function_count", 0)
            })
        except Exception as e:
            logger.error(f"Error activating ability '{ability_name}': {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500
    
    @bp.route('/functions', methods=['GET'])
    def list_functions():
        """List all available functions grouped by module."""
        try:
            function_manager = system_instance.llm_chat.function_manager
            enabled = set(function_manager.get_enabled_function_names())
            network_funcs = set(function_manager.get_network_functions())
            
            modules = {}
            for module_name, module_info in function_manager.function_modules.items():
                functions = []
                for tool in module_info['tools']:
                    func_name = tool['function']['name']
                    functions.append({
                        "name": func_name,
                        "description": tool['function'].get('description', ''),
                        "enabled": func_name in enabled,
                        "network": func_name in network_funcs
                    })
                
                modules[module_name] = {
                    "functions": functions,
                    "count": len(functions)
                }
            
            return jsonify({
                "modules": modules,
                "total_functions": len(function_manager.all_possible_tools),
                "enabled_count": len(enabled)
            })
        except Exception as e:
            logger.error(f"Error listing functions: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500
    
    @bp.route('/functions/enable', methods=['POST'])
    def enable_functions():
        """Enable specific functions (custom set)."""
        try:
            data = request.json
            function_list = data.get('functions', [])
            
            if not isinstance(function_list, list):
                return jsonify({"error": "functions must be a list"}), 400
            
            function_manager = system_instance.llm_chat.function_manager
            
            all_function_names = [tool['function']['name'] for tool in function_manager.all_possible_tools]
            invalid = [f for f in function_list if f not in all_function_names]
            
            if invalid:
                return jsonify({"error": f"Invalid functions: {', '.join(invalid)}"}), 400
            
            function_manager.update_enabled_functions(function_list)
            
            logger.info(f"Enabled custom function set: {len(function_list)} functions")
            return jsonify({
                "status": "success",
                "message": f"Enabled {len(function_list)} functions",
                "enabled_functions": function_list
            })
        except Exception as e:
            logger.error(f"Error enabling functions: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500
    
    @bp.route('/abilities/custom', methods=['POST'])
    def save_custom_ability():
        """Save custom ability set to toolsets."""
        try:
            data = request.json
            name = data.get('name', '').strip()
            functions = data.get('functions', [])
            
            if not name:
                return jsonify({"error": "name required"}), 400
            
            if not isinstance(functions, list):
                return jsonify({"error": "functions must be a list"}), 400
            
            function_manager = system_instance.llm_chat.function_manager
            if name in function_manager.function_modules:
                return jsonify({"error": f"Cannot overwrite module ability '{name}'"}), 400
            
            if name in ['all', 'none']:
                return jsonify({"error": f"Cannot overwrite built-in ability '{name}'"}), 400
            
            all_function_names = [tool['function']['name'] for tool in function_manager.all_possible_tools]
            invalid = [f for f in functions if f not in all_function_names]
            
            if invalid:
                return jsonify({"error": f"Invalid functions: {', '.join(invalid)}"}), 400
            
            if toolset_manager.save_toolset(name, functions):
                logger.info(f"Saved custom toolset '{name}' with {len(functions)} functions")
                publish(Events.ABILITY_CHANGED, {"name": name, "action": "created"})
                return jsonify({
                    "status": "success",
                    "message": f"Saved custom ability: {name}",
                    "name": name,
                    "function_count": len(functions)
                })
            else:
                return jsonify({"error": "Failed to save toolset"}), 500
        except Exception as e:
            logger.error(f"Error saving custom ability: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500
    
    @bp.route('/abilities/<ability_name>', methods=['DELETE'])
    def delete_custom_ability(ability_name):
        """Delete custom ability set from toolsets."""
        try:
            if ability_name in ['all', 'none']:
                return jsonify({"error": "Cannot delete built-in ability"}), 400
            
            function_manager = system_instance.llm_chat.function_manager
            
            if ability_name in function_manager.function_modules:
                return jsonify({"error": "Cannot delete module ability"}), 400
            
            if not toolset_manager.toolset_exists(ability_name):
                return jsonify({"error": f"Ability '{ability_name}' not found"}), 404
            
            if toolset_manager.delete_toolset(ability_name):
                logger.info(f"Deleted custom toolset: {ability_name}")
                publish(Events.ABILITY_CHANGED, {"name": ability_name, "action": "deleted"})
                return jsonify({
                    "status": "success",
                    "message": f"Deleted ability: {ability_name}"
                })
            else:
                return jsonify({"error": f"Failed to delete '{ability_name}'"}), 500
            
        except Exception as e:
            logger.error(f"Error deleting ability '{ability_name}': {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500
    
    return bp