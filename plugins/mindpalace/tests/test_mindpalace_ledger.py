"""Ledger v1 (2026-07-12) — the append-only change stream over the mind.

Covers: row-per-seam (self diff wording, save/delete, routes, librarian
buffer, imports), the read_self tail (cap, exclusions, watermark), the
get_ledger route (children, unread window), the Memories sheet exclusion,
and the append-only invariant (no UPDATE/DELETE path in the module).
"""
import json

import pytest

from plugins.mindpalace.tools import palace_tools as pt
from plugins.mindpalace.tools import self_tools as st
from plugins.mindpalace.tools import ledger as lg
from plugins.mindpalace.tools import librarian_tools as lt
from plugins.mindpalace.routes import browse
from plugins.mindpalace.routes import self_routes


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


def _rows(scope, **eq):
    where, params = ['scope = ?'], [scope]
    for k, v in eq.items():
        where.append(f'{k} = ?')
        params.append(v)
    with pt._get_connection() as conn:
        rows = conn.execute(
            f"SELECT id, ts, actor, action, layer, target, summary, detail, parent_id "
            f"FROM ledger WHERE {' AND '.join(where)} ORDER BY id", params).fetchall()
    return [{'id': r[0], 'ts': r[1], 'actor': r[2], 'action': r[3], 'layer': r[4],
             'target': r[5], 'summary': r[6],
             'detail': json.loads(r[7]) if r[7] else None, 'parent_id': r[8]}
            for r in rows]


# ─── Self-sheet seam: the diff wording (the #6 lesson) ───────────────────────

def test_values_diff_wording_added_removed(palace):
    st.write_section('s1', 'values', 'honesty\ncuriosity\ncourage')
    st.write_section('s1', 'values', 'honesty\ncuriosity')          # remove one
    st.write_section('s1', 'values', 'honesty\ncuriosity\nconsent')  # add one
    rows = _rows('s1', layer='self')
    assert [r['action'] for r in rows] == ['saved', 'edited', 'edited']
    assert rows[0]['summary'] == "wrote Values"
    assert rows[1]['summary'] == "removed 'courage' from Values"
    assert rows[2]['summary'] == "added 'consent' to Values"
    assert rows[1]['detail']['before'].splitlines()[-1] == 'courage'
    assert rows[1]['detail']['after'] == 'honesty\ncuriosity'


def test_values_diff_both_directions_and_clear(palace):
    st.write_section('s1', 'values', 'honesty\ncourage')
    st.write_section('s1', 'values', 'honesty\nconsent')   # swap one
    st.write_section('s1', 'values', '')                   # clear
    rows = _rows('s1', layer='self')
    assert rows[1]['summary'] == "Values: added 'consent'; removed 'courage'"
    assert rows[2]['action'] == 'removed'
    assert rows[2]['summary'] == "cleared Values"


def test_text_section_wording_and_noop_skipped(palace):
    st.write_section('s1', 'origin', 'Born in a terminal.')
    st.write_section('s1', 'origin', 'Born in a terminal.')     # no-op
    st.write_section('s1', 'origin', 'Born in a terminal, raised on a workbench.')
    rows = _rows('s1', layer='self')
    assert len(rows) == 2
    assert rows[0]['summary'] == "wrote Origin"
    assert rows[1]['summary'] == "edited Origin"
    assert rows[0]['actor'] == 'user'   # no tool_context on the test thread


# ─── Chunk seams: save / delete / routes ─────────────────────────────────────

def test_save_and_delete_memory_rows(palace):
    msg, ok = pt._save_memory('We fixed the ledger today', 's2')
    assert ok
    cid = int(msg.split('ID: ')[1].split(',')[0])
    pt._delete_memory(cid, 's2')
    rows = _rows('s2')
    assert [r['action'] for r in rows] == ['saved', 'deleted']
    assert 'We fixed the ledger today' in rows[0]['summary']
    assert rows[1]['target'] == str(cid)


def test_route_seams_delete_favorite_unprune_entity(palace):
    pt._save_memory('a memory to poke at', 's3')
    with pt._get_connection() as conn:
        cid = conn.execute("SELECT id FROM chunks WHERE scope='s3'").fetchone()[0]
    browse.toggle_favorite(cid=cid, body={'favorite': True})
    browse.toggle_favorite(cid=cid, body={'favorite': False})
    # prune by hand, then restore through the route
    with pt._get_connection() as conn:
        conn.execute("UPDATE chunks SET meta = json_set(COALESCE(meta,'{}'), "
                     "'$.pruned_at', '2026-07-12') WHERE id = ?", (cid,))
        conn.commit()
    browse.unprune_chunk(cid=cid)
    browse.delete_chunk(cid=cid)
    browse.create_entity(body={'name': 'Ledgerling', 'scope': 's3'})
    with pt._get_connection() as conn:
        eid = conn.execute("SELECT id FROM entities WHERE scope='s3'").fetchone()[0]
    browse.delete_entity(eid=eid)
    actions = [r['action'] for r in _rows('s3')]
    assert actions == ['saved', 'favorite', 'unfavorite', 'restored',
                       'deleted', 'deleted']
    ent_row = _rows('s3', action='deleted')[-1]
    assert "entity 'Ledgerling'" in ent_row['summary']
    assert all(r['actor'] == 'user' for r in _rows('s3'))


# ─── Librarian: children buffer + parent/child read shape ────────────────────

def test_librarian_verbs_buffer_and_close_pass_returns_them(palace):
    pt._save_memory('trivial note one', 's4')
    pt._save_memory('important thing about Krem', 's4')
    with pt._get_connection() as conn:
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM chunks WHERE scope='s4' ORDER BY id").fetchall()]
    lt.open_pass('s4', ids)
    msg, ok = lt._prune(ids[0], reason='trivia')
    assert ok
    msg, ok = lt._mark(ids[1])   # mark_processed does NOT buffer a row
    assert ok
    stats = lt.close_pass()
    assert stats['handled'] == 2
    # mark_processed buffers a 'marked' child too now (audit scout
    # 2026-07-19: ratings/favorites are data writes, they get a trail).
    assert len(stats['ledger']) == 2
    actions = {c['action'] for c in stats['ledger']}
    assert actions == {'retired', 'marked'}
    child = next(c for c in stats['ledger'] if c['action'] == 'retired')
    assert 'trivia' in child['summary']


def test_pass_parent_children_and_sheet_collapse(palace):
    # Write the shape _worker writes: one parent, children under it.
    parent = lg.record('s5', 'librarian', 'pass',
                       summary='librarian pass: 5 reviewed — 2 retired, rest kept',
                       detail={'presented': 5, 'handled': 5})
    for i in range(2):
        lg.record('s5', 'librarian', 'retired', layer='events', target=i + 100,
                  summary=f'retired [{i + 100}]', parent_id=parent)
    # get_ledger: children fold under the parent, fetchable by parent_id
    top = self_routes.get_ledger(query={'scope': 's5'})
    assert len(top['rows']) == 1 and top['rows'][0]['children'] == 2
    kids = self_routes.get_ledger(query={'scope': 's5', 'parent_id': str(parent)})
    assert [k['summary'] for k in kids['rows']] == ['retired [100]', 'retired [101]']
    # read_self tail: the pass is ONE line, children collapsed
    text, ok = st._read_self('s5')
    assert ok
    assert '2 retired, rest kept' in text
    assert 'retired [100]' not in text


# ─── read_self tail: cap, exclusions, watermark ──────────────────────────────

def test_tail_excludes_ai_rows_and_caps_with_more_line(palace):
    lg.record('s6', 'ai', 'saved', summary='her own routine save')
    for i in range(12):
        lg.record('s6', 'user', 'edited', layer='self', summary=f'user change {i:02d}')
    text, ok = st._read_self('s6')
    assert ok and '◆ Ledger' in text
    assert 'her own routine save' not in text
    assert 'user change 11' in text          # newest first
    assert 'user change 02' in text          # 10 shown
    assert 'user change 01' not in text      # oldest two are cut
    assert 'user change 00' not in text
    assert '…and 2 more' in text


def test_read_self_stamps_watermark_and_unread_window(palace):
    lg.record('s7', 'user', 'edited', layer='self', summary='before read')
    before = self_routes.get_ledger(query={'scope': 's7'})
    assert before['unread'] == 1 and before['last_read_ts'] is None
    st._read_self('s7')                      # she reads → watermark
    mid = self_routes.get_ledger(query={'scope': 's7'})
    assert mid['unread'] == 0 and mid['last_read_ts'] is not None
    lg.record('s7', 'user', 'edited', layer='self', summary='after read')
    lg.record('s7', 'ai', 'saved', summary='her own — not unread')
    after = self_routes.get_ledger(query={'scope': 's7'})
    assert after['unread'] == 1


def test_empty_ledger_no_tail_block(palace):
    text, ok = st._read_self('s8')
    assert ok and '◆ Ledger' not in text


# ─── Memories tab: the sheet leaves the stream ───────────────────────────────

def test_memories_exclude_sheet_hides_sheet_keeps_deliberate_self(palace):
    st.write_section('s9', 'values', 'honesty')                  # sheet chunk
    st.write_section('s9', 'values', 'honesty\ncourage')         # + superseded
    pt._save_memory('a deliberate self note', 's9', layer='self')
    pt._save_memory('an event', 's9')
    out = browse.list_chunks(query={'scope': 's9', 'layer': 'events,self',
                                    'exclude_sheet': '1'})
    contents = {c['content'] for c in out['chunks']}
    assert contents == {'a deliberate self note', 'an event'}
    assert out['total'] == 2
    # without the flag the sheet is still reachable (Self page, exports)
    full = browse.list_chunks(query={'scope': 's9', 'layer': 'events,self'})
    assert full['total'] == 4


# ─── Append-only invariant ───────────────────────────────────────────────────

def test_ledger_module_has_no_update_or_delete_path():
    import inspect
    src = inspect.getsource(lg)
    assert 'UPDATE ledger' not in src
    assert 'DELETE FROM ledger' not in src.replace('DELETE FROM ledger_reads', '')


def test_scope_delete_is_the_one_destructor(palace):
    pt.create_scope('s10')
    lg.record('s10', 'user', 'edited', summary='doomed row')
    st._read_self('s10')   # watermark row too
    pt.delete_scope('s10')
    assert _rows('s10') == []
    with pt._get_connection() as conn:
        left = conn.execute("SELECT COUNT(*) FROM ledger_reads WHERE scope='s10'").fetchone()[0]
    assert left == 0


def test_record_failure_isolated(palace, monkeypatch):
    # A broken ledger must never block the op it records — record returns
    # None instead of raising, and the seam op proceeds.
    monkeypatch.setattr(lg, '_now', lambda: (_ for _ in ()).throw(RuntimeError('boom')))
    assert lg.record('s11', 'user', 'edited', summary='x') is None
    msg, ok = pt._save_memory('op survives a dead ledger', 's11')
    assert ok
