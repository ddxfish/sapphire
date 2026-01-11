# api.py
import os
import tempfile
import logging
import io
import json
import time
from flask import Flask, Blueprint, request, jsonify, send_file, Response
from core.modules.system import prompts
import config

logger = logging.getLogger(__name__)
app = Flask(__name__)

def create_api(system_instance, restart_callback=None, shutdown_callback=None):
    """Create and return a Blueprint with all API routes.
    
    Args:
        system_instance: VoiceChatSystem instance
        restart_callback: Function to call to request restart (sets flag in main loop)
        shutdown_callback: Function to call to request shutdown (sets flag in main loop)
    """
    bp = Blueprint('sapphire_api', __name__)
    logger.info("API Blueprint created with VoiceChatSystem")
    
    @bp.before_request
    def check_api_key():
        """Require API key for all routes in this blueprint."""
        from core.setup import get_password_hash
        expected_key = get_password_hash()
        if not expected_key:
            logger.error("SAPPHIRE_API_KEY not set - rejecting request")
            return jsonify({"error": "Server misconfigured"}), 500
        provided_key = request.headers.get('X-API-Key')
        if not provided_key or provided_key != expected_key:
            logger.warning(f"Invalid API key from {request.remote_addr}")
            return jsonify({"error": "Unauthorized"}), 401
    
    def _apply_chat_settings(settings: dict):
        """Apply chat settings to the system (TTS, prompt, ability)."""
        try:
            # Apply TTS settings
            if "voice" in settings:
                system_instance.tts.set_voice(settings["voice"])
            if "pitch" in settings:
                system_instance.tts.set_pitch(settings["pitch"])
            if "speed" in settings:
                system_instance.tts.set_speed(settings["speed"])
            
            # Apply prompt
            if "prompt" in settings:
                from core.modules.system import prompts
                prompt_name = settings["prompt"]
                prompt_data = prompts.get_prompt(prompt_name)
                content = prompt_data.get('content') if isinstance(prompt_data, dict) else str(prompt_data)
                if content:
                    system_instance.llm_chat.set_system_prompt(content)
                    prompts.set_active_preset_name(prompt_name)
                    
                    # Apply scenario state for assembled prompts (same as prompt editor does)
                    if hasattr(prompts.prompt_manager, 'scenario_presets') and prompt_name in prompts.prompt_manager.scenario_presets:
                        prompts.apply_scenario(prompt_name)
                        logger.info(f"Applied scenario state '{prompt_name}'")
                    
                    logger.info(f"Applied prompt: {prompt_name}")
            
            # Apply ability
            if "ability" in settings:
                ability_name = settings["ability"]
                system_instance.llm_chat.function_manager.update_enabled_functions([ability_name])
                logger.info(f"Applied ability: {ability_name}")
            
            logger.info(f"Applied chat settings: voice={settings.get('voice')}, prompt={settings.get('prompt')}, ability={settings.get('ability')}")
            
        except Exception as e:
            logger.error(f"Error applying chat settings: {e}", exc_info=True)
    
    def format_messages_for_display(messages):
        """
        Transform proper message structure into display format for UI.
        Groups assistant + tool sequences into single display blocks.
        """
        display_messages = []
        current_block = None
        
        for msg in messages:
            role = msg.get("role")
            
            if role == "user":
                if current_block:
                    display_messages.append(finalize_block(current_block))
                    current_block = None
                
                display_messages.append({
                    "role": "user",
                    "content": msg.get("content", ""),
                    "timestamp": msg.get("timestamp")
                })
            
            elif role == "assistant":
                if current_block is None:
                    current_block = {
                        "role": "assistant",
                        "parts": [],
                        "timestamp": msg.get("timestamp")
                    }
                
                content = msg.get("content", "")
                if content:
                    current_block["parts"].append({
                        "type": "content",
                        "text": content
                    })
                
                if msg.get("tool_calls"):
                    for tc in msg.get("tool_calls", []):
                        current_block["parts"].append({
                            "type": "tool_call",
                            "id": tc.get("id"),
                            "name": tc.get("function", {}).get("name"),
                            "arguments": tc.get("function", {}).get("arguments")
                        })
            
            elif role == "tool":
                if current_block is None:
                    current_block = {
                        "role": "assistant",
                        "parts": [],
                        "timestamp": msg.get("timestamp")
                    }
                
                tool_part = {
                    "type": "tool_result",
                    "name": msg.get("name"),
                    "result": msg.get("content", "")
                }
                
                if "tool_inputs" in msg:
                    tool_part["inputs"] = msg["tool_inputs"]
                
                current_block["parts"].append(tool_part)
        
        if current_block:
            display_messages.append(finalize_block(current_block))
        
        return display_messages

    def finalize_block(block):
        """Return block with ordered parts array - preserves rendering order."""
        return {
            "role": "assistant",
            "parts": block.get("parts", []),
            "timestamp": block.get("timestamp")
        }

    @bp.route('/chat', methods=['POST'])
    def handle_chat():
        data = request.json
        if not data or 'text' not in data: 
            return jsonify({"error": "No text provided"}), 400
        
        response = system_instance.process_llm_query(data['text'], skip_tts=True)
        return jsonify({"response": response})

    @bp.route('/chat/stream', methods=['POST'])
    def handle_chat_stream():
        data = request.json
        if not data or 'text' not in data: 
            return jsonify({"error": "No text provided"}), 400
        
        prefill = data.get('prefill')
        skip_user_message = data.get('skip_user_message', False)
        
        if prefill:
            logger.info(f"STREAMING WITH PREFILL: {len(prefill)} chars")
        if skip_user_message:
            logger.info(f"STREAMING IN CONTINUE MODE: skip_user_message=True")
        
        system_instance.llm_chat.streaming_chat.cancel_flag = False
        
        def generate():
            try:
                chunk_count = 0
                for chunk in system_instance.llm_chat.chat_stream(data['text'], prefill=prefill, skip_user_message=skip_user_message):
                    if system_instance.llm_chat.streaming_chat.cancel_flag:
                        logger.info(f"STREAMING CANCELLED at chunk {chunk_count}")
                        yield f"data: {json.dumps({'cancelled': True})}\n\n"
                        break
                    
                    if chunk:
                        chunk_count += 1
                        yield f"data: {json.dumps({'chunk': chunk})}\n\n"
                
                if not system_instance.llm_chat.streaming_chat.cancel_flag:
                    ephemeral = system_instance.llm_chat.streaming_chat.ephemeral
                    logger.info(f"STREAMING COMPLETE: {chunk_count} chunks, ephemeral={ephemeral}")
                    yield f"data: {json.dumps({'done': True, 'ephemeral': ephemeral})}\n\n"
                
            except Exception as e:
                logger.error(f"STREAMING ERROR: {e}", exc_info=True)
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
        
        return Response(
            generate(),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive'
            }
        )

    @bp.route('/cancel', methods=['POST'])
    def handle_cancel():
        """Cancel ongoing streaming generation."""
        try:
            if system_instance and hasattr(system_instance, 'llm_chat'):
                system_instance.llm_chat.streaming_chat.cancel_flag = True
                logger.info("CANCEL: Flag set (cleanup happens in generator)")
                return jsonify({"status": "success", "message": "Cancellation requested"})
            else:
                return jsonify({"error": "System not initialized"}), 500
        except Exception as e:
            logger.error(f"Error during cancellation: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500

    @bp.route('/transcribe', methods=['POST'])
    def handle_transcribe():
        if 'audio' not in request.files: 
            return jsonify({"error": "No audio file provided"}), 400
        audio_file = request.files['audio']
        
        # Windows fix: mkstemp returns (fd, path) - must close fd before other processes can access
        fd, temp_path = tempfile.mkstemp(suffix=".wav")
        try:
            os.close(fd)  # Close fd immediately so file can be accessed by other processes
            # Browser sends 16kHz mono WAV - save directly
            audio_file.save(temp_path)
            transcribed_text = system_instance.whisper_client.transcribe_file(temp_path)
        except Exception as e:
            logger.error(f"Transcription error: {e}", exc_info=True)
            return jsonify({"error": "Failed to process audio"}), 500
        finally:
            # Windows-safe cleanup with retry
            try:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
            except PermissionError:
                # File still locked, try again after brief wait
                time.sleep(0.1)
                try:
                    if os.path.exists(temp_path):
                        os.unlink(temp_path)
                except Exception as cleanup_err:
                    logger.warning(f"Could not clean up temp file {temp_path}: {cleanup_err}")
        return jsonify({"text": transcribed_text})

    @bp.route('/history', methods=['GET'])
    def get_history():
        """Get history formatted for UI display."""
        raw_messages = system_instance.llm_chat.session_manager.get_messages()
        display_messages = format_messages_for_display(raw_messages)
        return jsonify(display_messages)

    @bp.route('/modules', methods=['GET'])
    def get_modules():
        return jsonify(system_instance.llm_chat.module_loader.get_module_list())

    @bp.route('/health', methods=['GET'])
    def health_check():
        return jsonify({"status": "ok"})

    @bp.route('/system/status', methods=['GET'])
    def get_system_status():
        try:
            prompt_state = prompts.get_current_state()
            function_names = system_instance.llm_chat.function_manager.get_enabled_function_names()
            ability_info = system_instance.llm_chat.function_manager.get_current_ability_info()
            
            # Check if enabled tools include network/cloud tools (uses NETWORK flag from modules)
            has_cloud_tools = system_instance.llm_chat.function_manager.has_network_tools_enabled()
            
            return jsonify({
                "prompt": prompt_state,
                "prompt_name": prompts.get_active_preset_name(),
                "prompt_char_count": prompts.get_prompt_char_count(),
                "functions": function_names,
                "ability": ability_info,
                "tts_enabled": config.TTS_ENABLED,
                "has_cloud_tools": has_cloud_tools
            })
        except Exception as e:
            logger.error(f"Error getting system status: {e}")
            return jsonify({"error": "Failed to get system status"}), 500

    @bp.route('/system/prompt', methods=['GET'])
    def get_system_prompt():
        prompt_name = request.args.get('prompt_name')
        if prompt_name:
            logger.info(f"API request for named prompt: '{prompt_name}'")
            prompt_data = prompts.get_prompt(prompt_name)
            if not prompt_data: 
                return jsonify({"error": f"Prompt '{prompt_name}' not found."}), 404
            content = prompt_data.get('content') if isinstance(prompt_data, dict) else str(prompt_data)
            return jsonify({"prompt": content, "source": f"storage: {prompt_name}"})
        else:
            logger.info("API request for active system prompt")
            if not system_instance or not hasattr(system_instance, 'llm_chat'): 
                return jsonify({"error": "System not initialized"}), 500
            
            prompt_template = system_instance.llm_chat.get_system_prompt_template()
            return jsonify({"prompt": prompt_template, "source": "active_memory_template"})

    @bp.route('/system/prompt', methods=['POST'])
    def set_system_prompt():
        if not system_instance or not hasattr(system_instance, 'llm_chat'): 
            return jsonify({"error": "System not initialized"}), 500
        data = request.json
        new_prompt = data.get('new_prompt')
        if not new_prompt: 
            return jsonify({"error": "A 'new_prompt' key must be provided"}), 400
        success = system_instance.llm_chat.set_system_prompt(new_prompt)
        if success:
            return jsonify({"status": "success", "message": "System prompt updated."})
        else:
            return jsonify({"error": "Error setting prompt"}), 500

    @bp.route('/history/messages', methods=['DELETE'])
    def remove_history_messages():
        data = request.json
        count = data.get('count', 0) if data else 0
        user_message = data.get('user_message') if data else None
        
        # Method 1: Delete from specific user message (for regenerate)
        if user_message:
            try:
                if system_instance.llm_chat.session_manager.remove_from_user_message(user_message):
                    return jsonify({"status": "success", "message": "Removed from user message"})
                else:
                    return jsonify({"error": "User message not found"}), 404
            except Exception as e:
                logger.error(f"Error removing from user message: {e}")
                return jsonify({"error": f"Failed: {str(e)}"}), 500
        
        # Method 2: Delete last N messages (for trash button)
        if count == -1:
            try:
                system_instance.llm_chat.session_manager.clear()
                return jsonify({"status": "success", "message": "All chat history cleared."})
            except Exception as e:
                logger.error(f"Error clearing history: {e}")
                return jsonify({"error": "Failed to clear history"}), 500
        
        if not isinstance(count, int) or count <= 0: 
            return jsonify({"error": "Invalid count"}), 400
            
        try:
            if system_instance.llm_chat.session_manager.remove_last_messages(count):
                return jsonify({"status": "success", "message": f"Removed {count} messages.", "deleted": count})
            else:
                return jsonify({"error": "Failed to remove messages"}), 500
        except Exception as e:
            logger.error(f"Error removing messages: {e}")
            return jsonify({"error": f"Failed: {str(e)}"}), 500

    @bp.route('/history/messages/remove-last-assistant', methods=['POST'])
    def remove_last_assistant():
        """Remove only the last assistant message in a turn (for continue)."""
        data = request.json
        timestamp = data.get('timestamp')
        
        if not timestamp:
            return jsonify({"error": "Timestamp required"}), 400
        
        try:
            if system_instance.llm_chat.session_manager.remove_last_assistant_in_turn(timestamp):
                return jsonify({"status": "success", "message": "Removed last assistant"})
            else:
                return jsonify({"error": "Failed to remove"}), 500
        except Exception as e:
            logger.error(f"Error removing last assistant: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500

    @bp.route('/history/messages/remove-from-assistant', methods=['POST'])
    def remove_from_assistant():
        """Remove assistant message and everything after it (leaves user message intact)."""
        data = request.json
        timestamp = data.get('timestamp')
        
        if not timestamp:
            return jsonify({"error": "Timestamp required"}), 400
        
        try:
            if system_instance.llm_chat.session_manager.remove_from_assistant_timestamp(timestamp):
                return jsonify({"status": "success", "message": "Removed from assistant"})
            else:
                return jsonify({"error": "Assistant message not found"}), 404
        except Exception as e:
            logger.error(f"Error removing from assistant: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500

    @bp.route('/chats', methods=['GET'])
    def list_chats():
        try:
            chats = system_instance.llm_chat.list_chats()
            active_chat = system_instance.llm_chat.get_active_chat()
            return jsonify({"chats": chats, "active_chat": active_chat})
        except Exception as e:
            logger.error(f"Error listing chats: {e}")
            return jsonify({"error": "Failed to list chats"}), 500

    @bp.route('/chats', methods=['POST'])
    def create_chat():
        try:
            data = request.json or {}
            chat_name = data.get('name')
            
            if not chat_name or not chat_name.strip():
                return jsonify({"error": "Chat name required"}), 400
            
            if system_instance.llm_chat.create_chat(chat_name):
                return jsonify({"status": "success", "name": chat_name, "message": f"Created: {chat_name}"})
            else:
                return jsonify({"error": f"Chat '{chat_name}' already exists or invalid name"}), 409
        except Exception as e:
            logger.error(f"Error creating chat: {e}")
            return jsonify({"error": "Failed to create chat"}), 500

    @bp.route('/chats/<chat_name>', methods=['DELETE'])
    def delete_chat(chat_name):
        try:
            was_active = (chat_name == system_instance.llm_chat.get_active_chat())
            
            if system_instance.llm_chat.delete_chat(chat_name):
                # If we deleted the active chat, it auto-switched to default
                # Apply the new default's settings to sync FunctionManager
                if was_active:
                    settings = system_instance.llm_chat.session_manager.get_chat_settings()
                    _apply_chat_settings(settings)
                    logger.info("Applied settings after deleting active chat")
                
                return jsonify({"status": "success", "message": f"Deleted: {chat_name}"})
            else:
                return jsonify({"error": f"Cannot delete '{chat_name}'"}), 400
        except Exception as e:
            logger.error(f"Error deleting chat {chat_name}: {e}")
            return jsonify({"error": "Failed to delete"}), 500
            
    @bp.route('/chats/<chat_name>/activate', methods=['POST'])
    def activate_chat(chat_name):
        try:
            if system_instance.llm_chat.switch_chat(chat_name):
                # Apply settings from the newly activated chat
                settings = system_instance.llm_chat.session_manager.get_chat_settings()
                _apply_chat_settings(settings)
                
                return jsonify({
                    "status": "success", 
                    "active_chat": chat_name, 
                    "message": f"Switched to: {chat_name}",
                    "settings": settings
                })
            else:
                return jsonify({"error": f"Cannot switch to: {chat_name}"}), 400
        except Exception as e:
            logger.error(f"Error switching to {chat_name}: {e}")
            return jsonify({"error": "Failed to switch"}), 500

    @bp.route('/chats/active', methods=['GET'])
    def get_active_chat():
        try:
            active_chat = system_instance.llm_chat.get_active_chat()
            return jsonify({"active_chat": active_chat})
        except Exception as e:
            logger.error(f"Error getting active chat: {e}")
            return jsonify({"error": "Failed to get active chat"}), 500

    @bp.route('/chats/<chat_name>/settings', methods=['GET'])
    def get_chat_settings(chat_name):
        """Get settings for a specific chat."""
        try:
            session_manager = system_instance.llm_chat.session_manager
            
            # For active chat, return from memory (always most current)
            if chat_name == session_manager.active_chat_name:
                logger.info(f"GET settings for active chat '{chat_name}' from memory")
                return jsonify({"settings": session_manager.get_chat_settings()})
            
            # For non-active chats, read from file
            chat_path = session_manager._get_chat_path(chat_name)
            
            logger.info(f"GET settings for '{chat_name}' at path: {chat_path}")
            
            if not chat_path.exists():
                logger.error(f"Chat file not found: {chat_path}")
                return jsonify({"error": f"Chat '{chat_name}' not found"}), 404
            
            with open(chat_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Handle both new and old format
            if isinstance(data, dict) and "settings" in data:
                settings = data["settings"]
            else:
                # Old format or corrupted - return defaults
                logger.warning(f"Chat '{chat_name}' missing settings, using defaults")
                from core.history import SYSTEM_DEFAULTS
                settings = SYSTEM_DEFAULTS.copy()
            
            logger.info(f"Returning settings for '{chat_name}': {settings}")
            return jsonify({"settings": settings})
            
        except Exception as e:
            logger.error(f"Error getting settings for '{chat_name}': {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500

    @bp.route('/chats/<chat_name>/settings', methods=['PUT'])
    def update_chat_settings(chat_name):
        """Update settings for a specific chat and apply them."""
        try:
            data = request.json
            if not data or "settings" not in data:
                return jsonify({"error": "Settings object required"}), 400
            
            session_manager = system_instance.llm_chat.session_manager
            new_settings = data["settings"]
            
            # Must be the active chat to update settings
            if chat_name != session_manager.get_active_chat_name():
                return jsonify({"error": "Can only update settings for active chat"}), 400
            
            # Update settings in session manager
            if not session_manager.update_chat_settings(new_settings):
                return jsonify({"error": "Failed to update settings"}), 500
            
            # Apply settings to system immediately
            _apply_chat_settings(session_manager.get_chat_settings())
            
            logger.info(f"Updated and applied settings for chat '{chat_name}'")
            return jsonify({"status": "success", "message": f"Settings updated for '{chat_name}'"})
            
        except Exception as e:
            logger.error(f"Error updating settings for '{chat_name}': {e}")
            return jsonify({"error": str(e)}), 500

    @bp.route('/tts/speak', methods=['POST'])
    def handle_tts_speak():
        data = request.json
        text = data.get('text')
        output_mode = data.get('output_mode', 'play')
        if not text: 
            return jsonify({"error": "No text provided"}), 400
        
        # Check if TTS is enabled
        if not config.TTS_ENABLED:
            if output_mode == 'play':
                return jsonify({"status": "success", "message": "TTS disabled"})
            elif output_mode == 'file':
                return jsonify({"status": "success", "message": "TTS disabled"}), 200
        
        if output_mode == 'play':
            system_instance.tts.speak(text)
            return jsonify({"status": "success", "message": "Playback started."})
        elif output_mode == 'file':
            audio_data = system_instance.tts.generate_audio_data(text)
            if audio_data:
                return send_file(io.BytesIO(audio_data), mimetype='audio/wav', as_attachment=True, download_name='output.wav')
            else:
                return jsonify({"error": "TTS generation failed"}), 503
        else:
            return jsonify({"error": "Invalid output_mode"}), 400

    @bp.route('/history/messages/edit', methods=['POST'])
    def edit_message():
        """Edit a message by timestamp."""
        data = request.json
        role = data.get('role')
        timestamp = data.get('timestamp')
        new_content = data.get('new_content')
        
        logger.info(f"[EDIT] Editing {role} message with timestamp: {timestamp}")
        
        if not all([role, timestamp, new_content is not None]):
            logger.warning(f"[EDIT] Missing fields: role={role}, ts={timestamp}, content={new_content is not None}")
            return jsonify({"error": "Missing required fields"}), 400
        
        if role not in ['user', 'assistant']:
            return jsonify({"error": "Invalid role"}), 400
        
        try:
            if system_instance.llm_chat.session_manager.edit_message_by_timestamp(role, timestamp, new_content):
                return jsonify({"status": "success", "message": "Message updated"})
            else:
                return jsonify({"error": "Message not found"}), 404
        except Exception as e:
            logger.error(f"[EDIT] Error: {e}", exc_info=True)
            return jsonify({"error": f"Failed to edit: {str(e)}"}), 500

    @bp.route('/history/raw', methods=['GET'])
    def get_raw_history():
        """Get raw history structure (not display format)."""
        return jsonify(system_instance.llm_chat.session_manager.get_messages())
    
    @bp.route('/history/import', methods=['POST'])
    def import_history():
        """Import messages into current chat (replaces existing)."""
        data = request.json
        messages = data.get('messages')
        if not messages or not isinstance(messages, list):
            return jsonify({"error": "Invalid messages array"}), 400
        
        try:
            session_manager = system_instance.llm_chat.session_manager
            session_manager.current_chat.messages = messages
            session_manager._save_current_chat()
            logger.info(f"Imported {len(messages)} messages into '{session_manager.active_chat_name}'")
            return jsonify({"status": "success", "message": f"Imported {len(messages)} messages"})
        except Exception as e:
            logger.error(f"Import failed: {e}")
            return jsonify({"error": str(e)}), 500

    # =============================================================================
    # BACKUP MANAGEMENT ROUTES
    # =============================================================================
    
    @bp.route('/backup/list', methods=['GET'])
    def list_backups():
        """List all backups grouped by type."""
        try:
            module_loader = system_instance.llm_chat.module_loader
            backup_module = module_loader.get_module_instance("backup")
            
            if not backup_module:
                return jsonify({"error": "Backup module not loaded"}), 503
            
            backups = backup_module.list_backups()
            return jsonify({"backups": backups})
        except Exception as e:
            logger.error(f"Error listing backups: {e}")
            return jsonify({"error": str(e)}), 500
    
    @bp.route('/backup/create', methods=['POST'])
    def create_backup():
        """Create a new backup."""
        try:
            data = request.json or {}
            backup_type = data.get('type', 'manual')
            
            if backup_type not in ('daily', 'weekly', 'monthly', 'manual'):
                return jsonify({"error": "Invalid backup type"}), 400
            
            module_loader = system_instance.llm_chat.module_loader
            backup_module = module_loader.get_module_instance("backup")
            
            if not backup_module:
                return jsonify({"error": "Backup module not loaded"}), 503
            
            filename = backup_module.create_backup(backup_type)
            if filename:
                backup_module.rotate_backups()
                return jsonify({"status": "success", "filename": filename})
            else:
                return jsonify({"error": "Backup creation failed"}), 500
        except Exception as e:
            logger.error(f"Error creating backup: {e}")
            return jsonify({"error": str(e)}), 500
    
    @bp.route('/backup/delete/<filename>', methods=['DELETE'])
    def delete_backup(filename):
        """Delete a specific backup."""
        try:
            module_loader = system_instance.llm_chat.module_loader
            backup_module = module_loader.get_module_instance("backup")
            
            if not backup_module:
                return jsonify({"error": "Backup module not loaded"}), 503
            
            if backup_module.delete_backup(filename):
                return jsonify({"status": "success", "deleted": filename})
            else:
                return jsonify({"error": "Backup not found or invalid"}), 404
        except Exception as e:
            logger.error(f"Error deleting backup: {e}")
            return jsonify({"error": str(e)}), 500
    
    @bp.route('/backup/download/<filename>', methods=['GET'])
    def download_backup(filename):
        """Download a backup file."""
        try:
            module_loader = system_instance.llm_chat.module_loader
            backup_module = module_loader.get_module_instance("backup")
            
            if not backup_module:
                return jsonify({"error": "Backup module not loaded"}), 503
            
            filepath = backup_module.get_backup_path(filename)
            if filepath:
                return send_file(filepath, as_attachment=True, download_name=filename)
            else:
                return jsonify({"error": "Backup not found"}), 404
        except Exception as e:
            logger.error(f"Error downloading backup: {e}")
            return jsonify({"error": str(e)}), 500
    
    # =============================================================================
    # AUDIO DEVICE ROUTES
    # =============================================================================
    
    @bp.route('/api/audio/devices', methods=['GET'])
    def get_audio_devices():
        """Get list of available audio input and output devices."""
        try:
            from core.audio import get_device_manager
            dm = get_device_manager()
            
            # Force refresh to get current device list
            devices = dm.query_devices(force_refresh=True)
            
            input_devices = []
            output_devices = []
            
            for dev in devices:
                dev_info = {
                    'index': dev.index,
                    'name': dev.name,
                    'is_default': dev.is_default_input or dev.is_default_output,
                }
                
                if dev.max_input_channels > 0:
                    input_devices.append({
                        **dev_info,
                        'channels': dev.max_input_channels,
                        'sample_rate': int(dev.default_samplerate),
                        'is_default': dev.is_default_input,
                    })
                
                if dev.max_output_channels > 0:
                    output_devices.append({
                        **dev_info,
                        'channels': dev.max_output_channels,
                        'sample_rate': int(dev.default_samplerate),
                        'is_default': dev.is_default_output,
                    })
            
            # Get current configured device
            configured_input = getattr(config, 'AUDIO_INPUT_DEVICE', None)
            configured_output = getattr(config, 'AUDIO_OUTPUT_DEVICE', None)
            
            return jsonify({
                'input': input_devices,
                'output': output_devices,
                'configured_input': configured_input,
                'configured_output': configured_output,
            })
            
        except Exception as e:
            logger.error(f"Failed to get audio devices: {e}")
            return jsonify({'error': str(e)}), 500
    
    @bp.route('/api/audio/test-input', methods=['POST'])
    def test_audio_input():
        """Test audio input device by recording a short sample."""
        try:
            from core.audio import get_device_manager
            dm = get_device_manager()
            
            data = request.get_json() or {}
            device_index = data.get('device_index')
            duration = min(data.get('duration', 1.0), 3.0)  # Cap at 3 seconds
            
            # Convert 'auto' or None to None for auto-detection
            if device_index == 'auto' or device_index == '':
                device_index = None
            elif device_index is not None:
                try:
                    device_index = int(device_index)
                except (ValueError, TypeError):
                    device_index = None
            
            result = dm.test_input_device(device_index=device_index, duration=duration)
            
            return jsonify(result)
            
        except Exception as e:
            logger.error(f"Audio input test failed: {e}")
            from core.audio import classify_audio_error
            return jsonify({
                'success': False,
                'error': classify_audio_error(e)
            }), 500
    
    @bp.route('/api/audio/test-output', methods=['POST'])
    def test_audio_output():
        """Test audio output device by playing a test tone."""
        try:
            import numpy as np
            import sounddevice as sd
            
            data = request.get_json() or {}
            device_index = data.get('device_index')
            duration = min(data.get('duration', 0.5), 2.0)  # Cap at 2 seconds
            frequency = data.get('frequency', 440)  # A4 note
            
            # Convert to int or None
            if device_index == 'auto' or device_index == '' or device_index is None:
                device_index = None
            else:
                try:
                    device_index = int(device_index)
                except (ValueError, TypeError):
                    device_index = None
            
            # Generate test tone
            sample_rate = 44100
            t = np.linspace(0, duration, int(sample_rate * duration), False)
            
            # Sine wave with fade in/out to avoid clicks
            tone = np.sin(2 * np.pi * frequency * t)
            fade_samples = int(sample_rate * 0.02)  # 20ms fade
            fade_in = np.linspace(0, 1, fade_samples)
            fade_out = np.linspace(1, 0, fade_samples)
            tone[:fade_samples] *= fade_in
            tone[-fade_samples:] *= fade_out
            tone = (tone * 0.5 * 32767).astype(np.int16)  # 50% volume
            
            # Play tone
            sd.play(tone, sample_rate, device=device_index)
            sd.wait()
            
            return jsonify({
                'success': True,
                'duration': duration,
                'frequency': frequency,
                'device_index': device_index,
            })
            
        except Exception as e:
            logger.error(f"Audio output test failed: {e}")
            from core.audio import classify_audio_error
            return jsonify({
                'success': False,
                'error': classify_audio_error(e)
            }), 500

    # =============================================================================
    # SYSTEM MANAGEMENT ROUTES
    # =============================================================================
    
    @bp.route('/api/system/restart', methods=['POST'])
    def request_system_restart():
        """Request application restart. Returns immediately, restart happens async."""
        if not restart_callback:
            return jsonify({"error": "Restart not available"}), 503
        try:
            restart_callback()
            logger.info("Restart requested via API")
            return jsonify({
                "status": "restarting",
                "message": "Restart initiated. Server will be back shortly."
            })
        except Exception as e:
            logger.error(f"Failed to request restart: {e}")
            return jsonify({"error": str(e)}), 500
    
    @bp.route('/api/system/shutdown', methods=['POST'])
    def request_system_shutdown():
        """Request clean application shutdown."""
        if not shutdown_callback:
            return jsonify({"error": "Shutdown not available"}), 503
        try:
            shutdown_callback()
            logger.info("Shutdown requested via API")
            return jsonify({
                "status": "shutting_down",
                "message": "Shutdown initiated."
            })
        except Exception as e:
            logger.error(f"Failed to request shutdown: {e}")
            return jsonify({"error": str(e)}), 500
    
    return bp