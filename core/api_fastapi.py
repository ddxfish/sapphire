# api_fastapi.py - Unified FastAPI API (replaces Flask api.py + web_interface.py)
import asyncio
import os
import io
import json
import time
import secrets
import tempfile
import logging
from pathlib import Path
from typing import Optional, Any
from datetime import timedelta

from fastapi import FastAPI, Request, Depends, HTTPException, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse, FileResponse
from starlette.middleware.sessions import SessionMiddleware

import config
from core.auth import (
    require_login, require_setup, check_rate_limit,
    generate_csrf_token, validate_csrf, get_client_ip
)
from core.setup import get_password_hash, save_password_hash, verify_password, is_setup_complete
from core.event_bus import publish, Events
from core.modules.system import prompts
from core.state_engine import STATE_TOOL_NAMES
from core.stt.stt_null import NullWhisperClient as _NullWhisperClient
from core.stt.utils import can_transcribe
from core.wakeword.wakeword_null import NullWakeWordDetector as _NullWakeWordDetector

logger = logging.getLogger(__name__)

# Cache-bust version — changes every server restart so browsers fetch fresh assets
BOOT_VERSION = str(int(time.time()))

# Project paths — defined early so _build_import_map() can use STATIC_DIR
PROJECT_ROOT = Path(__file__).parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "interfaces" / "web" / "templates"
STATIC_DIR = PROJECT_ROOT / "interfaces" / "web" / "static"
USER_PUBLIC_DIR = PROJECT_ROOT / "user" / "public"


def _build_import_map():
    """Build ES module import map — versions every JS file so browsers cache-bust on restart."""
    imports = {}
    for js_file in STATIC_DIR.rglob('*.js'):
        rel = js_file.relative_to(STATIC_DIR).as_posix()
        url = f"/static/{rel}"
        imports[url] = f"{url}?v={BOOT_VERSION}"
    return json.dumps({"imports": imports})


IMPORT_MAP = _build_import_map()

# =============================================================================
# APP SETUP
# =============================================================================

app = FastAPI(
    title="Sapphire",
    docs_url=None,  # Disable swagger UI
    redoc_url=None,  # Disable redoc
    openapi_url=None  # Disable openapi.json
)

# Session middleware added after HTTP middleware decorators below (outermost = LIFO)

# Static files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# User assets (avatars, etc)
if USER_PUBLIC_DIR.exists():
    app.mount("/user-assets", StaticFiles(directory=str(USER_PUBLIC_DIR)), name="user-assets")

# Templates
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# =============================================================================
# SYSTEM INSTANCE (dependency injection)
# =============================================================================

_system: Optional[Any] = None
_restart_callback: Optional[callable] = None
_shutdown_callback: Optional[callable] = None


def set_system(system, restart_callback=None, shutdown_callback=None):
    """Set the VoiceChatSystem instance for route handlers."""
    global _system, _restart_callback, _shutdown_callback
    _system = system
    _restart_callback = restart_callback
    _shutdown_callback = shutdown_callback
    logger.info("System instance registered with FastAPI")


def get_system():
    """Dependency to get system instance."""
    if _system is None:
        raise HTTPException(status_code=503, detail="System not initialized")
    return _system


# =============================================================================
# REQUEST LOGGING
# =============================================================================

@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log incoming requests."""
    if request.url.path.startswith('/static/'):
        logger.debug(f"REQ: {request.method} {request.url.path}")
    else:
        logger.info(f"REQ: {request.method} {request.url.path}")
    response = await call_next(request)
    if response.status_code >= 400 and not request.url.path.startswith('/static/'):
        logger.warning(f"RSP: {response.status_code} {request.method} {request.url.path}")
    return response


# =============================================================================
# SECURITY HEADERS
# =============================================================================

@app.middleware("http")
async def csrf_protection(request: Request, call_next):
    """Validate CSRF token on state-changing requests from browser sessions."""
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        # API key auth (internal/tool calls) — skip CSRF
        if not request.headers.get('X-API-Key'):
            # Form-based endpoints handle their own CSRF
            if request.url.path not in ("/login", "/setup"):
                if request.session.get('logged_in'):
                    csrf_header = request.headers.get('X-CSRF-Token')
                    session_token = request.session.get('csrf_token')
                    if not csrf_header or not session_token or csrf_header != session_token:
                        from starlette.responses import JSONResponse
                        return JSONResponse(status_code=403, content={"detail": "CSRF validation failed"})
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Add security headers to all responses."""
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'

    # Static assets: cached 1hr, busted by ?v=BOOT_VERSION (changes every restart)
    # Import map in index.html ensures ALL JS modules get versioned URLs
    if request.url.path.startswith('/static/'):
        response.headers['Cache-Control'] = 'public, max-age=3600'
    elif 'cache-control' not in response.headers:
        # API responses must never be cached — prevents stale fetch() after hard refresh
        # (Ctrl+Shift+R only bypasses cache for HTML, not JS fetch() calls)
        response.headers['Cache-Control'] = 'no-store'

    response.headers['Connection'] = 'keep-alive'
    return response


# Session middleware - added AFTER HTTP middleware so it's outermost (Starlette LIFO)
_password_hash = get_password_hash()
app.add_middleware(
    SessionMiddleware,
    secret_key=_password_hash if _password_hash else secrets.token_hex(32),
    session_cookie="sapphire_session",
    max_age=30 * 24 * 60 * 60,  # 30 days
    same_site="lax",
    https_only=getattr(config, 'WEB_UI_SSL_ADHOC', False)
)


# =============================================================================
# PAGE ROUTES (HTML)
# =============================================================================

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(STATIC_DIR / "favicon.ico", media_type="image/x-icon")


def _no_cache_html(template: str, context: dict):
    """TemplateResponse with aggressive no-cache headers (bypass middleware issues)."""
    resp = templates.TemplateResponse(template, context)
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


@app.get("/")
async def index(request: Request, _=Depends(require_login)):
    """Main chat page."""
    csrf_token = generate_csrf_token(request)
    return _no_cache_html("index.html", {
        "request": request,
        "csrf_token": lambda: csrf_token,
        "v": BOOT_VERSION,
        "import_map": IMPORT_MAP
    })


@app.get("/setup")
async def setup_page(request: Request):
    """Initial password setup page."""
    if is_setup_complete():
        return RedirectResponse(url="/login", status_code=302)
    csrf_token = generate_csrf_token(request)
    return _no_cache_html("setup.html", {
        "request": request,
        "csrf_token": lambda: csrf_token
    })


@app.post("/setup")
async def setup_submit(request: Request):
    """Handle password setup form."""
    if is_setup_complete():
        return RedirectResponse(url="/login", status_code=302)

    # Rate limit
    client_ip = get_client_ip(request)
    if check_rate_limit(client_ip):
        return RedirectResponse(url="/setup?error=rate", status_code=302)

    form = await request.form()
    password = form.get('password', '')
    confirm = form.get('confirm', '')

    if not password:
        return RedirectResponse(url="/setup?error=empty", status_code=302)
    if len(password) < 6:
        return RedirectResponse(url="/setup?error=short", status_code=302)
    if password != confirm:
        return RedirectResponse(url="/setup?error=mismatch", status_code=302)

    if save_password_hash(password):
        logger.info("Password setup complete")
        return RedirectResponse(url="/login", status_code=302)
    else:
        logger.error("Failed to save password hash")
        return RedirectResponse(url="/setup?error=failed", status_code=302)


@app.get("/login")
async def login_page(request: Request, _=Depends(require_setup)):
    """Login page."""
    if request.session.get('logged_in'):
        return RedirectResponse(url="/", status_code=302)
    csrf_token = generate_csrf_token(request)
    return _no_cache_html("login.html", {
        "request": request,
        "csrf_token": lambda: csrf_token
    })


@app.post("/login")
async def login_submit(request: Request):
    """Handle login form."""
    if not is_setup_complete():
        return RedirectResponse(url="/setup", status_code=302)

    # Rate limit
    client_ip = get_client_ip(request)
    if check_rate_limit(client_ip):
        return RedirectResponse(url="/login?error=rate", status_code=302)

    form = await request.form()

    # CSRF check
    csrf_token = form.get('csrf_token')
    if not validate_csrf(request, csrf_token):
        logger.warning(f"CSRF validation failed from {client_ip}")
        return RedirectResponse(url="/login?error=csrf", status_code=302)

    password = form.get('password', '')
    password_hash = get_password_hash()

    if not password_hash:
        logger.error("No password hash configured")
        return RedirectResponse(url="/login?error=config", status_code=302)

    if verify_password(password, password_hash):
        request.session['logged_in'] = True
        request.session['username'] = getattr(config, 'AUTH_USERNAME', 'user')
        logger.info(f"Successful login from {client_ip}")
        return RedirectResponse(url="/", status_code=302)
    else:
        logger.warning(f"Failed login attempt from {client_ip}")
        return RedirectResponse(url="/login?error=invalid", status_code=302)


@app.post("/logout")
async def logout(request: Request, _=Depends(require_login)):
    """Logout endpoint."""
    username = request.session.get('username', 'unknown')
    request.session.clear()
    logger.info(f"Logout for {username}")
    return JSONResponse({"status": "success"})


# =============================================================================
# HELPER FUNCTIONS (from api.py)
# =============================================================================

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


def _apply_chat_settings(system, settings: dict):
    """Apply chat settings to the system (TTS, prompt, ability, state engine)."""
    try:
        if "voice" in settings:
            system.tts.set_voice(settings["voice"])
        if "pitch" in settings:
            system.tts.set_pitch(settings["pitch"])
        if "speed" in settings:
            system.tts.set_speed(settings["speed"])

        if "prompt" in settings:
            prompt_name = settings["prompt"]
            prompt_data = prompts.get_prompt(prompt_name)
            content = prompt_data.get('content') if isinstance(prompt_data, dict) else str(prompt_data)
            if content:
                system.llm_chat.set_system_prompt(content)
                prompts.set_active_preset_name(prompt_name)

                if hasattr(prompts.prompt_manager, 'scenario_presets') and prompt_name in prompts.prompt_manager.scenario_presets:
                    prompts.apply_scenario(prompt_name)

                logger.info(f"Applied prompt: {prompt_name}")

        if "memory_scope" in settings:
            scope = settings["memory_scope"]
            system.llm_chat.function_manager.set_memory_scope(scope if scope != "none" else None)
        if "goal_scope" in settings:
            scope = settings["goal_scope"]
            system.llm_chat.function_manager.set_goal_scope(scope if scope != "none" else None)
        if "knowledge_scope" in settings:
            scope = settings["knowledge_scope"]
            system.llm_chat.function_manager.set_knowledge_scope(scope if scope != "none" else None)
        if "people_scope" in settings:
            scope = settings["people_scope"]
            system.llm_chat.function_manager.set_people_scope(scope if scope != "none" else None)

        if "spice_set" in settings:
            from core.modules.system.spice_sets import spice_set_manager
            set_name = settings["spice_set"]
            if spice_set_manager.set_exists(set_name):
                categories = spice_set_manager.get_categories(set_name)
                all_cats = set(prompts.prompt_manager.spices.keys())
                prompts.prompt_manager._disabled_categories = all_cats - set(categories)
                prompts.prompt_manager.save_spices()
                prompts.invalidate_spice_picks()
                spice_set_manager.active_name = set_name
                logger.info(f"Applied spice set: {set_name}")

        toolset_key = "toolset" if "toolset" in settings else "ability" if "ability" in settings else None
        if toolset_key:
            toolset_name = settings[toolset_key]
            system.llm_chat.function_manager.update_enabled_functions([toolset_name])
            logger.info(f"Applied toolset: {toolset_name}")
            publish(Events.TOOLSET_CHANGED, {"name": toolset_name})

        system.llm_chat._update_state_engine()

        if settings.get('state_engine_enabled') is not None:
            toolset_info = system.llm_chat.function_manager.get_current_toolset_info()
            publish(Events.TOOLSET_CHANGED, {
                "name": toolset_info.get("name", "custom"),
                "action": "state_engine_update",
                "function_count": toolset_info.get("function_count", 0)
            })

    except Exception as e:
        logger.error(f"Error applying chat settings: {e}", exc_info=True)


# =============================================================================
# CORE API ROUTES
# =============================================================================

@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}


@app.get("/api/history")
async def get_history(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Get history formatted for UI display with context usage info."""
    from core.chat.history import count_tokens, count_message_tokens

    raw_messages = system.llm_chat.session_manager.get_messages_for_display()
    display_messages = format_messages_for_display(raw_messages)

    context_limit = getattr(config, 'CONTEXT_LIMIT', 32000)
    history_tokens = sum(count_message_tokens(m.get("content", ""), include_images=False) for m in raw_messages)

    try:
        prompt_content = system.llm_chat.current_system_prompt or ""
        prompt_tokens = count_tokens(prompt_content) if prompt_content else 0
    except Exception:
        prompt_tokens = 0

    total_used = history_tokens + prompt_tokens
    percent = min(100, int((total_used / context_limit) * 100)) if context_limit > 0 else 0

    return {
        "messages": display_messages,
        "context": {
            "used": total_used,
            "limit": context_limit,
            "percent": percent
        }
    }


@app.post("/api/chat")
async def handle_chat(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Non-streaming chat endpoint."""
    data = await request.json()
    if not data or 'text' not in data:
        raise HTTPException(status_code=400, detail="No text provided")

    response = await asyncio.to_thread(system.process_llm_query, data['text'], True)
    return {"response": response}


@app.post("/api/chat/stream")
async def handle_chat_stream(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Streaming chat endpoint (SSE)."""
    data = await request.json()
    if not data or 'text' not in data:
        raise HTTPException(status_code=400, detail="No text provided")

    logger.info(f"[CHAT-STREAM] Request received at {time.time():.3f}")

    prefill = data.get('prefill')
    skip_user_message = data.get('skip_user_message', False)
    images = data.get('images', [])
    files = data.get('files', [])

    system.llm_chat.streaming_chat.cancel_flag = False

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

    return StreamingResponse(
        generate(),
        media_type='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no'
        }
    )


@app.post("/api/cancel")
async def handle_cancel(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Cancel ongoing streaming generation."""
    try:
        system.llm_chat.streaming_chat.cancel_flag = True
        logger.info("CANCEL: Flag set")
        return {"status": "success", "message": "Cancellation requested"}
    except Exception as e:
        logger.error(f"Error during cancellation: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/events")
async def event_stream(request: Request, replay: str = 'false', _=Depends(require_login)):
    """SSE endpoint for real-time event streaming."""
    from core.event_bus import get_event_bus

    do_replay = replay.lower() == 'true'

    def generate():
        bus = get_event_bus()
        for event in bus.subscribe(replay=do_replay):
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


@app.get("/api/status")
async def get_unified_status(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Unified status endpoint - single call for all UI state needs."""
    try:
        from core.chat.history import count_tokens, count_message_tokens

        chat_settings = system.llm_chat.session_manager.get_chat_settings()

        # Backfill trim_color from persona if missing (pre-persona chats)
        if not chat_settings.get('trim_color') and chat_settings.get('persona'):
            try:
                from core.modules.system.personas import persona_manager
                p = persona_manager.get(chat_settings['persona'])
                if p:
                    chat_settings['trim_color'] = p.get('settings', {}).get('trim_color', '')
            except Exception:
                pass

        if chat_settings.get('state_engine_enabled') and not system.llm_chat.function_manager.get_state_engine():
            system.llm_chat._update_state_engine()

        prompt_state = prompts.get_current_state()
        prompt_name = prompts.get_active_preset_name()
        prompt_char_count = prompts.get_prompt_char_count()
        prompt_privacy_required = prompts.is_current_prompt_private()
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
        history_tokens = sum(count_message_tokens(m.get("content", ""), include_images=False) for m in raw_messages)

        try:
            prompt_content = system.llm_chat.current_system_prompt or ""
            prompt_tokens = count_tokens(prompt_content) if prompt_content else 0
        except Exception:
            prompt_tokens = 0

        total_used = history_tokens + prompt_tokens
        context_percent = min(100, int((total_used / context_limit) * 100)) if context_limit > 0 else 0

        story_status = None
        try:
            if chat_settings.get('state_engine_enabled', False):
                story_status = {
                    "enabled": True,
                    "preset": chat_settings.get('state_preset', ''),
                    "preset_display": chat_settings.get('state_preset', '').replace('_', ' ').title() if chat_settings.get('state_preset') else ''
                }
                live_engine = system.llm_chat.function_manager.get_state_engine()
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

        state_tools = [f for f in function_names if f in STATE_TOOL_NAMES]
        user_tools = [f for f in function_names if f not in STATE_TOOL_NAMES]

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
            "stt_enabled": config.STT_ENABLED,
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


@app.get("/api/init")
async def get_init_data(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Mega-endpoint for initial page load - combines all plugin init data."""
    try:
        import glob as glob_mod
        from core.modules.system.toolsets import toolset_manager

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
        from core.modules.system.spice_sets import spice_set_manager
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
        from core.modules.system.personas import persona_manager
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
                "DEFAULT_USERNAME": getattr(config, 'DEFAULT_USERNAME', 'Human Protagonist')
            },
            "wizard_step": wizard_step,
            "avatars": avatars,
            "plugins_config": plugins_config
        }
    except Exception as e:
        logger.error(f"Error getting init data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/modules")
async def get_modules(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Get list of loaded modules."""
    return system.llm_chat.module_loader.get_module_list()


# =============================================================================
# HISTORY MANAGEMENT ROUTES
# =============================================================================

@app.delete("/api/history/messages")
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
            if chat_settings.get('state_engine_enabled', False):
                from pathlib import Path
                from core.state_engine import StateEngine
                db_path = Path("user/history/sapphire_history.db")
                if db_path.exists() and chat_name:
                    engine = StateEngine(chat_name, db_path)
                    preset = chat_settings.get('state_preset')
                    if preset:
                        engine.load_preset(preset, 1)
                    else:
                        engine.clear_all()

                    live_engine = system.llm_chat.function_manager.get_state_engine()
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


@app.post("/api/history/messages/remove-last-assistant")
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


@app.post("/api/history/messages/remove-from-assistant")
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


@app.delete("/api/history/tool-call/{tool_call_id}")
async def remove_tool_call(tool_call_id: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Remove a specific tool call and its result."""
    try:
        if system.llm_chat.session_manager.remove_tool_call(tool_call_id):
            return {"status": "success", "message": "Tool call removed"}
        else:
            raise HTTPException(status_code=404, detail="Tool call not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/history/messages/edit")
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


@app.get("/api/history/raw")
async def get_raw_history(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Get raw history structure."""
    return system.llm_chat.session_manager.get_messages()


@app.post("/api/history/import")
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

@app.get("/api/chats")
async def list_chats(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """List all chats."""
    try:
        chats = system.llm_chat.list_chats()
        active_chat = system.llm_chat.get_active_chat()
        return {"chats": chats, "active_chat": active_chat}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to list chats")


@app.post("/api/chats")
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


@app.delete("/api/chats/{chat_name}")
async def delete_chat(chat_name: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Delete a chat."""
    try:
        was_active = (chat_name == system.llm_chat.get_active_chat())
        if system.llm_chat.delete_chat(chat_name):
            if was_active:
                settings = system.llm_chat.session_manager.get_chat_settings()
                _apply_chat_settings(system, settings)
            return {"status": "success", "message": f"Deleted: {chat_name}"}
        else:
            raise HTTPException(status_code=400, detail=f"Cannot delete '{chat_name}'")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to delete")


@app.post("/api/chats/{chat_name}/activate")
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


@app.get("/api/chats/active")
async def get_active_chat(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Get active chat name."""
    return {"active_chat": system.llm_chat.get_active_chat()}


@app.get("/api/chats/{chat_name}/settings")
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


@app.put("/api/chats/{chat_name}/settings")
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

        return {"status": "success", "message": f"Settings updated for '{chat_name}'"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# TTS ROUTES
# =============================================================================

@app.post("/api/tts")
async def handle_tts_speak(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """TTS speak endpoint."""
    data = await request.json()
    text = data.get('text')
    output_mode = data.get('output_mode', 'play')

    if not text:
        raise HTTPException(status_code=400, detail="No text provided")

    if not config.TTS_ENABLED:
        return {"status": "success", "message": "TTS disabled"}

    if output_mode == 'play':
        system.tts.speak(text)
        return {"status": "success", "message": "Playback started."}
    elif output_mode == 'file':
        audio_data = await asyncio.to_thread(system.tts.generate_audio_data, text)
        if audio_data:
            return StreamingResponse(
                io.BytesIO(audio_data),
                media_type='audio/ogg',
                headers={'Content-Disposition': 'attachment; filename="output.ogg"'}
            )
        else:
            raise HTTPException(status_code=503, detail="TTS generation failed")
    else:
        raise HTTPException(status_code=400, detail="Invalid output_mode")


@app.post("/api/tts/preview")
async def tts_preview(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Generate TTS audio with custom voice/pitch/speed without changing system state."""
    data = await request.json()
    text = data.get('text', 'Hello!')
    voice = data.get('voice')
    pitch = data.get('pitch')
    speed = data.get('speed')

    if not config.TTS_ENABLED:
        raise HTTPException(status_code=503, detail="TTS disabled")

    # Save current settings
    orig_voice = system.tts.voice_name
    orig_pitch = system.tts.pitch_shift
    orig_speed = system.tts.speed

    try:
        if voice: system.tts.set_voice(voice)
        if pitch is not None: system.tts.set_pitch(pitch)
        if speed is not None: system.tts.set_speed(speed)
        audio_data = await asyncio.to_thread(system.tts.generate_audio_data, text)
    finally:
        # Restore original settings
        system.tts.set_voice(orig_voice)
        system.tts.set_pitch(orig_pitch)
        system.tts.set_speed(orig_speed)

    if not audio_data:
        raise HTTPException(status_code=503, detail="TTS generation failed")

    return StreamingResponse(
        io.BytesIO(audio_data),
        media_type='audio/ogg',
        headers={'Content-Disposition': 'inline; filename="preview.ogg"'}
    )


@app.get("/api/tts/status")
async def tts_status(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Get TTS playback status."""
    playing = getattr(system.tts, '_is_playing', False)
    return {"playing": playing}


@app.post("/api/tts/stop")
async def tts_stop(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Stop TTS playback."""
    system.tts.stop()
    return {"status": "success"}


# =============================================================================
# TRANSCRIBE / UPLOAD ROUTES
# =============================================================================

@app.post("/api/transcribe")
async def handle_transcribe(audio: UploadFile = File(...), _=Depends(require_login), system=Depends(get_system)):
    """Transcribe audio to text."""
    ok, reason = can_transcribe(system.whisper_client)
    if not ok:
        raise HTTPException(status_code=400, detail=reason)

    fd, temp_path = tempfile.mkstemp(suffix=".wav")
    try:
        os.close(fd)
        contents = await audio.read()
        with open(temp_path, 'wb') as f:
            f.write(contents)
        try:
            transcribed_text = await asyncio.wait_for(
                asyncio.to_thread(system.whisper_client.transcribe_file, temp_path),
                timeout=90.0
            )
        except asyncio.TimeoutError:
            logger.warning("Transcription timed out (90s) — model may be too slow on CPU")
            raise HTTPException(status_code=504, detail="Transcription timed out — try a smaller model or lower beam size in STT settings")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Transcription error: {e}")
        raise HTTPException(status_code=500, detail="Failed to process audio")
    finally:
        try:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
        except Exception:
            pass
    return {"text": transcribed_text}


@app.post("/api/upload/image")
async def handle_image_upload(image: UploadFile = File(...), _=Depends(require_login), system=Depends(get_system)):
    """Upload an image for chat."""
    import base64
    from io import BytesIO
    from core.settings_manager import settings

    allowed_ext = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
    ext = os.path.splitext(image.filename or '')[1].lower()
    if ext not in allowed_ext:
        raise HTTPException(status_code=400, detail=f"Invalid file type. Allowed: {', '.join(allowed_ext)}")

    contents = await image.read()
    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large. Max 10MB")

    media_types = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.webp': 'image/webp'}
    media_type = media_types.get(ext, 'image/jpeg')

    # Optional optimization
    max_width = settings.get('IMAGE_UPLOAD_MAX_WIDTH', 0)
    if max_width > 0:
        try:
            from PIL import Image
            img = Image.open(BytesIO(contents))
            if img.mode in ('RGBA', 'P'):
                img = img.convert('RGB')
            elif img.mode != 'RGB':
                img = img.convert('RGB')
            if img.width > max_width:
                ratio = max_width / img.width
                new_height = int(img.height * ratio)
                img = img.resize((max_width, new_height), Image.LANCZOS)
            buffer = BytesIO()
            img.save(buffer, format='JPEG', quality=85, optimize=True)
            optimized = buffer.getvalue()
            if len(optimized) < len(contents):
                contents = optimized
                media_type = 'image/jpeg'
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"Image optimization failed: {e}")

    base64_data = base64.b64encode(contents).decode('utf-8')
    return {"status": "success", "data": base64_data, "media_type": media_type, "filename": image.filename, "size": len(contents)}


# =============================================================================
# SYSTEM STATUS ROUTES
# =============================================================================

@app.get("/api/system/status")
async def get_system_status(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Get system status."""
    try:
        prompt_state = prompts.get_current_state()
        function_names = system.llm_chat.function_manager.get_enabled_function_names()
        toolset_info = system.llm_chat.function_manager.get_current_toolset_info()
        has_cloud_tools = system.llm_chat.function_manager.has_network_tools_enabled()

        chat_settings = system.llm_chat.session_manager.get_chat_settings()
        spice_enabled = chat_settings.get('spice_enabled', True)
        current_spice = prompts.get_current_spice()
        next_spice = prompts.get_next_spice()
        is_assembled = prompts.is_assembled_mode()

        return {
            "prompt": prompt_state,
            "prompt_name": prompts.get_active_preset_name(),
            "prompt_char_count": prompts.get_prompt_char_count(),
            "functions": function_names,
            "toolset": toolset_info,
            "tts_enabled": config.TTS_ENABLED,
            "has_cloud_tools": has_cloud_tools,
            "spice": {"current": current_spice, "next": next_spice, "enabled": spice_enabled, "available": is_assembled}
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to get system status")


@app.get("/api/system/prompt")
async def get_system_prompt(request: Request, prompt_name: str = None, _=Depends(require_login), system=Depends(get_system)):
    """Get system prompt."""
    if prompt_name:
        prompt_data = prompts.get_prompt(prompt_name)
        if not prompt_data:
            raise HTTPException(status_code=404, detail=f"Prompt '{prompt_name}' not found.")
        content = prompt_data.get('content') if isinstance(prompt_data, dict) else str(prompt_data)
        return {"prompt": content, "source": f"storage: {prompt_name}"}
    else:
        prompt_template = system.llm_chat.get_system_prompt_template()
        return {"prompt": prompt_template, "source": "active_memory_template"}


@app.post("/api/system/prompt")
async def set_system_prompt(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Set system prompt."""
    data = await request.json()
    new_prompt = data.get('new_prompt')
    if not new_prompt:
        raise HTTPException(status_code=400, detail="A 'new_prompt' key must be provided")
    success = system.llm_chat.set_system_prompt(new_prompt)
    if success:
        return {"status": "success", "message": "System prompt updated."}
    else:
        raise HTTPException(status_code=500, detail="Error setting prompt")


# =============================================================================
# SETTINGS ROUTES (from settings_api.py)
# =============================================================================

@app.get("/api/settings")
async def get_all_settings(request: Request, _=Depends(require_login)):
    """Get all current settings."""
    from core.settings_manager import settings
    try:
        all_settings = settings.get_all_settings()
        user_overrides = settings.get_user_overrides()
        return {"settings": all_settings, "user_overrides": list(user_overrides.keys()), "count": len(all_settings)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/settings/reload")
async def reload_settings(request: Request, _=Depends(require_login)):
    """Reload settings from disk."""
    from core.settings_manager import settings
    settings.reload()
    return {"status": "success", "message": "Settings reloaded"}


@app.post("/api/settings/reset")
async def reset_settings(request: Request, _=Depends(require_login)):
    """Reset all settings to defaults."""
    from core.settings_manager import settings
    if settings.reset_to_defaults():
        return {"status": "success", "message": "All settings reset to defaults"}
    else:
        raise HTTPException(status_code=500, detail="Failed to reset settings")


@app.get("/api/settings/tiers")
async def get_tiers(request: Request, _=Depends(require_login)):
    """Get tier classification for all settings."""
    from core.settings_manager import settings
    all_settings = settings.get_all_settings()
    tiers = {'hot': [], 'component': [], 'restart': []}
    for key in all_settings.keys():
        tier = settings.validate_tier(key)
        tiers[tier].append(key)
    return {"tiers": tiers, "counts": {k: len(v) for k, v in tiers.items()}}


@app.put("/api/settings/batch")
async def update_settings_batch(request: Request, _=Depends(require_login)):
    """Update multiple settings at once."""
    from core.settings_manager import settings
    data = await request.json()
    if not data or 'settings' not in data:
        raise HTTPException(status_code=400, detail="Missing 'settings'")
    settings_dict = data['settings']
    persist = data.get('persist', True)
    results = []
    for key, value in settings_dict.items():
        try:
            tier = settings.validate_tier(key)
            settings.set(key, value, persist=persist)
            results.append({"key": key, "status": "success", "tier": tier})
            if key == 'WAKE_WORD_ENABLED':
                get_system().toggle_wakeword(value)
            if key == 'STT_ENABLED':
                await asyncio.to_thread(get_system().toggle_stt, value)
            if key == 'TTS_ENABLED':
                await asyncio.to_thread(get_system().toggle_tts, value)
            publish(Events.SETTINGS_CHANGED, {"key": key, "value": value, "tier": tier})
        except Exception as e:
            results.append({"key": key, "status": "error", "error": str(e)})
    return {"status": "success", "results": results}


@app.get("/api/settings/help")
async def get_settings_help(request: Request, _=Depends(require_login)):
    """Get help text for settings."""
    help_path = Path(__file__).parent / "settings_help.json"
    try:
        with open(help_path) as f:
            return {"help": json.load(f)}
    except Exception:
        return {"help": {}}


@app.get("/api/settings/help/{key}")
async def get_setting_help(key: str, request: Request, _=Depends(require_login)):
    """Get help for a specific setting."""
    help_path = Path(__file__).parent / "settings_help.json"
    try:
        with open(help_path) as f:
            all_help = json.load(f)
    except Exception:
        raise HTTPException(status_code=500, detail="Could not load help data")
    help_text = all_help.get(key)
    if not help_text:
        raise HTTPException(status_code=404, detail=f"No help for '{key}'")
    return {"key": key, "help": help_text}


@app.get("/api/settings/chat-defaults")
async def get_chat_defaults(request: Request, _=Depends(require_login)):
    """Get chat defaults."""
    defaults_path = PROJECT_ROOT / "user" / "settings" / "chat_defaults.json"
    if defaults_path.exists():
        with open(defaults_path, 'r') as f:
            return json.load(f)
    return {}


@app.put("/api/settings/chat-defaults")
async def save_chat_defaults(request: Request, _=Depends(require_login)):
    """Save chat defaults."""
    data = await request.json()
    defaults_path = PROJECT_ROOT / "user" / "settings" / "chat_defaults.json"
    defaults_path.parent.mkdir(parents=True, exist_ok=True)
    with open(defaults_path, 'w') as f:
        json.dump(data, f, indent=2)
    return {"status": "success"}


@app.delete("/api/settings/chat-defaults")
async def reset_chat_defaults(request: Request, _=Depends(require_login)):
    """Reset chat defaults."""
    defaults_path = PROJECT_ROOT / "user" / "settings" / "chat_defaults.json"
    if defaults_path.exists():
        defaults_path.unlink()
    return {"status": "success"}


@app.get("/api/settings/wakeword-models")
async def get_wakeword_models(request: Request, _=Depends(require_login)):
    """Get available wakeword models."""
    models = set()
    for models_dir in [PROJECT_ROOT / "core" / "wakeword" / "models", PROJECT_ROOT / "user" / "wakeword_models"]:
        if models_dir.exists():
            for model_file in models_dir.glob("*.onnx"):
                models.add(model_file.stem)
    return {"all": sorted(models)}


# Parameterized settings routes MUST come after specific ones (FastAPI matches in registration order)
@app.get("/api/settings/{key}")
async def get_setting(key: str, request: Request, _=Depends(require_login)):
    """Get a specific setting."""
    from core.settings_manager import settings
    value = settings.get(key)
    if value is None:
        raise HTTPException(status_code=404, detail=f"Setting '{key}' not found")
    tier = settings.validate_tier(key)
    is_user_override = key in settings.get_user_overrides()
    return {"key": key, "value": value, "tier": tier, "user_override": is_user_override}


@app.put("/api/settings/{key}")
async def update_setting(key: str, request: Request, _=Depends(require_login)):
    """Update a setting."""
    from core.settings_manager import settings
    from core.socks_proxy import clear_session_cache
    data = await request.json()
    if data is None or 'value' not in data:
        raise HTTPException(status_code=400, detail="Missing 'value'")
    value = data['value']
    persist = data.get('persist', True)
    tier = settings.validate_tier(key)
    settings.set(key, value, persist=persist)
    if key in {'SOCKS_ENABLED', 'SOCKS_HOST', 'SOCKS_PORT', 'SOCKS_TIMEOUT'}:
        clear_session_cache()
    if key == 'WAKE_WORD_ENABLED':
        system = get_system()
        system.toggle_wakeword(value)
    if key == 'STT_ENABLED':
        await asyncio.to_thread(get_system().toggle_stt, value)
    if key == 'TTS_ENABLED':
        await asyncio.to_thread(get_system().toggle_tts, value)
    publish(Events.SETTINGS_CHANGED, {"key": key, "value": value, "tier": tier})
    return {"status": "success", "key": key, "value": value, "tier": tier, "persisted": persist}


@app.delete("/api/settings/{key}")
async def delete_setting(key: str, request: Request, _=Depends(require_login)):
    """Remove user override for a setting."""
    from core.settings_manager import settings
    if settings.remove_user_override(key):
        default_value = settings.get(key)
        return {"status": "success", "key": key, "reverted_to": default_value}
    else:
        raise HTTPException(status_code=404, detail=f"No user override exists for '{key}'")


# =============================================================================
# CREDENTIALS ROUTES
# =============================================================================

@app.get("/api/credentials")
async def get_credentials(request: Request, _=Depends(require_login)):
    """Get credentials status (not actual values)."""
    from core.credentials_manager import credentials
    return credentials.get_masked_summary()


@app.put("/api/credentials/llm/{provider}")
async def set_llm_credential(provider: str, request: Request, _=Depends(require_login)):
    """Set LLM API key for a provider."""
    from core.credentials_manager import credentials
    data = await request.json()
    api_key = data.get('api_key', '')
    if credentials.set_llm_api_key(provider, api_key):
        return {"status": "success", "provider": provider}
    else:
        raise HTTPException(status_code=500, detail="Failed to save credential")


@app.delete("/api/credentials/llm/{provider}")
async def delete_llm_credential(provider: str, request: Request, _=Depends(require_login)):
    """Delete LLM API key for a provider."""
    from core.credentials_manager import credentials
    if credentials.clear_llm_api_key(provider):
        return {"status": "success", "provider": provider}
    else:
        raise HTTPException(status_code=404, detail="Credential not found")


@app.get("/api/credentials/socks")
async def get_socks_credential(request: Request, _=Depends(require_login)):
    """Get SOCKS credentials (masked)."""
    from core.credentials_manager import credentials
    return {"has_credentials": credentials.has_socks_credentials()}


@app.put("/api/credentials/socks")
async def set_socks_credential(request: Request, _=Depends(require_login)):
    """Set SOCKS credentials."""
    from core.credentials_manager import credentials
    data = await request.json()
    username = data.get('username', '')
    password = data.get('password', '')
    if credentials.set_socks_credentials(username, password):
        return {"status": "success"}
    else:
        raise HTTPException(status_code=500, detail="Failed to save credentials")


@app.delete("/api/credentials/socks")
async def delete_socks_credential(request: Request, _=Depends(require_login)):
    """Delete SOCKS credentials."""
    from core.credentials_manager import credentials
    if credentials.delete_socks_credentials():
        return {"status": "success"}
    else:
        raise HTTPException(status_code=500, detail="Failed to delete credentials")


@app.post("/api/credentials/socks/test")
async def test_socks_connection(request: Request, _=Depends(require_login)):
    """Test SOCKS proxy connection."""
    if not config.SOCKS_ENABLED:
        return {"status": "error", "error": "SOCKS proxy is disabled"}

    def _test_socks():
        from core.socks_proxy import get_session, SocksAuthError, clear_session_cache
        import requests as req
        clear_session_cache()
        try:
            session = get_session()
            resp = session.get('https://icanhazip.com', timeout=8)
            if resp.ok:
                return {"status": "success", "message": f"Connected via {resp.text.strip()}"}
            return {"status": "error", "error": f"HTTP {resp.status_code}"}
        except SocksAuthError as e:
            return {"status": "error", "error": str(e)}
        except req.exceptions.Timeout:
            return {"status": "error", "error": "Connection timed out"}
        except Exception as e:
            return {"status": "error", "error": f"{type(e).__name__}: {e}"}

    return await asyncio.to_thread(_test_socks)


# =============================================================================
# PRIVACY ROUTES
# =============================================================================

@app.get("/api/privacy")
async def get_privacy_status(request: Request, _=Depends(require_login)):
    """Get privacy mode status."""
    from core.settings_manager import settings
    return {
        "privacy_mode": settings.get('PRIVACY_MODE', False),
        "start_in_privacy": settings.get('START_IN_PRIVACY_MODE', False)
    }


@app.put("/api/privacy")
async def set_privacy_status(request: Request, _=Depends(require_login)):
    """Set privacy mode."""
    from core.settings_manager import settings
    data = await request.json()
    enabled = data.get('enabled', False)
    settings.set('PRIVACY_MODE', enabled, persist=False)
    publish(Events.SETTINGS_CHANGED, {"key": "PRIVACY_MODE", "value": enabled})
    label = "Privacy mode enabled" if enabled else "Privacy mode disabled"
    return {"privacy_mode": enabled, "message": label}


@app.put("/api/privacy/start-mode")
async def set_start_in_privacy(request: Request, _=Depends(require_login)):
    """Set start in privacy mode."""
    from core.settings_manager import settings
    data = await request.json()
    enabled = data.get('enabled', False)
    settings.set('START_IN_PRIVACY_MODE', enabled, persist=True)
    return {"status": "success", "enabled": enabled}


# =============================================================================
# LLM PROVIDER ROUTES
# =============================================================================

@app.get("/api/llm/providers")
async def get_llm_providers(request: Request, _=Depends(require_login)):
    """Get LLM providers configuration."""
    from core.settings_manager import settings
    from core.chat.llm_providers import get_available_providers, PROVIDER_METADATA
    providers_config = settings.get('LLM_PROVIDERS', {})
    providers_list = get_available_providers(providers_config)
    metadata = {k: {'model_options': v.get('model_options'), 'is_local': v.get('is_local', False)}
                for k, v in PROVIDER_METADATA.items()}
    return {"providers": providers_list, "metadata": metadata}


@app.put("/api/llm/providers/{provider_key}")
async def update_llm_provider(provider_key: str, request: Request, _=Depends(require_login)):
    """Update LLM provider settings."""
    from core.settings_manager import settings
    data = await request.json()
    providers = settings.get('LLM_PROVIDERS', {})
    if provider_key not in providers:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_key}' not found")
    providers[provider_key].update(data)
    settings.set('LLM_PROVIDERS', providers, persist=True)
    return {"status": "success", "provider": provider_key}


@app.put("/api/llm/fallback-order")
async def update_fallback_order(request: Request, _=Depends(require_login)):
    """Update LLM fallback order."""
    from core.settings_manager import settings
    data = await request.json()
    order = data.get('order', [])
    settings.set('LLM_FALLBACK_ORDER', order, persist=True)
    return {"status": "success", "order": order}


@app.post("/api/llm/test/{provider_key}")
async def test_llm_provider(provider_key: str, request: Request, _=Depends(require_login)):
    """Test LLM provider connection via health_check()."""
    from core.chat.llm_providers import get_provider_by_key
    try:
        providers_config = dict(getattr(config, 'LLM_PROVIDERS', {}))
        if provider_key not in providers_config:
            return {"status": "error", "error": f"Unknown provider: {provider_key}"}

        test_config = dict(providers_config[provider_key])
        test_config['enabled'] = True

        try:
            body = await request.json()
        except Exception:
            body = {}
        for field in ('api_key', 'base_url', 'model'):
            if body.get(field):
                test_config[field] = body[field]

        providers_config[provider_key] = test_config

        def _test_provider():
            provider = get_provider_by_key(provider_key, providers_config, getattr(config, 'LLM_REQUEST_TIMEOUT', 30))
            if not provider:
                return {"status": "error", "error": f"Could not create provider '{provider_key}' — check API key and settings"}
            result = provider.test_connection()
            if result.get('ok'):
                return {"status": "success", "response": result.get("response")}
            return {"status": "error", "error": result.get("error", "Connection failed")}

        return await asyncio.to_thread(_test_provider)
    except Exception as e:
        return {"status": "error", "error": str(e)}


# =============================================================================
# PROMPTS ROUTES (from prompts_api.py)
# =============================================================================

@app.get("/api/prompts")
async def list_prompts(request: Request, _=Depends(require_login)):
    """List all prompts."""
    from core.chat.history import count_tokens
    prompt_names = prompts.list_prompts()
    prompt_list = []
    for name in prompt_names:
        pdata = prompts.get_prompt(name)
        content = pdata.get('content', '') if isinstance(pdata, dict) else str(pdata)
        prompt_list.append({
            'name': name,
            'type': pdata.get('type', 'unknown') if isinstance(pdata, dict) else 'monolith',
            'char_count': len(content),
            'token_count': count_tokens(content),
            'privacy_required': pdata.get('privacy_required', False) if isinstance(pdata, dict) else False
        })
    return {"prompts": prompt_list, "current": prompts.get_active_preset_name()}


@app.post("/api/prompts/reload")
async def reload_prompts(request: Request, _=Depends(require_login)):
    """Reload prompts from disk."""
    prompts.prompt_manager.reload()
    return {"status": "success"}


@app.get("/api/prompts/components")
async def get_prompt_components(request: Request, _=Depends(require_login)):
    """Get prompt components."""
    return {"components": prompts.prompt_manager.components}


@app.get("/api/prompts/{name}")
async def get_prompt(name: str, request: Request, _=Depends(require_login)):
    """Get a specific prompt."""
    from core.chat.history import count_tokens
    pdata = prompts.get_prompt(name)
    if not pdata:
        raise HTTPException(status_code=404, detail=f"Prompt '{name}' not found")
    content = pdata.get('content', '') if isinstance(pdata, dict) else str(pdata)
    pdata['char_count'] = len(content)
    pdata['token_count'] = count_tokens(content)
    return {"name": name, "data": pdata}


@app.put("/api/prompts/{name}")
async def save_prompt(name: str, request: Request, _=Depends(require_login)):
    """Save a prompt."""
    data = await request.json()
    if prompts.save_prompt(name, data):
        publish(Events.PROMPT_CHANGED, {"name": name, "action": "saved"})
        return {"status": "success", "name": name}
    else:
        raise HTTPException(status_code=500, detail="Failed to save prompt")


@app.delete("/api/prompts/{name}")
async def delete_prompt(name: str, request: Request, _=Depends(require_login)):
    """Delete a prompt."""
    if prompts.delete_prompt(name):
        publish(Events.PROMPT_DELETED, {"name": name})
        return {"status": "success", "name": name}
    else:
        raise HTTPException(status_code=500, detail="Failed to delete prompt")


@app.put("/api/prompts/components/{comp_type}/{key}")
async def save_prompt_component(comp_type: str, key: str, request: Request, _=Depends(require_login)):
    """Save a prompt component."""
    data = await request.json()
    value = data.get('value', '')
    components = prompts.prompt_manager.components
    if comp_type not in components:
        components[comp_type] = {}
    components[comp_type][key] = value
    prompts.prompt_manager.save_components()
    publish(Events.COMPONENTS_CHANGED, {"type": comp_type, "key": key})
    return {"status": "success", "components": components}


@app.delete("/api/prompts/components/{comp_type}/{key}")
async def delete_prompt_component(comp_type: str, key: str, request: Request, _=Depends(require_login)):
    """Delete a prompt component."""
    components = prompts.prompt_manager.components
    if comp_type in components and key in components[comp_type]:
        del components[comp_type][key]
        prompts.prompt_manager.save_components()
        publish(Events.COMPONENTS_CHANGED, {"type": comp_type, "key": key, "action": "deleted"})
        return {"status": "success", "components": components}
    else:
        raise HTTPException(status_code=404, detail=f"Component '{comp_type}/{key}' not found")


@app.post("/api/prompts/{name}/load")
async def load_prompt(name: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Load/activate a prompt."""
    pdata = prompts.get_prompt(name)
    if not pdata:
        raise HTTPException(status_code=404, detail=f"Prompt '{name}' not found")
    content = pdata.get('content') if isinstance(pdata, dict) else str(pdata)
    system.llm_chat.set_system_prompt(content)
    prompts.set_active_preset_name(name)
    if hasattr(prompts.prompt_manager, 'scenario_presets') and name in prompts.prompt_manager.scenario_presets:
        prompts.apply_scenario(name)
    # Persist to chat settings so it survives restart
    system.llm_chat.session_manager.update_chat_settings({"prompt": name})
    return {"status": "success", "name": name}


@app.post("/api/prompts/reset")
async def reset_prompts(request: Request, _=Depends(require_login)):
    """Reset prompts to factory defaults."""
    if prompts.prompt_manager.reset_to_defaults():
        return {"status": "success"}
    else:
        raise HTTPException(status_code=500, detail="Failed to reset prompts")


@app.post("/api/prompts/merge")
async def merge_prompts(request: Request, _=Depends(require_login)):
    """Merge factory defaults into user prompts."""
    if prompts.prompt_manager.merge_defaults():
        return {"status": "success"}
    else:
        raise HTTPException(status_code=500, detail="Failed to merge prompts")


@app.post("/api/prompts/reset-chat-defaults")
async def reset_prompts_chat_defaults(request: Request, _=Depends(require_login)):
    """Reset chat_defaults.json to factory."""
    defaults_path = PROJECT_ROOT / "user" / "settings" / "chat_defaults.json"
    if defaults_path.exists():
        defaults_path.unlink()
    return {"status": "success"}


# =============================================================================
# TOOLSET ROUTES
# =============================================================================

@app.get("/api/toolsets")
async def list_toolsets(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """List all toolsets. Use ?filter=sidebar to exclude module-level entries."""
    from core.modules.system.toolsets import toolset_manager
    function_manager = system.llm_chat.function_manager
    filter_mode = request.query_params.get("filter", "")
    ts_set = set()
    ts_set.update(function_manager.get_available_toolsets())
    ts_set.update(toolset_manager.get_toolset_names())
    network_functions = set(function_manager.get_network_functions())

    toolsets = []
    for name in sorted(ts_set):
        if name == "all":
            func_list = [t['function']['name'] for t in function_manager.all_possible_tools]
            ts_type = "builtin"
        elif name == "none":
            func_list = []
            ts_type = "builtin"
        elif name in function_manager.function_modules and not toolset_manager.toolset_exists(name):
            # Pure module (no toolset override) — skip for sidebar
            if filter_mode == "sidebar":
                continue
            func_list = function_manager.function_modules[name]['available_functions']
            ts_type = "module"
        elif toolset_manager.toolset_exists(name):
            func_list = toolset_manager.get_toolset_functions(name)
            ts_type = toolset_manager.get_toolset_type(name)
        else:
            func_list = []
            ts_type = "unknown"

        toolsets.append({
            "name": name,
            "type": ts_type,
            "function_count": len(func_list),
            "functions": func_list,
            "emoji": toolset_manager.get_toolset_emoji(name) if toolset_manager.toolset_exists(name) else "",
            "has_network_tools": bool(set(func_list) & network_functions)
        })
    return {"toolsets": toolsets}


@app.get("/api/toolsets/current")
async def get_current_toolset(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Get current toolset."""
    info = system.llm_chat.function_manager.get_current_toolset_info()
    return info


@app.post("/api/toolsets/{toolset_name}/activate")
async def activate_toolset(toolset_name: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Activate a toolset."""
    system.llm_chat.function_manager.update_enabled_functions([toolset_name])
    publish(Events.TOOLSET_CHANGED, {"name": toolset_name})
    # Persist to chat settings so it survives restart
    system.llm_chat.session_manager.update_chat_settings({"toolset": toolset_name})
    return {"status": "success", "toolset": toolset_name}


@app.get("/api/functions")
async def list_functions(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """List all available functions."""
    function_manager = system.llm_chat.function_manager
    enabled = set(function_manager.get_enabled_function_names())
    network = set(function_manager.get_network_functions())
    modules = {}
    for module_name, module_info in function_manager.function_modules.items():
        funcs = []
        for tool in module_info['tools']:
            func_name = tool['function']['name']
            funcs.append({
                "name": func_name,
                "description": tool['function'].get('description', ''),
                "enabled": func_name in enabled,
                "is_network": func_name in network
            })
        modules[module_name] = {"functions": funcs, "count": len(funcs), "emoji": module_info.get('emoji', '')}
    return {"modules": modules}


@app.post("/api/functions/enable")
async def enable_functions(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Enable specific functions."""
    data = await request.json()
    functions = data.get('functions', [])
    system.llm_chat.function_manager.update_enabled_functions(functions)
    publish(Events.TOOLSET_CHANGED, {"name": "custom", "functions": functions})
    return {"status": "success", "enabled": functions}


@app.post("/api/toolsets/custom")
async def save_custom_toolset(request: Request, _=Depends(require_login)):
    """Save a custom toolset."""
    from core.modules.system.toolsets import toolset_manager
    data = await request.json()
    name = data.get('name')
    functions = data.get('functions', [])
    if not name:
        raise HTTPException(status_code=400, detail="Name required")
    if toolset_manager.save_toolset(name, functions):
        return {"status": "success", "name": name}
    else:
        raise HTTPException(status_code=500, detail="Failed to save toolset")


@app.delete("/api/toolsets/{toolset_name}")
async def delete_toolset(toolset_name: str, request: Request, _=Depends(require_login)):
    """Delete a custom toolset."""
    from core.modules.system.toolsets import toolset_manager
    if toolset_manager.delete_toolset(toolset_name):
        return {"status": "success", "name": toolset_name}
    else:
        raise HTTPException(status_code=404, detail="Toolset not found or cannot delete")


@app.post("/api/toolsets/{toolset_name}/emoji")
async def set_toolset_emoji(toolset_name: str, request: Request, _=Depends(require_login)):
    """Set custom emoji for a toolset (works on presets and user toolsets)."""
    from core.modules.system.toolsets import toolset_manager
    data = await request.json()
    emoji = data.get('emoji', '')
    if toolset_manager.set_emoji(toolset_name, emoji):
        return {"status": "success", "name": toolset_name, "emoji": emoji}
    else:
        raise HTTPException(status_code=404, detail="Toolset not found")


# =============================================================================
# SPICES ROUTES (from spices_api.py)
# =============================================================================

def _build_spice_response():
    """Build standardized spice response dict."""
    spices_raw = prompts.prompt_manager.spices
    disabled_cats = prompts.prompt_manager.disabled_categories
    meta = prompts.prompt_manager.spice_meta
    categories = {}
    for cat_name, spice_list in spices_raw.items():
        cat_meta = meta.get(cat_name, {})
        categories[cat_name] = {
            'spices': spice_list,
            'count': len(spice_list),
            'enabled': cat_name not in disabled_cats,
            'emoji': cat_meta.get('emoji', ''),
            'description': cat_meta.get('description', '')
        }
    return {
        "categories": categories,
        "category_count": len(categories),
        "total_spices": sum(c['count'] for c in categories.values())
    }


@app.get("/api/spices")
async def list_spices(request: Request, _=Depends(require_login)):
    """List all spices."""
    return _build_spice_response()


# Category routes MUST come before wildcard /api/spices/{category}/{index}
@app.post("/api/spices/category")
async def create_spice_category(request: Request, _=Depends(require_login)):
    """Create a spice category."""
    data = await request.json()
    name = data.get('name')
    if not name:
        raise HTTPException(status_code=400, detail="Name required")
    spices = prompts.prompt_manager._spices
    if name in spices:
        raise HTTPException(status_code=409, detail=f"Category '{name}' already exists")
    spices[name] = []
    # Store emoji/description if provided
    emoji = data.get('emoji', '')
    description = data.get('description', '')
    if emoji or description:
        prompts.prompt_manager._spice_meta[name] = {'emoji': emoji, 'description': description}
    prompts.prompt_manager.save_spices()
    return {"status": "success", "name": name}


@app.delete("/api/spices/category/{name}")
async def delete_spice_category(name: str, request: Request, _=Depends(require_login)):
    """Delete a spice category."""
    spices = prompts.prompt_manager._spices
    if name not in spices:
        raise HTTPException(status_code=404, detail=f"Category '{name}' not found")
    del spices[name]
    prompts.prompt_manager._disabled_categories.discard(name)
    prompts.prompt_manager._spice_meta.pop(name, None)
    prompts.prompt_manager.save_spices()
    return {"status": "success", "name": name}


@app.put("/api/spices/category/{name}")
async def rename_spice_category(name: str, request: Request, _=Depends(require_login)):
    """Rename a spice category."""
    data = await request.json()
    new_name = data.get('new_name')
    if not new_name:
        raise HTTPException(status_code=400, detail="New name required")
    spices = prompts.prompt_manager._spices
    if name not in spices:
        raise HTTPException(status_code=404, detail=f"Category '{name}' not found")
    spices[new_name] = spices.pop(name)
    # Transfer disabled state
    if name in prompts.prompt_manager._disabled_categories:
        prompts.prompt_manager._disabled_categories.discard(name)
        prompts.prompt_manager._disabled_categories.add(new_name)
    # Transfer meta
    if name in prompts.prompt_manager._spice_meta:
        prompts.prompt_manager._spice_meta[new_name] = prompts.prompt_manager._spice_meta.pop(name)
    prompts.prompt_manager.save_spices()
    return {"status": "success", "old_name": name, "new_name": new_name}


@app.post("/api/spices/category/{name}/toggle")
async def toggle_spice_category(name: str, request: Request, _=Depends(require_login)):
    """Toggle a spice category."""
    disabled = prompts.prompt_manager._disabled_categories
    if name in disabled:
        disabled.discard(name)
        enabled = True
    else:
        disabled.add(name)
        enabled = False
    prompts.prompt_manager.save_spices()
    prompts.invalidate_spice_picks()
    publish(Events.SPICE_CHANGED, {"category": name, "enabled": enabled})
    return {"status": "success", "category": name, "enabled": enabled}


@app.post("/api/spices/reload")
async def reload_spices(request: Request, _=Depends(require_login)):
    """Reload spices from disk."""
    prompts.prompt_manager._load_spices()
    return {"status": "success"}


# Individual spice CRUD - wildcard routes AFTER category routes
@app.post("/api/spices")
async def add_spice(request: Request, _=Depends(require_login)):
    """Add a new spice."""
    data = await request.json()
    category = data.get('category')
    content = data.get('content') or data.get('text')
    if not category or not content:
        raise HTTPException(status_code=400, detail="Category and content required")
    spices = prompts.prompt_manager._spices
    if category not in spices:
        raise HTTPException(status_code=404, detail=f"Category '{category}' not found")
    spices[category].append(content)
    prompts.prompt_manager.save_spices()
    publish(Events.SPICE_CHANGED, {"category": category, "action": "added"})
    return {"status": "success"}


@app.put("/api/spices/{category}/{index}")
async def update_spice(category: str, index: int, request: Request, _=Depends(require_login)):
    """Update a spice."""
    data = await request.json()
    content = data.get('content') or data.get('text')
    spices = prompts.prompt_manager._spices
    if category not in spices or index < 0 or index >= len(spices[category]):
        raise HTTPException(status_code=404, detail="Spice not found")
    spices[category][index] = content
    prompts.prompt_manager.save_spices()
    publish(Events.SPICE_CHANGED, {"category": category, "index": index, "action": "updated"})
    return {"status": "success"}


@app.delete("/api/spices/{category}/{index}")
async def delete_spice(category: str, index: int, request: Request, _=Depends(require_login)):
    """Delete a spice."""
    spices = prompts.prompt_manager._spices
    if category not in spices or index < 0 or index >= len(spices[category]):
        raise HTTPException(status_code=404, detail="Spice not found")
    spices[category].pop(index)
    prompts.prompt_manager.save_spices()
    publish(Events.SPICE_CHANGED, {"category": category, "index": index, "action": "deleted"})
    return {"status": "success"}


# =============================================================================
# PERSONA ROUTES
# =============================================================================

@app.get("/api/personas")
async def list_personas(request: Request, _=Depends(require_login)):
    """List all personas with summary info."""
    from core.modules.system.personas import persona_manager
    return {"personas": persona_manager.get_list(), "default": getattr(config, 'DEFAULT_PERSONA', '') or ''}


@app.get("/api/personas/{name}")
async def get_persona(name: str, request: Request, _=Depends(require_login)):
    """Get single persona with full details."""
    from core.modules.system.personas import persona_manager
    persona = persona_manager.get(name)
    if not persona:
        raise HTTPException(status_code=404, detail="Persona not found")
    return persona


@app.post("/api/personas")
async def create_persona(request: Request, _=Depends(require_login)):
    """Create a new persona."""
    from core.modules.system.personas import persona_manager
    data = await request.json()
    name = data.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="Name required")
    if not persona_manager.create(name, data):
        raise HTTPException(status_code=409, detail="Persona already exists or invalid name")
    return {"status": "success", "name": persona_manager._sanitize_name(name)}


@app.put("/api/personas/{name}")
async def update_persona(name: str, request: Request, _=Depends(require_login)):
    """Update an existing persona."""
    from core.modules.system.personas import persona_manager
    if not persona_manager.exists(name):
        raise HTTPException(status_code=404, detail="Persona not found")
    data = await request.json()
    if not persona_manager.update(name, data):
        raise HTTPException(status_code=500, detail="Failed to update persona")
    return {"status": "success"}


@app.delete("/api/personas/{name}")
async def delete_persona(name: str, request: Request, _=Depends(require_login)):
    """Delete a persona."""
    from core.modules.system.personas import persona_manager
    if not persona_manager.delete(name):
        raise HTTPException(status_code=404, detail="Persona not found")
    return {"status": "success"}


@app.post("/api/personas/{name}/duplicate")
async def duplicate_persona(name: str, request: Request, _=Depends(require_login)):
    """Duplicate a persona with a new name."""
    from core.modules.system.personas import persona_manager
    data = await request.json()
    new_name = data.get("name")
    if not new_name:
        raise HTTPException(status_code=400, detail="New name required")
    if not persona_manager.duplicate(name, new_name):
        raise HTTPException(status_code=409, detail="Source not found or target name already exists")
    return {"status": "success", "name": persona_manager._sanitize_name(new_name)}


@app.post("/api/personas/{name}/avatar")
async def upload_persona_avatar(name: str, request: Request, file: UploadFile = File(...), _=Depends(require_login)):
    """Upload avatar image for a persona (max 4MB)."""
    from core.modules.system.personas import persona_manager
    if not persona_manager.exists(name):
        raise HTTPException(status_code=404, detail="Persona not found")

    data = await file.read()
    if len(data) > 4 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Avatar too large (max 4MB)")

    # Determine extension from content type
    content_type = file.content_type or ''
    ext_map = {'image/webp': '.webp', 'image/png': '.png', 'image/jpeg': '.jpg', 'image/gif': '.gif'}
    ext = ext_map.get(content_type, '.webp')
    filename = f"{name}{ext}"

    if not persona_manager.set_avatar(name, filename, data):
        raise HTTPException(status_code=500, detail="Failed to save avatar")
    return {"status": "success", "avatar": filename}


@app.get("/api/personas/{name}/avatar")
async def serve_persona_avatar(name: str, request: Request, _=Depends(require_login)):
    """Serve persona avatar image."""
    from core.modules.system.personas import persona_manager
    avatar_path = persona_manager.get_avatar_path(name)
    if not avatar_path:
        raise HTTPException(status_code=404, detail="Avatar not found")
    return FileResponse(str(avatar_path))


@app.post("/api/personas/{name}/load")
async def load_persona(name: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Stamp persona settings into the active chat."""
    from core.modules.system.personas import persona_manager
    persona = persona_manager.get(name)
    if not persona:
        raise HTTPException(status_code=404, detail="Persona not found")

    settings = persona.get("settings", {}).copy()
    settings["persona"] = name
    session_manager = system.llm_chat.session_manager
    session_manager.update_chat_settings(settings)

    # Apply all settings (prompt, toolset, voice, spice set, scopes, state engine)
    _apply_chat_settings(system, settings)

    publish(Events.CHAT_SETTINGS_CHANGED, {"persona": name})
    return {"status": "success", "persona": name, "settings": settings}


@app.post("/api/personas/from-chat")
async def create_persona_from_chat(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Create a persona from current active chat settings."""
    from core.modules.system.personas import persona_manager
    data = await request.json()
    name = data.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="Name required")

    chat_settings = system.llm_chat.session_manager.get_chat_settings()
    if not persona_manager.create_from_settings(name, chat_settings):
        raise HTTPException(status_code=409, detail="Persona already exists or invalid name")
    return {"status": "success", "name": persona_manager._sanitize_name(name)}


@app.put("/api/personas/default")
async def set_default_persona(request: Request, _=Depends(require_login)):
    """Set the default persona for new chats."""
    from core.modules.system.personas import persona_manager
    data = await request.json()
    name = data.get("name", "")
    if name and not persona_manager.exists(name):
        raise HTTPException(status_code=404, detail="Persona not found")
    config.settings_manager.set("DEFAULT_PERSONA", name, persist=True)
    return {"status": "success", "default": name}


@app.delete("/api/personas/default")
async def clear_default_persona(request: Request, _=Depends(require_login)):
    """Clear the default persona."""
    config.settings_manager.set("DEFAULT_PERSONA", "", persist=True)
    return {"status": "success"}


# =============================================================================
# SPICE SET ROUTES
# =============================================================================

@app.get("/api/spice-sets")
async def list_spice_sets(request: Request, _=Depends(require_login)):
    """List all spice sets."""
    from core.modules.system.spice_sets import spice_set_manager
    sets = []
    for name in spice_set_manager.get_set_names():
        ss = spice_set_manager.get_set(name)
        sets.append({
            "name": name,
            "categories": ss.get('categories', []),
            "category_count": len(ss.get('categories', [])),
            "emoji": ss.get('emoji', '')
        })
    return {"spice_sets": sets, "current": spice_set_manager.active_name}


@app.get("/api/spice-sets/current")
async def get_current_spice_set(request: Request, _=Depends(require_login)):
    """Get current spice set."""
    from core.modules.system.spice_sets import spice_set_manager
    name = spice_set_manager.active_name
    ss = spice_set_manager.get_set(name)
    return {"name": name, "categories": ss.get('categories', []), "emoji": ss.get('emoji', '')}


@app.post("/api/spice-sets/{set_name}/activate")
async def activate_spice_set(set_name: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Activate a spice set - updates which categories are enabled."""
    from core.modules.system.spice_sets import spice_set_manager
    if not spice_set_manager.set_exists(set_name):
        raise HTTPException(status_code=404, detail="Spice set not found")

    categories = spice_set_manager.get_categories(set_name)
    all_cats = set(prompts.prompt_manager.spices.keys())
    disabled = all_cats - set(categories)
    prompts.prompt_manager._disabled_categories = disabled
    prompts.prompt_manager.save_spices()
    prompts.invalidate_spice_picks()

    spice_set_manager.active_name = set_name
    system.llm_chat.session_manager.update_chat_settings({"spice_set": set_name})
    publish(Events.SPICE_CHANGED, {"spice_set": set_name})
    return {"status": "success", "spice_set": set_name}


@app.post("/api/spice-sets/custom")
async def save_custom_spice_set(request: Request, _=Depends(require_login)):
    """Save a custom spice set."""
    from core.modules.system.spice_sets import spice_set_manager
    data = await request.json()
    name = data.get('name')
    categories = data.get('categories', [])
    if not name:
        raise HTTPException(status_code=400, detail="Name required")
    spice_set_manager.save_set(name, categories)
    return {"status": "success", "name": name}


@app.delete("/api/spice-sets/{set_name}")
async def delete_spice_set(set_name: str, request: Request, _=Depends(require_login)):
    """Delete a spice set."""
    from core.modules.system.spice_sets import spice_set_manager
    if spice_set_manager.delete_set(set_name):
        return {"status": "success"}
    raise HTTPException(status_code=404, detail="Spice set not found")


@app.post("/api/spice-sets/{set_name}/emoji")
async def set_spice_set_emoji(set_name: str, request: Request, _=Depends(require_login)):
    """Set emoji for a spice set."""
    from core.modules.system.spice_sets import spice_set_manager
    data = await request.json()
    emoji = data.get('emoji', '')
    if spice_set_manager.set_emoji(set_name, emoji):
        return {"status": "success", "name": set_name, "emoji": emoji}
    raise HTTPException(status_code=404, detail="Spice set not found")


# =============================================================================
# MEMORY SCOPE ROUTES
# =============================================================================

@app.get("/api/memory/scopes")
async def get_memory_scopes(request: Request, _=Depends(require_login)):
    """Get list of memory scopes."""
    from functions import memory
    scopes = memory.get_scopes()
    return {"scopes": scopes}


@app.post("/api/memory/scopes")
async def create_memory_scope(request: Request, _=Depends(require_login)):
    """Create a new memory scope."""
    import re
    from functions import memory
    data = await request.json()
    name = data.get('name', '').strip().lower()
    if not name or not re.match(r'^[a-z0-9_]{1,32}$', name):
        raise HTTPException(status_code=400, detail="Invalid scope name")
    if memory.create_scope(name):
        return {"created": name}
    else:
        raise HTTPException(status_code=500, detail="Failed to create scope")


@app.delete("/api/memory/scopes/{scope_name}")
async def delete_memory_scope(scope_name: str, request: Request, _=Depends(require_login)):
    """Delete a memory scope and ALL its memories. Requires confirmation token."""
    from functions import memory
    data = await request.json()
    if data.get('confirm') != 'DELETE':
        raise HTTPException(status_code=400, detail="Confirmation required")
    result = memory.delete_scope(scope_name)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


# =============================================================================
# GOAL SCOPE ROUTES
# =============================================================================

@app.get("/api/goals/scopes")
async def get_goal_scopes(request: Request, _=Depends(require_login)):
    """Get list of goal scopes."""
    from functions import goals
    scopes = goals.get_scopes()
    return {"scopes": scopes}


@app.post("/api/goals/scopes")
async def create_goal_scope(request: Request, _=Depends(require_login)):
    """Create a new goal scope."""
    import re
    from functions import goals
    data = await request.json()
    name = data.get('name', '').strip().lower()
    if not name or not re.match(r'^[a-z0-9_]{1,32}$', name):
        raise HTTPException(status_code=400, detail="Invalid scope name")
    if goals.create_scope(name):
        return {"created": name}
    else:
        raise HTTPException(status_code=500, detail="Failed to create scope")


@app.delete("/api/goals/scopes/{scope_name}")
async def remove_goal_scope(scope_name: str, request: Request, _=Depends(require_login)):
    from functions import goals
    data = await request.json()
    if data.get('confirm') != 'DELETE':
        raise HTTPException(status_code=400, detail="Confirmation required")
    result = goals.delete_scope(scope_name)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.get("/api/goals")
async def list_goals_api(request: Request, _=Depends(require_login)):
    from functions import goals
    scope = request.query_params.get('scope', 'default')
    status = request.query_params.get('status', 'active')
    return {"goals": goals.get_goals_list(scope, status)}


@app.get("/api/goals/{goal_id}")
async def get_goal_api(goal_id: int, request: Request, _=Depends(require_login)):
    from functions import goals
    detail = goals.get_goal_detail(goal_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Goal not found")
    return detail


@app.post("/api/goals")
async def create_goal_endpoint(request: Request, _=Depends(require_login)):
    from functions import goals
    data = await request.json()
    try:
        goal_id = goals.create_goal_api(
            title=data.get('title', ''),
            description=data.get('description'),
            priority=data.get('priority', 'medium'),
            parent_id=data.get('parent_id'),
            scope=data.get('scope', 'default'),
        )
        return {"id": goal_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/api/goals/{goal_id}")
async def update_goal_endpoint(goal_id: int, request: Request, _=Depends(require_login)):
    from functions import goals
    data = await request.json()
    try:
        goals.update_goal_api(
            goal_id,
            title=data.get('title'),
            description=data.get('description'),
            priority=data.get('priority'),
            status=data.get('status'),
            progress_note=data.get('progress_note'),
        )
        return {"updated": goal_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/goals/{goal_id}/progress")
async def add_goal_progress(goal_id: int, request: Request, _=Depends(require_login)):
    from functions import goals
    data = await request.json()
    try:
        note_id = goals.add_progress_note(goal_id, data.get('note', ''))
        return {"id": note_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/goals/{goal_id}")
async def delete_goal_endpoint(goal_id: int, request: Request, _=Depends(require_login)):
    from functions import goals
    try:
        title = goals.delete_goal_api(goal_id)
        return {"deleted": goal_id, "title": title}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# =============================================================================
# KNOWLEDGE BASE ROUTES
# =============================================================================

@app.get("/api/knowledge/scopes")
async def get_knowledge_scopes(request: Request, _=Depends(require_login)):
    from functions import knowledge
    scopes = knowledge.get_scopes()
    return {"scopes": scopes}


@app.post("/api/knowledge/scopes")
async def create_knowledge_scope(request: Request, _=Depends(require_login)):
    import re as _re
    from functions import knowledge
    data = await request.json()
    name = data.get('name', '').strip().lower()
    if not name or not _re.match(r'^[a-z0-9_]{1,32}$', name):
        raise HTTPException(status_code=400, detail="Invalid scope name")
    if knowledge.create_scope(name):
        return {"created": name}
    else:
        raise HTTPException(status_code=500, detail="Failed to create scope")


@app.delete("/api/knowledge/scopes/{scope_name}")
async def delete_knowledge_scope(scope_name: str, request: Request, _=Depends(require_login)):
    """Delete a knowledge scope, ALL its tabs, and ALL entries. Requires confirmation token."""
    from functions import knowledge
    data = await request.json()
    if data.get('confirm') != 'DELETE':
        raise HTTPException(status_code=400, detail="Confirmation required")
    result = knowledge.delete_scope(scope_name)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.get("/api/knowledge/people/scopes")
async def list_people_scopes(request: Request, _=Depends(require_login)):
    from functions import knowledge
    return {"scopes": knowledge.get_people_scopes()}


@app.post("/api/knowledge/people/scopes")
async def create_people_scope(request: Request, _=Depends(require_login)):
    from functions import knowledge
    data = await request.json()
    name = data.get('name', '').strip().lower()
    if not name or len(name) > 32:
        raise HTTPException(status_code=400, detail="Invalid scope name")
    knowledge.create_people_scope(name)
    return {"created": name}


@app.delete("/api/knowledge/people/scopes/{scope_name}")
async def remove_people_scope(scope_name: str, request: Request, _=Depends(require_login)):
    from functions import knowledge
    data = await request.json()
    if data.get('confirm') != 'DELETE':
        raise HTTPException(status_code=400, detail="Confirmation required")
    result = knowledge.delete_people_scope(scope_name)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.get("/api/knowledge/people")
async def list_people(request: Request, _=Depends(require_login)):
    from functions import knowledge
    scope = request.query_params.get('scope', 'default')
    return {"people": knowledge.get_people(scope)}


@app.post("/api/knowledge/people")
async def save_person(request: Request, _=Depends(require_login)):
    from functions import knowledge
    data = await request.json()
    name = data.get('name', '').strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    scope = data.get('scope', 'default')
    pid, is_new = knowledge.create_or_update_person(
        name=name,
        relationship=data.get('relationship'),
        phone=data.get('phone'),
        email=data.get('email'),
        address=data.get('address'),
        notes=data.get('notes'),
        scope=scope,
        person_id=data.get('id'),
    )
    return {"id": pid, "created": is_new}


@app.delete("/api/knowledge/people/{person_id}")
async def remove_person(person_id: int, request: Request, _=Depends(require_login)):
    from functions import knowledge
    if knowledge.delete_person(person_id):
        return {"deleted": person_id}
    raise HTTPException(status_code=404, detail="Person not found")


@app.post("/api/knowledge/people/import-vcf")
async def import_vcf(request: Request, _=Depends(require_login)):
    """Import contacts from a VCF (vCard) file."""
    from functions import knowledge
    import re

    form = await request.form()
    file = form.get('file')
    scope = form.get('scope', 'default')
    if not file:
        raise HTTPException(status_code=400, detail="No file uploaded")

    content = (await file.read()).decode('utf-8', errors='replace')

    # Parse vCards
    cards = []
    current = {}
    for line in content.splitlines():
        line = line.strip()
        if line.upper() == 'BEGIN:VCARD':
            current = {'phones': [], 'emails': [], 'addresses': [], 'notes': [], 'org': '', 'title': ''}
        elif line.upper() == 'END:VCARD':
            if current.get('name'):
                cards.append(current)
            current = {}
        elif not current and not isinstance(current, dict):
            continue
        else:
            # Strip type params: "TEL;TYPE=CELL:+1234" -> key=TEL, val=+1234
            if ':' not in line:
                continue
            key_part, val = line.split(':', 1)
            key = key_part.split(';')[0].upper()
            val = val.strip()
            if not val:
                continue

            if key == 'FN':
                current['name'] = val
            elif key == 'TEL':
                current['phones'].append(val)
            elif key == 'EMAIL':
                current['emails'].append(val)
            elif key == 'ADR':
                # ADR format: ;;street;city;state;zip;country (semicolons separate parts)
                parts = [p.strip() for p in val.split(';') if p.strip()]
                current['addresses'].append(', '.join(parts))
            elif key == 'NOTE':
                current['notes'].append(val)
            elif key == 'ORG':
                current['org'] = val.replace(';', ', ')
            elif key == 'TITLE':
                current['title'] = val

    # Get existing people for duplicate detection
    existing = knowledge.get_people(scope)
    existing_keys = set()
    for p in existing:
        key = (p['name'].lower().strip(), (p.get('email') or '').lower().strip())
        existing_keys.add(key)

    imported = 0
    skipped = []
    for card in cards:
        name = card.get('name', '').strip()
        if not name:
            continue

        email = card['emails'][0] if card['emails'] else ''
        phone = card['phones'][0] if card['phones'] else ''
        address = card['addresses'][0] if card['addresses'] else ''

        # Build notes from extra data
        note_parts = list(card['notes'])
        if card['org']:
            note_parts.insert(0, card['org'])
        if card['title']:
            note_parts.insert(0, card['title'])
        # Extra emails/phones beyond the first
        if len(card['emails']) > 1:
            note_parts.append('Other emails: ' + ', '.join(card['emails'][1:]))
        if len(card['phones']) > 1:
            note_parts.append('Other phones: ' + ', '.join(card['phones'][1:]))
        notes = '. '.join(note_parts) if note_parts else ''

        # Duplicate check: name + email
        dup_key = (name.lower(), email.lower())
        if dup_key in existing_keys:
            skipped.append(f"{name}" + (f" ({email})" if email else ""))
            continue

        knowledge.create_or_update_person(
            name=name, phone=phone, email=email,
            address=address, notes=notes, scope=scope
        )
        existing_keys.add(dup_key)
        imported += 1

    return {
        "imported": imported,
        "skipped_count": len(skipped),
        "skipped": skipped[:25],
        "total_in_file": len(cards)
    }


@app.get("/api/knowledge/tabs")
async def list_tabs(request: Request, _=Depends(require_login)):
    from functions import knowledge
    scope = request.query_params.get('scope', 'default')
    tab_type = request.query_params.get('type')
    return {"tabs": knowledge.get_tabs(scope, tab_type)}


@app.get("/api/knowledge/tabs/{tab_id}")
async def get_tab(tab_id: int, request: Request, _=Depends(require_login)):
    from functions import knowledge
    entries = knowledge.get_tab_entries(tab_id)
    return {"entries": entries}


@app.post("/api/knowledge/tabs")
async def create_knowledge_tab(request: Request, _=Depends(require_login)):
    from functions import knowledge
    data = await request.json()
    name = data.get('name', '').strip()
    scope = data.get('scope', 'default')
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    tab_id = knowledge.create_tab(name, scope, data.get('description'), data.get('type', 'user'))
    if tab_id:
        return {"id": tab_id}
    raise HTTPException(status_code=409, detail="Tab already exists in this scope")


@app.put("/api/knowledge/tabs/{tab_id}")
async def update_knowledge_tab(tab_id: int, request: Request, _=Depends(require_login)):
    from functions import knowledge
    data = await request.json()
    if knowledge.update_tab(tab_id, data.get('name'), data.get('description')):
        return {"updated": tab_id}
    raise HTTPException(status_code=404, detail="Tab not found")


@app.delete("/api/knowledge/tabs/{tab_id}")
async def delete_knowledge_tab(tab_id: int, request: Request, _=Depends(require_login)):
    from functions import knowledge
    if knowledge.delete_tab(tab_id):
        return {"deleted": tab_id}
    raise HTTPException(status_code=404, detail="Tab not found")


@app.post("/api/knowledge/tabs/{tab_id}/entries")
async def add_knowledge_entry(tab_id: int, request: Request, _=Depends(require_login)):
    from functions import knowledge
    data = await request.json()
    content = data.get('content', '').strip()
    if not content:
        raise HTTPException(status_code=400, detail="Content is required")
    entry_id = knowledge.add_entry(tab_id, content, source_filename=data.get('source_filename'))
    return {"id": entry_id}


@app.post("/api/knowledge/tabs/{tab_id}/upload")
async def upload_knowledge_file(tab_id: int, file: UploadFile = File(...), _=Depends(require_login)):
    """Upload a text file into a knowledge tab — chunks and embeds automatically."""
    from functions import knowledge

    # Verify tab exists
    tab = knowledge.get_tabs_by_id(tab_id)
    if not tab:
        raise HTTPException(status_code=404, detail="Tab not found")

    # Read and decode file
    raw = await file.read()
    if len(raw) > 2 * 1024 * 1024:  # 2MB cap
        raise HTTPException(status_code=400, detail="File too large (max 2MB)")

    # Try common encodings
    text = None
    for enc in ('utf-8', 'utf-8-sig', 'latin-1'):
        try:
            text = raw.decode(enc)
            break
        except (UnicodeDecodeError, ValueError):
            continue
    if text is None:
        raise HTTPException(status_code=400, detail="Could not decode file — unsupported encoding")

    text = text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="File is empty")

    filename = file.filename or 'upload.txt'
    chunks = knowledge._chunk_text(text)
    entry_ids = []
    for i, chunk in enumerate(chunks):
        eid = knowledge.add_entry(tab_id, chunk, chunk_index=i, source_filename=filename)
        entry_ids.append(eid)

    return {"filename": filename, "chunks": len(chunks), "entry_ids": entry_ids}


@app.delete("/api/knowledge/tabs/{tab_id}/file/{filename}")
async def delete_knowledge_file(tab_id: int, filename: str, _=Depends(require_login)):
    """Delete all entries from a specific uploaded file."""
    from functions import knowledge
    count = knowledge.delete_entries_by_filename(tab_id, filename)
    if count == 0:
        raise HTTPException(status_code=404, detail="No entries found for that file")
    return {"deleted": count, "filename": filename}


@app.put("/api/knowledge/entries/{entry_id}")
async def update_knowledge_entry(entry_id: int, request: Request, _=Depends(require_login)):
    from functions import knowledge
    data = await request.json()
    content = data.get('content', '').strip()
    if not content:
        raise HTTPException(status_code=400, detail="Content is required")
    if knowledge.update_entry(entry_id, content):
        return {"updated": entry_id}
    raise HTTPException(status_code=404, detail="Entry not found")


@app.delete("/api/knowledge/entries/{entry_id}")
async def delete_knowledge_entry(entry_id: int, request: Request, _=Depends(require_login)):
    from functions import knowledge
    if knowledge.delete_entry(entry_id):
        return {"deleted": entry_id}
    raise HTTPException(status_code=404, detail="Entry not found")


# =============================================================================
# MEMORY CRUD ROUTES (for Mind view management)
# =============================================================================

@app.get("/api/memory/list")
async def list_memories(request: Request, _=Depends(require_login)):
    """List memories grouped by label for the Mind view."""
    from functions import memory
    scope = request.query_params.get('scope', 'default')
    conn = memory._get_connection()
    cursor = conn.cursor()
    scope_sql, scope_params = memory._scope_condition(scope)
    cursor.execute(
        f'SELECT id, content, timestamp, label FROM memories WHERE {scope_sql} ORDER BY label, timestamp DESC',
        scope_params
    )
    rows = cursor.fetchall()
    conn.close()
    grouped = {}
    for mid, content, ts, label in rows:
        key = label or 'unlabeled'
        if key not in grouped:
            grouped[key] = []
        grouped[key].append({"id": mid, "content": content, "timestamp": ts, "label": label})
    return {"memories": grouped, "total": len(rows)}


@app.put("/api/memory/{memory_id}")
async def update_memory(memory_id: int, request: Request, _=Depends(require_login)):
    """Update memory content and re-embed."""
    from functions import memory
    data = await request.json()
    content = data.get('content', '').strip()
    scope = data.get('scope', 'default')
    if not content:
        raise HTTPException(status_code=400, detail="Content is required")
    if len(content) > memory.MAX_MEMORY_LENGTH:
        raise HTTPException(status_code=400, detail=f"Max {memory.MAX_MEMORY_LENGTH} chars")

    conn = memory._get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM memories WHERE id = ? AND scope = ?', (memory_id, scope))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Memory not found")

    keywords = memory._extract_keywords(content)
    label = data.get('label')

    embedding_blob = None
    embedder = memory._get_embedder()
    if embedder.available:
        embs = embedder.embed([content], prefix='search_document')
        if embs is not None:
            embedding_blob = embs[0].tobytes()

    cursor.execute(
        'UPDATE memories SET content = ?, keywords = ?, label = ?, embedding = ?, timestamp = CURRENT_TIMESTAMP WHERE id = ? AND scope = ?',
        (content, keywords, label, embedding_blob, memory_id, scope)
    )
    conn.commit()
    conn.close()
    return {"updated": memory_id}


@app.delete("/api/memory/{memory_id}")
async def delete_memory_api(memory_id: int, request: Request, _=Depends(require_login)):
    """Delete a memory by ID."""
    from functions import memory
    scope = request.query_params.get('scope', 'default')
    result, success = memory._delete_memory(memory_id, scope)
    if success:
        return {"deleted": memory_id}
    raise HTTPException(status_code=404, detail=result)


# =============================================================================
# STATE ENGINE ROUTES
# =============================================================================

@app.get("/api/state/presets")
async def list_state_presets(request: Request, _=Depends(require_login)):
    """List available state presets."""
    presets = []
    search_paths = [
        PROJECT_ROOT / "user" / "state_presets",
        PROJECT_ROOT / "core" / "state_engine" / "presets",
    ]
    seen = set()
    for search_dir in search_paths:
        if not search_dir.exists():
            continue
        for preset_file in search_dir.glob("*.json"):
            name = preset_file.stem
            if name in seen:
                continue
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
    return {"presets": presets}


@app.get("/api/state/{chat_name}")
async def get_chat_state(chat_name: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Get current state for a chat."""
    from core.state_engine import StateEngine
    db_path = Path("user/history/sapphire_history.db")
    if not db_path.exists():
        raise HTTPException(status_code=404, detail="Database not found")

    engine = StateEngine(chat_name, db_path)
    session_manager = system.llm_chat.session_manager

    if chat_name == session_manager.get_active_chat_name():
        chat_settings = session_manager.get_chat_settings()
        if chat_settings.get('state_engine_enabled', False):
            settings_preset = chat_settings.get('state_preset')
            db_preset = engine.preset_name
            if settings_preset and settings_preset != db_preset:
                if engine.is_empty():
                    turn = session_manager.get_turn_count()
                    engine.load_preset(settings_preset, turn)
                else:
                    engine.reload_preset_config(settings_preset)

    state = engine.get_state_full()
    formatted = {}
    for key, entry in state.items():
        formatted[key] = {
            "value": entry["value"],
            "type": entry.get("type"),
            "label": entry.get("label"),
            "turn": entry.get("turn")
        }

    return {"chat_name": chat_name, "state": formatted, "key_count": len(formatted), "preset": engine.preset_name}


@app.get("/api/state/{chat_name}/history")
async def get_chat_state_history(chat_name: str, limit: int = 100, key: str = None, request: Request = None, _=Depends(require_login)):
    """Get state change history."""
    from core.state_engine import StateEngine
    db_path = Path("user/history/sapphire_history.db")
    if not db_path.exists():
        raise HTTPException(status_code=404, detail="Database not found")
    engine = StateEngine(chat_name, db_path)
    history = engine.get_history(key=key, limit=limit)
    return {"chat_name": chat_name, "history": history, "count": len(history)}


@app.post("/api/state/{chat_name}/reset")
async def reset_chat_state(chat_name: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Reset state."""
    from core.state_engine import StateEngine
    db_path = Path("user/history/sapphire_history.db")
    if not db_path.exists():
        raise HTTPException(status_code=404, detail="Database not found")

    data = await request.json() or {}
    preset = data.get('preset')
    engine = StateEngine(chat_name, db_path)

    if preset:
        turn = system.llm_chat.session_manager.get_turn_count() if system else 0
        success, msg = engine.load_preset(preset, turn)
        if not success:
            raise HTTPException(status_code=400, detail=msg)
        result = {"status": "reset", "preset": preset, "message": msg}
    else:
        engine.clear_all()
        result = {"status": "cleared", "message": "State cleared"}

    live_engine = system.llm_chat.function_manager.get_state_engine()
    if live_engine and live_engine.chat_name == chat_name:
        live_engine.reload_from_db()

    return result


@app.post("/api/state/{chat_name}/set")
async def set_chat_state_value(chat_name: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Set a state value."""
    from core.state_engine import StateEngine
    db_path = Path("user/history/sapphire_history.db")
    if not db_path.exists():
        raise HTTPException(status_code=404, detail="Database not found")

    data = await request.json() or {}
    key = data.get('key')
    value = data.get('value')
    if not key:
        raise HTTPException(status_code=400, detail="Key required")

    engine = StateEngine(chat_name, db_path)
    turn = system.llm_chat.session_manager.get_turn_count() if system else 0
    success, msg = engine.set_state(key, value, "user", turn, "Manual edit via UI")

    if success:
        live_engine = system.llm_chat.function_manager.get_state_engine()
        if live_engine and live_engine.chat_name == chat_name:
            live_engine.reload_from_db()
        return {"status": "set", "key": key, "value": value}
    else:
        raise HTTPException(status_code=400, detail=msg)


@app.get("/api/state/saves/{preset_name}")
async def list_game_saves(preset_name: str, request: Request, _=Depends(require_login)):
    """List save slots for a game preset."""
    saves_dir = Path("user/state_saves") / preset_name
    slots = []
    for i in range(1, 6):
        slot_file = saves_dir / f"slot_{i}.json"
        if slot_file.exists():
            with open(slot_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            slots.append({"slot": i, "timestamp": data.get("timestamp"), "turn": data.get("turn", 0), "empty": False})
        else:
            slots.append({"slot": i, "empty": True})
    return {"preset": preset_name, "slots": slots}


@app.post("/api/state/{chat_name}/save")
async def save_game_state(chat_name: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Save game state to a slot."""
    from datetime import datetime, timezone
    from core.state_engine import StateEngine

    data = await request.json() or {}
    slot = data.get('slot')
    if not slot or slot < 1 or slot > 5:
        raise HTTPException(status_code=400, detail="Slot must be 1-5")

    chat_settings = system.llm_chat.session_manager.get_chat_settings()
    preset_name = chat_settings.get('state_preset')
    if not preset_name:
        raise HTTPException(status_code=400, detail="No game preset active")

    db_path = Path("user/history/sapphire_history.db")
    engine = StateEngine(chat_name, db_path)
    state = engine.get_state()
    turn = system.llm_chat.session_manager.get_turn_count() if system else 0

    save_data = {
        "slot": slot,
        "preset": preset_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "turn": turn,
        "state": state
    }

    saves_dir = Path("user/state_saves") / preset_name
    saves_dir.mkdir(parents=True, exist_ok=True)
    slot_file = saves_dir / f"slot_{slot}.json"
    with open(slot_file, 'w', encoding='utf-8') as f:
        json.dump(save_data, f, indent=2)

    return {"status": "saved", "slot": slot, "timestamp": save_data["timestamp"]}


@app.post("/api/state/{chat_name}/load")
async def load_game_state(chat_name: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Load game state from a slot."""
    from core.state_engine import StateEngine

    data = await request.json() or {}
    slot = data.get('slot')
    if not slot or slot < 1 or slot > 5:
        raise HTTPException(status_code=400, detail="Slot must be 1-5")

    chat_settings = system.llm_chat.session_manager.get_chat_settings()
    preset_name = chat_settings.get('state_preset')
    if not preset_name:
        raise HTTPException(status_code=400, detail="No game preset active")

    saves_dir = Path("user/state_saves") / preset_name
    slot_file = saves_dir / f"slot_{slot}.json"
    if not slot_file.exists():
        raise HTTPException(status_code=404, detail=f"Slot {slot} is empty")

    with open(slot_file, 'r', encoding='utf-8') as f:
        save_data = json.load(f)

    db_path = Path("user/history/sapphire_history.db")
    engine = StateEngine(chat_name, db_path)
    turn = system.llm_chat.session_manager.get_turn_count() if system else 0

    engine.clear_all()
    for key, value in save_data.get("state", {}).items():
        val = value.get("value") if isinstance(value, dict) else value
        engine.set_state(key, val, "load", turn, f"Loaded from slot {slot}")

    live_engine = system.llm_chat.function_manager.get_state_engine()
    if live_engine and live_engine.chat_name == chat_name:
        live_engine.reload_from_db()

    return {"status": "loaded", "slot": slot, "turn": save_data.get("turn", 0), "timestamp": save_data.get("timestamp")}


# =============================================================================
# BACKUP ROUTES
# =============================================================================

@app.get("/api/backup/list")
async def list_backups(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """List all backups."""
    backup_module = system.llm_chat.module_loader.get_module_instance("backup")
    if not backup_module:
        raise HTTPException(status_code=503, detail="Backup module not loaded")
    backups = backup_module.list_backups()
    return {"backups": backups}


@app.post("/api/backup/create")
async def create_backup(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Create a backup."""
    data = await request.json() or {}
    backup_type = data.get('type', 'manual')
    if backup_type not in ('daily', 'weekly', 'monthly', 'manual'):
        raise HTTPException(status_code=400, detail="Invalid backup type")

    backup_module = system.llm_chat.module_loader.get_module_instance("backup")
    if not backup_module:
        raise HTTPException(status_code=503, detail="Backup module not loaded")

    filename = backup_module.create_backup(backup_type)
    if filename:
        backup_module.rotate_backups()
        return {"status": "success", "filename": filename}
    else:
        raise HTTPException(status_code=500, detail="Backup creation failed")


@app.delete("/api/backup/delete/{filename}")
async def delete_backup(filename: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Delete a backup."""
    backup_module = system.llm_chat.module_loader.get_module_instance("backup")
    if not backup_module:
        raise HTTPException(status_code=503, detail="Backup module not loaded")
    if backup_module.delete_backup(filename):
        return {"status": "success", "deleted": filename}
    else:
        raise HTTPException(status_code=404, detail="Backup not found")


@app.get("/api/backup/download/{filename}")
async def download_backup(filename: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Download a backup."""
    backup_module = system.llm_chat.module_loader.get_module_instance("backup")
    if not backup_module:
        raise HTTPException(status_code=503, detail="Backup module not loaded")
    filepath = backup_module.get_backup_path(filename)
    if filepath:
        return FileResponse(filepath, filename=filename, media_type='application/gzip')
    else:
        raise HTTPException(status_code=404, detail="Backup not found")


# =============================================================================
# AUDIO DEVICE ROUTES
# =============================================================================

@app.get("/api/audio/devices")
async def get_audio_devices(request: Request, _=Depends(require_login)):
    """Get audio devices."""
    from core.audio import get_device_manager
    dm = get_device_manager()
    devices = dm.query_devices(force_refresh=True)

    input_devices = []
    output_devices = []

    for dev in devices:
        dev_info = {'index': dev.index, 'name': dev.name}
        if dev.max_input_channels > 0:
            input_devices.append({**dev_info, 'channels': dev.max_input_channels, 'sample_rate': int(dev.default_samplerate), 'is_default': dev.is_default_input})
        if dev.max_output_channels > 0:
            output_devices.append({**dev_info, 'channels': dev.max_output_channels, 'sample_rate': int(dev.default_samplerate), 'is_default': dev.is_default_output})

    return {
        'input': input_devices,
        'output': output_devices,
        'configured_input': getattr(config, 'AUDIO_INPUT_DEVICE', None),
        'configured_output': getattr(config, 'AUDIO_OUTPUT_DEVICE', None),
    }


@app.post("/api/audio/test-input")
async def test_audio_input(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Test audio input device."""
    data = await request.json() or {}
    device_index = data.get('device_index')
    duration = min(data.get('duration', 3.0), 5.0)

    if device_index == 'auto' or device_index == '':
        device_index = None
    elif device_index is not None:
        try:
            device_index = int(device_index)
        except (ValueError, TypeError):
            device_index = None

    def _test_input():
        from core.audio import get_device_manager, classify_audio_error
        wakeword_paused = False
        try:
            if hasattr(system, 'wake_word_recorder') and system.wake_word_recorder:
                if hasattr(system.wake_word_recorder, 'pause_recording'):
                    wakeword_paused = system.wake_word_recorder.pause_recording()
                    if wakeword_paused:
                        time.sleep(0.3)
        except Exception:
            pass
        try:
            dm = get_device_manager()
            return dm.test_input_device_safe(device_index=device_index, duration=duration)
        except Exception as e:
            return {'success': False, 'error': classify_audio_error(e)}
        finally:
            if wakeword_paused:
                try:
                    time.sleep(0.2)
                    system.wake_word_recorder.resume_recording()
                except Exception:
                    pass

    return await asyncio.to_thread(_test_input)


@app.post("/api/audio/test-output")
async def test_audio_output(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Test audio output device."""
    data = await request.json() or {}
    device_index = data.get('device_index')
    duration = min(data.get('duration', 0.5), 2.0)
    frequency = data.get('frequency', 440)

    if device_index == 'auto' or device_index == '' or device_index is None:
        device_index = None
    else:
        try:
            device_index = int(device_index)
        except (ValueError, TypeError):
            device_index = None

    def _test_output():
        import numpy as np
        import sounddevice as sd

        # Pause wakeword stream to avoid audio device conflict
        wakeword_paused = False
        try:
            if hasattr(system, 'wake_word_recorder') and system.wake_word_recorder:
                if hasattr(system.wake_word_recorder, 'pause_recording'):
                    wakeword_paused = system.wake_word_recorder.pause_recording()
                    if wakeword_paused:
                        time.sleep(0.3)
        except Exception:
            pass

        try:
            sample_rate = None
            default_rate = 44100
            if device_index is not None:
                try:
                    dev_info = sd.query_devices(device_index)
                    default_rate = int(dev_info['default_samplerate'])
                except Exception:
                    pass

            for rate in [default_rate, 48000, 44100, 32000, 24000, 22050, 16000]:
                try:
                    stream = sd.OutputStream(device=device_index, samplerate=rate, channels=1, dtype=np.float32)
                    stream.close()
                    sample_rate = rate
                    break
                except Exception:
                    continue

            if sample_rate is None:
                return {'success': False, 'error': 'Device does not support any common sample rate'}

            t = np.linspace(0, duration, int(sample_rate * duration), False)
            tone = np.sin(2 * np.pi * frequency * t)
            fade_samples = int(sample_rate * 0.02)
            fade_in = np.linspace(0, 1, fade_samples)
            fade_out = np.linspace(1, 0, fade_samples)
            tone[:fade_samples] *= fade_in
            tone[-fade_samples:] *= fade_out
            tone = (tone * 0.5 * 32767).astype(np.int16)

            sd.play(tone, sample_rate, device=device_index)
            sd.wait()
            return {'success': True, 'duration': duration, 'frequency': frequency, 'sample_rate': sample_rate}
        finally:
            if wakeword_paused:
                try:
                    time.sleep(0.2)
                    system.wake_word_recorder.resume_recording()
                except Exception:
                    pass

    return await asyncio.to_thread(_test_output)


# =============================================================================
# CONTINUITY ROUTES
# =============================================================================

@app.get("/api/continuity/tasks")
async def list_continuity_tasks(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """List continuity tasks."""
    if not hasattr(system, 'continuity_scheduler') or not system.continuity_scheduler:
        return {"tasks": []}
    return {"tasks": system.continuity_scheduler.list_tasks()}


@app.post("/api/continuity/tasks")
async def create_continuity_task(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Create a continuity task."""
    if not hasattr(system, 'continuity_scheduler') or not system.continuity_scheduler:
        raise HTTPException(status_code=503, detail="Continuity scheduler not available")
    data = await request.json()
    task_id = system.continuity_scheduler.create_task(data)
    return {"status": "success", "task_id": task_id}


@app.get("/api/continuity/tasks/{task_id}")
async def get_continuity_task(task_id: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Get a continuity task."""
    if not hasattr(system, 'continuity_scheduler') or not system.continuity_scheduler:
        raise HTTPException(status_code=503, detail="Continuity scheduler not available")
    task = system.continuity_scheduler.get_task(task_id)
    if task:
        return task
    else:
        raise HTTPException(status_code=404, detail="Task not found")


@app.put("/api/continuity/tasks/{task_id}")
async def update_continuity_task(task_id: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Update a continuity task."""
    if not hasattr(system, 'continuity_scheduler') or not system.continuity_scheduler:
        raise HTTPException(status_code=503, detail="Continuity scheduler not available")
    data = await request.json()
    if system.continuity_scheduler.update_task(task_id, data):
        return {"status": "success"}
    else:
        raise HTTPException(status_code=404, detail="Task not found")


@app.delete("/api/continuity/tasks/{task_id}")
async def delete_continuity_task(task_id: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Delete a continuity task."""
    if not hasattr(system, 'continuity_scheduler') or not system.continuity_scheduler:
        raise HTTPException(status_code=503, detail="Continuity scheduler not available")
    if system.continuity_scheduler.delete_task(task_id):
        return {"status": "success"}
    else:
        raise HTTPException(status_code=404, detail="Task not found")


@app.post("/api/continuity/tasks/{task_id}/run")
def run_continuity_task(task_id: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Manually run a continuity task. Sync so it runs in threadpool, not blocking event loop."""
    if not hasattr(system, 'continuity_scheduler') or not system.continuity_scheduler:
        raise HTTPException(status_code=503, detail="Continuity scheduler not available")
    result = system.continuity_scheduler.run_task_now(task_id)
    return result


@app.get("/api/continuity/status")
async def get_continuity_status(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Get continuity scheduler status."""
    if not hasattr(system, 'continuity_scheduler') or not system.continuity_scheduler:
        return {"running": False}
    return system.continuity_scheduler.get_status()


@app.get("/api/continuity/activity")
async def get_continuity_activity(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Get continuity activity log."""
    if not hasattr(system, 'continuity_scheduler') or not system.continuity_scheduler:
        return {"activity": []}
    limit = int(request.query_params.get("limit", 50))
    return {"activity": system.continuity_scheduler.get_activity(limit)}


@app.get("/api/continuity/timeline")
async def get_continuity_timeline(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Get continuity task timeline."""
    if not hasattr(system, 'continuity_scheduler') or not system.continuity_scheduler:
        return {"timeline": []}
    hours = int(request.query_params.get("hours", 24))
    return {"timeline": system.continuity_scheduler.get_timeline(hours)}


# =============================================================================
# SETUP WIZARD ROUTES
# =============================================================================

@app.get("/api/setup/check-packages")
async def check_packages(request: Request, _=Depends(require_login)):
    """Check optional packages. Returns format expected by setup wizard UI."""
    checks = {
        "tts": {"package": "Kokoro TTS", "requirements": "requirements-tts.txt", "mod": "kokoro"},
        "stt": {"package": "Faster Whisper", "requirements": "requirements-stt.txt", "mod": "faster_whisper"},
        "wakeword": {"package": "OpenWakeWord", "requirements": "requirements-wakeword.txt", "mod": "openwakeword"},
    }
    packages = {}
    for key, info in checks.items():
        try:
            __import__(info["mod"])
            installed = True
        except ImportError:
            installed = False
        packages[key] = {"installed": installed, "package": info["package"], "requirements": info["requirements"]}
    return {"packages": packages}


@app.get("/api/setup/wizard-step")
async def get_wizard_step(request: Request, _=Depends(require_login)):
    """Get wizard step."""
    return {"step": getattr(config, 'SETUP_WIZARD_STEP', 'complete')}


@app.put("/api/setup/wizard-step")
async def set_wizard_step(request: Request, _=Depends(require_login)):
    """Set wizard step."""
    from core.settings_manager import settings
    data = await request.json()
    step = data.get('step', 'complete')
    settings.set('SETUP_WIZARD_STEP', step, persist=True)
    return {"status": "success", "step": step}


# =============================================================================
# AVATAR ROUTES
# =============================================================================

@app.get("/api/avatars")
async def get_avatars(request: Request, _=Depends(require_login)):
    """Get avatar paths."""
    avatar_dir = PROJECT_ROOT / 'user' / 'public' / 'avatars'
    static_dir = STATIC_DIR / 'users'

    result = {}
    for role in ('user', 'assistant'):
        custom = list(avatar_dir.glob(f'{role}.*')) if avatar_dir.exists() else []
        if custom:
            ext = custom[0].suffix
            result[role] = f"/user-assets/avatars/{role}{ext}"
        else:
            for ext in ('.webp', '.png', '.jpg'):
                if (static_dir / f'{role}{ext}').exists():
                    result[role] = f"/static/users/{role}{ext}"
                    break
            else:
                result[role] = None
    return result


@app.post("/api/avatar/upload")
async def upload_avatar(file: UploadFile = File(...), role: str = Form(...), _=Depends(require_login)):
    """Upload avatar."""
    import glob as glob_mod

    if role not in ('user', 'assistant'):
        raise HTTPException(status_code=400, detail="Invalid role")

    allowed_ext = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
    ext = os.path.splitext(file.filename or '')[1].lower()
    if ext not in allowed_ext:
        raise HTTPException(status_code=400, detail="Invalid file type")

    contents = await file.read()
    if len(contents) > 4 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large. Max 4MB")

    avatar_dir = PROJECT_ROOT / 'user' / 'public' / 'avatars'
    avatar_dir.mkdir(parents=True, exist_ok=True)

    # Delete existing
    existing = list(avatar_dir.glob(f'{role}.*'))
    for old_file in existing:
        try:
            old_file.unlink()
        except Exception:
            pass

    save_path = avatar_dir / f'{role}{ext}'
    with open(save_path, 'wb') as f:
        f.write(contents)

    return {"status": "success", "path": f"/user-assets/avatars/{role}{ext}"}


@app.get("/api/avatar/check/{role}")
async def check_avatar(role: str, request: Request, _=Depends(require_login)):
    """Check if custom avatar exists."""
    if role not in ('user', 'assistant'):
        raise HTTPException(status_code=400, detail="Invalid role")

    avatar_dir = PROJECT_ROOT / 'user' / 'public' / 'avatars'
    existing = list(avatar_dir.glob(f'{role}.*')) if avatar_dir.exists() else []

    if existing:
        ext = existing[0].suffix
        return {"exists": True, "path": f"/user-assets/avatars/{role}{ext}"}
    return {"exists": False, "path": None}


# =============================================================================
# PLUGINS ROUTES (from plugins_api.py)
# =============================================================================

# Plugin settings paths
USER_WEBUI_DIR = PROJECT_ROOT / 'user' / 'webui'
USER_PLUGINS_JSON = USER_WEBUI_DIR / 'plugins.json'
USER_PLUGIN_SETTINGS_DIR = USER_WEBUI_DIR / 'plugins'
LOCKED_PLUGINS = ['settings-modal', 'plugins-modal']


def _get_merged_plugins():
    """Merge static and user plugins.json."""
    static_plugins_json = STATIC_DIR / 'plugins' / 'plugins.json'
    try:
        with open(static_plugins_json) as f:
            static = json.load(f)
    except Exception:
        static = {"enabled": [], "plugins": {}}

    if not USER_PLUGINS_JSON.exists():
        return static

    try:
        with open(USER_PLUGINS_JSON) as f:
            user = json.load(f)
    except Exception:
        return static

    merged = {
        "enabled": user.get("enabled", static.get("enabled", [])),
        "plugins": dict(static.get("plugins", {}))
    }
    if "plugins" in user:
        merged["plugins"].update(user["plugins"])

    for locked in LOCKED_PLUGINS:
        if locked not in merged["enabled"]:
            merged["enabled"].append(locked)

    return merged


@app.get("/api/webui/plugins")
async def list_plugins(request: Request, _=Depends(require_login)):
    """List all plugins."""
    merged = _get_merged_plugins()
    enabled_set = set(merged.get("enabled", []))

    result = []
    for name, meta in merged.get("plugins", {}).items():
        result.append({
            "name": name,
            "enabled": name in enabled_set,
            "locked": name in LOCKED_PLUGINS,
            "title": meta.get("title", name),
            "showInSidebar": meta.get("showInSidebar", True),
            "collapsible": meta.get("collapsible", True)
        })

    return {"plugins": result, "locked": LOCKED_PLUGINS}


@app.put("/api/webui/plugins/toggle/{plugin_name}")
async def toggle_plugin(plugin_name: str, request: Request, _=Depends(require_login)):
    """Toggle a plugin."""
    if plugin_name in LOCKED_PLUGINS:
        raise HTTPException(status_code=403, detail=f"Cannot disable locked plugin: {plugin_name}")

    merged = _get_merged_plugins()
    if plugin_name not in merged.get("plugins", {}):
        raise HTTPException(status_code=404, detail=f"Unknown plugin: {plugin_name}")

    enabled = list(merged.get("enabled", []))
    if plugin_name in enabled:
        enabled.remove(plugin_name)
        new_state = False
    else:
        enabled.append(plugin_name)
        new_state = True

    USER_WEBUI_DIR.mkdir(parents=True, exist_ok=True)
    user_data = {}
    if USER_PLUGINS_JSON.exists():
        try:
            with open(USER_PLUGINS_JSON) as f:
                user_data = json.load(f)
        except Exception:
            pass
    user_data["enabled"] = enabled
    with open(USER_PLUGINS_JSON, 'w') as f:
        json.dump(user_data, f, indent=2)

    return {"status": "success", "plugin": plugin_name, "enabled": new_state, "reload_required": True}


@app.get("/api/webui/plugins/{plugin_name}/settings")
async def get_plugin_settings(plugin_name: str, request: Request, _=Depends(require_login)):
    """Get plugin settings."""
    settings_file = USER_PLUGIN_SETTINGS_DIR / f"{plugin_name}.json"
    if not settings_file.exists():
        return {"plugin": plugin_name, "settings": {}}
    try:
        with open(settings_file) as f:
            settings = json.load(f)
        return {"plugin": plugin_name, "settings": settings}
    except Exception:
        return {"plugin": plugin_name, "settings": {}}


@app.put("/api/webui/plugins/{plugin_name}/settings")
async def update_plugin_settings(plugin_name: str, request: Request, _=Depends(require_login)):
    """Update plugin settings."""
    data = await request.json()
    settings = data.get("settings", data)

    USER_PLUGIN_SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    settings_file = USER_PLUGIN_SETTINGS_DIR / f"{plugin_name}.json"
    with open(settings_file, 'w') as f:
        json.dump(settings, f, indent=2)

    return {"status": "success", "plugin": plugin_name, "settings": settings}


@app.delete("/api/webui/plugins/{plugin_name}/settings")
async def reset_plugin_settings(plugin_name: str, request: Request, _=Depends(require_login)):
    """Reset plugin settings."""
    settings_file = USER_PLUGIN_SETTINGS_DIR / f"{plugin_name}.json"
    if settings_file.exists():
        settings_file.unlink()
    return {"status": "success", "plugin": plugin_name, "message": "Settings reset"}


@app.get("/api/webui/plugins/config")
async def get_plugins_config(request: Request, _=Depends(require_login)):
    """Get full plugins config."""
    return _get_merged_plugins()


@app.post("/api/webui/plugins/image-gen/test-connection")
async def test_sdxl_connection(request: Request, _=Depends(require_login)):
    """Test SDXL connection."""
    data = await request.json() or {}
    url = data.get('url', '').strip()
    if not url:
        return {"success": False, "error": "No URL provided"}
    if not url.startswith(('http://', 'https://')):
        return {"success": False, "error": "URL must start with http:// or https://"}

    def _test():
        import requests as req
        try:
            response = req.get(url, timeout=5)
            return {"success": True, "status_code": response.status_code, "message": f"Connected (HTTP {response.status_code})"}
        except req.exceptions.Timeout:
            return {"success": False, "error": "Connection timed out (5s)"}
        except req.exceptions.ConnectionError as e:
            return {"success": False, "error": f"Cannot connect: {str(e)[:100]}"}
        except Exception as e:
            return {"success": False, "error": f"Error: {str(e)[:100]}"}

    return await asyncio.to_thread(_test)


@app.get("/api/webui/plugins/image-gen/defaults")
async def get_image_gen_defaults(request: Request, _=Depends(require_login)):
    """Get image-gen defaults."""
    try:
        from functions.image import DEFAULTS
        return DEFAULTS
    except ImportError:
        raise HTTPException(status_code=500, detail="Could not load defaults")


@app.get("/api/webui/plugins/homeassistant/defaults")
async def get_ha_defaults(request: Request, _=Depends(require_login)):
    """Get HA defaults."""
    return {"url": "http://homeassistant.local:8123", "blacklist": ["cover.*", "lock.*"], "notify_service": ""}


@app.post("/api/webui/plugins/homeassistant/test-connection")
async def test_ha_connection(request: Request, _=Depends(require_login)):
    """Test HA connection."""
    from core.credentials_manager import credentials

    data = await request.json() or {}
    url = data.get('url', '').strip().rstrip('/')
    token = data.get('token', '').strip()

    if not token:
        token = credentials.get_ha_token()

    if not url:
        return {"success": False, "error": "No URL provided"}
    if not token:
        return {"success": False, "error": "No API token found"}
    if len(token) < 100:
        return {"success": False, "error": f"Token too short ({len(token)} chars)"}
    if not url.startswith(('http://', 'https://')):
        return {"success": False, "error": "URL must start with http:// or https://"}

    def _test():
        import requests as req
        try:
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            response = req.get(f"{url}/api/", headers=headers, timeout=10)
            if response.status_code == 200:
                return {"success": True, "message": response.json().get('message', 'Connected')}
            elif response.status_code == 401:
                return {"success": False, "error": "Invalid API token"}
            return {"success": False, "error": f"HTTP {response.status_code}"}
        except req.exceptions.Timeout:
            return {"success": False, "error": "Connection timed out"}
        except req.exceptions.ConnectionError as e:
            return {"success": False, "error": f"Cannot connect: {str(e)[:100]}"}
        except Exception as e:
            return {"success": False, "error": f"Error: {str(e)[:100]}"}

    return await asyncio.to_thread(_test)


@app.post("/api/webui/plugins/homeassistant/test-notify")
async def test_ha_notify(request: Request, _=Depends(require_login)):
    """Test HA notification service."""
    from core.credentials_manager import credentials

    data = await request.json() or {}
    url = data.get('url', '').strip().rstrip('/')
    token = data.get('token', '').strip()
    notify_service = data.get('notify_service', '').strip()

    if not token:
        token = credentials.get_ha_token()

    if not url:
        return {"success": False, "error": "No URL provided"}
    if not token:
        return {"success": False, "error": "No API token found"}
    if not notify_service:
        return {"success": False, "error": "No notify service specified"}

    def _test():
        import requests as req
        try:
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            payload = {"message": "Test notification from Sapphire", "title": "Sapphire"}
            response = req.post(
                f"{url}/api/services/notify/{notify_service}",
                headers=headers, json=payload, timeout=10
            )
            if response.status_code == 200:
                return {"success": True}
            elif response.status_code == 401:
                return {"success": False, "error": "Invalid API token"}
            elif response.status_code == 404:
                return {"success": False, "error": f"Service 'notify.{notify_service}' not found"}
            return {"success": False, "error": f"HTTP {response.status_code}"}
        except req.exceptions.Timeout:
            return {"success": False, "error": "Connection timed out"}
        except req.exceptions.ConnectionError as e:
            return {"success": False, "error": f"Cannot connect: {str(e)[:100]}"}
        except Exception as e:
            return {"success": False, "error": f"Error: {str(e)[:100]}"}

    return await asyncio.to_thread(_test)


@app.put("/api/webui/plugins/homeassistant/token")
async def set_ha_token(request: Request, _=Depends(require_login)):
    """Store HA token."""
    from core.credentials_manager import credentials
    data = await request.json() or {}
    token = data.get('token', '').strip()
    if credentials.set_ha_token(token):
        return {"success": True, "has_token": bool(token)}
    else:
        raise HTTPException(status_code=500, detail="Failed to save token")


@app.get("/api/webui/plugins/homeassistant/token")
async def get_ha_token_status(request: Request, _=Depends(require_login)):
    """Check if HA token exists."""
    from core.credentials_manager import credentials
    has_token = credentials.has_ha_token()
    return {"has_token": has_token}


# =============================================================================
# SYSTEM MANAGEMENT ROUTES
# =============================================================================

@app.post("/api/system/restart")
async def request_system_restart(request: Request, _=Depends(require_login)):
    """Request system restart."""
    if not _restart_callback:
        raise HTTPException(status_code=503, detail="Restart not available")
    _restart_callback()
    return {"status": "restarting", "message": "Restart initiated"}


@app.post("/api/system/shutdown")
async def request_system_shutdown(request: Request, _=Depends(require_login)):
    """Request system shutdown."""
    if not _shutdown_callback:
        raise HTTPException(status_code=503, detail="Shutdown not available")
    _shutdown_callback()
    return {"status": "shutting_down", "message": "Shutdown initiated"}


# =============================================================================
# SDXL IMAGE PROXY
# =============================================================================

@app.get("/api/sdxl-image/{image_id}")
async def proxy_sdxl_image(image_id: str, request: Request, _=Depends(require_login)):
    """Proxy SDXL images."""
    import re

    if not re.match(r'^[a-zA-Z0-9_-]+$', image_id):
        raise HTTPException(status_code=400, detail="Invalid image ID")

    # Get SDXL URL from plugin settings
    settings_file = USER_PLUGIN_SETTINGS_DIR / "image-gen.json"
    sdxl_url = "http://127.0.0.1:5153"
    if settings_file.exists():
        try:
            with open(settings_file) as f:
                settings = json.load(f)
            sdxl_url = settings.get('api_url', sdxl_url)
        except Exception:
            pass

    def _fetch_image():
        import requests as req
        return req.get(f'{sdxl_url}/output/{image_id}.jpg', timeout=10)

    try:
        import requests as req
        response = await asyncio.to_thread(_fetch_image)
        if response.status_code == 200:
            return StreamingResponse(io.BytesIO(response.content), media_type='image/jpeg')
        elif response.status_code == 404:
            raise HTTPException(status_code=404, detail="Image not found yet")
        else:
            raise HTTPException(status_code=500, detail=f"SDXL returned {response.status_code}")
    except req.exceptions.Timeout:
        raise HTTPException(status_code=504, detail="SDXL timeout")
    except req.exceptions.ConnectionError:
        raise HTTPException(status_code=502, detail=f"Cannot connect to SDXL at {sdxl_url}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Image fetch failed")
