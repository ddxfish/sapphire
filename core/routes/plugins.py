# core/routes/plugins.py - Plugin management, plugin-specific settings, plugin route dispatcher
import asyncio
import json
import os
import tempfile
import threading
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse, Response

import config
from core.auth import require_login, check_endpoint_rate
from core.api_fastapi import get_system

logger = logging.getLogger(__name__)

router = APIRouter()

# Per-plugin toggle locks to prevent double-click races
_toggle_locks: dict[str, threading.Lock] = {}
_toggle_locks_guard = threading.Lock()

# Module-level mutex for the read-modify-write of user/webui/plugins.json.
# Per-plugin locks above don't help when concurrent toggles target DIFFERENT
# plugins — both reads see the same disk snapshot, both writes replace the
# file, the second writer's payload was computed from pre-first-write state,
# so the first toggle vanishes from disk silently. This guards the disk file
# itself across plugin names. Witch-hunt 2026-04-21 finding H10.
_user_plugins_file_mutex = threading.Lock()

PROJECT_ROOT = Path(__file__).parent.parent.parent
STATIC_DIR = PROJECT_ROOT / "interfaces" / "web" / "static"

_install_lock = threading.Lock()

# Plugin settings paths
USER_WEBUI_DIR = PROJECT_ROOT / 'user' / 'webui'
USER_PLUGINS_JSON = USER_WEBUI_DIR / 'plugins.json'
USER_PLUGIN_SETTINGS_DIR = USER_WEBUI_DIR / 'plugins'
LOCKED_PLUGINS = []


def _enforce_locked(result):
    """Ensure LOCKED_PLUGINS are always in the enabled list."""
    for locked in LOCKED_PLUGINS:
        if locked not in result["enabled"]:
            result["enabled"].append(locked)
    return result


def _get_merged_plugins():
    """Merge static and user plugins.json into the shape the UI consumes.

    The `enabled` list reflects what's ACTUALLY RUNNING, not just what's in
    the user file. Backends with `default_enabled: true` + no explicit user
    toggle are live via scan() semantics but won't appear in the user file
    until the user clicks the toggle — previously this meant /api/init's
    `plugins_config.enabled` missed them, and the frontend's
    `enabledPlugins` Set (which drives scope-dropdown visibility in
    scope-dropdowns.js:64) hid their scopes. Tools worked via SCOPE_REGISTRY
    but the Mind dropdowns ghosted. Toggle-off-then-on wrote the plugin to
    disk and the mismatch resolved. TODO L132 — 2026-04-21.
    """
    static_plugins_json = STATIC_DIR / 'core-ui' / 'plugins.json'
    try:
        with open(static_plugins_json, encoding='utf-8') as f:
            static = json.load(f)
    except Exception:
        static = {"enabled": [], "plugins": {}}

    if USER_PLUGINS_JSON.exists():
        try:
            with open(USER_PLUGINS_JSON, encoding='utf-8') as f:
                user = json.load(f)
        except Exception:
            user = None
    else:
        user = None

    if user is None:
        merged = {
            "enabled": list(static.get("enabled", [])),
            "plugins": dict(static.get("plugins", {}))
        }
    else:
        merged = {
            "enabled": list(user.get("enabled", static.get("enabled", []))),
            "plugins": dict(static.get("plugins", {})),
        }
        if "plugins" in user:
            merged["plugins"].update(user["plugins"])

    # Augment with runtime-enabled plugins the user file doesn't yet list
    # (default_enabled semantics). The plugin_loader is the source of truth
    # for what's actually active; merge its view into the returned dict so
    # frontend and backend agree.
    #
    # Snapshot under `_lock` and iterate the snapshot — concurrent
    # rescan/uninstall pop entries under the same lock, so iterating the live
    # dict can raise `RuntimeError: dictionary changed size during iteration`,
    # which the outer except would swallow into a half-populated `enabled`
    # list and frontend's `enabledPlugins` Set would silently hide scopes.
    # Witch-hunt 2026-04-21 finding H7.
    try:
        from core.plugin_loader import plugin_loader
        with plugin_loader._lock:
            snapshot = list(plugin_loader._plugins.items())
        disk = set(merged["enabled"])
        for name, info in snapshot:
            if info.get("loaded") and info.get("enabled") and name not in disk:
                merged["enabled"].append(name)
    except Exception:
        # plugin_loader may not be initialized during very early boot — in
        # that case the disk state is what we've got; ship it.
        pass

    return _enforce_locked(merged)


@router.get("/api/webui/plugins")
async def list_plugins(request: Request, _=Depends(require_login)):
    """List all plugins (core-ui + backend plugins)."""
    merged = _get_merged_plugins()
    enabled_set = set(merged.get("enabled", []))

    result = []
    seen = set()
    for name, meta in merged.get("plugins", {}).items():
        result.append({
            "name": name,
            "enabled": name in enabled_set,
            "locked": name in LOCKED_PLUGINS,
            "title": meta.get("title", name),
            "showInSidebar": meta.get("showInSidebar", True),
            "collapsible": meta.get("collapsible", True),
            "settingsUI": "core"
        })
        seen.add(name)

    # Include backend plugins discovered by plugin_loader
    try:
        from core.plugin_loader import plugin_loader
        for info in plugin_loader.get_all_plugin_info():
            if info["name"] not in seen:
                manifest = info.get("manifest", {})
                plugin_dir = info.get("path", "")
                has_web = (Path(plugin_dir) / "web" / "index.js").exists() if plugin_dir else False
                has_script = (Path(plugin_dir) / "web" / "main.js").exists() if plugin_dir else False
                settings_schema = manifest.get("capabilities", {}).get("settings")
                # Respect manifest settingsUI — null means no separate settings page
                manifest_ui = manifest.get("settingsUI", "auto")
                if manifest_ui is None or manifest_ui == "none":
                    settings_ui = None
                elif manifest_ui in ("plugin", "manifest", "core"):
                    settings_ui = manifest_ui
                elif has_web:
                    settings_ui = "plugin"
                elif settings_schema:
                    settings_ui = "manifest"
                else:
                    settings_ui = None
                result.append({
                    "name": info["name"],
                    "enabled": info.get("enabled", info["name"] in enabled_set),
                    "locked": False,
                    "title": manifest.get("short_name") or manifest.get("description", info["name"]).split("—")[0].strip(),
                    "showInSidebar": False,
                    "collapsible": True,
                    "settingsUI": settings_ui,
                    "settings_schema": settings_schema,
                    "verified": info.get("verified"),
                    "verify_msg": info.get("verify_msg"),
                    "verify_tier": info.get("verify_tier", "unsigned"),
                    "verified_author": info.get("verified_author"),
                    "url": manifest.get("url"),
                    "version": manifest.get("version"),
                    "author": manifest.get("author"),
                    "icon": manifest.get("icon"),
                    "band": info.get("band"),
                    "has_script": has_script,
                    "sidebar_accordion": manifest.get("capabilities", {}).get("sidebar_accordion"),
                    "missing_deps": info.get("missing_deps", []),
                    "essential": manifest.get("essential", False),
                })
    except Exception:
        pass

    return {"plugins": result, "locked": LOCKED_PLUGINS}


@router.put("/api/webui/plugins/toggle/{plugin_name}")
async def toggle_plugin(plugin_name: str, request: Request, _=Depends(require_login)):
    """Toggle a plugin."""
    if plugin_name in LOCKED_PLUGINS:
        raise HTTPException(status_code=403, detail=f"Cannot disable locked plugin: {plugin_name}")

    # Per-plugin lock prevents double-click races
    with _toggle_locks_guard:
        if plugin_name not in _toggle_locks:
            _toggle_locks[plugin_name] = threading.Lock()
        lock = _toggle_locks[plugin_name]
    if not lock.acquire(blocking=False):
        return {"status": "success", "plugin": plugin_name, "enabled": None, "reload_required": False, "note": "toggle already in progress"}

    try:
        merged = _get_merged_plugins()
        # Accept both static (plugins.json) and backend (plugin_loader) plugins
        known = set(merged.get("plugins", {}).keys())
        try:
            from core.plugin_loader import plugin_loader
            known.update(info["name"] for info in plugin_loader.get_all_plugin_info())
        except Exception:
            pass
        if plugin_name not in known:
            raise HTTPException(status_code=404, detail=f"Unknown plugin: {plugin_name}")

        # Read-modify-write of the user plugins.json must be atomic across
        # plugin names — concurrent toggles of TWO different plugins both
        # read the same disk snapshot and the second writer's payload was
        # computed before the first writer's change landed, silently losing
        # one toggle. Module-level `_user_plugins_file_mutex` guards the
        # whole RMW. Witch-hunt 2026-04-21 finding H10.
        USER_WEBUI_DIR.mkdir(parents=True, exist_ok=True)
        with _user_plugins_file_mutex:
            user_data = {}
            if USER_PLUGINS_JSON.exists():
                try:
                    with open(USER_PLUGINS_JSON, encoding='utf-8') as f:
                        user_data = json.load(f)
                except Exception:
                    pass
            enabled = list(user_data.get("enabled", []))
            disabled = list(user_data.get("disabled", []))

            # Determine current state from plugin_loader (handles default_enabled
            # plugins that aren't in the persisted enabled list).
            currently_enabled = plugin_name in enabled
            try:
                from core.plugin_loader import plugin_loader as _pl
                info = _pl.get_plugin_info(plugin_name)
                if info:
                    currently_enabled = info["enabled"]
            except Exception:
                pass

            if currently_enabled:
                if plugin_name in enabled:
                    enabled.remove(plugin_name)
                # Record explicit disable so default_enabled plugins stay off across reboots
                if plugin_name not in disabled:
                    disabled.append(plugin_name)
                new_state = False
            else:
                if plugin_name not in enabled:
                    enabled.append(plugin_name)
                if plugin_name in disabled:
                    disabled.remove(plugin_name)
                new_state = True

            user_data["enabled"] = enabled
            user_data["disabled"] = disabled
            tmp_path = USER_PLUGINS_JSON.with_suffix('.tmp')
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(user_data, f, indent=2)
            tmp_path.replace(USER_PLUGINS_JSON)

        # Live load/unload — no restart needed for backend plugins
        reload_required = True
        try:
            from core.plugin_loader import plugin_loader
            if plugin_name in plugin_loader._plugins:
                # Acquire reload lock to serialize against file watcher / reload API
                with plugin_loader._get_reload_lock(plugin_name):
                    if new_state:
                        with plugin_loader._lock:
                            plugin_loader._plugins[plugin_name]["enabled"] = True
                        # Re-verify signature on toggle-on — files may have been
                        # tampered between the original scan and now (e.g. user
                        # disabled, edited plugin.json, re-enabled). Mirrors
                        # the re-verify reload_plugin already does.
                        try:
                            from core.plugin_verify import verify_plugin
                            from pathlib import Path as _Path
                            plugin_path = _Path(plugin_loader._plugins[plugin_name]["path"])
                            verified, verify_msg, verify_meta = verify_plugin(plugin_path)
                            with plugin_loader._lock:
                                plugin_loader._plugins[plugin_name]["verified"] = verified
                                plugin_loader._plugins[plugin_name]["verify_msg"] = verify_msg
                                plugin_loader._plugins[plugin_name]["verified_author"] = verify_meta.get("author")
                        except Exception as _verr:
                            logger.warning(f"[PLUGINS] toggle re-verify failed for {plugin_name}: {_verr}")
                        loaded = plugin_loader._load_plugin(plugin_name)
                        if not loaded:
                            # Load failed (verification/deps). Leave plugins.json
                            # alone — user intent (enabled) survives so a fix +
                            # restart reactivates automatically. In-memory state
                            # reflects reality so UI shows plugin as off.
                            with plugin_loader._lock:
                                plugin_loader._plugins[plugin_name]["enabled"] = False
                                verify_msg = plugin_loader._plugins[plugin_name].get("verify_msg", "unknown")
                            if "unsigned" in verify_msg:
                                detail = "Unsigned plugin — enable 'Allow Unsigned Plugins' first"
                            elif "hash mismatch" in verify_msg or "tamper" in verify_msg.lower():
                                detail = "Plugin signature is invalid — files were modified after signing"
                            else:
                                detail = f"Plugin blocked: {verify_msg}"
                            raise HTTPException(status_code=403, detail=detail)
                    else:
                        plugin_loader.unload_plugin(plugin_name)
                        with plugin_loader._lock:
                            plugin_loader._plugins[plugin_name]["enabled"] = False
                reload_required = False

                # Re-sync toolset so enabled functions reflect the plugin change
                try:
                    system = get_system()
                    if system and hasattr(system, 'llm_chat'):
                        toolset_info = system.llm_chat.function_manager.get_current_toolset_info()
                        toolset_name = toolset_info.get("name", "custom")
                        system.llm_chat.function_manager.update_enabled_functions([toolset_name])
                        from core.event_bus import publish, Events
                        publish(Events.TOOLSET_CHANGED, {
                            "name": toolset_name,
                            "action": "plugin_toggle",
                            "function_count": toolset_info.get("function_count", 0)
                        })
                except Exception:
                    pass  # Best-effort; tools will sync on next chat
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"Live plugin toggle failed for {plugin_name}: {e}")

        # Check for missing deps after toggle-on
        missing_deps = []
        if new_state:
            try:
                from core.plugin_loader import plugin_loader
                p_info = plugin_loader.get_plugin_info(plugin_name)
                if p_info:
                    missing_deps = p_info.get("missing_deps", [])
            except Exception:
                pass

        return {"status": "success", "plugin": plugin_name, "enabled": new_state,
                "reload_required": reload_required, "missing_deps": missing_deps}
    finally:
        lock.release()


@router.get("/api/apps")
async def list_apps(_=Depends(require_login)):
    """List available plugin apps (plugins with an app/ directory)."""
    from core.plugin_loader import plugin_loader
    apps = []
    for name, info in plugin_loader._plugins.items():
        if not info.get("loaded"):
            continue
        manifest = info.get("manifest", {})
        app_config = manifest.get("capabilities", {}).get("app")
        if not app_config:
            # Also check for app/ dir even without manifest declaration
            app_dir = Path(info["path"]) / "app"
            if not app_dir.exists():
                continue
            app_config = {}
        apps.append({
            "name": name,
            "label": app_config.get("label", manifest.get("display_name", name)),
            "icon": app_config.get("icon", manifest.get("emoji", "")),
            "description": app_config.get("description", manifest.get("description", "")),
            "nav": app_config.get("nav", False),
        })
    return {"apps": apps}


@router.get("/api/themes")
async def list_themes(_=Depends(require_login)):
    """List all available themes — core + plugin manifest themes."""
    themes = []

    # 1. Core themes (static/themes/)
    themes_dir = PROJECT_ROOT / "interfaces" / "web" / "static" / "themes"
    themes_json = themes_dir / "themes.json"
    core_names = []
    if themes_json.exists():
        try:
            data = json.loads(themes_json.read_text(encoding='utf-8'))
            core_names = data.get("themes", [])
        except Exception:
            pass

    for name in core_names:
        css_path = themes_dir / f"{name}.css"
        preview = _extract_css_preview(css_path) if css_path.exists() else {}
        themes.append({
            "id": name,
            "name": name.replace('-', ' ').replace('_', ' ').title(),
            "source": "core",
            "css": f"/static/themes/{name}.css",
            "scripts": [],
            "preview": preview,
        })

    # 2. Plugin manifest themes (capabilities.themes)
    from core.plugin_loader import plugin_loader
    for pname, info in plugin_loader._plugins.items():
        if not info.get("loaded"):
            continue
        manifest = info.get("manifest", {})
        theme_defs = manifest.get("capabilities", {}).get("themes", [])
        for td in theme_defs:
            tid = td.get("id", "")
            if not tid:
                continue
            css_path = td.get("css", "")
            scripts = td.get("scripts", [])
            # Resolve paths relative to plugin web serving
            css_url = f"/plugin-web/{pname}/{css_path}" if css_path else ""
            script_urls = [f"/plugin-web/{pname}/{s}" for s in scripts]
            themes.append({
                "id": f"plugin-{pname}-{tid}",
                "name": td.get("name", tid.title()),
                "icon": td.get("icon", ""),
                "description": td.get("description", ""),
                "source": "plugin",
                "plugin": pname,
                "css": css_url,
                "scripts": script_urls,
                "preview": td.get("preview", {}),
                "settings": td.get("settings", []),
            })

    return {"themes": themes}


def _extract_css_preview(css_path):
    """Parse key CSS variables from a theme file for preview swatches."""
    try:
        text = css_path.read_text(encoding='utf-8')
        import re
        colors = {}
        for var, key in [('--bg:', 'bg'), ('--bg-secondary:', 'bg2'),
                         ('--text:', 'text'), ('--trim:', 'trim'),
                         ('--accent:', 'accent'), ('--border:', 'border')]:
            m = re.search(rf'{re.escape(var)}\s*(#[0-9a-fA-F]{{3,8}}|rgba?\([^)]+\))', text)
            if m:
                colors[key] = m.group(1).strip().rstrip(';')
        # Fallback accent to trim if not found
        if 'trim' in colors and 'accent' not in colors:
            colors['accent'] = colors['trim']
        return colors
    except Exception:
        return {}


@router.post("/api/plugins/rescan")
async def rescan_plugins(_=Depends(require_login)):
    """Scan for new/removed plugin folders without restart."""
    try:
        from core.plugin_loader import plugin_loader
        result = plugin_loader.rescan()
        return {"status": "ok", "added": result["added"], "removed": result["removed"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/plugins/{plugin_name}/reload")
async def reload_plugin(plugin_name: str, _=Depends(require_login)):
    """Hot-reload a plugin (unload + load). For development."""
    from core.plugin_loader import plugin_loader
    info = plugin_loader.get_plugin_info(plugin_name)
    if not info:
        raise HTTPException(status_code=404, detail=f"Unknown plugin: {plugin_name}")
    if not info["enabled"]:
        raise HTTPException(status_code=400, detail=f"Plugin '{plugin_name}' is not enabled")
    try:
        plugin_loader.reload_plugin(plugin_name)
        return {"status": "ok", "plugin": plugin_name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/plugins/install")
async def install_plugin(
    request: Request,
    url: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    force: bool = Form(False),
    _=Depends(require_login),
):
    """Install a plugin from GitHub URL or zip upload."""
    from core.settings_manager import settings
    # Block zip uploads in managed mode (GitHub installs OK — signing gate handles security)
    if settings.is_managed() and file:
        raise HTTPException(status_code=403, detail="Zip upload is disabled in managed mode")
    import shutil
    import zipfile
    import re

    MAX_ZIP_SIZE = 50 * 1024 * 1024  # 50MB
    MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB single file
    MAX_EXTRACTED_SIZE = 100 * 1024 * 1024  # 100MB total

    if not url and not file:
        raise HTTPException(status_code=400, detail="Provide a GitHub URL or zip file")

    from core.plugin_loader import plugin_loader, USER_PLUGINS_DIR

    tmp_zip = None
    tmp_dir = None
    try:
        # ── Download or receive zip ──
        url_install_method = None  # 'github_url' | 'gitlab_url' | 'zip_url' (set below)
        if url:
            import requests as req
            from urllib.parse import urlparse
            clean_url = url.strip()
            # Parse once and reuse. The path-only checks below let URLs with
            # query strings / fragments work — e.g. signed S3 URLs ending
            # `.zip?token=…` or GitHub/GitLab URLs pasted with tracking params.
            # We still send the FULL URL (clean_url) on the actual request so
            # signed-URL tokens reach the server. 2026-04-26 enhancement.
            _parsed = urlparse(clean_url)
            _path_lower = (_parsed.path or '').lower()
            # Strip query/fragment for regex matching against repo URL shapes
            # (GitHub/GitLab repo URLs don't use meaningful params for cloning).
            _url_for_match = f"{_parsed.scheme}://{_parsed.netloc}{_parsed.path}"

            # Direct .zip URL — undocumented fallback for plugin authors without
            # a reachable GitHub. Downstream validation (zip structure, manifest,
            # signing gate) catches bad content. Two guards here against SSRF:
            # require https:// (no plain http) and reject obvious localhost
            # variants. Doesn't catch DNS rebinding or redirect-to-localhost,
            # but the realistic attack surface for a single-user app is small.
            if _path_lower.endswith('.zip') and clean_url.startswith('https://'):
                _lower = clean_url.lower()
                if any(bad in _lower for bad in (
                    '://localhost', '://127.', '://0.0.0.0', '://169.254.',
                    '://[::1]', '://10.', '://192.168.', '://172.16.', '://172.17.',
                    '://172.18.', '://172.19.', '://172.20.', '://172.21.',
                    '://172.22.', '://172.23.', '://172.24.', '://172.25.',
                    '://172.26.', '://172.27.', '://172.28.', '://172.29.',
                    '://172.30.', '://172.31.',
                )):
                    raise HTTPException(status_code=400, detail="Refusing to fetch from localhost / private IP range")
                zip_url = clean_url
                url_install_method = 'zip_url'
                # allow_redirects=False — a 302 from an attacker's https URL to an
                # internal http://127.0.0.1:... would otherwise bypass the localhost
                # and https-only SSRF guards above.
                r = req.get(zip_url, stream=True, timeout=30, allow_redirects=False)
                if r.status_code != 200:
                    raise HTTPException(status_code=400, detail=f"Failed to download zip (HTTP {r.status_code})")
            else:
                # Parse GitHub URL → zip download
                m_gh = re.match(r'https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$', _url_for_match)
                # Parse GitLab URL → zip download. GitLab supports nested subgroups
                # so the pre-repo path can have multiple segments (e.g.
                # gitlab.com/group/subgroup/repo). Capture the full path before
                # the last segment as <gl_path>, last segment as <gl_repo>.
                # 2026-04-26 — GitLab support added.
                m_gl = re.match(r'https?://gitlab\.com/(.+?)/([^/]+?)(?:\.git)?/?$', _url_for_match)
                if m_gh:
                    owner, repo = m_gh.group(1), m_gh.group(2)
                    zip_url = f"https://github.com/{owner}/{repo}/archive/refs/heads/main.zip"
                    url_install_method = 'github_url'
                    # GitHub serves the zip via 302 -> codeload.github.com; explicit allowlist.
                    r = req.get(zip_url, stream=True, timeout=30, allow_redirects=False)
                    if r.status_code in (301, 302, 303, 307, 308):
                        loc = r.headers.get('Location', '')
                        if loc.startswith('https://codeload.github.com/'):
                            r = req.get(loc, stream=True, timeout=30, allow_redirects=False)
                    if r.status_code == 404:
                        zip_url = f"https://github.com/{owner}/{repo}/archive/refs/heads/master.zip"
                        r = req.get(zip_url, stream=True, timeout=30, allow_redirects=False)
                        if r.status_code in (301, 302, 303, 307, 308):
                            loc = r.headers.get('Location', '')
                            if loc.startswith('https://codeload.github.com/'):
                                r = req.get(loc, stream=True, timeout=30, allow_redirects=False)
                    if r.status_code != 200:
                        raise HTTPException(status_code=400, detail=f"Failed to download from GitHub (HTTP {r.status_code})")
                elif m_gl:
                    gl_path, gl_repo = m_gl.group(1), m_gl.group(2)
                    full_path = f"{gl_path}/{gl_repo}"
                    # GitLab archive URL pattern. The archive filename inside the
                    # zip is <repo>-<branch>-<sha>/, which the downstream extract
                    # already handles via the "find plugin.json at root or one
                    # level deep, hoist wrapper" logic. allow_redirects=False to
                    # preserve the SSRF guard pattern from the GitHub branch.
                    zip_url = f"https://gitlab.com/{full_path}/-/archive/main/{gl_repo}-main.zip"
                    url_install_method = 'gitlab_url'
                    r = req.get(zip_url, stream=True, timeout=30, allow_redirects=False)
                    # GitLab may redirect within gitlab.com (e.g. moved repos).
                    # Allow only same-host redirects.
                    if r.status_code in (301, 302, 303, 307, 308):
                        loc = r.headers.get('Location', '')
                        if loc.startswith('https://gitlab.com/'):
                            r = req.get(loc, stream=True, timeout=30, allow_redirects=False)
                    if r.status_code == 404:
                        zip_url = f"https://gitlab.com/{full_path}/-/archive/master/{gl_repo}-master.zip"
                        r = req.get(zip_url, stream=True, timeout=30, allow_redirects=False)
                        if r.status_code in (301, 302, 303, 307, 308):
                            loc = r.headers.get('Location', '')
                            if loc.startswith('https://gitlab.com/'):
                                r = req.get(loc, stream=True, timeout=30, allow_redirects=False)
                    if r.status_code != 200:
                        raise HTTPException(status_code=400, detail=f"Failed to download from GitLab (HTTP {r.status_code})")
                else:
                    raise HTTPException(status_code=400, detail="Invalid URL format. Supported: github.com/<owner>/<repo>, gitlab.com/<path>/<repo>, or a direct https:// .zip URL")
            content_length = int(r.headers.get("Content-Length", 0))
            if content_length > MAX_ZIP_SIZE:
                raise HTTPException(status_code=400, detail=f"Zip too large ({content_length // 1024 // 1024}MB, max 50MB)")
            fd, tmp_path = tempfile.mkstemp(suffix=".zip")
            tmp_zip = Path(tmp_path)
            downloaded = 0
            with os.fdopen(fd, "wb") as f:
                for chunk in r.iter_content(8192):
                    downloaded += len(chunk)
                    if downloaded > MAX_ZIP_SIZE:
                        raise HTTPException(status_code=400, detail="Zip exceeds 50MB limit")
                    f.write(chunk)
        else:
            # File upload
            fd, tmp_path = tempfile.mkstemp(suffix=".zip")
            os.close(fd)
            tmp_zip = Path(tmp_path)
            content = await file.read()
            if len(content) > MAX_ZIP_SIZE:
                raise HTTPException(status_code=400, detail=f"Zip too large ({len(content) // 1024 // 1024}MB, max 50MB)")
            tmp_zip.write_bytes(content)

        # ── Extract ──
        if not zipfile.is_zipfile(tmp_zip):
            raise HTTPException(status_code=400, detail="Not a valid zip file")

        tmp_dir = Path(tempfile.mkdtemp())
        with zipfile.ZipFile(tmp_zip, 'r') as zf:
            # Check uncompressed sizes before extracting (zip bomb protection)
            total_uncompressed = 0
            for info in zf.infolist():
                # Reject symlinks (path traversal vector)
                if (info.external_attr >> 16) & 0o120000 == 0o120000:
                    raise HTTPException(status_code=400, detail=f"Zip contains symlink: {info.filename}")
                # Reject path traversal via ..
                if '..' in info.filename or info.filename.startswith('/'):
                    raise HTTPException(status_code=400, detail=f"Zip contains unsafe path: {info.filename}")
                if info.file_size > MAX_FILE_SIZE:
                    raise HTTPException(status_code=400, detail=f"File too large in zip: {info.filename} ({info.file_size // 1024 // 1024}MB)")
                total_uncompressed += info.file_size
            if total_uncompressed > MAX_EXTRACTED_SIZE:
                raise HTTPException(status_code=400, detail=f"Zip uncompressed size too large ({total_uncompressed // 1024 // 1024}MB, max 100MB)")
            zf.extractall(tmp_dir)

        # ── Find plugin.json (root or one level deep) ──
        plugin_root = None
        if (tmp_dir / "plugin.json").exists():
            plugin_root = tmp_dir
        else:
            for child in tmp_dir.iterdir():
                if child.is_dir() and (child / "plugin.json").exists():
                    plugin_root = child
                    break

        if not plugin_root:
            raise HTTPException(status_code=400, detail="No plugin.json found in zip")

        # ── Validate manifest ──
        try:
            manifest = json.loads((plugin_root / "plugin.json").read_text(encoding="utf-8"))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid plugin.json: {e}")

        name = manifest.get("name")
        version = manifest.get("version")
        description = manifest.get("description")
        author = manifest.get("author", "unknown")
        if not name or not version or not description:
            raise HTTPException(status_code=400, detail="plugin.json must have name, version, and description")

        # Sanitize name — block path traversal
        import re
        if not re.match(r'^[a-zA-Z0-9_-]+$', name):
            raise HTTPException(status_code=400, detail=f"Invalid plugin name: '{name}'. Only alphanumeric, dash, underscore allowed.")

        # ── Name collision checks ──
        # Block system plugins
        if (PROJECT_ROOT / "plugins" / name).exists():
            raise HTTPException(status_code=409, detail=f"'{name}' conflicts with a system plugin")
        # Block core functions
        if (PROJECT_ROOT / "functions" / f"{name}.py").exists():
            raise HTTPException(status_code=409, detail=f"'{name}' conflicts with a core function")
        # Block core-ui plugins
        if (PROJECT_ROOT / "interfaces" / "web" / "static" / "core-ui" / name).exists():
            raise HTTPException(status_code=409, detail=f"'{name}' conflicts with a core UI plugin")

        # ── Size checks on extracted content ──
        total_size = 0
        for f in plugin_root.rglob("*"):
            if f.is_file():
                sz = f.stat().st_size
                if sz > MAX_FILE_SIZE:
                    raise HTTPException(status_code=400, detail=f"File too large: {f.name} ({sz // 1024 // 1024}MB, max 10MB)")
                total_size += sz
        if total_size > MAX_EXTRACTED_SIZE:
            raise HTTPException(status_code=400, detail=f"Extracted content too large ({total_size // 1024 // 1024}MB, max 100MB)")

        # ── Check for existing plugin (replace flow) ──
        # Lock prevents two concurrent installs from corrupting the same plugin
        with _install_lock:
            dest = USER_PLUGINS_DIR / name
            is_update = dest.exists()
            old_version = None
            old_author = None

            if is_update:
                # Read existing manifest for comparison
                existing_manifest_path = dest / "plugin.json"
                if existing_manifest_path.exists():
                    try:
                        existing = json.loads(existing_manifest_path.read_text(encoding="utf-8"))
                        old_version = existing.get("version")
                        old_author = existing.get("author")
                    except Exception:
                        pass

                if not force:
                    return JSONResponse(status_code=409, content={
                        "detail": "Plugin already exists",
                        "name": name,
                        "version": version,
                        "author": author,
                        "existing_version": old_version,
                        "existing_author": old_author,
                    })

                # Unload before replacing
                info = plugin_loader.get_plugin_info(name)
                if info and info.get("loaded"):
                    plugin_loader.unload_plugin(name)

                # Drop stale cache entry so rescan re-reads the new manifest
                with plugin_loader._lock:
                    plugin_loader._plugins.pop(name, None)

                # Delete old plugin dir (state preserved separately).
                # Uses the plugin_loader helper for Windows read-only tolerance.
                from core.plugin_loader import _rmtree_robust
                _rmtree_robust(dest)

            # ── Install ──
            USER_PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copytree(plugin_root, dest, symlinks=False)

        # ── Write install metadata to plugin state ──
        from datetime import datetime
        state = plugin_loader.get_plugin_state(name)
        if url:
            state.save("installed_from", url.strip())
            state.save("install_method", url_install_method or "github_url")
        else:
            state.save("install_method", "zip_upload")
        state.save("installed_at", datetime.utcnow().isoformat() + "Z")

        # ── Rescan to discover the new plugin ──
        plugin_loader.rescan()

        # ── Sync active toolset so new tools are immediately available ──
        system = get_system()
        if system and system.llm_chat:
            fm = system.llm_chat.function_manager
            current = fm.current_toolset_name
            if current:
                fm.update_enabled_functions([current])
            try:
                from core.event_bus import publish, Events
                toolset_info = fm.get_current_toolset_info()
                publish(Events.TOOLSET_CHANGED, {
                    "name": current or "custom",
                    "action": "plugin_install",
                    "function_count": toolset_info.get("function_count", 0)
                })
            except Exception:
                pass

        return {
            "status": "ok",
            "plugin_name": name,
            "version": version,
            "author": author,
            "is_update": is_update,
            "old_version": old_version,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[PLUGINS] Install failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Cleanup temp files
        import shutil
        if tmp_zip and tmp_zip.exists():
            tmp_zip.unlink(missing_ok=True)
        if tmp_dir and tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)


@router.delete("/api/plugins/{plugin_name}/uninstall")
async def uninstall_plugin_endpoint(plugin_name: str, _=Depends(require_login)):
    """Uninstall a user plugin — remove all files, settings, and state."""
    from core.plugin_loader import plugin_loader
    info = plugin_loader.get_plugin_info(plugin_name)
    if not info:
        raise HTTPException(status_code=404, detail=f"Unknown plugin: {plugin_name}")
    if info.get("band") != "user":
        raise HTTPException(status_code=403, detail="Cannot uninstall system plugins")
    try:
        plugin_loader.uninstall_plugin(plugin_name)
        # Sync toolset and notify frontend
        try:
            system = get_system()
            if system and system.llm_chat:
                fm = system.llm_chat.function_manager
                current = fm.current_toolset_name
                if current:
                    fm.update_enabled_functions([current])
                from core.event_bus import publish, Events
                toolset_info = fm.get_current_toolset_info()
                publish(Events.TOOLSET_CHANGED, {
                    "name": current or "custom",
                    "action": "plugin_uninstall",
                    "function_count": toolset_info.get("function_count", 0)
                })
        except Exception:
            pass
        return {"status": "ok", "plugin": plugin_name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/plugins/{plugin_name}/check-update")
async def check_plugin_update(plugin_name: str, _=Depends(require_login)):
    """Check if a newer version is available on GitHub or GitLab."""
    import re
    from core.plugin_loader import plugin_loader

    info = plugin_loader.get_plugin_info(plugin_name)
    if not info:
        raise HTTPException(status_code=404, detail=f"Unknown plugin: {plugin_name}")

    state = plugin_loader.get_plugin_state(plugin_name)
    source_url = state.get("installed_from")
    if not source_url:
        return {"update_available": False, "reason": "no_source"}
    source_url_stripped = source_url.strip()
    # Strip query/fragment for regex matching — defensive in case a stored
    # installed_from URL was saved with tracking params. Match install path.
    from urllib.parse import urlparse
    _src_parsed = urlparse(source_url_stripped)
    _src_for_match = f"{_src_parsed.scheme}://{_src_parsed.netloc}{_src_parsed.path}"

    # Build the list of raw-manifest URLs to try (main + master, GitHub or
    # GitLab). 2026-04-26 — GitLab support added alongside GitHub.
    manifest_urls = []
    m_gh = re.match(r'https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$', _src_for_match)
    m_gl = re.match(r'https?://gitlab\.com/(.+?)/([^/]+?)(?:\.git)?/?$', _src_for_match)
    if m_gh:
        owner, repo = m_gh.group(1), m_gh.group(2)
        for branch in ("main", "master"):
            manifest_urls.append(f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/plugin.json")
    elif m_gl:
        gl_path, gl_repo = m_gl.group(1), m_gl.group(2)
        full_path = f"{gl_path}/{gl_repo}"
        for branch in ("main", "master"):
            manifest_urls.append(f"https://gitlab.com/{full_path}/-/raw/{branch}/plugin.json")
    else:
        return {"update_available": False, "reason": "unsupported_source"}

    current_version = info.get("manifest", {}).get("version", "0.0.0")

    import requests as req
    remote_manifest = None
    for url in manifest_urls:
        try:
            r = req.get(url, timeout=10)
            if r.status_code == 200:
                remote_manifest = r.json()
                break
        except Exception:
            continue

    if not remote_manifest:
        return {"update_available": False, "reason": "fetch_failed"}

    remote_version = remote_manifest.get("version", "0.0.0")
    remote_author = remote_manifest.get("author", "unknown")

    def _ver_tuple(v):
        """Parse version string into comparable tuple (e.g. '1.2.3' → (1, 2, 3))."""
        try:
            return tuple(int(x) for x in v.split('.'))
        except (ValueError, AttributeError):
            return (0,)

    update = _ver_tuple(remote_version) > _ver_tuple(current_version)

    return {
        "update_available": update,
        "current_version": current_version,
        "remote_version": remote_version,
        "remote_author": remote_author,
        "source_url": source_url,
    }


@router.get("/api/plugins/{plugin_name}/check-deps")
async def check_plugin_deps(plugin_name: str, _=Depends(require_login)):
    """Check dependency status for a plugin."""
    import sys
    from core.plugin_loader import plugin_loader

    info = plugin_loader.get_plugin_info(plugin_name)
    if not info:
        raise HTTPException(status_code=404, detail=f"Unknown plugin: {plugin_name}")

    manifest = info.get("manifest", {})
    deps = manifest.get("pip_dependencies", [])
    missing = plugin_loader._check_dependencies(manifest)

    # Detect environment type
    conda_env = os.environ.get("CONDA_DEFAULT_ENV")
    in_venv = sys.prefix != sys.base_prefix
    if conda_env:
        env_type, env_name = "conda", conda_env
    elif in_venv:
        env_type, env_name = "venv", os.path.basename(sys.prefix)
    else:
        env_type, env_name = "system", "system"

    can_auto = env_type in ("conda", "venv")
    command = f"pip install {' '.join(missing)}" if missing else None

    return {
        "deps": deps, "missing": missing, "installed": [d for d in deps if d not in missing],
        "env_type": env_type, "env_name": env_name,
        "can_auto_install": can_auto, "command": command,
    }


@router.post("/api/plugins/{plugin_name}/install-deps")
async def install_plugin_deps(plugin_name: str, _=Depends(require_login)):
    """Install missing pip dependencies for a plugin.

    Only runs inside conda or venv — refuses on bare system Python.
    """
    import subprocess
    import sys
    from core.plugin_loader import plugin_loader

    info = plugin_loader.get_plugin_info(plugin_name)
    if not info:
        raise HTTPException(status_code=404, detail=f"Unknown plugin: {plugin_name}")

    manifest = info.get("manifest", {})
    missing = plugin_loader._check_dependencies(manifest)
    if not missing:
        return {"status": "ok", "message": "All dependencies already installed", "installed": []}

    # Environment safety gate
    conda_env = os.environ.get("CONDA_DEFAULT_ENV")
    in_venv = sys.prefix != sys.base_prefix
    if not conda_env and not in_venv:
        raise HTTPException(status_code=400, detail=(
            "Sapphire is running in system Python — auto-install disabled for safety. "
            f"Run manually: pip install {' '.join(missing)}"
        ))

    env_label = f"conda:{conda_env}" if conda_env else f"venv:{os.path.basename(sys.prefix)}"
    logger.info(f"[PLUGINS] Installing deps for {plugin_name} in {env_label}: {missing}")

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", *missing],
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="pip install timed out (120s)")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"pip install failed: {e}")

    if result.returncode != 0:
        return JSONResponse(status_code=500, content={
            "status": "error", "message": "pip install failed",
            "output": result.stderr or result.stdout, "command": f"pip install {' '.join(missing)}",
        })

    # Verify deps are now importable
    still_missing = plugin_loader._check_dependencies(manifest)

    # Auto-reload the plugin if all deps are now satisfied
    if not still_missing:
        try:
            plugin_loader.reload_plugin(plugin_name)
            logger.info(f"[PLUGINS] Auto-reloaded {plugin_name} after dep install")
        except Exception as e:
            logger.warning(f"[PLUGINS] Dep install OK but reload failed for {plugin_name}: {e}")

    return {
        "status": "ok" if not still_missing else "partial",
        "installed": [d for d in missing if d not in still_missing],
        "still_missing": still_missing,
        "output": result.stdout,
        "env": env_label,
    }


def _require_known_plugin(plugin_name: str):
    """404 if plugin doesn't exist in merged config or backend loader."""
    merged = _get_merged_plugins()
    if plugin_name in merged.get("plugins", {}):
        return
    try:
        from core.plugin_loader import plugin_loader
        if plugin_loader.get_plugin_info(plugin_name):
            return
    except Exception:
        pass
    raise HTTPException(status_code=404, detail=f"Unknown plugin: {plugin_name}")


@router.get("/api/webui/plugins/{plugin_name}/settings")
async def get_plugin_settings(plugin_name: str, request: Request, _=Depends(require_login)):
    """Get plugin settings, merged with manifest defaults."""
    _require_known_plugin(plugin_name)
    try:
        from core.plugin_loader import plugin_loader
        settings = plugin_loader.get_plugin_settings(plugin_name)
    except Exception:
        # Fallback: read file directly
        settings_file = USER_PLUGIN_SETTINGS_DIR / f"{plugin_name}.json"
        settings = {}
        if settings_file.exists():
            try:
                with open(settings_file, encoding='utf-8') as f:
                    settings = json.load(f)
            except Exception:
                pass
    return {"plugin": plugin_name, "settings": settings}


_settings_locks: dict = {}
_settings_locks_guard = threading.Lock()

def _get_settings_lock(plugin_name: str) -> threading.Lock:
    """Per-plugin file lock for atomic settings RMW. Lazy-created on first use."""
    with _settings_locks_guard:
        lk = _settings_locks.get(plugin_name)
        if lk is None:
            lk = threading.Lock()
            _settings_locks[plugin_name] = lk
        return lk


@router.put("/api/webui/plugins/{plugin_name}/settings")
async def update_plugin_settings(plugin_name: str, request: Request, _=Depends(require_login)):
    """Update plugin settings.

    Holds a per-plugin lock across the read-merge-write so two concurrent PUTs
    don't lose each other's keys (sibling of the MCP save-destroys-servers
    bug). Unique tmp suffix prevents shared-.tmp truncation between writers.
    """
    _require_known_plugin(plugin_name)
    data = await request.json()
    settings = data.get("settings", data)

    # Block toolmaker trust mode in managed mode
    from core.settings_manager import settings as sm
    if plugin_name == 'toolmaker' and sm.is_managed():
        if settings.get('validation') == 'trust':
            raise HTTPException(status_code=403, detail="Trust mode is disabled in managed mode")

    USER_PLUGIN_SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    settings_file = USER_PLUGIN_SETTINGS_DIR / f"{plugin_name}.json"

    # Shallow-merge over existing so side-channel keys (e.g. MCP's `servers`)
    # and partial patches (e.g. email's single-field updates) don't clobber
    # unrelated state. Full resets go through DELETE.
    with _get_settings_lock(plugin_name):
        existing = {}
        if settings_file.exists():
            try:
                existing = json.loads(settings_file.read_text(encoding='utf-8'))
                if not isinstance(existing, dict):
                    existing = {}
            except Exception:
                existing = {}
        merged = {**existing, **settings}

        # Unique tmp suffix per call so concurrent writers can't truncate each
        # other's tmp file before rename.
        tmp_path = settings_file.with_suffix(f'.tmp.{os.getpid()}.{id(merged):x}')
        try:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(merged, f, indent=2)
            tmp_path.replace(settings_file)
        finally:
            if tmp_path.exists():
                try: tmp_path.unlink()
                except Exception: pass

    return {"status": "success", "plugin": plugin_name, "settings": merged}


@router.delete("/api/webui/plugins/{plugin_name}/settings")
async def reset_plugin_settings(plugin_name: str, request: Request, _=Depends(require_login)):
    """Reset plugin settings."""
    _require_known_plugin(plugin_name)
    settings_file = USER_PLUGIN_SETTINGS_DIR / f"{plugin_name}.json"
    if settings_file.exists():
        settings_file.unlink()
    return {"status": "success", "plugin": plugin_name, "message": "Settings reset"}


@router.get("/api/webui/plugins/config")
async def get_plugins_config(request: Request, _=Depends(require_login)):
    """Get full plugins config."""
    return _get_merged_plugins()


@router.post("/api/webui/plugins/image-gen/test-connection")
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


@router.get("/api/webui/plugins/image-gen/defaults")
async def get_image_gen_defaults(request: Request, _=Depends(require_login)):
    """Get image-gen defaults."""
    return {
        'api_url': 'http://localhost:5153',
        'negative_prompt': 'ugly, deformed, noisy, blurry, distorted, grainy, low quality, bad anatomy, jpeg artifacts',
        'static_keywords': 'wide shot',
        'character_descriptions': {'me': '', 'you': ''},
        'defaults': {'height': 1024, 'width': 1024, 'steps': 23, 'cfg_scale': 3.0, 'scheduler': 'dpm++_2m_karras'}
    }


@router.get("/api/webui/plugins/homeassistant/defaults")
async def get_ha_defaults(request: Request, _=Depends(require_login)):
    """Get HA defaults."""
    return {"url": "http://homeassistant.local:8123", "blacklist": ["cover.*", "lock.*"], "notify_service": ""}


@router.post("/api/webui/plugins/homeassistant/test-connection")
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


@router.post("/api/webui/plugins/homeassistant/test-notify")
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

    # Strip 'notify.' prefix if user included it (matches real tool behavior)
    if notify_service.startswith('notify.'):
        notify_service = notify_service[7:]

    def _test():
        import requests as req
        try:
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            payload = {"message": "Test notification from Sapphire", "title": "Sapphire"}
            response = req.post(
                f"{url}/api/services/notify/{notify_service}",
                headers=headers, json=payload, timeout=15
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


@router.put("/api/webui/plugins/homeassistant/token")
async def set_ha_token(request: Request, _=Depends(require_login)):
    """Store HA token."""
    from core.credentials_manager import credentials
    data = await request.json() or {}
    token = data.get('token', '').strip()
    if credentials.set_ha_token(token):
        return {"success": True, "has_token": bool(token)}
    else:
        raise HTTPException(status_code=500, detail="Failed to save token")


@router.get("/api/webui/plugins/homeassistant/token")
async def get_ha_token_status(request: Request, _=Depends(require_login)):
    """Check if HA token exists."""
    from core.credentials_manager import credentials
    token = credentials.get_ha_token()
    return {"has_token": bool(token), "token_length": len(token) if token else 0}


@router.post("/api/webui/plugins/homeassistant/entities")
async def get_ha_entities(request: Request, _=Depends(require_login)):
    """Fetch visible HA entities (after blacklist filtering)."""
    from core.credentials_manager import credentials

    data = await request.json() or {}
    url = data.get('url', '').strip().rstrip('/')
    token = data.get('token', '').strip()
    blacklist = data.get('blacklist', [])

    if not token:
        token = credentials.get_ha_token()

    if not url:
        return {"success": False, "error": "No URL provided"}
    if not token:
        return {"success": False, "error": "No API token found"}

    def _fetch():
        import requests as req
        import fnmatch
        try:
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            response = req.get(f"{url}/api/states", headers=headers, timeout=15)
            if response.status_code != 200:
                return {"success": False, "error": f"HTTP {response.status_code}"}

            entities = response.json()

            # Get areas via template API
            areas = []
            try:
                tmpl = req.post(f"{url}/api/template", headers=headers,
                    json={"template": "{% for area in areas() %}{{ area_name(area) }}||{% endfor %}"},
                    timeout=10)
                if tmpl.status_code == 200:
                    areas = [a.strip() for a in tmpl.text.strip().split('||') if a.strip()]
            except Exception:
                pass

            # Count by domain, applying blacklist
            counts = {"lights": 0, "switches": 0, "scenes": 0, "scripts": 0, "climate": 0}
            domain_map = {"light": "lights", "switch": "switches", "scene": "scenes",
                          "script": "scripts", "climate": "climate"}

            for e in entities:
                eid = e.get('entity_id', '')
                domain = eid.split('.')[0] if '.' in eid else ''
                if domain not in domain_map:
                    continue
                # Apply blacklist
                blocked = False
                for pat in blacklist:
                    if pat.startswith('area:'):
                        continue  # Skip area patterns (would need entity-area mapping)
                    if fnmatch.fnmatch(eid, pat):
                        blocked = True
                        break
                if not blocked:
                    counts[domain_map[domain]] += 1

            return {"success": True, "counts": counts, "areas": areas}
        except req.exceptions.Timeout:
            return {"success": False, "error": "Connection timed out"}
        except req.exceptions.ConnectionError as e:
            return {"success": False, "error": f"Cannot connect: {str(e)[:100]}"}
        except Exception as e:
            return {"success": False, "error": f"Error: {str(e)[:100]}"}

    return await asyncio.to_thread(_fetch)


# =============================================================================
# EMAIL PLUGIN ROUTES
# =============================================================================

@router.get("/api/webui/plugins/email/credentials")
async def get_email_credentials_status(request: Request, _=Depends(require_login)):
    """Check if email credentials exist (never returns password)."""
    from core.credentials_manager import credentials
    creds = credentials.get_email_credentials()
    return {
        "has_credentials": credentials.has_email_credentials(),
        "address": creds['address'],
        "imap_server": creds['imap_server'],
        "smtp_server": creds['smtp_server'],
    }


@router.put("/api/webui/plugins/email/credentials")
async def set_email_credentials(request: Request, _=Depends(require_login)):
    """Store email credentials (app password is scrambled)."""
    from core.credentials_manager import credentials
    data = await request.json() or {}
    address = data.get('address', '').strip()
    app_password = data.get('app_password', '').strip()
    imap_server = data.get('imap_server', 'imap.gmail.com').strip()
    smtp_server = data.get('smtp_server', 'smtp.gmail.com').strip()

    if not address:
        raise HTTPException(status_code=400, detail="Email address is required")

    # If no new password provided, keep existing
    if not app_password:
        existing = credentials.get_email_credentials()
        app_password = existing.get('app_password', '')

    if credentials.set_email_credentials(address, app_password, imap_server, smtp_server):
        return {"success": True}
    raise HTTPException(status_code=500, detail="Failed to save email credentials")


@router.delete("/api/webui/plugins/email/credentials")
async def clear_email_credentials(request: Request, _=Depends(require_login)):
    """Clear email credentials."""
    from core.credentials_manager import credentials
    if credentials.clear_email_credentials():
        return {"success": True}
    raise HTTPException(status_code=500, detail="Failed to clear email credentials")


@router.post("/api/webui/plugins/email/test")
async def test_email_connection(request: Request, _=Depends(require_login)):
    """Test IMAP connection with provided or stored credentials."""
    import imaplib
    import socket
    import ssl
    from core.credentials_manager import credentials

    data = await request.json() or {}
    address = data.get('address', '').strip()
    app_password = data.get('app_password', '').strip()
    imap_server = data.get('imap_server', '').strip()
    imap_port = data.get('imap_port', 0)

    # Fall back to stored credentials for missing fields
    if not address or not app_password:
        stored = credentials.get_email_credentials()
        address = address or stored['address']
        app_password = app_password or stored['app_password']
        imap_server = imap_server or stored['imap_server']
        imap_port = imap_port or stored.get('imap_port', 993)

    if not address or not app_password:
        missing = []
        if not address: missing.append("email address")
        if not app_password: missing.append("password")
        return {"success": False, "error": f"Missing {' and '.join(missing)}"}

    if not imap_server:
        return {"success": False, "error": "IMAP server address is required"}
    imap_port = int(imap_port) or 993
    target = f"{imap_server}:{imap_port}"

    try:
        imap = imaplib.IMAP4_SSL(imap_server, imap_port, timeout=10)
        imap.login(address, app_password)
        _, data_resp = imap.select('INBOX', readonly=True)
        msg_count = int(data_resp[0])
        imap.logout()
        return {"success": True, "message_count": msg_count, "server": target}
    except imaplib.IMAP4.error as e:
        return {"success": False, "error": f"Login failed for {address} — check password", "detail": str(e), "server": target}
    except socket.timeout:
        return {"success": False, "error": f"Connection timed out to {target}", "detail": "Server didn't respond within 10s — check server address and port"}
    except ConnectionRefusedError:
        return {"success": False, "error": f"Connection refused by {target}", "detail": "Server rejected the connection — wrong port or server not running"}
    except socket.gaierror as e:
        return {"success": False, "error": f"DNS lookup failed for {imap_server}", "detail": "Hostname could not be resolved — check server address"}
    except ssl.SSLError as e:
        return {"success": False, "error": f"SSL error connecting to {target}", "detail": f"{e} — port may not support SSL/TLS"}
    except OSError as e:
        return {"success": False, "error": f"Network error connecting to {target}", "detail": str(e)}


# =============================================================================
# EMAIL ACCOUNTS (multi-account CRUD)
# =============================================================================

@router.get("/api/email/accounts")
async def list_email_accounts(request: Request, _=Depends(require_login)):
    """List all email accounts (no passwords)."""
    from core.credentials_manager import credentials
    return {"accounts": credentials.list_email_accounts()}


@router.put("/api/email/accounts/{scope}")
async def set_email_account(scope: str, request: Request, _=Depends(require_login)):
    """Create or update an email account for a scope."""
    from core.credentials_manager import credentials
    data = await request.json() or {}
    address = data.get('address', '').strip()
    app_password = data.get('app_password', '').strip()
    imap_server = data.get('imap_server', '').strip()
    smtp_server = data.get('smtp_server', '').strip()
    imap_port = int(data.get('imap_port', 993))
    smtp_port = int(data.get('smtp_port', 465))

    if not address:
        raise HTTPException(status_code=400, detail="Email address is required")

    # Don't overwrite an OAuth account with password-based save
    existing = credentials.get_email_account(scope)
    if existing.get('auth_type') == 'oauth2':
        raise HTTPException(status_code=400, detail="This is an OAuth account managed by the O365 plugin. Disconnect it there first.")

    # If no new password provided, keep existing
    if not app_password:
        app_password = existing.get('app_password', '')

    if credentials.set_email_account(scope, address, app_password, imap_server, smtp_server, imap_port, smtp_port):
        return {"success": True}
    raise HTTPException(status_code=500, detail="Failed to save email account")


@router.delete("/api/email/accounts/{scope}")
async def delete_email_account(scope: str, request: Request, _=Depends(require_login)):
    """Delete an email account."""
    from core.credentials_manager import credentials
    if credentials.delete_email_account(scope):
        return {"success": True}
    raise HTTPException(status_code=404, detail=f"Email account '{scope}' not found")


@router.post("/api/email/accounts/{scope}/test")
async def test_email_account(scope: str, request: Request, _=Depends(require_login)):
    """Test IMAP connection for a specific email account."""
    import imaplib
    import socket
    import ssl
    from core.credentials_manager import credentials

    data = await request.json() or {}
    address = data.get('address', '').strip()
    app_password = data.get('app_password', '').strip()
    imap_server = data.get('imap_server', '').strip()
    imap_port = data.get('imap_port', 0)

    # Fall back to stored credentials for missing fields
    if not address or not app_password:
        stored = credentials.get_email_account(scope)
        address = address or stored['address']
        app_password = app_password or stored['app_password']
        imap_server = imap_server or stored['imap_server']
        imap_port = imap_port or stored.get('imap_port', 993)

    if not address or not app_password:
        missing = []
        if not address: missing.append("email address")
        if not app_password: missing.append("password")
        return {"success": False, "error": f"Missing {' and '.join(missing)}"}

    if not imap_server:
        return {"success": False, "error": "IMAP server address is required"}
    imap_port = int(imap_port) or 993
    target = f"{imap_server}:{imap_port}"

    try:
        imap = imaplib.IMAP4_SSL(imap_server, imap_port, timeout=10)
        imap.login(address, app_password)
        _, data_resp = imap.select('INBOX', readonly=True)
        msg_count = int(data_resp[0])
        imap.logout()
        return {"success": True, "message_count": msg_count, "server": target}
    except imaplib.IMAP4.error as e:
        return {"success": False, "error": f"Login failed for {address} — check password", "detail": str(e), "server": target}
    except socket.timeout:
        return {"success": False, "error": f"Connection timed out to {target}", "detail": "Server didn't respond within 10s — check server address and port"}
    except ConnectionRefusedError:
        return {"success": False, "error": f"Connection refused by {target}", "detail": "Server rejected the connection — wrong port or server not running"}
    except socket.gaierror as e:
        return {"success": False, "error": f"DNS lookup failed for {imap_server}", "detail": "Hostname could not be resolved — check server address"}
    except ssl.SSLError as e:
        return {"success": False, "error": f"SSL error connecting to {target}", "detail": f"{e} — port may not support SSL/TLS"}
    except OSError as e:
        return {"success": False, "error": f"Network error connecting to {target}", "detail": str(e)}


# =============================================================================
# BITCOIN WALLET ROUTES
# =============================================================================

@router.get("/api/bitcoin/wallets")
async def list_bitcoin_wallets(request: Request, _=Depends(require_login)):
    """List all bitcoin wallets (no private keys)."""
    from core.credentials_manager import credentials
    return {"wallets": credentials.list_bitcoin_wallets()}


@router.put("/api/bitcoin/wallets/{scope}")
async def set_bitcoin_wallet(scope: str, request: Request, _=Depends(require_login)):
    """Create or import a bitcoin wallet for a scope."""
    from core.credentials_manager import credentials
    data = await request.json() or {}
    wif = data.get('wif', '').strip()
    label = data.get('label', '').strip()
    generate = data.get('generate', False)

    if generate:
        try:
            from bit import Key
            key = Key()
            wif = key.to_wif()
        except ImportError:
            raise HTTPException(status_code=500, detail="bit library not installed")

    # If no new WIF provided, keep existing (label-only update)
    if not wif:
        existing = credentials.get_bitcoin_wallet(scope)
        wif = existing.get('wif', '')
    if not wif:
        raise HTTPException(status_code=400, detail="WIF key is required (or set generate=true)")

    # Validate the WIF
    try:
        from bit import Key
        key = Key(wif)
        address = key.address
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid WIF key: {e}")

    if credentials.set_bitcoin_wallet(scope, wif, label):
        return {"success": True, "address": address}
    raise HTTPException(status_code=500, detail="Failed to save bitcoin wallet")


@router.delete("/api/bitcoin/wallets/{scope}")
async def delete_bitcoin_wallet(scope: str, request: Request, _=Depends(require_login)):
    """Delete a bitcoin wallet."""
    from core.credentials_manager import credentials
    if credentials.delete_bitcoin_wallet(scope):
        return {"success": True}
    raise HTTPException(status_code=404, detail=f"Bitcoin wallet '{scope}' not found")


@router.post("/api/bitcoin/wallets/{scope}/check")
async def check_bitcoin_wallet(scope: str, request: Request, _=Depends(require_login)):
    """Check balance for a bitcoin wallet."""
    from core.credentials_manager import credentials

    wallet = credentials.get_bitcoin_wallet(scope)
    if not wallet['wif']:
        return {"success": False, "error": "No wallet configured for this scope"}

    try:
        from bit import Key
        key = Key(wallet['wif'])
        balance_sat = key.get_balance()
        balance_btc = f"{int(balance_sat) / 1e8:.8f}"
        return {
            "success": True,
            "address": key.address,
            "balance_btc": balance_btc,
            "balance_sat": int(balance_sat),
        }
    except ImportError:
        return {"success": False, "error": "bit library not installed"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/api/bitcoin/wallets/{scope}/export")
async def export_bitcoin_wallet(scope: str, request: Request, _=Depends(require_login)):
    """Export a bitcoin wallet (includes WIF for backup)."""
    from core.credentials_manager import credentials

    wallet = credentials.get_bitcoin_wallet(scope)
    if not wallet['wif']:
        raise HTTPException(status_code=404, detail=f"No wallet for scope '{scope}'")

    try:
        from bit import Key
        address = Key(wallet['wif']).address
    except Exception:
        address = ''

    return {
        "scope": scope,
        "label": wallet['label'],
        "wif": wallet['wif'],
        "address": address,
    }


# =============================================================================
# GOOGLE CALENDAR ACCOUNT ROUTES
# =============================================================================

@router.get("/api/gcal/accounts")
async def list_gcal_accounts(request: Request, _=Depends(require_login)):
    """List all Google Calendar accounts (no secrets).
    Auto-migrates from legacy plugin_state + plugin_settings if needed."""
    from core.credentials_manager import credentials
    accounts = credentials.list_gcal_accounts()

    # One-time migration: if no accounts but old plugin_state/settings exist, migrate
    if not accounts:
        try:
            from core.plugin_loader import plugin_loader
            import json
            from pathlib import Path
            ps = plugin_loader.get_plugin_settings('google-calendar') or {}
            state_path = Path(__file__).parent.parent.parent / 'user' / 'plugin_state' / 'google-calendar.json'
            state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}

            client_id = ps.get('GCAL_CLIENT_ID', '').strip()
            client_secret = ps.get('GCAL_CLIENT_SECRET', '').strip()
            if client_id:
                credentials.set_gcal_account(
                    'default', client_id, client_secret,
                    ps.get('GCAL_CALENDAR_ID', 'primary').strip() or 'primary',
                    state.get('refresh_token', ''), 'default'
                )
                # Carry over cached access token
                if state.get('access_token'):
                    credentials.update_gcal_tokens(
                        'default', state.get('refresh_token', ''),
                        state['access_token'], state.get('expires_at', 0)
                    )
                accounts = credentials.list_gcal_accounts()
                logger.info("[GCAL] Migrated legacy settings to credentials manager")
        except Exception as e:
            logger.debug(f"[GCAL] Migration check: {e}")

    return {"accounts": accounts}


@router.put("/api/gcal/accounts/{scope}")
async def set_gcal_account(scope: str, request: Request, _=Depends(require_login)):
    """Create or update a Google Calendar account for a scope."""
    from core.credentials_manager import credentials
    data = await request.json() or {}
    client_id = data.get('client_id', '').strip()
    client_secret = data.get('client_secret', '').strip()
    calendar_id = data.get('calendar_id', 'primary').strip()
    label = data.get('label', '').strip()

    # If no new secret provided, keep existing
    if not client_secret:
        existing = credentials.get_gcal_account(scope)
        client_secret = existing.get('client_secret', '')

    if not client_id:
        raise HTTPException(status_code=400, detail="Client ID is required")

    # Preserve existing refresh token if present
    existing = credentials.get_gcal_account(scope)
    refresh_token = existing.get('refresh_token', '')

    if credentials.set_gcal_account(scope, client_id, client_secret, calendar_id, refresh_token, label):
        return {"success": True}
    raise HTTPException(status_code=500, detail="Failed to save gcal account")


@router.delete("/api/gcal/accounts/{scope}")
async def delete_gcal_account(scope: str, request: Request, _=Depends(require_login)):
    """Delete a Google Calendar account."""
    from core.credentials_manager import credentials
    if credentials.delete_gcal_account(scope):
        return {"success": True}
    raise HTTPException(status_code=404, detail=f"Google Calendar account '{scope}' not found")


# =============================================================================
# SSH PLUGIN ROUTES
# =============================================================================

@router.get("/api/webui/plugins/ssh/servers")
async def get_ssh_servers(request: Request, _=Depends(require_login)):
    """Get configured SSH servers."""
    from core.credentials_manager import credentials
    return {"servers": credentials.get_ssh_servers()}


@router.put("/api/webui/plugins/ssh/servers")
async def set_ssh_servers(request: Request, _=Depends(require_login)):
    """Replace the SSH servers list."""
    from core.credentials_manager import credentials
    data = await request.json() or {}
    servers = data.get('servers', [])
    # Validate each server has required fields
    for s in servers:
        if not s.get('name') or not s.get('host') or not s.get('user'):
            raise HTTPException(status_code=400, detail="Each server needs name, host, and user")
    if credentials.set_ssh_servers(servers):
        return {"success": True, "count": len(servers)}
    raise HTTPException(status_code=500, detail="Failed to save SSH servers")


@router.post("/api/webui/plugins/ssh/test")
async def test_ssh_connection(request: Request, _=Depends(require_login)):
    """Test SSH connection to a server."""
    import subprocess
    from pathlib import Path

    data = await request.json() or {}
    host = data.get('host', '').strip()
    user = data.get('user', '').strip()
    port = str(data.get('port', 22))
    key_path = data.get('key_path', '').strip()

    if not host or not user:
        return {"success": False, "error": "Host and user required"}

    ssh_cmd = [
        'ssh',
        '-o', 'StrictHostKeyChecking=accept-new',
        '-o', 'ConnectTimeout=5',
        '-o', 'BatchMode=yes',
        '-p', port,
    ]
    if key_path:
        ssh_cmd.extend(['-i', str(Path(key_path).expanduser())])
    ssh_cmd.append(f'{user}@{host}')
    ssh_cmd.append('echo ok')

    try:
        result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return {"success": True}
        return {"success": False, "error": result.stderr.strip() or f"Exit code {result.returncode}"}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Connection timed out"}
    except FileNotFoundError:
        return {"success": False, "error": "SSH client not found"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# =============================================================================
# PLUGIN ROUTE DISPATCHER
# =============================================================================

def _check_plugin_bearer(plugin_name: str, request: Request) -> bool:
    """Check if request Authorization header has a valid bearer token registered
    by the plugin. Plugins register bearer tokens by writing to
    `user/plugin_state/{plugin_name}_mcp_key.json` with shape `{"key": "..."}`.
    Used by MCP endpoints where the caller is a tool (e.g. Claude Code) with
    its own credentials rather than a browser session. Returns False if no
    token file, or if header doesn't match."""
    import json
    import secrets
    from pathlib import Path
    auth = request.headers.get('Authorization', '')
    if not auth.startswith('Bearer '):
        return False
    token = auth[len('Bearer '):].strip()
    if not token:
        return False
    project_root = Path(__file__).parent.parent.parent
    key_file = project_root / 'user' / 'plugin_state' / f'{plugin_name}_mcp_key.json'
    if not key_file.exists():
        return False
    try:
        expected = json.loads(key_file.read_text()).get('key', '')
    except Exception:
        return False
    if not expected:
        return False
    return secrets.compare_digest(token, expected)


@router.api_route("/api/plugin/{plugin_name}/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def plugin_route_dispatch(plugin_name: str, path: str, request: Request):
    """Dispatch requests to plugin-registered HTTP routes.

    Auth: session by default (require_login). A plugin may ALSO accept bearer
    tokens by dropping a key file at `user/plugin_state/{plugin}_mcp_key.json`.
    If the `Authorization: Bearer ...` header matches the plugin's registered
    key, session login is bypassed. Tools without a bearer fall through to
    session auth as before. Plugins cannot weaken session CSRF; they can
    only add an additional bearer-token auth path."""
    from core.plugin_loader import plugin_loader
    from core.auth import check_endpoint_rate
    import hashlib

    # Bearer-token bypass for plugins that registered a key file. Falls
    # through to require_login if no bearer or bearer invalid.
    bearer_ok = _check_plugin_bearer(plugin_name, request)
    if not bearer_ok:
        await require_login(request)

    # Rate limit: 30 requests per 60s. For bearer-authenticated requests,
    # identify by hash of the bearer token instead of IP — otherwise every
    # MCP client on localhost collapses into one bucket (initialize +
    # tools/list + first tools/call burns 3 of the 30 at session start).
    # Scout finding #13 — 2026-04-20.
    identity = None
    if bearer_ok:
        auth = request.headers.get('Authorization', '')
        token = auth[len('Bearer '):].strip() if auth.startswith('Bearer ') else ''
        if token:
            identity = f"bearer:{hashlib.sha256(token.encode()).hexdigest()[:16]}"
    check_endpoint_rate(request, f"plugin_route:{plugin_name}", max_calls=30,
                        identity=identity)

    result = plugin_loader.get_route_handler(plugin_name, request.method, path)
    if not result:
        raise HTTPException(status_code=404, detail="Route not found")

    handler, path_params = result

    # Parse request body for POST/PUT (skip for multipart — handler reads form directly)
    body = {}
    content_type = request.headers.get("content-type", "")
    if request.method in ("POST", "PUT") and "multipart" not in content_type:
        try:
            body = await request.json()
        except Exception:
            body = {}

    # Build handler kwargs: path params + body + settings + credentials + query params + request
    settings = plugin_loader.get_plugin_settings(plugin_name)
    credentials = plugin_loader.get_credentials()
    query_params = dict(request.query_params)
    kwargs = {**path_params, "body": body, "settings": settings, "credentials": credentials, "query": query_params, "request": request}

    # Call handler (may be sync — run in threadpool)
    import asyncio
    if asyncio.iscoroutinefunction(handler):
        response_data = await handler(**kwargs)
    else:
        response_data = await asyncio.to_thread(handler, **kwargs)

    if isinstance(response_data, Response):
        return response_data
    return response_data
