"""Persona-bundle full-propagation regression guard.

Voice mode is going to fire persona switches MANY times per session (user
walking around, switching contexts, multi-character roleplay). When that
happens, `_apply_chat_settings` (api_fastapi.py:501) needs to propagate
the persona's full bundle to every subsystem: TTS, prompt, scopes, spice
set, toolset.

Pre-test, each of the 5 try blocks excepted-and-continued silently. There
were tests for each block in isolation but no single test asserted ALL 5
fire together. Silent-skip in any one (e.g. TTS swallowed an exception
while everything else updated) was undetectable by the suite.

This test drives `_apply_chat_settings` with a complete persona bundle
and asserts every subsystem received its update. Voice-mode amplifier:
this fires 100×/session. One silent skip = silent drift the user can't
articulate ("voice didn't change after I switched personas").

Coverage scout HDF, 2026-05-07.
"""
import threading
from unittest.mock import MagicMock

import pytest


def _make_fm_with_toolset():
    """FunctionManager stub with the attributes _apply_chat_settings touches."""
    from core.chat.function_manager import FunctionManager
    fm = FunctionManager.__new__(FunctionManager)
    fm._tools_lock = threading.Lock()
    fm.all_possible_tools = []
    fm._mode_filters = {}
    fm.current_toolset_name = "none"
    fm.function_modules = {}
    fm._enabled_tools = []
    fm.update_enabled_functions = MagicMock()
    fm.set_rag_scope = MagicMock()
    return fm


def test_apply_chat_settings_full_bundle_hits_every_subsystem(monkeypatch):
    """[REGRESSION_GUARD] One persona-style settings dict must propagate to
    TTS (voice/pitch/speed), prompt, scopes, spice_set, AND toolset.

    Voice mode amplifier: persona switches fire 100x/session. A silent
    skip in any of the 5 try blocks at api_fastapi.py:504-614 would be
    invisible — every other subsystem still updates. This test asserts
    the full bundle reaches every destination."""
    from core import api_fastapi

    # Mock system with TTS + llm_chat + session_manager
    system = MagicMock()
    fm = _make_fm_with_toolset()
    system.llm_chat.function_manager = fm
    sm = system.llm_chat.session_manager
    sm.get_active_chat_name.return_value = 'trinity'
    sm.update_chat_settings.return_value = True

    # Stub prompts.get_prompt so the prompt block runs the success path
    fake_prompt = {'name': 'anita', 'content': 'You are Anita.'}
    monkeypatch.setattr(
        api_fastapi.prompts, 'get_prompt',
        lambda name: fake_prompt if name == 'anita' else None
    )
    monkeypatch.setattr(api_fastapi.prompts, 'set_active_preset_name', MagicMock())
    # apply_scenario only fires for scenario presets; bypass via attr-check
    # `scenario_presets` is a read-only property on PromptManager; bypass the
    # scenario branch by stubbing it on the prompts module the route reads.
    monkeypatch.setattr(
        api_fastapi.prompts.prompt_manager,
        '_scenarios',
        {},
        raising=False,
    )
    monkeypatch.setattr(
        api_fastapi.prompts, 'apply_scenario', MagicMock(), raising=False
    )

    # Stub spice_set_manager so the spice block runs the success path
    from core.spice_sets import spice_set_manager
    monkeypatch.setattr(spice_set_manager, 'set_exists', lambda n: n == 'anita-spice')
    monkeypatch.setattr(spice_set_manager, 'get_categories', lambda n: ['mood', 'tone'])
    # Mock the disabled_categories path
    if not hasattr(api_fastapi.prompts.prompt_manager, 'spices'):
        api_fastapi.prompts.prompt_manager.spices = {'mood': [], 'tone': [], 'extra': []}
    monkeypatch.setattr(api_fastapi.prompts.prompt_manager, 'save_spices', MagicMock())
    monkeypatch.setattr(api_fastapi.prompts, 'invalidate_spice_picks', MagicMock())

    settings = {
        'persona': 'anita',
        'voice': 'af_heart',
        'pitch': 1.1,
        'speed': 0.95,
        'prompt': 'anita',
        'toolset': 'anita-tools',
        'spice_set': 'anita-spice',
    }

    api_fastapi._apply_chat_settings(system, settings)

    # 1. TTS subsystem received voice + pitch + speed
    system.tts.set_voice.assert_called_with('af_heart')
    system.tts.set_pitch.assert_called_with(1.1)
    system.tts.set_speed.assert_called_with(0.95)

    # 2. Prompt subsystem received the loaded content
    system.llm_chat.set_system_prompt.assert_called_with('You are Anita.')

    # 3. Toolset subsystem received the toolset name. Positional args only —
    # the call also carries extra_toolsets (the union primitive, 2026-08-03)
    # and pinning the full signature made this fail on an unrelated change.
    assert fm.update_enabled_functions.call_args[0] == (['anita-tools'],)

    # 4. Spice subsystem set the active name
    assert spice_set_manager.active_name == 'anita-spice'


def test_apply_chat_settings_skips_silent_failure_is_caught(monkeypatch):
    """[REGRESSION_GUARD] If the TTS block raises, the prompt+toolset+spice
    blocks MUST still run. Catches the regression where a refactor accidentally
    coalesces the try blocks into one (skip-everything-on-first-failure)."""
    from core import api_fastapi

    system = MagicMock()
    fm = _make_fm_with_toolset()
    system.llm_chat.function_manager = fm
    sm = system.llm_chat.session_manager
    sm.get_active_chat_name.return_value = 'trinity'

    # TTS blows up
    system.tts.set_voice.side_effect = RuntimeError("TTS provider down")

    fake_prompt = {'name': 'p', 'content': 'You are P.'}
    monkeypatch.setattr(
        api_fastapi.prompts, 'get_prompt',
        lambda name: fake_prompt if name == 'p' else None
    )
    monkeypatch.setattr(api_fastapi.prompts, 'set_active_preset_name', MagicMock())
    # `scenario_presets` is a read-only property on PromptManager; bypass the
    # scenario branch by stubbing it on the prompts module the route reads.
    monkeypatch.setattr(
        api_fastapi.prompts.prompt_manager,
        '_scenarios',
        {},
        raising=False,
    )
    monkeypatch.setattr(
        api_fastapi.prompts, 'apply_scenario', MagicMock(), raising=False
    )

    settings = {
        'voice': 'af_heart',
        'prompt': 'p',
        'toolset': 'minimal',
    }

    api_fastapi._apply_chat_settings(system, settings)

    # TTS failed but the rest still ran (positional args only — see above)
    system.llm_chat.set_system_prompt.assert_called_with('You are P.')
    assert fm.update_enabled_functions.call_args[0] == (['minimal'],)
