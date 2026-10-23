"""Regression guard for the settings persist bug (#1, fixed 2026-06-18).

`_deep_update_from_flat` used to additively merge config-objects with the stale on-disk
dict, so a deleted provider/key was re-added from disk and "resurrected" on the next reload.
Since every config-object writer is read-modify-write the FULL dict, the in-memory value is
authoritative and must REPLACE, not merge.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.settings_manager import settings


def test_deep_update_deletes_config_object_subkey():
    """The fix: a sub-key absent from the new (full) value must NOT survive from disk."""
    nested = {'llm': {'LLM_CUSTOM_PROVIDERS': {
        'ollama': {'template': 'openai', 'enabled': True},
        'keepme': {'template': 'openai', 'enabled': True},
    }}}
    # in-memory _user, full dict, ollama deleted (read-modify-write pattern)
    flat = {'LLM_CUSTOM_PROVIDERS': {'keepme': {'template': 'openai', 'enabled': True}}}
    out = settings._deep_update_from_flat(nested, flat)
    cp = out['llm']['LLM_CUSTOM_PROVIDERS']
    assert 'ollama' not in cp, "deleted provider must NOT resurrect from the stale disk dict"
    assert 'keepme' in cp


def test_deep_update_field_change_keeps_siblings():
    """A field update on one provider (full dict, read-modify-write) keeps the others intact."""
    nested = {'llm': {'LLM_CUSTOM_PROVIDERS': {'a': {'model': 'old'}, 'b': {'model': 'x'}}}}
    flat = {'LLM_CUSTOM_PROVIDERS': {'a': {'model': 'new'}, 'b': {'model': 'x'}}}
    out = settings._deep_update_from_flat(nested, flat)
    cp = out['llm']['LLM_CUSTOM_PROVIDERS']
    assert cp['a']['model'] == 'new'
    assert cp['b']['model'] == 'x', "sibling provider must be preserved"
