"""Pre-campaign hardening batch (2026-07-19 scout sweep, Batch A+B):

- the groundskeeper latch releases even when a worker crashes pre-try
- _run_messages reports per-message success; degraded runs count as failure
- dedup stamps dedup_at ONLY for messages that completed (no false drain)
- a fully-failed pass spends no cap and writes no ledger
- set_event_dates empty verdict PRESERVES already-resolved dates
- mark_processed: string 'false' is false; importance=true (bool) refused
- atomize refuses non-list parts; promote caps entity names
- dates + mark verbs write ledger children now
- all sheet sections version (no more hard-delete on edit)
- embedder-down backfill does NOT latch
- get_messages_for_llm honors a per-call context_limit override

Package-path imports on purpose (see test_mindpalace_metadata's docstring).
"""
import json
import re

import numpy as np
import pytest

import core.chat.function_manager as fm
from plugins.mindpalace.tools import palace_tools as pt
from plugins.mindpalace.tools import librarian
from plugins.mindpalace.tools import librarian_tools as lt
from plugins.mindpalace.tools import self_tools as st


class _FakeEmbedder:
    provider_id = "fake"

    @property
    def available(self):
        return False

    def embed(self, texts, prefix="search_document"):
        return None


@pytest.fixture(autouse=True)
def _clean_tool_context():
    fm.tool_context.set(None)
    lt.close_pass()
    yield
    fm.tool_context.set(None)
    lt.close_pass()


@pytest.fixture
def palace(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "_db_path", tmp_path / "mind.db", raising=False)
    monkeypatch.setattr(pt, "_db_initialized", False, raising=False)
    monkeypatch.setattr(pt, "_backfill_done", True, raising=False)
    monkeypatch.setattr(pt, "_get_embedder", lambda: _FakeEmbedder(), raising=False)
    monkeypatch.setattr(librarian, "_snapshots", {}, raising=False)
    monkeypatch.setattr(librarian, "_enabled", lambda: True)
    return pt


def _save(content, scope='default', **kw):
    msg, ok = pt._save_memory(content, scope, **kw)
    assert ok, msg
    return int(re.search(r'ID: (\d+)', msg).group(1))


def _meta(cid):
    with pt._get_connection() as conn:
        raw = conn.execute('SELECT meta FROM chunks WHERE id = ?',
                           (cid,)).fetchone()[0]
    return json.loads(raw) if raw else {}


def _set_vec(cid, vec):
    v = np.asarray(vec, dtype=np.float32)
    v = v / np.linalg.norm(v)
    with pt._get_connection() as conn:
        conn.execute('UPDATE chunks SET embedding = ?, embedding_provider = ?, '
                     'embedding_dim = ? WHERE id = ?',
                     (v.tobytes(), 'fake', v.shape[0], cid))
        conn.commit()


def _passes_today(kind, scope='default'):
    row = next((r for r in librarian.get_status(scope)['scopes']
                if r['pass'] == kind), None)
    return row['passes_today'] if row else 0


# ─── The latch ───────────────────────────────────────────────────────────────

def test_latch_releases_when_worker_crashes(palace, monkeypatch):
    def boom(scope):
        raise ImportError("module vanished mid-reload")
    monkeypatch.setattr(librarian, "_worker_dates", boom)
    librarian.run_blocking('default', kind='dates', chat='librarian-t')
    with librarian._state_lock:
        assert librarian._state['running'] is False        # slot released
        assert 'crashed' in (librarian._state['last_message'] or '')
    # And the NEXT pass can claim — the jam is gone.
    msg, ok = librarian.run_blocking('default', kind='sort', chat='librarian-t')
    assert 'already running' not in msg


# ─── Degraded visibility ─────────────────────────────────────────────────────

def test_run_messages_flags_errors_and_degraded(palace, monkeypatch):
    results = [{'success': True, 'errors': []},
               {'success': True, 'errors': [], 'degraded': 'tool exhaustion'},
               {'success': False, 'errors': ['boom']}]

    class _Exec:
        def run(self, task):
            return results.pop(0)

    class _Sys:
        class continuity_scheduler:
            executor = _Exec()

    import core.api_fastapi as api
    monkeypatch.setattr(api, "get_system", lambda: _Sys)
    monkeypatch.setattr(librarian, "_ensure_toolset_and_chat", lambda: None)
    monkeypatch.setattr(librarian, "_session_snapshot", lambda c, s: '')
    monkeypatch.setattr(librarian, "_persona_for_chat", lambda: 'sapphire')
    oks = librarian._run_messages('default', ['a', 'b', 'c'], {'model': ''},
                                  'librarian', 'Test pass', lambda g, p, t: g)
    assert oks == [True, False, False]


# ─── Dedup: no false drain ───────────────────────────────────────────────────

def _dedup_setup():
    a = _save('Zebra visited the house today')
    b = _save('Zebra came by the house for a visit')
    c = _save('completely unrelated soldering note')
    _set_vec(a, [1, 0, 0])
    _set_vec(b, [1, 0, 0])       # a+b cluster at 1.0
    _set_vec(c, [0, 1, 0])       # clean — no partner
    return a, b, c


def test_dedup_failed_message_leaves_clusters_unstamped(palace, monkeypatch):
    a, b, c = _dedup_setup()
    monkeypatch.setattr(librarian, "_run_messages",
                        lambda *args, **kw: [False])       # message degraded
    msg, ok = librarian.run_blocking('default', kind='dedup', chat='librarian-t')
    assert 'failed' in msg.lower() and 'rescan' in msg.lower()
    assert 'dedup_at' not in _meta(a) and 'dedup_at' not in _meta(b)
    assert 'dedup_at' in _meta(c)          # mechanical fact — no LLM needed
    assert _passes_today('dedup') == 0     # failed pass spends nothing


def test_dedup_completed_message_stamps_and_records(palace, monkeypatch):
    a, b, c = _dedup_setup()
    monkeypatch.setattr(librarian, "_run_messages",
                        lambda *args, **kw: [True])
    msg, ok = librarian.run_blocking('default', kind='dedup', chat='librarian-t')
    assert 'complete' in msg.lower()
    for cid in (a, b, c):
        assert 'dedup_at' in _meta(cid)
    assert _passes_today('dedup') == 1


def test_sort_failed_pass_spends_nothing(palace, monkeypatch):
    _save('an old memory awaiting review')
    monkeypatch.setattr(librarian, "_run_messages",
                        lambda *args, **kw: [False])
    msg, ok = librarian.run_blocking('default', kind='sort', chat='librarian-t')
    assert 'failed' in msg.lower() and 'requeues' in msg.lower()
    assert _passes_today('sort') == 0
    with pt._get_connection() as conn:      # no misleading pass ledger row
        n = conn.execute("SELECT COUNT(*) FROM ledger WHERE actor='librarian'"
                         ).fetchone()[0]
    assert n == 0


# ─── Dates: empty verdict preserves resolved dates ───────────────────────────

def test_dateless_verdict_keeps_existing_dates(palace):
    cid = _save('lunch on the 12th went well')
    with pt._get_connection() as conn:
        m = _meta(cid)
        m.update({'event_dates': ['2026-08-12'], 'event_date_src': 'regex'})
        conn.execute('UPDATE chunks SET meta=? WHERE id=?',
                     (json.dumps(m), cid))
        conn.commit()
    lt.open_pass('default', [cid], kind='dates')
    msg, ok = lt.execute('set_event_dates',
                         {'entries': [{'memory_id': cid, 'dates': []}]}, None)
    assert ok, msg
    m = _meta(cid)
    assert m['event_dates'] == ['2026-08-12']      # the resolver's data stands
    assert m['event_date_src'] == 'regex'
    assert m['temporal_at']                        # verdict still filed
    stats = lt.close_pass()
    actions = {c['action'] for c in stats['ledger']}
    assert 'dateless' in actions                   # and ledgered now


def test_dated_verdict_writes_ledger_child(palace):
    cid = _save('the launch is on the 20th')
    lt.open_pass('default', [cid], kind='dates')
    msg, ok = lt.execute('set_event_dates',
                         {'entries': [{'memory_id': cid,
                                       'dates': ['2026-08-20']}]}, None)
    assert ok, msg
    stats = lt.close_pass()
    assert any(c['action'] == 'dated' for c in stats['ledger'])


# ─── mark_processed hardening ────────────────────────────────────────────────

def test_mark_string_false_does_not_set_favorite(palace):
    cid = _save('an ordinary memory')
    lt.open_pass('default', [cid])
    msg, ok = lt.execute('mark_processed',
                         {'memory_id': cid, 'favorite': 'false'}, None)
    assert ok, msg
    with pt._get_connection() as conn:
        fav = conn.execute('SELECT favorite FROM chunks WHERE id=?',
                           (cid,)).fetchone()[0]
    assert fav == 0
    stats = lt.close_pass()
    assert any(c['action'] == 'marked' for c in stats['ledger'])


def test_mark_bool_importance_refused(palace):
    cid = _save('another ordinary memory')
    lt.open_pass('default', [cid])
    msg, ok = lt.execute('mark_processed',
                         {'memory_id': cid, 'importance': True}, None)
    assert not ok and 'number' in msg


# ─── atomize / promote guards ────────────────────────────────────────────────

def test_atomize_refuses_bare_string(palace):
    cid = _save('lunch')
    lt.open_pass('default', [cid])
    msg, ok = lt.execute('atomize_memory',
                         {'memory_id': cid, 'parts': 'lunch'}, None)
    assert not ok and 'LIST' in msg
    with pt._get_connection() as conn:      # nothing shredded
        n = conn.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]
    assert n == 1


def test_promote_refuses_huge_entity_name(palace):
    cid = _save('a fact about someone important')
    lt.open_pass('default', [cid])
    msg, ok = lt.execute('promote_memory',
                         {'memory_id': cid, 'layer': 'entities',
                          'entity': 'x' * 200}, None)
    assert not ok and 'too long' in msg


# ─── Sheet: every section versions ───────────────────────────────────────────

def test_relationships_edits_archive_not_delete(palace):
    st.write_section('default', 'relationships', 'Krem — builds the boat')
    st.write_section('default', 'relationships', 'Krem — sails the boat')
    with pt._get_connection() as conn:
        rows = conn.execute(
            "SELECT content, json_extract(meta,'$.superseded_at') FROM chunks "
            "WHERE json_extract(meta,'$.section') = 'relationships'").fetchall()
    assert len(rows) == 2                              # old version SURVIVES
    old = next(r for r in rows if r[1] is not None)
    assert 'builds the boat' in old[0]


def test_origin_edits_archive_not_delete(palace):
    st.write_section('default', 'origin', 'It began in a garage.')
    st.write_section('default', 'origin', 'It began with a promise.')
    with pt._get_connection() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE "
            "json_extract(meta,'$.section') = 'origin'").fetchone()[0]
    assert n == 2


# ─── Backfill latch ──────────────────────────────────────────────────────────

def test_backfill_does_not_latch_when_embedder_down(palace, monkeypatch):
    monkeypatch.setattr(pt, "_backfill_done", False, raising=False)
    _save('a row that will lack a vector')
    pt._backfill_embeddings()
    assert pt._backfill_done is False                  # retries next search


# ─── History: per-call context override ──────────────────────────────────────

def test_get_messages_for_llm_context_override():
    from core.chat.history import ConversationHistory
    ch = ConversationHistory()
    ch.messages = [{'role': 'user', 'content': 'x' * 8000},
                   {'role': 'assistant', 'content': 'y' * 8000},
                   {'role': 'user', 'content': 'the tail survives'}]
    small = ch.get_messages_for_llm(context_limit=600)
    big = ch.get_messages_for_llm(context_limit=1_000_000)
    assert len(big) == 3
    assert len(small) < 3
    assert small[-1]['content'] == 'the tail survives'
