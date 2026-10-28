"""Hunt 21 privacy fixes outside rooms (tests/test_rooms.py has the room ones)."""
import pytest


def test_the_default_prompt_carries_the_live_presets_privacy(monkeypatch):
    """A task resolving 'default' wore the active chat's private preset with
    privacy forced off, so the resolver let it go to a cloud model."""
    from core import prompt_crud, prompt_state
    monkeypatch.setattr(prompt_state, 'is_current_prompt_private', lambda: True)
    assert prompt_crud.get_prompt('default')['privacy_required'] is True
    monkeypatch.setattr(prompt_state, 'is_current_prompt_private', lambda: False)
    monkeypatch.setattr(prompt_state, 'get_active_preset_name', lambda: 'my-secret')
    monkeypatch.setattr(prompt_crud, 'is_vault_prompt', lambda n: n == 'my-secret')
    assert prompt_crud.get_prompt('default')['privacy_required'] is True         # a vaulted preset counts
    monkeypatch.setattr(prompt_crud, 'is_vault_prompt', lambda n: False)
    assert prompt_crud.get_prompt('default')['privacy_required'] is False


def test_a_remote_embedder_skips_a_private_turn():
    from core.chat.function_manager import scope_private
    from core.embeddings import _private_turn_refuses
    tok = scope_private.set(True)
    try:
        assert _private_turn_refuses('https://api.nomic.example/v1/embeddings')
        assert not _private_turn_refuses('http://192.168.1.20:8080/v1/embeddings')    # the LAN is near
    finally:
        scope_private.reset(tok)
    assert not _private_turn_refuses('https://api.nomic.example/v1/embeddings')       # a public turn


def test_a_task_cannot_swap_the_operators_prompt(monkeypatch):
    from functions import meta
    from core.chat import stream_brain
    calls = []
    monkeypatch.setattr('core.prompts.get_prompt', lambda n: {'type': 'assembled', 'content': 'x'})
    monkeypatch.setattr('core.prompts.list_prompts', lambda: ['other'])
    monkeypatch.setattr('core.prompts.activate_prompt', lambda *a: calls.append(a) or (True, ''))
    monkeypatch.setattr('core.prompts.clear_transients', lambda: calls.append('clear'))
    tok = stream_brain._override.set({'ephemeral': True})
    try:
        msg, ok = meta._prompt_switch({'name': 'other'})
    finally:
        stream_brain._override.reset(tok)
    assert not ok and calls == []                         # the operator's persona and moods untouched
