"""Email plugin scope resolution — silent-default class regression guard.

`_get_current_people_scope()` previously returned 'default' on Exception.
Same silent-default class as the github scope fix landed 2026-05-14.
Both callers (`_get_recipients`, `_send_email` recipient_id path) already
fail-closed on None — verified during the fix.
"""
import importlib
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def email_tool():
    plugin_dir = Path(__file__).resolve().parent.parent / "core" / "plugins" / "email" / "tools"
    if str(plugin_dir) not in sys.path:
        sys.path.insert(0, str(plugin_dir))
    if "email_tool" in sys.modules:
        del sys.modules["email_tool"]
    return importlib.import_module("email_tool")


def test_get_current_people_scope_returns_none_on_exception(email_tool):
    """REGRESSION_GUARD: scope resolution failure must return None, not
    'default'. The fix replaced `return 'default'` with `return None`
    plus a debug log. Without this, a misconfigured chat or plugin-reload
    window silently routes to the default account's contacts.
    """
    # Force import to fail by patching the import path
    with patch.dict("sys.modules", {"core.chat.function_manager": None}):
        # When the import inside the function fails, it should hit the
        # except branch and return None (not 'default').
        result = email_tool._get_current_people_scope()
    assert result is None, \
        "Silent-default regression: scope must return None on exception"


def test_get_current_people_scope_returns_var_value_when_set(email_tool):
    """When ContextVar has a value, return that value."""
    # Create a fake registry entry with a real-ish ContextVar
    from contextvars import ContextVar
    fake_var = ContextVar("scope_people_test", default="work")
    with patch("core.chat.function_manager.scope_people", fake_var):
        result = email_tool._get_current_people_scope()
    assert result == "work"
