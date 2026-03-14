# Scout tools — AI interface to the scout system
import json
import logging

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '\U0001f52d'
AVAILABLE_FUNCTIONS = [
    'get_scout_options',
    'spawn_scout',
    'check_scouts',
    'recall_scout',
    'dismiss_scout',
]

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "get_scout_options",
            "description": "Get available models, toolsets, and prompts for spawning scouts. Call this first if you don't know what's available.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "spawn_scout",
            "description": "Launch a background AI scout to perform a task independently. The scout runs in isolation with its own context and reports back when done. Use check_scouts() to monitor and recall_scout() to get the report.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mission": {
                        "type": "string",
                        "description": "The task/question for the scout to complete. Be specific — this is the scout's only instruction."
                    },
                    "model": {
                        "type": "string",
                        "description": "Model override (e.g. 'claude-opus-4-6'). Leave empty for auto/default."
                    },
                    "toolset": {
                        "type": "string",
                        "description": "Which toolset the scout can use (e.g. 'default', 'research'). Defaults to plugin setting."
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Which prompt/personality the scout uses. Default: 'sapphire'."
                    }
                },
                "required": ["mission"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "check_scouts",
            "description": "Check the status of all active scouts. Returns each scout's name, status (running/done/failed), mission, and elapsed time.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "recall_scout",
            "description": "Get a completed scout's report/findings. The scout's full response is returned.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scout_id": {
                        "type": "string",
                        "description": "The scout's ID (from spawn_scout or check_scouts)"
                    }
                },
                "required": ["scout_id"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "dismiss_scout",
            "description": "Cancel a running scout or clean up a completed one. Returns the scout's last status.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scout_id": {
                        "type": "string",
                        "description": "The scout's ID to dismiss"
                    }
                },
                "required": ["scout_id"]
            }
        }
    },
]


def _get_manager():
    """Get ScoutManager from system singleton."""
    from core.api_fastapi import get_system
    system = get_system()
    if not hasattr(system, 'scout_manager'):
        return None
    return system.scout_manager


def _get_active_chat():
    """Get the current active chat name."""
    from core.api_fastapi import get_system
    try:
        return get_system().llm_chat.get_active_chat() or ''
    except Exception:
        return ''


def _get_plugin_settings():
    """Load scout plugin settings."""
    from pathlib import Path
    settings_file = Path(__file__).parent.parent.parent.parent / "user" / "webui" / "plugins" / "scouts.json"
    if settings_file.exists():
        try:
            with open(settings_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def execute(function_name, arguments, config):
    try:
        manager = _get_manager()
        if not manager:
            return "Scout system not initialized. Is Sapphire fully started?", False

        # Sync max_concurrent from plugin settings
        ps = _get_plugin_settings()
        max_c = ps.get('max_concurrent', 3)
        manager.max_concurrent = max(1, min(5, int(max_c)))

        if function_name == 'get_scout_options':
            options = manager.get_options()
            lines = []
            lines.append("Available Models:")
            for m in options['models']:
                lines.append(f"  - {m['key']} ({m['name']}) — model: {m['model']}")
            if not options['models']:
                lines.append("  (no providers enabled)")
            lines.append("\nAvailable Toolsets:")
            for t in options['toolsets']:
                lines.append(f"  - {t}")
            lines.append("\nAvailable Prompts:")
            for p in options['prompts']:
                name = p if isinstance(p, str) else p.get('name', str(p))
                lines.append(f"  - {name}")
            return '\n'.join(lines), True

        elif function_name == 'spawn_scout':
            mission = arguments.get('mission')
            if not mission:
                return "Mission is required.", False
            model = arguments.get('model', '')
            toolset = arguments.get('toolset', ps.get('default_toolset', 'default'))
            prompt = arguments.get('prompt', 'sapphire')
            result = manager.spawn(mission, model=model, toolset=toolset, prompt=prompt, chat_name=_get_active_chat())
            if 'error' in result:
                return result['error'], False
            provider_info = f" on {model}" if model else ""
            return f"Scout {result['name']} dispatched{provider_info} (id: {result['id']}). Use check_scouts() to monitor progress.", True

        elif function_name == 'check_scouts':
            scouts = manager.check_all(chat_name=_get_active_chat())
            if not scouts:
                return "No active scouts.", True
            lines = [f"Scouts ({len(scouts)}):"]
            for s in scouts:
                status_icon = {'running': '\U0001f7e1', 'done': '\U0001f7e2', 'failed': '\U0001f534', 'cancelled': '\u26aa'}.get(s['status'], '\u2753')
                lines.append(f"  {status_icon} {s['name']} [{s['id']}] — {s['status']} ({s['elapsed']}s)")
                lines.append(f"      Mission: {s['mission'][:100]}")
                tools = s.get('tool_log', [])
                if tools:
                    lines.append(f"      Tools called: {', '.join(tools)}")
            return '\n'.join(lines), True

        elif function_name == 'recall_scout':
            scout_id = arguments.get('scout_id')
            if not scout_id:
                return "scout_id is required.", False
            result = manager.recall(scout_id)
            if 'error' in result:
                return result['error'], False
            # Auto-dismiss after recall — clears the pill from UI
            if result['status'] in ('done', 'failed', 'cancelled'):
                manager.dismiss(scout_id)
            tools_used = result.get('tool_log', [])
            tools_line = f"\nTools used: {', '.join(tools_used)}" if tools_used else "\nTools used: none"
            return f"Scout {result['name']} ({result['status']}, {result.get('elapsed', '?')}s):{tools_line}\n\n{result['result']}", True

        elif function_name == 'dismiss_scout':
            scout_id = arguments.get('scout_id')
            if not scout_id:
                return "scout_id is required.", False
            result = manager.dismiss(scout_id)
            if 'error' in result:
                return result['error'], False
            msg = f"Scout {result['name']} dismissed."
            if result.get('last_result'):
                msg += f"\nLast result: {result['last_result'][:200]}"
            return msg, True

        return f"Unknown scout function: {function_name}", False

    except Exception as e:
        logger.error(f"Scout tool error in {function_name}: {e}", exc_info=True)
        return f"Scout error: {e}", False
