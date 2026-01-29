# api.py
import os
import tempfile
import logging
import io
import json
import time
from flask import Flask, Blueprint, request, jsonify, send_file, Response
from core.modules.system import prompts
from core.event_bus import publish, Events
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
        """Apply chat settings to the system (TTS, prompt, ability, state engine)."""
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
                    publish(Events.PROMPT_CHANGED, {"name": prompt_name})
            
            # Apply ability
            if "ability" in settings:
                ability_name = settings["ability"]
                system_instance.llm_chat.function_manager.update_enabled_functions([ability_name])
                logger.info(f"Applied ability: {ability_name}")
                publish(Events.ABILITY_CHANGED, {"name": ability_name})
            
            # State engine: ALWAYS update to sync state with current settings
            # This ensures tools are correctly added/removed on chat switch and startup
            system_instance.llm_chat._update_state_engine()
            
            # Notify UI that tools may have changed (state engine adds/removes tools)
            if settings.get('state_engine_enabled') is not None:
                ability_info = system_instance.llm_chat.function_manager.get_current_ability_info()
                publish(Events.ABILITY_CHANGED, {
                    "name": ability_info.get("name", "custom"),
                    "action": "state_engine_update",
                    "function_count": ability_info.get("function_count", 0)
                })
            
            logger.debug(f"Applied chat settings: voice={settings.get('voice')}, prompt={settings.get('prompt')}, ability={settings.get('ability')}, state_engine={settings.get('state_engine_enabled')}")
            
        except Exception as e:
            logger.error(f"Error applying chat settings: {e}", exc_info=True)
    
    def format_messages_for_display(messages):
        """
        Transform proper message structure into display format for UI.
        Groups assistant + tool sequences into single display blocks.
        Extracts images from user messages for display.
        """
        display_messages = []
        current_block = None
        
        for msg in messages:
            role = msg.get("role")
            
            if role == "user":
                if current_block:
                    display_messages.append(finalize_block(current_block))
                    current_block = None
                
                content = msg.get("content", "")
                user_msg = {
                    "role": "user",
                    "timestamp": msg.get("timestamp")
                }
                
                # Handle multimodal content (list with text and images)
                if isinstance(content, list):
                    text_parts = []
                    images = []
                    for block in content:
                        if isinstance(block, dict):
                            if block.get("type") == "text":
                                text_parts.append(block.get("text", ""))
                            elif block.get("type") == "image":
                                images.append({
                                    "data": block.get("data", ""),
                                    "media_type": block.get("media_type", "image/jpeg")
                                })
                        elif isinstance(block, str):
                            text_parts.append(block)
                    user_msg["content"] = " ".join(text_parts)
                    if images:
                        user_msg["images"] = images
                else:
                    user_msg["content"] = content
                
                display_messages.append(user_msg)
            
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
                
                # Capture metadata from the last assistant message in a turn
                if msg.get("metadata"):
                    current_block["metadata"] = msg["metadata"]
                
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
                    "result": msg.get("content", ""),
                    "tool_call_id": msg.get("tool_call_id")
                }
                
                if "tool_inputs" in msg:
                    tool_part["inputs"] = msg["tool_inputs"]
                
                current_block["parts"].append(tool_part)
        
        if current_block:
            display_messages.append(finalize_block(current_block))
        
        return display_messages

    def finalize_block(block):
        """Return block with ordered parts array - preserves rendering order."""
        result = {
            "role": "assistant",
            "parts": block.get("parts", []),
            "timestamp": block.get("timestamp")
        }
        if block.get("metadata"):
            result["metadata"] = block["metadata"]
        return result

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
        images = data.get('images', [])  # List of {data: "...", media_type: "..."}
        
        if prefill:
            logger.info(f"STREAMING WITH PREFILL: {len(prefill)} chars")
        if skip_user_message:
            logger.info(f"STREAMING IN CONTINUE MODE: skip_user_message=True")
        if images:
            logger.info(f"STREAMING WITH {len(images)} IMAGES")
        
        system_instance.llm_chat.streaming_chat.cancel_flag = False
        
        def generate():
            try:
                chunk_count = 0
                for event in system_instance.llm_chat.chat_stream(data['text'], prefill=prefill, skip_user_message=skip_user_message, images=images):
                    if system_instance.llm_chat.streaming_chat.cancel_flag:
                        logger.info(f"STREAMING CANCELLED at chunk {chunk_count}")
                        yield f"data: {json.dumps({'cancelled': True})}\n\n"
                        break
                    
                    if event:
                        chunk_count += 1
                        
                        # Handle typed events (dicts) vs legacy strings
                        if isinstance(event, dict):
                            event_type = event.get("type")
                            
                            if event_type == "stream_started":
                                yield f"data: {json.dumps({'type': 'stream_started'})}\n\n"
                            
                            elif event_type == "iteration_start":
                                yield f"data: {json.dumps({'type': 'iteration_start', 'iteration': event.get('iteration', 1)})}\n\n"
                            
                            elif event_type == "content":
                                yield f"data: {json.dumps({'type': 'content', 'text': event.get('text', '')})}\n\n"
                            
                            elif event_type == "tool_start":
                                yield f"data: {json.dumps({'type': 'tool_start', 'id': event.get('id'), 'name': event.get('name'), 'args': event.get('args', {})})}\n\n"
                            
                            elif event_type == "tool_end":
                                yield f"data: {json.dumps({'type': 'tool_end', 'id': event.get('id'), 'name': event.get('name'), 'result': event.get('result', ''), 'error': event.get('error', False)})}\n\n"
                            
                            elif event_type == "reload":
                                yield f"data: {json.dumps({'type': 'reload'})}\n\n"
                            
                            else:
                                # Unknown typed event, send as-is
                                yield f"data: {json.dumps(event)}\n\n"
                        else:
                            # Legacy string chunk (backwards compatibility)
                            if '<<RELOAD_PAGE>>' in str(event):
                                yield f"data: {json.dumps({'type': 'reload'})}\n\n"
                            else:
                                yield f"data: {json.dumps({'type': 'content', 'text': str(event)})}\n\n"
                
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

    @bp.route('/upload/image', methods=['POST'])
    def handle_image_upload():
        """Upload an image, optionally optimize it, and return base64 data for chat."""
        import base64
        from io import BytesIO
        from core.settings_manager import settings
        
        def optimize_image(image_data: bytes, max_width: int) -> tuple:
            """
            Optimize image: resize to max width @ 85% JPEG quality.
            Returns (optimized_data, media_type) or original if optimization makes it bigger.
            """
            try:
                from PIL import Image
            except ImportError:
                logger.warning("Pillow not installed, skipping image optimization")
                return image_data, None
            
            original_size = len(image_data)
            
            try:
                img = Image.open(BytesIO(image_data))
                
                # Convert RGBA/P to RGB for JPEG (no alpha channel)
                if img.mode in ('RGBA', 'P'):
                    img = img.convert('RGB')
                elif img.mode != 'RGB':
                    img = img.convert('RGB')
                
                # Resize if wider than max_width
                if img.width > max_width:
                    ratio = max_width / img.width
                    new_height = int(img.height * ratio)
                    img = img.resize((max_width, new_height), Image.LANCZOS)
                
                # Compress to JPEG @ 85%
                buffer = BytesIO()
                img.save(buffer, format='JPEG', quality=85, optimize=True)
                optimized_data = buffer.getvalue()
                optimized_size = len(optimized_data)
                
                # Use smaller version
                if optimized_size < original_size:
                    logger.info(f"Image optimized: {original_size} -> {optimized_size} bytes ({100 - (optimized_size * 100 // original_size)}% reduction)")
                    return optimized_data, 'image/jpeg'
                else:
                    logger.info(f"Image optimization skipped: {optimized_size} >= {original_size} (would be larger)")
                    return image_data, None
                    
            except Exception as e:
                logger.warning(f"Image optimization failed, using original: {e}")
                return image_data, None
        
        if 'image' not in request.files:
            return jsonify({"error": "No image file provided"}), 400
        
        image_file = request.files['image']
        if not image_file.filename:
            return jsonify({"error": "No file selected"}), 400
        
        # Validate extension
        allowed_ext = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
        ext = os.path.splitext(image_file.filename)[1].lower()
        if ext not in allowed_ext:
            return jsonify({"error": f"Invalid file type. Allowed: {', '.join(allowed_ext)}"}), 400
        
        # Check file size (10MB max for images going to LLM)
        image_file.seek(0, 2)
        size = image_file.tell()
        image_file.seek(0)
        max_size = 10 * 1024 * 1024  # 10MB
        if size > max_size:
            return jsonify({"error": f"File too large. Max {max_size // (1024*1024)}MB"}), 400
        
        # Determine media type
        media_types = {
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.gif': 'image/gif',
            '.webp': 'image/webp'
        }
        media_type = media_types.get(ext, 'image/jpeg')
        
        # Read and optionally optimize
        try:
            image_data = image_file.read()
            original_size = len(image_data)
            
            # Check if optimization is enabled
            max_width = settings.get('IMAGE_UPLOAD_MAX_WIDTH', 0)
            if max_width > 0:
                optimized_data, optimized_type = optimize_image(image_data, max_width)
                if optimized_type:
                    image_data = optimized_data
                    media_type = optimized_type
            
            base64_data = base64.b64encode(image_data).decode('utf-8')
            final_size = len(image_data)
            
            logger.info(f"Image uploaded: {image_file.filename}, {original_size} -> {final_size} bytes, {media_type}")
            
            return jsonify({
                "status": "success",
                "data": base64_data,
                "media_type": media_type,
                "filename": image_file.filename,
                "size": final_size
            })
        except Exception as e:
            logger.error(f"Image upload error: {e}", exc_info=True)
            return jsonify({"error": "Failed to process image"}), 500

    @bp.route('/history', methods=['GET'])
    def get_history():
        """Get history formatted for UI display with context usage info."""
        import time as _time
        start = _time.time()
        logger.debug(f"[TIMING] Backend /history request received")
        
        from core.chat.history import count_tokens, count_message_tokens
        
        raw_messages = system_instance.llm_chat.session_manager.get_messages_for_display()
        logger.debug(f"[TIMING] get_messages_for_display done at {_time.time() - start:.3f}s, {len(raw_messages)} messages")
        
        display_messages = format_messages_for_display(raw_messages)
        logger.debug(f"[TIMING] format_messages done at {_time.time() - start:.3f}s")
        
        # Calculate context usage
        context_limit = getattr(config, 'CONTEXT_LIMIT', 32000)
        
        # Count tokens in history (images stripped from all but wouldn't be sent anyway)
        # LLM only receives text from historical messages, images are stripped
        history_tokens = sum(count_message_tokens(m.get("content", ""), include_images=False) for m in raw_messages)
        logger.debug(f"[TIMING] count_tokens done at {_time.time() - start:.3f}s")
        
        # Estimate system prompt tokens (use current prompt if available)
        try:
            prompt_content = system_instance.llm_chat.current_system_prompt or ""
            prompt_tokens = count_tokens(prompt_content) if prompt_content else 0
        except Exception:
            prompt_tokens = 0
        
        total_used = history_tokens + prompt_tokens
        percent = min(100, int((total_used / context_limit) * 100)) if context_limit > 0 else 0
        
        logger.debug(f"[TIMING] Backend /history complete at {_time.time() - start:.3f}s")
        return jsonify({
            "messages": display_messages,
            "context": {
                "used": total_used,
                "limit": context_limit,
                "percent": percent
            }
        })

    @bp.route('/modules', methods=['GET'])
    def get_modules():
        return jsonify(system_instance.llm_chat.module_loader.get_module_list())

    @bp.route('/health', methods=['GET'])
    def health_check():
        return jsonify({"status": "ok"})

    @bp.route('/status', methods=['GET'])
    def get_unified_status():
        """Unified status endpoint - single call for all UI state needs."""
        try:
            from core.chat.history import count_tokens, count_message_tokens
            
            # Ensure state engine is synced with current settings before checking tools
            system_instance.llm_chat._update_state_engine()
            
            # Prompt state
            prompt_state = prompts.get_current_state()
            prompt_name = prompts.get_active_preset_name()
            prompt_char_count = prompts.get_prompt_char_count()
            is_assembled = prompts.is_assembled_mode()
            
            # Ability/function state
            function_names = system_instance.llm_chat.function_manager.get_enabled_function_names()
            ability_info = system_instance.llm_chat.function_manager.get_current_ability_info()
            has_cloud_tools = system_instance.llm_chat.function_manager.has_network_tools_enabled()
            
            # Spice state
            chat_settings = system_instance.llm_chat.session_manager.get_chat_settings()
            spice_enabled = chat_settings.get('spice_enabled', True)
            current_spice = prompts.get_current_spice()
            
            # TTS state
            tts_playing = getattr(system_instance.tts, '_is_playing', False)
            
            # Chat state
            active_chat = system_instance.llm_chat.get_active_chat()
            
            # Streaming state
            is_streaming = getattr(system_instance.llm_chat.streaming_chat, 'is_streaming', False)
            
            # Context usage (lightweight calculation)
            # Images are stripped from history for LLM, so don't count them
            context_limit = getattr(config, 'CONTEXT_LIMIT', 32000)
            raw_messages = system_instance.llm_chat.session_manager.get_messages()
            message_count = len(raw_messages)
            history_tokens = sum(count_message_tokens(m.get("content", ""), include_images=False) for m in raw_messages)
            try:
                prompt_content = system_instance.llm_chat.current_system_prompt or ""
                prompt_tokens = count_tokens(prompt_content) if prompt_content else 0
            except Exception:
                prompt_tokens = 0
            total_used = history_tokens + prompt_tokens
            context_percent = min(100, int((total_used / context_limit) * 100)) if context_limit > 0 else 0
            
            return jsonify({
                "prompt_name": prompt_name,
                "prompt_char_count": prompt_char_count,
                "prompt": prompt_state,
                "ability": ability_info,
                "functions": function_names,
                "has_cloud_tools": has_cloud_tools,
                "tts_enabled": config.TTS_ENABLED,
                "tts_playing": tts_playing,
                "active_chat": active_chat,
                "is_streaming": is_streaming,
                "message_count": message_count,
                "spice": {
                    "current": current_spice,
                    "enabled": spice_enabled,
                    "available": is_assembled
                },
                "context": {
                    "used": total_used,
                    "limit": context_limit,
                    "percent": context_percent
                },
                # Combined init data - reduces startup API calls
                "chats": system_instance.llm_chat.list_chats(),
                "chat_settings": chat_settings
            })
        except Exception as e:
            logger.error(f"Error getting unified status: {e}")
            return jsonify({"error": "Failed to get status"}), 500

    @bp.route('/events', methods=['GET'])
    def event_stream():
        """SSE endpoint for real-time event streaming."""
        from core.event_bus import get_event_bus
        
        # Capture request args BEFORE entering generator (request context ends after return)
        replay = request.args.get('replay', 'false').lower() == 'true'
        
        def generate():
            bus = get_event_bus()
            
            for event in bus.subscribe(replay=replay):
                yield f"data: {json.dumps(event)}\n\n"
        
        return Response(
            generate(),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'X-Accel-Buffering': 'no'
            }
        )

    @bp.route('/system/status', methods=['GET'])
    def get_system_status():
        try:
            # Ensure state engine is synced with current settings before checking tools
            system_instance.llm_chat._update_state_engine()
            
            prompt_state = prompts.get_current_state()
            function_names = system_instance.llm_chat.function_manager.get_enabled_function_names()
            ability_info = system_instance.llm_chat.function_manager.get_current_ability_info()
            
            # Check if enabled tools include network/cloud tools (uses NETWORK flag from modules)
            has_cloud_tools = system_instance.llm_chat.function_manager.has_network_tools_enabled()
            
            # Get spice state
            chat_settings = system_instance.llm_chat.session_manager.get_chat_settings()
            spice_enabled = chat_settings.get('spice_enabled', True)
            current_spice = prompts.get_current_spice()
            is_assembled = prompts.is_assembled_mode()
            
            return jsonify({
                "prompt": prompt_state,
                "prompt_name": prompts.get_active_preset_name(),
                "prompt_char_count": prompts.get_prompt_char_count(),
                "functions": function_names,
                "ability": ability_info,
                "tts_enabled": config.TTS_ENABLED,
                "has_cloud_tools": has_cloud_tools,
                "spice": {
                    "current": current_spice,
                    "enabled": spice_enabled,
                    "available": is_assembled  # Only works in assembled mode
                }
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

    @bp.route('/history/tool-call/<tool_call_id>', methods=['DELETE'])
    def remove_tool_call(tool_call_id):
        """Remove a specific tool call and its result from history."""
        try:
            if system_instance.llm_chat.session_manager.remove_tool_call(tool_call_id):
                return jsonify({"status": "success", "message": "Tool call removed"})
            else:
                return jsonify({"error": "Tool call not found"}), 404
        except Exception as e:
            logger.error(f"Error removing tool call: {e}", exc_info=True)
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
                
                publish(Events.CHAT_SWITCHED, {"name": chat_name})
                
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
            
            # Publish SSE event for UI updates
            publish(Events.CHAT_SETTINGS_CHANGED, {
                "chat": chat_name,
                "settings": new_settings
            })
            
            logger.info(f"Updated and applied settings for chat '{chat_name}'")
            return jsonify({"status": "success", "message": f"Settings updated for '{chat_name}'"})
            
        except Exception as e:
            logger.error(f"Error updating settings for '{chat_name}': {e}")
            return jsonify({"error": str(e)}), 500

    @bp.route('/tts/speak', methods=['POST'])
    def handle_tts_speak():
        import time as _time
        tts_start = _time.time()
        logger.debug(f"[TIMING] /tts/speak received request")
        data = request.json
        text = data.get('text')
        output_mode = data.get('output_mode', 'play')
        if not text: 
            return jsonify({"error": "No text provided"}), 400
        
        logger.debug(f"[TIMING] /tts/speak processing {len(text)} chars, mode={output_mode}")
        
        # Check if TTS is enabled
        if not config.TTS_ENABLED:
            logger.debug(f"[TIMING] TTS disabled, returning early")
            if output_mode == 'play':
                return jsonify({"status": "success", "message": "TTS disabled"})
            elif output_mode == 'file':
                return jsonify({"status": "success", "message": "TTS disabled"}), 200
        
        if output_mode == 'play':
            system_instance.tts.speak(text)
            return jsonify({"status": "success", "message": "Playback started."})
        elif output_mode == 'file':
            logger.debug(f"[TIMING] Starting TTS generation at {_time.time() - tts_start:.2f}s")
            audio_data = system_instance.tts.generate_audio_data(text)
            logger.debug(f"[TIMING] TTS generation complete at {_time.time() - tts_start:.2f}s, got {len(audio_data) if audio_data else 0} bytes")
            if audio_data:
                return send_file(io.BytesIO(audio_data), mimetype='audio/wav', as_attachment=True, download_name='output.wav')
            else:
                return jsonify({"error": "TTS generation failed"}), 503
        else:
            return jsonify({"error": "Invalid output_mode"}), 400

    @bp.route('/tts/status', methods=['GET'])
    def tts_status():
        """Get local TTS playback status."""
        playing = getattr(system_instance.tts, '_is_playing', False)
        return jsonify({"playing": playing})

    @bp.route('/tts/stop', methods=['POST'])
    def tts_stop():
        """Stop local TTS playback."""
        system_instance.tts.stop()
        return jsonify({"status": "success"})

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
            
            # Find a working sample rate for this device
            # Cheap USB devices often only support 48000Hz
            sample_rate = None
            default_rate = 44100
            
            if device_index is not None:
                try:
                    dev_info = sd.query_devices(device_index)
                    default_rate = int(dev_info['default_samplerate'])
                except Exception:
                    pass
            
            test_rates = [default_rate, 48000, 44100, 32000, 24000, 22050, 16000]
            seen = set()
            test_rates = [r for r in test_rates if not (r in seen or seen.add(r))]
            
            for rate in test_rates:
                try:
                    stream = sd.OutputStream(
                        device=device_index,
                        samplerate=rate,
                        channels=1,
                        dtype=np.float32
                    )
                    stream.close()
                    sample_rate = rate
                    break
                except Exception:
                    continue
            
            if sample_rate is None:
                return jsonify({
                    'success': False,
                    'error': f'Device does not support any common sample rate'
                }), 400
            
            # Generate test tone at working rate
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
                'sample_rate': sample_rate,
            })
            
        except Exception as e:
            logger.error(f"Audio output test failed: {e}")
            from core.audio import classify_audio_error
            return jsonify({
                'success': False,
                'error': classify_audio_error(e)
            }), 500

    # =============================================================================
    # MEMORY SCOPE ROUTES
    # =============================================================================
    
    @bp.route('/api/memory/scopes', methods=['GET'])
    def get_memory_scopes():
        """Get list of memory scopes with counts."""
        try:
            from functions import memory
            scopes = memory.get_scopes()
            return jsonify({"scopes": scopes})
        except Exception as e:
            logger.error(f"Failed to get memory scopes: {e}")
            return jsonify({"error": str(e)}), 500

    @bp.route('/api/memory/scopes', methods=['POST'])
    def create_memory_scope():
        """Create a new memory scope (persists even when empty)."""
        try:
            import re
            data = request.get_json() or {}
            name = data.get('name', '').strip().lower()
            
            # Validate: alphanumeric + underscore, 1-32 chars
            if not name or not re.match(r'^[a-z0-9_]{1,32}$', name):
                return jsonify({"error": "Invalid scope name. Use lowercase letters, numbers, underscore, max 32 chars."}), 400
            
            # Actually create the scope in the registry
            from functions import memory
            if memory.create_scope(name):
                return jsonify({"created": name})
            else:
                return jsonify({"error": "Failed to create scope"}), 500
        except Exception as e:
            logger.error(f"Failed to create memory scope: {e}")
            return jsonify({"error": str(e)}), 500

    # =============================================================================
    # STATE ENGINE ROUTES
    # =============================================================================
    
    @bp.route('/api/state/presets', methods=['GET'])
    def list_state_presets():
        """List available state presets from core and user directories."""
        try:
            from pathlib import Path
            presets = []
            
            # Get project root from api.py location
            project_root = Path(__file__).parent.parent
            
            # Search paths: user first (can override), then core
            search_paths = [
                project_root / "user" / "state_presets",
                project_root / "core" / "state_presets",
            ]
            
            seen = set()
            for search_dir in search_paths:
                if not search_dir.exists():
                    continue
                for preset_file in search_dir.glob("*.json"):
                    name = preset_file.stem
                    if name in seen:
                        continue  # User preset overrides core
                    seen.add(name)
                    
                    try:
                        with open(preset_file, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        presets.append({
                            "name": name,
                            "display_name": data.get("name", name),
                            "description": data.get("description", ""),
                            "key_count": len(data.get("initial_state", {})),
                            "source": "user" if "user" in str(search_dir) else "core"
                        })
                    except Exception as e:
                        logger.warning(f"Failed to load preset {preset_file}: {e}")
            
            return jsonify({"presets": presets})
        except Exception as e:
            logger.error(f"Failed to list state presets: {e}")
            return jsonify({"error": str(e)}), 500
    
    @bp.route('/api/state/<chat_name>', methods=['GET'])
    def get_chat_state(chat_name):
        """Get current state for a chat."""
        try:
            from pathlib import Path
            from core.chat.state_engine import StateEngine
            
            db_path = Path("user/history/sapphire_history.db")
            if not db_path.exists():
                return jsonify({"error": "Database not found"}), 404
            
            engine = StateEngine(chat_name, db_path)
            
            # Check if settings preset differs from DB - sync if needed
            session_manager = system_instance.llm_chat.session_manager
            if chat_name == session_manager.get_active_chat_name():
                chat_settings = session_manager.get_chat_settings()
                
                if chat_settings.get('state_engine_enabled', False):
                    settings_preset = chat_settings.get('state_preset')
                    db_preset = engine.preset_name
                    
                    if settings_preset and settings_preset != db_preset:
                        if engine.is_empty():
                            # No state yet - load full preset
                            turn = session_manager.get_turn_count()
                            success, msg = engine.load_preset(settings_preset, turn)
                            if success:
                                logger.info(f"[STATE] API loaded preset '{settings_preset}' for empty state")
                        else:
                            # State exists - just reload config (don't wipe progress!)
                            engine.reload_preset_config(settings_preset)
                            logger.info(f"[STATE] API synced config for '{settings_preset}' (preserving state)")
            
            state = engine.get_state_full()
            
            # Format for API response
            formatted = {}
            for key, entry in state.items():
                formatted[key] = {
                    "value": entry["value"],
                    "type": entry.get("type"),
                    "label": entry.get("label"),
                    "turn": entry.get("turn")
                }
            
            return jsonify({
                "chat_name": chat_name,
                "state": formatted,
                "key_count": len(formatted),
                "preset": engine.preset_name
            })
        except Exception as e:
            logger.error(f"Failed to get state for {chat_name}: {e}")
            return jsonify({"error": str(e)}), 500
    
    @bp.route('/api/state/<chat_name>/history', methods=['GET'])
    def get_chat_state_history(chat_name):
        """Get state change history for a chat."""
        try:
            from pathlib import Path
            from core.chat.state_engine import StateEngine
            
            db_path = Path("user/history/sapphire_history.db")
            if not db_path.exists():
                return jsonify({"error": "Database not found"}), 404
            
            limit = request.args.get('limit', 100, type=int)
            key = request.args.get('key')
            
            engine = StateEngine(chat_name, db_path)
            history = engine.get_history(key=key, limit=limit)
            
            return jsonify({
                "chat_name": chat_name,
                "history": history,
                "count": len(history)
            })
        except Exception as e:
            logger.error(f"Failed to get state history for {chat_name}: {e}")
            return jsonify({"error": str(e)}), 500
    
    @bp.route('/api/state/<chat_name>/reset', methods=['POST'])
    def reset_chat_state(chat_name):
        """Reset state to preset or clear all."""
        try:
            from pathlib import Path
            from core.chat.state_engine import StateEngine
            
            db_path = Path("user/history/sapphire_history.db")
            if not db_path.exists():
                return jsonify({"error": "Database not found"}), 404
            
            data = request.get_json() or {}
            preset = data.get('preset')
            
            engine = StateEngine(chat_name, db_path)
            
            if preset:
                # Get current turn from chat
                turn = system_instance.llm_chat.session_manager.get_turn_count() if system_instance else 0
                success, msg = engine.load_preset(preset, turn)
                if not success:
                    return jsonify({"error": msg}), 400
                result = {"status": "reset", "preset": preset, "message": msg}
            else:
                # Clear all state
                engine.clear_all()
                result = {"status": "cleared", "message": "State cleared"}
            
            # Reload live engine if it exists for this chat (fixes stale cache)
            if system_instance:
                live_engine = system_instance.llm_chat.function_manager.get_state_engine()
                if live_engine and live_engine.chat_name == chat_name:
                    live_engine.reload_from_db()
                    logger.info(f"[STATE] Reloaded live engine for '{chat_name}'")
            
            return jsonify(result)
        except Exception as e:
            logger.error(f"Failed to reset state for {chat_name}: {e}")
            return jsonify({"error": str(e)}), 500
    
    @bp.route('/api/state/<chat_name>/set', methods=['POST'])
    def set_chat_state_value(chat_name):
        """Set a state value directly (user/admin action)."""
        try:
            from pathlib import Path
            from core.chat.state_engine import StateEngine
            
            db_path = Path("user/history/sapphire_history.db")
            if not db_path.exists():
                return jsonify({"error": "Database not found"}), 404
            
            data = request.get_json() or {}
            key = data.get('key')
            value = data.get('value')
            
            if not key:
                return jsonify({"error": "Key required"}), 400
            
            engine = StateEngine(chat_name, db_path)
            turn = system_instance.llm_chat.session_manager.get_turn_count() if system_instance else 0
            
            success, msg = engine.set_state(key, value, "user", turn, "Manual edit via UI")
            
            if success:
                # Reload live engine if it exists for this chat (fixes stale cache)
                if system_instance:
                    live_engine = system_instance.llm_chat.function_manager.get_state_engine()
                    if live_engine and live_engine.chat_name == chat_name:
                        live_engine.reload_from_db()
                
                return jsonify({"status": "set", "key": key, "value": value})
            else:
                return jsonify({"error": msg}), 400
        except Exception as e:
            logger.error(f"Failed to set state for {chat_name}: {e}")
            return jsonify({"error": str(e)}), 500

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