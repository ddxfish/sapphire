"""Tests for API route breakup — structural integrity of route modules."""
import pytest
import importlib
import sys


# =============================================================================
# Route Registration
# =============================================================================

class TestRouteRegistration:
    """Verify all route modules register correctly with the FastAPI app."""

    def test_app_has_routes(self):
        from core.api_fastapi import app
        from fastapi.routing import APIRoute
        routes = [r for r in app.routes if isinstance(r, APIRoute)]
        assert len(routes) > 200, f"Expected 200+ routes, got {len(routes)}"

    @pytest.mark.parametrize("path", [
        # chat.py
        "/api/health", "/api/history", "/api/chat", "/api/init",
        "/api/chats", "/api/chats/{chat_name}/settings",
        # tts.py
        "/api/tts", "/api/tts/stop", "/api/tts/voices",
        "/api/transcribe", "/api/mic/active",
        # settings.py
        "/api/settings", "/api/settings/batch",
        "/api/credentials/llm/{provider}", "/api/llm/providers",
        # content.py
        "/api/prompts", "/api/toolsets", "/api/spices",
        "/api/personas", "/api/spice-sets",
        # knowledge.py
        "/api/knowledge/tabs", "/api/knowledge/scopes",
        "/api/knowledge/people", "/api/memory/scopes",
        # system.py
        "/api/backup/list", "/api/continuity/tasks",
        "/api/setup/wizard-step", "/api/avatars",
        # plugins.py
        "/api/webui/plugins", "/api/webui/plugins/config",
        "/api/plugins/install", "/api/plugins/rescan",
        # media.py
        "/api/tool-image/{image_id}",
    ])
    def test_key_route_exists(self, path):
        from core.api_fastapi import app
        from fastapi.routing import APIRoute
        paths = [r.path for r in app.routes if isinstance(r, APIRoute)]
        assert path in paths, f"Route {path} not registered"

    def test_all_routers_included(self):
        """Each route module's router should contribute at least one route."""
        from core.api_fastapi import app
        from fastapi.routing import APIRoute
        paths = [r.path for r in app.routes if isinstance(r, APIRoute)]

        # At least one route from each module
        module_markers = {
            "chat":         "/api/health",
            "tts":          "/api/tts",
            "settings":     "/api/settings",
            "content":      "/api/prompts",
            "knowledge":    "/api/knowledge/tabs",
            "system":       "/api/backup/list",
            "plugins":      "/api/webui/plugins",
            "media":        "/api/tool-image/{image_id}",
        }
        for module, marker in module_markers.items():
            assert marker in paths, f"Route module '{module}' missing — {marker} not found"


# =============================================================================
# Import Integrity
# =============================================================================

class TestImportIntegrity:
    """Verify route modules import cleanly without circular import errors."""

    def test_api_fastapi_imports(self):
        """Main app module imports without error."""
        mod = importlib.import_module("core.api_fastapi")
        assert hasattr(mod, "app")
        assert hasattr(mod, "get_system")
        assert hasattr(mod, "set_system")

    @pytest.mark.parametrize("module_name", [
        "core.routes.chat",
        "core.routes.tts",
        "core.routes.settings",
        "core.routes.content",
        "core.routes.knowledge",
        "core.routes.system",
        "core.routes.plugins",
        "core.routes.media",
    ])
    def test_route_module_imports(self, module_name):
        """Each route module imports without circular import errors."""
        # Ensure api_fastapi is loaded first (as it would be in production)
        importlib.import_module("core.api_fastapi")
        mod = importlib.import_module(module_name)
        assert hasattr(mod, "router"), f"{module_name} missing 'router' attribute"

    def test_route_modules_have_apiRouter(self):
        """Each router should be a FastAPI APIRouter instance."""
        from fastapi import APIRouter
        import core.api_fastapi  # noqa: F401
        route_modules = [
            "core.routes.chat", "core.routes.tts", "core.routes.settings",
            "core.routes.content", "core.routes.knowledge",
            "core.routes.system",
            "core.routes.plugins", "core.routes.media",
        ]
        for name in route_modules:
            mod = importlib.import_module(name)
            assert isinstance(mod.router, APIRouter), f"{name}.router is not APIRouter"


# =============================================================================
# Shared Function Accessibility
# =============================================================================

class TestSharedFunctions:
    """Verify shared functions in api_fastapi are accessible from route modules."""

    def test_get_system_callable(self):
        from core.api_fastapi import get_system
        assert callable(get_system)

    def test_apply_chat_settings_callable(self):
        from core.api_fastapi import _apply_chat_settings
        assert callable(_apply_chat_settings)

    def test_project_root_is_path(self):
        from core.api_fastapi import PROJECT_ROOT
        from pathlib import Path
        assert isinstance(PROJECT_ROOT, Path)
        assert PROJECT_ROOT.exists()

    def test_get_restart_callback_callable(self):
        from core.api_fastapi import get_restart_callback
        assert callable(get_restart_callback)

    def test_get_shutdown_callback_callable(self):
        from core.api_fastapi import get_shutdown_callback
        assert callable(get_shutdown_callback)

    def test_boot_version_is_string(self):
        from core.api_fastapi import BOOT_VERSION
        assert isinstance(BOOT_VERSION, str)
        assert len(BOOT_VERSION) > 0

    def test_format_messages_accessible_from_chat(self):
        """format_messages_for_display moved to routes/chat.py — verify it's there."""
        import core.api_fastapi  # noqa: F401
        from core.routes.chat import format_messages_for_display
        assert callable(format_messages_for_display)

    def test_format_messages_basic(self):
        """Smoke test: format_messages_for_display handles simple input."""
        import core.api_fastapi  # noqa: F401
        from core.routes.chat import format_messages_for_display
        result = format_messages_for_display([
            {"role": "user", "content": "hello", "timestamp": 1000}
        ])
        assert len(result) == 1
        assert result[0]["content"] == "hello"
