"""Regression guards for 2026-04-22 sapphire-killer fix D (3 sub-fixes).

D1 — Corrupt user personas.json must NOT cause `_personas = {}` → next save
     wipes user's real personas. Fix: preserve corrupt file (timestamped),
     seed from core defaults.

D2 — `_clean_settings` must preserve `*_scope` keys even when not in the
     current SCOPE_REGISTRY allowlist. Plugin temporarily unloaded during
     upgrade → persona updates strict-stripped the binding → silent loss.

D3 — `delete()` a persona that's active in a chat must rewrite the chat's
     settings to 'default' loudly, not leave chats silently pointing at a
     deleted persona.
"""
import json
from unittest.mock import patch, MagicMock
import pytest


@pytest.fixture
def isolated_persona_manager(tmp_path, monkeypatch):
    """Spin up a PersonaManager with user/personas/ at tmp_path."""
    from core.personas import persona_manager as pm_module
    # Create user dir with a core file to seed from
    (tmp_path / "personas").mkdir()
    core_file = tmp_path / "personas.json"
    core_file.write_text(json.dumps({
        "_comment": "core default",
        "default": {"prompt": "default", "toolset": "default", "settings": {}},
        "sapphire": {"prompt": "sapphire", "toolset": "all", "settings": {}},
    }))

    # Build a fresh manager pointing at our tmp_path
    from core.personas.persona_manager import PersonaManager
    mgr = PersonaManager.__new__(PersonaManager)
    import threading
    mgr._lock = threading.RLock()
    mgr.BASE_DIR = tmp_path
    mgr.USER_DIR = tmp_path / "personas"
    mgr.USER_AVATARS = mgr.USER_DIR / "avatars"
    mgr.USER_AVATARS.mkdir(parents=True, exist_ok=True)
    mgr._personas = {}
    return mgr, tmp_path


def test_d1_corrupt_user_file_preserves_and_seeds_from_core(isolated_persona_manager):
    """D1 — corrupt user personas.json → rename to .corrupt-TS + seed from core."""
    mgr, tmp_path = isolated_persona_manager

    user_file = tmp_path / "personas" / "personas.json"
    user_file.write_text("{this is not valid JSON at all")

    mgr._load()

    # Corrupt file should have been renamed with .corrupt-TIMESTAMP
    corrupt_backups = list((tmp_path / "personas").glob("personas.json.corrupt-*"))
    assert len(corrupt_backups) == 1, \
        f"Corrupt file should be preserved as .corrupt-TS backup, got: {corrupt_backups}"

    # Personas should have been seeded from core (fall-through to first-run path)
    assert 'default' in mgr._personas
    assert 'sapphire' in mgr._personas

    # If personas.json exists now, it must be the seeded one — NOT the old
    # corrupt bytes (which are preserved at the .corrupt-TS backup).
    if user_file.exists():
        current = user_file.read_text()
        assert current != "{this is not valid JSON at all", \
            "personas.json must not contain the original corrupt bytes"


def test_d1_corrupt_file_preserved_bytes_match(isolated_persona_manager):
    """The preserved .corrupt-TS file must contain the original corrupt bytes."""
    mgr, tmp_path = isolated_persona_manager

    user_file = tmp_path / "personas" / "personas.json"
    corrupt_content = "{this is not valid JSON at all"
    user_file.write_text(corrupt_content)

    mgr._load()

    corrupt_backups = list((tmp_path / "personas").glob("personas.json.corrupt-*"))
    assert len(corrupt_backups) == 1
    assert corrupt_backups[0].read_text() == corrupt_content, \
        "Preserved backup must contain the original corrupt bytes for manual recovery"


def test_d2_clean_settings_preserves_unknown_scope_keys(isolated_persona_manager):
    """D2 — _clean_settings must preserve *_scope keys even if not in
    SCOPE_REGISTRY allowlist. Simulates a plugin being temporarily unloaded."""
    mgr, _ = isolated_persona_manager

    settings = {
        # Standard keys — these are always allowed
        "prompt": "sapphire",
        "toolset": "all",
        # Plugin scope key that's temporarily not in the registry
        "mystery_plugin_scope": "work",
        # Another unknown *_scope
        "future_feature_scope": "default",
        # A genuinely unknown key that is NOT a scope — should be stripped
        "random_junk": "value",
    }

    with patch("core.personas.persona_manager.get_persona_settings_keys",
               return_value=["prompt", "toolset"]):
        cleaned = mgr._clean_settings(settings)

    # Allowed keys preserved
    assert cleaned.get("prompt") == "sapphire"
    assert cleaned.get("toolset") == "all"
    # Unknown *_scope keys PRESERVED despite not being allowlisted
    assert cleaned.get("mystery_plugin_scope") == "work", \
        "Unknown *_scope key must be preserved (plugin may be unloaded temporarily)"
    assert cleaned.get("future_feature_scope") == "default"
    # Non-scope junk key STRIPPED
    assert "random_junk" not in cleaned


def test_d2_standard_keys_still_pass_allowlist(isolated_persona_manager):
    """Sanity — D2 doesn't break the whitelist for known keys."""
    mgr, _ = isolated_persona_manager
    settings = {"prompt": "test", "garbage": "nope"}
    with patch("core.personas.persona_manager.get_persona_settings_keys",
               return_value=["prompt"]):
        cleaned = mgr._clean_settings(settings)
    assert cleaned == {"prompt": "test"}


def test_d3_delete_active_persona_rewrites_chat_settings(isolated_persona_manager):
    """D3 — deleting a persona that's active in chats must rewrite those chats
    to 'default' with a loud log + SETTINGS_CHANGED event."""
    mgr, _ = isolated_persona_manager
    mgr._personas = {
        "default": {"prompt": "default", "settings": {}},
        "anita": {"prompt": "anita", "settings": {}},
    }

    # Mock the session manager as if two chats exist — one uses 'anita', one uses 'default'
    mock_sm = MagicMock()
    mock_sm.list_chats.return_value = [{"name": "chat-with-anita"}, {"name": "chat-with-default"}]

    def read_settings(chat_name):
        if chat_name == "chat-with-anita":
            return {"persona": "anita"}
        return {"persona": "default"}

    mock_sm.read_chat_settings.side_effect = read_settings

    mock_system = MagicMock()
    mock_system.llm_chat.session_manager = mock_sm

    with patch("core.personas.persona_manager.PersonaManager._save_to_user", return_value=True):
        with patch("core.api_fastapi.get_system", return_value=mock_system):
            result = mgr.delete("anita")

    assert result is True

    # update_chat_settings must have been called for chat-with-anita (only)
    update_calls = mock_sm.update_chat_settings.call_args_list
    assert len(update_calls) == 1
    assert update_calls[0][0][0] == "chat-with-anita"
    assert update_calls[0][0][1] == {"persona": "default"}


def test_d3_delete_unused_persona_no_chat_rewrites(isolated_persona_manager):
    """Sanity — D3 doesn't misfire when the persona isn't active anywhere."""
    mgr, _ = isolated_persona_manager
    mgr._personas = {
        "default": {"prompt": "default", "settings": {}},
        "unused": {"prompt": "unused", "settings": {}},
    }

    mock_sm = MagicMock()
    mock_sm.list_chats.return_value = [{"name": "chat-a"}]
    mock_sm.read_chat_settings.return_value = {"persona": "default"}

    mock_system = MagicMock()
    mock_system.llm_chat.session_manager = mock_sm

    with patch("core.personas.persona_manager.PersonaManager._save_to_user", return_value=True):
        with patch("core.api_fastapi.get_system", return_value=mock_system):
            mgr.delete("unused")

    # No chat should have been rewritten
    assert mock_sm.update_chat_settings.call_count == 0
