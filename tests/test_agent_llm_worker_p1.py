"""Surface 5 P1 — LLMWorker + agent plugin registration guards.

Covers:
  5.22 register_type idempotent at runtime
  5.27 agent persona inline fallback when 'agent' persona missing
  5.28 persona.prompt field resolves prompt file (NOT persona name) — Phase 5
  5.29 resolve_model colon syntax splits provider:model
  5.32 prompt='self' path (covered indirectly via persona_manager integration)

These guard the LLMWorker's startup decisions. A regression here means a
spawned agent runs with the wrong persona / scopes / model silently.

See tmp/coverage-test-plan.md Surface 5 P1.
"""
import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _load_agent_tools():
    """Load plugins/agents/tools/agent_tools.py by file path.

    The directory is fine for import (no hyphens), but loading by file path
    keeps the test self-contained and avoids coupling to plugin-loader state.
    """
    if 'agent_tools_test' in sys.modules:
        return sys.modules['agent_tools_test']
    project_root = Path(__file__).resolve().parent.parent
    module_path = project_root / 'plugins' / 'agents' / 'tools' / 'agent_tools.py'
    spec = importlib.util.spec_from_file_location('agent_tools_test', module_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules['agent_tools_test'] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def at():
    """The agent_tools module."""
    return _load_agent_tools()


# ─── 5.29 _resolve_model colon syntax ────────────────────────────────────────

def test_resolve_model_colon_syntax(at):
    """[PROACTIVE] 'provider:model' string → (provider, model_override).
    Lets users force a specific model regardless of provider config."""
    provider, model = at._resolve_model('anthropic:claude-opus-4-6')
    assert provider == 'anthropic'
    assert model == 'claude-opus-4-6'

    # Empty after colon is still a valid split (empty override)
    provider, model = at._resolve_model('openai:')
    assert provider == 'openai'
    assert model == ''


def test_resolve_model_empty_returns_auto(at):
    """Empty/None model → 'auto' provider, empty override."""
    provider, model = at._resolve_model('')
    assert provider == 'auto'
    assert model == ''


def test_resolve_model_unknown_string_falls_back_to_auto(at):
    """An unresolvable model name → 'auto' provider, string passed as override."""
    provider, model = at._resolve_model('some-unknown-model-xyz')
    assert provider == 'auto'
    # Exact content of model is implementation detail; assert it's non-empty
    # so the provider still has SOMETHING to try
    assert model  # Non-empty fallback


# ─── 5.27 Agent persona inline fallback when 'agent' persona missing ─────────

def test_llm_worker_inline_fallback_when_agent_persona_missing(at, monkeypatch):
    """[REGRESSION_GUARD] If persona_manager.get('agent') returns nothing
    (user/personas.json predates the 'agent' built-in), the worker MUST
    use inline lean defaults (memory=default, goal=none, etc).

    Without the fallback, scope settings are empty → all scopes stay at
    defaults → the 'lean background worker' intent is violated. This was
    the Phase 5 day-ruiner scout finding.
    """
    from core import personas
    LLMWorker = at._create_llm_worker()

    # Mock persona_manager to return None for 'agent'
    fake_mgr = MagicMock()
    fake_mgr.get.return_value = None
    monkeypatch.setattr(personas, 'persona_manager', fake_mgr)

    # Stub get_system + ExecutionContext so run() exits early after persona resolution
    import core.api_fastapi as apifa
    sys_mock = MagicMock()
    sys_mock.llm_chat.function_manager = MagicMock()
    sys_mock.llm_chat.tool_engine = MagicMock()
    monkeypatch.setattr(apifa, '_system', sys_mock, raising=False)

    captured_settings = {}
    from core.continuity import execution_context as exec_ctx_mod

    class _CapturingCtx:
        def __init__(self, fm, te, settings):
            captured_settings.update(settings)
            self.tool_log = []
        def run(self, mission):
            return ''

    monkeypatch.setattr(exec_ctx_mod, 'ExecutionContext', _CapturingCtx)

    worker = LLMWorker(
        agent_id='t1', name='Alpha', mission='test',
        chat_name='trinity', prompt='agent',
    )
    worker.run()

    # Inline fallback kicked in with lean defaults. 2026-04-19: every scope
    # flipped to 'none' — agents shouldn't read from or write to the user's
    # personal memory/knowledge/people/goals by default. See agent_tools.py
    # for the rationale comment.
    assert captured_settings.get('memory_scope') == 'none'
    assert captured_settings.get('goal_scope') == 'none'
    assert captured_settings.get('knowledge_scope') == 'none'
    assert captured_settings.get('people_scope') == 'none'
    assert captured_settings.get('email_scope') == 'none'
    assert captured_settings.get('bitcoin_scope') == 'none'


def test_llm_worker_no_inline_fallback_for_non_agent_persona(at, monkeypatch):
    """Inverse: fallback is ONLY for prompt='agent'. Any other persona that
    resolves to empty settings gets empty settings — we don't invent scope
    defaults for user-named personas (that would silently override user
    intent)."""
    from core import personas
    LLMWorker = at._create_llm_worker()

    fake_mgr = MagicMock()
    fake_mgr.get.return_value = None
    monkeypatch.setattr(personas, 'persona_manager', fake_mgr)

    import core.api_fastapi as apifa
    sys_mock = MagicMock()
    sys_mock.llm_chat.function_manager = MagicMock()
    sys_mock.llm_chat.tool_engine = MagicMock()
    monkeypatch.setattr(apifa, '_system', sys_mock, raising=False)

    captured_settings = {}
    from core.continuity import execution_context as exec_ctx_mod

    class _CapturingCtx:
        def __init__(self, fm, te, settings):
            captured_settings.update(settings)
            self.tool_log = []
        def run(self, mission):
            return ''

    monkeypatch.setattr(exec_ctx_mod, 'ExecutionContext', _CapturingCtx)

    worker = LLMWorker(
        agent_id='t1', name='Alpha', mission='test',
        chat_name='trinity', prompt='user_custom_persona',
    )
    worker.run()

    # For non-'agent' prompts, no scope keys should be injected from inline defaults
    scope_keys = [k for k in captured_settings if k.endswith('_scope')]
    assert not scope_keys, \
        f"inline fallback wrongly fired for non-agent persona: {scope_keys}"


# ─── 5.28 persona.prompt field resolves to prompt FILE name (Phase 5) ────────

def test_llm_worker_persona_prompt_field_resolves_to_prompt_file_not_persona_name(
    at, monkeypatch,
):
    """[REGRESSION_GUARD] A persona's `settings.prompt` field is the PROMPT
    FILE name, not the persona name. If a user has persona='quirk_bot' whose
    settings are {'prompt': 'sapphire', ...}, the worker should load the
    'sapphire' prompt file, NOT a 'quirk_bot' one (which may not exist).

    Before the Phase 5 fix, the worker passed persona name as prompt name,
    silently falling back to 'You are a helpful assistant'.
    """
    from core import personas
    LLMWorker = at._create_llm_worker()

    fake_mgr = MagicMock()
    fake_mgr.get.return_value = {
        'settings': {
            'prompt': 'sapphire',  # <-- the actual prompt file name
            'memory_scope': 'custom',
        },
    }
    monkeypatch.setattr(personas, 'persona_manager', fake_mgr)

    import core.api_fastapi as apifa
    sys_mock = MagicMock()
    sys_mock.llm_chat.function_manager = MagicMock()
    sys_mock.llm_chat.tool_engine = MagicMock()
    monkeypatch.setattr(apifa, '_system', sys_mock, raising=False)

    captured_settings = {}
    from core.continuity import execution_context as exec_ctx_mod

    class _CapturingCtx:
        def __init__(self, fm, te, settings):
            captured_settings.update(settings)
            self.tool_log = []
        def run(self, mission):
            return ''

    monkeypatch.setattr(exec_ctx_mod, 'ExecutionContext', _CapturingCtx)

    worker = LLMWorker(
        agent_id='t1', name='Alpha', mission='test',
        chat_name='trinity', prompt='quirk_bot',  # persona name
    )
    worker.run()

    # prompt should be the FILE name from settings, not the persona name
    assert captured_settings.get('prompt') == 'sapphire', \
        f"prompt field not resolved correctly: got {captured_settings.get('prompt')!r}"
    # Scope from persona also carries through
    assert captured_settings.get('memory_scope') == 'custom'


def test_llm_worker_persona_without_prompt_field_falls_back_to_persona_name(
    at, monkeypatch,
):
    """If persona.settings doesn't set 'prompt', fall back to the persona name
    (prompt file name == persona name, the common case)."""
    from core import personas
    LLMWorker = at._create_llm_worker()

    fake_mgr = MagicMock()
    fake_mgr.get.return_value = {
        'settings': {
            # No 'prompt' field
            'memory_scope': 'personal',
        },
    }
    monkeypatch.setattr(personas, 'persona_manager', fake_mgr)

    import core.api_fastapi as apifa
    sys_mock = MagicMock()
    sys_mock.llm_chat.function_manager = MagicMock()
    sys_mock.llm_chat.tool_engine = MagicMock()
    monkeypatch.setattr(apifa, '_system', sys_mock, raising=False)

    captured = {}
    from core.continuity import execution_context as exec_ctx_mod

    class _CapturingCtx:
        def __init__(self, fm, te, settings):
            captured.update(settings)
            self.tool_log = []
        def run(self, mission):
            return ''

    monkeypatch.setattr(exec_ctx_mod, 'ExecutionContext', _CapturingCtx)

    worker = LLMWorker(
        agent_id='t1', name='Alpha', mission='test',
        chat_name='trinity', prompt='sapphire',
    )
    worker.run()

    assert captured.get('prompt') == 'sapphire'
    assert captured.get('memory_scope') == 'personal'


# ─── 5.22 register_llm_type is idempotent ────────────────────────────────────

def test_register_llm_type_idempotent(at):
    """[PROACTIVE] Calling _register_llm_type twice on the same manager must
    not raise or double-register. Plugin reload cycles re-run the module
    bottom, which calls _register_llm_type — idempotence required.
    """
    from core.agents.manager import AgentManager
    mgr = AgentManager(max_concurrent=3)
    at._register_llm_type(mgr)
    assert 'llm' in mgr.get_types()

    # Second call must be a no-op
    at._register_llm_type(mgr)
    types = mgr.get_types()
    # Still exactly one 'llm' entry, manager is intact
    assert 'llm' in types
    assert types['llm']['display_name'] == 'LLM Agent'


# ─── Bonus: LLMWorker preserves task_settings toolset + provider resolution ──

def test_llm_worker_passes_toolset_and_resolved_model(at, monkeypatch):
    """Task settings passed to ExecutionContext must include the worker's
    toolset and the resolved provider/model tuple. Regression here = agent
    runs with wrong tools or wrong model silently."""
    from core import personas
    LLMWorker = at._create_llm_worker()

    fake_mgr = MagicMock()
    fake_mgr.get.return_value = {'settings': {'prompt': 'sapphire'}}
    monkeypatch.setattr(personas, 'persona_manager', fake_mgr)

    import core.api_fastapi as apifa
    sys_mock = MagicMock()
    sys_mock.llm_chat.function_manager = MagicMock()
    sys_mock.llm_chat.tool_engine = MagicMock()
    monkeypatch.setattr(apifa, '_system', sys_mock, raising=False)

    captured = {}
    from core.continuity import execution_context as exec_ctx_mod

    class _CapturingCtx:
        def __init__(self, fm, te, settings):
            captured.update(settings)
            self.tool_log = []
        def run(self, mission):
            return ''

    monkeypatch.setattr(exec_ctx_mod, 'ExecutionContext', _CapturingCtx)

    worker = LLMWorker(
        agent_id='t1', name='Alpha', mission='test',
        chat_name='trinity', prompt='quirk_bot',
        toolset='research',
        model='anthropic:claude-haiku-4-5',
    )
    worker.run()

    assert captured['toolset'] == 'research'
    assert captured['provider'] == 'anthropic'
    assert captured['model'] == 'claude-haiku-4-5'
    # Safety defaults always present
    assert captured['max_tool_rounds'] == 10
    assert captured['max_parallel_tools'] == 3
