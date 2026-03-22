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

PROJECT_ROOT = Path(__file__).parent.parent.parent
STATIC_DIR = PROJECT_ROOT / "interfaces" / "web" / "static"

_install_lock = threading.Lock()

# Plugin settings paths
USER_WEBUI_DIR = PROJECT_ROOT / 'user' / 'webui'
USER_PLUGINS_JSON = USER_WEBUI_DIR / 'plugins.json'
USER_PLUGIN_SETTINGS_DIR = USER_WEBUI_DIR / 'plugins'
LOCKED_PLUGINS = []


def _get_merged_plugins():
    """Merge static and user plugins.json."""
    static_plugins_json = STATIC_DIR / 'core-ui' / 'plugins.json'
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
                if has_web:
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
                })
    except Exception:
        pass

    return {"plugins": result, "locked": LOCKED_PLUGINS}


@router.put("/api/webui/plugins/toggle/{plugin_name}")
async def toggle_plugin(plugin_name: str, request: Request, _=Depends(require_login)):
    """Toggle a plugin."""
    if plugin_name in LOCKED_PLUGINS:
        raise HTTPException(status_code=403, detail=f"Cannot disable locked plugin: {plugin_name}")

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

    enabled = list(merged.get("enabled", []))

    # Determine current state from plugin_loader (handles default_enabled plugins
    # that aren't in the persisted enabled list)
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
        new_state = False
    else:
        if plugin_name not in enabled:
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

    # Live load/unload — no restart needed for backend plugins
    reload_required = True
    try:
        from core.plugin_loader import plugin_loader
        if plugin_name in plugin_loader._plugins:
            if new_state:
                plugin_loader._plugins[plugin_name]["enabled"] = True
                loaded = plugin_loader._load_plugin(plugin_name)
                if not loaded:
                    # Blocked by verification — revert enabled list
                    plugin_loader._plugins[plugin_name]["enabled"] = False
                    if plugin_name in enabled:
                        enabled.remove(plugin_name)
                    user_data["enabled"] = enabled
                    with open(USER_PLUGINS_JSON, 'w') as f:
                        json.dump(user_data, f, indent=2)
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
                plugin_loader._plugins[plugin_name]["enabled"] = False
            reload_required = False

            # Re-sync toolset so enabled functions reflect the plugin change
            try:
                system = get_system()
                if system and hasattr(system, 'llm_chat'):
                    toolset_info = system.llm_chat.function_manager.get_current_toolset_info()
                    toolset_name = toolset_info.get("name", "custom")
                    system.llm_chat.function_manager.update_enabled_functions([toolset_name])
            except Exception:
                pass  # Best-effort; tools will sync on next chat
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Live plugin toggle failed for {plugin_name}: {e}")

    return {"status": "success", "plugin": plugin_name, "enabled": new_state, "reload_required": reload_required}


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

    from core.plugin_loader import plugin_loader, PluginState, USER_PLUGINS_DIR

    tmp_zip = None
    tmp_dir = None
    try:
        # ── Download or receive zip ──
        if url:
            import requests as req
            # Parse GitHub URL → zip download
            m = re.match(r'https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$', url.strip())
            if not m:
                raise HTTPException(status_code=400, detail="Invalid GitHub URL format")
            owner, repo = m.group(1), m.group(2)
            zip_url = f"https://github.com/{owner}/{repo}/archive/refs/heads/main.zip"
            r = req.get(zip_url, stream=True, timeout=30)
            if r.status_code == 404:
                zip_url = f"https://github.com/{owner}/{repo}/archive/refs/heads/master.zip"
                r = req.get(zip_url, stream=True, timeout=30)
            if r.status_code != 200:
                raise HTTPException(status_code=400, detail=f"Failed to download from GitHub (HTTP {r.status_code})")
            content_length = int(r.headers.get("Content-Length", 0))
            if content_length > MAX_ZIP_SIZE:
                raise HTTPException(status_code=400, detail=f"Zip too large ({content_length // 1024 // 1024}MB, max 50MB)")
            tmp_zip = Path(tempfile.mktemp(suffix=".zip"))
            downloaded = 0
            with open(tmp_zip, "wb") as f:
                for chunk in r.iter_content(8192):
                    downloaded += len(chunk)
                    if downloaded > MAX_ZIP_SIZE:
                        raise HTTPException(status_code=400, detail="Zip exceeds 50MB limit")
                    f.write(chunk)
        else:
            # File upload
            tmp_zip = Path(tempfile.mktemp(suffix=".zip"))
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

                # Delete old plugin dir (state preserved separately)
                shutil.rmtree(dest)

            # ── Install ──
            USER_PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copytree(plugin_root, dest, symlinks=False)

        # ── Write install metadata to plugin state ──
        from datetime import datetime
        state = PluginState(name)
        if url:
            state.save("installed_from", url.strip())
            state.save("install_method", "github_url")
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
        return {"status": "ok", "plugin": plugin_name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/plugins/{plugin_name}/check-update")
async def check_plugin_update(plugin_name: str, _=Depends(require_login)):
    """Check if a newer version is available on GitHub."""
    import re
    from core.plugin_loader import plugin_loader, PluginState

    info = plugin_loader.get_plugin_info(plugin_name)
    if not info:
        raise HTTPException(status_code=404, detail=f"Unknown plugin: {plugin_name}")

    state = PluginState(plugin_name)
    source_url = state.get("installed_from")
    if not source_url or "github.com" not in source_url:
        return {"update_available": False, "reason": "no_source"}

    m = re.match(r'https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$', source_url.strip())
    if not m:
        return {"update_available": False, "reason": "invalid_url"}

    owner, repo = m.group(1), m.group(2)
    current_version = info.get("manifest", {}).get("version", "0.0.0")

    import requests as req
    remote_manifest = None
    for branch in ("main", "master"):
        try:
            r = req.get(
                f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/plugin.json",
                timeout=10,
            )
            if r.status_code == 200:
                remote_manifest = r.json()
                break
        except Exception:
            continue

    if not remote_manifest:
        return {"update_available": False, "reason": "fetch_failed"}

    remote_version = remote_manifest.get("version", "0.0.0")
    remote_author = remote_manifest.get("author", "unknown")

    return {
        "update_available": remote_version != current_version,
        "current_version": current_version,
        "remote_version": remote_version,
        "remote_author": remote_author,
        "source_url": source_url,
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


@router.put("/api/webui/plugins/{plugin_name}/settings")
async def update_plugin_settings(plugin_name: str, request: Request, _=Depends(require_login)):
    """Update plugin settings."""
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
    with open(settings_file, 'w', encoding='utf-8') as f:
        json.dump(settings, f, indent=2)

    return {"status": "success", "plugin": plugin_name, "settings": settings}


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

@router.api_route("/api/plugin/{plugin_name}/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def plugin_route_dispatch(plugin_name: str, path: str, request: Request, _=Depends(require_login)):
    """Dispatch requests to plugin-registered HTTP routes.

    Auth and CSRF are enforced by the framework — plugins cannot bypass them.
    Routes are registered via plugin.json capabilities.routes declarations.
    """
    from core.plugin_loader import plugin_loader
    from core.auth import check_endpoint_rate

    # Rate limit: 30 requests per 60s per session per plugin
    check_endpoint_rate(request, f"plugin_route:{plugin_name}", max_calls=30)

    result = plugin_loader.get_route_handler(plugin_name, request.method, path)
    if not result:
        raise HTTPException(status_code=404, detail="Route not found")

    handler, path_params = result

    # Parse request body for POST/PUT
    body = {}
    if request.method in ("POST", "PUT"):
        try:
            body = await request.json()
        except Exception:
            body = {}

    # Build handler kwargs: path params + body + settings + query params + request
    settings = plugin_loader.get_plugin_settings(plugin_name)
    query_params = dict(request.query_params)
    kwargs = {**path_params, "body": body, "settings": settings, "query": query_params, "request": request}

    # Call handler (may be sync — run in threadpool)
    import asyncio
    if asyncio.iscoroutinefunction(handler):
        response_data = await handler(**kwargs)
    else:
        response_data = await asyncio.to_thread(handler, **kwargs)

    if isinstance(response_data, Response):
        return response_data
    return response_data
