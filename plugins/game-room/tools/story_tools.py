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
TOOL_CATEGORY = 'game-room'
GROUP = "Story Engine"

TOOLS = [
    {
        "type": "function",
        "writes": True,
        "is_local": True,
        "function": {
            "name": "story_act",
            "description": "Resolve ONE player-chosen act against the current room; the engine referees, you narrate its "
                           "verdict. Never decide mechanical outcomes yourself. The room is already in your context each turn - "
                           "look at 'room' only if it may have changed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "verb": {"type": "string", "description": "move (target = exit) | search | solve (target = puzzle, answer = attempt) | look (object or 'room') "
                                                              "| take | a verb an object declares"},
                    "target": {"type": "string", "description": "Exit label or object name"},
                    "answer": {"type": "string", "description": "solve only: the player's attempt"}
                },
                "required": ["verb"]
            }
        }
    },
    {
        "type": "function",
        "writes": True,
        "is_local": True,
        "function": {
            "name": "story_place",
            "description": "Author a NEW persistent object into a story room: something you introduce (a note, a gift, a tool). "
                           "One verb per call; call again on the same object to add more. Not for objects the room already "
                           "tracks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "room": {"type": "string", "description": "Title or id (empty = current room)"},
                    "name": {"type": "string", "description": "Object name, e.g. 'folded_note'"},
                    "desc": {"type": "string", "description": "What one sees looking at it"},
                    "verb": {"type": "string", "description": "Custom verb it responds to, e.g. 'read'"},
                    "response": {"type": "string", "description": "What that verb returns"},
                    "hidden": {"type": "boolean", "description": "true = found only by searching"}
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "writes": True,
        "is_local": True,
        "function": {
            "name": "story_end",
            "description": "Close this chat's active story and restore the previous prompt. The journal is kept.",
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
