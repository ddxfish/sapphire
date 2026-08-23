"""Surface 2 P0 — chat settings + activate route regression guards.

HTTP-level coverage for `core/routes/chat.py`:
  - PUT /api/chats/{name}/settings
  - POST /api/chats/{name}/activate

R5/R6 already cover `_apply_chat_settings` directly; these assert the HTTP
surface does the wiring: PUT/activate → apply path → scopes propagate,
events fire, response shape is right.

See tmp/coverage-test-plan.md Surface 2.
"""
import threading
from unittest.mock import MagicMock

import pytest


# ─── helpers ─────────────────────────────────────────────────────────────────

def _make_fm():
    """Real FunctionManager with just the attributes _apply_chat_settings
    touches. Same pattern as test_regression_guards_scopes._make_mock_system."""
    from core.chat.function_manager import FunctionManager
    fm = FunctionManager.__new__(FunctionManager)
    fm._tools_lock = threading.Lock()
    fm.all_possible_tools = []
    fm._mode_filters = {}
    fm.current_toolset_name = "none"
    fm.function_modules = {}
    fm._enabled_tools = []
    # Response-shape helpers that the PUT handler calls
    fm.get_current_toolset_info = MagicMock(return_value={'name': 'none'})
    fm.get_enabled_function_names = MagicMock(return_value=[])
    return fm


@pytest.fixture
def chat_client(client, mock_system, scope_snapshot, monkeypatch):
    """TestClient + mock_system wired for chat settings/activate tests.

    Puts a real FunctionManager on mock_system so _apply_chat_settings
    actually runs apply_scopes_from_settings.

    Captures post-apply scope state into a dict the test can inspect.
    FastAPI routes run in an async task whose ContextVars don't leak back
    to the test thread — observing SCOPE_REGISTRY directly after the call
    would see the test-thread snapshot, not the route's. The spy wraps
    apply_scopes_from_settings inside the route's context and captures.
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

    captured = {}
    orig_apply = fm_mod.apply_scopes_from_settings

    def spy_apply(fm_, settings_):
        orig_apply(fm_, settings_)
        captured.clear()
        for n, r in list(fm_mod.SCOPE_REGISTRY.items()):
            captured[n] = r['var'].get()

    monkeypatch.setattr(fm_mod, 'apply_scopes_from_settings', spy_apply)

    # Spy set_rag_scope on the FM (called by _apply_chat_settings after apply)
    def spy_set_rag(val):
        captured['rag'] = val
    fm.set_rag_scope = spy_set_rag

    return c, csrf, mock_system, fm, captured


# ─── 2.2 All scopes propagate through PUT /api/chats/{name}/settings ─────────

def test_put_chat_settings_propagates_every_scope_setting(chat_client):
    """[REGRESSION_GUARD] PUT with every `<key>_scope` in SCOPE_REGISTRY must
    update the corresponding ContextVar. Guards against a setting key being
    added without being registered, or registry-iteration breaking."""
    from core.chat.function_manager import SCOPE_REGISTRY
    c, csrf, mock_system, fm, captured = chat_client

    # Build settings payload with a unique value per known scope
    payload_settings = {}
    expected = {}
    for name, reg in list(SCOPE_REGISTRY.items()):
        setting_key = reg.get('setting')
        if not setting_key:
            continue
        if reg['default'] is False or reg['default'] is True:
            payload_settings[setting_key] = True
            expected[name] = True
        else:
            val = f"scope_test_{name}"
            payload_settings[setting_key] = val
            expected[name] = val

    mock_system.llm_chat.session_manager.get_chat_settings.return_value = payload_settings

    r = c.put(
        '/api/chats/trinity/settings',
        headers={'X-CSRF-Token': csrf},
        json={'settings': payload_settings},
    )
    assert r.status_code == 200, r.text

    for name, want in expected.items():
        got = captured.get(name)
        assert got == want, f"scope '{name}' not propagated: want {want!r} got {got!r}"


# ─── 2.4 / 2.5 Activate loads chat settings + aligns rag scope ───────────────

def test_activate_chat_applies_stored_settings_and_aligns_rag(chat_client):
    """[REGRESSION_GUARD] POST /api/chats/{name}/activate must apply the
    chat's stored settings AND set scope_rag to `__rag__:{chat_name}`.
    Before the 2026-04-18 fix, activation without sending a message left
    scope_rag pointing at the previous chat's docs.

    Since 2026-08-22 the apply rides the store's on_switched hook inside
    switch_chat (SWITCH MEANS APPLY — the route no longer applies itself).
    switch_chat is a mock here, so this emulates the store firing the hook
    with the landing chat's snapshot; tests/test_chat_switch_applies.py
    covers the real store firing it.
    """
    from core import api_fastapi
    c, csrf, mock_system, fm, captured = chat_client

    stored = {'memory_scope': 'alpha_mem', 'knowledge_scope': 'alpha_know'}
    sm = mock_system.llm_chat.session_manager
    sm.get_chat_settings.return_value = stored
    sm.get_active_chat_name.return_value = 'alpha'
    sm._switch_gen = 1
    sm.on_switched = lambda n, s_, g: api_fastapi.apply_on_switch(mock_system, n, s_, g)
    mock_system.llm_chat.switch_chat.side_effect = \
        lambda name: (sm.on_switched(name, dict(stored), 1), True)[1]

    r = c.post('/api/chats/alpha/activate', headers={'X-CSRF-Token': csrf})
    assert r.status_code == 200, r.text
    assert r.json()['active_chat'] == 'alpha'

    assert captured.get('memory') == 'alpha_mem'
    assert captured.get('knowledge') == 'alpha_know'
    assert captured.get('rag') == '__rag__:alpha'


def test_activate_unknown_chat_returns_400(chat_client):
    """switch_chat returns False → 400 (not 500)."""
    c, csrf, mock_system, fm, captured = chat_client
    mock_system.llm_chat.switch_chat.return_value = False

    r = c.post('/api/chats/nonexistent/activate', headers={'X-CSRF-Token': csrf})
    assert r.status_code == 400


# ─── 2.14 PUT response includes toolset / functions / state_tools ────────────

def test_put_chat_settings_response_includes_toolset_state(chat_client):
    """[PROACTIVE] Response must include toolset + functions + state_tools so
    the frontend can update the sidebar sync without a second round-trip."""
    c, csrf, mock_system, fm, captured = chat_client
    fm.get_current_toolset_info.return_value = {'name': 'coder', 'id': 'coder'}
    fm.get_enabled_function_names.return_value = ['read_file', 'write_file']

    r = c.put(
        '/api/chats/trinity/settings',
        headers={'X-CSRF-Token': csrf},
        json={'settings': {}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body['status'] == 'success'
    assert body['toolset'] == {'name': 'coder', 'id': 'coder'}
    assert body['functions'] == ['read_file', 'write_file']
    assert 'state_tools' in body


# ─── 2.15 PUT to inactive chat writes by name, never touches the live chat ───

def test_put_chat_settings_on_inactive_chat_writes_by_name_without_live_apply(chat_client):
    """[CONTRACT — replaces the pre-2026-08-02 400 guard] A PUT for a chat
    that is NOT active writes that chat's settings BY NAME — the Twilio
    daemon and mode-tagged game/story sessions are load-bearing on this —
    and must NEVER touch the live chat's runtime: no settings apply, no
    scope apply, no toolset sync. The old 400 was never the point;
    CONTAINMENT is (a call naming a session must not restamp the live
    chat's brain — finding 1.1's bug class, asserted at the core layer)."""
    from unittest.mock import MagicMock
    c, csrf, mock_system, fm, captured = chat_client
    sm = mock_system.llm_chat.session_manager
    sm.get_active_chat_name.return_value = 'trinity'
    sm.set_named_chat_settings.return_value = True
    fm.update_enabled_functions = MagicMock()

    r = c.put(
        '/api/chats/other_chat/settings',
        headers={'X-CSRF-Token': csrf},
        json={'settings': {'memory_scope': 'x'}},
    )
    assert r.status_code == 200
    body = r.json()
    # 1. The write landed in storage under the RIGHT name.
    sm.set_named_chat_settings.assert_called_once_with('other_chat', {'memory_scope': 'x'})
    # 2. The live legs did NOT fire.
    sm.update_chat_settings.assert_not_called()
    fm.update_enabled_functions.assert_not_called()
    assert captured == {}       # apply_scopes_from_settings / set_rag_scope never ran
    # 3. Response shape tells the UI not to repaint tool pills from a write
    #    that never touched the live runtime.
    assert body['toolset'] is None
    assert body['functions'] == []
    assert body['state_tools'] == []


def test_put_chat_settings_missing_prompt_falls_back_with_real_content(chat_client):
    """[REGRESSION_GUARD — post-fix review 2026-08-05] A chat whose configured
    prompt doesn't resolve must run on the assembled DEFAULT this turn.
    'default' is the assembled-mode sentinel, not a prompt name: the first
    version of this fallback called get_prompt('default'), got None, and was
    dead code — she silently kept the PREVIOUS chat's prompt. Assert the
    fallback applies NON-EMPTY content, and that the chat's own setting is
    never rewritten (the original Sapph-not-Rose data-loss mechanism)."""
    from core import prompts
    c, csrf, mock_system, fm, captured = chat_client
    sm = mock_system.llm_chat.session_manager
    payload = {'prompt': 'no-such-prompt-xyz'}
    sm.get_chat_settings.return_value = payload
    prev = prompts.get_active_preset_name()
    try:
        r = c.put(
            '/api/chats/trinity/settings',
            headers={'X-CSRF-Token': csrf},
            json={'settings': payload},
        )
        assert r.status_code == 200
        mock_system.llm_chat.set_system_prompt.assert_called()
        content = mock_system.llm_chat.set_system_prompt.call_args[0][0]
        assert isinstance(content, str) and content.strip(), \
            "fallback must apply non-empty assembled-default content"
        # The runtime fell back; the SETTING stays the user's.
        for call in sm.update_chat_settings.call_args_list:
            args = call[0]
            if args and isinstance(args[0], dict):
                assert args[0].get('prompt') != 'default', \
                    "missing-prompt fallback must never persist 'default'"
    finally:
        prompts.set_active_preset_name(prev)


def test_put_chat_settings_on_unknown_chat_returns_404(chat_client):
    """The negative case the old 400 test actually protected: an unknown
    chat name refuses instead of silently succeeding."""
    c, csrf, mock_system, fm, captured = chat_client
    sm = mock_system.llm_chat.session_manager
    sm.get_active_chat_name.return_value = 'trinity'
    sm.set_named_chat_settings.return_value = False

    r = c.put(
        '/api/chats/no_such_chat/settings',
        headers={'X-CSRF-Token': csrf},
        json={'settings': {'memory_scope': 'x'}},
    )
    assert r.status_code == 404


def test_put_chat_settings_missing_settings_key_returns_400(chat_client):
    """PUT body without 'settings' key → 400."""
    c, csrf, mock_system, fm, captured = chat_client
    r = c.put(
        '/api/chats/trinity/settings',
        headers={'X-CSRF-Token': csrf},
        json={},
    )
    assert r.status_code == 400


# ─── 2.17 PUT publishes CHAT_SETTINGS_CHANGED with origin ────────────────────

def test_put_chat_settings_publishes_event_with_origin(chat_client, event_bus_capture):
    """[PROACTIVE] Event carries the X-Session-ID origin so the UI can avoid
    reacting to its own writes (feedback-loop guard)."""
    c, csrf, mock_system, fm, captured = chat_client

    r = c.put(
        '/api/chats/trinity/settings',
        headers={'X-CSRF-Token': csrf, 'X-Session-ID': 'sess-42'},
        json={'settings': {'memory_scope': 'x'}},
    )
    assert r.status_code == 200

    events = [d for ev, d in event_bus_capture.events if ev == 'chat_settings_changed']
    assert events, f"no chat_settings_changed event fired; saw: {[ev for ev,_ in event_bus_capture.events]}"
    assert events[-1]['origin'] == 'sess-42'
    assert events[-1]['chat'] == 'trinity'


# ─── 2.3 Unmentioned scopes reset to default via HTTP ────────────────────────

def test_put_chat_settings_resets_unmentioned_scopes(chat_client):
    """[REGRESSION_GUARD] Settings payload with only ONE scope key must not
    leak prior values for the OTHER scopes — _apply_chat_settings calls
    reset_scopes() before apply. This guards that behavior through the HTTP
    layer (R6 guards it at the function level).

    We can't pre-seed ContextVars from the test thread (they don't leak into
    the route's async context). Instead we verify the captured post-apply
    snapshot: only 'memory' should have the new value; everything else
    (except rag, which aligns to chat name) should equal its default.
    """
    from core.chat.function_manager import SCOPE_REGISTRY
    c, csrf, mock_system, fm, captured = chat_client

    new_settings = {'memory_scope': 'clean_value'}
    mock_system.llm_chat.session_manager.get_chat_settings.return_value = new_settings

    r = c.put(
        '/api/chats/trinity/settings',
        headers={'X-CSRF-Token': csrf},
        json={'settings': new_settings},
    )
    assert r.status_code == 200

    assert captured.get('memory') == 'clean_value'
    for name, reg in list(SCOPE_REGISTRY.items()):
        if name in ('memory', 'rag'):
            continue
        got = captured.get(name)
        assert got == reg['default'], \
            f"scope '{name}' leaked across PUT: want default {reg['default']!r} got {got!r}"
