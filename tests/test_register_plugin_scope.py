"""
register_plugin_scope() validation & idempotency + __getattr__ shim.

Companion to the pre-existing tests/test_scope_registry.py — that file
covers SCOPE_REGISTRY structure, apply/reset/snapshot, and FunctionManager
wrappers. This file fills in the gaps:

  1. register_plugin_scope() input validation (Phase 4 added .isidentifier()
     check after the privacy scout flagged that bad scope keys could create
     malformed registry entries).
  2. register_plugin_scope() idempotency — calling twice with same key must
     return the same ContextVar (this is the contract that makes hot-reload
     safe).
  3. __getattr__ shim resolves `scope_<key>` for everything in SCOPE_REGISTRY.

Run with: pytest tests/test_register_plugin_scope.py -v
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def scope_cleanup():
    """Snapshot SCOPE_REGISTRY keys before test, remove any new ones after.

    Tests that call register_plugin_scope() leak entries into the global
    SCOPE_REGISTRY. The pre-existing tests/test_scope_registry.py asserts
    an exact key set on SCOPE_REGISTRY, so any leak breaks it. This fixture
    cleans up after every test that uses it.

    PYTEST PRIMER — fixture cleanup
    ───────────────────────────────
    Anything after `yield` runs as teardown — even if the test failed. This
    is the safer cousin of try/finally for test setup.
    """
    from core.chat.function_manager import SCOPE_REGISTRY
    before = set(SCOPE_REGISTRY.keys())
    yield
    leaked = set(SCOPE_REGISTRY.keys()) - before
    for key in leaked:
        SCOPE_REGISTRY.pop(key, None)


def test_every_registered_scope_is_reachable_via_getattr():
    """For every key in SCOPE_REGISTRY, `scope_<key>` must resolve via __getattr__.

    This is the contract that lets plugin code do
        from core.chat.function_manager import scope_email
    even though `scope_email` is not a real module-level name. The shim is in
    function_manager.__getattr__; this test pins it.
    """
    import core.chat.function_manager as fm
    for key in list(fm.SCOPE_REGISTRY.keys()):
        attr_name = f"scope_{key}"
        var = getattr(fm, attr_name)
        assert var is fm.SCOPE_REGISTRY[key]['var'], \
            f"scope_{key} resolved to wrong ContextVar"


def test_getattr_raises_for_unknown_scope():
    """Asking for a scope that doesn't exist must raise AttributeError.

    Not return None — that would silently break `from ... import` statements
    by giving them a None object instead of failing the import.
    """
    import core.chat.function_manager as fm
    with pytest.raises(AttributeError):
        getattr(fm, "scope_definitely_not_a_real_scope")


def test_getattr_raises_for_non_scope_name():
    """Names that don't start with `scope_` must also raise AttributeError."""
    import core.chat.function_manager as fm
    with pytest.raises(AttributeError):
        getattr(fm, "totally_random_attribute")


# ─── register_plugin_scope() input validation ──────────────────────────────

# Parameterized list of bad keys — each runs as its own test case.
# Using parametrize means a failure tells you which input failed without
# needing to read a stack trace.
@pytest.mark.parametrize("bad_key", [
    '',           # empty string
    '123abc',     # starts with digit
    'has-dash',   # hyphens not allowed in identifiers
    'has space',  # spaces not allowed
    'has.dot',    # dots not allowed
    None,         # not even a string
])
def test_register_plugin_scope_rejects_invalid_key(bad_key, scope_cleanup):
    """Malformed scope keys must be rejected without polluting the registry."""
    from core.chat.function_manager import register_plugin_scope, SCOPE_REGISTRY

    before_keys = set(SCOPE_REGISTRY.keys())
    result = register_plugin_scope(bad_key, plugin_name='test')

    assert result is None, f"Invalid key {bad_key!r} should have returned None"
    assert set(SCOPE_REGISTRY.keys()) == before_keys, \
        f"Invalid key {bad_key!r} leaked into SCOPE_REGISTRY"


def test_register_plugin_scope_is_idempotent(scope_cleanup):
    """Two registrations of the same key return the SAME ContextVar.

    This is the contract that makes hot-reload safe: a reloaded plugin
    re-registers its scopes, and we must NOT replace the existing var
    (which would orphan any thread already holding the old reference).
    """
    from core.chat.function_manager import register_plugin_scope, SCOPE_REGISTRY

    key = 'test_idempotent_scope_xyz'  # unique to avoid collisions
    var1 = register_plugin_scope(key, plugin_name='test1')
    var2 = register_plugin_scope(key, plugin_name='test2')

    assert var1 is not None
    assert var1 is var2, "register_plugin_scope returned different vars for same key"
    assert key in SCOPE_REGISTRY


def test_register_plugin_scope_derives_setting_key(scope_cleanup):
    """A registered scope's `setting` field must be `<key>_scope`."""
    from core.chat.function_manager import register_plugin_scope, SCOPE_REGISTRY

    key = 'test_setting_derivation_scope'  # unique
    register_plugin_scope(key, plugin_name='test')
    assert SCOPE_REGISTRY[key]['setting'] == f'{key}_scope'


def test_register_plugin_scope_uses_default_value(scope_cleanup):
    """The `default` argument is what reset_scopes() will return the var to."""
    from core.chat.function_manager import (register_plugin_scope, SCOPE_REGISTRY,
                                            reset_scopes)

    key = 'test_default_value_scope'  # unique
    var = register_plugin_scope(key, plugin_name='test', default='custom_default')
    assert SCOPE_REGISTRY[key]['default'] == 'custom_default'

    var.set('something_else')
    reset_scopes()
    assert var.get() == 'custom_default'
