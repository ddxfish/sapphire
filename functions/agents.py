# functions/agents.py - the four agent tools (tmp/agents-v2.md §3.6)
"""
Thin doors into core/agents/engine.py, the twin of functions/devices.py:
list, look, start, do.

The descriptions carry the KINDS (static: what can be spawned) and nothing
about live agents - tool descriptions are one global object every chat ships
to its provider, so a private chat's agent names must never ride in them
(scout C H1). Everything live is answered at call time, from the caller's
chat: an agent is found only from the chat it belongs to, and anything else
reads exactly like not-found.

All four are is_local: they never touch the network themselves. The engine's
own gates (cloud kinds refused from a private chat - at spawn, say, answer and
wake) are the protection, and they fail closed.
"""
import json
import logging

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '\U0001f52d'
TOOL_CATEGORY = 'agents'
AVAILABLE_FUNCTIONS = ['agent_list', 'agent_peek', 'agent_spawn', 'agent_action']

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "agent_list",
            "description": ("Your agents in this chat, the kinds you can spawn, and what waits in this chat's inbox."),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "agent_peek",
            "description": ("Look in on one agent without interrupting it: status, pending question, latest transcript lines, "
                            "head of its last report."),
            "parameters": {
                "type": "object",
                "properties": {
                    "agent": {"type": "string", "description": "From agent_list"},
                    "what": {"type": "string", "description": "'report' = the last report in full, 'transcript' = more lines. Omit for the overview."}
                },
                "required": ["agent"]
            }
        }
    },
    {
        "type": "function",
        "writes": True,
        "is_local": True,
        "function": {
            "name": "agent_spawn",
            "description": ("Start a background agent. It works while you keep talking, reports into this chat when done, and "
                            "asks here at a fork - answer with agent_action. agent_spawn(kind) alone shows that kind's options. "
                            "Say WHAT to do, not how."),
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string"},
                    "mission": {"type": "string", "description": "Omit to see the kind's options"},
                    "options": {"type": "object",
                                "description": ("Optional. Common: name (a workspace directory name, NOT the agent's name), model, context. "
                                                "agent_spawn(kind) lists the rest. An llm agent's toolset composes from words: web, memory, "
                                                "knowledge, people, goals, files, system, devices, comms, media, meta_danger, agents - or a saved "
                                                "toolset or plugin by name, e.g. 'web, files'.")}
                },
                "required": ["kind"]
            }
        }
    },
    {
        "type": "function",
        "writes": True,
        "is_local": True,
        "function": {
            "name": "agent_action",
            "description": ("Act on one agent: answer resolves its pending question; say sends a follow-up turn to an idle or "
                            "resting agent; stop ends it. agent_action(agent) alone lists what it takes now."),
            "parameters": {
                "type": "object",
                "properties": {
                    "agent": {"type": "string", "description": "From agent_list"},
                    "action": {"type": "string", "enum": ["answer", "say", "stop"], "description": "Omit for the list"},
                    "value": {"type": "string", "description": "The answer (a letter, an option label or your words), or the text to say"}
                },
                "required": ["agent"]
            }
        }
    },
]


def _kinds_line(kinds):
    said = []
    for k in kinds:
        if not k.get('available'):
            said.append(f"{k['kind']} (off: {k['note']})")
            continue
        cloud = ', cloud' if k.get('cloud') else ''
        said.append(f"{k['kind']}: {k.get('description') or k['label']}{cloud}")
    return ' · '.join(said)


def get_tools():
    """The schemas, with the kinds in them. Falls back to the plain TOOLS
    before the engine can answer (FunctionManager builds before the manager)."""
    tools = json.loads(json.dumps(TOOLS))
    try:
        from core.agents import registry
        from core.agents.engine import KIND_ENUM
        kinds = registry.list_kinds()
    except Exception as e:
        logger.debug(f"[AGENTS] tool descriptions built without the kinds: {e}")
        return tools
    by_name = {t['function']['name']: t['function'] for t in tools}
    if kinds:
        by_name['agent_spawn']['description'] += " Kinds: " + _kinds_line([dict(k, available=True) for k in kinds]) + "."
        ids = [k['kind'] for k in kinds]
        kind = by_name['agent_spawn']['parameters']['properties']['kind']
        if 0 < len(ids) <= KIND_ENUM:
            kind['enum'] = ids
        else:
            kind['description'] = 'Which kind of agent, one of: ' + ', '.join(ids)
    else:
        by_name['agent_spawn']['description'] += " No agent kinds are loaded yet (enable the Agents plugin)."
    return tools


def _manager():
    try:
        from core.api_fastapi import get_system
        return getattr(get_system(), 'agent_manager', None)
    except Exception:
        return None


def _chat():
    """The chat THIS turn runs in - the stream override's chat on a background
    lane, else the active chat. Never a guess: the global name alone once
    delivered agent results to whatever chat the operator had open."""
    try:
        from core.api_fastapi import get_system
        return get_system().llm_chat.session_manager._effective_chat_name() or ''
    except Exception:
        return ''


def execute(function_name, arguments, config):
    a = arguments or {}
    mgr = _manager()
    if mgr is None:
        return "The agent system is not up yet. Is Sapphire fully started?", False
    chat = _chat()
    try:
        from core.agents.engine import AgentError
        # A turn that came in over her MCP door may not start or steer agents:
        # a Claude Code session with that door could spawn itself in circles
        # (the devices loop-breaker, scout E).
        try:
            from core import mcp_server
            over_mcp = bool(mcp_server.answering.get())
        except Exception:
            over_mcp = False
        if over_mcp and (function_name == 'agent_spawn' or
                         (function_name == 'agent_action' and str(a.get('action') or '').lower() == 'say')):
            return ("This turn came in over MCP; agents are not spawned or steered from there. "
                    "Answer in your message instead."), False
        if function_name == 'agent_list':
            return mgr.list_text(chat)
        if function_name == 'agent_peek':
            return mgr.peek_text(chat, a.get('agent'), a.get('what'))
        if function_name == 'agent_spawn':
            return mgr.spawn_text(chat, a.get('kind'), a.get('mission'), a.get('options'))
        if function_name == 'agent_action':
            return mgr.action_text(chat, a.get('agent'), a.get('action'), a.get('value'))
        return f"Unknown agent tool '{function_name}'.", False
    except AgentError as e:
        return str(e), False
    except Exception as e:
        logger.error(f"[AGENTS] {function_name} failed: {e}", exc_info=True)
        return f"{function_name} failed: {type(e).__name__}", False
