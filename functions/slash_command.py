# functions/slash_command.py - she runs a slash command (core/commands.py)

import logging

from core import commands

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '⌘'
TOOL_CATEGORY = 'meta_danger'
AVAILABLE_FUNCTIONS = ['slash_command']

# Hidden from her until the person opts in (Settings > Tools).
SETTINGS_GATED = {'slash_command': 'AI_SLASH_COMMANDS_ENABLED'}

_MENU = '; '.join(f"/{n} {c['args']}: {c['description']}" for n, c in sorted(commands.ai_commands().items()))

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "slash_command",
            "description": (
                f"Run a slash command on this chat when the user asks. {_MENU}. "
                "/remove N takes the N turns before this one, and this turn too when it ends: "
                "the request and your reply go with them. To undo the last turn, use /remove 1."
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
    if function_name != "slash_command":
        return f"Unknown function: {function_name}", False
    from core.api_fastapi import get_system
    from core.chat.stream_brain import get_override
    import config
    if not getattr(config, 'AI_SLASH_COMMANDS_ENABLED', False):
        return "slash_command is turned off in Settings > Tools.", False
    if (get_override() or {}).get('agent'):
        return "slash_command: an agent's run can't change a chat's history.", False
    chat = get_system().llm_chat.session_manager._effective_chat_name()
    out = commands.run(str((arguments or {}).get('command') or ''), chat, 'ai')
    return out.get('say', ''), bool(out.get('ok'))
