# Story tool: Sapphire's bio-scanner for Site 4
# Scans targets for threat assessment, biological data, and environmental info

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "scan",
            "description": "Use Sapphire's bio-scanner to analyze a target — creature, object, or area. Returns threat level, biological data, and tactical assessment.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "What to scan (e.g. 'the claw marks', 'the jungle ahead', 'Dr. Vasquez', 'the river')"
                    }
                },
                "required": ["target"]
            }
        }
    }
]


def execute(function_name, arguments, engine):
    """Execute the scan tool. Engine is the StoryEngine instance."""
    target = arguments.get("target", "nothing")
    room = engine.get_state("room") or "unknown"
    health = engine.get_state("health") or 100

    return (
        f"[SCAN] Sapphire analyzes: {target} (location: {room}, operator health: {health}%). "
        f"Provide a tactical readout — threat level, biological details if organic, "
        f"structural assessment if environmental. Keep it punchy and in-character for Sapphire. "
        f"Add a personal comment from her if relevant."
    ), True
