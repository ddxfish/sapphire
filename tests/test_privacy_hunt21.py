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


# ── hunt 2026-11-16: the piece and edit tools had no such guard ──

@pytest.fixture
def lanes(monkeypatch):
    """Her prompt tools with every write recorded, and an open chat named 'main'."""
    from types import SimpleNamespace
    from functions import meta
    calls = []
    sm = SimpleNamespace(get_active_chat_name=lambda: 'main')
    monkeypatch.setattr(meta, '_system', lambda: SimpleNamespace(llm_chat=SimpleNamespace(session_manager=sm)))
    monkeypatch.setattr(meta, '_save_and_activate_assembled', lambda system, state=None: calls.append(('save', state)) or 'p')
    monkeypatch.setattr(meta, '_resnapshot', lambda system: calls.append('resnapshot'))
    monkeypatch.setattr(meta, '_audit_emit', lambda e: None)
    monkeypatch.setattr(meta, '_status_string', lambda: '')
    monkeypatch.setattr('core.prompts.visible_components', lambda: {'emotions': {'happy': 'x'}, 'character': {'sapphire': 'x'}})
    monkeypatch.setattr('core.prompts.set_transient_piece', lambda *a: calls.append(('transient', a)))
    monkeypatch.setattr('core.prompts.remove_transient_piece', lambda *a: calls.append(('untransient', a)) or False)
    monkeypatch.setattr('core.prompts.get_active_preset_name', lambda: 'sapphire')
    monkeypatch.setattr('core.prompts.get_prompt', lambda n: {'type': 'monolith', 'content': 'I am Sapphire.'})
    monkeypatch.setattr('core.prompts.save_prompt', lambda *a, **k: calls.append(('save_prompt', a[0])) or (True, ''))
    monkeypatch.setattr('core.prompts.activate_prompt', lambda *a: calls.append(('activate', a[0])) or (True, ''))
    monkeypatch.setattr('core.prompts.is_vault_prompt', lambda n: False)
    return meta, calls


@pytest.mark.parametrize('override', [{'chat': 'phone-call', 'settings': {}}, {'chat': None, 'ephemeral': True}])
def test_a_call_or_a_task_cannot_rewrite_the_operators_prompt(lanes, override):
    """On a call, a satellite or a task on another chat, prompt_pieces and prompt_edit copied the
    OPEN chat's live prompt, saved it under the operator's preset and stamped it on the lane's chat."""
    from core.chat import stream_brain
    meta, calls = lanes
    tok = stream_brain._override.set(override)
    try:
        for args in ({'action': 'set', 'component': 'emotions', 'key': 'happy'},
                     {'action': 'set', 'component': 'emotions', 'key': 'happy', 'minutes': 5},
                     {'action': 'remove', 'component': 'emotions', 'key': 'happy'}):
            msg, ok = meta._prompt_pieces(args)
            assert not ok and 'unchanged' in msg, args
        msg, ok = meta._prompt_edit({'old_text': 'Sapphire', 'new_text': 'Someone else'})
        assert not ok and 'unchanged' in msg
    finally:
        stream_brain._override.reset(tok)
    assert calls == []                                    # nothing saved, activated or re-snapshotted


def test_the_open_chat_keeps_its_tools_however_the_turn_arrived(lanes):
    """A voice turn that lands in the open chat carries an override naming that chat: it IS the open chat."""
    from core.chat import stream_brain
    meta, calls = lanes
    for override in (None, {'chat': 'main', 'settings': {}}):
        calls.clear()
        tok = stream_brain._override.set(override)
        try:
            msg, ok = meta._prompt_pieces({'action': 'set', 'component': 'emotions', 'key': 'happy'})
            assert ok and calls and calls[0][0] == 'save', (override, msg)
            msg, ok = meta._prompt_edit({'old_text': 'Sapphire', 'new_text': 'Sapph'})
            assert ok and ('save_prompt', 'sapphire') in calls, (override, msg)
        finally:
            stream_brain._override.reset(tok)
