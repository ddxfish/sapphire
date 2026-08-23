"""GET /api/chats/{name}/prompt-preview (2026-08-23) — the system prompt +
ghost envelope EXACTLY as a turn on that chat would carry them, built by
the same assembly (`_get_system_prompt` / `build_ghost_message`). The
story room's 👁 preview used to stitch this client-side and silently
dropped plugin injections (avatar instructions she was getting unseen).

- active chat: no brain override; the live enabled-tools list
- other chat: stream-brain override for the duration, reset after
- missing chat: 404
"""
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def preview_client(client, mock_system, monkeypatch):
    c, csrf = client
    sm = mock_system.llm_chat.session_manager
    sm.active_chat_name = 'trinity'
    sm.get_chat_settings.return_value = {'surface': 'game', 'toolset': 'none'}
    mock_system.llm_chat._get_system_prompt.return_value = ('PERSONA\n\n[Avatar] tags', 'user', None)
    mock_system.llm_chat.function_manager.get_enabled_function_names.return_value = ['story_act']
    from core import ghost_messages
    monkeypatch.setattr(ghost_messages, 'build_ghost_message',
                        lambda system, settings, user_input='': '[envelope] room block')
    return c, mock_system


def test_active_chat_preview_is_core_assembly(preview_client):
    c, system = preview_client
    r = c.get('/api/chats/trinity/prompt-preview')
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['system_prompt'] == 'PERSONA\n\n[Avatar] tags'
    assert body['ghost'] == '[envelope] room block'
    assert body['tools'] == ['story_act']
    assert body['surface'] == 'game'
    # no override for the active chat
    system.llm_chat.session_manager.make_stream_session.assert_not_called()


def test_other_chat_rides_stream_brain_override(preview_client, monkeypatch):
    c, system = preview_client
    sm = system.llm_chat.session_manager
    sm.make_stream_session.return_value = {
        'chat': 'side-chat', 'settings': {'prompt': 'persona_b', 'toolset': 'all'},
        'system_prompt': None, 'tools': None, 'history': MagicMock()}
    system.llm_chat._resolve_toolset_tools.return_value = [
        {'function': {'name': 'zeta'}}, {'function': {'name': 'alpha'}}]
    from core import prompts
    monkeypatch.setattr(prompts, 'get_prompt', lambda n: {'content': f'prompt:{n}'})
    seen = {}
    from core.chat import stream_brain

    def _capture(*a, **k):
        seen['override'] = stream_brain.get_override()
        return ('SIDE PROMPT', 'user', None)
    system.llm_chat._get_system_prompt.side_effect = _capture
    r = c.get('/api/chats/side-chat/prompt-preview')
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['system_prompt'] == 'SIDE PROMPT'
    assert body['tools'] == ['alpha', 'zeta']
    # the override was live DURING assembly and bound to that chat…
    assert seen['override']['chat'] == 'side-chat'
    assert seen['override']['system_prompt'] == 'prompt:persona_b'
    # …and is gone after
    assert stream_brain.get_override() is None


def test_missing_chat_404(preview_client):
    c, system = preview_client
    system.llm_chat.session_manager.make_stream_session.return_value = None
    r = c.get('/api/chats/nope/prompt-preview')
    assert r.status_code == 404
