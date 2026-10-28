"""
Plugin route helper tests for core/routes/plugins.py.

_get_merged_plugins() and _extract_css_preview() are the pure-logic helpers
in the biggest risk file in the coverage map (10.4% coverage). Testing these
directly is cheap and covers significant production code without needing a
running server or auth context.

Run with: pytest tests/test_plugin_helpers.py -v
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Bootstrap api_fastapi first to resolve the circular import between
# core/routes/plugins.py and core/api_fastapi.py (same pattern as agents.py).
import importlib
importlib.import_module("core.api_fastapi")


# ─── _get_merged_plugins ────────────────────────────────────────────────────

class TestGetMergedPlugins:
    """_get_merged_plugins() reads user/webui/plugins.json — the only plugin list
    since the core-ui class retired (2026-10-25) — and forces LOCKED_PLUGINS on."""

    @pytest.fixture
    def user_json(self, tmp_path, monkeypatch):
        import core.routes.plugins as mod
        path = tmp_path / "user" / "webui" / "plugins.json"
        path.parent.mkdir(parents=True)
        monkeypatch.setattr(mod, "USER_PLUGINS_JSON", path)
        return path, mod

    def test_no_user_file_is_an_empty_shell(self, user_json):
        path, mod = user_json
        assert mod._get_merged_plugins() == {"enabled": [], "plugins": {}}

    def test_user_enabled_list_is_the_list(self, user_json):
        path, mod = user_json
        path.write_text(json.dumps({"enabled": ["backup", "image-gen"], "disabled": ["x"]}), encoding='utf-8')
        assert mod._get_merged_plugins()["enabled"] == ["backup", "image-gen"]

    def test_locked_plugins_always_enabled(self, user_json, monkeypatch):
        """LOCKED_PLUGINS must be enforced even when no user plugins.json exists."""
        path, mod = user_json
        monkeypatch.setattr(mod, "LOCKED_PLUGINS", ["critical-plugin"])
        assert "critical-plugin" in mod._get_merged_plugins()["enabled"]

    def test_corrupt_user_file_returns_empty_shell(self, user_json):
        path, mod = user_json
        path.write_text("NOT VALID JSON", encoding='utf-8')
        assert mod._get_merged_plugins() == {"enabled": [], "plugins": {}}


# ─── _extract_css_preview ───────────────────────────────────────────────────

class TestExtractCssPreview:
    """Tests for the CSS variable parser used by theme preview swatches."""

    def test_extracts_hex_colors(self, tmp_path):
        from core.routes.plugins import _extract_css_preview

        css = """
        :root {
            --bg: #1a1b2e;
            --bg-secondary: #252639;
            --text: #e1e1e6;
            --trim: #4a9eff;
            --border: #333450;
        }
        """
        css_file = tmp_path / "theme.css"
        css_file.write_text(css, encoding='utf-8')

        colors = _extract_css_preview(css_file)
        assert colors["bg"] == "#1a1b2e"
        assert colors["text"] == "#e1e1e6"
        assert colors["trim"] == "#4a9eff"
        assert colors["border"] == "#333450"
        # accent should fallback to trim when not declared
        assert colors["accent"] == "#4a9eff"

    def test_extracts_rgba_colors(self, tmp_path):
        from core.routes.plugins import _extract_css_preview

        css = ":root { --bg: rgba(26, 27, 46, 1); --text: #fff; }"
        css_file = tmp_path / "theme.css"
        css_file.write_text(css, encoding='utf-8')

        colors = _extract_css_preview(css_file)
        assert "rgba" in colors["bg"]
        assert colors["text"] == "#fff"

    def test_returns_empty_on_missing_file(self, tmp_path):
        from core.routes.plugins import _extract_css_preview

        colors = _extract_css_preview(tmp_path / "nonexistent.css")
        assert colors == {}

    def test_returns_empty_on_no_variables(self, tmp_path):
        from core.routes.plugins import _extract_css_preview

        css_file = tmp_path / "empty.css"
        css_file.write_text("body { margin: 0; }", encoding='utf-8')

        colors = _extract_css_preview(css_file)
        assert colors == {}


def test_purging_a_plugin_never_takes_a_longer_named_plugins_files(tmp_path, monkeypatch):
    """Hunt 21: the `{name}-*` sweep deleted claude-code's state when 'claude'
    was purged (google -> google-calendar tokens, mcp -> mcp_client keys)."""
    import core.plugin_loader as pl
    loader = pl.PluginLoader.__new__(pl.PluginLoader)
    loader._plugins = {'claude': {}, 'claude-code': {}}
    monkeypatch.setattr(pl, 'SYSTEM_PLUGINS_DIR', tmp_path / 'sys')
    monkeypatch.setattr(pl, 'USER_PLUGINS_DIR', tmp_path / 'usr')
    assert loader._owned_by_other('claude', 'claude-code.json')
    assert loader._owned_by_other('claude', 'claude-code-sessions')
    assert not loader._owned_by_other('claude', 'claude-logs')
    assert not loader._owned_by_other('claude', 'claude_sessions.json')
