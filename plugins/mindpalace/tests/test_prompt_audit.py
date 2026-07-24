"""Prompt-ledger sink (2026-07-22, buffer-and-flush rework) — core.audit
queue + palace routing.

North star: she can always answer "was I tampered with?" from read_ledger.
Record automatic/unconditional; reason optional annotation. Routing by
COALESCE(watched_prompt, prompt); containment checked for component edits
(over-log when unanswerable). APPEND-ONLY honored fully (B+D): edits BUFFER
in memory and flush as ONE INSERT per 30-min session — the ledger is never
UPDATEd; post-hoc reasons are 'noted' child rows readers overlay.
"""
import json
from datetime import timedelta

import pytest

from core import audit
from plugins.mindpalace.tools import palace_tools as pt
from plugins.mindpalace.tools import ledger as lg
from plugins.mindpalace.tools import prompt_audit as pa


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
    pa._open.clear()   # no buffered sessions bleed between tests
    yield pt
    pa._open.clear()


def _rows(scope, **eq):
    where, params = ['scope = ?'], [scope]
    for k, v in eq.items():
        where.append(f'{k} = ?')
        params.append(v)
    with pt._get_connection() as conn:
        rows = conn.execute(
            f"SELECT id, ts, actor, action, target, summary, detail, parent_id "
            f"FROM ledger WHERE {' AND '.join(where)} ORDER BY id", params).fetchall()
    return [{'id': r[0], 'ts': r[1], 'actor': r[2], 'action': r[3],
             'target': r[4], 'summary': r[5],
             'detail': json.loads(r[6]) if r[6] else None,
             'parent_id': r[7]} for r in rows]


def _mono(name='sapph-first', before='old text', after='new text',
          actor='user', reason=None):
    e = {'kind': 'monolith', 'name': name, 'before': before, 'after': after,
         'actor': actor}
    if reason:
        e['reason'] = reason
    return e


def _edit_and_flush(*events):
    for e in events:
        pa.handle(e)
    pa.flush(force=True)


# ─── core/audit registry (queued worker) ─────────────────────────────────────

@pytest.fixture
def bare_registry():
    """Snapshot-and-clear the sink registry: the import-time palace sink
    must not receive these synthetic events (it would resolve the REAL
    mind.db path outside the palace fixture)."""
    with audit._lock:
        saved = dict(audit._sinks)
        audit._sinks.clear()
    yield
    audit.flush(timeout=5)
    with audit._lock:
        audit._sinks.clear()
        audit._sinks.update(saved)


def test_emit_with_no_sinks_is_a_noop(bare_registry):
    audit.emit({'kind': 'monolith', 'name': 'x'})   # dropped pre-queue


def test_broken_sink_is_contained_and_others_still_fire(bare_registry):
    got = []
    audit.register_sink('t2-bad', lambda e: (_ for _ in ()).throw(RuntimeError('boom')))
    audit.register_sink('t2-good', got.append)
    audit.emit({'kind': 'monolith', 'name': 'x'})
    assert audit.flush(timeout=5)
    assert len(got) == 1


def test_emit_never_runs_sinks_on_the_calling_thread(bare_registry):
    import threading
    seen = []
    audit.register_sink('t3', lambda e: seen.append(threading.current_thread().name))
    audit.emit({'kind': 'monolith', 'name': 'x'})
    assert audit.flush(timeout=5)
    assert seen and seen[0] == 'audit-sink'
    assert seen[0] != threading.current_thread().name


# ─── Routing ─────────────────────────────────────────────────────────────────

def test_resident_prompt_is_the_default_watch(palace):
    pt.set_scope_resident('s1', prompt='sapph-first')
    _edit_and_flush(_mono(reason='tightened the opener'),
                    _mono(name='other-prompt'))
    rows = _rows('s1', layer='prompt')
    assert len(rows) == 1
    assert rows[0]['target'] == 'monolith/sapph-first'
    assert rows[0]['summary'] == 'prompt "sapph-first" edited: tightened the opener'
    assert rows[0]['detail']['before'] == 'old text'
    assert rows[0]['detail']['after'] == 'new text'


def test_watched_prompt_overrides_resident(palace):
    pt.set_scope_resident('s2', prompt='librarian-x', watched_prompt='sapph-first')
    _edit_and_flush(_mono(), _mono(name='librarian-x'))
    rows = _rows('s2', layer='prompt')
    assert len(rows) == 1 and rows[0]['target'] == 'monolith/sapph-first'
    assert 'no reason given' in rows[0]['summary']


def test_one_event_lands_in_every_watching_scope(palace):
    pt.set_scope_resident('s3a', prompt='sapph-first')
    pt.set_scope_resident('s3b', watched_prompt='sapph-first')
    pt.set_scope_resident('s3c', prompt='unrelated')
    _edit_and_flush(_mono())
    assert len(_rows('s3a', layer='prompt')) == 1
    assert len(_rows('s3b', layer='prompt')) == 1
    assert _rows('s3c', layer='prompt') == []


def test_prompt_ledger_off_gates_the_watch(palace):
    """The Self-page toggle: off = this scope records no prompt changes at
    all; back on = the watch resumes. The flip row itself is put_resident's
    job (tested with the route)."""
    pt.set_scope_resident('s3d', prompt='sapph-first', prompt_ledger=False)
    _edit_and_flush(_mono(), _mono(before='', after='born'))   # edit AND save
    assert _rows('s3d', layer='prompt') == []
    pt.set_scope_resident('s3d', prompt_ledger=True)
    _edit_and_flush(_mono())
    assert len(_rows('s3d', layer='prompt')) == 1


def test_component_edit_checks_preset_containment(palace, monkeypatch):
    from core import prompts
    monkeypatch.setattr(prompts, 'get_prompt', lambda n: {
        'type': 'assembled', 'components': {'emotions': ['happy', 'curious']}})
    pt.set_scope_resident('s4', prompt='sapph-first')
    _edit_and_flush(
        {'kind': 'component', 'comp_type': 'emotions', 'key': 'happy',
         'before': 'joyful', 'after': 'joyful and light', 'actor': 'user'},
        {'kind': 'component', 'comp_type': 'emotions', 'key': 'sad',
         'before': 'x', 'after': 'y', 'actor': 'user'})   # not contained
    rows = _rows('s4', layer='prompt')
    assert len(rows) == 1 and rows[0]['target'] == 'component/emotions/happy'


def test_unanswerable_containment_over_logs(palace, monkeypatch):
    from core import prompts
    monkeypatch.setattr(prompts, 'get_prompt',
                        lambda n: (_ for _ in ()).throw(RuntimeError('down')))
    pt.set_scope_resident('s6', prompt='sapph-first')
    _edit_and_flush({'kind': 'component', 'comp_type': 'emotions', 'key': 'happy',
                     'before': 'a', 'after': 'b', 'actor': 'user'})
    assert len(_rows('s6', layer='prompt')) == 1   # over-log beats under-log


def test_activation_routes_by_preset_and_keeps_ttl(palace):
    pt.set_scope_resident('s7', prompt='sapph-first')
    pa.handle({'kind': 'activation', 'comp_type': 'emotions', 'key': 'happy',
               'active': True, 'prompt': 'sapph-first', 'actor': 'ai',
               'ttl_minutes': 60, 'reason': 'green night'})
    rows = _rows('s7', layer='prompt')
    assert len(rows) == 1
    assert rows[0]['summary'] == \
        'prompt piece "happy" added to "sapph-first" for 60m: green night'
    assert rows[0]['detail']['ttl_minutes'] == 60
    assert rows[0]['actor'] == 'ai'


def test_anonymous_working_preset_matches_assembled_watch(palace, monkeypatch):
    # Scout find: her live piece toggles carry preset='custom' when the
    # running persona is an anonymous working copy — a named watch must
    # still get the row (over-log beats a silent tamper gap).
    from core import prompts
    monkeypatch.setattr(prompts, 'get_prompt',
                        lambda n: {'type': 'assembled', 'components': {}})
    pt.set_scope_resident('s7b', watched_prompt='sapphire')
    pa.handle({'kind': 'activation', 'comp_type': 'goals', 'key': 'quest',
               'active': True, 'prompt': 'custom', 'actor': 'ai'})
    assert len(_rows('s7b', layer='prompt')) == 1
    # ...but a DIFFERENT named preset still doesn't match a monolith watch
    monkeypatch.setattr(prompts, 'get_prompt',
                        lambda n: {'type': 'monolith', 'content': 'x'})
    pa.handle({'kind': 'activation', 'comp_type': 'goals', 'key': 'quest',
               'active': True, 'prompt': 'custom', 'actor': 'ai'})
    assert len(_rows('s7b', layer='prompt')) == 1


# ─── Buffer-and-flush (append-only coalescing) ───────────────────────────────

def test_autosave_edits_buffer_into_one_appended_row(palace):
    pt.set_scope_resident('s8', prompt='sapph-first')
    pa.handle(_mono(before='v1', after='v2'))
    pa.handle(_mono(before='v2', after='v3'))
    pa.handle(_mono(before='v3', after='v4', reason='late reason'))
    assert _rows('s8', layer='prompt') == []       # in-flight: nothing written
    pa.flush(force=True)
    rows = _rows('s8', layer='prompt')
    assert len(rows) == 1
    assert rows[0]['detail']['before'] == 'v1'     # session's first, frozen
    assert rows[0]['detail']['after'] == 'v4'      # advanced
    assert rows[0]['detail']['reason'] == 'late reason'
    assert rows[0]['summary'].endswith(': late reason')


def test_expired_window_flushes_lazily_and_new_session_opens(palace):
    pt.set_scope_resident('s9', prompt='sapph-first')
    pa.handle(_mono(before='v1', after='v2'))
    with pa._buf_lock:   # age the open session past the window
        for buf in pa._open.values():
            buf['last'] -= timedelta(minutes=pa.WINDOW_MINUTES + 1)
    pa.handle(_mono(before='v2', after='v3'))      # lazy sweep closes the old one
    pa.flush(force=True)
    assert len(_rows('s9', layer='prompt')) == 2


def test_delete_flushes_open_session_then_appends(palace):
    pt.set_scope_resident('s10', prompt='sapph-first')
    pa.handle(_mono(before='v1', after='v2'))
    pa.handle(_mono(before='v2', after=''))        # deletion closes the session
    rows = _rows('s10', layer='prompt')
    assert [r['action'] for r in rows] == ['edited', 'removed']
    assert 'deleted' in rows[1]['summary']


def test_type_then_revert_session_writes_nothing(palace):
    pt.set_scope_resident('s10b', prompt='sapph-first')
    pa.handle(_mono(before='v1', after='v2'))
    pa.handle(_mono(before='v2', after='v1'))      # back where it started
    pa.flush(force=True)
    assert _rows('s10b', layer='prompt') == []     # no net change, no evidence


def test_reason_last_typed_wins_within_session(palace):
    pt.set_scope_resident('s14', prompt='sapph-first')
    _edit_and_flush(_mono(before='v1', after='v2', reason='first draft why'),
                    _mono(before='v2', after='v3', reason='the real why'))
    rows = _rows('s14', layer='prompt')
    assert len(rows) == 1
    assert rows[0]['detail']['reason'] == 'the real why'
    assert rows[0]['summary'].endswith(': the real why')


def test_huge_content_is_clamped(palace):
    pt.set_scope_resident('s11', prompt='sapph-first')
    _edit_and_flush(_mono(before='y' * 25_000, after='z'))
    d = _rows('s11', layer='prompt')[0]['detail']
    assert len(d['before']) <= pa.CONTENT_CHARS + 20
    assert d['before'].endswith('… [truncated]')


def test_ledger_reads_flush_in_flight_sessions(palace, monkeypatch):
    from plugins.mindpalace.tools import self_tools
    pt.set_scope_resident('s12', prompt='sapph-first')
    pa.handle(_mono(reason='mid-session edit'))
    text, ok = self_tools._read_ledger('s12')      # the read is the flush trigger
    assert ok and 'mid-session edit' in text


def test_prompt_audit_never_updates_or_deletes_ledger_rows():
    import inspect
    src = inspect.getsource(pa)
    assert 'UPDATE ledger' not in src
    assert 'DELETE FROM ledger' not in src


# ─── End-to-end wiring + hardening ───────────────────────────────────────────

def test_emit_reaches_the_palace_sink(palace):
    audit.register_sink('mindpalace', pa.handle)   # what import-time does
    try:
        pt.set_scope_resident('s13', prompt='sapph-first')
        audit.emit(_mono())
        assert audit.flush(timeout=5)
        pa.flush(force=True)
        assert len(_rows('s13', layer='prompt')) == 1
    finally:
        audit.unregister_sink('mindpalace')


def test_handle_never_raises_into_core(palace, monkeypatch):
    monkeypatch.setattr(pt, '_ensure_db',
                        lambda: (_ for _ in ()).throw(RuntimeError('db gone')))
    pa.handle(_mono())   # must not raise


def test_prompt_rows_ride_the_deep_view(palace):
    from plugins.mindpalace.tools import self_tools
    pt.set_scope_resident('s15', prompt='sapph-first')
    _edit_and_flush(_mono(reason='wanted her to choose'))
    rid = _rows('s15', layer='prompt')[0]['id']
    text, ok = self_tools._read_ledger('s15', ids=[rid])
    assert ok
    assert 'reason: wanted her to choose' in text
    assert 'before: old text' in text and 'after: new text' in text


# ─── Reason pencil (PUT ledger/{id}/reason — 'noted' child rows, D) ──────────

def test_patch_reason_appends_note_and_readers_overlay(palace):
    from plugins.mindpalace.routes import self_routes
    from plugins.mindpalace.tools import self_tools
    pt.set_scope_resident('s16', prompt='sapph-first')
    _edit_and_flush(_mono())
    row = _rows('s16', layer='prompt')[0]
    out = self_routes.patch_ledger_reason(lid=row['id'],
                                          body={'scope': 's16',
                                                'reason': 'tuned her opener'})
    assert out['success']
    # the target row itself is UNTOUCHED — append-only honored
    after = [r for r in _rows('s16', layer='prompt') if r['id'] == row['id']][0]
    assert after['summary'] == row['summary']
    assert after['detail'] == row['detail']
    # the note is a child row
    notes = [r for r in _rows('s16') if r['parent_id'] == row['id']]
    assert len(notes) == 1 and notes[0]['action'] == 'noted'
    # stream line + deep view + UI rows all surface the effective reason
    text, ok = self_tools._read_ledger('s16')
    assert ok and '(reason: tuned her opener)' in text
    text, ok = self_tools._read_ledger('s16', ids=[row['id']])
    assert ok and 'noted later — reason: tuned her opener' in text
    ui = self_routes.get_ledger(query={'scope': 's16'})
    ui_row = [r for r in ui['rows'] if r['id'] == row['id']][0]
    assert ui_row['detail']['reason'] == 'tuned her opener'


def test_patch_reason_clear_via_note(palace):
    from plugins.mindpalace.routes import self_routes
    pt.set_scope_resident('s17', prompt='sapph-first')
    _edit_and_flush(_mono(reason='original why'))
    row = _rows('s17', layer='prompt')[0]
    self_routes.patch_ledger_reason(lid=row['id'], body={'scope': 's17',
                                                         'reason': ''})
    ui = self_routes.get_ledger(query={'scope': 's17'})
    ui_row = [r for r in ui['rows'] if r['id'] == row['id']][0]
    assert 'reason' not in (ui_row['detail'] or {})   # cleared note wins


def test_patch_reason_refuses_ai_rows_and_wrong_scope(palace):
    from plugins.mindpalace.routes import self_routes
    rid = lg.record('s18', 'ai', 'edited', layer='prompt',
                    summary='her own change: her own why')
    out, code = self_routes.patch_ledger_reason(lid=rid, body={'scope': 's18',
                                                               'reason': 'x'})
    assert code == 403
    out, code = self_routes.patch_ledger_reason(lid=rid, body={'scope': 'other',
                                                               'reason': 'x'})
    assert code == 404


# ─── Reason-only events (the ✓ commit, 2026-07-23) ───────────────────────────
# A why typed after the last content keystroke produces no saver diff — it
# arrives as kind='reason' and must land whether the session is still open
# or she already flushed it by reading.

def test_reason_event_sets_open_sessions_reason(palace):
    pt.set_scope_resident('s20', prompt='sapph-first')
    pa.handle(_mono(before='v1', after='v2'))              # no reason yet
    pa.handle({'kind': 'reason', 'name': 'sapph-first',
               'actor': 'user', 'reason': 'typed after the edit'})
    pa.flush(force=True)
    rows = _rows('s20', layer='prompt')
    assert len(rows) == 1
    assert rows[0]['detail']['reason'] == 'typed after the edit'
    assert rows[0]['summary'].endswith(': typed after the edit')


def test_reason_event_after_flush_lands_as_note(palace):
    from plugins.mindpalace.tools import self_tools
    pt.set_scope_resident('s21', prompt='sapph-first')
    pa.handle(_mono(before='v1', after='v2'))
    pa.flush(force=True)                                   # she read mid-session
    row = _rows('s21', layer='prompt')[0]
    assert 'no reason given' in row['summary']
    pa.handle({'kind': 'reason', 'name': 'sapph-first',
               'actor': 'user', 'reason': 'the late why'})
    notes = [r for r in _rows('s21') if r['parent_id'] == row['id']]
    assert len(notes) == 1 and notes[0]['action'] == 'noted'
    text, ok = self_tools._read_ledger('s21')
    assert ok and '(reason: the late why)' in text
    # idempotent: the same why arriving again adds no second note
    pa.handle({'kind': 'reason', 'name': 'sapph-first',
               'actor': 'user', 'reason': 'the late why'})
    assert len([r for r in _rows('s21') if r['parent_id'] == row['id']]) == 1


def test_reason_event_ignores_stale_rows(palace):
    pt.set_scope_resident('s22', prompt='sapph-first')
    _edit_and_flush(_mono(before='v1', after='v2'))
    with pt._get_connection() as conn:                     # age the row out
        conn.execute("UPDATE ledger SET ts = '2020-01-01T00:00:00+00:00' "
                     "WHERE scope = 's22'")
        conn.commit()
    pa.handle({'kind': 'reason', 'name': 'sapph-first',
               'actor': 'user', 'reason': 'way too late'})
    rows = _rows('s22')
    assert all(r['action'] != 'noted' for r in rows)       # ✏ is the old-row path
