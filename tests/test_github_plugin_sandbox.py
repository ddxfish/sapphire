"""GitHub plugin path sandbox + scope behavior tests.

Two ship-blocker fixes shipped 2026-05-14:
  1. `push_directory` accepted arbitrary filesystem paths via
     `.expanduser().resolve()` with no containment check. The AI calling
     `local_path="~/.config/sapphire"` would exfil credentials, scramble
     salt, and the plugin signing private key. Now sandboxed to project
     root with explicit user/ + CONFIG_DIR rejection.
  2. `_get_github_scope()` silent-defaulted to 'default' when the scope
     ContextVar was None — silent-default class regression. Now returns
     None, callers fail closed.
"""
import importlib
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def github_tools():
    """Load the github plugin tools module the same way the plugin loader
    would. The plugin's tools/github.py expects `from typing import Optional`
    at top-level (added 2026-05-14) and accesses core.* lazily.
    """
    plugin_dir = Path(__file__).resolve().parent.parent / "core" / "plugins" / "github"
    if str(plugin_dir / "tools") not in sys.path:
        sys.path.insert(0, str(plugin_dir / "tools"))
    # Force-reimport to pick up any code edits between test runs
    if "github" in sys.modules:
        del sys.modules["github"]
    return importlib.import_module("github")


# ─── push_directory sandbox ─────────────────────────────────────────────


def test_push_directory_refuses_user_dir(github_tools, tmp_path):
    """REGRESSION_GUARD: a path inside <project_root>/user/ must be refused
    even if it exists and is a real directory. user/ holds credentials,
    plugin signing keys, chats — pushing it to GitHub = catastrophic leak."""
    project_root = Path(__file__).resolve().parent.parent
    user_dir = project_root / "user"
    if not user_dir.exists():
        pytest.skip("user/ dir doesn't exist in test env")

    with patch.object(github_tools, "_get_github_creds", return_value=("u", "pat", None)):
        result, ok = github_tools._file_push_directory(
            {"repo": "x/y", "local_path": str(user_dir)}, "u", "pat"
        )
    assert ok is False
    assert "user/" in result or "forbidden" in result.lower()


def test_push_directory_refuses_path_outside_project_root(github_tools, tmp_path):
    """REGRESSION_GUARD: a real directory outside the Sapphire project root
    must be refused. tmp_path is a pytest-created temp dir outside the
    project — perfect adversarial input."""
    with patch.object(github_tools, "_get_github_creds", return_value=("u", "pat", None)):
        result, ok = github_tools._file_push_directory(
            {"repo": "x/y", "local_path": str(tmp_path)}, "u", "pat"
        )
    assert ok is False
    assert "project root" in result.lower() or "must be inside" in result.lower()


def test_push_directory_accepts_valid_in_project_path(github_tools, tmp_path, monkeypatch):
    """A path inside the project root (and not in user/) should pass the
    sandbox check. We create an empty subdir under project_root/tests so
    the sandbox accepts it, then assert the failure mode is "no files"
    (not a sandbox refusal)."""
    project_root = Path(__file__).resolve().parent.parent
    empty_dir = project_root / "tests" / "_tmp_empty_sandbox_check"
    empty_dir.mkdir(exist_ok=True)
    try:
        with patch.object(github_tools, "_get_github_creds", return_value=("u", "pat", None)):
            result, ok = github_tools._file_push_directory(
                {"repo": "x/y", "local_path": str(empty_dir)}, "u", "pat"
            )
        # Sandbox must have accepted; failure should be "no files" not "forbidden"
        assert "forbidden" not in result.lower()
        assert "must be inside" not in result.lower()
        # Expected failure mode: empty directory has no files
        assert "no files" in result.lower() or ok is True
    finally:
        empty_dir.rmdir()


# ─── _get_github_scope: silent-default class ────────────────────────────


def test_get_github_scope_returns_none_when_var_unset(github_tools):
    """REGRESSION_GUARD: a chat configured with github scope='none' (the
    sidebar disable) sets the ContextVar to None. _get_github_scope must
    return None (not 'default') so the caller refuses rather than silently
    routing to the default account.

    Same silent-default class as test_silent_default_fix.py — applied to
    github after the regression was caught 2026-05-14."""
    # Make the ContextVar return None by patching the registry lookup
    fake_registry = {"github": {"var": type("V", (), {"get": staticmethod(lambda: None)})()}}
    with patch.dict("core.chat.function_manager.SCOPE_REGISTRY", fake_registry, clear=False):
        result = github_tools._get_github_scope()
    assert result is None, "Silent-default regression: scope must return None when ContextVar is None"


def test_get_github_creds_refuses_when_scope_none(github_tools):
    """REGRESSION_GUARD: caller of _get_github_scope must fail closed when
    scope is None — return a 'disabled' message, not load default account's
    credentials."""
    with patch.object(github_tools, "_get_github_scope", return_value=None):
        username, pat, err = github_tools._get_github_creds()
    assert username == ""
    assert pat == ""
    assert err is not None
    assert "disabled" in err.lower()
