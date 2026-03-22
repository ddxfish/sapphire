# core/routes/story_engine.py - Story engine routes
import json
import logging
from pathlib import Path

import yaml

from fastapi import APIRouter, Request, Depends, HTTPException

from core.auth import require_login
from core.api_fastapi import get_system, _apply_chat_settings
from core.event_bus import publish, Events

logger = logging.getLogger(__name__)

router = APIRouter()

PROJECT_ROOT = Path(__file__).parent.parent.parent


@router.post("/api/story/start")
async def start_story(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Create a dedicated story chat with auto-configured settings."""
    try:
        data = await request.json() or {}
        preset_name = data.get("preset")
        if not preset_name:
            raise HTTPException(status_code=400, detail="Preset name required")

        # Load preset to get display name
        from core.story_engine.engine import StoryEngine
        preset_path = StoryEngine._find_preset_path_static(preset_name)
        if not preset_path:
            raise HTTPException(status_code=404, detail=f"Preset not found: {preset_name}")

        with open(preset_path, 'r', encoding='utf-8') as f:
            if preset_path.suffix == '.yaml':
                preset_data = yaml.safe_load(f) or {}
            else:
                preset_data = json.load(f)
        story_display = preset_data.get("name", preset_name.replace('_', ' ').title())

        # Create unique chat name (sanitize same as create_chat does)
        raw_name = f"story_{preset_name}"
        chat_name = "".join(c for c in raw_name if c.isalnum() or c in (' ', '-', '_')).strip()
        chat_name = chat_name.replace(' ', '_').lower()
        base_name = chat_name
        counter = 1
        existing = {c["name"] for c in system.llm_chat.list_chats()}
        while chat_name in existing:
            counter += 1
            chat_name = f"{base_name}_{counter}"

        if not system.llm_chat.create_chat(chat_name):
            raise HTTPException(status_code=500, detail="Failed to create story chat")

        # Switch to the new chat
        if not system.llm_chat.switch_chat(chat_name):
            raise HTTPException(status_code=500, detail="Failed to switch to story chat")

        # Configure story settings — toolset "none" so only story tools are active
        story_settings = {
            "story_chat": True,
            "story_display_name": f"[STORY] {story_display}",
            "story_engine_enabled": True,
            "story_preset": preset_name,
            "story_in_prompt": True,
            "story_vars_in_prompt": False,
            "toolset": "none",
            "prompt": "__story__",
        }
        system.llm_chat.session_manager.update_chat_settings(story_settings)

        # Apply settings (loads story engine, prompt, etc.)
        settings = system.llm_chat.session_manager.get_chat_settings()
        _apply_chat_settings(system, settings)

        origin = request.headers.get('X-Session-ID')
        publish(Events.CHAT_SWITCHED, {"name": chat_name, "origin": origin})

        return {
            "status": "success",
            "chat_name": chat_name,
            "display_name": f"[STORY] {story_display}",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to start story: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/story/presets")
async def list_story_presets(request: Request, _=Depends(require_login)):
    """List available story presets (folder-based and flat)."""
    presets = []
    search_dirs = [
        PROJECT_ROOT / "user" / "story_presets",
        PROJECT_ROOT / "core" / "story_engine" / "presets",
    ]
    seen = set()

    def add_preset(name, preset_file, source):
        if name in seen or name.startswith("_"):
            return
        seen.add(name)
        try:
            with open(preset_file, 'r', encoding='utf-8') as f:
                if preset_file.suffix == '.yaml':
                    data = yaml.safe_load(f) or {}
                else:
                    data = json.load(f)
            is_folder = preset_file.name in ("story.json", "story.yaml")
            has_prompt = (preset_file.parent / "prompt.md").exists() if is_folder else False
            has_tools = (preset_file.parent / "tools").is_dir() if is_folder else False
            has_rooms = (preset_file.parent / "rooms").is_dir() if is_folder else False
            presets.append({
                "name": name,
                "display_name": data.get("name", name),
                "description": data.get("description", ""),
                "key_count": len(data.get("initial_state", data.get("state", {}))),
                "source": source,
                "folder": is_folder,
                "has_prompt": has_prompt,
                "has_tools": has_tools,
                "has_rooms": has_rooms,
            })
        except Exception as e:
            logger.warning(f"Failed to load preset {preset_file}: {e}")

    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        source = "user" if "user" in str(search_dir) else "core"
        # Folder-based: {name}/story.yaml (preferred)
        for story_file in search_dir.glob("*/story.yaml"):
            add_preset(story_file.parent.name, story_file, source)
        # Folder-based: {name}/story.json
        for story_file in search_dir.glob("*/story.json"):
            add_preset(story_file.parent.name, story_file, source)
        # Flat: {name}.json
        for preset_file in search_dir.glob("*.json"):
            add_preset(preset_file.stem, preset_file, source)

    return {"presets": presets}


@router.get("/api/story/{chat_name}")
async def get_chat_state(chat_name: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Get current state for a chat."""
    from core.story_engine import StoryEngine
    db_path = PROJECT_ROOT / "user" / "history" / "sapphire_history.db"
    if not db_path.exists():
        raise HTTPException(status_code=404, detail="Database not found")

    engine = StoryEngine(chat_name, db_path)
    session_manager = system.llm_chat.session_manager

    if chat_name == session_manager.get_active_chat_name():
        chat_settings = session_manager.get_chat_settings()
        story_enabled = chat_settings.get('story_engine_enabled', False)
        if story_enabled:
            settings_preset = chat_settings.get('story_preset')
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


@router.get("/api/story/{chat_name}/history")
async def get_chat_state_history(chat_name: str, limit: int = 100, key: str = None, request: Request = None, _=Depends(require_login)):
    """Get state change history."""
    from core.story_engine import StoryEngine
    db_path = PROJECT_ROOT / "user" / "history" / "sapphire_history.db"
    if not db_path.exists():
        raise HTTPException(status_code=404, detail="Database not found")
    engine = StoryEngine(chat_name, db_path)
    history = engine.get_history(key=key, limit=limit)
    return {"chat_name": chat_name, "history": history, "count": len(history)}


@router.post("/api/story/{chat_name}/reset")
async def reset_chat_state(chat_name: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Reset state."""
    from core.story_engine import StoryEngine
    db_path = PROJECT_ROOT / "user" / "history" / "sapphire_history.db"
    if not db_path.exists():
        raise HTTPException(status_code=404, detail="Database not found")

    data = await request.json() or {}
    preset = data.get('preset')
    engine = StoryEngine(chat_name, db_path)

    if preset:
        turn = system.llm_chat.session_manager.get_turn_count() if system else 0
        success, msg = engine.load_preset(preset, turn)
        if not success:
            raise HTTPException(status_code=400, detail=msg)
        result = {"status": "reset", "preset": preset, "message": msg}
    else:
        engine.clear_all()
        result = {"status": "cleared", "message": "State cleared"}

    live_engine = system.llm_chat.function_manager.get_story_engine()
    if live_engine and live_engine.chat_name == chat_name:
        live_engine.reload_from_db()

    return result


@router.post("/api/story/{chat_name}/set")
async def set_chat_state_value(chat_name: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Set a state value."""
    from core.story_engine import StoryEngine
    db_path = PROJECT_ROOT / "user" / "history" / "sapphire_history.db"
    if not db_path.exists():
        raise HTTPException(status_code=404, detail="Database not found")

    data = await request.json() or {}
    key = data.get('key')
    value = data.get('value')
    if not key:
        raise HTTPException(status_code=400, detail="Key required")

    engine = StoryEngine(chat_name, db_path)
    turn = system.llm_chat.session_manager.get_turn_count() if system else 0
    success, msg = engine.set_state(key, value, "user", turn, "Manual edit via UI")

    if success:
        live_engine = system.llm_chat.function_manager.get_story_engine()
        if live_engine and live_engine.chat_name == chat_name:
            live_engine.reload_from_db()
        return {"status": "set", "key": key, "value": value}
    else:
        raise HTTPException(status_code=400, detail=msg)


@router.get("/api/story/saves/{preset_name}")
async def list_game_saves(preset_name: str, request: Request, _=Depends(require_login)):
    """List save slots for a game preset."""
    saves_dir = PROJECT_ROOT / "user" / "story_saves" / preset_name
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


@router.post("/api/story/{chat_name}/save")
async def save_game_state(chat_name: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Save game state + chat history to a slot (quicksave)."""
    from datetime import datetime, timezone
    from core.story_engine import StoryEngine

    data = await request.json() or {}
    slot = data.get('slot')
    if not slot or slot < 1 or slot > 5:
        raise HTTPException(status_code=400, detail="Slot must be 1-5")

    chat_settings = system.llm_chat.session_manager.get_chat_settings()
    preset_name = chat_settings.get('story_preset')
    if not preset_name:
        raise HTTPException(status_code=400, detail="No game preset active")

    db_path = PROJECT_ROOT / "user" / "history" / "sapphire_history.db"
    engine = StoryEngine(chat_name, db_path)
    state = engine.get_state()
    turn = system.llm_chat.session_manager.get_turn_count() if system else 0

    # Snapshot both state AND chat messages for full quicksave
    messages = system.llm_chat.session_manager.current_chat.get_messages()

    save_data = {
        "slot": slot,
        "preset": preset_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "turn": turn,
        "state": state,
        "messages": messages,
    }

    saves_dir = PROJECT_ROOT / "user" / "story_saves" / preset_name
    saves_dir.mkdir(parents=True, exist_ok=True)
    slot_file = saves_dir / f"slot_{slot}.json"
    with open(slot_file, 'w', encoding='utf-8') as f:
        json.dump(save_data, f, indent=2)

    msg_count = len(messages)
    return {"status": "saved", "slot": slot, "timestamp": save_data["timestamp"], "message_count": msg_count}


@router.post("/api/story/{chat_name}/load")
async def load_game_state(chat_name: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Load game state + chat history from a slot (quickload)."""
    from core.story_engine import StoryEngine

    data = await request.json() or {}
    slot = data.get('slot')
    if not slot or slot < 1 or slot > 5:
        raise HTTPException(status_code=400, detail="Slot must be 1-5")

    chat_settings = system.llm_chat.session_manager.get_chat_settings()
    preset_name = chat_settings.get('story_preset')
    if not preset_name:
        raise HTTPException(status_code=400, detail="No game preset active")

    saves_dir = PROJECT_ROOT / "user" / "story_saves" / preset_name
    slot_file = saves_dir / f"slot_{slot}.json"
    if not slot_file.exists():
        raise HTTPException(status_code=404, detail=f"Slot {slot} is empty")

    with open(slot_file, 'r', encoding='utf-8') as f:
        save_data = json.load(f)

    # Restore story state
    db_path = PROJECT_ROOT / "user" / "history" / "sapphire_history.db"
    engine = StoryEngine(chat_name, db_path)
    turn = system.llm_chat.session_manager.get_turn_count() if system else 0

    engine.clear_all()
    for key, value in save_data.get("state", {}).items():
        val = value.get("value") if isinstance(value, dict) else value
        engine.set_state(key, val, "load", turn, f"Loaded from slot {slot}")

    live_engine = system.llm_chat.function_manager.get_story_engine()
    if live_engine and live_engine.chat_name == chat_name:
        live_engine.reload_from_db()

    # Restore chat history if saved (quickload)
    saved_messages = save_data.get("messages")
    if saved_messages is not None:
        session_manager = system.llm_chat.session_manager
        session_manager.current_chat.messages = saved_messages
        session_manager._save_current_chat()
        logger.info(f"[STORY] Quickloaded slot {slot}: {len(saved_messages)} messages + state restored")

    return {"status": "loaded", "slot": slot, "turn": save_data.get("turn", 0), "timestamp": save_data.get("timestamp")}
