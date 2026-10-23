# functions/scene.py — Sapphire sets the chat's visual background scene.
#
# The tool DESCRIPTION is a live menu of available scenes (Door-B dynamic descriptions:
# the loader calls get_tools() at load, and the backgrounds endpoints refresh it on
# upload/delete). The user always sees the scene behind the conversation; this lets
# Sapphire set where you are ("let's talk on the boat").

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = "\U0001F3DE"  # national park / scene

AVAILABLE_FUNCTIONS = ['set_scene']

# functions/scene.py -> project root. .absolute() not .resolve() (symlink-trap memory).
_BACKGROUNDS_DIR = Path(__file__).absolute().parent.parent / "user" / "backgrounds"


def _list_scenes():
    """Current scene names from the library (filesystem = source of truth). Never raises."""
    out = []
    try:
        if _BACKGROUNDS_DIR.exists():
            for p in sorted(_BACKGROUNDS_DIR.glob("*.webp")):
                if not p.name.endswith(".thumb.webp"):
                    out.append(p.name[:-5])
    except Exception:
        pass
    return out


def _build_description():
    scenes = _list_scenes()
    menu = ", ".join(scenes) if scenes else "(none uploaded yet)"
    return ("Set the visual background scene of the current chat - the user sees it behind "
            "the conversation. Use it to set the setting (e.g. 'let's talk on the boat'). Pass "
            "the scene name, or 'none' to clear it. Available scenes: " + menu + ".")


def _tool_schema(description):
    return [{
        "type": "function",
        "is_local": True,
        "function": {
            "name": "set_scene",
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string",
                             "description": "A scene name from the available list, or 'none' to clear the scene."}
                },
                "required": ["name"],
            },
        },
    }]


def get_tools():
    """Live-menu schema builder (Door-B). Called at load + on library change."""
    return _tool_schema(_build_description())


# Static fallback (loader prefers get_tools()).
TOOLS = _tool_schema(_build_description())


def execute(function_name, arguments, config=None):
    if function_name != "set_scene":
        return f"Unknown function: {function_name}", False

    raw = arguments.get("name")
    name = raw.strip().lower() if isinstance(raw, str) else ""
    scenes = _list_scenes()
    menu = ", ".join(scenes) if scenes else "(none uploaded yet)"

    if name in ("none", "", "clear", "off", "default"):
        target = ""
    elif name in scenes:
        target = name
    else:
        return (f"Scene '{name}' not found. Available scenes: {menu}.", False)

    try:
        from core.api_fastapi import get_system
        system = get_system()
        if not system or not getattr(system, 'llm_chat', None):
            return "Could not reach the chat to set the scene.", False
        # Per-chat override (merges; resolution = chat > persona > none).
        sm = system.llm_chat.session_manager
        # R5 intent: if a vault eviction retargets the active chat before
        # the write, refuse the stamp instead of writing the landing chat.
        _intended = sm._effective_chat_name()
        if not sm.update_chat_settings({"background": target},
                                       expected_active=_intended):
            return "Failed to update chat settings.", False
        # Tell the frontend to re-render #chatbg live. `chat` is load-bearing:
        # without it a background-lane call repainted the operator's open
        # chat (hunt 2026-08-17).
        from core.event_bus import publish, Events
        publish(Events.CHAT_SETTINGS_CHANGED,
                {"chat": sm._effective_chat_name(), "background": target, "origin": "set_scene"})
    except Exception as e:
        logger.error(f"[SCENE] set_scene failed: {e}")
        return f"Failed to set the scene: {e}", False

    if target:
        return f"Scene set to '{target}'. The user now sees it behind the chat.", True
    return "Scene cleared - back to the default background.", True
