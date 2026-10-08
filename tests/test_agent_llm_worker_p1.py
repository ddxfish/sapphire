"""The `llm` agent kind (plugins/agents/agent_kind.py) - the LLMWorker's startup
decisions, moved behavior for behavior into agents v2 (tmp/agents-v2.md §18).

Each test pins a past fix: the inline lean 'agent' persona, persona -> prompt
file resolution, 'self' = identity only, toolset/model resolution, the privacy
carry, and the safety caps."""
import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def kind():
    return importlib.import_module('plugins.agents.agent_kind')


class _Engine:
    """What the base Agent needs from the engine, recorded."""

    def __init__(self):
        self.reports, self.events = [], []

    def install_carrier(self, agent):
        return None

    def release_carrier(self, token):
        pass

    def finished(self, agent):
        pass

    def report_out(self, agent, text):
        self.reports.append(text)

    def event_out(self, agent, kind):
        self.events.append(kind)

    def question_out(self, agent, q):
        pass


def _row(**opts):
    return {'id': 't1', 'name': 'Alpha', 'kind': 'llm', 'chat': 'trinity', 'mission': 'test',
            'options': opts, 'privacy': opts.pop('_privacy', False)}


def _capture(monkeypatch, persona):
    """Stub persona_manager, get_system and ExecutionContext; return the
    dict ExecutionContext was built with."""
    from core import personas
    fake_mgr = MagicMock()
    fake_mgr.get.return_value = persona
    monkeypatch.setattr(personas, 'persona_manager', fake_mgr)
    import core.api_fastapi as apifa
    sys_mock = MagicMock()
    monkeypatch.setattr(apifa, '_system', sys_mock, raising=False)
    captured = {}
    from core.continuity import execution_context as exec_ctx_mod

    class _CapturingCtx:
        def __init__(self, fm, te, settings, session_manager=None, **kw):
            captured.update(settings)
            captured['_kw'] = kw
            self.tool_log = []
            self.degraded_reason = None

        def run(self, mission):
            captured['_mission'] = mission
            return 'done'

    monkeypatch.setattr(exec_ctx_mod, 'ExecutionContext', _CapturingCtx)
    monkeypatch.setattr(kind_mod(), '_settings', lambda: {})
    return captured


def kind_mod():
    return importlib.import_module('plugins.agents.agent_kind')


# --- _resolve_model -----------------------------------------------------------------

def test_resolve_model_colon_syntax(kind):
    assert kind._resolve_model('anthropic:claude-haiku-4-5') == ('anthropic', 'claude-haiku-4-5')


def test_resolve_model_empty_returns_auto(kind):
    assert kind._resolve_model('') == ('auto', '')
    assert kind._resolve_model(None) == ('auto', '')


def test_resolve_model_unknown_string_falls_back_to_auto_with_no_model(kind, monkeypatch):
    """The pair rule (2026-10-07): a model beside auto is dead at resolve time,
    so the quiet floor is ('auto', '') — never ('auto', <string>)."""
    import config as cfg
    monkeypatch.setattr(cfg, 'LLM_PROVIDERS', {}, raising=False)
    monkeypatch.setattr(cfg, 'LLM_CUSTOM_PROVIDERS', {}, raising=False)
    assert kind._resolve_model('mystery-model') == ('auto', '')


def test_spawn_refuses_a_model_no_provider_matches(kind, monkeypatch):
    """Loud at spawn: the engine hands this back to the caller instead of
    running the agent on auto with a dead model override."""
    import config as cfg
    monkeypatch.setattr(cfg, 'LLM_PROVIDERS', {}, raising=False)
    monkeypatch.setattr(cfg, 'LLM_CUSTOM_PROVIDERS', {}, raising=False)
    from core.agents.engine import AgentError
    with pytest.raises(AgentError, match="No provider matches"):
        kind.Agent(_row(model='mystery-model'), _Engine())
    kind.Agent(_row(), _Engine())                                 # no model asked → fine


# --- the run's decisions -------------------------------------------------------------

def test_inline_lean_fallback_when_agent_persona_missing(kind, monkeypatch):
    """persona_manager.get('agent') returns nothing (user/personas.json predates
    the built-in): every scope must be 'none' - agents are headless workers
    that must not read or write the user's memory (2026-04-19)."""
    captured = _capture(monkeypatch, None)
    kind.Agent(_row(prompt='agent'), _Engine()).run('test')
    for k in ('memory_scope', 'goal_scope', 'knowledge_scope', 'people_scope', 'email_scope', 'bitcoin_scope'):
        assert captured.get(k) == 'none'


def test_no_inline_fallback_for_a_user_named_persona(kind, monkeypatch):
    """The fallback is ONLY for prompt='agent'; a user persona that resolves to
    nothing gets nothing - we never invent scope defaults over user intent."""
    captured = _capture(monkeypatch, None)
    kind.Agent(_row(prompt='user_custom_persona'), _Engine()).run('test')
    assert not [k for k in captured if k.endswith('_scope')]


def test_persona_prompt_field_resolves_to_the_prompt_file_not_the_persona_name(kind, monkeypatch):
    """Persona names and prompt-file names are two namespaces: 'quirk_bot' may
    use the 'sapphire' prompt file. Loading by persona name fell back to
    "helpful assistant" - silent voice loss."""
    captured = _capture(monkeypatch, {'settings': {'prompt': 'sapphire', 'memory_scope': 'custom', 'voice': 'x'}})
    kind.Agent(_row(prompt='quirk_bot'), _Engine()).run('test')
    assert captured.get('prompt') == 'sapphire'
    assert captured.get('memory_scope') == 'custom'
    assert 'voice' not in captured                                   # only *_scope keys ride


def test_persona_without_prompt_field_falls_back_to_its_name(kind, monkeypatch):
    captured = _capture(monkeypatch, {'settings': {'memory_scope': 'personal'}})
    kind.Agent(_row(prompt='sapphire'), _Engine()).run('test')
    assert captured.get('prompt') == 'sapphire' and captured.get('memory_scope') == 'personal'


def test_toolset_and_resolved_model_reach_the_context_with_the_safety_caps(kind, monkeypatch):
    captured = _capture(monkeypatch, {'settings': {'prompt': 'sapphire'}})
    kind.Agent(_row(prompt='quirk_bot', toolset='research', model='anthropic:claude-haiku-4-5'), _Engine()).run('test')
    assert captured['toolset'] == 'research'
    assert captured['provider'] == 'anthropic' and captured['model'] == 'claude-haiku-4-5'
    assert captured['max_tool_rounds'] == 10 and captured['max_parallel_tools'] == 3
    assert captured['inject_datetime'] is True
    assert callable(captured['_kw'].get('cancel_check')) and callable(captured['_kw'].get('on_tool'))


def test_self_means_identity_only_scopes_stripped(kind, monkeypatch):
    """H1 2026-04-22: prompt='self' inherits the chat's persona IDENTITY but
    not its data scopes - 'self' used to bring the whole bundle silently.
    2026-10-08: her MEMORY rides again (the chat's scope, read tools by
    default - see the memory tests below); goals, knowledge, people and every
    channel scope still never do."""
    captured = _capture(monkeypatch, {'settings': {'prompt': 'sapphire', 'memory_scope': 'personal', 'goal_scope': 'mine'}})
    monkeypatch.setattr(kind, '_current_chat_persona', lambda chat=None: 'sapphire')
    monkeypatch.setattr(kind, '_chat_memory_scope', lambda chat=None: 'personal')
    a = kind.Agent(_row(prompt='self'), _Engine())
    assert a._prompt == 'sapphire' and a._inherit_scopes is False
    a.run('test')
    assert captured.get('prompt') == 'sapphire'
    assert [k for k in captured if k.endswith('_scope')] == ['memory_scope']
    # an explicit persona name keeps full inherit
    captured2 = _capture(monkeypatch, {'settings': {'prompt': 'sapphire', 'memory_scope': 'personal'}})
    kind.Agent(_row(prompt='sapphire'), _Engine()).run('test')
    assert captured2.get('memory_scope') == 'personal'


def test_empty_prompt_and_toolset_fall_to_the_defaults(kind, monkeypatch):
    """`or`, not default=: prompt='' used to bypass the 'self' check and land
    with no persona; toolset='' resolved to zero tools."""
    captured = _capture(monkeypatch, None)
    a = kind.Agent(_row(prompt='', toolset=''), _Engine())
    assert a._prompt == 'agent' and a._toolset == 'default'
    a.run('test')
    assert captured.get('memory_scope') == 'none'


def test_a_private_spawn_carries_privacy_required(kind, monkeypatch):
    """F4: the worker thread can't read the caller's ContextVar; the row carries
    the snapshot and ExecutionContext gates the provider off it."""
    captured = _capture(monkeypatch, None)
    kind.Agent(_row(prompt='agent', _privacy=True), _Engine()).run('test')
    assert captured.get('privacy_required') is True
    captured2 = _capture(monkeypatch, None)
    kind.Agent(_row(prompt='agent'), _Engine()).run('test')
    assert 'privacy_required' not in captured2


def test_roster_name_resolves_case_insensitively_and_context_is_appended(kind, monkeypatch):
    monkeypatch.setattr(kind, '_settings', lambda: {'roster': [{'name': 'Big Brain', 'provider': 'anthropic', 'model': 'opus'}]})
    a = kind.Agent(_row(model='big brain', context='the API docs'), _Engine())
    assert a._model == 'anthropic:opus'
    assert a.mission.endswith('Context:\nthe API docs')


def test_the_result_is_reported_stripped_and_degradation_becomes_a_warning(kind, monkeypatch):
    from core import personas
    monkeypatch.setattr(personas, 'persona_manager', MagicMock(get=MagicMock(return_value=None)))
    import core.api_fastapi as apifa
    monkeypatch.setattr(apifa, '_system', MagicMock(), raising=False)
    from core.continuity import execution_context as exec_ctx_mod

    class _Ctx:
        def __init__(self, *a, **kw):
            self.tool_log = []
            self.degraded_reason = 'tool loop exhausted'

        def run(self, mission):
            return '<think>hmm</think>the answer'
    monkeypatch.setattr(exec_ctx_mod, 'ExecutionContext', _Ctx)
    monkeypatch.setattr(kind, '_settings', lambda: {})
    eng = _Engine()
    a = kind.Agent(_row(prompt='agent'), eng)
    a.run('test')
    assert eng.reports == ['the answer'] and a.warning == 'tool loop exhausted'


# --- `self` and her memory (Krem, 2026-10-08) ----------------------------------------

def _self_agent(kind, monkeypatch, memory=None, chat_scope='personal'):
    captured = _capture(monkeypatch, {'settings': {'prompt': 'sapphire', 'memory_scope': 'persona-wide',
                                                   'email_scope': 'inbox', 'bitcoin_scope': 'wallet'}})
    monkeypatch.setattr(kind, '_current_chat_persona', lambda chat=None: 'sapphire')
    monkeypatch.setattr(kind, '_chat_memory_scope', lambda chat=None: chat_scope)
    opts = {'prompt': 'self'} if memory is None else {'prompt': 'self', 'memory': memory}
    kind.Agent(_row(**opts), _Engine()).run('test')
    return captured


def test_self_reads_her_memory_by_default_and_nothing_else(kind, monkeypatch):
    """read_self pulls memories per sheet item: without the scope a `self`
    agent could not read her own sheet. The scope is the CHAT's (never an
    argument), the write tools are withheld, channels never ride."""
    c = _self_agent(kind, monkeypatch)
    assert c['memory_scope'] == 'personal', "the spawning chat's scope, not the persona's"
    assert [k for k in c if k.endswith('_scope')] == ['memory_scope']
    assert set(c['add_tools']) == set(kind.MEMORY_READ_TOOLS)
    assert set(c['drop_tools']) == set(kind.MEMORY_READ_TOOLS + kind.MEMORY_WRITE_TOOLS)
    assert 'save_memory' not in c['add_tools'] and 'read_self' in c['add_tools']


def test_self_memory_full_adds_the_write_tools(kind, monkeypatch):
    c = _self_agent(kind, monkeypatch, memory='full')
    assert set(c['add_tools']) == set(kind.MEMORY_READ_TOOLS + kind.MEMORY_WRITE_TOOLS)
    assert c['memory_scope'] == 'personal'


def test_self_memory_false_is_identity_only_with_no_memory_tools(kind, monkeypatch):
    for v in (False, 'false', 'none'):
        c = _self_agent(kind, monkeypatch, memory=v)
        assert not [k for k in c if k.endswith('_scope')]
        assert 'add_tools' not in c and set(c['drop_tools']) == set(kind.MEMORY_READ_TOOLS + kind.MEMORY_WRITE_TOOLS)


def test_self_in_a_chat_without_a_memory_scope_gets_none(kind, monkeypatch):
    c = _self_agent(kind, monkeypatch, memory='full', chat_scope='none')
    assert 'memory_scope' not in c and 'add_tools' not in c


def test_memory_is_refused_off_self_and_when_misspelt(kind, monkeypatch):
    from core.agents.engine import AgentError
    _capture(monkeypatch, None)
    with pytest.raises(AgentError, match="prompt='self' only"):
        kind.Agent(_row(prompt='agent', memory='full'), _Engine())
    with pytest.raises(AgentError, match="prompt='self' only"):
        kind.Agent(_row(prompt='sapphire', memory='read-only'), _Engine())
    monkeypatch.setattr(kind, '_current_chat_persona', lambda chat=None: 'sapphire')
    with pytest.raises(AgentError, match="read-only"):
        kind.Agent(_row(prompt='self', memory='sometimes'), _Engine())
    assert kind._memory_mode(None) == 'read' and kind._memory_mode(True) == 'full' and kind._memory_mode('READ_ONLY') == 'read'
