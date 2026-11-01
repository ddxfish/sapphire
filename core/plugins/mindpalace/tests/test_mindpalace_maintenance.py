"""Maintenance actions (2026-07-15) — self-serve rescue for the alpha toggles.

Every action is an UPDATE on metadata columns/keys: memory content is never
touched and nothing is deleted, by construction. Package-path imports on
purpose (see test_mindpalace_metadata's docstring).
"""
import json
import re

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


def _save(content, scope='default'):
    msg, ok = pt._save_memory(content, scope)
    assert ok, msg
    return int(re.search(r'ID: (\d+)', msg).group(1))


def _set(cid, **cols):
    meta = cols.pop('meta', None)
    with pt._get_connection() as conn:
        if meta is not None:
            conn.execute('UPDATE chunks SET meta = ? WHERE id = ?',
                         (json.dumps(meta), cid))
        for k, v in cols.items():
            conn.execute(f'UPDATE chunks SET {k} = ? WHERE id = ?', (v, cid))
        conn.commit()


def _row(cid, cols='importance, content, meta'):
    with pt._get_connection() as conn:
        return conn.execute(f'SELECT {cols} FROM chunks WHERE id = ?',
                            (cid,)).fetchone()


# ─── reset_importance ────────────────────────────────────────────────────────

def test_reset_importance_restores_known_good_state(palace):
    rated = _save('librarian rated this 0.7')
    _set(rated, importance=0.7)
    unrated = _save('never rated')
    fav = _save('favorite that drifted somehow')
    _set(fav, importance=0.6, favorite=1)
    perm = _save('a permanent goal')
    _set(perm, importance=0.5, meta={'permanent': True})
    identity = _save('I am the one who tends the lake house')
    _set(identity, importance=0.95, label='self-sheet',
         meta={'section': 'identity'})

    out = browse.maintenance(body={'action': 'reset_importance', 'scope': 'default'})
    assert out['success'] and out['cleared'] == 1 and out['restored'] == 2

    assert _row(rated)[0] is None                        # rating cleared
    assert _row(unrated)[0] is None                      # untouched
    assert _row(fav)[0] == pytest.approx(0.95)           # favorite re-anchored
    assert _row(perm)[0] == pytest.approx(0.95)          # permanent re-anchored
    assert _row(identity)[0] == pytest.approx(0.95)      # sheet pin survives (live-found 2026-07-15)
    assert _row(rated)[1] == 'librarian rated this 0.7'  # content never touched


def test_reset_importance_scoped_and_idempotent(palace):
    mine = _save('in default')
    _set(mine, importance=0.4)
    other = _save('in work', scope='work')
    _set(other, importance=0.4)

    out = browse.maintenance(body={'action': 'reset_importance', 'scope': 'default'})
    assert out['cleared'] == 1
    assert _row(other)[0] == pytest.approx(0.4)          # other scope untouched

    out = browse.maintenance(body={'action': 'reset_importance', 'scope': 'default'})
    assert out['cleared'] == 0 and out['restored'] == 0  # second run: no-op


# ─── restore_retired ─────────────────────────────────────────────────────────

def test_restore_retired_skips_atomize_and_merge(palace):
    pruned = _save('retired as noise')
    _set(pruned, meta={'pruned_at': 'x', 'pruned_reason': 'noise', 'librarian_at': 'x'})
    atomized = _save('was split into parts')
    _set(atomized, meta={'pruned_at': 'x', 'atomized_into': [99]})
    merged = _save('was folded into another')
    _set(merged, meta={'pruned_at': 'x', 'merged_into': 98})

    out = browse.maintenance(body={'action': 'restore_retired', 'scope': 'default'})
    assert out['success'] and out['restored'] == 1 and out['skipped'] == 2

    m = json.loads(_row(pruned)[2])
    assert 'pruned_at' not in m and 'pruned_reason' not in m
    assert m.get('librarian_at')                          # review stamp survives
    assert json.loads(_row(atomized)[2]).get('pruned_at')  # stays retired
    assert json.loads(_row(merged)[2]).get('pruned_at')    # stays retired

    # Restored memory is back in her recall.
    assert f'[{pruned}]' in pt._search_memory('retired noise', 'default')[0]


# ─── guardrails ──────────────────────────────────────────────────────────────

def test_maintenance_rejects_bad_input(palace):
    out, code = browse.maintenance(body={'action': 'reset_importance'})
    assert code == 400 and 'scope' in out['error']
    out, code = browse.maintenance(body={'action': 'delete_everything',
                                         'scope': 'default'})
    assert code == 400 and 'Unknown' in out['error']


# ─── import_v1 ───────────────────────────────────────────────────────────────

def _fake_v1(tmp_path):
    """Minimal classic-system fixture: <root>/user/memory.db + a config module
    stand-in whose __file__ anchors _source_path there."""
    import sqlite3
    root = tmp_path / 'v1root'
    (root / 'user').mkdir(parents=True)
    db = root / 'user' / 'memory.db'
    conn = sqlite3.connect(db)
    conn.execute('CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT, '
                 'timestamp TEXT, scope TEXT, label TEXT, private_key TEXT)')
    conn.execute("INSERT INTO memories (content, timestamp, scope) VALUES "
                 "('the first classic memory', '2025-01-02 03:04:05', 'default')")
    conn.execute("INSERT INTO memories (content, timestamp, scope) VALUES "
                 "('a memory from another scope', '2025-02-03 04:05:06', 'imported-scope')")
    conn.commit()
    conn.close()

    class _Cfg:
        __file__ = str(root / 'config.py')
    return db, _Cfg


def _sha(path):
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_import_v1_copies_and_never_touches_source(palace, tmp_path, monkeypatch):
    db, cfg = _fake_v1(tmp_path)
    monkeypatch.setattr(browse, '_config', lambda: cfg)
    before = _sha(db)

    out = browse.maintenance(body={'action': 'import_v1', 'scope': 'default'})
    assert out['success'] and 'memories: 2 copied' in out['summary']
    assert 'NOT modified' in out['summary']
    assert _sha(db) == before                      # v1 byte-identical

    with pt._get_connection() as conn:
        rows = conn.execute(
            "SELECT scope, content FROM chunks WHERE layer='events' "
            "AND source='memory v2' ORDER BY id").fetchall()
    assert rows == [('default', 'the first classic memory'),
                    ('imported-scope', 'a memory from another scope')]

    # Idempotent: second run copies zero, still read-only.
    out = browse.maintenance(body={'action': 'import_v1', 'scope': 'default'})
    assert '0 copied, 2 already imported' in out['summary']
    assert _sha(db) == before


# ─── generate_metadata ───────────────────────────────────────────────────────

def test_generate_metadata_stamps_and_reseeds_edges(palace):
    msg, ok = pt._save_memory('a fact about her', 'default',
                              layer='entities', entity='Zebra')
    assert ok, msg
    cid = _save('spoke with Zebra about the lake')
    with pt._get_connection() as conn:
        eid = conn.execute("SELECT id FROM entities WHERE name='Zebra'").fetchone()[0]
        conn.execute('UPDATE chunks SET meta = NULL WHERE id = ?', (cid,))
        conn.execute("DELETE FROM edges WHERE src_type='chunk' AND src_id = ?", (cid,))
        conn.commit()

    out = browse.maintenance(body={'action': 'generate_metadata', 'scope': 'default'})
    assert out['success'] and out['stamped'] >= 1

    m = json.loads(_row(cid)[2])
    assert m.get('md_v')                              # mechanical stamp is back
    with pt._get_connection() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM edges WHERE src_type='chunk' AND src_id=? "
            "AND dst_type='entity' AND dst_id=? AND kind='mentions'",
            (cid, eid)).fetchone()[0]
    assert n == 1                                     # mention edge re-seeded


# ─── wipe_scope ──────────────────────────────────────────────────────────────

def test_wipe_scope_requires_typed_confirm(palace):
    cid = _save('precious data')
    out, code = browse.maintenance(body={'action': 'wipe_scope', 'scope': 'default'})
    assert code == 400 and 'confirm' in out['error']
    out, code = browse.maintenance(body={'action': 'wipe_scope', 'scope': 'default',
                                         'confirm': 'defualt'})   # typo ≠ match
    assert code == 400
    assert _row(cid)[1] == 'precious data'            # nothing deleted


def test_wipe_scope_deletes_only_that_scope(palace):
    msg, ok = pt._save_memory('Zebra is a person here', 'default',
                              layer='entities', entity='Zebra')
    assert ok, msg
    gone = _save('mentions Zebra so an edge exists')
    kept = _save('memory in the other scope', scope='work')

    out = browse.maintenance(body={'action': 'wipe_scope', 'scope': 'default',
                                   'confirm': 'default'})
    assert out['success'] and out['deleted_chunks'] >= 2
    assert out['deleted_entities'] == 1 and out['deleted_edges'] >= 1

    with pt._get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM chunks WHERE scope='default'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM entities WHERE scope='default'").fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM edges').fetchone()[0] == 0
    assert _row(kept)[1] == 'memory in the other scope'   # other scope intact

    # The scope is immediately usable again — FTS index survived the wipe.
    cid = _save('fresh start after the wipe')
    assert f'[{cid}]' in pt._search_memory('fresh start wipe', 'default')[0]


# ─── redate actions (2026-07-16) — the date-tuning loop ──────────────────────

def test_redate_regex_reanchors_and_keeps_librarian_verdicts(palace):
    stale = _save('we leave tomorrow')             # floor stamps vs today
    # Noon UTC — same civil date in any nearby zone (midnight UTC would BE
    # the evening-save timezone bug the anchor fix addresses).
    _set(stale, created='2026-01-01T12:00:00+00:00')   # …but it was saved in January
    ruled = _save('party on Dec 25 as she said')
    _set(ruled, meta={'refers_to_time': ['dec 25'], 'temporal_at': 'x',
                      'event_dates': ['2026-12-25'], 'event_date_src': 'librarian'})
    plain = _save('nothing dated here')

    out = browse.maintenance(body={'action': 'redate_regex', 'scope': 'default'})
    assert out['success'] and out['stamped'] >= 1 and out['kept'] == 1

    m = json.loads(_row(stale)[2])
    assert m['event_dates'] == ['2026-01-02']      # re-anchored to ITS created
    assert m['event_date_src'] == 'regex'
    mr = json.loads(_row(ruled)[2])
    assert mr['event_dates'] == ['2026-12-25']     # librarian verdict untouched
    assert mr['event_date_src'] == 'librarian'
    assert 'event_dates' not in json.loads(_row(plain)[2])


def test_redate_model_requires_librarian_and_requeues(palace, monkeypatch):
    from plugins.mindpalace.tools import librarian
    ruled = _save('meeting on the 12th she said')
    _set(ruled, meta={'refers_to_time': ['the 12th'], 'temporal_at': 'x',
                      'event_dates': ['2026-08-12'], 'event_date_src': 'librarian'})

    monkeypatch.setattr(librarian, '_enabled', lambda: False)
    out, code = browse.maintenance(body={'action': 'redate_model', 'scope': 'default'})
    assert code == 400 and 'disabled' in out['error']
    assert json.loads(_row(ruled)[2]).get('temporal_at')   # nothing reopened

    monkeypatch.setattr(librarian, '_enabled', lambda: True)
    started = []
    monkeypatch.setattr(librarian, 'start',
                        lambda scope, what='all', kind='sort':
                        (started.append((scope, kind)) or ('pass started', True)))
    out = browse.maintenance(body={'action': 'redate_model', 'scope': 'default'})
    assert out['success'] and out['requeued'] == 1
    assert started == [('default', 'dates')]
    m = json.loads(_row(ruled)[2])
    assert 'temporal_at' not in m and 'event_date_src' not in m
    assert m['event_dates'] == ['2026-08-12']      # current-best kept until re-ruled


# ─── entity rename (modal pencil, 2026-07-16) ────────────────────────────────

def test_entity_rename_validates_and_ledgers(palace):
    msg, ok = pt._save_memory('a fact', 'default', layer='entities', entity='Zebra')
    assert ok, msg
    msg, ok = pt._save_memory('b fact', 'default', layer='entities', entity='Yak')
    assert ok, msg
    with pt._get_connection() as conn:
        zid = conn.execute("SELECT id FROM entities WHERE name='Zebra'").fetchone()[0]

    out, code = browse.update_entity(eid=zid, body={'name': ''})
    assert code == 400
    out, code = browse.update_entity(eid=zid, body={'name': 'yak'})   # NOCASE dup
    assert code == 409

    out = browse.update_entity(eid=zid, body={'name': 'Zed'})
    assert out['success'] and out['name'] == 'Zed'
    with pt._get_connection() as conn:
        assert conn.execute('SELECT name FROM entities WHERE id=?', (zid,)).fetchone()[0] == 'Zed'
        led = conn.execute("SELECT summary FROM ledger WHERE action='update' "
                           "ORDER BY id DESC LIMIT 1").fetchone()
    assert led and 'Zebra' in led[0] and 'Zed' in led[0]

    # Save-time matcher now speaks the new name.
    cid = _save('talked with Zed about the fence')
    with pt._get_connection() as conn:
        n = conn.execute("SELECT COUNT(*) FROM edges WHERE src_id=? AND dst_id=? "
                         "AND kind='mentions'", (cid, zid)).fetchone()[0]
    assert n == 1


# ─── wipe_dates (the danger-zone date reset, 2026-07-24) ─────────────────────

def test_wipe_dates_clears_every_verdict_and_keeps_refers(palace):
    """Full date reset: regex-dated, librarian-ruled, and recurring all wiped
    in one pass; refers_to_time survives (it gates the rebuild queue) and
    content/other meta are untouched. The wipe lands in the ledger."""
    rx = _save('lunch on 2026-08-01 with Zed')
    _set(rx, meta={'md_v': 2, 'refers_to_time': ['2026-08-01'],
                   'event_dates': ['2026-08-01'], 'event_date_src': 'regex'})
    lib = _save('the regatta is next summer')
    _set(lib, meta={'md_v': 2, 'refers_to_time': ['next summer'],
                    'event_dates': ['2027-07-01'], 'event_date_src': 'librarian',
                    'temporal_at': '2026-07-20T00:00:00+00:00'})
    rec = _save('coffee every sunday morning')
    _set(rec, meta={'md_v': 2, 'recurring_dates': ['weekly:sun']})
    plain = _save('no dates here at all')
    _set(plain, meta={'md_v': 2, 'stats': {'words': 5}})

    out = browse.maintenance(body={'action': 'wipe_dates', 'scope': 'default',
                                   'confirm': 'default'})
    assert out.get('success') and out.get('wiped') == 3

    for cid in (rx, lib, rec):
        meta = json.loads(_row(cid)[2])
        for key in ('event_dates', 'event_date_src', 'temporal_at',
                    'recurring_dates'):
            assert key not in meta, f'{key} survived on {cid}'
    assert json.loads(_row(rx)[2])['refers_to_time'] == ['2026-08-01']
    assert json.loads(_row(plain)[2]) == {'md_v': 2, 'stats': {'words': 5}}
    with pt._get_connection() as conn:
        led = conn.execute(
            "SELECT summary FROM ledger WHERE scope='default' AND "
            "action='maintenance' ORDER BY id DESC LIMIT 1").fetchone()
    assert led and 'wiped all derived dates on 3' in led[0]


def test_wipe_dates_requires_typed_confirm(palace):
    cid = _save('dated thing on 2026-08-01')
    _set(cid, meta={'md_v': 2, 'event_dates': ['2026-08-01'],
                    'event_date_src': 'regex'})
    out, code = browse.maintenance(body={'action': 'wipe_dates',
                                         'scope': 'default'})
    assert code == 400 and 'confirm' in out['error']
    out, code = browse.maintenance(body={'action': 'wipe_dates',
                                         'scope': 'default',
                                         'confirm': 'wrong'})
    assert code == 400
    assert 'event_dates' in json.loads(_row(cid)[2])   # nothing wiped


def test_wipe_dates_then_redate_regex_rebuilds_the_floor(palace):
    """The rebuild path Krem will click: wipe → redate_regex restamps from
    content, including chunks the librarian had ruled (verdict gone = floor
    applies again)."""
    cid = _save('dinner on 2026-09-15 at the lake house')
    _set(cid, meta={'md_v': 2, 'refers_to_time': ['2026-09-15'],
                    'event_dates': ['1999-01-01'],       # the poisoned date
                    'event_date_src': 'librarian',
                    'temporal_at': '2026-07-20T00:00:00+00:00'})
    browse.maintenance(body={'action': 'wipe_dates', 'scope': 'default',
                             'confirm': 'default'})
    out = browse.maintenance(body={'action': 'redate_regex', 'scope': 'default'})
    assert out.get('success')
    meta = json.loads(_row(cid)[2])
    assert meta.get('event_dates') == ['2026-09-15']     # rebuilt from content
    assert meta.get('event_date_src') == 'regex'
    assert 'temporal_at' not in meta                     # verdict stays open


# ─── strip_stamps — the one action that edits text (2026-10-31) ──────────────

SAVED = '2026-03-05T14:30:00+00:00'


def _as_written(monkeypatch):
    monkeypatch.setattr(pt, '_strip_stamps_enabled', lambda: False)


def test_strip_stamps_preview_run_and_revert(palace, monkeypatch):
    from plugins.mindpalace.tools import librarian_tools as lt
    _as_written(monkeypatch)
    stale = _save('March 3 2026: today we fixed the bug')
    _set(stale, created=SAVED, meta={'event_dates': ['2026-03-03'], 'event_date_src': 'librarian',
                                     'temporal_at': 'x', 'when': '2026-03-03', 'when_src': 'librarian'})
    same = _save('Core memory — March 5 2026: the boat')
    _set(same, created=SAVED)
    birthday = _save('July 10 2026: my birthday party was great')
    _set(birthday, created='2026-07-10T20:00:00+00:00')
    history = _save("1997-05-12: Krem's birth date")
    _set(history, created=SAVED)
    msg, ok = pt._save_memory('March 4 2026: a keyed one', 'default', private_key='k')
    keyed = int(re.search(r'ID: (\d+)', msg).group(1))
    _set(keyed, created=SAVED)

    p = browse.maintenance({'action': 'strip_stamps', 'scope': 'default', 'preview': True})
    assert p['preview'] and p['stripped'] == 2 and p['stale'] == 1 and p['same_day'] == 1
    assert p['kept_anniversary'] == 1 and p['private'] == 1 and p['run_id'] is None
    assert _row(stale)[1].startswith('March 3 2026:')          # preview writes nothing

    r = browse.maintenance({'action': 'strip_stamps', 'scope': 'default'})
    assert r['stripped'] == 2 and r['run_id']
    assert _row(stale)[1] == 'today we fixed the bug'
    assert _row(same)[1] == 'Core memory: the boat'
    assert _row(birthday)[1].startswith('July 10 2026:')         # the anniversary lives on the stamp
    assert _row(history)[1].startswith('1997-05-12:')
    assert _row(keyed)[1].startswith('March 4 2026:')
    # the old verdict went with the old words: "today" is now the save day
    m = json.loads(_row(stale)[2])
    assert m['event_dates'] == ['2026-03-05'] and m['event_date_src'] == 'regex'
    assert 'temporal_at' not in m and 'when' not in m
    with pt._get_connection() as conn:
        run = conn.execute('SELECT actor, action, target FROM ledger WHERE id = ?',
                           (r['run_id'],)).fetchone()
        kids = conn.execute("SELECT action, detail FROM ledger WHERE parent_id = ?",
                            (r['run_id'],)).fetchall()
    assert run == ('user', 'unstamp', 'run') and len(kids) == 2
    assert all(a == 'unstamped' and json.loads(d)['before'].startswith(('March', 'Core'))
               for a, d in kids)

    # a row rewritten since is left alone; the rest go back word for word
    pt._update_memory(same, 'default', content='Core memory: the boat, revised')
    counts, err = lt.revert_run('default', r['run_id'])
    assert err is None and counts['content'] == 1 and counts['skipped'] == 1
    assert _row(stale)[1] == 'March 3 2026: today we fixed the bug'
    assert _row(same)[1] == 'Core memory: the boat, revised'
    assert lt.revert_run('default', r['run_id']) == (None, 'this run was already reverted')


def test_save_and_update_drop_her_today_stamp(palace):
    import calendar
    from datetime import datetime
    d = datetime.now()
    msg, ok = pt._save_memory(f'{calendar.month_name[d.month]} {d.day} {d.year}: today we fixed the bug',
                              'default')
    assert ok and 'Dropped the leading date' in msg
    cid = int(re.search(r'ID: (\d+)', msg).group(1))
    assert _row(cid)[1] == 'today we fixed the bug'
    msg, ok = pt._save_memory("1997-05-12: Krem's birth date", 'default')
    assert ok and 'Dropped' not in msg                          # history is content
    cid2 = _save('plain text')
    _set(cid2, created=SAVED)                                   # the edit anchors to the row's own day
    msg, ok = pt._update_memory(cid2, 'default', content='March 3 2026: now with a stamp')
    assert ok and 'Dropped the leading date (2026-03-03)' in msg
    assert _row(cid2)[1] == 'now with a stamp'


def test_strip_setting_off_stores_as_written(palace, monkeypatch):
    _as_written(monkeypatch)
    import calendar
    from datetime import datetime
    d = datetime.now()
    text = f'{calendar.month_name[d.month]} {d.day} {d.year}: today we fixed the bug'
    msg, ok = pt._save_memory(text, 'default')
    assert ok and 'Dropped' not in msg
    assert _row(int(re.search(r'ID: (\d+)', msg).group(1)))[1] == text
