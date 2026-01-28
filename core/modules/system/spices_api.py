# core/modules/system/spices_api.py
"""
Flask blueprint for spice CRUD operations.
Provides API endpoints for managing prompt spices.
"""
import logging
from flask import Blueprint, request, jsonify
from . import prompts
from core.event_bus import publish, Events

logger = logging.getLogger(__name__)


def create_spices_api():
    """Create and return the spices API blueprint."""
    bp = Blueprint('spices_api', __name__, url_prefix='/api/spices')
    
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
    def list_spices():
        """Get all spices organized by category."""
        try:
            spices = prompts.prompt_manager.spices
            disabled = prompts.prompt_manager.disabled_categories
            
            categories = {}
            total_count = 0
            
            for category_name, spice_list in spices.items():
                categories[category_name] = {
                    'spices': spice_list,
                    'count': len(spice_list),
                    'enabled': category_name not in disabled
                }
                total_count += len(spice_list)
            
            return jsonify({
                'categories': categories,
                'category_count': len(categories),
                'total_spices': total_count
            })
        except Exception as e:
            logger.error(f"Error listing spices: {e}")
            return jsonify({'error': str(e)}), 500
    
    @bp.route('/category/<cat_name>/toggle', methods=['POST'])
    def toggle_category(cat_name):
        """Toggle a category's enabled state and pick new spice."""
        try:
            spices = prompts.prompt_manager.spices
            
            if cat_name not in spices:
                return jsonify({'error': f"Category '{cat_name}' not found"}), 404
            
            currently_enabled = prompts.prompt_manager.is_category_enabled(cat_name)
            new_state = not currently_enabled
            prompts.prompt_manager.set_category_enabled(cat_name, new_state)
            
            # Force pick new spice from updated enabled categories
            prompts.set_random_spice()
            current_spice = prompts.get_current_spice()
            
            logger.info(f"Toggled category '{cat_name}' to {'enabled' if new_state else 'disabled'}")
            publish(Events.SPICE_CHANGED, {"category": cat_name, "enabled": new_state, "action": "toggled"})
            return jsonify({
                'status': 'success',
                'category': cat_name,
                'enabled': new_state,
                'current_spice': current_spice
            })
        except Exception as e:
            logger.error(f"Error toggling category: {e}")
            return jsonify({'error': str(e)}), 500
    
    @bp.route('', methods=['POST'])
    def add_spice():
        """Add a new spice to a category."""
        try:
            data = request.json
            if not data:
                return jsonify({'error': 'No data provided'}), 400
            
            category = data.get('category', '').strip()
            text = data.get('text', '').strip()
            
            if not category:
                return jsonify({'error': 'Category required'}), 400
            if not text:
                return jsonify({'error': 'Spice text required'}), 400
            
            spices = prompts.prompt_manager._spices
            
            if category not in spices:
                spices[category] = []
            
            if text in spices[category]:
                return jsonify({'error': 'Spice already exists in this category'}), 409
            
            spices[category].append(text)
            prompts.prompt_manager.save_spices()
            
            logger.info(f"Added spice to '{category}': {text[:50]}...")
            publish(Events.SPICE_CHANGED, {"category": category, "action": "added"})
            return jsonify({
                'status': 'success',
                'message': f'Added spice to {category}',
                'category': category,
                'index': len(spices[category]) - 1
            })
        except Exception as e:
            logger.error(f"Error adding spice: {e}")
            return jsonify({'error': str(e)}), 500
    
    @bp.route('/<category>/<int:index>', methods=['PUT'])
    def update_spice(category, index):
        """Update a spice by category and index."""
        try:
            data = request.json
            if not data or 'text' not in data:
                return jsonify({'error': 'Spice text required'}), 400
            
            text = data['text'].strip()
            if not text:
                return jsonify({'error': 'Spice text cannot be empty'}), 400
            
            spices = prompts.prompt_manager._spices
            
            if category not in spices:
                return jsonify({'error': f"Category '{category}' not found"}), 404
            
            if index < 0 or index >= len(spices[category]):
                return jsonify({'error': f"Invalid index {index} for category '{category}'"}), 404
            
            old_text = spices[category][index]
            spices[category][index] = text
            prompts.prompt_manager.save_spices()
            
            logger.info(f"Updated spice in '{category}' at index {index}")
            publish(Events.SPICE_CHANGED, {"category": category, "index": index, "action": "updated"})
            return jsonify({
                'status': 'success',
                'message': 'Spice updated',
                'category': category,
                'index': index,
                'old_text': old_text,
                'new_text': text
            })
        except Exception as e:
            logger.error(f"Error updating spice: {e}")
            return jsonify({'error': str(e)}), 500
    
    @bp.route('/<category>/<int:index>', methods=['DELETE'])
    def delete_spice(category, index):
        """Delete a spice by category and index."""
        try:
            spices = prompts.prompt_manager._spices
            
            if category not in spices:
                return jsonify({'error': f"Category '{category}' not found"}), 404
            
            if index < 0 or index >= len(spices[category]):
                return jsonify({'error': f"Invalid index {index} for category '{category}'"}), 404
            
            deleted_text = spices[category].pop(index)
            
            if len(spices[category]) == 0:
                del spices[category]
                logger.info(f"Removed empty category '{category}'")
            
            prompts.prompt_manager.save_spices()
            
            logger.info(f"Deleted spice from '{category}' at index {index}")
            publish(Events.SPICE_CHANGED, {"category": category, "action": "deleted"})
            return jsonify({
                'status': 'success',
                'message': 'Spice deleted',
                'category': category,
                'deleted_text': deleted_text
            })
        except Exception as e:
            logger.error(f"Error deleting spice: {e}")
            return jsonify({'error': str(e)}), 500
    
    @bp.route('/category', methods=['POST'])
    def create_category():
        """Create a new empty category."""
        try:
            data = request.json
            if not data or 'name' not in data:
                return jsonify({'error': 'Category name required'}), 400
            
            cat_name = data['name'].strip()
            if not cat_name:
                return jsonify({'error': 'Category name cannot be empty'}), 400
            
            spices = prompts.prompt_manager._spices
            
            if cat_name in spices:
                return jsonify({'error': f"Category '{cat_name}' already exists"}), 409
            
            spices[cat_name] = []
            prompts.prompt_manager.save_spices()
            
            logger.info(f"Created category '{cat_name}'")
            publish(Events.SPICE_CHANGED, {"category": cat_name, "action": "category_created"})
            return jsonify({
                'status': 'success',
                'message': f"Created category '{cat_name}'",
                'category': cat_name
            })
        except Exception as e:
            logger.error(f"Error creating category: {e}")
            return jsonify({'error': str(e)}), 500
    
    @bp.route('/category/<cat_name>', methods=['DELETE'])
    def delete_category(cat_name):
        """Delete an entire category and all its spices."""
        try:
            spices = prompts.prompt_manager._spices
            
            if cat_name not in spices:
                return jsonify({'error': f"Category '{cat_name}' not found"}), 404
            
            spice_count = len(spices[cat_name])
            del spices[cat_name]
            prompts.prompt_manager.save_spices()
            
            logger.info(f"Deleted category '{cat_name}' with {spice_count} spices")
            publish(Events.SPICE_CHANGED, {"category": cat_name, "action": "category_deleted"})
            return jsonify({
                'status': 'success',
                'message': f"Deleted category '{cat_name}'",
                'deleted_spices': spice_count
            })
        except Exception as e:
            logger.error(f"Error deleting category: {e}")
            return jsonify({'error': str(e)}), 500
    
    @bp.route('/category/<cat_name>', methods=['PUT'])
    def rename_category(cat_name):
        """Rename a category."""
        try:
            data = request.json
            if not data or 'new_name' not in data:
                return jsonify({'error': 'New category name required'}), 400
            
            new_name = data['new_name'].strip()
            if not new_name:
                return jsonify({'error': 'New category name cannot be empty'}), 400
            
            spices = prompts.prompt_manager._spices
            
            if cat_name not in spices:
                return jsonify({'error': f"Category '{cat_name}' not found"}), 404
            
            if new_name in spices:
                return jsonify({'error': f"Category '{new_name}' already exists"}), 409
            
            spices[new_name] = spices.pop(cat_name)
            prompts.prompt_manager.save_spices()
            
            logger.info(f"Renamed category '{cat_name}' to '{new_name}'")
            publish(Events.SPICE_CHANGED, {"old_name": cat_name, "new_name": new_name, "action": "category_renamed"})
            return jsonify({
                'status': 'success',
                'message': f"Renamed category to '{new_name}'",
                'old_name': cat_name,
                'new_name': new_name
            })
        except Exception as e:
            logger.error(f"Error renaming category: {e}")
            return jsonify({'error': str(e)}), 500
    
    @bp.route('/reload', methods=['POST'])
    def reload_spices():
        """Reload spices from disk."""
        try:
            prompts.prompt_manager._load_spices()
            spice_count = sum(len(v) for v in prompts.prompt_manager.spices.values())
            category_count = len(prompts.prompt_manager.spices)
            
            logger.info(f"Reloaded spices: {category_count} categories, {spice_count} spices")
            return jsonify({
                'status': 'success',
                'message': 'Spices reloaded',
                'category_count': category_count,
                'spice_count': spice_count
            })
        except Exception as e:
            logger.error(f"Error reloading spices: {e}")
            return jsonify({'error': str(e)}), 500
    
    return bp