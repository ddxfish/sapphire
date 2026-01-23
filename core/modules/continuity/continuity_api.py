# core/modules/continuity/continuity_api.py
"""
Flask blueprint for Continuity task management.
Provides CRUD endpoints + manual run, status, activity, timeline.
"""

import logging
from flask import Blueprint, request, jsonify

logger = logging.getLogger(__name__)


def create_continuity_api(scheduler):
    """
    Create and return the continuity API blueprint.
    
    Args:
        scheduler: ContinuityScheduler instance
    """
    bp = Blueprint('continuity_api', __name__)
    
    @bp.before_request
    def check_api_key():
        """Require API key for all routes."""
        from core.setup import get_password_hash
        expected_key = get_password_hash()
        if not expected_key:
            return jsonify({"error": "Setup required"}), 503
        provided_key = request.headers.get('X-API-Key')
        if not provided_key or provided_key != expected_key:
            return jsonify({"error": "Unauthorized"}), 401
    
    # =========================================================================
    # TASK CRUD
    # =========================================================================
    
    @bp.route('/tasks', methods=['GET'])
    def list_tasks():
        """List all tasks."""
        try:
            tasks = scheduler.list_tasks()
            return jsonify({"tasks": tasks})
        except Exception as e:
            logger.error(f"Error listing tasks: {e}")
            return jsonify({"error": str(e)}), 500
    
    @bp.route('/tasks', methods=['POST'])
    def create_task():
        """Create a new task."""
        try:
            data = request.json
            if not data:
                return jsonify({"error": "No data provided"}), 400
            
            if not data.get("name"):
                return jsonify({"error": "Task name required"}), 400
            
            task = scheduler.create_task(data)
            return jsonify({"status": "success", "task": task}), 201
            
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logger.error(f"Error creating task: {e}")
            return jsonify({"error": str(e)}), 500
    
    @bp.route('/tasks/<task_id>', methods=['GET'])
    def get_task(task_id):
        """Get single task by ID."""
        try:
            task = scheduler.get_task(task_id)
            if not task:
                return jsonify({"error": "Task not found"}), 404
            return jsonify(task)
        except Exception as e:
            logger.error(f"Error getting task: {e}")
            return jsonify({"error": str(e)}), 500
    
    @bp.route('/tasks/<task_id>', methods=['PUT'])
    def update_task(task_id):
        """Update existing task."""
        try:
            data = request.json
            if not data:
                return jsonify({"error": "No data provided"}), 400
            
            task = scheduler.update_task(task_id, data)
            if not task:
                return jsonify({"error": "Task not found"}), 404
            
            return jsonify({"status": "success", "task": task})
            
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logger.error(f"Error updating task: {e}")
            return jsonify({"error": str(e)}), 500
    
    @bp.route('/tasks/<task_id>', methods=['DELETE'])
    def delete_task(task_id):
        """Delete task by ID."""
        try:
            if scheduler.delete_task(task_id):
                return jsonify({"status": "success", "message": "Task deleted"})
            else:
                return jsonify({"error": "Task not found"}), 404
        except Exception as e:
            logger.error(f"Error deleting task: {e}")
            return jsonify({"error": str(e)}), 500
    
    # =========================================================================
    # MANUAL RUN
    # =========================================================================
    
    @bp.route('/tasks/<task_id>/run', methods=['POST'])
    def run_task(task_id):
        """Manually run a task immediately (for testing)."""
        try:
            result = scheduler.run_task_now(task_id)
            
            if result.get("success"):
                return jsonify({"status": "success", "result": result})
            else:
                error = result.get("error", "Unknown error")
                if error == "Task not found":
                    return jsonify({"error": error}), 404
                return jsonify({"status": "failed", "result": result})
                
        except Exception as e:
            logger.error(f"Error running task: {e}")
            return jsonify({"error": str(e)}), 500
    
    # =========================================================================
    # STATUS & MONITORING
    # =========================================================================
    
    @bp.route('/status', methods=['GET'])
    def get_status():
        """Get scheduler status."""
        try:
            status = scheduler.get_status()
            return jsonify(status)
        except Exception as e:
            logger.error(f"Error getting status: {e}")
            return jsonify({"error": str(e)}), 500
    
    @bp.route('/activity', methods=['GET'])
    def get_activity():
        """Get recent activity log."""
        try:
            limit = request.args.get('limit', 50, type=int)
            activity = scheduler.get_activity(min(limit, 100))
            return jsonify({"activity": activity})
        except Exception as e:
            logger.error(f"Error getting activity: {e}")
            return jsonify({"error": str(e)}), 500
    
    @bp.route('/timeline', methods=['GET'])
    def get_timeline():
        """Get timeline of upcoming scheduled tasks."""
        try:
            hours = request.args.get('hours', 24, type=int)
            timeline = scheduler.get_timeline(min(hours, 168))  # Max 1 week
            return jsonify({"timeline": timeline})
        except Exception as e:
            logger.error(f"Error getting timeline: {e}")
            return jsonify({"error": str(e)}), 500
    
    return bp