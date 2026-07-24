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


# ─── Coverage holes closed (prompt-ledger Phase 2, 2026-07-22) ───────────────
# wake_tools CRUD, residency, scope birth/death — the three seams the audit
# found silent. All land as 'user' rows: visible in her wake tail.

def test_wake_tool_crud_hits_the_ledger(palace, monkeypatch):
    # Full-suite order independence: with a booted system _known_tools()
    # returns the real enabled list (no 'get_weather'); pin the skip path.
    monkeypatch.setattr(self_routes, '_known_tools', lambda: None)
    out = self_routes.create_wake_tool(body={'scope': 'w1', 'tool': 'get_weather',
                                             'params': {'city': 'Tampa'}})
    wid = out['id']
    self_routes.update_wake_tool(wid=wid, body={'enabled': False})
    self_routes.update_wake_tool(wid=wid, body={'position': 3})   # reorder only
    self_routes.delete_wake_tool(wid=wid)
    rows = _rows('w1', layer='self')
    assert [r['action'] for r in rows] == ['saved', 'edited', 'deleted']
    assert rows[0]['summary'] == 'armed "get_weather" at wake'
    assert rows[1]['summary'] == 'wake tool "get_weather": disabled'
    assert rows[1]['detail']['fields']['enabled'] == ['on', 'off']
    assert rows[2]['summary'] == 'disarmed "get_weather" wake tool'
    with pt._get_connection() as conn:
        block = lg.tail_block(conn.cursor(), 'w1')
    assert 'armed' in block                     # surfaces in her wake tail


def test_wake_tool_noop_update_stays_silent(palace, monkeypatch):
    monkeypatch.setattr(self_routes, '_known_tools', lambda: None)
    out = self_routes.create_wake_tool(body={'scope': 'w1b', 'tool': 'get_weather'})
    self_routes.update_wake_tool(wid=out['id'], body={'enabled': True})  # already on
    rows = _rows('w1b', layer='self')
    assert [r['action'] for r in rows] == ['saved']


def test_put_resident_change_and_noop(palace):
    browse.put_resident(body={'scope': 'w2', 'model': 'fireworks-glm',
                              'prompt': 'sapph-first'})
    browse.put_resident(body={'scope': 'w2'})              # no-op save
    rows = _rows('w2', layer='self', target='resident')
    assert len(rows) == 1
    assert 'model → fireworks-glm' in rows[0]['summary']
    assert 'prompt → sapph-first' in rows[0]['summary']
    assert rows[0]['detail']['fields']['model'] == ['', 'fireworks-glm']
    browse.put_resident(body={'scope': 'w2', 'passes': {'dedup': True}})
    rows = _rows('w2', layer='self', target='resident')
    assert len(rows) == 2 and 'passes → dedup' in rows[1]['summary']


def test_put_resident_prompt_ledger_flip_is_recorded(palace):
    """Disabling prompt recording writes one last row — a blind window
    always starts with a visible line."""
    browse.put_resident(body={'scope': 'w2b', 'prompt_ledger': False})
    rows = _rows('w2b', layer='self', target='resident')
    assert len(rows) == 1 and 'prompt ledger → off' in rows[0]['summary']
    assert rows[0]['detail']['fields']['prompt_ledger'] == ['on', 'off']
    browse.put_resident(body={'scope': 'w2b', 'prompt_ledger': False})  # no-op
    assert len(_rows('w2b', layer='self', target='resident')) == 1
    browse.put_resident(body={'scope': 'w2b', 'prompt_ledger': True})
    rows = _rows('w2b', layer='self', target='resident')
    assert len(rows) == 2 and 'prompt ledger → on' in rows[1]['summary']


def test_scope_birth_and_death_rows(palace):
    pt.create_scope('w3')
    pt.create_scope('w3')                       # ensure-exists: silent
    birth = _rows('w3', layer='scopes')
    assert len(birth) == 1 and birth[0]['summary'] == 'scope "w3" created'
    lg.record('w3', 'user', 'edited', summary='doomed')
    pt.delete_scope('w3')
    assert _rows('w3') == []                    # razed with the scope
    death = _rows('default', layer='scopes')
    assert len(death) == 1
    assert death[0]['summary'].startswith('scope "w3" deleted')
    assert death[0]['actor'] == 'user'


def test_phantom_scope_delete_stays_silent(palace):
    pt.delete_scope('never-existed')
    assert _rows('default', layer='scopes') == []


# ─── Librarian run grouping (2026-07-24, ledger-spam fix) ────────────────────
# Drains/nightly used to write one TOP-LEVEL pass row per batch. Now the
# orchestrators open a run row, batches nest under it, and run_end appends a
# 'report' child that readers overlay onto the run line.

def _fake_batch(i):
    return {'presented': 20, 'handled': 15,
            'ledger': [{'action': 'promoted', 'summary': f'kept {i}'}]}


def test_librarian_run_groups_batches_into_one_line(palace):
    from plugins.mindpalace.tools import librarian as lib
    lib.run_begin('r1', 'Run ALL (sort)')
    with pt._get_connection() as conn:
        cur = conn.cursor()
        for i in range(3):
            lib._write_ledger(cur, 'r1', _fake_batch(i), f'sort pass: batch {i}', 'sort')
        conn.commit()
    lib.run_end('r1', left=5)
    top = [r for r in _rows('r1') if r['parent_id'] is None]
    assert len(top) == 1                       # ONE line for the whole run
    with pt._get_connection() as conn:
        block, shown = lg.read_block(conn.cursor(), 'r1')
    assert shown == 1
    assert 'Run ALL (sort): 3 batch(es), 45/60 handled' in block
    assert '3 promoted' in block and '(5 still queued)' in block
    assert 'running…' not in block             # report replaced the opener
    with pt._get_connection() as conn:         # tail overlays too
        tail = lg.tail_block(conn.cursor(), 'r1')
    assert '3 batch(es)' in tail and 'running…' not in tail


def test_run_drilldown_keeps_batches_and_items(palace):
    from plugins.mindpalace.tools import librarian as lib
    lib.run_begin('r2', 'nightly tending')
    with pt._get_connection() as conn:
        cur = conn.cursor()
        lib._write_ledger(cur, 'r2', _fake_batch(0), 'sort pass: batch', 'sort')
        conn.commit()
    lib.run_end('r2')
    run_id = [r for r in _rows('r2') if r['parent_id'] is None][0]['id']
    kids = self_routes.get_ledger(query={'scope': 'r2', 'parent_id': str(run_id)})
    batch = [k for k in kids['rows'] if k['action'] == 'pass']
    assert len(batch) == 1
    assert batch[0]['children'] == 1           # the item row — UI can drill on
    # top-level UI row shows the after-action, not "running…"
    ui = self_routes.get_ledger(query={'scope': 'r2'})
    assert 'nightly tending: 1 batch(es)' in ui['rows'][0]['summary']


def test_run_crash_leaves_honest_running_line(palace):
    from plugins.mindpalace.tools import librarian as lib
    lib.run_begin('r3', 'Run ALL (dedup)')
    with pt._get_connection() as conn:
        cur = conn.cursor()
        lib._write_ledger(cur, 'r3', _fake_batch(0), 'dedup pass', 'dedup')
        conn.commit()
    # no run_end — crash. The line stays honest about it.
    with pt._get_connection() as conn:
        block, _ = lg.read_block(conn.cursor(), 'r3')
    assert 'running…' in block
    lib._runs.pop('r3', None)                  # don't leak into other tests


def test_run_notes_and_quiet_night(palace):
    from plugins.mindpalace.tools import librarian as lib
    lib.run_begin('r4', 'Run ALL (dedup)')
    lib._run_note('r4', scanned=40)
    lib._run_note('r4', scanned=20)
    lib.run_end('r4')
    with pt._get_connection() as conn:
        block, _ = lg.read_block(conn.cursor(), 'r4')
    assert '60 scanned' in block
    lib.run_begin('r5', 'nightly tending')
    lib.run_end('r5')
    with pt._get_connection() as conn:
        block, _ = lg.read_block(conn.cursor(), 'r5')
    assert 'nightly tending: nothing to do' in block


def test_unwrapped_single_pass_stays_one_top_level_row(palace):
    from plugins.mindpalace.tools import librarian as lib
    with pt._get_connection() as conn:
        cur = conn.cursor()
        lib._write_ledger(cur, 'r6', _fake_batch(0), 'sort pass: 20 reviewed', 'sort')
        conn.commit()
    top = [r for r in _rows('r6') if r['parent_id'] is None]
    assert len(top) == 1 and top[0]['summary'] == 'sort pass: 20 reviewed'


# ─── clear_ledger maintenance valve (2026-07-24) ─────────────────────────────

def test_clear_ledger_requires_typed_confirm_and_records_itself(palace):
    for i in range(4):
        lg.record('c1', 'user', 'edited', summary=f'change {i}')
    st._read_self('c1')                        # watermark row exists
    out, code = browse.maintenance(body={'action': 'clear_ledger',
                                         'scope': 'c1', 'confirm': 'wrong'})
    assert code == 400
    assert len(_rows('c1')) == 4               # nothing touched
    out = browse.maintenance(body={'action': 'clear_ledger',
                                   'scope': 'c1', 'confirm': 'c1'})
    assert out['success'] and out['cleared'] == 4
    rows = _rows('c1')
    assert len(rows) == 1                      # the fresh ledger's first row
    assert rows[0]['summary'] == 'ledger cleared — 4 entries removed'
    with pt._get_connection() as conn:
        left = conn.execute("SELECT COUNT(*) FROM ledger_reads "
                            "WHERE scope='c1'").fetchone()[0]
    assert left == 0                           # watermark reset too
