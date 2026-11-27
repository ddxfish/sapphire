"""A chat's documents are knowledge tabs in scope '__rag__:<chat>'. The
per-chat document routes check the chat; the general knowledge routes took any
scope, tab id or entry id. With the vault locked they listed, returned,
changed and deleted a sealed private chat's document text by name (bug hunt
2026-11-26, blind route scout).

One gate for all of them: reaching a chat's documents answers 404 unless that
chat can be read right now.
"""
import types

import pytest

import core.api_fastapi  # noqa: F401
from core.api_fastapi import app, get_system
from plugins.memory.tools import knowledge_tools as kb

SECRET = "PRIVATE-WORDS from a document attached to a private chat"


@pytest.fixture
def world(client, tmp_path, monkeypatch):
    """A sealed chat 'secret' and an ordinary chat 'desk', each with one
    document chunk, and an ordinary knowledge tab in scope 'default'."""
    monkeypatch.setattr(kb, '_db_path', tmp_path / 'knowledge.db')
    monkeypatch.setattr(kb, '_db_initialized', False)
    monkeypatch.setattr(kb, '_get_embedder', lambda: types.SimpleNamespace(available=False))
    monkeypatch.setattr(kb, '_backfill_done', True)
    readable = {'desk': {'private_chat': False}}
    sm = types.SimpleNamespace(read_chat_settings=lambda name: readable.get(name))
    system = types.SimpleNamespace(llm_chat=types.SimpleNamespace(session_manager=sm))
    app.dependency_overrides[get_system] = lambda: system
    ids = {}
    for scope, text in (('__rag__:secret', SECRET), ('__rag__:desk', 'an ordinary document'), ('default', 'a note')):
        tab = kb.create_tab('doc.txt', scope=scope, description='', tab_type='user')
        kb.add_entry(tab, text)
        with kb._get_connection() as conn:
            entry = conn.execute('SELECT id FROM knowledge_entries WHERE tab_id = ?', (tab,)).fetchone()[0]
        ids[scope] = (tab, entry)
    c, csrf = client
    yield types.SimpleNamespace(c=c, h={'X-CSRF-Token': csrf}, ids=ids, readable=readable)
    app.dependency_overrides.pop(get_system, None)


def _text(scope):
    with kb._get_connection() as conn:
        return [r[0] for r in conn.execute(
            'SELECT e.content FROM knowledge_entries e JOIN knowledge_tabs t ON e.tab_id = t.id '
            'WHERE t.scope = ?', (scope,))]


def test_a_sealed_chats_documents_are_not_listed_or_returned(world):
    tab, entry = world.ids['__rag__:secret']
    for url in ('/api/knowledge/tabs?scope=__rag__:secret',
                f'/api/knowledge/tabs/{tab}?scope=__rag__:secret',
                f'/api/knowledge/tabs/{tab}',                       # the tab id alone, no scope named
                f'/api/knowledge/tabs/{tab}/export?scope=__rag__:secret',
                '/api/knowledge/dedup?scope=__rag__:secret'):
        r = world.c.get(url)
        assert r.status_code == 404, (url, r.status_code, r.text[:200])
        assert SECRET not in r.text


def test_nor_changed_deleted_or_planted_in(world):
    tab, entry = world.ids['__rag__:secret']
    c, h = world.c, world.h
    assert c.put(f'/api/knowledge/entries/{entry}', json={'content': 'overwritten'}, headers=h).status_code == 404
    assert c.delete(f'/api/knowledge/entries/{entry}', headers=h).status_code == 404
    assert c.delete(f'/api/knowledge/tabs/{tab}', headers=h).status_code == 404
    assert c.post('/api/knowledge/tabs', json={'name': 'planted', 'scope': '__rag__:secret'}, headers=h).status_code == 404
    assert c.post(f'/api/knowledge/tabs/{tab}/entries', json={'content': 'planted'}, headers=h).status_code == 404
    assert _text('__rag__:secret') == [SECRET], "nothing of it was touched"


def test_an_ordinary_chats_documents_and_ordinary_knowledge_work_as_ever(world):
    tab, entry = world.ids['__rag__:desk']
    assert 'an ordinary document' in world.c.get(f'/api/knowledge/tabs/{tab}?scope=__rag__:desk').text
    note_tab, note = world.ids['default']
    assert world.c.get('/api/knowledge/tabs?scope=default').status_code == 200
    assert 'a note' in world.c.get(f'/api/knowledge/tabs/{note_tab}?scope=default').text
    r = world.c.put(f'/api/knowledge/entries/{note}', json={'content': 'a changed note'}, headers=world.h)
    assert r.status_code == 200, r.text
    assert _text('default') == ['a changed note']


def test_the_same_chat_reads_normally_once_it_can_be_read(world):
    tab, _ = world.ids['__rag__:secret']
    world.readable['secret'] = {'private_chat': True}              # the vault is open
    assert SECRET in world.c.get(f'/api/knowledge/tabs/{tab}?scope=__rag__:secret').text


def test_a_change_to_a_chats_documents_is_not_announced_while_it_is_sealed():
    """The bus drops an event naming a sealed chat; `mind_changed` names the
    chat inside its document scope."""
    from core import event_bus
    seen = []
    old = event_bus._hidden_chat_probe
    event_bus.set_hidden_chat_probe(lambda name: seen.append(name) or name == 'secret')
    try:
        assert event_bus._names_hidden_chat({'scope': '__rag__:secret'}) is True
        assert event_bus._names_hidden_chat({'scope': '__rag__:desk'}) is False
        assert event_bus._names_hidden_chat({'scope': 'default'}) is False
        assert 'default' not in seen, "an ordinary scope is not a chat name"
    finally:
        event_bus.set_hidden_chat_probe(old)
