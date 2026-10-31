# functions/self_command.py - she runs a slash command (core/commands.py)

import logging

from core import commands

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '⌘'
TOOL_CATEGORY = 'meta_danger'
AVAILABLE_FUNCTIONS = ['self_command']

_MENU = '; '.join(f"/{n} {c['args']}: {c['description']}" for n, c in sorted(commands.ai_commands().items()))

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "self_command",
            "description": (
                f"Run a slash command on this chat when the user asks. {_MENU}. "
                "Your current turn stays, and the change lands when it ends."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The command line, e.g. /remove 2"},
                },
                "required": ["command"],
            },
        },
    }
]


def execute(function_name, arguments, config=None):
    if function_name != "self_command":
        return f"Unknown function: {function_name}", False
    from core.api_fastapi import get_system
    from core.chat.stream_brain import get_override
    if (get_override() or {}).get('agent'):
        return "self_command: an agent's run can't change a chat's history.", False
    chat = get_system().llm_chat.session_manager._effective_chat_name()
    out = commands.run(str((arguments or {}).get('command') or ''), chat, 'ai')
    return out.get('say', ''), bool(out.get('ok'))
