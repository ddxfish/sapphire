"""Run ALL / drain mode (2026-07-19): empty a pass kind's whole queue in
back-to-back batches, fresh chat each, daily cap bypassed, no counter spend,
stoppable, no-progress guard.

Package-path imports on purpose (see test_mindpalace_metadata's docstring).
"""
import re

import pytest

import core.chat.function_manager as fm
from plugins.mindpalace.tools import palace_tools as pt
from plugins.mindpalace.tools import librarian
from plugins.mindpalace.tools import librarian_tools as lt


class _FakeEmbedder:
    provider_id = "fake"

    @property
    def available(self):
        return False

    def embed(self, texts, prefix="search_document"):
        return None


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    fm.tool_context.set(None)
    # Never let a stray flag leak between tests.
    librarian._drain_active = False
    librarian._drain_stop = False
    with librarian._state_lock:
        librarian._state['drain'] = None
        librarian._state['running'] = False
    yield
    fm.tool_context.set(None)
    librarian._drain_active = False


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


def _passes_today(kind, scope='default'):
    row = next((r for r in librarian.get_status(scope)['scopes']
                if r['pass'] == kind), None)
    return row['passes_today'] if row else 0


# ─── queue depth ─────────────────────────────────────────────────────────────

def test_queue_depth_matches_selector(palace):
    for i in range(5):
        cid = _save(f'event on the 12th number {i}')
        with pt._get_connection() as conn:
            m = {'refers_to_time': ['the 12th']}
            conn.execute('UPDATE chunks SET meta=? WHERE id=?',
                         (__import__('json').dumps(m), cid))
            conn.commit()
    assert librarian._queue_depth('default', 'dates') == 5
    assert librarian._queue_depth('default', 'self') == 0    # not a queue


# ─── the drain loop (workers mocked → deterministic) ─────────────────────────

def test_drain_empties_queue_fresh_chat_no_cap_spend(palace, monkeypatch):
    # Seed 45 dates candidates → 3 batches of 20/20/5.
    import json
    ids = []
    for i in range(45):
        cid = _save(f'happened on day {i}')
        with pt._get_connection() as conn:
            conn.execute('UPDATE chunks SET meta=? WHERE id=?',
                         (json.dumps({'refers_to_time': ['a day']}), cid))
            conn.commit()
        ids.append(cid)

    seen_chats = []

    def fake_worker(scope, what, kind):
        # Mimic a real batch: stamp temporal_at on the newest 20 candidates,
        # then _finish (clears running) like every real worker.
        with librarian._state_lock:
            seen_chats.append(librarian._state['chat'])
        with pt._get_connection() as conn:
            cur = conn.cursor()
            batch = librarian.build_temporal_batch(cur, scope, 20)
            for row in batch:
                m = json.loads(cur.execute('SELECT meta FROM chunks WHERE id=?',
                                           (row[0],)).fetchone()[0] or '{}')
                m['temporal_at'] = pt._now()
                cur.execute('UPDATE chunks SET meta=? WHERE id=?',
                            (json.dumps(m), row[0]))
            librarian._record_pass(cur, scope, 'dates',
                                   {'presented': len(batch), 'handled': len(batch),
                                    'ledger': []})
            conn.commit()
        librarian._finish(f"batch of {len(batch)}")

    monkeypatch.setattr(librarian, '_worker', fake_worker)
    # Pin per_chat=1: this test asserts the classic fresh-chat-every-batch
    # contract (rolling chats have their own test below).
    monkeypatch.setattr(librarian, '_settings', lambda: {
        'batch': 20, 'per_msg': 10, 'per_day': 3,
        'drain_chat_batches': 1, 'model': ''})
    # Run the loop synchronously for a deterministic assert.
    librarian._drain_active = False
    librarian._drain_loop('default', 'all', 'dates')

    assert librarian._queue_depth('default', 'dates') == 0     # fully drained
    assert len(seen_chats) == 3                                # 20 + 20 + 5
    assert len(set(seen_chats)) == 3                           # a FRESH chat each
    assert all(c.endswith(('-c1', '-c2', '-c3')) for c in seen_chats)
    assert _passes_today('dates') == 0                         # cap NOT spent
    assert librarian._state['drain'] is None                  # cleaned up
    assert librarian._drain_active is False


def test_drain_rolling_chats_group_batches(palace, monkeypatch):
    """librarian_drain_batches_per_chat=2 (Krem, 2026-07-19): batches 1-2
    share one rolling chat, batch 3 starts a fresh one — accumulated context
    for judgment passes without the old whole-drain balloon."""
    import json
    for i in range(45):
        cid = _save(f'rolled on day {i}')
        with pt._get_connection() as conn:
            conn.execute('UPDATE chunks SET meta=? WHERE id=?',
                         (json.dumps({'refers_to_time': ['a day']}), cid))
            conn.commit()

    seen_chats = []

    def fake_worker(scope, what, kind):
        with librarian._state_lock:
            seen_chats.append(librarian._state['chat'])
        with pt._get_connection() as conn:
            cur = conn.cursor()
            batch = librarian.build_temporal_batch(cur, scope, 20)
            for row in batch:
                m = json.loads(cur.execute('SELECT meta FROM chunks WHERE id=?',
                                           (row[0],)).fetchone()[0] or '{}')
                m['temporal_at'] = pt._now()
                cur.execute('UPDATE chunks SET meta=? WHERE id=?',
                            (json.dumps(m), row[0]))
            conn.commit()
        librarian._finish(f"batch of {len(batch)}")

    monkeypatch.setattr(librarian, '_worker', fake_worker)
    monkeypatch.setattr(librarian, '_settings', lambda: {
        'batch': 20, 'per_msg': 10, 'per_day': 3,
        'drain_chat_batches': 2, 'model': ''})
    librarian._drain_active = False
    librarian._drain_loop('default', 'all', 'dates')

    assert len(seen_chats) == 3                    # 20 + 20 + 5
    assert seen_chats[0] == seen_chats[1]          # batches 1-2 share the chat
    assert seen_chats[0].endswith('-c1')
    assert seen_chats[2] != seen_chats[0]          # batch 3 rolls to a new one
    assert seen_chats[2].endswith('-c2')


def test_drain_stops_on_no_progress(palace, monkeypatch):
    import json
    for i in range(30):
        cid = _save(f'stuck day {i}')
        with pt._get_connection() as conn:
            conn.execute('UPDATE chunks SET meta=? WHERE id=?',
                         (json.dumps({'refers_to_time': ['a day']}), cid))
            conn.commit()
    calls = {'n': 0}

    def stuck_worker(scope, what, kind):
        calls['n'] += 1
        librarian._finish("did nothing")     # degraded batch — stamps nothing

    monkeypatch.setattr(librarian, '_worker', stuck_worker)
    librarian._drain_loop('default', 'all', 'dates')
    assert calls['n'] == 1                     # one no-progress batch → STOP, no spin
    assert librarian._queue_depth('default', 'dates') == 30


def test_drain_stop_flag_halts_between_batches(palace, monkeypatch):
    import json
    for i in range(40):
        cid = _save(f'day {i}')
        with pt._get_connection() as conn:
            conn.execute('UPDATE chunks SET meta=? WHERE id=?',
                         (json.dumps({'refers_to_time': ['a day']}), cid))
            conn.commit()

    def worker_then_stop(scope, what, kind):
        with pt._get_connection() as conn:
            cur = conn.cursor()
            for row in librarian.build_temporal_batch(cur, scope, 20):
                m = json.loads(cur.execute('SELECT meta FROM chunks WHERE id=?',
                                           (row[0],)).fetchone()[0] or '{}')
                m['temporal_at'] = pt._now()
                cur.execute('UPDATE chunks SET meta=? WHERE id=?',
                            (json.dumps(m), row[0]))
            conn.commit()
        librarian.drain_stop()                # ask to stop after this batch
        librarian._finish("batch")

    monkeypatch.setattr(librarian, '_worker', worker_then_stop)
    librarian._drain_loop('default', 'all', 'dates')
    assert librarian._queue_depth('default', 'dates') == 20    # 1 batch done, stopped


# ─── guards ──────────────────────────────────────────────────────────────────

def test_drain_refuses_self_and_unknown(palace):
    msg, ok = librarian.drain('default', kind='self')
    assert not ok and 'self pass' in msg
    msg, ok = librarian.drain('default', kind='bogus')
    assert not ok and 'Unknown' in msg


def test_drain_refuses_when_running(palace):
    with librarian._state_lock:
        librarian._state['running'] = True
    try:
        msg, ok = librarian.drain('default', kind='dates')
        assert not ok and 'already running' in msg
    finally:
        with librarian._state_lock:
            librarian._state['running'] = False


def test_check_caps_bypassed_only_during_drain(palace):
    cfg = {'per_day': 3, 'batch': 20, 'per_msg': 10, 'model': ''}
    with pt._get_connection() as conn:
        cur = conn.cursor()
        # Spend the cap.
        for _ in range(3):
            librarian._record_pass(cur, 'default', 'dates',
                                   {'presented': 0, 'handled': 0, 'ledger': []})
        conn.commit()
        ok, why = librarian._check_caps(cur, 'default', cfg, kind='dates')
        assert not ok and 'cap reached' in why.lower()
        librarian._drain_active = True
        try:
            ok, why = librarian._check_caps(cur, 'default', cfg, kind='dates')
            assert ok and why is None      # drain authorizes past the cap
        finally:
            librarian._drain_active = False
