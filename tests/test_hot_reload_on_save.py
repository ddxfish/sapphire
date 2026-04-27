"""Regression guard for 2026-04-23 Remmi/Zeebs field report.

Bug class: saving edits to a toolset/prompt/persona that's ACTIVE in the
current chat writes the file but doesn't refresh in-memory runtime state.
The user's edits appeared to take effect (file saved, UI updated) but the
chat continued to use stale data until re-Activate.

Fix: `reapply_if_active` in core/api_fastapi.py — called from the three
save endpoints. If the active chat's `toolset`/`prompt`/`persona` setting
matches the just-saved name, push the fresh content into runtime.
"""
from unittest.mock import MagicMock, patch


def _make_system(chat_settings):
    """Build a mock system where session_manager.get_chat_settings()
    returns `chat_settings`. llm_chat subsystems are MagicMocks so the
    test can assert on calls without a real DB/function_manager."""
    system = MagicMock()
    system.llm_chat.session_manager.get_chat_settings.return_value = chat_settings
    return system


def test_toolset_save_hot_reloads_active_chat():
    """Active chat uses toolset 'X'. User edits 'X'. Re-apply must fire."""
    from core.api_fastapi import reapply_if_active
    system = _make_system({"toolset": "my-dnd"})
    with patch("core.api_fastapi.publish") as mock_publish:
        reapply_if_active(system, "toolset", "my-dnd")
    system.llm_chat.function_manager.update_enabled_functions.assert_called_once_with(["my-dnd"])
    # TOOLSET_CHANGED must fire so sidebars + listeners refresh
    assert any(
        call.args and call.args[1].get("name") == "my-dnd"
        for call in mock_publish.call_args_list
    ), "Expected TOOLSET_CHANGED publish with name='my-dnd'"


def test_prompt_save_hot_reloads_active_chat():
    """Active chat uses prompt 'P'. User edits 'P'. Re-apply must push
    the fresh content into system.llm_chat.set_system_prompt."""
    from core.api_fastapi import reapply_if_active
    system = _make_system({"prompt": "sapphire"})
    with patch("core.api_fastapi.prompts") as mock_prompts, \
         patch("core.api_fastapi.publish"):
        mock_prompts.get_prompt.return_value = {"content": "you are sapphire v2"}
        reapply_if_active(system, "prompt", "sapphire")
    system.llm_chat.set_system_prompt.assert_called_once_with("you are sapphire v2")


def test_persona_save_hot_reloads_active_chat():
    """Active chat uses persona 'anita'. User edits 'anita'. Re-apply
    calls _apply_chat_settings with the full persona bundle."""
    from core.api_fastapi import reapply_if_active
    system = _make_system({"persona": "anita"})
    mock_persona = {
        "settings": {"prompt": "anita-prompt", "toolset": "anita-tools", "voice": "en_US-amy-medium"}
    }
    with patch("core.personas.persona_manager") as mock_pm, \
         patch("core.api_fastapi._apply_chat_settings") as mock_apply, \
         patch("core.api_fastapi.publish"):
        mock_pm.get.return_value = mock_persona
        reapply_if_active(system, "persona", "anita")
    # _apply_chat_settings called once with the persona's settings
    # (plus the stamped persona name)
    mock_apply.assert_called_once()
    called_system, called_settings = mock_apply.call_args.args
    assert called_system is system
    assert called_settings["persona"] == "anita"
    assert called_settings["prompt"] == "anita-prompt"
    assert called_settings["toolset"] == "anita-tools"


def test_save_does_not_reapply_when_not_active():
    """Saving a toolset the active chat doesn't use must NOT re-apply.
    Proves the domain-match check gates the hot-reload and there's no
    cross-chat collateral."""
    from core.api_fastapi import reapply_if_active
    system = _make_system({"toolset": "currently-active"})
    with patch("core.api_fastapi.publish") as mock_publish:
        reapply_if_active(system, "toolset", "some-other-toolset")
    system.llm_chat.function_manager.update_enabled_functions.assert_not_called()
    # Should NOT fire TOOLSET_CHANGED — this save doesn't touch any
    # active chat's runtime state.
    assert not any(
        call.args and call.args[1].get("name") == "some-other-toolset"
        for call in mock_publish.call_args_list
    )


def test_save_survives_reapply_exception():
    """If the re-apply call raises, reapply_if_active must swallow it
    (log + return). Save response must not get hijacked by hot-reload
    plumbing bugs. We don't re-raise, we log and move on."""
    from core.api_fastapi import reapply_if_active
    system = _make_system({"toolset": "boom"})
    system.llm_chat.function_manager.update_enabled_functions.side_effect = RuntimeError(
        "simulated hot-reload failure"
    )
    with patch("core.api_fastapi.publish"):
        # Must NOT raise
        reapply_if_active(system, "toolset", "boom")


def test_empty_chat_settings_is_safe():
    """get_chat_settings() returning None (fresh boot, no active chat)
    must be handled — `or {}` guards the lookup."""
    from core.api_fastapi import reapply_if_active
    system = MagicMock()
    system.llm_chat.session_manager.get_chat_settings.return_value = None
    # Must NOT raise AttributeError on None.get(...)
    reapply_if_active(system, "toolset", "anything")
    system.llm_chat.function_manager.update_enabled_functions.assert_not_called()


def test_prompt_with_empty_content_applies_empty_string():
    """An existing prompt with empty content is a valid state — the user's
    'blank' prompt is intentionally empty so they can run with NO system
    prompt. The hot-reload must apply the empty content (clearing the live
    prompt), NOT silently skip and leave the previous prompt loaded.
    Pre-2026-04-27, falsy-content was treated as "missing" and the previous
    prompt stayed active — making 'blank' a no-op. Regression test."""
    from core.api_fastapi import reapply_if_active
    system = _make_system({"prompt": "blank"})
    with patch("core.api_fastapi.prompts") as mock_prompts, \
         patch("core.api_fastapi.publish"):
        mock_prompts.get_prompt.return_value = {"content": ""}
        reapply_if_active(system, "prompt", "blank")
    system.llm_chat.set_system_prompt.assert_called_once_with("")


def test_prompt_with_none_get_result_is_silent_noop():
    """When prompts.get_prompt returns None (truly missing prompt),
    skip set_system_prompt rather than pushing garbage into runtime."""
    from core.api_fastapi import reapply_if_active
    system = _make_system({"prompt": "ghost"})
    with patch("core.api_fastapi.prompts") as mock_prompts, \
         patch("core.api_fastapi.publish"):
        mock_prompts.get_prompt.return_value = None
        reapply_if_active(system, "prompt", "ghost")
    system.llm_chat.set_system_prompt.assert_not_called()


def test_persona_not_found_is_silent_noop():
    """If persona_manager.get returns None (renamed, deleted race),
    skip _apply_chat_settings rather than crashing."""
    from core.api_fastapi import reapply_if_active
    system = _make_system({"persona": "ghost"})
    with patch("core.personas.persona_manager") as mock_pm, \
         patch("core.api_fastapi._apply_chat_settings") as mock_apply:
        mock_pm.get.return_value = None
        reapply_if_active(system, "persona", "ghost")
    mock_apply.assert_not_called()
