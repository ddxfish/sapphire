# core/routes/chat.py - Core chat, history, and chat management routes
import asyncio
import json
import os
import time
import logging
from pathlib import Path

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse, Response

import config
from core.auth import require_login, check_endpoint_rate
from core.api_fastapi import get_system, _apply_chat_settings, PROJECT_ROOT
from core.event_bus import publish, Events
from core import prompts
from core.stt.stt_null import NullWhisperClient as _NullWhisperClient
from core.stt.utils import can_transcribe
from core.wakeword.wakeword_null import NullWakeWordDetector as _NullWakeWordDetector
from core.chat.display_format import format_messages_for_display

logger = logging.getLogger(__name__)

router = APIRouter()


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
    # Drain any UX notices the chat run wanted to surface (dangling toolset,
    # empty-content fallback). Notices are transient — read + clear so they
    # don't bleed into the next turn.
    notices = system.llm_chat.pending_notices
    system.llm_chat.pending_notices = []
    payload = {"response": response}
    if notices:
        payload["notices"] = notices
    return payload


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

    # Per-request StreamingChat instance. Each /api/chat call gets its own
    # — no more singleton stomping between tabs. H4 2026-04-22.
    stream, sid, active_chat = system.llm_chat.begin_stream()
    system.web_active_inc()

    def generate():
        try:
            chunk_count = 0
            for event in stream.chat_stream(data['text'], prefill=prefill, skip_user_message=skip_user_message, images=images, files=files):
                if stream.cancel_flag:
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
                        elif event_type == "notice":
                            yield f"data: {json.dumps({'type': 'notice', 'message': event.get('message', ''), 'severity': event.get('severity', 'warning')})}\n\n"
                        else:
                            yield f"data: {json.dumps(event)}\n\n"
                    else:
                        if '<<RELOAD_PAGE>>' in str(event):
                            yield f"data: {json.dumps({'type': 'reload'})}\n\n"
                        else:
                            yield f"data: {json.dumps({'type': 'content', 'text': str(event)})}\n\n"

            if not stream.cancel_flag:
                ephemeral = stream.ephemeral
                logger.info(f"STREAMING COMPLETE: {chunk_count} chunks, ephemeral={ephemeral}, chat={active_chat!r}")
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
            system.llm_chat.end_stream(sid, active_chat)
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
    """Cancel ongoing streaming generation.

    Optional `chat` query param scopes the cancel to that chat's active
    streams (every tab concurrently on the chat gets cancelled together).
    Without the param, every active stream is flagged. H4/H5 2026-04-22.
    """
    try:
        requested_chat = request.query_params.get('chat')
        # Surface isolation (2026-07-05): a live phone call's chat may be the one
        # the operator is viewing — web cancel must never kill the caller's turn.
        # Scoped: no-op on a call-owned chat. Unscoped: flag everything EXCEPT them.
        try:
            _mgr = getattr(system, "_conversation_manager", None)
            _ext = _mgr.external_chats() if _mgr else set()
        except Exception:
            _ext = set()
        if requested_chat and requested_chat in _ext:
            logger.info(f"CANCEL: no-op — '{requested_chat}' belongs to a live phone call")
            return {"status": "no-op",
                    "message": "That chat belongs to a live phone call — web cancel won't touch it."}
        count = system.llm_chat.cancel_streams(chat_name=requested_chat,
                                               exclude_chats=(None if requested_chat else _ext))
        if count == 0:
            if requested_chat:
                logger.info(f"CANCEL: no-op — no active stream for chat '{requested_chat}'")
                return {
                    "status": "no-op",
                    "message": f"No stream active for chat '{requested_chat}'.",
                }
            logger.info("CANCEL: no-op — no active streams")
            return {"status": "no-op", "message": "No active streams."}
        scope = f"chat '{requested_chat}'" if requested_chat else "all chats"
        logger.info(f"CANCEL: Flagged {count} stream(s) in {scope}")
        return {
            "status": "success",
            "message": f"Cancellation requested ({count} stream{'s' if count != 1 else ''} flagged).",
            "cancelled": count,
        }
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

        chat_settings = _backfill_persona_visuals(chat_settings)

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
        # H4 2026-04-22: was `streaming_chat.is_streaming` singleton read;
        # now aggregates across per-request streams.
        is_streaming = system.llm_chat.any_streaming() if hasattr(system.llm_chat, 'any_streaming') else False

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

        user_tools = list(function_names)

        return {
            "prompt_name": prompt_name,
            "prompt_char_count": prompt_char_count,
            "prompt_privacy_required": prompt_privacy_required,
            "prompt": prompt_state,
            "toolset": toolset_info,
            "functions": user_tools,
            "state_tools": [],
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
        from core.plugin_loader import plugin_loader
        def _get_load_errors():
            try: return plugin_loader.get_load_errors()
            except Exception: return []

        function_manager = system.llm_chat.function_manager
        session_manager = system.llm_chat.session_manager

        # Toolsets data
        toolsets_set = set()
        toolsets_set.update(function_manager.get_available_toolsets())
        toolsets_set.update(toolset_manager.get_toolset_names())
        network_functions = set(function_manager.get_network_functions())
        hidden_functions = function_manager.get_hidden_functions()

        toolsets_list = []
        for ts_name in sorted(toolsets_set):
            if ts_name in ['all', 'none']:
                ts_type = 'builtin'
                func_list = [t['function']['name'] for t in function_manager.all_possible_tools
                             if t['function']['name'] not in hidden_functions] if ts_name == 'all' else []
            elif ts_name in function_manager.function_modules and not toolset_manager.toolset_exists(ts_name):
                ts_type = 'module'
                func_list = [n for n in function_manager.function_modules[ts_name]['available_functions']
                             if n not in hidden_functions]
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
                if func_name in hidden_functions:
                    continue
                functions.append({
                    "name": func_name,
                    "description": tool['function'].get('description', ''),
                    "enabled": func_name in enabled,
                    "is_network": func_name in network_functions
                })
            # Same GROUP → one display group (mirrors /api/functions)
            key = module_info.get('group') or module_name
            slot = modules.setdefault(key, {"functions": [], "count": 0, "emoji": ""})
            slot["functions"].extend(functions)
            slot["count"] += len(functions)
            slot["emoji"] = slot["emoji"] or module_info.get('emoji', '')

        # Prompts data
        from core import prompt_packs
        prompt_names = prompts.list_prompts()
        pack_sources = prompt_packs.get_sources()
        user_prompt_names = set(prompts.prompt_manager._monoliths) | set(prompts.prompt_manager._scenario_presets)
        prompt_list = []
        for name in prompt_names:
            pdata = prompts.get_prompt(name)
            prompt_list.append({
                'name': name,
                'type': pdata.get('type', 'unknown') if isinstance(pdata, dict) else 'monolith',
                'char_count': len(pdata.get('content', '')) if isinstance(pdata, dict) else len(str(pdata)),
                'source': pack_sources.get(name) if name not in user_prompt_names else None
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

        # Scope declarations — frontend uses this to render scope dropdowns dynamically
        # in the chat sidebar, trigger editor, persona editor, etc.
        #
        # After Phase 4: ALL scope declarations come from plugin manifests. The memory
        # plugin contributes memory/goal/knowledge/people (with `plugin: "memory"`).
        # The 5 other plugins (email/bitcoin/gcal/telegram/discord) contribute their
        # own scope each. This loop is now the ONLY source of scope declarations.
        scope_declarations = []
        for plugin_name, info in plugin_loader._plugins.items():
            if not info.get("loaded") or not info.get("enabled"):
                continue
            manifest = info.get("manifest", {}) or {}
            for scope_def in manifest.get("capabilities", {}).get("scopes", []):
                # Defensive extraction: a sloppy third-party plugin manifest with
                # missing `key` or `endpoint` must not break /api/init for all users.
                # plugin_loader uses .get() and tolerates missing fields, so a
                # malformed scope makes it into _plugins — we skip it here with a
                # warning log instead of raising KeyError and bricking the sidebar.
                if not isinstance(scope_def, dict):
                    logger.warning(f"Plugin '{plugin_name}' has non-dict scope entry, skipping: {scope_def!r}")
                    continue
                key = scope_def.get("key")
                endpoint = scope_def.get("endpoint")
                if not key or not endpoint:
                    logger.warning(f"Plugin '{plugin_name}' has malformed scope (missing key or endpoint), skipping: {scope_def!r}")
                    continue
                scope_declarations.append({
                    "key": key,
                    "label": scope_def.get("label", key),
                    "plugin": plugin_name,
                    "endpoint": endpoint,
                    "data_key": scope_def.get("data_key", "accounts"),
                    "value_field": scope_def.get("value_field", "name"),
                    "name_field": scope_def.get("name_field", "name"),
                    "label_template": scope_def.get("label_template", "{name}"),
                    "format_js": (f"/plugin-web/{plugin_name}/{scope_def['format_js']}"
                                  if scope_def.get("format_js") else None),
                    "nav_target": scope_def.get("nav_target"),
                })

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
            "plugins_config": plugins_config,
            "scope_declarations": scope_declarations,
            "load_errors": _get_load_errors()
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
        # replace_messages instead of raw assignment: gains the streaming
        # guard (importing over an in-flight generation interleaved/corrupted)
        # and the full offset reset (a >cap chat kept its old head under raw
        # assignment — import wasn't actually a full replace).
        chat_name = session_manager.active_chat_name
        ok, err = session_manager.replace_messages(chat_name, messages)
        if not ok:
            raise HTTPException(status_code=409, detail=err)
        return {"status": "success", "message": f"Imported {len(messages)} messages"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# CHAT MANAGEMENT ROUTES
# =============================================================================

@router.get("/api/chats")
async def list_chats(request: Request, type: str = None, stats: int = 0,
                     _=Depends(require_login), system=Depends(get_system)):
    """List chats. stats=1 adds size_bytes per chat (Chat Manager view)."""
    try:
        chats = system.llm_chat.session_manager.list_chat_files(stats=bool(stats))
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


def _live_call_chats(system) -> set:
    """Chats currently owned by a live phone call — bulk/manage ops refuse them."""
    try:
        _mgr = getattr(system, "_conversation_manager", None)
        return _mgr.external_chats() if _mgr else set()
    except Exception:
        return set()


def _delete_one_chat(system, chat_name: str, origin=None):
    """Single-chat delete with the FULL cleanup chain — the one true path,
    shared by the single route and bulk-delete. Returns (ok, message).

    Surface isolation (2026-07-06): deleting a chat that hosts a LIVE phone
    call silently drops the call's brain override on its next turn — the
    caller's turns would land in the operator's active chat with the
    operator's persona/tools/scopes. Refuse until the call ends."""
    if chat_name in _live_call_chats(system):
        return False, f"'{chat_name}' has a live phone call — hang up before deleting."
    was_active = (chat_name == system.llm_chat.get_active_chat())
    if not system.llm_chat.delete_chat(chat_name):
        return False, f"Cannot delete '{chat_name}'"
    if was_active:
        settings = system.llm_chat.session_manager.get_chat_settings()
        _apply_chat_settings(system, settings)
    # Cleanup per-chat RAG documents
    try:
        from plugins.memory.tools import knowledge_tools as knowledge
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
    publish(Events.CHAT_DELETED, {"name": chat_name, "origin": origin})
    return True, f"Deleted: {chat_name}"


@router.delete("/api/chats/{chat_name}")
async def delete_chat(chat_name: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Delete a chat."""
    try:
        origin = request.headers.get('X-Session-ID')
        ok, message = _delete_one_chat(system, chat_name, origin)
        if not ok:
            code = 409 if "live phone call" in message else 400
            raise HTTPException(status_code=code, detail=message)
        return {"status": "success", "message": message}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to delete")


@router.post("/api/chats/bulk-delete")
async def bulk_delete_chats(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Delete many chats in one call. Per-chat results — a refused chat
    (live call, protected) doesn't stop the rest."""
    data = await request.json()
    names = data.get('names') if data else None
    if not names or not isinstance(names, list):
        raise HTTPException(status_code=400, detail="names list required")
    origin = request.headers.get('X-Session-ID')
    results = {}
    for name in names:
        ok, message = _delete_one_chat(system, str(name), origin)
        results[str(name)] = {"ok": ok, "message": message}
    deleted = sum(1 for r in results.values() if r["ok"])
    return {"status": "success", "deleted": deleted, "results": results}


@router.post("/api/chats/bulk-clear")
async def bulk_clear_chats(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Clear messages in many chats (chats survive, histories wiped)."""
    data = await request.json()
    names = data.get('names') if data else None
    if not names or not isinstance(names, list):
        raise HTTPException(status_code=400, detail="names list required")
    live = _live_call_chats(system)
    results = {}
    for name in names:
        name = str(name)
        if name in live:
            results[name] = {"ok": False, "message": "live phone call — hang up first"}
            continue
        ok = system.llm_chat.session_manager.clear_chat(name)
        results[name] = {"ok": ok, "message": "Cleared" if ok else "Not found or failed"}
    cleared = sum(1 for r in results.values() if r["ok"])
    return {"status": "success", "cleared": cleared, "results": results}


@router.get("/api/chats/{chat_name}/export")
async def export_single_chat(chat_name: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Full raw export of one chat: {name, settings, messages} — untrimmed."""
    data = system.llm_chat.session_manager.export_chat(chat_name)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Chat '{chat_name}' not found")
    return {"name": chat_name, **data}


@router.post("/api/chats/bulk-export")
async def bulk_export_chats(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Export many chats as one JSON document (safety valve before bulk ops).
    Each chat's messages array stays import-compatible with /api/history/import."""
    data = await request.json()
    names = data.get('names') if data else None
    if not names or not isinstance(names, list):
        raise HTTPException(status_code=400, detail="names list required")
    sm = system.llm_chat.session_manager
    chats, missing = {}, []
    for name in names:
        d = sm.export_chat(str(name))
        if d is None:
            missing.append(str(name))
        else:
            chats[str(name)] = d
    return {"chats": chats, "missing": missing}


@router.post("/api/chats/bulk-export-zip")
async def bulk_export_chats_zip(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Export many chats as a zip — one {name}.json per chat (each file
    import-compatible with /api/history/import). Names are create_chat-
    sanitized (alnum/_/-) so entry paths are traversal-safe by construction."""
    import io
    import zipfile
    data = await request.json()
    names = data.get('names') if data else None
    if not names or not isinstance(names, list):
        raise HTTPException(status_code=400, detail="names list required")
    sm = system.llm_chat.session_manager
    buf = io.BytesIO()
    exported = 0
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name in names:
            d = sm.export_chat(str(name))
            if d is None:
                continue
            zf.writestr(f"{name}.json", json.dumps({"name": str(name), **d}, indent=2))
            exported += 1
    if not exported:
        raise HTTPException(status_code=404, detail="No exportable chats in list")
    stamp = time.strftime("%Y-%m-%d_%H%M")
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="sapphire_chats_{stamp}.zip"'})


@router.post("/api/chats/{chat_name}/rename")
async def rename_chat(chat_name: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Rename a chat. Carries tool images, rows, RAG scope, and the
    active-chat pointer along. Refuses live-call chats and chats with
    attached agents (they key on the chat name)."""
    data = await request.json()
    new_name = (data or {}).get('new_name', '')
    if chat_name in _live_call_chats(system):
        raise HTTPException(status_code=409,
                            detail=f"'{chat_name}' has a live phone call — hang up before renaming.")
    try:
        if hasattr(system, 'agent_manager') and system.agent_manager:
            if system.agent_manager.check_all(chat_name=chat_name):
                raise HTTPException(status_code=409,
                                    detail="Chat has agents attached — dismiss them first (they key on the chat name).")
    except HTTPException:
        raise
    except Exception:
        pass
    ok, result = system.llm_chat.session_manager.rename_chat(chat_name, new_name)
    if not ok:
        raise HTTPException(status_code=400, detail=result)
    # Carry the per-chat RAG scope along
    try:
        from plugins.memory.tools import knowledge_tools as knowledge
        knowledge.rename_scope(f"__rag__:{chat_name}", f"__rag__:{result}")
    except Exception:
        pass
    origin = request.headers.get('X-Session-ID')
    publish(Events.CHAT_RENAMED, {"old": chat_name, "new": result, "origin": origin})
    return {"status": "success", "old": chat_name, "new": result}


@router.post("/api/chats/{chat_name}/archive")
async def archive_chat(chat_name: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Toggle a chat's archived flag. Archive is a UI shade, not a freeze:
    the chat vanishes from the sidebar dropdown but daemons/cron/heartbeat
    still reach it, and the Manager always shows it. Fully reversible."""
    data = await request.json()
    archived = bool((data or {}).get('archived', True))
    sm = system.llm_chat.session_manager
    if not sm.set_named_chat_settings(chat_name, {"archived": archived}, touch_updated=False):
        raise HTTPException(status_code=404, detail=f"Chat '{chat_name}' not found")
    origin = request.headers.get('X-Session-ID')
    publish(Events.CHAT_ARCHIVED, {"chat_name": chat_name, "archived": archived, "origin": origin})
    return {"status": "success", "chat": chat_name, "archived": archived}


@router.get("/api/chats/compress/status")
async def compress_status(_=Depends(require_login)):
    """Status of the one-at-a-time compress job (the UI polls this)."""
    from core.chat import compress
    return compress.get_job_status()


@router.post("/api/chats/{chat_name}/trim")
async def trim_chat(chat_name: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Turn-snapped middle trim: keep the first A and last B turns, delete
    the span between. preview:true computes the report without writing."""
    data = await request.json()
    data = data or {}
    try:
        keep_first = int(data.get('keep_first_turns', 0))
        keep_last = int(data.get('keep_last_turns', 1))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="keep_first_turns/keep_last_turns must be integers")
    preview = bool(data.get('preview', False))
    if keep_last < 1:
        raise HTTPException(status_code=400,
                            detail="keep_last_turns must be at least 1 — deleting the recent end is turn surgery, use the chat view.")
    if not preview and chat_name in _live_call_chats(system):
        raise HTTPException(status_code=409,
                            detail=f"'{chat_name}' has a live phone call — hang up before trimming.")
    ok, result = system.llm_chat.session_manager.trim_chat(
        chat_name, keep_first, keep_last, preview=preview)
    if not ok:
        raise HTTPException(status_code=404 if 'not found' in str(result) else 400, detail=result)
    if not preview and not result.get('no_op'):
        origin = request.headers.get('X-Session-ID')
        publish(Events.CHAT_TRIMMED, {"chat_name": chat_name, "report": result, "origin": origin})
    return {"status": "success", "preview": preview, **result}


@router.post("/api/chats/{chat_name}/compress")
async def compress_chat(chat_name: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Start the background compress job (whole = one summary pair,
    chunked = one pair per ~6k-token span). One job at a time — 409 if busy."""
    from core.chat import compress
    data = await request.json()
    data = data or {}
    mode = data.get('mode', 'whole')
    if mode not in ('whole', 'chunked'):
        raise HTTPException(status_code=400, detail="mode must be 'whole' or 'chunked'")
    provider_key = (data.get('provider') or '').strip()
    if not provider_key or provider_key in ('auto', 'none'):
        raise HTTPException(status_code=400, detail="A concrete provider is required")
    try:
        target_tokens = int(data.get('target_tokens', 5000))
        keep_last = int(data.get('keep_last_turns', 10))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="target_tokens/keep_last_turns must be integers")
    target_tokens = max(500, min(20000, target_tokens))
    if keep_last < 1:
        raise HTTPException(status_code=400, detail="keep_last_turns must be at least 1")
    # If the kept tail fills the LLM history window, the summary at position 0
    # is never sent to the model — compressed but invisible. LLM_MAX_HISTORY
    # counts single MESSAGES; get_messages_for_llm retains max_history // 2
    # user turns, so that halved value is the real ceiling for keep_last_turns.
    max_history = getattr(config, 'LLM_MAX_HISTORY', 30)
    max_turns = max_history // 2
    if max_turns and keep_last >= max_turns:
        raise HTTPException(status_code=400,
                            detail=f"keep_last_turns must be under {max_turns} (half the {max_history}-message LLM history window) or the summary never reaches the model.")
    if chat_name in _live_call_chats(system):
        raise HTTPException(status_code=409,
                            detail=f"'{chat_name}' has a live phone call — hang up before compressing.")
    sm = system.llm_chat.session_manager
    chat_settings = sm.read_chat_settings(chat_name)
    if chat_settings is None:
        raise HTTPException(status_code=404, detail=f"Chat '{chat_name}' not found")
    if chat_settings.get('private_chat'):
        # Private chat: the summarizing provider must be marked local/private-safe,
        # same rule as the chat's own turns.
        from core.chat.llm_providers import PROVIDER_METADATA
        providers_config = {**dict(getattr(config, "LLM_PROVIDERS", {})),
                            **dict(getattr(config, "LLM_CUSTOM_PROVIDERS", {}))}
        pconf = providers_config.get(provider_key, {})
        meta = PROVIDER_METADATA.get(provider_key, {})
        if not pconf.get('is_local', meta.get('is_local', False)):
            raise HTTPException(status_code=400,
                                detail=f"'{chat_name}' is a private chat — compression needs a provider marked local/private-safe.")
    ok, err = compress.start_compress_job(
        sm, chat_name, mode=mode, provider_key=provider_key,
        model=(data.get('model') or '').strip(), target_tokens=target_tokens,
        keep_last_turns=keep_last, backup=bool(data.get('backup', True)))
    if not ok:
        raise HTTPException(status_code=409, detail=err)
    return {"status": "started", "chat": chat_name, "mode": mode}


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


def _backfill_persona_visuals(chat_settings):
    """Backfill trim_color from the persona for pre-persona chats (migration crutch).

    Background is intentionally NOT backfilled. chat.background is the SINGLE source of
    truth: a scene name, or empty = no scene. Persona default scenes reach a chat by
    STAMPING at activation (load_persona), never by read-time inheritance — so an
    untouched chat shows no scene (privacy: no retroactive leak of a persona's scene)
    and an explicit 'None' sticks (empty isn't reinterpreted as 'inherit'). 2026-06-15.

    Returns a COPY when it changes anything (never mutates the live settings)."""
    if not isinstance(chat_settings, dict):
        return chat_settings
    if chat_settings.get('persona') and not chat_settings.get('trim_color'):
        try:
            from core.personas import persona_manager
            p = persona_manager.get(chat_settings['persona'])
            if p:
                out = dict(chat_settings)
                out['trim_color'] = p.get('settings', {}).get('trim_color', '')
                return out
        except Exception:
            pass
    return chat_settings


@router.get("/api/chats/{chat_name}/settings")
async def get_chat_settings(chat_name: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Get settings for a specific chat.

    Reads from SQLite (same storage used by every other chat operation).
    Legacy pre-SQLite code path checked a JSON file that no longer exists
    post-migration, causing every non-active chat to 404 here — which in
    turn made `tools/ask-sapphire.sh` and any other external caller
    silently fall back to default scopes + sapphire persona. Root cause of
    the silent-default class we found on 2026-04-19."""
    try:
        session_manager = system.llm_chat.session_manager
        if chat_name == session_manager.active_chat_name:
            return {"settings": _backfill_persona_visuals(session_manager.get_chat_settings())}

        settings = session_manager.read_chat_settings(chat_name)
        if settings is None:
            raise HTTPException(status_code=404, detail=f"Chat '{chat_name}' not found")
        return {"settings": _backfill_persona_visuals(settings)}
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

        if new_settings.get('private_chat'):
            from core.settings_manager import settings as sm_settings
            if sm_settings.is_managed():
                raise HTTPException(status_code=403, detail="Private chats are disabled in managed mode")

        if not session_manager.update_chat_settings(new_settings):
            raise HTTPException(status_code=500, detail="Failed to update settings")

        _apply_chat_settings(system, session_manager.get_chat_settings())

        origin = request.headers.get('X-Session-ID')
        publish(Events.CHAT_SETTINGS_CHANGED, {"chat": chat_name, "settings": new_settings, "origin": origin})

        # Return updated tool state so frontend can sync pills immediately
        fm = system.llm_chat.function_manager
        toolset_info = fm.get_current_toolset_info()
        function_names = fm.get_enabled_function_names()

        return {
            "status": "success",
            "message": f"Settings updated for '{chat_name}'",
            "toolset": toolset_info,
            "functions": list(function_names),
            "state_tools": [],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
