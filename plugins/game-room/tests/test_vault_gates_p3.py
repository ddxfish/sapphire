"""Vaulted chats Phase 3 (T3) — the game seat honors chat privacy.

The explicit-provider path used to bypass core's private-chat local-only
gate entirely, and a hidden session silently lost its own llm_primary
(fail-open to the room-wide provider). Plan: tmp/vaulted-chats-plan.md §9.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

PLUGIN_DIR = Path(__file__).parent.parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

import gameroom_core  # noqa: E402


class TestIsLocalProvider:
    def test_config_is_local_wins(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "LLM_PROVIDERS",
                            {"lanbox": {"enabled": True, "is_local": True},
                             "cloudy": {"enabled": True}}, raising=False)
        monkeypatch.setattr(config, "LLM_CUSTOM_PROVIDERS", {}, raising=False)
        assert gameroom_core._is_local_provider("lanbox") is True
        assert gameroom_core._is_local_provider("cloudy") is False

    def test_unknown_provider_is_cloud(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "LLM_PROVIDERS", {}, raising=False)
        monkeypatch.setattr(config, "LLM_CUSTOM_PROVIDERS", {}, raising=False)
        assert gameroom_core._is_local_provider("nope") is False


class TestProviderGate:
    def test_explicit_nonlocal_refused_no_fallback(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "LLM_PROVIDERS",
                            {"cloudy": {"enabled": True}}, raising=False)
        monkeypatch.setattr(config, "LLM_CUSTOM_PROVIDERS", {}, raising=False)
        from core.chat.llm_providers import provider_registry
        called = []
        monkeypatch.setattr(provider_registry, "get_provider_by_key",
                            lambda *a, **k: called.append(1) or object())
        monkeypatch.setattr(provider_registry, "get_first_available_provider",
                            lambda *a, **k: called.append(2) or None)
        assert gameroom_core._get_provider("cloudy",
                                           privacy_required=True) is None
        # Refusal is terminal — no registry lookup, no auto fallback pick.
        assert 1 not in called

    def test_explicit_local_allowed(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "LLM_PROVIDERS",
                            {"lanbox": {"enabled": True, "is_local": True}},
                            raising=False)
        monkeypatch.setattr(config, "LLM_CUSTOM_PROVIDERS", {}, raising=False)
        from core.chat.llm_providers import provider_registry
        sentinel = object()
        monkeypatch.setattr(provider_registry, "get_provider_by_key",
                            lambda key, model_override='': sentinel)
        assert gameroom_core._get_provider(
            "lanbox", privacy_required=True) is sentinel

    def test_private_auto_filters_to_local_roster(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "LLM_PROVIDERS",
                            {"lanbox": {"enabled": True, "is_local": True},
                             "cloudy": {"enabled": True}}, raising=False)
        monkeypatch.setattr(config, "LLM_CUSTOM_PROVIDERS", {}, raising=False)
        monkeypatch.setattr(config, "LLM_FALLBACK_ORDER",
                            ["cloudy", "lanbox"], raising=False)
        from core.chat.llm_providers import provider_registry
        seen = {}
        sentinel = object()

        def fake_first(cfg, order):
            seen["cfg"] = cfg
            seen["order"] = order
            return ("lanbox", sentinel)
        monkeypatch.setattr(provider_registry,
                            "get_first_available_provider", fake_first)
        got = gameroom_core._get_provider(None, privacy_required=True)
        assert got is sentinel
        assert set(seen["cfg"]) == {"lanbox"} and seen["order"] == ["lanbox"]


class TestSeatGates:
    def test_call_llm_refuses_hidden_session(self):
        assert gameroom_core._call_llm("sys", "user", {"hidden": True}) is None

    def _fake_system(self, settings=None, hidden=False):
        sm = SimpleNamespace(
            get_settings_for=lambda name: settings,
            is_chat_hidden=lambda name: hidden)
        return SimpleNamespace(llm_chat=SimpleNamespace(session_manager=sm))

    def test_session_cfg_marks_hidden(self, monkeypatch):
        import core.api_fastapi as api
        monkeypatch.setattr(api, "get_system",
                            lambda: self._fake_system(settings=None, hidden=True))
        cfg = gameroom_core.session_cfg("sealed-one")
        assert cfg.get("hidden") is True

    def test_session_cfg_carries_privacy(self, monkeypatch):
        import core.api_fastapi as api
        monkeypatch.setattr(api, "get_system", lambda: self._fake_system(
            settings={"private_chat": True, "llm_primary": "lanbox"}))
        cfg = gameroom_core.session_cfg("mychat")
        assert cfg.get("privacy_required") is True
        assert cfg["provider"] == "lanbox"

    def test_public_session_unflagged(self, monkeypatch):
        import core.api_fastapi as api
        monkeypatch.setattr(api, "get_system", lambda: self._fake_system(
            settings={"llm_primary": "auto"}))
        cfg = gameroom_core.session_cfg("mychat")
        assert cfg.get("privacy_required") is False
        assert "hidden" not in cfg
