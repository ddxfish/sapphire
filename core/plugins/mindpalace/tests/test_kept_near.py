"""Isolation hunt E1 in the palace (2026-11-26). Text a private turn's privacy
held back from an embedder that is off this machine must not be sent to it
later by a job that runs outside any turn.

Two marks decide, and neither needs a turn:
  - the row's own stamp: `embedding_provider = KEPT_NEAR`, written at a save
    or edit whose embed was held back (core/embeddings);
  - the palace's private-model mark (`wr_private`, or filed under a private
    entity), which already rides into day rows, merges and promotes. Those
    rows are born with no vector and rely on the sweep.
While the embedder is far the sweep and every rewrite leave both alone. A
local or LAN embedder embeds them. Nothing here writes to an existing row
except to embed it.

Package-path imports on purpose (never re-import a plugin module in a test).
"""
import json

import numpy as np
import pytest

import core.chat.function_manager as fm
import core.embeddings as emb
from core.chat.function_manager import scope_private
from core.chat.llm_providers import resolve
from plugins.mindpalace.tools import palace_tools as pt
from plugins.mindpalace.tools import import_tools, self_tools

LOCAL, CLOUD = 'qwen-box', 'claude'
PRIVATE = "PRIVATE-WORDS said in a private chat"
PUBLIC = "ordinary words from an ordinary chat"


class Embedder:
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


@pytest.fixture(autouse=True)
def _clean_turn():
    fm.tool_context.set(None)
    yield
    fm.tool_context.set(None)


@pytest.fixture
def palace(tmp_path, monkeypatch):
    e = Embedder(far=True)
    monkeypatch.setattr(pt, "_db_path", tmp_path / "mind.db", raising=False)
    monkeypatch.setattr(pt, "_db_initialized", False, raising=False)
    monkeypatch.setattr(pt, "_backfill_done", False, raising=False)
    monkeypatch.setattr(pt, "_name_seen", None, raising=False)
    monkeypatch.setattr(pt, "_get_embedder", lambda: e, raising=False)
    monkeypatch.setattr(pt, "_instance_name", lambda: 'Sapphire-Purple')
    monkeypatch.setattr(pt, "_private_filter_on", lambda: True)
    monkeypatch.setattr(resolve, "is_local", lambda key, conf=None: key == LOCAL)
    return e


class private_turn:
    """A turn in a private chat, written by the local model."""
    def __enter__(self):
        fm.tool_context.set({'provider': LOCAL, 'model': 'm', 'chat': 'c'})
        self.token = scope_private.set(True)

    def __exit__(self, *a):
        scope_private.reset(self.token)
        fm.tool_context.set(None)


def turn(provider):
    fm.tool_context.set({'provider': provider, 'model': 'm', 'chat': 'c'} if provider else None)


def _save(text, **kw):
    msg, ok = pt._save_memory(text, 's', **kw)
    assert ok, msg
    return int(msg.split('ID: ')[1].split(',')[0])


def _stamp(cid):
    with pt._get_connection() as conn:
        return tuple(conn.execute('SELECT embedding, embedding_provider, embedding_dim, wr_private '
                                  'FROM chunks WHERE id = ?', (cid,)).fetchone())


def _sweep():
    pt._backfill_done = False
    pt._backfill_embeddings()                # no turn: any search starts this


# ── a save in a private chat ──

def test_a_private_chats_memory_is_stamped_kept_near(palace):
    with private_turn():
        cid = _save(PRIVATE)
    assert _stamp(cid)[:3] == (None, emb.KEPT_NEAR, None)
    assert palace.sent == []


def test_the_sweep_leaves_it_alone_and_embeds_its_neighbours(palace, monkeypatch):
    with private_turn():
        kept = _save(PRIVATE)
    turn(CLOUD)
    monkeypatch.setattr(palace, 'available', False)        # the embedder was down for this save
    plain = _save(PUBLIC)
    monkeypatch.setattr(palace, 'available', True)
    turn(None)
    assert _stamp(plain)[:3] == (None, None, None)
    _sweep()
    assert palace.sent == [PUBLIC], "the private row's text went to the far embedder"
    assert _stamp(kept)[:3] == (None, emb.KEPT_NEAR, None)
    assert _stamp(plain)[1] == 'test:remote'


def test_a_near_embedder_embeds_it_and_replaces_the_stamp(palace):
    with private_turn():
        kept = _save(PRIVATE)
    palace.is_far = False
    _sweep()
    assert palace.sent == [PRIVATE]
    blob, provider, dim, _ = _stamp(kept)
    assert blob is not None and (provider, dim) == ('test:remote', 3)


def test_an_ordinary_save_is_embedded_as_ever(palace):
    turn(CLOUD)
    cid = _save(PUBLIC)
    assert palace.sent == [PUBLIC] and _stamp(cid)[1] == 'test:remote'


def test_a_local_models_save_in_an_ordinary_chat_is_embedded_as_ever(palace):
    """The turn decides at save time: an ordinary chat is not held back."""
    turn(LOCAL)
    cid = _save(PUBLIC)
    assert palace.sent == [PUBLIC]
    assert _stamp(cid)[1] == 'test:remote' and _stamp(cid)[3] == 1


# ── rows born without a vector: only the sweep ever embeds them ──
# The mark that decides is "written in a private chat" (`meta.chat ==
# '__private__'`, there since vaulted chats), never the private-MODEL mark:
# a local model in an ordinary chat is embedded at save, so skipping its
# rows later protected nothing and cost them their vectors (bug hunt
# 2026-11-26, Fable scout).

def _bare_row(text, wr_private=False, private_chat=False, provider=None, vec=None):
    """As a day row, a merge or an import lands: no vector, the writer's mark."""
    meta = {'chat': '__private__'} if private_chat else {'chat': 'desk'}
    with pt._get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO chunks (layer, scope, content, created, updated, wr_private, meta, "
            "embedding, embedding_provider, embedding_dim) "
            "VALUES ('events', 's', ?, ?, ?, ?, ?, ?, ?, ?)",
            (text, pt._now(), pt._now(), int(wr_private), json.dumps(meta),
             vec, provider, 3 if vec is not None else None))
        conn.commit()
        return cur.lastrowid


def test_the_sweep_does_not_send_a_private_chats_row_to_a_far_embedder(palace):
    pt._ensure_db()
    private = _bare_row(PRIVATE, private_chat=True)
    plain = _bare_row(PUBLIC)
    _sweep()
    assert palace.sent == [PUBLIC], "the private row's text went to the far embedder"
    assert _stamp(private)[:3] == (None, None, None), "left exactly as it was: nothing is written to it"
    assert _stamp(plain)[1] == 'test:remote'


def test_and_embeds_it_once_the_embedder_is_near(palace):
    pt._ensure_db()
    private = _bare_row(PRIVATE, private_chat=True)
    palace.is_far = False
    _sweep()
    assert palace.sent == [PRIVATE] and _stamp(private)[1] == 'test:remote'


def test_a_private_models_row_from_an_ordinary_chat_is_embedded_as_ever(palace):
    """THE regression: a local model in an ordinary chat. Its rows carry the
    private-model mark and nothing else, and they are not held back."""
    pt._ensure_db()
    row = _bare_row(PUBLIC, wr_private=True)
    with pt._get_connection() as conn:
        eid = conn.execute("INSERT INTO entities (scope, name, kind, created, updated, wr_private) "
                           "VALUES ('s', 'Someone', 'person', ?, ?, 1)", (pt._now(), pt._now())).lastrowid
        filed = conn.execute("INSERT INTO chunks (layer, scope, content, entity_id, created, updated, meta) "
                             "VALUES ('knowledge', 's', ?, ?, ?, ?, '{}')",
                             ('about someone', eid, pt._now(), pt._now())).lastrowid
        conn.commit()
    _sweep()
    assert sorted(palace.sent) == sorted([PUBLIC, 'about someone'])
    assert _stamp(row)[1] == 'test:remote' and _stamp(filed)[1] == 'test:remote'


def test_a_local_models_edit_in_an_ordinary_chat_keeps_the_row_embedded(palace):
    turn(LOCAL)
    cid = _save(PUBLIC)
    assert _stamp(cid)[3] == 1 and _stamp(cid)[1] == 'test:remote'
    palace.sent.clear()
    msg, ok = pt._update_memory(cid, 's', content=PUBLIC + ' (edited)')
    assert ok, msg
    assert palace.sent == [PUBLIC + ' (edited)']
    assert _stamp(cid)[0] is not None and _stamp(cid)[1] == 'test:remote', "the edit cost it its vector"


def test_a_row_embedded_while_the_embedder_was_near_is_not_sent_after_a_swap(palace):
    """Saved in a private chat under a local embedder: an ordinary stamp and
    a real vector. Then the person picks a far embedder. The re-embed walk
    must not send it (the stamp alone cannot say 'private')."""
    pt._ensure_db()
    old = np.ones(3, dtype=np.float32).tobytes()
    private = _bare_row(PRIVATE, private_chat=True, provider='local:old', vec=old)
    plain = _bare_row(PUBLIC, provider='local:old', vec=old)
    _sweep()
    assert palace.sent == [PUBLIC]
    assert _stamp(private)[:2] == (old, 'local:old'), "its old vector is left as it is"
    assert _stamp(plain)[1] == 'test:remote'


def test_what_the_librarian_makes_from_a_private_chats_row_is_marked_too(palace):
    """A merge or a promote is born with no vector. Made from a private
    chat's words, it carries that chat mark, so no sweep sends it."""
    from plugins.mindpalace.tools import librarian_tools as lt
    pt._ensure_db()
    src = _bare_row(PRIVATE, private_chat=True)
    plain_src = _bare_row(PUBLIC)
    from plugins.mindpalace.tools import metadata as md
    cols = 'id, layer, scope, content, entity_id, tier, label, favorite, importance, private_key, meta, created'
    with pt._get_connection() as conn:
        cur = conn.cursor()
        rows = {i: tuple(cur.execute(f'SELECT {cols} FROM chunks WHERE id = ?', (i,)).fetchone())
                for i in (src, plain_src)}
        made = lt._insert_derived(cur, pt, md, rows[src], PRIVATE + ' (merged)', 'events', 's')
        plain = lt._insert_derived(cur, pt, md, rows[plain_src], PUBLIC + ' (merged)', 'events', 's')
        conn.commit()
        marks = {r[0]: json.loads(r[1] or '{}').get('chat')
                 for r in conn.execute('SELECT id, meta FROM chunks WHERE id IN (?, ?)', (made, plain))}
    assert marks[made] == '__private__' and marks[plain] != '__private__'
    _sweep()
    assert PRIVATE + ' (merged)' not in palace.sent and PUBLIC + ' (merged)' in palace.sent


# ── rewrites ──

def test_an_edit_in_a_private_chat_drops_the_old_vector_and_says_why(palace):
    turn(CLOUD)
    cid = _save(PUBLIC)
    assert _stamp(cid)[0] is not None
    palace.sent.clear()
    with private_turn():
        msg, ok = pt._update_memory(cid, 's', content=PRIVATE)
    assert ok, msg
    assert _stamp(cid)[:3] == (None, emb.KEPT_NEAR, None), "a stale vector's stamp would send the new words out later"
    assert palace.sent == []


def test_an_ordinary_chats_edit_of_a_kept_row_embeds_its_new_words(palace):
    """The new words are that ordinary turn's own. Before, the stamp was
    sticky and the row stayed out of search by meaning for good."""
    with private_turn():
        cid = _save(PRIVATE)
    assert _stamp(cid)[1] == emb.KEPT_NEAR
    turn(LOCAL)                 # an ordinary chat; a cloud model may not edit a private-model row at all
    msg, ok = pt._update_memory(cid, 's', content=PUBLIC)
    assert ok, msg
    assert palace.sent == [PUBLIC]
    assert _stamp(cid)[0] is not None and _stamp(cid)[1] == 'test:remote'


def test_a_rewrite_outside_any_turn_does_not_send_a_kept_row(palace):
    """The librarian's passes rewrite a row's words (a date stamp, a revert)
    and re-embed it: not a kept row, not to a far embedder."""
    with private_turn():
        cid = _save(PRIVATE)
    with pt._get_connection() as conn:
        cur = conn.cursor()
        row = cur.execute('SELECT created, meta FROM chunks WHERE id = ?', (cid,)).fetchone()
        pt._rewrite_content(cur, cid, PRIVATE + ' (reworded)', row[0], None, 's')
        conn.commit()
    assert palace.sent == []
    assert _stamp(cid)[:3] == (None, emb.KEPT_NEAR, None)


def test_a_rewrite_outside_any_turn_does_not_send_a_private_chats_row(palace):
    pt._ensure_db()
    cid = _bare_row(PRIVATE, private_chat=True)
    with pt._get_connection() as conn:
        cur = conn.cursor()
        row = cur.execute('SELECT created, meta FROM chunks WHERE id = ?', (cid,)).fetchone()
        pt._rewrite_content(cur, cid, PRIVATE + ' (reworded)', row[0], json.loads(row[1]), 's')
        conn.commit()
    assert palace.sent == []


def test_a_rewrite_while_the_embedder_is_down_keeps_the_kept_stamp(palace, monkeypatch):
    """It wrote NULL over 'kept-near', and the next sweep sent the row out."""
    with private_turn():
        cid = _save(PRIVATE)
    monkeypatch.setattr(palace, 'available', False)
    with pt._get_connection() as conn:
        cur = conn.cursor()
        row = cur.execute('SELECT created, meta FROM chunks WHERE id = ?', (cid,)).fetchone()
        pt._rewrite_content(cur, cid, PRIVATE + ' (reworded)', row[0], json.loads(row[1]), 's')
        conn.commit()
    monkeypatch.setattr(palace, 'available', True)
    _sweep()
    assert palace.sent == []


# ── the self sheet and the importer ──

def test_a_self_sheet_write_in_a_private_chat_is_stamped(palace):
    with private_turn():
        out = self_tools.write_section('s', 'identity', PRIVATE)
    with pt._get_connection() as conn:
        rows = conn.execute("SELECT embedding, embedding_provider FROM chunks WHERE layer = 'self'").fetchall()
    assert rows, out
    assert all(tuple(r) == (None, emb.KEPT_NEAR) for r in rows)
    assert palace.sent == []
    _sweep()
    assert palace.sent == []


def test_an_import_carries_the_stamp(palace):
    assert import_tools._provenance(
        {'embedding': None, 'embedding_provider': emb.KEPT_NEAR, 'embedding_dim': None}) \
        == (None, emb.KEPT_NEAR, None)
    assert import_tools._provenance(
        {'embedding': None, 'embedding_provider': None, 'embedding_dim': None}) == (None, None, None)
