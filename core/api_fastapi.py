# api_fastapi.py - FastAPI app setup, middleware, page routes, and router includes
import os
import json
import time
import secrets
import logging
from pathlib import Path
from typing import Optional, Any

from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse, RedirectResponse, FileResponse
from starlette.middleware.sessions import SessionMiddleware

import config
from core.auth import (
    require_login, require_setup, check_rate_limit,
    generate_csrf_token, validate_csrf, get_client_ip
)
from core.setup import get_password_hash, save_password_hash, verify_password, is_setup_complete
from core.event_bus import publish, Events
from core import prompts

logger = logging.getLogger(__name__)

# Cache-bust version — changes every server restart so browsers fetch fresh assets
BOOT_VERSION = str(int(time.time()))

# App version from VERSION file
try:
    APP_VERSION = (Path(__file__).parent.parent / 'VERSION').read_text().strip()
except Exception:
    APP_VERSION = '?'

# Project paths — defined early so _build_import_map() can use STATIC_DIR
PROJECT_ROOT = Path(__file__).parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "interfaces" / "web" / "templates"
STATIC_DIR = PROJECT_ROOT / "interfaces" / "web" / "static"
USER_PUBLIC_DIR = PROJECT_ROOT / "user" / "public"


def _is_managed():
    """Check if running in managed/Docker mode."""
    from core.settings_manager import settings
    return settings.is_managed()


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


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Log unhandled exceptions to app logger instead of just stderr."""
    logger.error(f"Unhandled {type(exc).__name__} on {request.method} {request.url.path}: {exc}", exc_info=True)
    from starlette.responses import JSONResponse
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# Session middleware added after HTTP middleware decorators below (outermost = LIFO)

# Static files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# User assets (avatars, etc)
if USER_PUBLIC_DIR.exists():
    app.mount("/user-assets", StaticFiles(directory=str(USER_PUBLIC_DIR)), name="user-assets")

# Plugin web assets — serves from plugins/{name}/web/ and user/plugins/{name}/web/
SYSTEM_PLUGINS_DIR = PROJECT_ROOT / "plugins"
USER_PLUGINS_DIR_WEB = PROJECT_ROOT / "user" / "plugins"

import mimetypes
@app.get("/plugin-web/{plugin_name}/{path:path}")
async def serve_plugin_web(plugin_name: str, path: str, _=Depends(require_login)):
    """Serve web assets from plugin web/ directories."""
    for base_dir in [SYSTEM_PLUGINS_DIR, USER_PLUGINS_DIR_WEB]:
        web_dir = (base_dir / plugin_name / "web").resolve()
        file_path = (web_dir / path).resolve()
        # Security: ensure path doesn't escape web/ dir
        if not str(file_path).startswith(str(web_dir)):
            continue
        if file_path.exists() and file_path.is_file():
            content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
            return FileResponse(file_path, media_type=content_type)
    return JSONResponse({"error": "Not found"}, status_code=404)

# Workspace file serving — Claude Code project outputs
@app.get("/workspace/{project}/{path:path}")
async def serve_workspace(project: str, path: str, _=Depends(require_login)):
    """Serve files from Claude Code workspace directories."""
    try:
        from core.plugin_loader import plugin_loader
        settings = plugin_loader.get_plugin_settings("claude-code") or {}
        ws_dir = settings.get('workspace_dir', '~/claude-workspaces')
    except Exception:
        ws_dir = '~/claude-workspaces'
    workspace_base = Path(os.path.expanduser(ws_dir)).resolve()
    project_dir = (workspace_base / project).resolve()
    if not str(project_dir).startswith(str(workspace_base)):
        return JSONResponse({"error": "Not found"}, status_code=404)
    file_path = (project_dir / path).resolve()
    if not str(file_path).startswith(str(project_dir)):
        return JSONResponse({"error": "Not found"}, status_code=404)
    if file_path.exists() and file_path.is_file():
        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        return FileResponse(file_path, media_type=content_type)
    return JSONResponse({"error": "Not found"}, status_code=404)

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


def get_restart_callback():
    """Get restart callback (for route modules that need it)."""
    return _restart_callback


def get_shutdown_callback():
    """Get shutdown callback (for route modules that need it)."""
    return _shutdown_callback


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
        "app_version": APP_VERSION,
        "managed": _is_managed(),
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


from core.tts.utils import validate_voice as _validate_tts_voice, default_voice as _tts_default_voice


def _apply_chat_settings(system, settings: dict):
    """Apply chat settings to the system (TTS, prompt, ability, state engine)."""
    try:
        if "voice" in settings:
            voice = _validate_tts_voice(settings["voice"])
            system.tts.set_voice(voice)
        if "pitch" in settings:
            system.tts.set_pitch(settings["pitch"])
        if "speed" in settings:
            system.tts.set_speed(settings["speed"])

        if "prompt" in settings:
            prompt_name = settings["prompt"]
            prompt_data = prompts.get_prompt(prompt_name)
            content = prompt_data.get('content', '') if isinstance(prompt_data, dict) else ''
            if content:
                system.llm_chat.set_system_prompt(content)
                prompts.set_active_preset_name(prompt_name)

                if hasattr(prompts.prompt_manager, 'scenario_presets') and prompt_name in prompts.prompt_manager.scenario_presets:
                    prompts.apply_scenario(prompt_name)

                logger.info(f"Applied prompt: {prompt_name}")

        from core.chat.function_manager import apply_scopes_from_settings
        apply_scopes_from_settings(system.llm_chat.function_manager, settings)

        if "spice_set" in settings:
            from core.spice_sets import spice_set_manager
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

        system.llm_chat._update_story_engine()

        if settings.get('story_engine_enabled') is not None:
            toolset_info = system.llm_chat.function_manager.get_current_toolset_info()
            publish(Events.TOOLSET_CHANGED, {
                "name": toolset_info.get("name", "custom"),
                "action": "story_engine_update",
                "function_count": toolset_info.get("function_count", 0)
            })

    except Exception as e:
        logger.error(f"Error applying chat settings: {e}", exc_info=True)


# =============================================================================
# ROUTE MODULES
# =============================================================================

from core.routes.chat import router as chat_router
from core.routes.tts import router as tts_router
from core.routes.settings import router as settings_router
from core.routes.content import router as content_router
from core.routes.knowledge import router as knowledge_router
from core.routes.story_engine import router as story_engine_router
from core.routes.system import router as system_router
from core.routes.plugins import router as plugins_router
from core.routes.media import router as media_router
from core.routes.agents import router as agents_router
from core.routes.docs import router as docs_router

app.include_router(chat_router)
app.include_router(tts_router)
app.include_router(settings_router)
app.include_router(content_router)
app.include_router(knowledge_router)
app.include_router(story_engine_router)
app.include_router(system_router)
app.include_router(plugins_router)
app.include_router(media_router)
app.include_router(agents_router)
app.include_router(docs_router)

