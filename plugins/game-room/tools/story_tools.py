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
            "description": "Resolve ONE player-chosen act against the current room — the engine is the referee, you narrate its verdict. NEVER call this to orient or look around: the current room (scene, exits, objects, state) is already in your turn context every turn. Verbs: 'move' (target = exit label), 'search' (uncover hidden things), 'solve' (target = puzzle object, answer = the player's attempt), 'look' (target REQUIRED — a specific object the player examines), or any verb a room object declares. Never decide mechanical outcomes yourself.",
            "parameters": {
                "type": "object",
                "properties": {
                    "verb": {"type": "string", "description": "move | search | solve | look | <object-declared verb>"},
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
        if function_name == "story_end":
            return session.end(system)
        return f"Unknown function: {function_name}", False
    except KeyError as e:
        return f"Unknown story: {e}", False
    except Exception as e:
        logger.error(f"[STORY] {function_name} failed: {e}", exc_info=True)
        return f"Story engine error: {e}", False
