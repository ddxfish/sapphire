"""Tests for scope registry system (function_manager.py scope functions).

Covers: SCOPE_REGISTRY, apply_scopes_from_settings, reset_scopes,
snapshot_all_scopes, restore_scopes, scope_setting_keys.
"""
import pytest
import threading
from unittest.mock import patch, MagicMock

from core.chat.function_manager import (
    SCOPE_REGISTRY, apply_scopes_from_settings, reset_scopes,
    snapshot_all_scopes, restore_scopes, scope_setting_keys,
    scope_memory, scope_goal, scope_knowledge, scope_people,
    scope_email, scope_bitcoin, scope_gcal, scope_rag, scope_private,
    FunctionManager,
)


class TestScopeRegistry:
    """SCOPE_REGISTRY must have entries for all ContextVars."""

    def test_all_scopes_present(self):
        expected = {'memory', 'goal', 'knowledge', 'people', 'email',
                    'bitcoin', 'gcal', 'telegram', 'discord', 'rag', 'private'}
        assert set(SCOPE_REGISTRY.keys()) == expected

    def test_each_entry_has_required_keys(self):
        for name, reg in SCOPE_REGISTRY.items():
            assert 'var' in reg, f"{name} missing 'var'"
            assert 'default' in reg, f"{name} missing 'default'"
            assert 'setting' in reg, f"{name} missing 'setting'"

    def test_setting_keys_match_sidebar_pattern(self):
        """Setting keys should follow {name}_scope pattern (except private_chat, rag=None)."""
        for name, reg in SCOPE_REGISTRY.items():
            key = reg['setting']
            if key is None:
                continue
            if name == 'private':
                assert key == 'private_chat'
            else:
                assert key == f'{name}_scope', f"{name} has unexpected setting key: {key}"


class TestApplyScopesFromSettings:
    """apply_scopes_from_settings must set ContextVars from a settings dict."""

    def setup_method(self):
        reset_scopes()

    def test_applies_all_string_scopes(self):
        settings = {
            'memory_scope': 'shared',
            'goal_scope': 'work',
            'knowledge_scope': 'research',
            'people_scope': 'team',
            'email_scope': 'work_email',
            'bitcoin_scope': 'wallet_a',
            'gcal_scope': 'sawyer',
        }
        apply_scopes_from_settings(None, settings)

        assert scope_memory.get() == 'shared'
        assert scope_goal.get() == 'work'
        assert scope_knowledge.get() == 'research'
        assert scope_people.get() == 'team'
        assert scope_email.get() == 'work_email'
        assert scope_bitcoin.get() == 'wallet_a'
        assert scope_gcal.get() == 'sawyer'

    def test_none_string_disables_scope(self):
        """String 'none' should convert to None (disabled)."""
        apply_scopes_from_settings(None, {'bitcoin_scope': 'none'})
        assert scope_bitcoin.get() is None

    def test_empty_string_resets_to_default(self):
        """Empty string should reset to default value."""
        scope_memory.set('custom')
        apply_scopes_from_settings(None, {'memory_scope': ''})
        assert scope_memory.get() == 'default'

    def test_private_chat_coerced_to_bool(self):
        apply_scopes_from_settings(None, {'private_chat': 1})
        assert scope_private.get() is True

        apply_scopes_from_settings(None, {'private_chat': 0})
        assert scope_private.get() is False

    def test_missing_keys_dont_reset_existing(self):
        """Keys not in settings dict should leave ContextVars unchanged."""
        scope_memory.set('custom')
        apply_scopes_from_settings(None, {'goal_scope': 'work'})
        assert scope_memory.get() == 'custom'  # untouched
        assert scope_goal.get() == 'work'

    def test_rag_skipped(self):
        """RAG has setting=None, should never be set by apply_scopes."""
        scope_rag.set('old_value')
        apply_scopes_from_settings(None, {'rag_scope': 'should_not_apply'})
        assert scope_rag.get() == 'old_value'


class TestResetScopes:
    """reset_scopes must set all ContextVars back to defaults."""

    def test_resets_all_to_defaults(self):
        scope_memory.set('custom')
        scope_goal.set('work')
        scope_private.set(True)
        scope_rag.set('docs')

        reset_scopes()

        assert scope_memory.get() == 'default'
        assert scope_goal.get() == 'default'
        assert scope_private.get() is False
        assert scope_rag.get() is None


class TestSnapshotRestore:
    """snapshot_all_scopes / restore_scopes roundtrip."""

    def setup_method(self):
        reset_scopes()

    def test_snapshot_captures_current_values(self):
        scope_memory.set('shared')
        scope_bitcoin.set('wallet_a')
        scope_private.set(True)

        snap = snapshot_all_scopes()

        assert snap['memory'] == 'shared'
        assert snap['bitcoin'] == 'wallet_a'
        assert snap['private'] is True
        assert snap['goal'] == 'default'

    def test_restore_applies_snapshot(self):
        snap = {
            'memory': 'restored',
            'goal': 'restored_goal',
            'knowledge': 'default',
            'people': 'default',
            'email': 'default',
            'bitcoin': 'default',
            'gcal': 'default',
            'rag': None,
            'private': True,
        }
        restore_scopes(snap)

        assert scope_memory.get() == 'restored'
        assert scope_goal.get() == 'restored_goal'
        assert scope_private.get() is True

    def test_restore_missing_keys_get_defaults(self):
        """If a scope key is missing from snapshot, it resets to default."""
        scope_memory.set('custom')
        restore_scopes({'goal': 'work'})  # missing 'memory'
        assert scope_memory.get() == 'default'
        assert scope_goal.get() == 'work'

    def test_roundtrip(self):
        scope_memory.set('shared')
        scope_gcal.set('sawyer')
        scope_private.set(True)

        snap = snapshot_all_scopes()
        reset_scopes()
        assert scope_memory.get() == 'default'

        restore_scopes(snap)
        assert scope_memory.get() == 'shared'
        assert scope_gcal.get() == 'sawyer'
        assert scope_private.get() is True


class TestScopeSettingKeys:
    """scope_setting_keys returns all sidebar-configurable setting keys."""

    def test_returns_expected_keys(self):
        keys = scope_setting_keys()
        assert 'memory_scope' in keys
        assert 'goal_scope' in keys
        assert 'knowledge_scope' in keys
        assert 'people_scope' in keys
        assert 'email_scope' in keys
        assert 'bitcoin_scope' in keys
        assert 'gcal_scope' in keys

    def test_excludes_private_chat(self):
        keys = scope_setting_keys()
        assert 'private_chat' not in keys

    def test_excludes_rag(self):
        """RAG has setting=None, should not appear."""
        keys = scope_setting_keys()
        assert None not in keys


class TestFunctionManagerScopeWrappers:
    """FunctionManager.apply_scopes / set_scope / get_scope thin wrappers."""

    def setup_method(self):
        reset_scopes()

    def test_set_scope_and_get_scope(self):
        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()
            mgr.set_scope('memory', 'custom')
            assert mgr.get_scope('memory') == 'custom'
            assert scope_memory.get() == 'custom'

    def test_apply_scopes_delegates(self):
        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()
            mgr.apply_scopes({'memory_scope': 'shared', 'gcal_scope': 'sawyer'})
            assert scope_memory.get() == 'shared'
            assert scope_gcal.get() == 'sawyer'

    def test_legacy_setters_still_work(self):
        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()
            mgr.set_memory_scope('legacy')
            assert mgr.get_memory_scope() == 'legacy'
            mgr.set_gcal_scope('cal_legacy')
            assert mgr.get_gcal_scope() == 'cal_legacy'


class TestContextVarThreadIsolation:
    """Scopes must be isolated between threads."""

    def test_thread_isolation(self):
        reset_scopes()
        results = {}

        def thread_a():
            scope_memory.set('thread_a')
            import time; time.sleep(0.05)
            results['a'] = scope_memory.get()

        def thread_b():
            scope_memory.set('thread_b')
            import time; time.sleep(0.05)
            results['b'] = scope_memory.get()

        t1 = threading.Thread(target=thread_a)
        t2 = threading.Thread(target=thread_b)
        t1.start(); t2.start()
        t1.join(); t2.join()

        assert results['a'] == 'thread_a'
        assert results['b'] == 'thread_b'
        # Main thread untouched
        assert scope_memory.get() == 'default'
