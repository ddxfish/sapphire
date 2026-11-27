"""API tokens and private chats (Krem's ruling 2026-11-26, after the blind
route scout): a bearer token is an integration, not the person at their own
screen. It gets the locked-vault view: no vault prompt or piece text and no
private chat, even while the vault is open.
"""
import asyncio
import types

import pytest
from fastapi import HTTPException

import core.api_fastapi as af
import core.auth as auth
import core.prompt_vault as pv
from test_vault_isolation_hunt import world, SECRET   # noqa: F401  (fixture rides along)

BEARER = {'Authorization': 'Bearer test-token'}


class _SM:
    def __init__(self, active='desk'):
        self.chats = {'desk': {'private_chat': False}, 'bedroom': {'private_chat': True}}
        self.active = active

    def get_settings_for(self, name):
        return self.chats.get(name)

    def get_chat_settings(self):
        return self.chats.get(self.active)

    def list_chat_files(self, **kw):
        return [{'name': n, **s} for n, s in self.chats.items()]

    def search_chat_content(self, q):
        return {'desk': 2, 'bedroom': 5}


def _request(path, method='GET', headers=None, logged_in=False, query=None, params=None, body=None):
    async def json():
        return body
    return types.SimpleNamespace(
        session={'logged_in': True} if logged_in else {},
        headers={**(headers or {}), **({'content-type': 'application/json'} if body is not None else {})},
        url=types.SimpleNamespace(path=path), method=method, state=types.SimpleNamespace(), scope={'type': 'http'},
        query_params=query or {}, path_params=params or {}, json=json)


@pytest.fixture
def sm(monkeypatch):
    s = _SM()
    monkeypatch.setattr(af, 'get_system', lambda: types.SimpleNamespace(
        llm_chat=types.SimpleNamespace(session_manager=s)))
    return s


def _gate(req):
    return asyncio.run(auth.token_gate(req))


def _refused(req):
    with pytest.raises(HTTPException) as e:
        _gate(req)
    return e.value.status_code


# ── who is a token ──

def test_a_logged_in_browser_is_the_person_whatever_headers_it_sends():
    assert auth.token_caller(_request('/api/history', headers=BEARER, logged_in=True)) is False
    assert auth.token_caller(_request('/api/history', headers=BEARER)) is True
    assert auth.token_caller(_request('/api/history')) is False
    assert pv.person_reads(_request('/x', logged_in=True)) is True
    assert pv.person_reads(_request('/x', headers=BEARER)) is False
    assert pv.person_reads(None) is False


# ── a private chat named by a token ──

def test_a_token_naming_a_private_chat_gets_the_answer_for_a_chat_that_is_not_there(sm):
    assert _refused(_request('/api/chats/bedroom/export', headers=BEARER, params={'chat_name': 'bedroom'})) == 404
    assert _refused(_request('/api/history', headers=BEARER, query={'chat': 'bedroom'})) == 404
    assert _refused(_request('/api/chat/stream', 'POST', BEARER, body={'chat': 'bedroom', 'text': 'hi'})) == 404
    assert _refused(_request('/api/knowledge/tabs', headers=BEARER, query={'scope': '__rag__:bedroom'})) == 404


def test_a_token_naming_an_ordinary_chat_passes_even_while_a_private_chat_is_open(sm):
    """An integration set to its own ordinary chat keeps working while the
    person sits in a private chat, as a device does."""
    sm.active = 'bedroom'
    assert _gate(_request('/api/chat/stream', 'POST', BEARER, body={'chat': 'desk', 'text': 'hi'})) is None
    assert _gate(_request('/api/history', headers=BEARER, query={'chat': 'desk'})) is None


# ── the chat that is open ──

def test_a_token_acting_on_the_open_chat_is_refused_while_that_chat_is_private(sm):
    sm.active = 'bedroom'
    for path in ('/api/history', '/api/chat', '/api/chat/stream', '/api/status', '/api/init',
                 '/api/history/raw', '/api/chats/active', '/api/commands/run'):
        assert _refused(_request(path, headers=BEARER)) == 403, path
    assert _gate(_request('/api/chats', headers=BEARER)) is None, "the chat list is not the open chat (it is filtered)"
    assert _gate(_request('/api/health', headers=BEARER)) is None


def test_the_open_chat_is_a_tokens_to_use_while_it_is_ordinary(sm):
    assert _gate(_request('/api/history', headers=BEARER)) is None
    assert _gate(_request('/api/chat', 'POST', BEARER, body={'text': 'hi'})) is None


def test_the_person_at_the_browser_is_never_gated(sm):
    sm.active = 'bedroom'
    assert _gate(_request('/api/history', logged_in=True)) is None
    assert _gate(_request('/api/chats/bedroom/export', logged_in=True, params={'chat_name': 'bedroom'})) is None


def test_a_check_that_fails_refuses(sm, monkeypatch):
    def broken(name):
        raise RuntimeError("database is locked")
    monkeypatch.setattr(sm, 'get_settings_for', broken)
    assert _refused(_request('/api/history', headers=BEARER, query={'chat': 'desk'})) == 403


def test_the_mcp_door_keeps_its_own_rules(sm):
    sm.active = 'bedroom'
    assert _gate(_request('/mcp', 'POST', BEARER, body={'chat': 'bedroom'})) is None


# ── through the real app ──

def test_a_token_gets_the_locked_view_of_the_vault_and_the_person_gets_it_all(world, client):
    assert pv.set_monolith('private-persona', SECRET)[0]
    c, _ = client
    assert SECRET in c.get('/api/prompts/components').text, "setup: the person's screen reads the vault"
    r = c.get('/api/prompts/components', headers=BEARER)
    assert r.status_code == 200 and SECRET not in r.text
    assert 'tender' not in r.json()['components'].get('emotions', {})
    assert SECRET not in c.get('/api/prompts/private-persona', headers=BEARER).text
    assert SECRET not in c.get('/api/prompts', headers=BEARER).text


def test_a_token_is_not_shown_private_chats_in_the_list_or_the_search():
    """The gate marks the request; the two routes that hand out every chat
    leave private ones out for a marked request."""
    import inspect
    import core.routes.chat as routes
    listing = inspect.getsource(routes.list_chats)
    assert "getattr(request.state, 'api_token', False)" in listing
    assert "if not c.get('private_chat')" in listing
    search = inspect.getsource(routes.search_chats)
    assert "getattr(request.state, 'api_token', False)" in search and 'name not in private' in search
    req = _request('/api/chats', headers=BEARER)
    _gate(req)
    assert req.state.api_token is True


def test_a_websocket_route_is_not_the_gates_business(sm):
    """The gate is app-wide, so it also runs for websocket routes. Those are
    session-cookie only; it must pass them untouched and never need a Request
    (five plugin-socket tests failed on exactly that)."""
    ws = types.SimpleNamespace(scope={'type': 'websocket'}, headers=BEARER, session={})
    assert _gate(ws) is None


# ── typing counts as being here (timing scout, 2026-11-26) ──

def test_typing_resets_the_idle_clock_for_the_person_and_never_for_a_token(client, monkeypatch):
    c, csrf = client
    touched = []
    monkeypatch.setattr(pv, 'vault_unlocked', lambda: True)
    monkeypatch.setattr(pv, 'touch', lambda: touched.append(1))
    assert c.post('/api/vault/touch', headers={'X-CSRF-Token': csrf}).status_code == 200
    assert touched == [1], "the person's keystrokes are activity"
    assert c.post('/api/vault/touch', headers={'X-CSRF-Token': csrf, **BEARER}).status_code == 200
    assert touched == [1], "an API token cannot hold the vault open"
    monkeypatch.setattr(pv, 'vault_unlocked', lambda: False)
    c.post('/api/vault/touch', headers={'X-CSRF-Token': csrf})
    assert touched == [1], "a locked vault has no clock to reset"


def test_the_composer_tells_the_vault_someone_is_typing():
    from pathlib import Path
    static = Path(__file__).parent.parent / "interfaces" / "web" / "static"
    send = (static / "handlers" / "send-handlers.js").read_text(encoding='utf-8')
    assert 'if (input.value) vaultTouch();' in send
    api = (static / "shared" / "vault-api.js").read_text(encoding='utf-8')
    assert "now - _lastTouch < 20000" in api and "post('/api/vault/touch')" in api
