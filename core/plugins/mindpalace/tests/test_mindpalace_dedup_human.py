"""Human Dedup (2026-07-26) — mechanical candidate scans + the human-only
entity fold. The librarian's dedup pass stays the AI's lane; this surface
shows the human every pair and lets them rule. Package-path imports on
purpose (see test_mindpalace_metadata's docstring).
"""
import json
import re

import numpy as np
import pytest

from plugins.mindpalace.tools import palace_tools as pt
from plugins.mindpalace.routes import browse


class _FakeEmbedder:
    provider_id = "fake"

    @property
    def available(self):
        return False

    def embed(self, texts, prefix="search_document"):
        return None


@pytest.fixture
def palace(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "_db_path", tmp_path / "mind.db", raising=False)
    monkeypatch.setattr(pt, "_db_initialized", False, raising=False)
    monkeypatch.setattr(pt, "_backfill_done", True, raising=False)
    monkeypatch.setattr(pt, "_get_embedder", lambda: _FakeEmbedder(), raising=False)
    return pt


def _save(content, scope='default', **kw):
    msg, ok = pt._save_memory(content, scope, **kw)
    assert ok, msg
    return int(re.search(r'ID: (\d+)', msg).group(1))


def _embed(cid, vec):
    v = np.asarray(vec, dtype=np.float32)
    v = v / np.linalg.norm(v)
    with pt._get_connection() as conn:
        conn.execute(
            'UPDATE chunks SET embedding = ?, embedding_provider = ?, '
            'embedding_dim = ? WHERE id = ?',
            (v.tobytes(), 'fake', len(vec), cid))
        conn.commit()


def _entity(name, scope='default', fields=None, kind=None, mentions=0):
    with pt._get_connection() as conn:
        cur = conn.cursor()
        eid = pt.upsert_entity(cur, name, scope, kind=kind)
        if fields:
            cur.execute('UPDATE entities SET meta = ? WHERE id = ?',
                        (json.dumps({'fields': fields}), eid))
        if mentions:
            cur.execute('UPDATE entities SET mentions = ? WHERE id = ?',
                        (mentions, eid))
        conn.commit()
    return eid


def _edge(src_id, dst_eid, kind='mentions'):
    with pt._get_connection() as conn:
        conn.execute(
            "INSERT INTO edges (src_type, src_id, dst_type, dst_id, kind, "
            "created) VALUES ('chunk', ?, 'entity', ?, ?, ?)",
            (src_id, dst_eid, kind, pt._now()))
        conn.commit()


def _fact(eid, content, headline=False, scope='default'):
    with pt._get_connection() as conn:
        cur = conn.cursor()
        now = pt._now()
        cur.execute(
            "INSERT INTO chunks (layer, scope, content, entity_id, tier, "
            "meta, created, updated) VALUES ('entities', ?, ?, ?, 1, ?, ?, ?)",
            (scope, content, eid,
             json.dumps({'headline': True}) if headline else None, now, now))
        conn.commit()
        return cur.lastrowid


def _ent_row(eid, cols='name, mentions, meta'):
    with pt._get_connection() as conn:
        return conn.execute(f'SELECT {cols} FROM entities WHERE id = ?',
                            (eid,)).fetchone()


# ─── dedup/candidates: memories & knowledge (cosine) ─────────────────────────

def test_memory_candidates_pair_by_cosine(palace):
    a = _save("the boat build hit a snag with the keel")
    b = _save("boat build snagged on the keel today")
    c = _save("completely unrelated grocery run")
    _embed(a, [1, 0])
    _embed(b, [0.999, 0.045])
    _embed(c, [0, 1])
    out = browse.dedup_candidates(query={'scope': 'default', 'what': 'memories'})
    assert out['scanned'] == 3
    pairs = [{p['a']['id'], p['b']['id']} for p in out['pairs']]
    assert {a, b} in pairs
    assert all(c not in p for p in pairs)
    assert out['pairs'][0]['sim'] >= out['threshold']


def test_memory_candidates_skip_superseded_show_favorites(palace):
    a = _save("twin one about the mast")
    b = _save("twin two about the mast")
    c = _save("twin three about the mast")
    for cid in (a, b, c):
        _embed(cid, [1, 0])
    with pt._get_connection() as conn:
        conn.execute("UPDATE chunks SET meta = json_object('superseded_at', "
                     "'2026-01-01') WHERE id = ?", (c,))
        conn.execute('UPDATE chunks SET favorite = 1 WHERE id = ?', (a,))
        conn.commit()
    out = browse.dedup_candidates(query={'scope': 'default', 'what': 'memories'})
    assert len(out['pairs']) == 1                       # superseded never pairs
    pair = out['pairs'][0]
    assert {pair['a']['id'], pair['b']['id']} == {a, b}
    fav = pair['a'] if pair['a']['id'] == a else pair['b']
    assert fav['favorite'] is True                      # flagged, never hidden


def _raw_chunk(content, layer, scope='default'):
    """Legacy-layer row (save_memory routes knowledge to the Library now —
    the scan covers the pre-Library chunks that still live in the layer)."""
    with pt._get_connection() as conn:
        cur = conn.cursor()
        now = pt._now()
        cur.execute("INSERT INTO chunks (layer, scope, content, created, "
                    "updated) VALUES (?, ?, ?, ?, ?)",
                    (layer, scope, content, now, now))
        conn.commit()
        return cur.lastrowid


def test_knowledge_candidates_scan_only_knowledge_layer(palace):
    a = _raw_chunk("python asyncio uses an event loop", 'knowledge')
    b = _raw_chunk("asyncio runs on an event loop", 'knowledge')
    e = _save("event loop chatter in the events layer")
    for cid in (a, b, e):
        _embed(cid, [1, 0])
    out = browse.dedup_candidates(query={'scope': 'default', 'what': 'knowledge'})
    assert out['scanned'] == 2                          # events chunk invisible
    assert [{out['pairs'][0]['a']['id'], out['pairs'][0]['b']['id']}] == [{a, b}]


def test_candidates_guards(palace):
    out, code = browse.dedup_candidates(query={'what': 'memories'})
    assert code == 400
    out, code = browse.dedup_candidates(query={'scope': 'default', 'what': 'sock'})
    assert code == 400


# ─── dedup/candidates: entities (name similarity) ────────────────────────────

def test_entity_candidates_fuzzy_containment_and_nickname(palace):
    _entity('Sky', mentions=3)
    _entity('Skye')
    _entity('Krem')
    _entity('The Boss', fields={'nicknames': 'krem'})
    _entity('Zebra')                                    # no lookalike
    out = browse.dedup_candidates(query={'scope': 'default', 'what': 'entities'})
    assert out['scanned'] == 5
    pairs = {frozenset((p['a']['name'], p['b']['name'])): p['sim']
             for p in out['pairs']}
    assert frozenset(('Sky', 'Skye')) in pairs          # the Sky/Skye class
    assert pairs[frozenset(('Krem', 'The Boss'))] == 1.0   # nickname hit
    assert not any('Zebra' in fs for fs in pairs)
    sky = next(p['a'] if p['a']['name'] == 'Sky' else p['b']
               for p in out['pairs'] if 'Sky' in (p['a']['name'], p['b']['name']))
    assert sky['mentions'] == 3                         # keeper-picking data


def test_entity_candidates_stay_in_scope(palace):
    _entity('Sky')
    _entity('Skye', scope='work')
    out = browse.dedup_candidates(query={'scope': 'default', 'what': 'entities'})
    assert out['pairs'] == []                           # cross-scope never pairs


# ─── entities/merge: the fold ────────────────────────────────────────────────

def test_merge_entities_full_fold(palace):
    sky = _entity('Sky', kind='person', mentions=3,
                  fields={'phone': '555-0100', 'nicknames': 'Blue'})
    skye = _entity('Skye', mentions=2,
                   fields={'phone': '555-9999', 'email': 'sky@example.test',
                           'nicknames': 'Skyebird'})
    m1 = _save("only mentions the duplicate")
    _edge(m1, skye)
    m2 = _save("mentions both spellings")
    _edge(m2, sky)
    _edge(m2, skye)
    _fact(sky, "Sky is the navigator.", headline=True)
    lose_head = _fact(skye, "Skye handles the charts.", headline=True)

    out = browse.merge_entities(body={'keeper': sky, 'loser': skye})
    assert out['success'] and out['name'] == 'Sky'

    with pt._get_connection() as conn:
        cur = conn.cursor()
        assert cur.execute('SELECT COUNT(*) FROM entities WHERE id = ?',
                           (skye,)).fetchone()[0] == 0          # loser gone
        edges = cur.execute(
            "SELECT src_id, COUNT(*) FROM edges WHERE dst_type = 'entity' "
            "AND dst_id = ? GROUP BY src_id", (sky,)).fetchall()
        assert dict(edges) == {m1: 1, m2: 1}            # repointed AND deduped
        assert cur.execute("SELECT COUNT(*) FROM edges WHERE dst_type = "
                           "'entity' AND dst_id = ?", (skye,)).fetchone()[0] == 0
        heads = cur.execute(
            "SELECT id FROM chunks WHERE entity_id = ? AND "
            "json_extract(meta, '$.headline') IS NOT NULL", (sky,)).fetchall()
        assert len(heads) == 1 and heads[0][0] != lose_head     # keeper's card line
        assert cur.execute('SELECT entity_id FROM chunks WHERE id = ?',
                           (lose_head,)).fetchone()[0] == sky   # fact survives
        led = cur.execute("SELECT summary FROM ledger WHERE summary LIKE "
                          "'%merged duplicate entity%'").fetchone()
        assert led and '"Skye" into "Sky"' in led[0]

    name, mentions, meta = _ent_row(sky)
    fields = json.loads(meta)['fields']
    assert mentions == 5                                # summed
    assert fields['phone'] == '555-0100'                # keeper wins conflicts
    assert fields['email'] == 'sky@example.test'        # blanks filled
    nicks = [n.strip() for n in fields['nicknames'].split(',')]
    assert nicks == ['Blue', 'Skye', 'Skyebird']        # alias leak closed


def test_merge_entities_keeper_without_headline_adopts_losers(palace):
    a = _entity('Anita')
    b = _entity('Anitaa')
    head = _fact(b, "Sister persona of Sapphire.", headline=True)
    out = browse.merge_entities(body={'keeper': a, 'loser': b})
    assert out['success']
    with pt._get_connection() as conn:
        row = conn.execute(
            "SELECT entity_id, json_extract(meta, '$.headline') FROM chunks "
            "WHERE id = ?", (head,)).fetchone()
    assert row == (a, 1)                                # flag survives the move


def test_merge_entities_guards(palace):
    a = _entity('Sky')
    b = _entity('Skye', scope='work')
    out, code = browse.merge_entities(body={'keeper': a, 'loser': a})
    assert code == 400
    out, code = browse.merge_entities(body={'keeper': a, 'loser': 999999})
    assert code == 404
    out, code = browse.merge_entities(body={'keeper': a, 'loser': b})
    assert code == 400                                  # cross-scope refused
    out, code = browse.merge_entities(body={})
    assert code == 400


# ─── maintenance: fold_promotion_clones (2026-07-26) ─────────────────────────
# Transition scaffolding, two clone classes (lineage proved by meta):
# identical → deleted; reworded → deleted AND the original re-queued for her
# sort pass (librarian_at cleared) — she re-judges them herself. Favorites
# always survive.

def _clone(src_id, content, scope='default', favorite=0, extra=None):
    """A migrated promotion clone: events + was_self_layer + derived_from."""
    meta = {'was_self_layer': True, 'derived_from': src_id}
    meta.update(extra or {})
    with pt._get_connection() as conn:
        cur = conn.cursor()
        now = pt._now()
        cur.execute("INSERT INTO chunks (layer, scope, content, favorite, "
                    "meta, created, updated) VALUES ('events', ?, ?, ?, ?, ?, ?)",
                    (scope, content, favorite, json.dumps(meta), now, now))
        conn.commit()
        return cur.lastrowid


def _patch_meta(cid, **kv):
    with pt._get_connection() as conn:
        raw = conn.execute('SELECT meta FROM chunks WHERE id = ?',
                           (cid,)).fetchone()[0]
        meta = json.loads(raw) if raw else {}
        meta.update(kv)
        conn.execute('UPDATE chunks SET meta = ? WHERE id = ?',
                     (json.dumps(meta), cid))
        conn.commit()


def test_fold_promotion_clones_two_classes(palace):
    src1 = _save("The boat build hit a snag with the keel.")
    twin = _clone(src1, "  the boat BUILD hit a snag with the keel. ")  # ws/case
    _patch_meta(src1, promoted_to=[twin], librarian_at='2026-07-20')
    _edge(twin, _entity('Krem'))
    src2 = _save("Krem clarified he is NOT my boss.")
    hers = _clone(src2, "Krem is NOT my boss — my space, my home.")     # reworded
    _patch_meta(src2, promoted_to=[hers], librarian_at='2026-07-20')
    src3 = _save("A pinned moment on the porch.")
    fav = _clone(src3, "A pinned moment on the porch.", favorite=1)     # favorite

    out = browse.maintenance(body={'action': 'fold_promotion_clones',
                                   'scope': 'default', 'confirm': 'default'})
    assert out['success']
    assert out['folded_identical'] == 1 and out['retired_reworded'] == 1
    assert out['requeued'] == 1 and out['kept_favorited'] == 1

    with pt._get_connection() as conn:
        cur = conn.cursor()
        left = {r[0] for r in cur.execute(
            'SELECT id FROM chunks WHERE scope = ?', ('default',)).fetchall()}
        assert twin not in left and hers not in left     # both clone classes gone
        assert {src1, src2, src3, fav} <= left           # originals + favorite stay
        assert cur.execute("SELECT COUNT(*) FROM edges WHERE src_type='chunk' "
                           "AND src_id = ?", (twin,)).fetchone()[0] == 0
        m1 = json.loads(cur.execute(
            'SELECT meta FROM chunks WHERE id = ?', (src1,)).fetchone()[0])
        assert 'promoted_to' not in m1                   # dangling pointer scrubbed
        assert m1.get('librarian_at')                    # identical: judgment kept
        m2 = json.loads(cur.execute(
            'SELECT meta FROM chunks WHERE id = ?', (src2,)).fetchone()[0])
        assert 'librarian_at' not in m2                  # reworded: re-queued
        led = cur.execute("SELECT summary FROM ledger WHERE summary LIKE "
                          "'%retired 1 reworded%'").fetchone()
        assert led and 're-queued' in led[0]

    # The re-queued original is back in her sort queue; the kept one is not.
    from plugins.mindpalace.tools import librarian
    with pt._get_connection() as conn:
        batch_ids = {b[0] for b in librarian.build_batch(
            conn.cursor(), 'default', 'all', 50)}
    assert src2 in batch_ids and src1 not in batch_ids

    # Idempotent: nothing left to fold.
    out2 = browse.maintenance(body={'action': 'fold_promotion_clones',
                                    'scope': 'default', 'confirm': 'default'})
    assert out2['folded_identical'] == 0 and out2['retired_reworded'] == 0


def test_fold_keeps_clone_when_original_is_retired(palace):
    src = _save("only live copy scenario")
    cl = _clone(src, "only live copy scenario")
    with pt._get_connection() as conn:
        conn.execute("UPDATE chunks SET meta = json_object('pruned_at', "
                     "'2026-01-01') WHERE id = ?", (src,))
        conn.commit()
    out = browse.maintenance(body={'action': 'fold_promotion_clones',
                                   'scope': 'default', 'confirm': 'default'})
    assert out['folded_identical'] == 0 and out['retired_reworded'] == 0
    with pt._get_connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM chunks WHERE id = ?',
                            (cl,)).fetchone()[0] == 1    # clone survives


def test_fold_requires_typed_confirm_and_tolerates_bad_lineage(palace):
    out, code = browse.maintenance(body={'action': 'fold_promotion_clones',
                                         'scope': 'default', 'confirm': 'nope'})
    assert code == 400
    src = _save("original entry")
    _clone(src, "original entry", extra={'derived_from': [1, 2]})  # list, not int
    _clone(999999, "orphan clone")                       # source doesn't exist
    out = browse.maintenance(body={'action': 'fold_promotion_clones',
                                   'scope': 'default', 'confirm': 'default'})
    assert out['success']                                # neither folds, no crash
    assert out['folded_identical'] == 0 and out['retired_reworded'] == 0


def test_merge_never_absorbs_permission_booleans(palace):
    """Lane-4 scout (2026-07-27): allow_call/allow_email are whitelist gates
    — 'unset' is indistinguishable from 'explicitly denied', so booleans
    never blank-fill across a merge. Text fields still do."""
    sky = _entity('Sky', kind='person', fields={'background': 'the navigator'})
    skye = _entity('Skye', fields={'allow_call': True, 'allow_email': True,
                                   'phone': '555-0100'})
    out = browse.merge_entities(body={'keeper': sky, 'loser': skye})
    assert out['success']
    fields = json.loads(_ent_row(sky)[2])['fields']
    assert 'allow_call' not in fields and 'allow_email' not in fields
    assert fields['phone'] == '555-0100'                # text absorbs
    with pt._get_connection() as conn:
        led = conn.execute("SELECT summary FROM ledger WHERE summary LIKE "
                           "'%fields absorbed%'").fetchone()
    assert led and 'phone' in led[0] and 'allow_call' not in led[0]


def test_import_lock_refuses_concurrent_run(palace):
    """Lane-1 scout (2026-07-27, reproduced): the idempotency snapshot is
    read-once — a second import racing the first doubled every row. The
    module lock turns the race into a clean refusal."""
    import threading
    from plugins.mindpalace.tools import import_tools as it
    held = threading.Lock()
    held.acquire()
    orig = it._import_lock
    it._import_lock = held
    try:
        msg = it._run_import('all', object())
        assert 'already running' in msg
    finally:
        it._import_lock = orig
        held.release()


def test_scan_skips_garbage_rows_and_errors_loudly(palace):
    """Lane-4/5 scouts (2026-07-27): per-row garbage (short blob, NULL dim)
    is skipped — the good pair still surfaces; a scan-level failure returns
    a 500, never an empty 'shelf looks clean'."""
    a = _save("twin one about the mast")
    b = _save("twin two about the mast")
    bad = _save("row with a torn embedding")
    nodim = _save("row with no dim stamp")
    _embed(a, [1, 0])
    _embed(b, [1, 0])
    with pt._get_connection() as conn:
        conn.execute("UPDATE chunks SET embedding = X'0102030405', "
                     "embedding_provider = 'fake', embedding_dim = 2 "
                     "WHERE id = ?", (bad,))     # 5 bytes: not float32-aligned
        conn.execute("UPDATE chunks SET embedding = X'01020304', "
                     "embedding_provider = 'fake', embedding_dim = NULL "
                     "WHERE id = ?", (nodim,))
        conn.commit()
    out = browse.dedup_candidates(query={'scope': 'default', 'what': 'memories'})
    assert 'error' not in out
    assert [{out['pairs'][0]['a']['id'], out['pairs'][0]['b']['id']}] == [{a, b}]

    import plugins.mindpalace.routes.browse as br
    orig = br._cosine_pairs
    br._cosine_pairs = lambda rows, thr: (_ for _ in ()).throw(MemoryError('boom'))
    try:
        out, code = browse.dedup_candidates(query={'scope': 'default',
                                                   'what': 'memories'})
        assert code == 500 and 'scan failed' in out['error']
    finally:
        br._cosine_pairs = orig
