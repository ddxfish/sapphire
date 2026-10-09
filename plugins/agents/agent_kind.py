# plugins/agents/agent_kind.py - the `llm` agent kind: an isolated LLM + tool loop
#
# The old LLMWorker (plugins/agents/tools/agent_tools.py, retired 2026-10-06)
# moved here behavior for behavior. Every line that encodes a past fix is kept
# and named; tmp/agents-v2.md §18 is the preservation list.
#
# Options (agent_spawn ... options): model (a roster name, a provider key, or
# provider:model), toolset, prompt ('agent' = lean worker with no data scopes,
# 'self' = this chat's persona IDENTITY only, or a persona name = that persona
# in full, scopes included), plus the common name/context.
import logging

from core.agents.base import Agent as BaseAgent

logger = logging.getLogger(__name__)

STORE = 'agents'


def _settings():
    try:
        from core.plugin_loader import plugin_loader
        return plugin_loader.get_plugin_settings(STORE) or {}
    except Exception:
        return {}


def _resolve_model(model_str):
    """A model string -> (provider_key, model_override)."""
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
    # No provider matches: auto, with NO model — a model beside auto is dead
    # at resolve time (the pair rule, 2026-10-07). Agent.__init__ refuses
    # the spawn loudly before this can run; this is the quiet floor.
    logger.warning(f"[agents] Could not resolve '{model_str}' to a provider — running on auto")
    return 'auto', ''


def _current_chat_persona(chat_name=None):
    """The persona `prompt='self'` resolves to. Priority (scout #7): a running
    task's persona first (an agent chain-spawning 'self' must not inherit the
    USER's scopes), else the SNAPSHOTTED chat's persona (day-ruiner #5: a tab
    switch between spawn entry and this read must not flip it), else None."""
    try:
        from core.continuity.execution_context import current_task_persona
        task_persona = current_task_persona.get()
        if task_persona:
            return task_persona
    except Exception:
        pass
    try:
        from core.api_fastapi import get_system
        sm = get_system().llm_chat.session_manager
        settings = (sm.read_chat_settings(chat_name) or {}) if chat_name else sm.get_chat_settings()
        return settings.get('persona') or None
    except Exception:
        return None


# Lean background-worker defaults, used when the built-in 'agent' persona is
# not seeded on an install (persona_manager._load early-returns when
# user/personas/personas.json exists). Every scope 'none': agents are headless
# workers and must not read or write the user's memory/knowledge/people/goals
# (2026-04-19). Keep aligned with core/personas/personas.json::agent.settings.
_LEAN_AGENT = {
    'prompt': 'agent', 'toolset': 'default', 'spice_enabled': False, 'inject_datetime': True,
    'memory_scope': 'none', 'goal_scope': 'none', 'knowledge_scope': 'none', 'people_scope': 'none',
    'email_scope': 'none', 'bitcoin_scope': 'none', 'gcal_scope': 'none', 'telegram_scope': 'none',
    'discord_scope': 'none',
}


# Her memory, as tools. A `self` agent gets the READERS of the `memory`
# category by default - it can read her sheet (read_self pulls memories per
# sheet item) and search - and the WRITERS only when spawned with
# memory='full'. Which tool is which is the tool's own metadata (category +
# writes, core/chat/function_manager.py), not a list kept here. Every other
# data scope (knowledge, people, goals) and every channel scope (email,
# bitcoin, gcal, telegram, discord) stays closed for `self`: H1's hazard was
# the whole bundle riding into a background worker silently (Krem, 2026-10-08).
_MEMORY_MODES = {'none': 'none', 'false': 'none', 'off': 'none', 'no': 'none',
                 'read': 'read', 'read-only': 'read', 'readonly': 'read', 'read_only': 'read',
                 'full': 'full', 'true': 'full', 'write': 'full', 'yes': 'full'}


def _memory_mode(value, default='read'):
    """'none' | 'read' | 'full' from what she passed, or None when it means nothing."""
    if value is None or value == '':
        return default
    if value is False:
        return 'none'
    if value is True:
        return 'full'
    return _MEMORY_MODES.get(str(value).strip().lower())


def _chat_memory_scope(chat_name):
    """The spawning chat's own memory scope - the only one a `self` agent can
    have (never an argument: nothing she passes names a scope)."""
    try:
        from core.api_fastapi import get_system
        sm = get_system().llm_chat.session_manager
        s = (sm.read_chat_settings(chat_name) or {}) if chat_name else sm.get_chat_settings()
        return s.get('memory_scope') or None
    except Exception:
        return None


class Agent(BaseAgent):
    """One mission through ExecutionContext on a background thread."""

    def __init__(self, row, engine):
        super().__init__(row, engine)
        ps = _settings()
        o = self.options
        # `or` (not default=): an explicit toolset='' would resolve to zero
        # tools and the agent would run a silent, useless job. A list or a
        # comma string composes: 'web, files, mindpalace' - each word a saved
        # toolset, a tool category (web, memory, files, ...) or a plugin's
        # name; the first is THE toolset, the rest union on (2026-10-08).
        words = o.get('toolset')
        if isinstance(words, str):
            words = [w.strip() for w in words.split(',')]
        words = [str(w).strip() for w in (words or []) if str(w).strip()]
        self._toolset = (words[0] if words else '') or ps.get('default_toolset') or 'default'
        self._extra_toolsets = words[1:]
        # 'agent' is the lean default; 'self' = this chat's persona identity
        # with her memory as tools (read by default, `memory` says how much);
        # every other scope STRIPPED (H1 2026-04-22). A persona name = full inherit.
        # `or 'agent'` so '' and None both land on the default.
        requested = o.get('prompt') or 'agent'
        self._inherit_scopes = True
        self._memory = 'none'
        if requested == 'self':
            requested = _current_chat_persona(self.chat) or 'agent'
            self._inherit_scopes = False
            self._memory = _memory_mode(o.get('memory'))
            if self._memory is None:
                from core.agents.engine import AgentError
                raise AgentError(f"memory must be false, 'read-only' or 'full', not {o.get('memory')!r}.")
            logger.info(f"[agents] prompt='self' resolved to '{requested}' (memory: {self._memory})")
        elif o.get('memory') not in (None, ''):
            from core.agents.engine import AgentError
            raise AgentError("`memory` is for prompt='self' only: 'agent' has no memory by design, "
                             "and a persona name already brings its own.")
        self._prompt = requested
        # a roster name -> provider:model, case-insensitive
        model_arg = str(o.get('model') or '')
        resolved = model_arg
        for r in ps.get('roster', []) or []:
            if model_arg and str(r.get('name', '')).lower() == model_arg.lower():
                resolved = f"{r['provider']}:{r.get('model', '')}" if r.get('model') else r['provider']
                break
        self._model = resolved
        if resolved and resolved != 'auto' and _resolve_model(resolved)[0] == 'auto':
            # Loud, at spawn (the engine returns this to the caller): a model
            # string no provider matches used to run silently on auto with a
            # dead model override (the pair rule, 2026-10-07).
            from core.agents.engine import AgentError
            raise AgentError(f"No provider matches model '{model_arg}'. Use provider:model, "
                             f"a provider key, or a roster name from the agents settings.")
        if o.get('context'):
            self.mission = f"{self.mission}\n\n---\n\nContext:\n{o['context']}"

    def run(self, mission):
        from core.continuity.execution_context import ExecutionContext
        from core.api_fastapi import get_system
        from core.personas import persona_manager

        provider_key, model_override = _resolve_model(self._model)

        persona = persona_manager.get(self._prompt) or {}
        persona_settings = persona.get('settings', {}) if isinstance(persona, dict) else {}
        if not persona_settings and self._prompt == 'agent':
            logger.info("[agents] 'agent' persona not seeded - using inline lean defaults")
            persona_settings = dict(_LEAN_AGENT)

        system = get_system()
        fm = system.llm_chat.function_manager

        # only the scope keys ride; voice/spice don't apply to a background agent
        scope_settings = {k: v for k, v in persona_settings.items() if k.endswith('_scope')}
        tool_rule = {}
        if not self._inherit_scopes:
            if scope_settings:
                logger.info(f"[agents] prompt='self' - dropping inherited scope settings from "
                            f"'{self._prompt}' persona ({', '.join(scope_settings.keys())})")
            scope_settings = {}
            # her memory, and only hers: the spawning chat's scope, as tools.
            # 'none': no scope → the executor sheds the whole memory category
            # itself. 'read': the scope, the category's readers on, its writers
            # off whatever the toolset said. 'full': the whole category on.
            scope = _chat_memory_scope(self.chat) if self._memory != 'none' else None
            if scope and scope != 'none':
                scope_settings['memory_scope'] = scope
                if self._memory == 'read':
                    tool_rule['drop_tools'] = fm.tools_in_category('memory', writes=True)
                    tool_rule['add_tools'] = fm.tools_in_category('memory', writes=False)
                else:
                    tool_rule['add_tools'] = fm.tools_in_category('memory')
            elif self._memory != 'none':
                logger.info(f"[agents] prompt='self' memory={self._memory}: the chat has no memory scope - none given")
        if self._extra_toolsets:
            tool_rule['extra_toolsets'] = list(self._extra_toolsets)

        # persona names and prompt-file names are two namespaces: resolve the
        # file through the persona's `prompt` field (silent voice loss otherwise)
        prompt_file_name = persona_settings.get('prompt', self._prompt)

        task_settings = {
            'prompt': prompt_file_name,
            'toolset': self._toolset,
            'provider': provider_key,
            'model': model_override,
            'max_tool_rounds': 10,
            'max_parallel_tools': 3,
            'inject_datetime': True,
            **scope_settings,
            **tool_rule,
        }
        if self.privacy:
            # the same carrier the continuity executor uses (Phase 0):
            # ExecutionContext gates provider selection AND network tools off it
            task_settings['privacy_required'] = True

        te = system.llm_chat.tool_engine
        # cancel_check: a stopped agent ends at its next round instead of
        # burning to max_rounds (E2#4). on_tool: the transcript sees each
        # call as it happens. The stream-brain carrier is already installed
        # by the engine around run() (vault hunt H2; scout C M5).
        ctx = ExecutionContext(fm, te, task_settings,
                               session_manager=system.llm_chat.session_manager,
                               cancel_check=self._cancelled.is_set,
                               on_tool=lambda n: self.event('tool_use', {'name': n}))
        raw = ctx.run(mission)
        from core import think
        result = think.strip(raw) if raw else ''
        # a run that degraded to a placeholder (tool loop exhausted, context
        # overflow, empty LLM) is amber, not green (scout #15)
        self.warning = getattr(ctx, 'degraded_reason', None)
        if self._cancelled.is_set():
            return
        self.report(result)
