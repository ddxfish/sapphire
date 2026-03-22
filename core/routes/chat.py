# core/routes/chat.py - Core chat, history, and chat management routes
import asyncio
import json
import os
import time
import logging
from pathlib import Path

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

import config
from core.auth import require_login, check_endpoint_rate
from core.api_fastapi import get_system, _apply_chat_settings, PROJECT_ROOT
from core.event_bus import publish, Events
from core import prompts
from core.story_engine import STORY_TOOL_NAMES
from core.stt.stt_null import NullWhisperClient as _NullWhisperClient
from core.stt.utils import can_transcribe
from core.wakeword.wakeword_null import NullWakeWordDetector as _NullWakeWordDetector

logger = logging.getLogger(__name__)

router = APIRouter()


def format_messages_for_display(messages):
    """Transform message structure into display format for UI."""
    display_messages = []
    current_block = None

    def finalize_block(block):
        result = {
            "role": "assistant",
            "parts": block.get("parts", []),
            "timestamp": block.get("timestamp")
        }
        if block.get("metadata"):
            result["metadata"] = block["metadata"]
        if block.get("persona"):
            result["persona"] = block["persona"]
        return result

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
            if msg.get("persona"):
                user_msg["persona"] = msg["persona"]

            if isinstance(content, list):
                text_parts = []
                images = []
                user_files = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif block.get("type") == "image":
                            images.append({
                                "data": block.get("data", ""),
                                "media_type": block.get("media_type", "image/jpeg")
                            })
                        elif block.get("type") == "file":
                            user_files.append({
                                "filename": block.get("filename", ""),
                                "text": block.get("text", "")
                            })
                    elif isinstance(block, str):
                        text_parts.append(block)
                user_msg["content"] = " ".join(text_parts)
                if images:
                    user_msg["images"] = images
                if user_files:
                    user_msg["files"] = user_files
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

            if msg.get("metadata"):
                current_block["metadata"] = msg["metadata"]

            if msg.get("persona") and "persona" not in current_block:
                current_block["persona"] = msg["persona"]

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


@router.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}


@router.get("/api/history")
async def get_history(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Get history formatted for UI display with context usage info."""
    from core.chat.history import count_tokens, count_message_tokens

    raw_messages = system.llm_chat.session_manager.get_messages_for_display()
    display_messages = format_messages_for_display(raw_messages)

    context_limit = getattr(config, 'CONTEXT_LIMIT', 32000)
    history_tokens = sum(
        count_message_tokens(m.get("content", ""), include_images=False)
        + count_tokens(m.get("thinking", "") or "")
        for m in raw_messages
    )

    try:
        prompt_content = system.llm_chat.current_system_prompt or ""
        prompt_tokens = count_tokens(prompt_content) if prompt_content else 0
    except Exception:
        prompt_tokens = 0

    total_used = history_tokens + prompt_tokens
    percent = min(100, int((total_used / context_limit) * 100)) if context_limit > 0 else 0

    return {
        "messages": display_messages,
        "chat_name": system.llm_chat.session_manager.get_active_chat_name(),
        "context": {
            "used": total_used,
            "limit": context_limit,
            "percent": percent
        }
    }


@router.post("/api/chat")
async def handle_chat(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Non-streaming chat endpoint."""
    check_endpoint_rate(request, 'chat', max_calls=30, window=60)

    data = await request.json()
    if not data or 'text' not in data:
        raise HTTPException(status_code=400, detail="No text provided")

    system.web_active_inc()
    try:
        response = await asyncio.to_thread(system.process_llm_query, data['text'], True)
    finally:
        system.web_active_dec()
    return {"response": response}


@router.post("/api/chat/stream")
async def handle_chat_stream(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Streaming chat endpoint (SSE)."""
    check_endpoint_rate(request, 'chat', max_calls=30, window=60)

    data = await request.json()
    if not data or 'text' not in data:
        raise HTTPException(status_code=400, detail="No text provided")

    logger.info(f"[CHAT-STREAM] Request received at {time.time():.3f}")

    prefill = data.get('prefill')
    skip_user_message = data.get('skip_user_message', False)
    images = data.get('images', [])
    files = data.get('files', [])

    system.llm_chat.streaming_chat.cancel_flag = False
    system.web_active_inc()

    def generate():
        try:
            chunk_count = 0
            for event in system.llm_chat.chat_stream(data['text'], prefill=prefill, skip_user_message=skip_user_message, images=images, files=files):
                if system.llm_chat.streaming_chat.cancel_flag:
                    logger.info(f"STREAMING CANCELLED at chunk {chunk_count}")
                    yield f"data: {json.dumps({'cancelled': True})}\n\n"
                    break

                if event:
                    chunk_count += 1

                    if isinstance(event, dict):
                        event_type = event.get("type")

                        if event_type == "stream_started":
                            yield f"data: {json.dumps({'type': 'stream_started'})}\n\n"
                        elif event_type == "iteration_start":
                            yield f"data: {json.dumps({'type': 'iteration_start', 'iteration': event.get('iteration', 1)})}\n\n"
                        elif event_type == "content":
                            yield f"data: {json.dumps({'type': 'content', 'text': event.get('text', '')})}\n\n"
                        elif event_type == "tool_pending":
                            yield f"data: {json.dumps({'type': 'tool_pending', 'name': event.get('name'), 'index': event.get('index', 0)})}\n\n"
                        elif event_type == "tool_start":
                            yield f"data: {json.dumps({'type': 'tool_start', 'id': event.get('id'), 'name': event.get('name'), 'args': event.get('args', {})})}\n\n"
                        elif event_type == "tool_end":
                            yield f"data: {json.dumps({'type': 'tool_end', 'id': event.get('id'), 'name': event.get('name'), 'result': event.get('result', ''), 'error': event.get('error', False)})}\n\n"
                        elif event_type == "reload":
                            yield f"data: {json.dumps({'type': 'reload'})}\n\n"
                        else:
                            yield f"data: {json.dumps(event)}\n\n"
                    else:
                        if '<<RELOAD_PAGE>>' in str(event):
                            yield f"data: {json.dumps({'type': 'reload'})}\n\n"
                        else:
                            yield f"data: {json.dumps({'type': 'content', 'text': str(event)})}\n\n"

            if not system.llm_chat.streaming_chat.cancel_flag:
                ephemeral = system.llm_chat.streaming_chat.ephemeral
                logger.info(f"STREAMING COMPLETE: {chunk_count} chunks, ephemeral={ephemeral}")
                yield f"data: {json.dumps({'done': True, 'ephemeral': ephemeral})}\n\n"

        except ConnectionError as e:
            logger.warning(f"STREAMING: {e}")
            from core.chat.chat import friendly_llm_error
            msg = friendly_llm_error(e) or str(e)
            yield f"data: {json.dumps({'error': msg})}\n\n"
        except Exception as e:
            logger.error(f"STREAMING ERROR: {e}", exc_info=True)
            from core.chat.chat import friendly_llm_error
            msg = friendly_llm_error(e) or str(e)
            yield f"data: {json.dumps({'error': msg})}\n\n"
        finally:
            system.llm_chat.streaming_chat.cancel_flag = True
            system.web_active_dec()

    return StreamingResponse(
        generate(),
        media_type='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no'
        }
    )


@router.post("/api/cancel")
async def handle_cancel(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Cancel ongoing streaming generation."""
    try:
        system.llm_chat.streaming_chat.cancel_flag = True
        logger.info("CANCEL: Flag set")
        return {"status": "success", "message": "Cancellation requested"}
    except Exception as e:
        logger.error(f"Error during cancellation: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/events")
async def event_stream(request: Request, replay: str = 'false', _=Depends(require_login)):
    """SSE endpoint for real-time event streaming (async — no threadpool thread consumed)."""
    from core.event_bus import get_event_bus

    do_replay = replay.lower() == 'true'

    async def generate():
        bus = get_event_bus()
        async for event in bus.async_subscribe(replay=do_replay):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        generate(),
        media_type='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no'
        }
    )


@router.get("/api/status")
async def get_unified_status(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Unified status endpoint - single call for all UI state needs."""
    try:
        from core.chat.history import count_tokens, count_message_tokens

        chat_settings = system.llm_chat.session_manager.get_chat_settings()

        # Backfill trim_color from persona if missing (pre-persona chats)
        if not chat_settings.get('trim_color') and chat_settings.get('persona'):
            try:
                from core.personas import persona_manager
                p = persona_manager.get(chat_settings['persona'])
                if p:
                    chat_settings['trim_color'] = p.get('settings', {}).get('trim_color', '')
            except Exception:
                pass

        story_enabled = chat_settings.get('story_engine_enabled', False)
        if story_enabled and not system.llm_chat.function_manager.get_story_engine():
            system.llm_chat._update_story_engine()

        prompt_state = prompts.get_current_state()
        prompt_name = prompts.get_active_preset_name()
        prompt_char_count = prompts.get_prompt_char_count()
        prompt_privacy_required = prompts.is_current_prompt_private() and not chat_settings.get('private_chat', False)
        is_assembled = prompts.is_assembled_mode()

        function_names = system.llm_chat.function_manager.get_enabled_function_names()
        toolset_info = system.llm_chat.function_manager.get_current_toolset_info()
        has_cloud_tools = system.llm_chat.function_manager.has_network_tools_enabled()

        spice_enabled = chat_settings.get('spice_enabled', True)
        current_spice = prompts.get_current_spice()
        next_spice = prompts.get_next_spice()

        tts_playing = getattr(system.tts, '_is_playing', False)
        active_chat = system.llm_chat.get_active_chat()
        is_streaming = getattr(system.llm_chat.streaming_chat, 'is_streaming', False)

        context_limit = getattr(config, 'CONTEXT_LIMIT', 32000)
        raw_messages = system.llm_chat.session_manager.get_messages()
        message_count = len(raw_messages)
        history_tokens = sum(
            count_message_tokens(m.get("content", ""), include_images=False)
            + count_tokens(m.get("thinking", "") or "")
            for m in raw_messages
        )

        try:
            prompt_content = system.llm_chat.current_system_prompt or ""
            prompt_tokens = count_tokens(prompt_content) if prompt_content else 0
        except Exception:
            prompt_tokens = 0

        total_used = history_tokens + prompt_tokens
        context_percent = min(100, int((total_used / context_limit) * 100)) if context_limit > 0 else 0

        story_status = None
        try:
            story_enabled_status = chat_settings.get('story_engine_enabled', False)
            story_preset = chat_settings.get('story_preset', '')
            if story_enabled_status:
                story_status = {
                    "enabled": True,
                    "preset": story_preset,
                    "preset_display": story_preset.replace('_', ' ').title() if story_preset else ''
                }
                live_engine = system.llm_chat.function_manager.get_story_engine()
                if live_engine and live_engine.story_prompt:
                    story_status["has_prompt"] = True
                if live_engine and hasattr(live_engine, 'preset_config'):
                    story_status["turn"] = getattr(live_engine, 'current_turn', 0)
                    visible_state = live_engine.get_visible_state() if hasattr(live_engine, 'get_visible_state') else {}
                    story_status["key_count"] = len(visible_state)
                    preset_config = live_engine.preset_config or {}
                    iterator_key = preset_config.get('progressive_prompt', {}).get('iterator', 'scene')
                    iterator_val = live_engine.get_state(iterator_key) if hasattr(live_engine, 'get_state') else None
                    if iterator_val is not None:
                        story_status["iterator_key"] = iterator_key
                        story_status["iterator_value"] = iterator_val
                        state_def = preset_config.get('initial_state', {}).get(iterator_key, {})
                        if state_def.get('type') == 'integer' and state_def.get('max'):
                            story_status["iterator_max"] = state_def['max']
        except Exception as e:
            logger.warning(f"Error getting story status: {e}")

        # Collect all story tool names (built-in + custom)
        all_story_names = set(STORY_TOOL_NAMES)
        live_engine = system.llm_chat.function_manager.get_story_engine()
        if live_engine:
            all_story_names |= live_engine.story_tool_names

        state_tools = [f for f in function_names if f in all_story_names]
        user_tools = [f for f in function_names if f not in all_story_names]

        # Story prompt override: prefix prompt name so user knows story prompt is active
        if live_engine and live_engine.story_prompt:
            prompt_name = f"[STORY] {prompt_name}"
            prompt_char_count = len(live_engine.story_prompt)

        return {
            "prompt_name": prompt_name,
            "prompt_char_count": prompt_char_count,
            "prompt_privacy_required": prompt_privacy_required,
            "prompt": prompt_state,
            "toolset": toolset_info,
            "functions": user_tools,
            "state_tools": state_tools,
            "has_cloud_tools": has_cloud_tools,
            "tts_enabled": config.TTS_ENABLED,
            "tts_provider": getattr(config, 'TTS_PROVIDER', 'none'),
            "stt_enabled": config.STT_ENABLED,
            "stt_provider": getattr(config, 'STT_PROVIDER', 'none'),
            "stt_ready": not isinstance(system.whisper_client, _NullWhisperClient),
            "wakeword_enabled": config.WAKE_WORD_ENABLED,
            "wakeword_ready": not isinstance(system.wake_detector, _NullWakeWordDetector),
            "tts_playing": tts_playing,
            "active_chat": active_chat,
            "is_streaming": is_streaming,
            "message_count": message_count,
            "spice": {
                "current": current_spice,
                "next": next_spice,
                "enabled": spice_enabled,
                "available": is_assembled
            },
            "context": {
                "used": total_used,
                "limit": context_limit,
                "percent": context_percent
            },
            "story": story_status,
            "chats": system.llm_chat.list_chats(),
            "chat_settings": chat_settings
        }
    except Exception as e:
        logger.error(f"Error getting unified status: {e}")
        raise HTTPException(status_code=500, detail="Failed to get status")


@router.get("/api/init")
async def get_init_data(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Mega-endpoint for initial page load - combines all plugin init data."""
    try:
        from core.toolsets import toolset_manager
        from core.routes.content import _build_spice_response
        from core.routes.plugins import _get_merged_plugins

        function_manager = system.llm_chat.function_manager
        session_manager = system.llm_chat.session_manager

        # Toolsets data
        toolsets_set = set()
        toolsets_set.update(function_manager.get_available_toolsets())
        toolsets_set.update(toolset_manager.get_toolset_names())
        network_functions = set(function_manager.get_network_functions())

        toolsets_list = []
        for ts_name in sorted(toolsets_set):
            if ts_name in ['all', 'none']:
                ts_type = 'builtin'
                func_list = [t['function']['name'] for t in function_manager.all_possible_tools] if ts_name == 'all' else []
            elif ts_name in function_manager.function_modules and not toolset_manager.toolset_exists(ts_name):
                ts_type = 'module'
                func_list = function_manager.function_modules[ts_name]['available_functions']
            elif toolset_manager.toolset_exists(ts_name):
                ts_type = toolset_manager.get_toolset_type(ts_name)
                func_list = toolset_manager.get_toolset_functions(ts_name)
            else:
                ts_type = 'unknown'
                func_list = []

            toolsets_list.append({
                "name": ts_name,
                "function_count": len(func_list),
                "type": ts_type,
                "functions": func_list,
                "emoji": toolset_manager.get_toolset_emoji(ts_name) if toolset_manager.toolset_exists(ts_name) else "",
                "has_network_tools": bool(set(func_list) & network_functions)
            })

        toolset_info = function_manager.get_current_toolset_info()
        current_toolset = {
            "name": toolset_info.get("name", "custom"),
            "function_count": toolset_info.get("function_count", 0),
            "enabled_functions": function_manager.get_enabled_function_names(),
            "has_network_tools": function_manager.has_network_tools_enabled()
        }

        # Functions data
        enabled = set(function_manager.get_enabled_function_names())
        modules = {}
        for module_name, module_info in function_manager.function_modules.items():
            functions = []
            for tool in module_info['tools']:
                func_name = tool['function']['name']
                functions.append({
                    "name": func_name,
                    "description": tool['function'].get('description', ''),
                    "enabled": func_name in enabled,
                    "is_network": func_name in network_functions
                })
            modules[module_name] = {"functions": functions, "count": len(functions), "emoji": module_info.get('emoji', '')}

        # Prompts data
        prompt_names = prompts.list_prompts()
        prompt_list = []
        for name in prompt_names:
            pdata = prompts.get_prompt(name)
            prompt_list.append({
                'name': name,
                'type': pdata.get('type', 'unknown') if isinstance(pdata, dict) else 'monolith',
                'char_count': len(pdata.get('content', '')) if isinstance(pdata, dict) else len(str(pdata))
            })
        current_prompt_name = prompts.get_active_preset_name()
        current_prompt_data = prompts.get_prompt(current_prompt_name) if current_prompt_name else None
        prompt_components = prompts.prompt_manager.components if hasattr(prompts.prompt_manager, 'components') else {}

        # Spices data
        spice_data = _build_spice_response()

        # Spice sets data
        from core.spice_sets import spice_set_manager
        spice_sets_list = []
        for name in spice_set_manager.get_set_names():
            ss = spice_set_manager.get_set(name)
            spice_sets_list.append({
                "name": name,
                "categories": ss.get('categories', []),
                "category_count": len(ss.get('categories', [])),
                "emoji": ss.get('emoji', '')
            })
        current_spice_set = spice_set_manager.active_name

        # Settings
        avatars_in_chat = getattr(config, 'AVATARS_IN_CHAT', False)
        wizard_step = getattr(config, 'SETUP_WIZARD_STEP', 'complete')

        # Avatars
        STATIC_DIR = PROJECT_ROOT / "interfaces" / "web" / "static"
        avatar_dir = PROJECT_ROOT / 'user' / 'public' / 'avatars'
        static_dir = STATIC_DIR / 'users'
        avatars = {}
        for role in ('user', 'assistant'):
            custom = list(avatar_dir.glob(f'{role}.*')) if avatar_dir.exists() else []
            if custom:
                ext = custom[0].suffix
                avatars[role] = f"/user-assets/avatars/{role}{ext}"
            else:
                for ext in ('.webp', '.jpg', '.png'):
                    if (static_dir / f'{role}{ext}').exists():
                        avatars[role] = f"/static/users/{role}{ext}"
                        break
                else:
                    avatars[role] = None

        # Personas data
        from core.personas import persona_manager
        personas_list = persona_manager.get_list()

        # Plugins config (merged: static + user overrides)
        plugins_config = _get_merged_plugins()

        return {
            "toolsets": {
                "list": toolsets_list,
                "current": current_toolset
            },
            "functions": {
                "modules": modules
            },
            "prompts": {
                "list": prompt_list,
                "current_name": current_prompt_name,
                "current": current_prompt_data,
                "components": prompt_components,
                "presets": dict(prompts.prompt_manager.scenario_presets)
            },
            "spices": spice_data,
            "spice_sets": {
                "list": spice_sets_list,
                "current": current_spice_set
            },
            "personas": {
                "list": personas_list,
                "default": getattr(config, 'DEFAULT_PERSONA', '') or ''
            },
            "settings": {
                "AVATARS_IN_CHAT": avatars_in_chat,
                "DEFAULT_USERNAME": getattr(config, 'DEFAULT_USERNAME', 'Human Protagonist'),
                "USER_TIMEZONE": getattr(config, 'USER_TIMEZONE', 'UTC') or 'UTC'
            },
            "wizard_step": wizard_step,
            "avatars": avatars,
            "plugins_config": plugins_config
        }
    except Exception as e:
        logger.error(f"Error getting init data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# HISTORY MANAGEMENT ROUTES
# =============================================================================

@router.delete("/api/history/messages")
async def remove_history_messages(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Remove messages from history."""
    data = await request.json()
    count = data.get('count', 0) if data else 0
    user_message = data.get('user_message') if data else None

    # Method 1: Delete from specific user message
    if user_message:
        try:
            if system.llm_chat.session_manager.remove_from_user_message(user_message):
                return {"status": "success", "message": "Removed from user message"}
            else:
                raise HTTPException(status_code=404, detail="User message not found")
        except Exception as e:
            logger.error(f"Error removing from user message: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Method 2: Clear all
    if count == -1:
        try:
            session_manager = system.llm_chat.session_manager
            chat_name = session_manager.get_active_chat_name()
            session_manager.clear()

            chat_settings = session_manager.get_chat_settings()
            story_enabled = chat_settings.get('story_engine_enabled', False)
            if story_enabled:
                from core.story_engine import StoryEngine
                db_path = PROJECT_ROOT / "user" / "history" / "sapphire_history.db"
                if db_path.exists() and chat_name:
                    engine = StoryEngine(chat_name, db_path)
                    preset = chat_settings.get('story_preset')
                    if preset:
                        engine.load_preset(preset, 1)
                    else:
                        engine.clear_all()

                    live_engine = system.llm_chat.function_manager.get_story_engine()
                    if live_engine and live_engine.chat_name == chat_name:
                        live_engine.reload_from_db()

            origin = request.headers.get('X-Session-ID')
            publish(Events.CHAT_CLEARED, {"chat_name": chat_name, "origin": origin})
            return {"status": "success", "message": "All chat history cleared."}
        except Exception as e:
            logger.error(f"Error clearing history: {e}")
            raise HTTPException(status_code=500, detail="Failed to clear history")

    # Method 3: Delete last N
    if not isinstance(count, int) or count <= 0:
        raise HTTPException(status_code=400, detail="Invalid count")

    try:
        if system.llm_chat.session_manager.remove_last_messages(count):
            return {"status": "success", "message": f"Removed {count} messages.", "deleted": count}
        else:
            raise HTTPException(status_code=500, detail="Failed to remove messages")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/history/messages/remove-last-assistant")
async def remove_last_assistant(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Remove only the last assistant message in a turn."""
    data = await request.json()
    timestamp = data.get('timestamp')
    if not timestamp:
        raise HTTPException(status_code=400, detail="Timestamp required")
    try:
        if system.llm_chat.session_manager.remove_last_assistant_in_turn(timestamp):
            return {"status": "success", "message": "Removed last assistant"}
        else:
            raise HTTPException(status_code=500, detail="Failed to remove")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/history/messages/remove-from-assistant")
async def remove_from_assistant(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Remove assistant message and everything after it."""
    data = await request.json()
    timestamp = data.get('timestamp')
    if not timestamp:
        raise HTTPException(status_code=400, detail="Timestamp required")
    try:
        if system.llm_chat.session_manager.remove_from_assistant_timestamp(timestamp):
            return {"status": "success", "message": "Removed from assistant"}
        else:
            raise HTTPException(status_code=404, detail="Assistant message not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/history/tool-call/{tool_call_id}")
async def remove_tool_call(tool_call_id: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Remove a specific tool call and its result."""
    try:
        if system.llm_chat.session_manager.remove_tool_call(tool_call_id):
            return {"status": "success", "message": "Tool call removed"}
        else:
            raise HTTPException(status_code=404, detail="Tool call not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/history/messages/edit")
async def edit_message(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Edit a message by timestamp."""
    data = await request.json()
    role = data.get('role')
    timestamp = data.get('timestamp')
    new_content = data.get('new_content')

    if not all([role, timestamp, new_content is not None]):
        raise HTTPException(status_code=400, detail="Missing required fields")
    if role not in ['user', 'assistant']:
        raise HTTPException(status_code=400, detail="Invalid role")

    try:
        if system.llm_chat.session_manager.edit_message_by_timestamp(role, timestamp, new_content):
            return {"status": "success", "message": "Message updated"}
        else:
            raise HTTPException(status_code=404, detail="Message not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/history/raw")
async def get_raw_history(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Get raw history structure."""
    return system.llm_chat.session_manager.get_messages()


@router.post("/api/history/import")
async def import_history(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Import messages into current chat."""
    data = await request.json()
    messages = data.get('messages')
    if not messages or not isinstance(messages, list):
        raise HTTPException(status_code=400, detail="Invalid messages array")
    try:
        session_manager = system.llm_chat.session_manager
        session_manager.current_chat.messages = messages
        session_manager._save_current_chat()
        return {"status": "success", "message": f"Imported {len(messages)} messages"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# CHAT MANAGEMENT ROUTES
# =============================================================================

@router.get("/api/chats")
async def list_chats(request: Request, type: str = None, _=Depends(require_login), system=Depends(get_system)):
    """List chats. Optional ?type=regular|story to filter."""
    try:
        chats = system.llm_chat.list_chats()
        if type == "regular":
            chats = [c for c in chats if not c.get("story_chat")]
        elif type == "story":
            chats = [c for c in chats if c.get("story_chat")]
        active_chat = system.llm_chat.get_active_chat()
        return {"chats": chats, "active_chat": active_chat}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to list chats")


@router.post("/api/chats")
async def create_chat(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Create a new chat."""
    try:
        data = await request.json() or {}
        chat_name = data.get('name')
        if not chat_name or not chat_name.strip():
            raise HTTPException(status_code=400, detail="Chat name required")
        if system.llm_chat.create_chat(chat_name):
            return {"status": "success", "name": chat_name}
        else:
            raise HTTPException(status_code=409, detail=f"Chat '{chat_name}' already exists")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to create chat")


@router.post("/api/chats/private")
async def create_private_chat(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Create a permanently private chat (privacy enforced, no toggle)."""
    from core.settings_manager import settings as sm
    if sm.is_managed():
        raise HTTPException(status_code=403, detail="Private chats are disabled in managed mode")
    try:
        data = await request.json() or {}
        raw_name = data.get("name", "").strip()
        if not raw_name:
            raw_name = "private"
        chat_name = "private_" + "".join(c for c in raw_name if c.isalnum() or c in (' ', '-', '_')).strip().replace(' ', '_').lower()

        # Unique name
        base_name = chat_name
        counter = 1
        existing = {c["name"] for c in system.llm_chat.list_chats()}
        while chat_name in existing:
            counter += 1
            chat_name = f"{base_name}_{counter}"

        if not system.llm_chat.create_chat(chat_name):
            raise HTTPException(status_code=500, detail="Failed to create private chat")
        if not system.llm_chat.switch_chat(chat_name):
            raise HTTPException(status_code=500, detail="Failed to switch to private chat")

        display = raw_name.replace('_', ' ').title()
        system.llm_chat.session_manager.update_chat_settings({
            "private_chat": True,
            "private_display_name": f"[PRIVATE] {display}",
        })

        settings = system.llm_chat.session_manager.get_chat_settings()
        _apply_chat_settings(system, settings)

        origin = request.headers.get('X-Session-ID')
        publish(Events.CHAT_SWITCHED, {"name": chat_name, "origin": origin})

        return {"status": "success", "chat_name": chat_name, "display_name": f"[PRIVATE] {display}"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create private chat: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/chats/{chat_name}")
async def delete_chat(chat_name: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Delete a chat."""
    try:
        was_active = (chat_name == system.llm_chat.get_active_chat())
        if system.llm_chat.delete_chat(chat_name):
            if was_active:
                settings = system.llm_chat.session_manager.get_chat_settings()
                _apply_chat_settings(system, settings)
            # Cleanup per-chat RAG documents
            try:
                from functions import knowledge
                knowledge.delete_scope(f"__rag__:{chat_name}")
            except Exception:
                pass
            # Dismiss any agents spawned for this chat
            try:
                if hasattr(system, 'agent_manager') and system.agent_manager:
                    for agent in system.agent_manager.check_all(chat_name=chat_name):
                        system.agent_manager.dismiss(agent['id'])
            except Exception:
                pass
            return {"status": "success", "message": f"Deleted: {chat_name}"}
        else:
            raise HTTPException(status_code=400, detail=f"Cannot delete '{chat_name}'")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to delete")


@router.post("/api/chats/{chat_name}/activate")
async def activate_chat(chat_name: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Activate/switch to a chat."""
    try:
        if system.llm_chat.switch_chat(chat_name):
            settings = system.llm_chat.session_manager.get_chat_settings()
            _apply_chat_settings(system, settings)
            origin = request.headers.get('X-Session-ID')
            publish(Events.CHAT_SWITCHED, {"name": chat_name, "origin": origin})
            return {"status": "success", "active_chat": chat_name, "settings": settings}
        else:
            raise HTTPException(status_code=400, detail=f"Cannot switch to: {chat_name}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to switch")


@router.get("/api/chats/active")
async def get_active_chat(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Get active chat name."""
    return {"active_chat": system.llm_chat.get_active_chat()}


@router.get("/api/chats/{chat_name}/settings")
async def get_chat_settings(chat_name: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Get settings for a specific chat."""
    try:
        session_manager = system.llm_chat.session_manager
        if chat_name == session_manager.active_chat_name:
            return {"settings": session_manager.get_chat_settings()}

        chat_path = session_manager._get_chat_path(chat_name)
        if not chat_path.exists():
            raise HTTPException(status_code=404, detail=f"Chat '{chat_name}' not found")

        with open(chat_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        if isinstance(data, dict) and "settings" in data:
            settings = data["settings"]
        else:
            from core.chat.history import SYSTEM_DEFAULTS
            settings = SYSTEM_DEFAULTS.copy()

        return {"settings": settings}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/chats/{chat_name}/settings")
async def update_chat_settings(chat_name: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Update settings for active chat."""
    try:
        data = await request.json()
        if not data or "settings" not in data:
            raise HTTPException(status_code=400, detail="Settings object required")

        session_manager = system.llm_chat.session_manager
        new_settings = data["settings"]

        if chat_name != session_manager.get_active_chat_name():
            raise HTTPException(status_code=400, detail="Can only update settings for active chat")

        if not session_manager.update_chat_settings(new_settings):
            raise HTTPException(status_code=500, detail="Failed to update settings")

        _apply_chat_settings(system, session_manager.get_chat_settings())

        origin = request.headers.get('X-Session-ID')
        publish(Events.CHAT_SETTINGS_CHANGED, {"chat": chat_name, "settings": new_settings, "origin": origin})

        # Return updated tool state so frontend can sync pills immediately
        fm = system.llm_chat.function_manager
        toolset_info = fm.get_current_toolset_info()
        function_names = fm.get_enabled_function_names()
        from core.story_engine import STORY_TOOL_NAMES
        all_story_names = set(STORY_TOOL_NAMES)
        engine = fm.get_story_engine()
        if engine:
            all_story_names |= engine.story_tool_names
        state_tools = [f for f in function_names if f in all_story_names]
        user_tools = [f for f in function_names if f not in all_story_names]

        return {
            "status": "success",
            "message": f"Settings updated for '{chat_name}'",
            "toolset": toolset_info,
            "functions": user_tools,
            "state_tools": state_tools,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
