# core/routes/content.py - Prompts, toolsets, spices, personas, and spice set routes
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Request, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse

import config
from core.auth import require_login
from core.api_fastapi import get_system, _apply_chat_settings, PROJECT_ROOT
from core.event_bus import publish, Events
from core import prompts

logger = logging.getLogger(__name__)

router = APIRouter()


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


# =============================================================================
# PROMPTS ROUTES
# =============================================================================

@router.get("/api/prompts")
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


@router.post("/api/prompts/reload")
async def reload_prompts(request: Request, _=Depends(require_login)):
    """Reload prompts from disk."""
    prompts.prompt_manager.reload()
    return {"status": "success"}


@router.get("/api/prompts/components")
async def get_prompt_components(request: Request, _=Depends(require_login)):
    """Get prompt components."""
    return {"components": prompts.prompt_manager.components}


@router.get("/api/prompts/{name}")
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


@router.put("/api/prompts/{name}")
async def save_prompt(name: str, request: Request, _=Depends(require_login)):
    """Save a prompt."""
    data = await request.json()
    if prompts.save_prompt(name, data):
        publish(Events.PROMPT_CHANGED, {"name": name, "action": "saved"})
        return {"status": "success", "name": name}
    else:
        raise HTTPException(status_code=500, detail="Failed to save prompt")


@router.delete("/api/prompts/{name}")
async def delete_prompt(name: str, request: Request, _=Depends(require_login)):
    """Delete a prompt."""
    if prompts.delete_prompt(name):
        publish(Events.PROMPT_DELETED, {"name": name})
        return {"status": "success", "name": name}
    else:
        raise HTTPException(status_code=500, detail="Failed to delete prompt")


@router.put("/api/prompts/components/{comp_type}/{key}")
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


@router.delete("/api/prompts/components/{comp_type}/{key}")
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


@router.post("/api/prompts/{name}/load")
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


@router.post("/api/prompts/reset")
async def reset_prompts(request: Request, _=Depends(require_login)):
    """Reset prompts to factory defaults."""
    if prompts.prompt_manager.reset_to_defaults():
        return {"status": "success"}
    else:
        raise HTTPException(status_code=500, detail="Failed to reset prompts")


@router.post("/api/prompts/merge")
async def merge_prompts(request: Request, _=Depends(require_login)):
    """Merge factory defaults into user prompts."""
    result = prompts.prompt_manager.merge_defaults()
    if result:
        return {"status": "success", **result}
    raise HTTPException(status_code=500, detail="Failed to merge prompts")


@router.post("/api/system/merge-updates")
async def merge_updates(request: Request, _=Depends(require_login)):
    """Unified merge: add missing prompts + personas from app updates."""
    from datetime import datetime
    backup_dir = str(PROJECT_ROOT / "user" / "backups" / datetime.now().strftime("%Y%m%d_%H%M%S"))

    prompt_result = prompts.prompt_manager.merge_defaults(backup_dir)
    if not prompt_result:
        raise HTTPException(status_code=500, detail="Failed to merge prompt defaults")

    from core.personas import persona_manager
    personas_added = persona_manager.merge_defaults(backup_dir)

    added = prompt_result["added"]
    added["personas"] = personas_added

    return {"status": "success", "backup": prompt_result["backup"], "added": added}


@router.post("/api/prompts/reset-chat-defaults")
async def reset_prompts_chat_defaults(request: Request, _=Depends(require_login)):
    """Reset chat_defaults.json to factory."""
    defaults_path = PROJECT_ROOT / "user" / "settings" / "chat_defaults.json"
    if defaults_path.exists():
        defaults_path.unlink()
    return {"status": "success"}


# =============================================================================
# TOOLSET ROUTES
# =============================================================================

@router.get("/api/toolsets")
async def list_toolsets(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """List all toolsets. Use ?filter=sidebar to exclude module-level entries."""
    from core.toolsets import toolset_manager
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


@router.get("/api/toolsets/current")
async def get_current_toolset(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Get current toolset."""
    info = system.llm_chat.function_manager.get_current_toolset_info()
    return info


@router.post("/api/toolsets/{toolset_name}/activate")
async def activate_toolset(toolset_name: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Activate a toolset."""
    system.llm_chat.function_manager.update_enabled_functions([toolset_name])
    publish(Events.TOOLSET_CHANGED, {"name": toolset_name})
    # Persist to chat settings so it survives restart
    system.llm_chat.session_manager.update_chat_settings({"toolset": toolset_name})
    return {"status": "success", "toolset": toolset_name}


@router.get("/api/functions")
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


@router.post("/api/functions/enable")
async def enable_functions(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Enable specific functions."""
    data = await request.json()
    functions = data.get('functions', [])
    system.llm_chat.function_manager.update_enabled_functions(functions)
    publish(Events.TOOLSET_CHANGED, {"name": "custom", "functions": functions})
    return {"status": "success", "enabled": functions}


@router.post("/api/toolsets/custom")
async def save_custom_toolset(request: Request, _=Depends(require_login)):
    """Save a custom toolset."""
    from core.toolsets import toolset_manager
    data = await request.json()
    name = data.get('name')
    functions = data.get('functions', [])
    if not name:
        raise HTTPException(status_code=400, detail="Name required")
    if toolset_manager.save_toolset(name, functions):
        return {"status": "success", "name": name}
    else:
        raise HTTPException(status_code=500, detail="Failed to save toolset")


@router.delete("/api/toolsets/{toolset_name}")
async def delete_toolset(toolset_name: str, request: Request, _=Depends(require_login)):
    """Delete a custom toolset."""
    from core.toolsets import toolset_manager
    if toolset_manager.delete_toolset(toolset_name):
        return {"status": "success", "name": toolset_name}
    else:
        raise HTTPException(status_code=404, detail="Toolset not found or cannot delete")


@router.post("/api/toolsets/{toolset_name}/emoji")
async def set_toolset_emoji(toolset_name: str, request: Request, _=Depends(require_login)):
    """Set custom emoji for a toolset (works on presets and user toolsets)."""
    from core.toolsets import toolset_manager
    data = await request.json()
    emoji = data.get('emoji', '')
    if toolset_manager.set_emoji(toolset_name, emoji):
        return {"status": "success", "name": toolset_name, "emoji": emoji}
    else:
        raise HTTPException(status_code=404, detail="Toolset not found")


# =============================================================================
# SPICES ROUTES
# =============================================================================

@router.get("/api/spices")
async def list_spices(request: Request, _=Depends(require_login)):
    """List all spices."""
    return _build_spice_response()


# Category routes MUST come before wildcard /api/spices/{category}/{index}
@router.post("/api/spices/category")
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


@router.delete("/api/spices/category/{name}")
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


@router.put("/api/spices/category/{name}")
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


@router.post("/api/spices/category/{name}/emoji")
async def set_spice_category_emoji(name: str, request: Request, _=Depends(require_login)):
    """Set emoji for a spice category."""
    data = await request.json()
    emoji = data.get('emoji', '')
    if name not in prompts.prompt_manager._spices:
        raise HTTPException(status_code=404, detail=f"Category '{name}' not found")
    meta = prompts.prompt_manager._spice_meta.get(name, {})
    meta['emoji'] = emoji
    prompts.prompt_manager._spice_meta[name] = meta
    prompts.prompt_manager.save_spices()
    return {"status": "success", "name": name, "emoji": emoji}


@router.post("/api/spices/category/{name}/toggle")
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


@router.post("/api/spices/reload")
async def reload_spices(request: Request, _=Depends(require_login)):
    """Reload spices from disk."""
    prompts.prompt_manager._load_spices()
    return {"status": "success"}


# Individual spice CRUD - wildcard routes AFTER category routes
@router.post("/api/spices")
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


@router.put("/api/spices/{category}/{index}")
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


@router.delete("/api/spices/{category}/{index}")
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

@router.get("/api/personas")
async def list_personas(request: Request, _=Depends(require_login)):
    """List all personas with summary info."""
    from core.personas import persona_manager
    return {"personas": persona_manager.get_list(), "default": getattr(config, 'DEFAULT_PERSONA', '') or ''}


@router.get("/api/personas/{name}")
async def get_persona(name: str, request: Request, _=Depends(require_login)):
    """Get single persona with full details."""
    from core.personas import persona_manager
    persona = persona_manager.get(name)
    if not persona:
        raise HTTPException(status_code=404, detail="Persona not found")
    return persona


@router.post("/api/personas")
async def create_persona(request: Request, _=Depends(require_login)):
    """Create a new persona."""
    from core.personas import persona_manager
    data = await request.json()
    name = data.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="Name required")
    if not persona_manager.create(name, data):
        raise HTTPException(status_code=409, detail="Persona already exists or invalid name")
    return {"status": "success", "name": persona_manager._sanitize_name(name)}


@router.put("/api/personas/default")
async def set_default_persona(request: Request, _=Depends(require_login)):
    """Set the default persona for new chats."""
    from core.personas import persona_manager
    from core.settings_manager import settings
    data = await request.json()
    name = data.get("name", "")
    if name and not persona_manager.exists(name):
        raise HTTPException(status_code=404, detail="Persona not found")
    settings.set("DEFAULT_PERSONA", name, persist=True)
    return {"status": "success", "default": name}


@router.delete("/api/personas/default")
async def clear_default_persona(request: Request, _=Depends(require_login)):
    """Clear the default persona."""
    from core.settings_manager import settings
    settings.set("DEFAULT_PERSONA", "", persist=True)
    return {"status": "success"}


@router.put("/api/personas/{name}")
async def update_persona(name: str, request: Request, _=Depends(require_login)):
    """Update an existing persona."""
    from core.personas import persona_manager
    if not persona_manager.exists(name):
        raise HTTPException(status_code=404, detail="Persona not found")
    data = await request.json()
    if not persona_manager.update(name, data):
        raise HTTPException(status_code=500, detail="Failed to update persona")
    return {"status": "success"}


@router.delete("/api/personas/{name}")
async def delete_persona(name: str, request: Request, _=Depends(require_login)):
    """Delete a persona."""
    from core.personas import persona_manager
    if not persona_manager.delete(name):
        raise HTTPException(status_code=404, detail="Persona not found")
    return {"status": "success"}


@router.post("/api/personas/{name}/duplicate")
async def duplicate_persona(name: str, request: Request, _=Depends(require_login)):
    """Duplicate a persona with a new name."""
    from core.personas import persona_manager
    data = await request.json()
    new_name = data.get("name")
    if not new_name:
        raise HTTPException(status_code=400, detail="New name required")
    if not persona_manager.duplicate(name, new_name):
        raise HTTPException(status_code=409, detail="Source not found or target name already exists")
    return {"status": "success", "name": persona_manager._sanitize_name(new_name)}


@router.post("/api/personas/{name}/avatar")
async def upload_persona_avatar(name: str, request: Request, file: UploadFile = File(...), _=Depends(require_login)):
    """Upload avatar image for a persona (max 4MB). Auto-resized to 512x512 webp."""
    from core.personas import persona_manager
    if not persona_manager.exists(name):
        raise HTTPException(status_code=404, detail="Persona not found")

    raw = await file.read()
    if len(raw) > 4 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Avatar too large (max 4MB)")

    # Resize to 512x512 square (center crop) and convert to webp
    data = _process_avatar(raw)
    filename = f"{name}.webp"

    if not persona_manager.set_avatar(name, filename, data):
        raise HTTPException(status_code=500, detail="Failed to save avatar")
    return {"status": "success", "avatar": filename}


def _process_avatar(raw: bytes, size: int = 512) -> bytes:
    """Resize image to square webp. Center-crops to avoid distortion."""
    import io
    from PIL import Image

    # Guard against decompression bombs
    Image.MAX_IMAGE_PIXELS = 4096 * 4096

    img = Image.open(io.BytesIO(raw))
    img = img.convert("RGBA") if img.mode == "RGBA" else img.convert("RGB")

    # Center crop to square
    w, h = img.size
    if w != h:
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        img = img.crop((left, top, left + side, top + side))

    # Resize to target
    if img.size[0] != size:
        img = img.resize((size, size), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=85)
    return buf.getvalue()


@router.delete("/api/personas/{name}/avatar")
async def delete_persona_avatar(name: str, request: Request, _=Depends(require_login)):
    """Delete avatar for a persona, reverting to fallback."""
    from core.personas import persona_manager
    if not persona_manager.delete_avatar(name):
        raise HTTPException(status_code=404, detail="Persona not found")
    return {"status": "success"}


@router.get("/api/personas/{name}/avatar")
async def serve_persona_avatar(name: str, request: Request, _=Depends(require_login)):
    """Serve persona avatar image."""
    from core.personas import persona_manager
    avatar_path = persona_manager.get_avatar_path(name)
    if not avatar_path:
        raise HTTPException(status_code=404, detail="Avatar not found")
    return FileResponse(str(avatar_path))


@router.post("/api/personas/{name}/load")
async def load_persona(name: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Stamp persona settings into the active chat."""
    from core.personas import persona_manager
    persona = persona_manager.get(name)
    if not persona:
        raise HTTPException(status_code=404, detail="Persona not found")

    settings = persona.get("settings", {}).copy()
    settings["persona"] = name
    # Reset scope keys to defaults if persona doesn't specify them,
    # otherwise old persona's scopes persist through dict merge
    from core.chat.function_manager import scope_setting_keys
    for key in scope_setting_keys():
        if key not in settings:
            settings[key] = "default"
    session_manager = system.llm_chat.session_manager
    session_manager.update_chat_settings(settings)

    # Apply all settings (prompt, toolset, voice, spice set, scopes, state engine)
    _apply_chat_settings(system, settings)

    publish(Events.CHAT_SETTINGS_CHANGED, {"persona": name})
    return {"status": "success", "persona": name, "settings": settings}


@router.post("/api/personas/from-chat")
async def create_persona_from_chat(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Create a persona from current active chat settings."""
    from core.personas import persona_manager
    data = await request.json()
    name = data.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="Name required")

    chat_settings = system.llm_chat.session_manager.get_chat_settings()
    if not persona_manager.create_from_settings(name, chat_settings):
        raise HTTPException(status_code=409, detail="Persona already exists or invalid name")
    return {"status": "success", "name": persona_manager._sanitize_name(name)}


# =============================================================================
# PERSONA IMPORT/EXPORT
# =============================================================================

@router.get("/api/personas/{name}/export")
async def export_persona(name: str, request: Request, _=Depends(require_login)):
    """Export persona as a portable JSON bundle (persona + prompt + avatar)."""
    import base64
    from datetime import datetime, timezone
    from core.personas import persona_manager
    from core.prompt_crud import get_prompt
    from core.prompt_manager import prompt_manager

    persona = persona_manager.get(name)
    if not persona:
        raise HTTPException(status_code=404, detail="Persona not found")

    settings = persona.get("settings", {})

    # Build export bundle
    bundle = {
        "sapphire_export": True,
        "type": "persona",
        "version": 1,
        "created": datetime.now(timezone.utc).isoformat(),
        "name": name,
        "tagline": persona.get("tagline", ""),
        "trim_color": settings.get("trim_color", ""),
        "voice": {
            "voice": settings.get("voice", ""),
            "speed": settings.get("speed", 1.0),
            "pitch": settings.get("pitch", 1.0),
        },
    }

    # Include prompt data (same shape as prompt export)
    prompt_name = settings.get("prompt", "")
    if prompt_name and prompt_name != "__story__":
        prompt_data = get_prompt(prompt_name)
        if prompt_data:
            prompt_export = dict(prompt_data)
            # Strip computed fields
            for k in ("content", "compiled", "char_count", "token_count"):
                if prompt_export.get("type") == "assembled" and k == "content":
                    prompt_export.pop(k, None)
                elif k != "content":
                    prompt_export.pop(k, None)
            bundle["prompt"] = {"name": prompt_name, "data": prompt_export}

            # Include components used by assembled prompts
            if prompt_data.get("type") == "assembled" and prompt_data.get("components"):
                used = {}
                for comp_type, comp_key in prompt_data["components"].items():
                    if isinstance(comp_key, str) and comp_key:
                        pieces = prompt_manager.components.get(comp_type, {})
                        if comp_key in pieces:
                            used.setdefault(comp_type, {})[comp_key] = pieces[comp_key]
                    elif isinstance(comp_key, list):
                        for ck in comp_key:
                            pieces = prompt_manager.components.get(comp_type, {})
                            if ck in pieces:
                                used.setdefault(comp_type, {})[ck] = pieces[ck]
                if used:
                    bundle["components"] = used

    # Include avatar as base64
    avatar_path = persona_manager.get_avatar_path(name)
    if avatar_path and avatar_path.exists():
        avatar_data = avatar_path.read_bytes()
        ext = avatar_path.suffix.lstrip('.')
        mime = {'webp': 'image/webp', 'png': 'image/png', 'jpg': 'image/jpeg', 'gif': 'image/gif'}.get(ext, 'image/webp')
        bundle["avatar"] = f"data:{mime};base64,{base64.b64encode(avatar_data).decode()}"
    else:
        bundle["avatar"] = None

    return bundle


@router.post("/api/personas/import")
async def import_persona(request: Request, _=Depends(require_login)):
    """Import a persona from a portable JSON bundle."""
    import base64
    from core.personas import persona_manager
    from core.prompt_crud import get_prompt, save_prompt
    from core.prompt_manager import prompt_manager

    data = await request.json()

    # Validate
    if not data.get("sapphire_export") or data.get("type") != "persona":
        raise HTTPException(status_code=400, detail="Invalid persona export format")

    name = data.get("name", "imported")
    overwrite_prompt = data.get("overwrite_prompt", False)
    overwrite_avatar = data.get("overwrite_avatar", False)

    # Check persona name collision (frontend handles rename prompt)
    if persona_manager.exists(name):
        raise HTTPException(status_code=409, detail=f"Persona '{name}' already exists")

    # Import prompt + components first
    prompt_name = None
    if data.get("prompt"):
        prompt_info = data["prompt"]
        if not isinstance(prompt_info, dict):
            raise HTTPException(status_code=400, detail="Invalid prompt data format")
        prompt_name = prompt_info.get("name", name)
        prompt_data = prompt_info.get("data", {})
        if not isinstance(prompt_data, dict):
            raise HTTPException(status_code=400, detail="Invalid prompt data")

        # Validate required fields based on type
        prompt_type = prompt_data.get("type", "assembled")
        if prompt_type == "monolith" and "content" not in prompt_data:
            raise HTTPException(status_code=400, detail="Monolith prompt requires 'content' field")

        existing = get_prompt(prompt_name)
        if existing and not overwrite_prompt:
            # Don't overwrite — use existing prompt by name
            logger.info(f"[IMPORT] Prompt '{prompt_name}' exists, keeping existing")
        else:
            # Import components if present
            if data.get("components"):
                components = data["components"]
                if not isinstance(components, dict):
                    raise HTTPException(status_code=400, detail="Invalid components format")
                for comp_type, defs in components.items():
                    if not isinstance(defs, dict):
                        continue
                    for key, value in defs.items():
                        existing_piece = prompt_manager.components.get(comp_type, {}).get(key)
                        if existing_piece and not overwrite_prompt:
                            continue
                        prompt_manager.components.setdefault(comp_type, {})[key] = value
                prompt_manager.save_components()

            # Save prompt
            save_prompt(prompt_name, prompt_data, allow_overwrite=overwrite_prompt)
            logger.info(f"[IMPORT] Saved prompt '{prompt_name}'")

    # Build persona settings
    voice_data = data.get("voice", {})
    persona_settings = {}
    if prompt_name:
        persona_settings["prompt"] = prompt_name
    if voice_data.get("voice"):
        persona_settings["voice"] = voice_data["voice"]
    if "speed" in voice_data:
        persona_settings["speed"] = voice_data["speed"]
    if "pitch" in voice_data:
        persona_settings["pitch"] = voice_data["pitch"]
    if data.get("trim_color"):
        persona_settings["trim_color"] = data["trim_color"]

    # Create persona
    persona_data = {
        "name": name,
        "tagline": data.get("tagline", ""),
        "settings": persona_settings,
    }
    if not persona_manager.create(name, persona_data):
        raise HTTPException(status_code=500, detail="Failed to create persona")

    # Import avatar
    if data.get("avatar") and isinstance(data["avatar"], str) and data["avatar"].startswith("data:"):
        existing_avatar = persona_manager.get_avatar_path(name)
        if existing_avatar and not overwrite_avatar:
            logger.info(f"[IMPORT] Avatar exists for '{name}', keeping existing")
        else:
            try:
                # Parse data URI: data:image/webp;base64,XXXX
                header, b64data = data["avatar"].split(",", 1)
                if len(b64data) > 5 * 1024 * 1024:  # 5MB base64 ≈ 3.75MB decoded
                    raise ValueError("Avatar data too large")
                mime = header.split(":")[1].split(";")[0]
                if mime not in ('image/webp', 'image/png', 'image/jpeg', 'image/gif'):
                    raise ValueError(f"Unsupported image type: {mime}")
                avatar_bytes = _process_avatar(base64.b64decode(b64data))
                filename = f"{persona_manager._sanitize_name(name)}.webp"
                persona_manager.set_avatar(persona_manager._sanitize_name(name), filename, avatar_bytes)
                logger.info(f"[IMPORT] Saved avatar for '{name}' ({len(avatar_bytes)} bytes)")
            except Exception as e:
                logger.warning(f"[IMPORT] Failed to import avatar: {e}")

    sanitized = persona_manager._sanitize_name(name)
    return {"status": "success", "name": sanitized}


# =============================================================================
# SPICE SET ROUTES
# =============================================================================

@router.get("/api/spice-sets")
async def list_spice_sets(request: Request, _=Depends(require_login)):
    """List all spice sets."""
    from core.spice_sets import spice_set_manager
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


@router.get("/api/spice-sets/current")
async def get_current_spice_set(request: Request, _=Depends(require_login)):
    """Get current spice set."""
    from core.spice_sets import spice_set_manager
    name = spice_set_manager.active_name
    ss = spice_set_manager.get_set(name)
    return {"name": name, "categories": ss.get('categories', []), "emoji": ss.get('emoji', '')}


@router.post("/api/spice-sets/{set_name}/activate")
async def activate_spice_set(set_name: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Activate a spice set - updates which categories are enabled."""
    from core.spice_sets import spice_set_manager
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


@router.post("/api/spice-sets/custom")
async def save_custom_spice_set(request: Request, _=Depends(require_login)):
    """Save a custom spice set."""
    from core.spice_sets import spice_set_manager
    data = await request.json()
    name = data.get('name')
    categories = data.get('categories', [])
    if not name:
        raise HTTPException(status_code=400, detail="Name required")
    spice_set_manager.save_set(name, categories)
    return {"status": "success", "name": name}


@router.delete("/api/spice-sets/{set_name}")
async def delete_spice_set(set_name: str, request: Request, _=Depends(require_login)):
    """Delete a spice set."""
    from core.spice_sets import spice_set_manager
    if spice_set_manager.delete_set(set_name):
        return {"status": "success"}
    raise HTTPException(status_code=404, detail="Spice set not found")


@router.post("/api/spice-sets/{set_name}/emoji")
async def set_spice_set_emoji(set_name: str, request: Request, _=Depends(require_login)):
    """Set emoji for a spice set."""
    from core.spice_sets import spice_set_manager
    data = await request.json()
    emoji = data.get('emoji', '')
    if spice_set_manager.set_emoji(set_name, emoji):
        return {"status": "success", "name": set_name, "emoji": emoji}
    raise HTTPException(status_code=404, detail="Spice set not found")
