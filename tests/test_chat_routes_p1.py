"""Surface 2 P1 — chat route coverage beyond the P0 regression guards.

Covers what P0 didn't:
  - Toolset key handling (`toolset` vs legacy `ability`, 'none' clears)
  - Private chat toggle via settings (managed-mode gate, scope round-trip)
  - Chat deletion side effects (fallback settings re-apply, rag scope cleanup,
    per-chat agent dismissal)
  - History message deletion (count=-1 fires CHAT_CLEARED, count=0 rejected,
    user_message vs count precedence)
  - History import (replace semantics, type validation)
  - PUT settings idempotence

See tmp/coverage-test-plan.md Surface 2 for stubs 2.12-2.29.
"""
import threading
from unittest.mock import MagicMock

import pytest


def _make_fm():
    """Real FunctionManager minimal-attrs pattern.

    Same skeleton as test_chat_settings_routes_p0 — kept local so this file
    stays self-contained.
    """
    from core.chat.function_manager import FunctionManager
    fm = FunctionManager.__new__(FunctionManager)
    fm._tools_lock = threading.Lock()
    fm.all_possible_tools = []
    fm._mode_filters = {}
    fm.current_toolset_name = "none"
    fm.function_modules = {}
    fm._enabled_tools = []
    fm.get_current_toolset_info = MagicMock(return_value={'name': 'none'})
    fm.get_enabled_function_names = MagicMock(return_value=[])
    return fm


@pytest.fixture
def chat_client(client, mock_system, scope_snapshot, monkeypatch):
    """TestClient + mock_system with spy on scope state.

    Same pattern as P0: FastAPI's route-async-context ContextVars don't leak
    back to the test thread; spy `apply_scopes_from_settings` + `set_rag_scope`
    to capture post-apply state.
    """
    from core.chat import function_manager as fm_mod
    c, csrf = client
    fm = _make_fm()
    mock_system.llm_chat.function_manager = fm
    sm = mock_system.llm_chat.session_manager
    sm.get_active_chat_name.return_value = 'trinity'
    sm.active_chat_name = 'trinity'
    sm.update_chat_settings.return_value = True
    sm.get_chat_settings.return_value = {}
    mock_system.llm_chat.switch_chat.return_value = True
    mock_system.llm_chat.get_active_chat.return_value = 'trinity'
    mock_system.llm_chat.create_chat.return_value = True
    mock_system.llm_chat.delete_chat.return_value = True
    mock_system.llm_chat.list_chats.return_value = []

    captured = {}
    orig_apply = fm_mod.apply_scopes_from_settings

    def spy_apply(fm_, settings_):
        orig_apply(fm_, settings_)
        captured.clear()
        for n, r in list(fm_mod.SCOPE_REGISTRY.items()):
            captured[n] = r['var'].get()

    monkeypatch.setattr(fm_mod, 'apply_scopes_from_settings', spy_apply)

    def spy_set_rag(val):
        captured['rag'] = val
    fm.set_rag_scope = spy_set_rag

    return c, csrf, mock_system, fm, captured


# ─── 2.12 Toolset 'none' clears enabled functions ────────────────────────────

def test_put_toolset_none_clears_enabled_functions(chat_client):
    """[PROACTIVE] Setting toolset='none' in chat settings must empty
    FunctionManager._enabled_tools immediately (before response).

    Note: the route applies settings from `session_manager.get_chat_settings()`
    after update — so we mock that to return our payload.
    """
    c, csrf, mock_system, fm, captured = chat_client
    payload = {'toolset': 'none'}
    mock_system.llm_chat.session_manager.get_chat_settings.return_value = payload
    fm._enabled_tools = [{'function': {'name': 'read_file'}}]

    r = c.put(
        '/api/chats/trinity/settings',
        headers={'X-CSRF-Token': csrf},
        json={'settings': payload},
    )
    assert r.status_code == 200
    assert fm._enabled_tools == []
    assert fm.current_toolset_name == 'none'


# ─── 2.13 Legacy 'ability' key still switches toolset ────────────────────────

def test_put_legacy_ability_key_still_switches_toolset(chat_client):
    """[PROACTIVE] Old clients send 'ability' instead of 'toolset' — the route
    still honors it (see core/api_fastapi.py _apply_chat_settings)."""
    c, csrf, mock_system, fm, captured = chat_client
    payload = {'ability': 'none'}
    mock_system.llm_chat.session_manager.get_chat_settings.return_value = payload
    fm._enabled_tools = [{'function': {'name': 'ghost'}}]

    r = c.put(
        '/api/chats/trinity/settings',
        headers={'X-CSRF-Token': csrf},
        json={'settings': payload},
    )
    assert r.status_code == 200
    assert fm._enabled_tools == []
    assert fm.current_toolset_name == 'none'


# ─── 2.16 PUT settings idempotent ────────────────────────────────────────────

def test_put_settings_idempotent(chat_client):
    """Double-PUT with identical settings must succeed both times and leave
    the system in the same state (no accumulation, no errors)."""
    c, csrf, mock_system, fm, captured = chat_client
    settings = {'memory_scope': 'idempotent_test'}
    mock_system.llm_chat.session_manager.get_chat_settings.return_value = settings

    payload = {'settings': settings}
    r1 = c.put('/api/chats/trinity/settings', headers={'X-CSRF-Token': csrf}, json=payload)
    r2 = c.put('/api/chats/trinity/settings', headers={'X-CSRF-Token': csrf}, json=payload)
    assert r1.status_code == 200 and r2.status_code == 200
    assert captured.get('memory') == 'idempotent_test'


# ─── 2.19 Private toggle blocked in managed mode ─────────────────────────────

def test_private_toggle_blocked_in_managed_mode(chat_client, monkeypatch):
    """[PROACTIVE] Managed deployments can't flip a chat private via the
    settings route (eyeball parity with the old create-private gate)."""
    from core import settings_manager as sm_mod
    c, csrf, mock_system, fm, captured = chat_client
    monkeypatch.setattr(sm_mod.settings, 'is_managed', lambda: True)

    r = c.put('/api/chats/trinity/settings', headers={'X-CSRF-Token': csrf},
              json={'settings': {'private_chat': True}})
    assert r.status_code == 403
    # Settings were never written (guard short-circuited)
    assert mock_system.llm_chat.session_manager.update_chat_settings.call_count == 0


# ─── 2.20/2.21 Private toggle round-trip via settings (the eyeball flow) ─────

def test_private_toggle_roundtrip_applies_scope(chat_client, monkeypatch):
    """[PROACTIVE] private_chat=True through PUT settings must land in
    scope_private (network tools refuse); False must clear it. This is the
    whole eyeball contract — the flag toggles both ways, live."""
    from core import settings_manager as sm_mod
    c, csrf, mock_system, fm, captured = chat_client
    monkeypatch.setattr(sm_mod.settings, 'is_managed', lambda: False)
    sm = mock_system.llm_chat.session_manager

    on = {'private_chat': True}
    sm.get_chat_settings.return_value = on
    r = c.put('/api/chats/trinity/settings', headers={'X-CSRF-Token': csrf},
              json={'settings': on})
    assert r.status_code == 200
    assert sm.update_chat_settings.call_args_list[-1].args[0].get('private_chat') is True
    assert captured.get('private') is True

    off = {'private_chat': False}
    sm.get_chat_settings.return_value = off
    r = c.put('/api/chats/trinity/settings', headers={'X-CSRF-Token': csrf},
              json={'settings': off})
    assert r.status_code == 200
    assert captured.get('private') is False


def test_managed_mode_allows_toggling_private_off(chat_client, monkeypatch):
    """Managed guard only blocks turning private ON — a pre-existing private
    chat can still be switched off (and other settings keep working)."""
    from core import settings_manager as sm_mod
    c, csrf, mock_system, fm, captured = chat_client
    monkeypatch.setattr(sm_mod.settings, 'is_managed', lambda: True)
    sm = mock_system.llm_chat.session_manager
    sm.get_chat_settings.return_value = {'private_chat': False}

    r = c.put('/api/chats/trinity/settings', headers={'X-CSRF-Token': csrf},
              json={'settings': {'private_chat': False}})
    assert r.status_code == 200


# ─── Private chat: compress provider gate ────────────────────────────────────

def test_compress_refuses_cloud_provider_for_private_chat(chat_client):
    """[PROACTIVE] A private chat's words must not reach a cloud summarizer —
    the compress route 400s on a cloud provider before any job starts."""
    c, csrf, mock_system, fm, captured = chat_client
    sm = mock_system.llm_chat.session_manager
    sm.read_chat_settings.return_value = {'private_chat': True}

    r = c.post('/api/chats/trinity/compress', headers={'X-CSRF-Token': csrf},
               json={'mode': 'whole', 'provider': 'claude', 'keep_last_turns': 1})
    assert r.status_code == 400
    assert 'private' in r.json()['detail'].lower()


def test_compress_allows_local_provider_for_private_chat(chat_client, monkeypatch):
    """A provider with the is_local checkbox ticked passes the private gate
    and the job starts. (Provider injected — the gate reads config, not the
    box's real settings.)"""
    import config
    from core.chat import compress
    c, csrf, mock_system, fm, captured = chat_client
    sm = mock_system.llm_chat.session_manager
    sm.read_chat_settings.return_value = {'private_chat': True}
    monkeypatch.setattr(config, 'LLM_CUSTOM_PROVIDERS',
                        {'localbox': {'enabled': True, 'is_local': True,
                                      'base_url': 'http://127.0.0.1:1234/v1'}},
                        raising=False)
    monkeypatch.setattr(compress, 'start_compress_job', lambda *a, **k: (True, None))

    r = c.post('/api/chats/trinity/compress', headers={'X-CSRF-Token': csrf},
               json={'mode': 'whole', 'provider': 'localbox', 'keep_last_turns': 1})
    assert r.status_code == 200, r.text


def test_compress_guard_uses_half_history_window(chat_client, monkeypatch):
    """[REGRESSION_GUARD] LLM_MAX_HISTORY counts single MESSAGES; retention is
    max_history // 2 user turns (get_messages_for_llm). The compressed-but-
    invisible guard must reject keep_last_turns at HALF the window — the old
    full-window compare let keep_last in [half, full) through, and the summary
    never reached the model (bug hunt 2026-07-15 #9)."""
    import config
    from core.chat import compress
    c, csrf, mock_system, fm, captured = chat_client
    sm = mock_system.llm_chat.session_manager
    sm.read_chat_settings.return_value = {}
    monkeypatch.setattr(config, 'LLM_MAX_HISTORY', 30, raising=False)
    monkeypatch.setattr(config, 'LLM_CUSTOM_PROVIDERS',
                        {'localbox': {'enabled': True, 'is_local': True,
                                      'base_url': 'http://127.0.0.1:1234/v1'}},
                        raising=False)
    monkeypatch.setattr(compress, 'start_compress_job', lambda *a, **k: (True, None))

    # 15 == 30 // 2: first invisible value — must be refused
    r = c.post('/api/chats/trinity/compress', headers={'X-CSRF-Token': csrf},
               json={'mode': 'whole', 'provider': 'localbox', 'keep_last_turns': 15})
    assert r.status_code == 400
    assert 'keep_last_turns' in r.json()['detail']

    # 14: last visible value — must pass the guard and start the job
    r = c.post('/api/chats/trinity/compress', headers={'X-CSRF-Token': csrf},
               json={'mode': 'whole', 'provider': 'localbox', 'keep_last_turns': 14})
    assert r.status_code == 200, r.text


# ─── Private chat: persona load preserves the flag ───────────────────────────

def test_persona_load_preserves_private_chat(chat_client, monkeypatch):
    """[REGRESSION_GUARD] Loading a persona must NOT flip the chat's
    private_chat — privacy belongs to the chat. (Old footgun: persona apply
    stamped private_chat=False and silently un-privated chats.)"""
    from core.personas import persona_manager
    c, csrf, mock_system, fm, captured = chat_client
    sm = mock_system.llm_chat.session_manager
    sm.get_chat_settings.return_value = {'private_chat': True}
    monkeypatch.setattr(persona_manager, 'get',
                        lambda n: {'settings': {'prompt': 'sapphire', 'private_chat': False}})

    r = c.post('/api/personas/nova/load', headers={'X-CSRF-Token': csrf}, json={})
    assert r.status_code == 200, r.text
    stamped = sm.update_chat_settings.call_args_list[-1].args[0]
    # Chat's flag wins over the persona's stored False
    assert stamped['private_chat'] is True
    assert r.json()['settings']['private_chat'] is True


# ─── 2.22 Delete active chat re-applies fallback chat settings ───────────────

def test_delete_active_chat_reapplies_fallback_settings(chat_client):
    """[REGRESSION_GUARD] Deleting the active chat must apply the fallback
    chat's settings — otherwise the UI keeps showing the dead chat's
    toolset/scopes until another activate."""
    c, csrf, mock_system, fm, captured = chat_client
    mock_system.llm_chat.get_active_chat.return_value = 'trinity'
    sm = mock_system.llm_chat.session_manager
    sm.get_chat_settings.return_value = {'memory_scope': 'fallback_mem'}
    sm.get_active_chat_name.return_value = 'fallback'

    r = c.delete('/api/chats/trinity', headers={'X-CSRF-Token': csrf})
    assert r.status_code == 200
    # _apply_chat_settings ran → captured has the fallback's memory scope
    assert captured.get('memory') == 'fallback_mem'


def test_delete_inactive_chat_does_not_reapply(chat_client):
    """Deleting a non-active chat must NOT touch the current chat's settings."""
    c, csrf, mock_system, fm, captured = chat_client
    mock_system.llm_chat.get_active_chat.return_value = 'other_chat'
    sm = mock_system.llm_chat.session_manager
    sm.get_chat_settings.return_value = {'memory_scope': 'should_not_leak'}

    r = c.delete('/api/chats/trinity', headers={'X-CSRF-Token': csrf})
    assert r.status_code == 200
    # _apply_chat_settings was NOT called → captured stays empty
    assert 'memory' not in captured


# ─── 2.23 Delete chat cleans up RAG scope ────────────────────────────────────

def test_delete_chat_deletes_rag_scope(chat_client, monkeypatch):
    """[PROACTIVE] Per-chat RAG documents must be purged when the chat is
    deleted — otherwise uploaded docs linger forever in __rag__:{chat_name}."""
    from plugins.memory.tools import knowledge_tools as knowledge_mod
    c, csrf, mock_system, fm, captured = chat_client
    delete_scope_mock = MagicMock(return_value=True)
    monkeypatch.setattr(knowledge_mod, 'delete_scope', delete_scope_mock)

    r = c.delete('/api/chats/dead_chat', headers={'X-CSRF-Token': csrf})
    assert r.status_code == 200
    delete_scope_mock.assert_called_once_with('__rag__:dead_chat')


# ─── 2.24 Delete chat dismisses per-chat agents ──────────────────────────────

def test_delete_chat_dismisses_per_chat_agents(chat_client):
    """[PROACTIVE] Any agents spawned for the deleted chat must be dismissed.
    Otherwise they keep running with a dangling chat_name reference."""
    c, csrf, mock_system, fm, captured = chat_client
    am = mock_system.agent_manager
    am.check_all.return_value = [
        {'id': 'aaa111', 'name': 'Alpha'},
        {'id': 'bbb222', 'name': 'Bravo'},
    ]
    am.dismiss.return_value = {'name': 'Alpha', 'status': 'dismissed'}

    r = c.delete('/api/chats/dead_chat', headers={'X-CSRF-Token': csrf})
    assert r.status_code == 200
    am.check_all.assert_called_with(chat_name='dead_chat')
    dismiss_ids = [call.args[0] for call in am.dismiss.call_args_list]
    assert 'aaa111' in dismiss_ids and 'bbb222' in dismiss_ids


# ─── 2.25 DELETE /api/history/messages count=-1 publishes CHAT_CLEARED ───────

def test_delete_history_count_neg1_publishes_chat_cleared(chat_client, event_bus_capture):
    """[REGRESSION_GUARD] Clearing all history must fire CHAT_CLEARED with the
    origin session ID — the event drives the sidebar to refresh across tabs."""
    c, csrf, mock_system, fm, captured = chat_client
    mock_system.llm_chat.session_manager.get_active_chat_name.return_value = 'trinity'
    mock_system.llm_chat.session_manager.clear = MagicMock(return_value=True)

    r = c.request(
        'DELETE', '/api/history/messages',
        headers={'X-CSRF-Token': csrf, 'X-Session-ID': 'sess-42'},
        json={'count': -1},
    )
    assert r.status_code == 200
    events = [d for ev, d in event_bus_capture.events if ev == 'chat_cleared']
    assert events, "no chat_cleared event fired"
    assert events[-1]['chat_name'] == 'trinity'
    assert events[-1]['origin'] == 'sess-42'


# ─── 2.26 User_message takes precedence over count ───────────────────────────

def test_delete_history_user_message_takes_precedence_over_count(chat_client):
    """When both user_message and count are provided, user_message wins
    (handler short-circuits before count is inspected)."""
    c, csrf, mock_system, fm, captured = chat_client
    sm = mock_system.llm_chat.session_manager
    sm.remove_from_user_message = MagicMock(return_value=True)
    sm.remove_last_messages = MagicMock(return_value=True)
    sm.clear = MagicMock()

    r = c.request(
        'DELETE', '/api/history/messages',
        headers={'X-CSRF-Token': csrf},
        json={'user_message': 'hello', 'count': 3},
    )
    assert r.status_code == 200
    sm.remove_from_user_message.assert_called_once_with('hello')
    sm.remove_last_messages.assert_not_called()
    sm.clear.assert_not_called()


# ─── 2.27 delete_history count=0 returns 400 ─────────────────────────────────

def test_delete_history_count_zero_returns_400(chat_client):
    """count=0 is an invalid delete-count — route must reject with 400."""
    c, csrf, mock_system, fm, captured = chat_client
    r = c.request(
        'DELETE', '/api/history/messages',
        headers={'X-CSRF-Token': csrf},
        json={'count': 0},
    )
    assert r.status_code == 400


# ─── 2.28 History import replaces, does not append ───────────────────────────

def test_import_history_replaces_not_appends(chat_client):
    """[PROACTIVE] POST /api/history/import must ASSIGN the messages list,
    not extend the existing list. Otherwise importing into a non-empty chat
    leaves old messages above the imported ones."""
    c, csrf, mock_system, fm, captured = chat_client
    existing_chat = MagicMock()
    existing_chat.messages = [{'role': 'user', 'content': 'old'}]
    mock_system.llm_chat.session_manager.current_chat = existing_chat
    mock_system.llm_chat.session_manager._save_current_chat = MagicMock()

    new_msgs = [
        {'role': 'user', 'content': 'new-1'},
        {'role': 'assistant', 'content': 'new-2'},
    ]
    r = c.post(
        '/api/history/import',
        headers={'X-CSRF-Token': csrf},
        json={'messages': new_msgs},
    )
    assert r.status_code == 200
    # messages was ASSIGNED (not extended) — identity is the new list
    assert existing_chat.messages == new_msgs
    assert mock_system.llm_chat.session_manager._save_current_chat.call_count == 1


# ─── 2.29 Import non-list messages returns 400 ───────────────────────────────

def test_import_history_non_list_messages_returns_400(chat_client):
    """Messages must be a list. String, dict, None → 400."""
    c, csrf, mock_system, fm, captured = chat_client

    for bad in ('a string', {'role': 'user'}, None):
        r = c.post(
            '/api/history/import',
            headers={'X-CSRF-Token': csrf},
            json={'messages': bad},
        )
        assert r.status_code == 400, f"expected 400 for {bad!r}, got {r.status_code}"


def test_import_history_empty_list_rejected(chat_client):
    """Empty list — route treats it as invalid since there's nothing to import."""
    c, csrf, mock_system, fm, captured = chat_client
    r = c.post(
        '/api/history/import',
        headers={'X-CSRF-Token': csrf},
        json={'messages': []},
    )
    assert r.status_code == 400
