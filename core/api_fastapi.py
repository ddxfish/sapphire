# api_fastapi.py - FastAPI app setup, middleware, page routes, and router includes
import os
import re
import json
import time
import secrets
import logging
import threading
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
from core.fs_utils import replace_with_retry

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

    # Bare-specifier mappings for CDN libraries used via /cdn-cache/. Lets
    # esm.sh's `?external=three` addons (GLTFLoader, OrbitControls) resolve
    # `import * from "three"` to the cached three.js — sharing one module
    # instance across all importers so instanceof checks work across them.
    # 2026-05-13.
    imports["three"] = "/cdn-cache/esm.sh/three@0.170.0?bundle&target=es2022"

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

# Dashboard fonts — bootstrap on import (downloads from Google Fonts on first
# boot if missing; honors DASHBOARD_FONTS_AUTOFETCH). Mounted regardless so
# the dir exists before mount; missing files 404 cleanly and CSS falls back.
USER_FONTS_DIR = PROJECT_ROOT / "user" / "fonts"
try:
    from core.font_bootstrap import ensure_dashboard_fonts
    ensure_dashboard_fonts(PROJECT_ROOT / "user")
except Exception as _font_e:  # never block boot on font fetch
    import logging as _logging
    _logging.getLogger(__name__).warning(f"font bootstrap failed: {_font_e}")
USER_FONTS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/user-fonts", StaticFiles(directory=str(USER_FONTS_DIR)), name="user-fonts")

# Dashboard built-in widgets — register with the central widget registry,
# then mount their JS render modules at /core-widgets/ so the dashboard
# host can dynamic-import them.
try:
    from core.dashboard_builtins import register_all as _register_builtin_widgets
    _register_builtin_widgets()
except Exception as _w_e:
    import logging as _logging
    _logging.getLogger(__name__).warning(f"built-in widget registration failed: {_w_e}")
CORE_WIDGETS_DIR = PROJECT_ROOT / "core" / "dashboard_builtins" / "web"
CORE_WIDGETS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/core-widgets", StaticFiles(directory=str(CORE_WIDGETS_DIR)), name="core-widgets")

# Plugin web assets — serves from plugins/{name}/web/ and user/plugins/{name}/web/
SYSTEM_PLUGINS_DIR = PROJECT_ROOT / "plugins"
USER_PLUGINS_DIR_WEB = PROJECT_ROOT / "user" / "plugins"

import mimetypes

# Windows reads MIME types from the registry (HKCR), and common installers
# rewrite .js to text/plain there — browsers hard-refuse ES modules served
# that way, blanking every module page with zero server errors. Pin the
# types the web UI ships; add_type overrides whatever the registry said.
for _ext, _mt in (('.js', 'text/javascript'), ('.mjs', 'text/javascript'),
                  ('.css', 'text/css'), ('.json', 'application/json'),
                  ('.svg', 'image/svg+xml'), ('.wasm', 'application/wasm'),
                  ('.woff2', 'font/woff2'), ('.html', 'text/html')):
    mimetypes.add_type(_mt, _ext)

_PLUGIN_NAME_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,64}')
_STORY_ART_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".gif")


def _serve_contained(file_path: Path, base: Path):
    """FileResponse for file_path only if it is truly inside base — real path
    containment, not string prefix (which lets sibling dirs sharing a name
    prefix, and any `..` that lands back under the prefix, through)."""
    try:
        if file_path.is_relative_to(base) and file_path.is_file():
            content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
            return FileResponse(file_path, media_type=content_type)
    except (OSError, ValueError):
        # ValueError: embedded null byte (%00 in the path) — refuse, not 500
        pass
    return None


@app.get("/plugin-web/{plugin_name}/{path:path}")
async def serve_plugin_web(plugin_name: str, path: str, _=Depends(require_login)):
    """Serve web assets from plugin web/ and app/ directories.
    /plugin-web/{name}/foo.js     → {plugin}/web/foo.js  (existing behavior)
    /plugin-web/{name}/app/foo.js → {plugin}/app/foo.js  (app pages)
    /plugin-web/{name}/stories/…  → story-pack scene art (IMAGES ONLY)
    """
    # Plugin names are flat slugs — no dots, no separators. Anything else
    # (`..`, absolute paths) re-roots the candidate walk; refuse pre-disk.
    if not _PLUGIN_NAME_RE.fullmatch(plugin_name):
        return JSONResponse({"error": "Not found"}, status_code=404)
    # Registry first: a user-band plugin can shadow a same-named system plugin,
    # and the dir scan below (system first) would serve the shadowed copy's
    # assets. The registry knows which copy actually loaded.
    candidates = []
    try:
        from core.plugin_loader import plugin_loader
        info = plugin_loader.get_plugin_info(plugin_name)
        # Disable must be revocation: a known-but-not-running plugin serves
        # NOTHING — before this, a disabled theme plugin's CSS/JS kept loading
        # from disk on every boot via stale localStorage URLs (lifecycle #1).
        # Unknown names still fall through to the dir scan (registry gaps).
        if info and (not info.get("loaded") or not info.get("enabled")):
            return JSONResponse({"error": "Not found"}, status_code=404)
        if info and info.get("path"):
            candidates.append(Path(info["path"]))
    except Exception:
        pass
    candidates += [SYSTEM_PLUGINS_DIR / plugin_name, USER_PLUGINS_DIR_WEB / plugin_name]
    for plugin_dir in candidates:
        try:
            plugin_dir = plugin_dir.resolve()

            # Each lane is contained to ITS OWN subtree — `app/../` must not
            # reach the plugin root (room JSONs, engine code, keys live there).
            # Lane bases are RESOLVED like the candidate, or a symlinked lane
            # dir can never match its own base (post-fix review 2026-08-05).
            if path.startswith("app/"):
                resp = _serve_contained((plugin_dir / path).resolve(),
                                        (plugin_dir / "app").resolve())
                if resp:
                    return resp
                continue

            # Story-pack art: stories/<slug>/backdrops/*.jpg — packs are plugins
            # with a stories/ dir, and their scene art is web-facing by design.
            # IMAGES ONLY: the room JSONs beside them carry puzzle solutions and
            # stay engine-side. 2026-08-03.
            if path.startswith("stories/"):
                file_path = (plugin_dir / path).resolve()
                if file_path.suffix.lower() in _STORY_ART_SUFFIXES:
                    resp = _serve_contained(file_path, (plugin_dir / "stories").resolve())
                    if resp:
                        return resp
                continue

            # Otherwise serve from web/ subdirectory (existing behavior)
            web_dir = (plugin_dir / "web").resolve()
            resp = _serve_contained((web_dir / path).resolve(), web_dir)
            if resp:
                return resp
        except (OSError, ValueError):
            # embedded null byte / unreadable candidate — try the next one
            continue
    return JSONResponse({"error": "Not found"}, status_code=404)

# ── CDN cache proxy ─────────────────────────────────────────────────────────
# Plugins that need browser-side libraries (three.js, etc.) can import via
# /cdn-cache/<host>/<path> instead of hitting esm.sh/jsdelivr/unpkg directly.
# First request fetches + caches to user/cdn_cache/<sha256>; subsequent
# requests serve from disk. Cache is permanent — CDN URLs are version-pinned
# (e.g. three@0.170.0), so stale isn't a concern. Allowlist prevents the
# endpoint from becoming an open relay. Added 2026-05-13 — avatar was the
# only CDN consumer; drives' Chart.js dep was removed earlier today.

CDN_HOST_ALLOWLIST = {"esm.sh", "cdn.jsdelivr.net", "unpkg.com"}
CDN_CACHE_DIR = (PROJECT_ROOT / "user" / "cdn_cache").resolve()
CDN_FETCH_TIMEOUT = 30.0

# Regex: match quoted root-relative module paths inside JS bodies that look
# like CDN package references — paths that start with `/` and contain
# `@version` somewhere. Conservative — won't rewrite arbitrary `/foo/bar`
# strings, only paths with the @digit signature CDNs use for pinned versions.
import re as _re
_CDN_PATH_REWRITE_RE = _re.compile(
    r'''(["'])(/[^"'\s]*?@\d[^"'\s]*?)(["'])'''
)


def _rewrite_cdn_paths(body_bytes: bytes, host: str) -> bytes:
    """Rewrite root-relative paths in JS module responses to flow back through
    /cdn-cache/<host>/. esm.sh's `?bundle` output references internal files
    via root-relative imports (e.g. `from "/three@0.170.0/es2022/three.bundle.mjs"`)
    which would otherwise resolve against the document origin and 404.
    """
    try:
        body_str = body_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return body_bytes  # binary — don't touch
    rewritten = _CDN_PATH_REWRITE_RE.sub(
        lambda m: f'{m.group(1)}/cdn-cache/{host}{m.group(2)}{m.group(3)}',
        body_str,
    )
    return rewritten.encode("utf-8")


@app.get("/cdn-cache/{path:path}")
async def serve_cdn_cache(request: Request, path: str, _=Depends(require_login)):
    """Local cache proxy for whitelisted CDN libraries.

    Path: <host>/<remaining-url-path>, query string preserved.
    Cache key: SHA256 of full upstream URL (including query).
    JS responses get root-relative module paths rewritten so subimports
    flow back through us — necessary for esm.sh's bundle format.
    """
    import hashlib as _hashlib
    from fastapi.responses import Response as _Response

    if "/" not in path:
        host, rest = path, ""
    else:
        host, rest = path.split("/", 1)
    if host not in CDN_HOST_ALLOWLIST:
        raise HTTPException(status_code=400, detail=f"host not allowed: {host}")

    query = request.url.query or ""
    upstream_url = f"https://{host}/{rest}"
    if query:
        upstream_url = f"{upstream_url}?{query}"

    key = _hashlib.sha256(upstream_url.encode("utf-8")).hexdigest()
    cache_file = CDN_CACHE_DIR / key
    meta_file = CDN_CACHE_DIR / f"{key}.ct"

    # Cache hit
    if cache_file.exists() and meta_file.exists():
        try:
            content_type = meta_file.read_text(encoding="utf-8").strip()
        except Exception:
            content_type = "application/octet-stream"
        return _Response(content=cache_file.read_bytes(),
                         media_type=content_type or "application/octet-stream")

    # Cache miss — fetch upstream. SSRF prevention: don't follow redirects
    # blindly; resolve each hop manually so we can re-validate the target
    # host against the allowlist. A compromised or sloppily-configured CDN
    # could otherwise redirect to 127.0.0.1, metadata endpoints, or internal
    # services running on the same VPS. Hop cap = 5 (sane). 2026-05-13.
    try:
        import httpx as _httpx
        from urllib.parse import urljoin, urlparse

        current_url = upstream_url
        hops = 0
        async with _httpx.AsyncClient(timeout=CDN_FETCH_TIMEOUT) as client:
            while True:
                r = await client.get(current_url, follow_redirects=False)
                if r.status_code in (301, 302, 303, 307, 308):
                    if hops >= 5:
                        raise HTTPException(status_code=502,
                                            detail="cdn redirect chain too long")
                    location = r.headers.get("location", "")
                    if not location:
                        break
                    next_url = urljoin(current_url, location)
                    next_host = urlparse(next_url).hostname or ""
                    if next_host not in CDN_HOST_ALLOWLIST:
                        raise HTTPException(
                            status_code=502,
                            detail=f"cdn redirect to disallowed host: {next_host}",
                        )
                    current_url = next_url
                    hops += 1
                    continue
                break

        if r.status_code != 200:
            raise HTTPException(status_code=502,
                                detail=f"upstream {host} returned {r.status_code}")
        content = r.content
        content_type = r.headers.get("content-type", "application/octet-stream")

        # Rewrite root-relative module paths in JS responses so subimports
        # flow back through the proxy.
        if "javascript" in content_type.lower() or "ecmascript" in content_type.lower():
            content = _rewrite_cdn_paths(content, host)

        # Atomic write
        CDN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = cache_file.with_suffix(".tmp")
        tmp.write_bytes(content)
        replace_with_retry(tmp, cache_file)
        meta_file.write_text(content_type, encoding="utf-8")
        return _Response(content=content, media_type=content_type)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"cdn fetch failed: {e}")


# Avatar assets (user/avatar/)
@app.get("/api/avatar/{filename}")
async def serve_avatar_asset(filename: str, _=Depends(require_login)):
    """Serve avatar files from user/avatar/."""
    avatar_dir = (PROJECT_ROOT / "user" / "avatar").resolve()
    file_path = (avatar_dir / filename).resolve()
    if not str(file_path).startswith(str(avatar_dir)) or not file_path.exists():
        return JSONResponse({"error": "Not found"}, status_code=404)
    content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    return FileResponse(file_path, media_type=content_type)

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
    # Real path containment, not string prefix — the same sibling-prefix bug
    # fixed in /plugin-web lived on here (verified live: %2e%2e reached a
    # sibling of the project dir; post-fix review 2026-08-05).
    try:
        workspace_base = Path(os.path.expanduser(ws_dir)).resolve()
        project_dir = (workspace_base / project).resolve()
        if not project_dir.is_relative_to(workspace_base):
            return JSONResponse({"error": "Not found"}, status_code=404)
        file_path = (project_dir / path).resolve()
        if not file_path.is_relative_to(project_dir):
            return JSONResponse({"error": "Not found"}, status_code=404)
        if file_path.is_file():
            content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
            return FileResponse(file_path, media_type=content_type)
    except (OSError, ValueError):
        pass
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
    """Log incoming requests; stamp the tab's session id into request context
    (core/request_context.py) so publishers can mark their events' origin."""
    from core.request_context import session_origin
    session_origin.set(request.headers.get('X-Session-ID'))
    logger.debug(f"REQ: {request.method} {request.url.path}")
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

    # Permissive CSP — defense in depth around the community-content surfaces.
    # 'unsafe-inline' on script-src and style-src is required because Sapphire's
    # templates use inline <script> and inline style="" attributes throughout
    # (see login.html, setup.html, index.html). Even with that allowance, the
    # CSP still blocks the high-impact attacks:
    #   - connect-src 'self' prevents any XSS from exfiltrating session cookie
    #     via fetch/XHR to an attacker domain
    #   - frame-ancestors 'none' blocks clickjacking
    #   - default-src 'self' blocks surprise external resource loads
    #
    # Allowed external script CDNs: esm.sh, jsdelivr, unpkg — the standard
    # ESM hosts plugin authors use to load runtime deps (e.g. avatar plugin
    # imports three.js from esm.sh). Plugins using exotic CDNs need to vendor
    # or use these.
    #
    # blob: in media-src is required for TTS playback — `URL.createObjectURL`
    # returns blob: URLs that <audio src> consumes. blob: in img-src covers
    # generated-image surfaces (image-gen plugin and similar).
    #
    # blob: in connect-src is required for three.js GLTFLoader to decode
    # embedded GLB textures: it extracts each image as a Blob, wraps it in
    # `URL.createObjectURL`, then `ImageBitmapLoader` calls `fetch(blobUrl)`
    # on it. Without `blob:` in connect-src, that fetch is silently
    # CSP-blocked, the texture never binds, and the avatar renders with
    # white surfaces (geometry + animation still work because the GLB
    # buffer load is XHR to same-origin /api/avatar/<file>). 2026-05-11.
    # blob: in worker-src is cheap insurance for any future loader that
    # spawns a worker from a blob (KTX2 transcoder, Draco mesh decoder,
    # basis-universal, etc.). blob: URLs are ephemeral and same-origin
    # by construction — no exfiltration risk added.
    #
    # Tightening to strict CSP requires cleaning up the inline handlers across
    # the codebase first — out of scope.
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://esm.sh https://cdn.jsdelivr.net https://unpkg.com; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob: https:; "
        "media-src 'self' blob:; "
        "frame-src https://www.youtube-nocookie.com; "
        "font-src 'self' data:; "
        "connect-src 'self' blob:; "
        "worker-src 'self' blob:; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )

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
# Use a dedicated session secret file (not the password hash) so sessions survive
# password changes and are stable from first boot through setup completion.
def _get_session_secret():
    from core.setup import CONFIG_DIR
    secret_file = CONFIG_DIR / 'session_secret'
    if secret_file.exists():
        try:
            val = secret_file.read_text().strip()
            if val:  # Guard against empty/truncated file from crash
                return val
        except Exception:
            pass
    # Generate and persist a new secret (atomic write)
    secret = secrets.token_hex(32)
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp_path = secret_file.with_suffix('.tmp')
        tmp_path.write_text(secret)
        import sys
        if sys.platform != 'win32':
            import os as _os
            _os.chmod(tmp_path, 0o600)
        replace_with_retry(tmp_path, secret_file)
    except Exception:
        pass  # Falls back to ephemeral secret (session won't survive restart)
    return secret

app.add_middleware(
    SessionMiddleware,
    secret_key=_get_session_secret(),
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
    # Starlette 0.30+ requires request as first positional arg
    request = context.get("request")
    try:
        resp = templates.TemplateResponse(request, template, context=context)
    except TypeError:
        resp = templates.TemplateResponse(name=template, context=context)
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


def _default_theme():
    """Boot-fallback theme id from themes.json 'default' (was dead config —
    lifecycle #15). Template-stamped into the FOUC guard."""
    try:
        data = json.loads((STATIC_DIR / "themes" / "themes.json").read_text(encoding='utf-8'))
        d = data.get("default")
        if isinstance(d, str) and re.fullmatch(r'[a-z0-9_-]{1,50}', d):
            return d
    except Exception:
        pass
    return "dark"


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
        "import_map": IMPORT_MAP,
        "default_theme": _default_theme(),
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

    # CSRF check
    csrf_token = form.get('csrf_token')
    if not validate_csrf(request, csrf_token):
        logger.warning(f"CSRF validation failed on setup from {client_ip}")
        return RedirectResponse(url="/setup?error=csrf", status_code=302)

    password = form.get('password', '')
    confirm = form.get('confirm', '')

    if not password:
        return RedirectResponse(url="/setup?error=empty", status_code=302)
    if len(password) < 10:
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
        # Rotate session state before promoting to authenticated. Prevents
        # session-fixation — a pre-login cookie an attacker could have planted
        # (LAN XSS on another localhost app, stale iframe, etc) gets cleared
        # before we stamp logged_in. 2026-04-22 M5 fix.
        request.session.clear()
        request.session['logged_in'] = True
        # Salt stamp: require_login accepts this cookie only while the live
        # hash still carries the same salt (password change / reset evicts
        # every other session). 2026-09-08.
        request.session['pw'] = password_hash[:29]
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


# SWITCH MEANS APPLY (2026-08-22): every runtime apply serializes here.
# Until now all six call sites sat inside async route handlers, so the event
# loop serialized them by accident; the on_switched hook also fires from the
# vault's idle-lock Timer thread and the stream-end worker, so the accident
# becomes a lock. RLock: reapply_if_active → _apply_chat_settings nesting is
# legal.
_apply_lock = threading.RLock()


def _apply_chat_settings(system, settings: dict):
    """Apply chat settings to the system (TTS, prompt, ability, state engine).
    Each section is isolated so one failure doesn't skip the rest.
    Serialized by _apply_lock (see above)."""
    with _apply_lock:
        _apply_chat_settings_unlocked(system, settings)


def apply_on_switch(system, name: str, settings: dict, gen: int):
    """ChatSessionManager.on_switched body — installed post-plugin-scan by
    sapphire.py. Takes the apply lock FIRST, then re-checks the store's
    switch generation: two switches racing (story entry fires two
    overlapping activates; an idle-lock eviction can land under a dropdown
    click) must apply in order or not at all — a stale apply would leave the
    store on chat B with the brain on chat A, the exact desync this hook
    exists to close. `settings` is the snapshot the store captured under its
    own lock; never re-read it here."""
    sm = system.llm_chat.session_manager
    with _apply_lock:
        if gen != getattr(sm, '_switch_gen', gen):
            logger.info(f"Switch apply for '{name}' superseded — skipped")
            return
        _apply_chat_settings_unlocked(system, settings)


def _apply_chat_settings_unlocked(system, settings: dict):
    try:
        if "voice" in settings:
            voice = _validate_tts_voice(settings["voice"])
            system.tts.set_voice(voice)
        if "pitch" in settings:
            system.tts.set_pitch(settings["pitch"])
        if "speed" in settings:
            system.tts.set_speed(settings["speed"])
    except Exception as e:
        logger.error(f"Error applying TTS settings: {e}")

    try:
        if "prompt" in settings:
            prompt_name = settings["prompt"]
            prompt_data = prompts.get_prompt(prompt_name)
            # Existence is "prompt_data is a dict", NOT "content is truthy".
            # The 'blank' prompt is an intentional empty-content prompt — it
            # exists so users can run with NO system prompt. Treating empty
            # content as "missing" silently kept the previous prompt loaded
            # (Sapphire) and made `blank` a no-op. 2026-04-27 fix.
            if isinstance(prompt_data, dict):
                content = prompt_data.get('content', '') or ''
                # Pieces BEFORE the live snapshot (C-5): a preset failing
                # validation keeps the previous prompt and trackers intact.
                ok = True
                if hasattr(prompts.prompt_manager, 'scenario_presets') and prompt_name in prompts.prompt_manager.scenario_presets:
                    ok = prompts.apply_scenario(prompt_name)
                if ok:
                    system.llm_chat.set_system_prompt(content)
                    prompts.set_active_preset_name(prompt_name)
                    logger.info(f"Applied prompt: {prompt_name}{' (empty content — blank mode)' if not content else ''}")
                else:
                    logger.error(f"Preset '{prompt_name}' failed to apply — keeping previous prompt")
            else:
                # Prompt not registered right now — run on 'default' for THIS
                # turn, but never rewrite the chat's setting. "Missing" is
                # usually transient: a plugin-provided prompt (a story costume
                # rendered at runtime) is absent for the window between a
                # reload and its re-registration. The old H3 behavior
                # (2026-04-22) persisted the fallback, which turned that
                # window into permanent loss of user intent — Sapphire woke up
                # as 'default' mid-story and the costume could never come back
                # (Sapph-not-Rose, 2026-08-05). Runtime falls back; the
                # setting is the user's, and it stays.
                logger.warning(
                    f"Chat references unknown prompt '{prompt_name}' — running on "
                    f"'default' this turn; the chat's setting is left intact so it "
                    f"heals if the prompt re-registers."
                )
                # 'default' is the assembled-mode SENTINEL, not a prompt name —
                # get_prompt('default') returns None on every stock install, which
                # made this fallback dead code (post-fix review 2026-08-05: she
                # silently kept the PREVIOUS chat's prompt). get_current_prompt()
                # is the one API that resolves the sentinel; set the preset name
                # first because it reads it.
                prompts.set_active_preset_name('default')
                _fb = prompts.get_current_prompt()
                _fb_content = (_fb or {}).get('content') if isinstance(_fb, dict) else ''
                system.llm_chat.set_system_prompt(_fb_content or '')
                try:
                    # Ephemeral (2026-08-22): a transient notice for live
                    # tabs, never replayed — this apply now also runs inside
                    # the vault-lock eviction, three lines after lock()
                    # cleared the replay ring to keep sealed names out of it.
                    publish(Events.SETTINGS_CHANGED, {
                        "key": "chat_prompt_fallback",
                        "value": "default",
                        "reason": f"missing:{prompt_name}",
                    }, ephemeral=True)
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"Error applying prompt settings: {e}")

    try:
        # Reset before apply so scopes not present in this chat's settings fall
        # back to defaults instead of inheriting the previous chat's values.
        # Matches the pattern used in chat.py, chat_streaming.py, and
        # continuity/execution_context.py.
        from core.chat.function_manager import apply_scopes_from_settings, reset_scopes
        reset_scopes()
        apply_scopes_from_settings(system.llm_chat.function_manager, settings)
        # Align RAG scope with the active chat — chat.py/chat_streaming.py set this
        # per-request, but routes that only activate a chat (no message sent) left
        # scope_rag pointing at the previous chat's documents.
        try:
            chat_name = system.llm_chat.session_manager.get_active_chat_name()
            if chat_name:
                system.llm_chat.function_manager.set_rag_scope(f"__rag__:{chat_name}")
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Error applying scope settings: {e}")

    try:
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
    except Exception as e:
        logger.error(f"Error applying spice set: {e}")

    try:
        toolset_key = "toolset" if "toolset" in settings else "ability" if "ability" in settings else None
        if toolset_key:
            toolset_name = settings[toolset_key]
            extras = settings.get("extra_toolsets") or None
            system.llm_chat.function_manager.update_enabled_functions([toolset_name], extra_toolsets=extras)
            logger.info(f"Applied toolset: {toolset_name}" + (f" + extras {extras}" if extras else ""))
            publish(Events.TOOLSET_CHANGED, {"name": toolset_name})
    except Exception as e:
        logger.error(f"Error applying toolset: {e}")


def reapply_if_active(system, domain: str, name: str):
    """Hot-reload a saveable thing into the active chat's runtime state.

    When a user edits a toolset/prompt/persona that the active chat is
    currently using, saving the file alone does not refresh the in-memory
    runtime — function_manager._enabled_tools, current_system_prompt, etc.
    stay stale until re-activation. This helper closes that gap.

    No-op when the active chat doesn't reference `name`. Wrapped in a broad
    try/except so a hot-reload failure never breaks the save response.

    Remmi/Zeebs field report 2026-04-23: editing an active toolset to add a
    newly-registered plugin tool looked like it worked (file saved) but the
    tool call returned "not currently available" until re-Activate. This
    makes the edit land on the first save, as users reasonably expect.
    """
    try:
        chat_settings = system.llm_chat.session_manager.get_chat_settings() or {}
        if chat_settings.get(domain) != name:
            return
        if domain == 'toolset':
            system.llm_chat.function_manager.update_enabled_functions(
                [name], extra_toolsets=chat_settings.get('extra_toolsets') or None)
            publish(Events.TOOLSET_CHANGED, {"name": name})
        elif domain == 'prompt':
            data = prompts.get_prompt(name)
            # Same fix as _apply_chat_settings: existence is "is dict",
            # not "content truthy". An intentionally-empty prompt (the
            # 'blank' prompt) must hot-reload correctly when edited.
            # 2026-04-27 fix.
            if isinstance(data, dict):
                # Re-run apply_scenario for assembled presets — setting
                # content alone left _assembled_state on the OLD pieces, and
                # the next spice rotation reassembled from them, silently
                # reverting the edit. Runs BEFORE set_system_prompt (C-5):
                # a bad edit keeps the previous prompt live instead of
                # half-applying.
                if name in prompts.prompt_manager.scenario_presets:
                    if not prompts.apply_scenario(name):
                        logger.error(f"Hot-reload: edited preset '{name}' failed "
                                     f"validation — previous prompt kept")
                        return
                system.llm_chat.set_system_prompt(data.get('content', '') or '')
                publish(Events.PROMPT_CHANGED, {"name": name, "action": "reapplied"})
        elif domain == 'persona':
            # Persona is a bundle; rerun the full apply so prompt/toolset/
            # voice/scopes all sync to the edited persona's settings.
            from core.personas import persona_manager
            persona = persona_manager.get(name)
            if persona:
                settings = persona.get("settings", {}).copy()
                settings["persona"] = name
                _apply_chat_settings(system, settings)
        logger.info(f"Hot-reload: re-applied {domain} '{name}' to active chat")
    except Exception as e:
        logger.warning(f"Hot-reload {domain}='{name}' failed: {e}")


# =============================================================================
# ROUTE MODULES
# =============================================================================

from core.routes.chat import router as chat_router
from core.routes.tts import router as tts_router
from core.routes.settings import router as settings_router
from core.routes.content import router as content_router
from core.routes.knowledge import router as knowledge_router
from core.routes.system import router as system_router
from core.routes.plugins import router as plugins_router
from core.routes.media import router as media_router
from core.routes.perception import router as perception_router
from core.routes.agents import router as agents_router
from core.routes.docs import router as docs_router
from core.routes.store import router as store_router
from core.routes.dashboard import router as dashboard_router
from core.routes.body import router as body_router
from core.routes.videos import router as videos_router
from core.routes.backgrounds import router as backgrounds_router
from core.routes.fonts import router as fonts_router
from core.routes.conversation import router as conversation_router
from core.routes.vault import router as vault_router

app.include_router(chat_router)
app.include_router(tts_router)
app.include_router(settings_router)
app.include_router(content_router)
app.include_router(knowledge_router)
app.include_router(system_router)
app.include_router(plugins_router)
app.include_router(media_router)
app.include_router(perception_router)
app.include_router(agents_router)
app.include_router(docs_router)
app.include_router(store_router)
app.include_router(dashboard_router)
app.include_router(body_router)
app.include_router(videos_router)
app.include_router(backgrounds_router)
app.include_router(fonts_router)
app.include_router(conversation_router)
app.include_router(vault_router)

