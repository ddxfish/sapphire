# tools/story_tools.py — the one-tool referee surface (+ lifecycle).
# story_act is the ONLY way the world changes: the engine validates every
# act against the room JSON. The AI narrates outcomes; it never decides them.
import logging
import sys
from pathlib import Path

# .absolute(), never .resolve(): symlinked plugin dirs must not escape.
_PLUGIN_ROOT = str(Path(__file__).absolute().parent.parent)
if _PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, _PLUGIN_ROOT)

logger = logging.getLogger(__name__)

EMOJI = "📖"
GROUP = "Story Engine"

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "story_act",
            "description": "Resolve ONE player-chosen act against the current room — the engine is the referee, you narrate its verdict. The current room (scene, exits, objects, state) is already in your turn context every turn; call 'look' with target 'room' only to re-read a room that may have CHANGED. Verbs: 'move' (target = exit label), 'search' (uncover hidden things), 'solve' (target = puzzle object, answer = the player's attempt), 'look' (target = a specific object, or 'room'), 'take' (target = an object marked 'can be taken' — it joins the inventory and leaves the room), or any verb a room object declares. Never decide mechanical outcomes yourself.",
            "parameters": {
                "type": "object",
                "properties": {
                    "verb": {"type": "string", "description": "move | search | solve | look | take | <object-declared verb>"},
                    "target": {"type": "string", "description": "Exit label or object name"},
                    "answer": {"type": "string", "description": "solve only: the player's attempted solution"}
                },
                "required": ["verb"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "story_place",
            "description": "Author a NEW object into a story room — it becomes part of the tracked world (visible in room context, actable via story_act). Use for things YOU introduce into the fiction that should persist: a note you leave, a gift you hide, a tool you fashion. One verb per call; call again on the same object to add more. Not for objects the room already tracks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "room": {"type": "string", "description": "Room title or id (empty = current room)"},
                    "name": {"type": "string", "description": "Object name, e.g. 'folded_note'"},
                    "desc": {"type": "string", "description": "What one sees looking at it"},
                    "verb": {"type": "string", "description": "Optional custom verb it responds to, e.g. 'read'"},
                    "response": {"type": "string", "description": "What that verb returns when performed"},
                    "hidden": {"type": "boolean", "description": "True = found only by searching"}
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "story_end",
            "description": "Close the active story in this chat and restore the previous prompt. The journal (full event history) is kept.",
            "parameters": {"type": "object", "properties": {}}
        }
    }
]


def _system():
    from core.api_fastapi import get_system
    system = get_system()
    if not system:
        raise RuntimeError("System not ready.")
    return system


def execute(function_name, arguments, config):
    from gameroom_story import session
    try:
        system = _system()
        if function_name == "story_act":
            return session.act(system,
                               arguments.get("verb"),
                               arguments.get("target"),
                               arguments.get("answer"))
        if function_name == "story_place":
            return session.place_object(system,
                                        arguments.get("room"),
                                        arguments.get("name"),
                                        desc=arguments.get("desc"),
                                        verb=arguments.get("verb"),
                                        response=arguments.get("response"),
                                        hidden=bool(arguments.get("hidden")))
        if function_name == "story_end":
            return session.end(system)
        return f"Unknown function: {function_name}", False
    except KeyError as e:
        return f"Unknown story: {e}", False
    except Exception as e:
        logger.error(f"[STORY] {function_name} failed: {e}", exc_info=True)
        return f"Story engine error: {e}", False
