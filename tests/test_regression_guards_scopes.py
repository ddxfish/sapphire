"""Regression guards for scope-propagation bugs shipped 2026-04-18.

Covered:
  R5  [REGRESSION_GUARD] _apply_chat_settings aligns scope_rag to active chat
                         (Race #6 — fix in api_fastapi.py:478-482)
  R6  [REGRESSION_GUARD] _apply_chat_settings resets unmentioned scopes to default
                         (no bleed from previous chat's values)

These assert on ContextVar state directly — we drive _apply_chat_settings from
a mock system and observe SCOPE_REGISTRY outcomes.

See tmp/coverage-test-plan.md.
"""
from unittest.mock import MagicMock

import pytest


def _make_mock_system(active_chat='trinity'):
    """Minimal system stub with just the hooks _apply_chat_settings touches."""
    from core.chat.function_manager import FunctionManager
    sys = MagicMock()
    fm = FunctionManager.__new__(FunctionManager)
    fm._tools_lock = __import__('threading').Lock()
    fm.all_possible_tools = []
    fm._mode_filters = {}
    fm.current_toolset_name = "none"
    fm.function_modules = {}
    sys.llm_chat.function_manager = fm
    sys.llm_chat.session_manager.get_active_chat_name.return_value = active_chat
    return sys


# ─── R5: RAG scope aligns to active chat ─────────────────────────────────────

def test_R5_apply_chat_settings_sets_rag_scope_to_active_chat(scope_snapshot):
    """[REGRESSION_GUARD] After _apply_chat_settings, scope_rag must be
    `__rag__:{active_chat_name}`. Before the 2026-04-18 fix, routes that
    activated a chat without sending a message left scope_rag pointing at
    the previous chat's docs."""
    from core.api_fastapi import _apply_chat_settings
    from core.chat.function_manager import SCOPE_REGISTRY

    sys = _make_mock_system(active_chat='trinity')
    _apply_chat_settings(sys, {})

    assert SCOPE_REGISTRY['rag']['var'].get() == '__rag__:trinity'


def test_R5_rag_scope_updates_when_switching_active_chat(scope_snapshot):
    """Switching active chat through two calls of _apply_chat_settings must
    realign scope_rag to each chat in turn — this is the exact flow of a
    persona activation followed by a chat switch."""
    from core.api_fastapi import _apply_chat_settings
    from core.chat.function_manager import SCOPE_REGISTRY

    sys_a = _make_mock_system(active_chat='project_alpha')
    _apply_chat_settings(sys_a, {})
    assert SCOPE_REGISTRY['rag']['var'].get() == '__rag__:project_alpha'

    sys_b = _make_mock_system(active_chat='personal')
    _apply_chat_settings(sys_b, {})
    assert SCOPE_REGISTRY['rag']['var'].get() == '__rag__:personal'


# ─── R6: Unmentioned scopes reset to default ─────────────────────────────────

def test_R6_apply_chat_settings_resets_unmentioned_scopes_to_default(scope_snapshot):
    """[REGRESSION_GUARD] If chat A set memory_scope='alice' and the user
    activates chat B whose settings don't mention memory_scope, memory scope
    must reset to 'default' — not leak 'alice' into chat B.

    Mechanism: _apply_chat_settings calls reset_scopes() BEFORE
    apply_scopes_from_settings(settings), so keys absent from settings fall
    back to ContextVar defaults.
    """
    from core.api_fastapi import _apply_chat_settings
    from core.chat.function_manager import SCOPE_REGISTRY

    # Seed chat A scope
    if 'memory' in SCOPE_REGISTRY:
        SCOPE_REGISTRY['memory']['var'].set('alice')
        assert SCOPE_REGISTRY['memory']['var'].get() == 'alice'

    # Activate chat B with settings that DON'T mention memory_scope
    sys = _make_mock_system(active_chat='chat_b')
    _apply_chat_settings(sys, {'toolset': 'all'})  # no memory_scope

    # memory scope should have reset to default
    if 'memory' in SCOPE_REGISTRY:
        default = SCOPE_REGISTRY['memory']['default']
        assert SCOPE_REGISTRY['memory']['var'].get() == default, (
            f"memory scope should have reset to {default!r}, "
            f"got {SCOPE_REGISTRY['memory']['var'].get()!r} — scope bleed from chat A"
        )


def test_R6_apply_chat_settings_applies_explicitly_provided_scopes(scope_snapshot):
    """Inverse of R6: scopes that ARE in the settings dict should be applied,
    not reset. Pinning this means reset_scopes() doesn't accidentally shadow
    apply_scopes_from_settings() via ordering."""
    from core.api_fastapi import _apply_chat_settings
    from core.chat.function_manager import SCOPE_REGISTRY

    if 'memory' not in SCOPE_REGISTRY:
        pytest.skip("memory scope not registered (memory plugin not loaded in this env)")

    sys = _make_mock_system(active_chat='chat_c')
    _apply_chat_settings(sys, {'memory_scope': 'work'})

    assert SCOPE_REGISTRY['memory']['var'].get() == 'work'


def test_R6_multiple_scopes_propagate_together(scope_snapshot):
    """All scopes provided in settings should get applied in one _apply_chat_settings
    call — not just the first, not selectively."""
    from core.api_fastapi import _apply_chat_settings
    from core.chat.function_manager import SCOPE_REGISTRY

    sys = _make_mock_system(active_chat='chat_d')
    payload = {
        'memory_scope': 'research',
        'knowledge_scope': 'library',
        'goal_scope': 'q2_plan',
    }
    _apply_chat_settings(sys, payload)

    for k, expected in [('memory', 'research'), ('knowledge', 'library'), ('goal', 'q2_plan')]:
        if k in SCOPE_REGISTRY:
            assert SCOPE_REGISTRY[k]['var'].get() == expected, (
                f"scope '{k}' did not apply: expected {expected!r}, "
                f"got {SCOPE_REGISTRY[k]['var'].get()!r}"
            )
