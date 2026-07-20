"""Librarian verbs (phase L1) — the blast shield, the charter, soft-everything.

Package-path imports on purpose (see test_mindpalace_metadata's docstring):
librarian_tools lazily imports plugins.mindpalace.tools.palace_tools, so the
tests must patch THAT instance, not a standalone copy.
"""
import json
import re
import sqlite3

import pytest

from plugins.mindpalace.tools import palace_tools as pt
from plugins.mindpalace.tools import librarian_tools as lt


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
    monkeypatch.setattr(pt, "_backfill_done", False, raising=False)
    monkeypatch.setattr(pt, "_get_embedder", lambda: _FakeEmbedder(), raising=False)
    # Rating tests exercise importance — force the alpha master ON (the
    # manifest default is off → mark_processed ignores ratings).
    monkeypatch.setattr(lt, "_importance_enabled", lambda: True)
    lt.close_pass()
    yield pt
    lt.close_pass()


def _save(content, scope='default', **kw):
    msg, ok = pt._save_memory(content, scope, **kw)
    assert ok, msg
    return int(re.search(r'ID: (\d+)', msg).group(1))


def _meta(cid):
    with pt._get_connection() as conn:
        raw = conn.execute('SELECT meta FROM chunks WHERE id = ?', (cid,)).fetchone()[0]
    return json.loads(raw) if raw else {}


def _run(fn, args):
    return lt.execute(fn, args, None)


def test_no_open_pass_refuses(palace):
    cid = _save("a stray thought")
    msg, ok = _run('prune_memory', {'memory_id': cid})
    assert not ok and 'pass' in msg.lower()


def test_id_outside_pass_refused(palace):
    cid = _save("inside")
    outside = _save("outside")
    lt.open_pass('default', [cid])
    msg, ok = _run('mark_processed', {'memory_id': outside})
    assert not ok and 'not in the current pass' in msg


def test_scope_mismatch_refused(palace):
    cid = _save("wrong scope row", scope='work')
    lt.open_pass('default', [cid])   # engine bug simulation: id from another scope
    msg, ok = _run('mark_processed', {'memory_id': cid})
    assert not ok and 'scope' in msg.lower()


def test_prune_hides_from_ai_reads_but_keeps_row(palace):
    cid = _save("the pizza logistics of tuesday")
    assert f"[{cid}]" in pt._search_memory("pizza logistics", 'default')[0]
    lt.open_pass('default', [cid])
    msg, ok = _run('prune_memory', {'memory_id': cid, 'reason': 'trivia, faded'})
    assert ok
    assert f"[{cid}]" not in pt._search_memory("pizza logistics", 'default')[0]
    with pt._get_connection() as conn:   # row still there — reversible
        assert conn.execute('SELECT COUNT(*) FROM chunks WHERE id = ?', (cid,)).fetchone()[0] == 1
    m = _meta(cid)
    assert m['pruned_at'] and m['pruned_reason'] == 'trivia, faded' and m['librarian_at']


def test_prune_refuses_favorites_the_hare_stays(palace):
    cid = _save("the hare", favorite=True)
    lt.open_pass('default', [cid])
    msg, ok = _run('prune_memory', {'memory_id': cid})
    assert not ok and 'stays' in msg


def test_mark_favorite_is_a_one_way_ratchet(palace, monkeypatch):
    # Favorite works even with the importance alpha OFF — it's the flag
    # mechanism, independent of ratings (Krem 2026-07-16: only lib has
    # fav=true; save_memory dropped the param).
    # 2026-07-19 REVERSAL of the two-way design: humans now hand-set
    # favorites as merge/prune shields, so the librarian may GRANT ★ but
    # never clear one — un-favoriting is human/UI-only.
    monkeypatch.setattr(lt, "_importance_enabled", lambda: False)
    cid = _save("the night she chose trust over intensity")
    lt.open_pass('default', [cid])
    msg, ok = _run('mark_processed', {'memory_id': cid, 'favorite': True})
    assert ok and 'favorite ★' in msg
    with pt._get_connection() as conn:
        fav, imp = conn.execute(
            'SELECT favorite, importance FROM chunks WHERE id = ?',
            (cid,)).fetchone()
    assert fav == 1 and imp == 0.95
    # The bird's-eye view can NOT take it back — false is a filed no-op:
    # the memory still stamps as reviewed, the shield never moves.
    lt.open_pass('default', [cid])
    msg, ok = _run('mark_processed', {'memory_id': cid, 'favorite': False})
    assert ok and 'granting only' in msg
    with pt._get_connection() as conn:
        fav, imp = conn.execute(
            'SELECT favorite, importance FROM chunks WHERE id = ?',
            (cid,)).fetchone()
    assert fav == 1 and imp == 0.95     # shield intact


def test_atomize_splits_hides_original_links_provenance(palace):
    cid = _save("Krem got a boat AND fixed the router AND hates mondays")
    lt.open_pass('default', [cid])
    msg, ok = _run('atomize_memory', {'memory_id': cid, 'parts': [
        "Krem got a boat", "Krem fixed the router", "Krem hates mondays"]})
    assert ok
    new_ids = [int(x) for x in re.findall(r'\d+', msg.split(':')[1])][:3]
    with pt._get_connection() as conn:
        orig_created = conn.execute('SELECT created FROM chunks WHERE id = ?', (cid,)).fetchone()[0]
        for nid in new_ids:
            row = conn.execute('SELECT created, meta FROM chunks WHERE id = ?', (nid,)).fetchone()
            assert row[0] == orig_created                      # parts inherit created
            assert json.loads(row[1])['derived_from'] == cid   # provenance meta
            edge = conn.execute(
                "SELECT 1 FROM edges WHERE src_type='chunk' AND src_id=? "
                "AND dst_type='chunk' AND dst_id=? AND kind='derived_from'",
                (nid, cid)).fetchone()
            assert edge                                        # provenance edge
    m = _meta(cid)
    assert m['pruned_at'] and m['atomized_into'] == new_ids
    assert "boat" in pt._search_memory("boat", 'default')[0]   # parts searchable
    assert lt.close_pass()['handled'] == 1


def test_atomize_needs_two_parts(palace):
    cid = _save("single concept")
    lt.open_pass('default', [cid])
    msg, ok = _run('atomize_memory', {'memory_id': cid, 'parts': ['just one']})
    assert not ok


def test_promote_copies_to_entity_original_stays(palace):
    cid = _save("Krem dreams of the ocean floor")
    lt.open_pass('default', [cid])
    msg, ok = _run('promote_memory', {'memory_id': cid, 'layer': 'entities',
                                      'entity': 'Krem'})
    assert ok
    new_id = int(re.search(r'→ \[(\d+)\]', msg).group(1))
    with pt._get_connection() as conn:
        layer, tier, eid = conn.execute(
            'SELECT layer, tier, entity_id FROM chunks WHERE id = ?', (new_id,)).fetchone()
        assert layer == 'entities' and tier == 2 and eid is not None
    assert _meta(cid).get('pruned_at') is None        # original NOT hidden
    assert _meta(cid)['promoted_to'] == [new_id]
    assert "ocean" in pt._search_memory("ocean floor", 'default')[0]  # both live


def test_promote_to_entities_requires_entity(palace):
    cid = _save("something")
    lt.open_pass('default', [cid])
    msg, ok = _run('promote_memory', {'memory_id': cid, 'layer': 'entities'})
    assert not ok


def test_promote_kind_categorizes_new_entity_only(palace):
    """kind= (Krem 2026-07-19, after the sort drain minted 9 'uncategorized'
    cards): a NEW entity takes the kind; an existing entity keeps its own;
    an unknown kind files unsorted instead of failing the verb."""
    cid = _save("Sudo is a 14 year old dog with calm energy")
    lt.open_pass('default', [cid])
    msg, ok = _run('promote_memory', {'memory_id': cid, 'layer': 'entities',
                                      'entity': 'Sudo', 'kind': 'person'})
    assert ok
    with pt._get_connection() as conn:
        assert conn.execute("SELECT kind FROM entities WHERE name='Sudo'").fetchone()[0] == 'person'
        # Existing entity keeps its kind — a later promote can't re-categorize.
        cid2 = _save("Sudo loves the lake in summer")
    lt.open_pass('default', [cid2])
    msg, ok = _run('promote_memory', {'memory_id': cid2, 'layer': 'entities',
                                      'entity': 'Sudo', 'kind': 'place'})
    assert ok
    with pt._get_connection() as conn:
        assert conn.execute("SELECT kind FROM entities WHERE name='Sudo'").fetchone()[0] == 'person'
    # Unknown kind → unsorted, verb still files.
    cid3 = _save("The garage is where the work happens")
    lt.open_pass('default', [cid3])
    msg, ok = _run('promote_memory', {'memory_id': cid3, 'layer': 'entities',
                                      'entity': 'Garage', 'kind': 'building'})
    assert ok
    with pt._get_connection() as conn:
        assert conn.execute("SELECT kind FROM entities WHERE name='Garage'").fetchone()[0] is None


def test_set_links_requires_link_pass_and_files_verdicts(palace):
    """The link pass's bulk verb (2026-07-16): existing entities only, every
    entry stamps link_at (the verdict drains the queue), unknown names are
    noted but still filed — requeueing won't invent the entity."""
    _save("a fact about someone", scope='default', layer='entities', entity='Zebra')
    a = _save("she did the thing with the wallet")
    b = _save("nothing to connect here")
    outside = _save("not presented")

    lt.open_pass('default', [a])                    # sort pass — wrong kind
    msg, ok = _run('set_links', {'entries': [{'memory_id': a, 'entities': ['Zebra']}]})
    assert not ok and 'link pass' in msg

    lt.open_pass('default', [a, b], kind='link')
    msg, ok = _run('set_links', {'entries': [
        {'memory_id': a, 'entities': ['zebra', 'Nobody']},   # NOCASE + unknown
        {'memory_id': b, 'entities': []},
        {'memory_id': outside, 'entities': ['Zebra']},
    ]})
    assert ok and '1 connected' in msg and '1 ruled connection-free' in msg
    assert "no entity named 'Nobody'" in msg and 'not in this pass' in msg
    with pt._get_connection() as conn:
        eid = conn.execute("SELECT id FROM entities WHERE name='Zebra'").fetchone()[0]
        assert conn.execute(
            "SELECT 1 FROM edges WHERE src_type='chunk' AND src_id=? "
            "AND dst_type='entity' AND dst_id=? AND kind='mentions'",
            (a, eid)).fetchone()
    assert _meta(a)['link_at'] and _meta(b)['link_at']
    assert 'librarian_at' not in _meta(a)           # linking is not review
    assert 'link_at' not in _meta(outside)
    assert lt.close_pass()['handled'] == 2


def test_link_batch_selects_candidates_and_drains(palace):
    from plugins.mindpalace.tools import librarian as eng
    cand = _save("coffee with someone downtown")
    plain = _save("an uneventful afternoon")
    with pt._get_connection() as conn:
        cur = conn.cursor()
        m = _meta(cand); m['noun_candidates'] = ['Marisol']
        cur.execute('UPDATE chunks SET meta = ? WHERE id = ?', (json.dumps(m), cand))
        m = _meta(plain); m.pop('noun_candidates', None)
        cur.execute('UPDATE chunks SET meta = ? WHERE id = ?', (json.dumps(m), plain))
        conn.commit()
        ids = [b[0] for b in eng.build_link_batch(cur, 'default', 20)]
    assert cand in ids and plain not in ids
    # The verdict drains the queue — connection-free counts too.
    lt.open_pass('default', [cand], kind='link')
    assert _run('set_links', {'entries': [{'memory_id': cand, 'entities': []}]})[1]
    lt.close_pass()
    with pt._get_connection() as conn:
        ids = [b[0] for b in eng.build_link_batch(conn.cursor(), 'default', 20)]
    assert cand not in ids


def test_present_link_carries_roster_and_bulk_instruction(palace):
    from plugins.mindpalace.tools import librarian as eng
    cid = _save("saw her at the market")
    with pt._get_connection() as conn:
        m = _meta(cid); m['noun_candidates'] = ['Marisol']
        conn.execute('UPDATE chunks SET meta = ? WHERE id = ?', (json.dumps(m), cid))
        conn.commit()
        batch = eng.build_link_batch(conn.cursor(), 'default', 10)
    text = eng._present_link(batch, 'default', ['Krem', 'Zebra'], 1, 1)
    assert f"[{cid}]" in text and 'Krem, Zebra' in text
    assert 'set_links' in text and 'Marisol' in text
    assert 'never creates' in text


def test_mark_processed_stamps_and_counts(palace):
    a, b = _save("fine as is"), _save("also fine")
    lt.open_pass('default', [a, b])
    assert _run('mark_processed', {'memory_id': a})[1]
    assert lt.pass_status() == {'open': True, 'scope': 'default', 'remaining': 1}
    assert _run('mark_processed', {'memory_id': b})[1]
    stats = lt.close_pass()
    # mark_processed now buffers a 'marked' child per call (2026-07-19).
    assert (stats['scope'], stats['presented'], stats['handled']) == ('default', 2, 2)
    assert [c['action'] for c in stats['ledger']] == ['marked', 'marked']
    assert _meta(a)['librarian_at']


def _importance(cid):
    with pt._get_connection() as conn:
        return conn.execute('SELECT importance FROM chunks WHERE id = ?',
                            (cid,)).fetchone()[0]


def test_mark_processed_rating_writes_importance(palace):
    """mark_processed(importance=) writes the column (spider pricing v2 fuel);
    omitted → NULL untouched; out-of-range clamps; garbage refuses."""
    a, b, c, d = (_save(x) for x in ("rate me", "leave me", "clamp me", "core me"))
    lt.open_pass('default', [a, b, c, d])
    msg, ok = _run('mark_processed', {'memory_id': a, 'importance': 0.8})
    assert ok and 'importance 0.8' in msg
    assert _importance(a) == pytest.approx(0.8)
    assert _run('mark_processed', {'memory_id': b})[1]
    assert _importance(b) is None                      # omitted stays unrated
    assert _run('mark_processed', {'memory_id': c, 'importance': 7})[1]
    assert _importance(c) == 1.0                       # clamped to [0, 1]
    msg, ok = _run('mark_processed', {'memory_id': d, 'importance': 'lots'})
    assert not ok and 'between 0 and 1' in msg
    assert _importance(d) is None                      # refused → untouched
    msg, ok = _run('mark_processed', {'memory_id': d, 'importance': 0.95})
    assert ok and 'core, never fades' in msg           # NEVER_FADES ack
    # A rated-core memory is now prune-proof — the charter picks it up.
    msg, ok = _run('prune_memory', {'memory_id': d})
    assert not ok and 'protected' in msg


# ─── merge_memories: the code-gated dedup verb (2026-07-11; dedup-pass-only
#     since the pass split, 2026-07-16) ─────────────────────────────────────

def _set_vec(cid, vec):
    """Stamp a normalized fake embedding onto a chunk."""
    import numpy as np
    v = np.asarray(vec, dtype=np.float32)
    v = v / np.linalg.norm(v)
    with pt._get_connection() as conn:
        conn.execute('UPDATE chunks SET embedding = ?, embedding_provider = ?, '
                     'embedding_dim = ? WHERE id = ?',
                     (v.tobytes(), 'fake', v.shape[0], cid))
        conn.commit()


def _set_created(cid, ts):
    with pt._get_connection() as conn:
        conn.execute('UPDATE chunks SET created = ? WHERE id = ?', (ts, cid))
        conn.commit()


def test_merge_folds_duplicates_earliest_date_provenance(palace):
    a = _save("Zebra visited the house today")
    b = _save("Zebra came by the house for a visit")
    _set_vec(a, [1, 0, 0]); _set_vec(b, [1, 0, 0])          # identical → sim 1.0
    _set_created(a, '2025-02-01T00:00:00+00:00')
    _set_created(b, '2025-03-01T00:00:00+00:00')
    lt.open_pass('default', [a, b], kind='dedup')
    msg, ok = _run('merge_memories', {'memory_ids': [a, b]})
    assert ok, msg
    new_id = int(re.search(r'→ \[(\d+)\]', msg).group(1))
    with pt._get_connection() as conn:
        row = conn.execute('SELECT content, created, meta FROM chunks WHERE id = ?',
                           (new_id,)).fetchone()
    assert row[0] == "Zebra came by the house for a visit"   # longest original
    assert row[1] == '2025-02-01T00:00:00+00:00'             # earliest kept
    assert json.loads(row[2])['derived_from'] == [a, b]
    for cid in (a, b):
        m = _meta(cid)
        assert m['pruned_reason'] == 'merged' and m['merged_into'] == new_id
    # Hidden from AI reads, like any pruned row.
    out, _ = pt._search_memory("Zebra visited house", 'default')
    assert f"[{a}]" not in out and f"[{b}]" not in out
    assert lt.close_pass()['handled'] == 2


def test_merge_refuses_below_threshold(palace):
    a = _save("Zebra visited the house")
    b = _save("bought a new soldering iron")
    _set_vec(a, [1, 0, 0]); _set_vec(b, [0, 1, 0])           # orthogonal → sim 0
    lt.open_pass('default', [a, b], kind='dedup')
    msg, ok = _run('merge_memories', {'memory_ids': [a, b]})
    assert not ok and 'threshold' in msg
    assert _meta(a).get('pruned_at') is None                 # untouched


def test_merge_refuses_missing_embedding_favorite_and_single(palace):
    a = _save("dup one"); b = _save("dup two")
    lt.open_pass('default', [a, b], kind='dedup')
    msg, ok = _run('merge_memories', {'memory_ids': [a, b]})
    assert not ok and 'embedding' in msg                     # no vectors yet
    _set_vec(a, [1, 0]); _set_vec(b, [1, 0])
    with pt._get_connection() as conn:
        conn.execute('UPDATE chunks SET favorite = 1 WHERE id = ?', (a,))
        conn.commit()
    msg, ok = _run('merge_memories', {'memory_ids': [a, b]})
    assert not ok and 'protected' in msg                     # the hare stays
    msg, ok = _run('merge_memories', {'memory_ids': [b]})
    assert not ok and 'at least 2' in msg


def test_merge_same_ids_twice_refused(palace):
    """The re-merge guard (Krem's TODO): retired rows refuse ALL verbs, so a
    second merge_memories on the same ids can't mint a second survivor."""
    a = _save("dup alpha"); b = _save("dup beta")
    _set_vec(a, [1, 0]); _set_vec(b, [1, 0])
    lt.open_pass('default', [a, b], kind='dedup')
    assert _run('merge_memories', {'memory_ids': [a, b]})[1]
    msg, ok = _run('merge_memories', {'memory_ids': [a, b]})
    assert not ok and 'already retired' in msg
    with pt._get_connection() as conn:
        n = conn.execute("SELECT COUNT(*) FROM chunks WHERE "
                         "json_extract(meta, '$.derived_from') IS NOT NULL").fetchone()[0]
    assert n == 1                                            # exactly one survivor


def test_derived_chunks_born_reviewed(palace):
    """Atomize parts / merge survivors carry librarian_at at birth — they
    never re-enter the next batch for a second look."""
    cid = _save("pizza AND the boat AND the antenna all tangled")
    lt.open_pass('default', [cid])
    msg, ok = _run('atomize_memory', {'memory_id': cid,
                                      'parts': ['the pizza part', 'the boat part']})
    assert ok, msg
    lt.close_pass()
    with pt._get_connection() as conn:
        cur = conn.cursor()
        batch = eng.build_batch(cur, 'default', 'all', 50)
    assert batch == []                                       # nothing left to review


def test_dedup_batch_clusters_and_mechanical_drain(palace):
    """The dedup pass (2026-07-16): unchecked embedded chunks are scanned,
    overlapping duplicate groups union into clusters, and dedup_at drains
    mechanically — a chunk already checked leaves the batch, but a NEW
    duplicate drags its checked partner back in via the cluster."""
    from plugins.mindpalace.tools import librarian as eng
    a = _save("Zebra visited the house")
    b = _save("Zebra visited the house again")
    solo = _save("bought a soldering iron")
    _set_vec(a, [1, 0, 0]); _set_vec(b, [1, 0, 0]); _set_vec(solo, [0, 1, 0])
    with pt._get_connection() as conn:
        cur = conn.cursor()
        batch = eng.build_dedup_batch(cur, 'default', 20)
        assert {r[0] for r in batch} == {a, b, solo}
        dups = eng.find_duplicates(cur, 'default', batch, 0.9)
    clusters = eng._build_clusters(batch, dups, {})
    assert len(clusters) == 1                       # a↔b union into ONE group
    assert {cid for cid, _cr, _c in clusters[0]} == {a, b}
    # Presentation: whole groups, the merge verb, the leave-alone option.
    text = eng._present_dedup(clusters, 'default', 1, 1)
    assert f"[{a}]" in text and f"[{b}]" in text and 'Group 1:' in text
    assert 'merge_memories' in text and 'leave the group alone' in text
    # Mechanical drain: stamped chunks leave the next batch.
    assert eng._stamp_meta_at([a, b, solo], 'dedup_at') == 3
    with pt._get_connection() as conn:
        assert eng.build_dedup_batch(conn.cursor(), 'default', 20) == []
    # A new duplicate of a checked chunk still surfaces — it's unchecked.
    c = _save("Zebra visited the house once more")
    _set_vec(c, [1, 0, 0])
    with pt._get_connection() as conn:
        cur = conn.cursor()
        batch = eng.build_dedup_batch(cur, 'default', 20)
        assert [r[0] for r in batch] == [c]
        dups = eng.find_duplicates(cur, 'default', batch, 0.9)
    cluster_ids = {cid for cl in eng._build_clusters(
        batch, dups, {i: ('2025-01-01', 'x') for i in (a, b)}) for cid, _cr, _c in cl}
    assert c in cluster_ids and a in cluster_ids and b in cluster_ids


def test_dedup_clusters_exclude_protected(palace):
    """Favorites/never-fades core can never merge (the guard refuses any
    cluster containing one) — presenting them as merge candidates burns the
    model's judgment on impossible combos (Krem live-fire find, 2026-07-19).
    Protected chunks stay OUT of clusters but still drain mechanically."""
    from plugins.mindpalace.tools import librarian as eng
    a = _save("Comet crossed the yard at dusk")
    b = _save("Comet crossed the yard at dusk again")
    c = _save("Moth landed on the porch light")
    d = _save("Moth landed on the porch light twice")
    _set_vec(a, [1, 0, 0]); _set_vec(b, [1, 0, 0])
    _set_vec(c, [0, 1, 0]); _set_vec(d, [0, 1, 0])
    with pt._get_connection() as conn:
        conn.execute('UPDATE chunks SET favorite = 1 WHERE id = ?', (a,))
        conn.execute('UPDATE chunks SET importance = 0.95 WHERE id = ?', (d,))
        conn.commit()
    with pt._get_connection() as conn:
        cur = conn.cursor()
        batch = eng.build_dedup_batch(cur, 'default', 20)
        dups = eng.find_duplicates(cur, 'default', batch, 0.9)
    cluster_ids = {cid for cl in eng._build_clusters(batch, dups, {})
                   for cid, _cr, _c in cl}
    assert cluster_ids == set(), (
        f"protected chunks seeded/joined a cluster: {cluster_ids}")
    # They still drain: all four are in the batch for the mechanical stamp.
    assert {a, b, c, d} <= {r[0] for r in batch}


# ─── Engine (phase L2): batch selection + caps ───────────────────────────────

from plugins.mindpalace.tools import librarian as eng


def test_batch_selection_excludes_processed_pruned_and_sheet(palace):
    a = _save("old raw event")
    b = _save("already reviewed")
    c = _save("free self thought", layer='self')
    lt.open_pass('default', [b])
    assert _run('mark_processed', {'memory_id': b})[1]
    lt.close_pass()
    d = _save("pure noise")
    lt.open_pass('default', [d])
    assert _run('prune_memory', {'memory_id': d})[1]
    lt.close_pass()
    with pt._get_connection() as conn:
        cur = conn.cursor()
        # a sheet section — the librarian never touches curated sections
        cur.execute("INSERT INTO chunks (layer, scope, content, meta, created, updated) "
                    "VALUES ('self', 'default', 'I am', ?, ?, ?)",
                    (json.dumps({'section': 'identity'}), pt._now(), pt._now()))
        conn.commit()
        ids_all = [r[0] for r in eng.build_batch(cur, 'default', 'all', 50)]
        assert a in ids_all and c in ids_all
        assert b not in ids_all and d not in ids_all
        assert all(r[1] in ('events', 'self') for r in eng.build_batch(cur, 'default', 'all', 50))
        ids_self = [r[0] for r in eng.build_batch(cur, 'default', 'self', 50)]
        assert ids_self == [c]


def test_daily_cap_refuses(palace):
    with pt._get_connection() as conn:
        cur = conn.cursor()
        eng._ensure_state_table(cur)
        cur.execute("INSERT INTO librarian_state (scope, last_pass, day, passes_today) "
                    "VALUES ('default', 'x', ?, 3)", (eng._today(),))
        conn.commit()
        ok, why = eng._check_caps(cur, 'default', {'per_day': 3})
        assert not ok and 'cap' in why.lower()
        ok, _ = eng._check_caps(cur, 'other-scope', {'per_day': 3})
        assert ok


def test_present_sort_carries_ids_and_charter(palace):
    a = _save("Krem got a boat")
    with pt._get_connection() as conn:
        batch = eng.build_batch(conn.cursor(), 'default', 'all', 10)
    msg = eng._present_sort(batch, 'default', [('Krem', 4)], 1, 1)
    assert f"[{a}]" in msg
    assert "REAL stays" in msg          # the charter travels with every pass
    assert "Krem (4 new mentions)" in msg
    assert "mark_processed" in msg
    # Merging and linking left the sort charter — they have their own passes.
    assert 'merge_memories' not in msg and 'link_memory' not in msg


# ─── Alpha master toggles (2026-07-15) ──────────────────────────────────────

def test_librarian_disabled_gate(monkeypatch):
    """librarian_enabled off (the shipping default): every pass entry —
    Tidy buttons, run_librarian tool, nightly — refuses at the one gate."""
    monkeypatch.setattr(eng, '_enabled', lambda: False)
    msg, ok = eng.start('default')
    assert not ok and 'disabled' in msg.lower() and 'alpha' in msg.lower()
    msg, ok = eng.run_blocking('default')
    assert not ok and 'disabled' in msg.lower()
    # Enabled → the gate opens (next check is the scope guard).
    monkeypatch.setattr(eng, '_enabled', lambda: True)
    msg, ok = eng.start('')
    assert not ok and 'scope' in msg.lower()


def test_mark_rating_ignored_when_importance_off(palace, monkeypatch):
    """importance_enabled off: mark_processed still files the memory but the
    rating is dropped — the column never moves."""
    monkeypatch.setattr(lt, '_importance_enabled', lambda: False)
    a = _save("an ordinary thought")
    lt.open_pass('default', [a])
    msg, ok = _run('mark_processed', {'memory_id': a, 'importance': 0.8})
    assert ok and 'importance' not in msg
    assert _importance(a) is None


def test_get_tools_strips_rating_param_when_off(monkeypatch):
    def _schema(tools):
        fn = next(t['function'] for t in tools
                  if t['function']['name'] == 'mark_processed')
        return fn['parameters']['properties']

    monkeypatch.setattr(lt, '_importance_enabled', lambda: False)
    assert 'importance' not in _schema(lt.get_tools())
    monkeypatch.setattr(lt, '_importance_enabled', lambda: True)
    assert 'importance' in _schema(lt.get_tools())
    # The static TOOLS list itself is never mutated by the off-path deepcopy.
    assert 'importance' in _schema(lt.TOOLS)


# ─── Dates pass (2026-07-16) — the librarian's date slice ───────────────────

from plugins.mindpalace.tools import librarian as eng2  # same module as eng


def test_set_event_dates_requires_dates_pass(palace):
    cid = _save("meeting on the 12th maybe")
    lt.open_pass('default', [cid])                      # sort pass
    msg, ok = _run('set_event_dates', {'entries': [{'memory_id': cid, 'dates': ['2026-08-12']}]})
    assert not ok and 'dates pass' in msg.lower()
    # …and sort verbs refuse inside a dates pass.
    lt.open_pass('default', [cid], kind='dates')
    msg, ok = _run('mark_processed', {'memory_id': cid})
    assert not ok and 'dates pass' in msg.lower()


def test_set_event_dates_writes_terminal_verdicts(palace):
    a = _save("Julie is coming on the 12th")
    b = _save("no dates in here at all")
    c = _save("bad date test")
    lt.open_pass('default', [a, b, c], kind='dates')
    msg, ok = _run('set_event_dates', {'entries': [
        {'memory_id': a, 'dates': ['2026-08-12', '2026-08-12T15:00']},
        {'memory_id': b, 'dates': []},
        {'memory_id': c, 'dates': ['12th of never']},
    ]})
    assert ok and '1 dated' in msg and '1 ruled dateless' in msg and 'invalid' in msg

    ma, mb, mc = _meta(a), _meta(b), _meta(c)
    assert ma['event_dates'] == ['2026-08-12', '2026-08-12T15:00']
    assert ma['event_date_src'] == 'librarian' and ma['temporal_at']
    assert 'librarian_at' not in ma                     # dating is not review
    assert 'event_dates' not in mb and mb['temporal_at']  # dateless verdict filed
    assert 'temporal_at' not in mc                      # invalid → untouched
    assert lt.close_pass()['handled'] == 2


def test_set_event_dates_overwrites_regex_and_refuses_outside_ids(palace):
    a = _save("we leave tomorrow")                      # regex floor stamps this
    assert _meta(a).get('event_date_src') == 'regex'
    outside = _save("not in the pass")
    lt.open_pass('default', [a], kind='dates')
    msg, ok = _run('set_event_dates', {'entries': [
        {'memory_id': a, 'dates': ['2026-07-17']},
        {'memory_id': outside, 'dates': ['2026-07-18']},
    ]})
    assert ok and 'not in this pass' in msg
    assert _meta(a)['event_dates'] == ['2026-07-17']
    assert _meta(a)['event_date_src'] == 'librarian'
    assert 'event_dates' not in _meta(outside)
    lt.close_pass()


def test_temporal_batch_selects_candidates_and_drains(palace):
    dated = _save("dinner on Dec 25 with everyone")     # regex-stamped, still candidate
    plain = _save("a memory with no time in it")
    with pt._get_connection() as conn:
        cur = conn.cursor()
        batch = eng2.build_temporal_batch(cur, 'default', 20)
        ids = [b[0] for b in batch]
        assert dated in ids and plain not in ids

    # A librarian verdict is terminal — the chunk leaves the queue.
    lt.open_pass('default', [dated], kind='dates')
    _run('set_event_dates', {'entries': [{'memory_id': dated, 'dates': ['2026-12-25']}]})
    lt.close_pass()
    with pt._get_connection() as conn:
        batch = eng2.build_temporal_batch(conn.cursor(), 'default', 20)
        assert dated not in [b[0] for b in batch]


def test_temporal_batch_newest_first(palace):
    old = _save("we met in january 1999")
    new = _save("party on saturday night")
    with pt._get_connection() as conn:
        conn.execute("UPDATE chunks SET created = '2020-01-01T00:00:00+00:00' "
                     "WHERE id = ?", (old,))
        conn.commit()
    with pt._get_connection() as conn:
        ids = [b[0] for b in eng2.build_temporal_batch(conn.cursor(), 'default', 20)]
    assert ids.index(new) < ids.index(old)


def test_state_table_migrates_v1_to_composite_key_and_renames_kinds(palace):
    """v1 (PK scope) → v2 (scope, pass) → v3 kind rename: old review rows
    come out as 'sort', old temporal rows as 'dates'."""
    with pt._get_connection() as conn:
        cur = conn.cursor()
        cur.execute('''CREATE TABLE librarian_state (
            scope TEXT PRIMARY KEY, last_pass TEXT, day TEXT,
            passes_today INTEGER NOT NULL DEFAULT 0, last_result TEXT)''')
        cur.execute("INSERT INTO librarian_state VALUES ('default', 'x', 'd', 2, NULL)")
        eng2._ensure_state_table(cur)
        rows = cur.execute('SELECT scope, pass, passes_today FROM librarian_state').fetchall()
        assert rows == [('default', 'sort', 2)]
        # v2-era rows with the old kind names rename in place too.
        cur.execute("INSERT INTO librarian_state (scope, pass, last_pass, day, passes_today) "
                    "VALUES ('work', 'temporal', 'x', 'd', 1)")
        eng2._ensure_state_table(cur)
        rows = cur.execute("SELECT pass FROM librarian_state WHERE scope='work'").fetchall()
        assert rows == [('dates',)]
        conn.commit()


def test_caps_are_per_pass_kind(palace):
    with pt._get_connection() as conn:
        cur = conn.cursor()
        eng2._ensure_state_table(cur)
        cur.execute("INSERT INTO librarian_state (scope, pass, last_pass, day, passes_today) "
                    "VALUES ('default', 'sort', 'x', ?, 3)", (eng2._today(),))
        ok, why = eng2._check_caps(cur, 'default', {'per_day': 3})
        assert not ok                                    # sort capped (default kind)
        ok, _ = eng2._check_caps(cur, 'default', {'per_day': 3}, kind='dates')
        assert ok                                        # dates budget untouched
        conn.commit()


def test_kind_aliases_and_unknown_kind(palace, monkeypatch):
    """Legacy names keep working ('temporal'→'dates', 'review'→'sort') and
    bogus kinds refuse before any thread spawns."""
    assert eng2._normalize_kind('temporal') == 'dates'
    assert eng2._normalize_kind('review') == 'sort'
    assert eng2._normalize_kind('dedup') == 'dedup'
    assert eng2.PASS_KINDS == ('dates', 'link', 'dedup', 'sort', 'self')
    monkeypatch.setattr(eng2, '_enabled', lambda: True)
    msg, ok = eng2.start('default', kind='bogus')
    assert not ok and 'Unknown pass kind' in msg


def test_pass_enabled_defaults_on_and_reads_toggle(monkeypatch):
    """librarian_pass_<kind>: absent = ON (the nightly recipe ships whole);
    an explicit false turns just that pass off."""
    from core.plugin_loader import plugin_loader
    monkeypatch.setattr(plugin_loader, 'get_plugin_settings', lambda n: {})
    assert eng2.pass_enabled('dedup') is True
    monkeypatch.setattr(plugin_loader, 'get_plugin_settings',
                        lambda n: {'librarian_pass_dedup': False})
    assert eng2.pass_enabled('dedup') is False
    assert eng2.pass_enabled('sort') is True


def test_upcoming_lists_future_dates_only(palace):
    fut = _save("vacation planning")
    past = _save("old anniversary")
    pruned = _save("retired future thing")
    with pt._get_connection() as conn:
        cur = conn.cursor()
        for cid, dates, extra in ((fut, ['2030-01-02'], {}),
                                  (past, ['2020-01-01'], {}),
                                  (pruned, ['2030-03-03'], {'pruned_at': 'x'})):
            m = json.loads(cur.execute('SELECT meta FROM chunks WHERE id=?', (cid,)).fetchone()[0])
            m.update({'event_dates': dates, 'event_date_src': 'librarian', **extra})
            cur.execute('UPDATE chunks SET meta=? WHERE id=?', (json.dumps(m), cid))
        conn.commit()
    from plugins.mindpalace.tools import self_tools as st2
    with pt._get_connection() as conn:
        up = st2._upcoming(conn.cursor(), 'default')
    ids = [u['id'] for u in up]
    assert fut in ids and past not in ids and pruned not in ids
    assert up[0]['date'] == '2030-01-02'


def test_just_happened_and_timed_upcoming_split(palace):
    """Timed events graduate from Upcoming to Just-happened as their clock
    passes (the same-day-lingering fix); date-only today holds Upcoming all
    day; the window is PAST_EVENT_DAYS."""
    from datetime import datetime, timedelta
    from plugins.mindpalace.tools import self_tools as st2
    now = datetime.now()
    fmt_min = '%Y-%m-%dT%H:%M'
    cases = {
        'timed_past': (now - timedelta(minutes=90)).strftime(fmt_min),
        'timed_fut': (now + timedelta(minutes=90)).strftime(fmt_min),
        'dateonly_today': now.strftime('%Y-%m-%d'),
        'yesterday': (now - timedelta(days=1)).strftime('%Y-%m-%d'),
        'ancient': (now - timedelta(days=30)).strftime('%Y-%m-%d'),
    }
    ids = {name: _save(f'event {name}') for name in cases}
    with pt._get_connection() as conn:
        cur = conn.cursor()
        for name, date in cases.items():
            m = json.loads(cur.execute('SELECT meta FROM chunks WHERE id=?',
                                       (ids[name],)).fetchone()[0])
            m.update({'event_dates': [date], 'event_date_src': 'librarian'})
            cur.execute('UPDATE chunks SET meta=? WHERE id=?',
                        (json.dumps(m), ids[name]))
        conn.commit()
        up = {u['id'] for u in st2._upcoming(cur, 'default')}
        jh = {u['id'] for u in st2._just_happened(cur, 'default')}
    assert ids['timed_past'] not in up and ids['timed_past'] in jh
    assert ids['timed_fut'] in up and ids['timed_fut'] not in jh
    assert ids['dateonly_today'] in up
    assert ids['yesterday'] in jh
    assert ids['ancient'] not in jh                     # outside the 7-day window
