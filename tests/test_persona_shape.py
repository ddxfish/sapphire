"""
Persona structure validation (Breadth-5).

Walks personas.json and asserts every persona has the keys persona_manager
expects. This would have caught the agent-persona seeding bug from Phase 5
(persona missing from existing installs because _load() early-returns when
user/personas/personas.json exists).

Why we walk the CORE personas.json (not user file): the user file is the
authoritative source after first run, but the CORE file is what ships and
what new installs seed from. If a built-in persona is malformed in core,
every new user gets the bug.

Run with: pytest tests/test_persona_shape.py -v
"""
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

CORE_PERSONAS_PATH = PROJECT_ROOT / "core" / "personas" / "personas.json"


def load_core_personas():
    """Load and filter out _comment-prefixed metadata keys."""
    data = json.loads(CORE_PERSONAS_PATH.read_text(encoding='utf-8'))
    return {k: v for k, v in data.items() if not k.startswith('_')}


CORE_PERSONAS = load_core_personas()


@pytest.mark.parametrize("persona_key", sorted(CORE_PERSONAS.keys()))
def test_persona_has_top_level_required_fields(persona_key):
    """Every persona must have name, settings, and a settings dict."""
    persona = CORE_PERSONAS[persona_key]
    assert isinstance(persona, dict), f"{persona_key}: persona must be a dict"
    assert "name" in persona, f"{persona_key}: missing 'name'"
    assert "settings" in persona, f"{persona_key}: missing 'settings'"
    assert isinstance(persona["settings"], dict), \
        f"{persona_key}: 'settings' must be a dict"


@pytest.mark.parametrize("persona_key", sorted(CORE_PERSONAS.keys()))
def test_persona_settings_have_core_keys(persona_key):
    """Every persona's settings must include the bare minimum keys.

    These are the ones the chat session manager assumes will be present —
    if they're missing, you get KeyErrors at chat-spawn time.
    """
    settings = CORE_PERSONAS[persona_key]["settings"]
    required = ["prompt", "toolset"]
    for key in required:
        assert key in settings, f"{persona_key}: settings missing '{key}'"


# The "core 4" memory scopes that come from the memory plugin. Every persona
# in core/personas/personas.json declares values for these — they're the
# universally-required ones. Plugin scopes (email, bitcoin, gcal, telegram,
# discord) are NOT required by every persona — see test_known_scope_gap below.
CORE_PERSONA_SCOPE_KEYS = ('memory_scope', 'goal_scope', 'knowledge_scope', 'people_scope')


@pytest.mark.parametrize("persona_key", sorted(CORE_PERSONAS.keys()))
def test_persona_settings_have_core_memory_scopes(persona_key):
    """Every persona must declare values for the core 4 memory scopes.

    These are memory_scope, goal_scope, knowledge_scope, people_scope — the
    scopes from the memory plugin (Phase 4). If a persona is missing one of
    these, switching to that persona will leak whatever value was in the
    previous chat's scope ContextVar.

    Plugin-specific scopes (email, bitcoin, etc.) are tracked separately
    by test_known_scope_gap.
    """
    settings = CORE_PERSONAS[persona_key]["settings"]
    for key in CORE_PERSONA_SCOPE_KEYS:
        assert key in settings, \
            f"{persona_key}: settings missing core memory scope key '{key}'"


@pytest.mark.xfail(reason="Known gap: built-in personas predate plugin scopes "
                          "(email/bitcoin/gcal/telegram/discord). Adding values "
                          "for these is a Phase 5 cleanup item.",
                   strict=False)
def test_known_scope_gap_personas_missing_plugin_scope_keys():
    """DOCUMENTED FAILURE — flags the persona/scope gap without breaking the suite.

    Built-in personas in core/personas/personas.json were authored before the
    Phase 3-4 plugin scope work, so most of them don't declare email_scope,
    bitcoin_scope, gcal_scope, etc. The 'agent' persona DOES declare them all
    (it was added in Phase 5).

    PYTEST PRIMER — xfail
    ─────────────────────
    `@pytest.mark.xfail` marks a test as "expected to fail." It still runs;
    if it fails, that counts as a pass (XFAIL). If it unexpectedly *passes*
    (the gap got fixed), it shows as XPASS — a signal that you can promote
    it from xfail to a real test. `strict=False` means XPASS doesn't fail
    the suite, just shows up in the summary.

    Use xfail when: the gap is real and you want it visible in test output,
    but fixing it is out of scope for the current change.
    """
    from core.chat.function_manager import scope_setting_keys

    missing = {}
    for persona_key, persona in CORE_PERSONAS.items():
        settings = persona["settings"]
        gaps = [k for k in scope_setting_keys() if k not in settings]
        if gaps:
            missing[persona_key] = gaps

    assert not missing, \
        f"Personas missing scope keys: {missing}"


def test_agent_persona_exists_in_core():
    """The 'agent' persona is required for spawn_agent to have a sane default.

    Phase 5 added this. If it's missing from core personas.json, new installs
    won't have a fallback for background agent work.
    """
    assert "agent" in CORE_PERSONAS, \
        "core personas.json missing 'agent' persona — spawn_agent default broken"
    agent = CORE_PERSONAS["agent"]
    assert agent["settings"].get("prompt") == "agent", \
        "agent persona must reference the 'agent' prompt file"
