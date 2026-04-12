# Agent tools — unified AI interface to the agent system (v2)
import json
import logging
import re

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '\U0001f52d'
AVAILABLE_FUNCTIONS = [
    'agent_options',
    'spawn_agent',
    'check_agents',
    'recall_agent',
    'dismiss_agent',
]

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "agent_options",
            "description": "Get available agent types, models, toolsets, and prompts for spawning agents. Call this first if you don't know what's available.",
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
            "name": "spawn_agent",
            "description": "Launch a background agent. IMPORTANT: Always call agent_options() first to see available agent types — there may be specialized types like 'claude_code' for coding tasks. Do NOT default to 'llm' for coding — check what's available. The agent runs in isolation and reports back automatically when done.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mission": {
                        "type": "string",
                        "description": "The task/question for the agent to complete. Be specific — this is the agent's only instruction."
                    },
                    "agent_type": {
                        "type": "string",
                        "description": "Type of agent to spawn. MUST call agent_options() first to see available types. Use 'claude_code' for coding/building tasks, 'llm' for research/analysis. Defaults to 'llm'."
                    },
                    "model": {
                        "type": "string",
                        "description": "Model override (e.g. 'claude-opus-4-6') — only for 'llm' type. Leave empty for auto/default."
                    },
                    "toolset": {
                        "type": "string",
                        "description": "Which toolset the agent can use (e.g. 'default', 'research') — only for 'llm' type."
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Which persona the agent runs under (controls voice, toolset, and scope defaults) — only for 'llm' type. Default: 'agent' (lean background worker — no personality, minimal scopes, safe for automation). Special keyword 'self' inherits the spawning chat's current persona, useful when you want the agent to sound like you and have your scope set. Or pass any persona name string (e.g. 'sapphire', 'cobalt')."
                    },
                    "project_name": {
                        "type": "string",
                        "description": "Project/workspace name — only for 'claude_code' type."
                    },
                    "session_id": {
                        "type": "string",
                        "description": "Resume a previous Claude Code session by ID — only for 'claude_code' type."
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
            "name": "check_agents",
            "description": "Check the status of all active agents. Returns each agent's name, status (running/done/failed), mission, and elapsed time.",
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
            "name": "recall_agent",
            "description": "Get a completed agent's report/findings. The agent's full response is returned.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": "The agent's ID (from spawn_agent or check_agents)"
                    }
                },
                "required": ["agent_id"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "dismiss_agent",
            "description": "Cancel a running agent or clean up a completed one.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": "The agent's ID to dismiss"
                    }
                },
                "required": ["agent_id"]
            }
        }
    },
]


# --- LLM Worker (inlined — runs in exec'd namespace, can't import sibling files) ---

def _create_llm_worker():
    """Create an LLMWorker class. Deferred to avoid import issues at exec time."""
    from core.agents.base_worker import BaseWorker
    import config as cfg

    class LLMWorker(BaseWorker):
        """Runs an isolated LLM + tool loop in a background thread."""

        def __init__(self, agent_id, name, mission, chat_name='', on_complete=None,
                     model='', toolset='default', prompt='agent', **kwargs):
            super().__init__(agent_id, name, mission, chat_name, on_complete)
            self._model = model
            self._toolset = toolset
            self._prompt = prompt

        def run(self):
            from core.continuity.execution_context import ExecutionContext
            from core.api_fastapi import get_system
            from core.personas import persona_manager

            provider_key, model_override = _resolve_model(self._model)

            # Phase 2i: scope values now come from the assigned persona instead of a
            # hardcoded 9-scope dict. The built-in 'agent' persona (added to
            # core/personas/personas.json) provides lean background-worker defaults
            # that match prior behavior: memory/knowledge/people = 'default',
            # goal/email/bitcoin/gcal/telegram/discord = 'none'.
            # Zero-touch for new plugin scopes: they're added to the persona via
            # the Persona editor and flow through automatically.
            persona = persona_manager.get(self._prompt) or {}
            persona_settings = persona.get('settings', {}) if isinstance(persona, dict) else {}
            # Extract only the scope keys from persona settings (other fields like
            # voice/spice don't apply to background agents — provider/model/prompt/
            # toolset are handled explicitly below via worker args).
            scope_settings = {k: v for k, v in persona_settings.items() if k.endswith('_scope')}

            task_settings = {
                'prompt': self._prompt,
                'toolset': self._toolset,
                'provider': provider_key,
                'model': model_override,
                'max_tool_rounds': 10,
                'max_parallel_tools': 3,
                'inject_datetime': True,
                **scope_settings,
            }

            system = get_system()
            fm = system.llm_chat.function_manager
            te = system.llm_chat.tool_engine

            ctx = ExecutionContext(fm, te, task_settings)
            raw = ctx.run(self.mission)
            self.result = re.sub(r'<think>[\s\S]*?</think>\s*', '', raw).strip() if raw else ''
            self.tool_log = ctx.tool_log

    return LLMWorker


def _resolve_model(model_str):
    """Resolve a model string to (provider_key, model_override)."""
    import config as cfg

    if not model_str:
        return 'auto', ''
    if ':' in model_str:
        parts = model_str.split(':', 1)
        return parts[0], parts[1]

    providers_config = {**getattr(cfg, 'LLM_PROVIDERS', {}), **getattr(cfg, 'LLM_CUSTOM_PROVIDERS', {})}
    enabled_keys = [k for k, v in providers_config.items() if v.get('enabled')]

    if model_str in providers_config:
        return model_str, ''
    for key in enabled_keys:
        if model_str.lower() == key.lower():
            return key, ''
    for key in enabled_keys:
        if model_str.lower().startswith(key.lower()):
            return key, model_str
        display = providers_config[key].get('display_name', '').lower()
        if display and model_str.lower().startswith(display.split()[0].lower()):
            return key, model_str

    logger.warning(f"Could not resolve '{model_str}' to a provider, using auto with model override")
    return 'auto', model_str


# --- Registration ---

def _register_llm_type(mgr):
    """Register LLM agent type with the given AgentManager."""
    if 'llm' in mgr.get_types():
        return

    LLMWorker = _create_llm_worker()

    def llm_factory(agent_id, name, mission, chat_name='', on_complete=None, **kwargs):
        return LLMWorker(agent_id, name, mission, chat_name=chat_name, on_complete=on_complete, **kwargs)

    mgr.register_type(
        type_key='llm',
        display_name='LLM Agent',
        factory=llm_factory,
        spawn_args={
            'model': {'type': 'string', 'description': 'Model or roster name (e.g. "claude-opus-4-6"). Empty = current chat model.'},
            'toolset': {'type': 'string', 'description': 'Toolset name (e.g. "default", "research").'},
            'prompt': {'type': 'string', 'description': 'Prompt/personality name. Default: "agent".'},
        },
    )

# Register at load time via module singleton
try:
    from core.agents import agent_manager as _mgr
    if _mgr is not None:
        _register_llm_type(_mgr)
except Exception as e:
    logger.warning(f"Failed to register LLM agent type at load: {e}")


# --- Helpers ---

def _get_manager():
    """Get AgentManager from system singleton."""
    from core.api_fastapi import get_system
    system = get_system()
    if not hasattr(system, 'agent_manager'):
        return None
    return system.agent_manager


def _get_active_chat():
    """Get the current active chat name."""
    from core.api_fastapi import get_system
    try:
        return get_system().llm_chat.get_active_chat() or ''
    except Exception:
        return ''


def _get_current_chat_persona():
    """Return the persona name the active chat is currently running under.

    Used by spawn_agent to resolve the special `prompt='self'` keyword —
    "spawn an agent that inherits the spawning chat's current persona".

    Returns None if no persona is set on the chat, or if anything goes wrong.
    Callers fall back to 'agent' (the lean background-worker persona) in that case.
    """
    from core.api_fastapi import get_system
    try:
        settings = get_system().llm_chat.session_manager.get_chat_settings()
        persona = settings.get('persona')
        return persona if persona else None
    except Exception:
        return None


def _get_plugin_settings():
    """Load agent plugin settings."""
    from pathlib import Path
    settings_file = Path(__file__).parent.parent.parent.parent / "user" / "webui" / "plugins" / "agents.json"
    if settings_file.exists():
        try:
            with open(settings_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


# --- Main dispatch ---

def execute(function_name, arguments, config):
    try:
        manager = _get_manager()
        if not manager:
            return "Agent system not initialized. Is Sapphire fully started?", False

        # Sync max_concurrent from plugin settings
        ps = _get_plugin_settings()
        max_c = ps.get('max_concurrent', 3)
        manager.max_concurrent = max(1, min(5, int(max_c)))

        if function_name == 'agent_options':
            return _agent_options(manager, ps), True

        elif function_name == 'spawn_agent':
            return _spawn_agent(manager, arguments, ps)

        elif function_name == 'check_agents':
            return _check_agents(manager)

        elif function_name == 'recall_agent':
            return _recall_agent(manager, arguments)

        elif function_name == 'dismiss_agent':
            return _dismiss_agent(manager, arguments)

        return f"Unknown agent function: {function_name}", False

    except Exception as e:
        logger.error(f"Agent tool error in {function_name}: {e}", exc_info=True)
        return f"Agent error: {e}", False


def _agent_options(manager, ps):
    lines = []

    # Registered agent types
    types = manager.get_types()
    lines.append("Agent Types:")
    for key, info in types.items():
        lines.append(f"  - {key}: {info['display_name']}")
        if info['spawn_args']:
            for arg_name, arg_info in info['spawn_args'].items():
                req = ' (required)' if arg_info.get('required') else ''
                lines.append(f"      {arg_name}: {arg_info.get('description', '')}{req}")
    if not types:
        lines.append("  (no agent types registered)")

    # Model roster (for LLM agents)
    roster = ps.get('roster', [])
    if roster:
        lines.append("\nModel Roster (for LLM agents):")
        for r in roster:
            lines.append(f"  - \"{r['name']}\" \u2192 {r['provider']}/{r.get('model', 'default')}")
        lines.append("\nUse the roster name in the model parameter (e.g. model=\"Big Brain\").")
        lines.append("Leave model empty to use the current chat model.")
    else:
        import config as cfg
        from core.chat.llm_providers import provider_registry
        core_keys = set(provider_registry.get_core_keys())
        providers_config = {**getattr(cfg, 'LLM_PROVIDERS', {}), **getattr(cfg, 'LLM_CUSTOM_PROVIDERS', {})}
        lines.append("\nAvailable Providers (leave model empty = current chat model):")
        for key, pconf in providers_config.items():
            if not pconf.get('enabled'):
                continue
            name = pconf.get('display_name', key)
            model = pconf.get('model', '')
            if key in core_keys:
                # Core providers: show model override syntax
                meta = provider_registry.get_metadata(key)
                model_opts = meta.get('model_options', {})
                if model_opts:
                    opts = ', '.join(model_opts.keys())
                    lines.append(f"  - {key} ({name}) \u2014 default: {model}")
                    lines.append(f"    models: {opts}")
                    lines.append(f"    usage: model=\"{key}:{list(model_opts.keys())[0]}\"")
                else:
                    lines.append(f"  - {key} ({name}) \u2014 model: {model}")
            else:
                # Custom providers: key IS the model+config combo
                lines.append(f"  - {key} ({name}) \u2014 model: {model or '(auto)'}")
                lines.append(f"    usage: model=\"{key}\"")

    # Toolsets
    from core.toolsets import toolset_manager
    toolset_names = toolset_manager.get_toolset_names()
    lines.append("\nAvailable Toolsets:")
    for t in sorted(toolset_names):
        lines.append(f"  - {t}")

    # Prompts / personas available for the spawn_agent `prompt` parameter.
    # Special: 'self' means "inherit the spawning chat's current persona".
    from core import prompts
    prompt_list = prompts.list_prompts()
    lines.append("\nAvailable Personas (for the `prompt` parameter):")
    lines.append("  - agent  (default, lean background worker — no personality, minimal scopes)")
    lines.append("  - self   (special: inherit the spawning chat's current persona)")
    seen = {'agent'}
    for p in prompt_list:
        name = p if isinstance(p, str) else p.get('name', str(p))
        if name in seen:
            continue
        seen.add(name)
        lines.append(f"  - {name}")

    return '\n'.join(lines)


def _spawn_agent(manager, arguments, ps):
    mission = arguments.get('mission')
    if not mission:
        return "Mission is required.", False

    agent_type = arguments.get('agent_type', 'llm')
    chat_name = _get_active_chat()

    # Build kwargs based on agent type
    kwargs = {}

    if agent_type == 'llm':
        model_arg = arguments.get('model', '')
        kwargs['toolset'] = arguments.get('toolset', ps.get('default_toolset', 'default'))

        # Phase 5: `prompt` resolution.
        # Default is 'agent' (lean background-worker persona with minimal scopes,
        # no personality layer) — matches the tool description contract.
        # Special keyword 'self' means "inherit the spawning chat's current persona"
        # so Sapphire can explicitly extend herself into an agent when she wants to.
        # Fallback for 'self' when no active persona is set: 'agent'.
        requested_prompt = arguments.get('prompt', 'agent')
        if requested_prompt == 'self':
            inherited = _get_current_chat_persona()
            requested_prompt = inherited or 'agent'
            logger.info(f"[agents] spawn_agent: prompt='self' resolved to '{requested_prompt}'")
        kwargs['prompt'] = requested_prompt

        # Resolve roster name to provider:model
        resolved_model = model_arg
        roster = ps.get('roster', [])
        if model_arg and roster:
            match = next((r for r in roster if r['name'].lower() == model_arg.lower()), None)
            if match:
                resolved_model = f"{match['provider']}:{match.get('model', '')}" if match.get('model') else match['provider']
        kwargs['model'] = resolved_model

    elif agent_type == 'claude_code':
        project_name = arguments.get('project_name', '')
        if project_name:
            kwargs['project_name'] = project_name
        session_id = arguments.get('session_id', '')
        if session_id:
            kwargs['session_id'] = session_id

    result = manager.spawn(agent_type, mission, chat_name=chat_name, **kwargs)
    if 'error' in result:
        return result['error'], False

    type_label = agent_type
    types = manager.get_types()
    if agent_type in types:
        type_label = types[agent_type]['display_name']

    return f"Agent {result['name']} dispatched ({type_label}, id: {result['id']}). Results will appear automatically when done.", True


def _check_agents(manager):
    agents = manager.check_all(chat_name=_get_active_chat())
    if not agents:
        return "No active agents.", True
    lines = [f"Agents ({len(agents)}):"]
    for a in agents:
        status_icon = {'running': '\U0001f7e1', 'done': '\U0001f7e2', 'failed': '\U0001f534', 'cancelled': '\u26aa'}.get(a['status'], '\u2753')
        lines.append(f"  {status_icon} {a['name']} [{a['id']}] \u2014 {a['status']} ({a['elapsed']}s)")
        lines.append(f"      Mission: {a['mission'][:100]}")
        tools = a.get('tool_log', [])
        if tools:
            lines.append(f"      Tools called: {', '.join(tools)}")
    return '\n'.join(lines), True


def _recall_agent(manager, arguments):
    agent_id = arguments.get('agent_id')
    if not agent_id:
        return "agent_id is required.", False
    result = manager.recall(agent_id)
    if 'error' in result:
        return result['error'], False
    if result['status'] in ('done', 'failed', 'cancelled'):
        manager.dismiss(agent_id)
    tools_used = result.get('tool_log', [])
    tools_line = f"\nTools used: {', '.join(tools_used)}" if tools_used else "\nTools used: none"
    return f"Agent {result['name']} ({result['status']}, {result.get('elapsed', '?')}s):{tools_line}\n\n{result['result']}", True


def _dismiss_agent(manager, arguments):
    agent_id = arguments.get('agent_id')
    if not agent_id:
        return "agent_id is required.", False
    result = manager.dismiss(agent_id)
    if 'error' in result:
        return result['error'], False
    msg = f"Agent {result['name']} dismissed."
    if result.get('last_result'):
        msg += f"\nLast result: {result['last_result'][:200]}"
    return msg, True
