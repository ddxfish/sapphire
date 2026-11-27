"""Isolation hunt E1 and the second half of E2 (2026-11-26, Krem's go).

A memory, a knowledge entry or a private chat's document saved in a PRIVATE
turn is held back from an embedder that is off this machine and its network.
It was then stored with no vector and looked exactly like a row saved while
the embedder was down, and every deferred job (the backfills, the re-embed
after a swap) sent such rows to whatever embedder was active, outside any
turn.

The fix: the row remembers. Its existing `embedding_provider` column is
stamped KEPT_NEAR, and every job leaves those rows alone while the embedder
is far. A local or LAN embedder embeds them and the stamp is replaced.

Temporary databases only (the classic memory and knowledge stores here; the
palace's own tests are in core/plugins/mindpalace/tests/test_kept_near.py).
"""
import types

import numpy as np
import pytest

import core.api_fastapi  # noqa: F401  (route modules trip circular imports alone)
import core.embeddings as emb
from core.chat.function_manager import scope_private
from plugins.memory.tools import knowledge_tools as kb
from plugins.memory.tools import memory_tools as mt

PRIVATE = "PRIVATE-WORDS said in a private chat"
PUBLIC = "ordinary words from an ordinary chat"


class Embedder:
    """A remote embedder as the real ones behave: it refuses a private turn's
    text when it is far, and keeps a list of every text it was sent."""
    provider_id = 'test:remote'
    dimension = 3
    available = True

    def __init__(self, far=True):
        self.is_far = far
        self.sent = []

    def embed(self, texts, prefix='search_document'):
        if emb.kept_near(self):
            return None
        self.sent += list(texts)
        return np.ones((len(texts), 3), dtype=np.float32)


class private_turn:
    def __enter__(self):
        self.token = scope_private.set(True)

    def __exit__(self, *a):
        scope_private.reset(self.token)


# ── the rule itself ──

def test_far_and_near():
    assert emb.far(Embedder(far=True)) and not emb.far(Embedder(far=False))
    assert not emb.far(emb.LocalEmbedder()) and not emb.far(emb.NullEmbedder())
    assert not emb.far(None)


def test_the_real_remote_embedder_is_far_only_off_the_network(monkeypatch):
    r = emb.RemoteEmbedder()
    monkeypatch.setattr(emb.config, 'EMBEDDING_API_URL', 'https://embed.example.com', raising=False)
    assert emb.far(r)
    for near in ('http://192.168.1.20:8080', 'http://localhost:8080', 'http://nas:8080'):
        monkeypatch.setattr(emb.config, 'EMBEDDING_API_URL', near, raising=False)
        assert not emb.far(r), near


def test_an_embedder_that_does_not_say_counts_as_far():
    """A plugin's embedder with nothing known about it: fail closed."""
    class Unknown:
        provider_id, available = 'x', True
    assert emb.far(Unknown())


def test_kept_near_is_a_private_turn_meeting_a_far_embedder():
    far, near = Embedder(True), Embedder(False)
    assert not emb.kept_near(far), "an ordinary turn is not held back"
    with private_turn():
        assert emb.kept_near(far) and not emb.kept_near(near)
        assert emb.kept_stamp(far) == emb.KEPT_NEAR and emb.kept_stamp(near) is None
    assert emb.kept_stamp(far) is None


def test_a_plugins_cloud_embedder_is_gated_like_the_built_in_ones():
    """Only the two built-in remote classes checked the private turn
    themselves; a plugin's cloud embedder was handed the text."""
    class PluginCloud:
        PROVIDER_ID = 'plug:cloud'
        provider_id = 'plug:cloud'
        dimension = 3
        available = True
        sent = []

        def embed(self, texts, prefix='search_document'):
            type(self).sent += list(texts)
            return np.full((len(texts), 3), 3 ** -0.5, dtype=np.float32)    # unit length: the canary checks

    reg = emb.embedding_registry
    reg.register_plugin('test_plug_cloud', PluginCloud, 'Plug cloud', 'test-plugin', is_local=False)
    try:
        inst = reg.create('test_plug_cloud')
        assert emb.far(inst)
        PluginCloud.sent.clear()                 # the registry's own canary line
        with private_turn():
            assert inst.embed([PRIVATE]) is None
        assert PluginCloud.sent == []
        assert inst.embed([PUBLIC]) is not None and PluginCloud.sent == [PUBLIC]
        assert inst.provider_id == 'plug:cloud' and inst.available
    finally:
        reg.unregister_plugin('test-plugin')


# ── the classic memory store ──

@pytest.fixture
def memory(tmp_path, monkeypatch):
    e = Embedder(far=True)
    monkeypatch.setattr(mt, '_db_path', tmp_path / 'memory.db')
    monkeypatch.setattr(mt, '_db_initialized', False)
    monkeypatch.setattr(mt, '_backfill_done', False)
    monkeypatch.setattr(mt, '_get_embedder', lambda: e)
    return e


def _mem(content):
    with mt._get_connection() as conn:
        return conn.execute('SELECT embedding, embedding_provider, embedding_dim FROM memories '
                            'WHERE content = ?', (content,)).fetchone()


def test_memory_saved_in_a_private_turn_is_stamped_kept_near(memory):
    with private_turn():
        msg, ok = mt._save_memory(PRIVATE)
    assert ok, msg
    assert tuple(_mem(PRIVATE)) == (None, emb.KEPT_NEAR, None)
    assert memory.sent == []


def test_the_memory_backfill_leaves_it_alone_and_embeds_its_neighbours(memory, monkeypatch):
    with private_turn():
        assert mt._save_memory(PRIVATE)[1]
    monkeypatch.setattr(memory, 'available', False)        # the embedder is down for this save
    assert mt._save_memory(PUBLIC)[1]
    monkeypatch.setattr(memory, 'available', True)
    assert tuple(_mem(PUBLIC)) == (None, None, None), "setup: a row that only lacks a vector"
    monkeypatch.setattr(mt, '_backfill_done', False)
    mt._backfill_embeddings()                               # no turn here: a search started it
    assert memory.sent == [PUBLIC], "the private row's text went to the far embedder"
    assert tuple(_mem(PRIVATE)) == (None, emb.KEPT_NEAR, None)
    assert _mem(PUBLIC)[1] == 'test:remote'


def test_a_near_embedder_embeds_the_kept_row_and_replaces_the_stamp(memory, monkeypatch):
    with private_turn():
        assert mt._save_memory(PRIVATE)[1]
    memory.is_far = False                                   # the person moved to a local embedder
    monkeypatch.setattr(mt, '_backfill_done', False)
    mt._backfill_embeddings()
    assert memory.sent == [PRIVATE]
    row = _mem(PRIVATE)
    assert row[0] is not None and row[1] == 'test:remote' and row[2] == 3


def test_an_ordinary_save_is_untouched(memory):
    assert mt._save_memory(PUBLIC)[1]
    assert memory.sent == [PUBLIC] and _mem(PUBLIC)[1] == 'test:remote'


# ── the knowledge store: entries and people ──

@pytest.fixture
def knowledge(tmp_path, monkeypatch):
    e = Embedder(far=True)
    monkeypatch.setattr(kb, '_db_path', tmp_path / 'knowledge.db')
    monkeypatch.setattr(kb, '_db_initialized', False)
    monkeypatch.setattr(kb, '_backfill_done', False)
    monkeypatch.setattr(kb, '_get_embedder', lambda: e)
    return e


def _entry(content):
    with kb._get_connection() as conn:
        return conn.execute('SELECT embedding, embedding_provider, embedding_dim FROM knowledge_entries '
                            'WHERE content = ?', (content,)).fetchone()


def _person(name):
    with kb._get_connection() as conn:
        return conn.execute('SELECT embedding, embedding_provider FROM people WHERE name = ?', (name,)).fetchone()


def test_knowledge_and_people_saved_in_a_private_turn_are_stamped_and_swept_around(knowledge, monkeypatch):
    tab = kb.create_tab('notes')
    tab_id = tab if isinstance(tab, int) else tab[0] if isinstance(tab, tuple) else tab['id']
    with private_turn():
        kb.add_entry(tab_id, PRIVATE)
        msg, ok = kb._save_person('Private Person', notes=PRIVATE)
        assert ok, msg
    assert tuple(_entry(PRIVATE)) == (None, emb.KEPT_NEAR, None)
    assert tuple(_person('Private Person')) == (None, emb.KEPT_NEAR)
    monkeypatch.setattr(knowledge, 'available', False)
    kb.add_entry(tab_id, PUBLIC)
    assert kb._save_person('Public Person', notes=PUBLIC)[1]
    monkeypatch.setattr(knowledge, 'available', True)
    monkeypatch.setattr(kb, '_backfill_done', False)
    kb._backfill_knowledge_embeddings()
    assert not any('PRIVATE' in t or 'Private Person' in t for t in knowledge.sent), knowledge.sent
    assert any(PUBLIC in t for t in knowledge.sent)
    assert tuple(_entry(PRIVATE)) == (None, emb.KEPT_NEAR, None)
    assert _entry(PUBLIC)[1] == 'test:remote' and _person('Public Person')[1] == 'test:remote'


def test_an_edit_in_a_private_turn_is_stamped_too(knowledge):
    tab = kb.create_tab('notes')
    tab_id = tab if isinstance(tab, int) else tab[0] if isinstance(tab, tuple) else tab['id']
    kb.add_entry(tab_id, PUBLIC)
    with kb._get_connection() as conn:
        eid = conn.execute('SELECT id FROM knowledge_entries WHERE content = ?', (PUBLIC,)).fetchone()[0]
    with private_turn():
        kb.update_entry(eid, PRIVATE)
    assert tuple(_entry(PRIVATE)) == (None, emb.KEPT_NEAR, None)
    assert PRIVATE not in knowledge.sent


# ── the re-embed job after an embedder swap ──

def test_the_reembed_job_and_its_count_leave_kept_rows_alone(memory, knowledge, monkeypatch):
    from core.embeddings import reembed
    tab = kb.create_tab('notes')
    tab_id = tab if isinstance(tab, int) else tab[0] if isinstance(tab, tuple) else tab['id']
    with private_turn():
        assert mt._save_memory(PRIVATE)[1]
        kb.add_entry(tab_id, PRIVATE)
        assert kb._save_person('Private Person', notes=PRIVATE)[1]
    assert mt._save_memory(PUBLIC)[1]
    kb.add_entry(tab_id, PUBLIC)
    new = Embedder(far=True)                                # the person swapped to another far embedder
    new.provider_id = 'test:other'
    counts = reembed._count_pending(new)
    assert counts == {'memories': 1, 'knowledge_entries': 1, 'people': 0}, counts
    monkeypatch.setattr(reembed, '_cancel_check', lambda: False)
    monkeypatch.setattr(reembed, '_bump_done', lambda *a, **k: None)
    monkeypatch.setattr(reembed, '_bump_error', lambda *a, **k: None)
    reembed._process_memories(new)
    reembed._process_knowledge_entries(new)
    reembed._process_people(new)
    assert sorted(new.sent) == [PUBLIC, PUBLIC], new.sent
    assert tuple(_mem(PRIVATE)) == (None, emb.KEPT_NEAR, None)
    near = Embedder(far=False)                              # ...and then to a local one
    near.provider_id = 'test:local'
    assert reembed._count_pending(near)['memories'] == 2
    reembed._process_memories(near)
    assert PRIVATE in near.sent and _mem(PRIVATE)[1] == 'test:local'


# ── a private chat's documents: the second door of E2 ──

def test_a_private_chats_document_stays_off_a_far_embedder_through_every_job(knowledge, monkeypatch, tmp_path):
    import core.documents as d
    import core.api_fastapi as af
    chats = {'secret': {'private_chat': True}, 'desk': {'private_chat': False}}
    sm = types.SimpleNamespace(get_settings_for=lambda name: chats.get(name), chat_exists=lambda n: n in chats)
    monkeypatch.setattr(af, 'get_system', lambda: types.SimpleNamespace(
        llm_chat=types.SimpleNamespace(session_manager=sm)))
    monkeypatch.setattr(d, '_dir', lambda: tmp_path / 'documents')
    monkeypatch.setattr(d, '_changed', lambda chat: None)
    monkeypatch.setattr(d, '_heal_later', lambda chat: None)
    monkeypatch.setattr(d, '_kept_near', {})
    d.add('secret', 'diary.txt', (PRIVATE + ' in a document. ').encode() * 5)
    d.add('desk', 'memo.txt', (PUBLIC + ' in a document. ').encode() * 5)
    assert knowledge.sent and not any('PRIVATE' in t for t in knowledge.sent), "upload"
    with kb._get_connection() as conn:
        stamps = {r[0] for r in conn.execute(
            "SELECT embedding_provider FROM knowledge_entries WHERE content LIKE '%PRIVATE%'")}
    assert stamps == {emb.KEPT_NEAR}
    knowledge.sent.clear()
    d._heal('secret')                                       # the documents' own repair thread
    monkeypatch.setattr(kb, '_backfill_done', False)
    kb._backfill_knowledge_embeddings()                     # the knowledge store's sweep (the second door)
    assert not any('PRIVATE' in t for t in knowledge.sent), knowledge.sent
