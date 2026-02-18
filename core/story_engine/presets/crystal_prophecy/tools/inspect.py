# Story tool: inspect items and surroundings in the Crystal Prophecy
# The engine instance is passed as the third argument to execute()

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "inspect",
            "description": "Examine an object, creature, or feature in the environment more closely. Returns details the player wouldn't notice at a glance.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "What to inspect (e.g. 'the glowing rune', 'the locked door', 'Sapphire')"
                    }
                },
                "required": ["target"]
            }
        }
    }
]


def execute(function_name, arguments, engine):
    """Execute the inspect tool. Engine is the StoryEngine instance."""
    target = arguments.get("target", "nothing")
    room = engine.get_state("current_room")
    room_val = room if isinstance(room, str) else (room.get("value") if isinstance(room, dict) else "unknown")
    return f"[INSPECT] The player closely examines: {target} (in {room_val}). Describe what they discover — reveal a detail, clue, or sensory impression that rewards curiosity.", True
