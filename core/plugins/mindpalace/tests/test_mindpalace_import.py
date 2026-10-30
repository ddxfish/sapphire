"""v2 → v3 mind palace importer tests.

Synthetic old-format fixture DBs are built in tmp_path (never real user data).
palace_tools' mind.db path and the importer's source-DB anchor are both
redirected to tmp_path via monkeypatch, and palace module globals are reset
between tests so each test gets a fresh, isolated mind.db.

Coverage:
  - counts (copied / skipped / failed) per store
  - provenance triple carried byte-identical when all three present
  - all-or-nothing triple rule (embedding present but provider NULL → all NULL)
  - timestamps → ISO-8601 UTC, tz-aware, both source formats
  - layer / tier / entity mapping (people → entities row + tier-1 chunk)
  - knowledge (label, source, chunk_index) preserved
  - idempotent re-run copies 0
  - source DB bytes UNCHANGED after import (hash before/after)
  - scopes registered in mind_scopes
  - embedder unavailable in test env → import still succeeds
"""
import sys
import json
import types
import hashlib
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# conftest pre-registers the 'memory' scope; grab the ContextVar lazily.
import core.chat.function_manager as fm


# ─── Synthetic source-DB builders (real v2 schemas) ──────────────────────────

# A tiny, valid float32 blob (4 floats = 16 bytes). Byte-identity is what we
# assert, not that it's a "real" embedding.
_EMB_BLOB = bytes(range(16))


def _build_memory_db(path: Path):
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            timestamp TEXT,
            importance INTEGER DEFAULT 5,
            keywords TEXT,
            context TEXT,
            scope TEXT NOT NULL DEFAULT 'default',
            label TEXT,
            embedding BLOB,
            embedding_provider TEXT,
            embedding_dim INTEGER,
            private_key TEXT
        )
    ''')
    rows = [
        # id 1: SQLite CURRENT_TIMESTAMP format (space separator, UTC-naive)
        ("space-format memory", "2026-01-15 08:30:00", 5, "kw", "ctx",
         "default", "family", None, None, None, None),
        # id 2: T-separated legacy local-time isoformat, naive
        ("t-format memory", "2026-02-20T14:45:30", 5, "kw", "ctx",
         "work", "technical", None, None, None, None),
        # id 3: private_key row
        ("secret memory", "2026-03-01 12:00:00", 5, "kw", "ctx",
         "default", "opinions", None, None, None, "hunter2"),
        # id 4: full provenance triple present → carried byte-for-byte
        ("embedded memory", "2026-03-05 09:00:00", 5, "kw", "ctx",
         "default", "stories", _EMB_BLOB, "provX", 4, None),
        # id 5: embedding present but provider NULL → all-three-NULL rule
        ("half-embedded memory", "2026-03-06 10:00:00", 5, "kw", "ctx",
         "default", None, _EMB_BLOB, None, 4, None),
        # id 6: global scope (import copies it verbatim, no write-block)
        ("global memory", "2026-03-07 11:00:00", 5, "kw", "ctx",
         "global", None, None, None, None, None),
    ]
    c.executemany(
        'INSERT INTO memories (content, timestamp, importance, keywords, context, '
        'scope, label, embedding, embedding_provider, embedding_dim, private_key) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', rows)
    conn.commit()
    conn.close()


def _build_knowledge_db(path: Path):
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE people (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            relationship TEXT,
            phone TEXT,
            email TEXT,
            address TEXT,
            notes TEXT,
            scope TEXT NOT NULL DEFAULT 'default',
            embedding BLOB,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            email_whitelisted INTEGER DEFAULT 0,
            call_whitelisted INTEGER DEFAULT 0,
            embedding_provider TEXT,
            embedding_dim INTEGER
        )
    ''')
    people = [
        # multi-column descriptive text + whitelist flag → meta bag
        ("Krem", "creator", "555-1000", "krem@example.com", "Boat, someday",
         "loves Sapphire", "default", None, "2026-01-01 10:00:00",
         "2026-01-01 10:00:00", 1, 0, None, None),
        # duplicate-differing-case name in SAME scope → one entity only
        ("krem", "duplicate case", None, None, None, "second row",
         "default", None, "2026-01-02 10:00:00",
         "2026-01-02 10:00:00", 0, 0, None, None),
    ]
    c.executemany(
        'INSERT INTO people (name, relationship, phone, email, address, notes, '
        'scope, embedding, created_at, updated_at, email_whitelisted, '
        'call_whitelisted, embedding_provider, embedding_dim) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', people)

    c.execute('''
        CREATE TABLE knowledge_tabs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            type TEXT NOT NULL DEFAULT 'user',
            scope TEXT NOT NULL DEFAULT 'default',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(name, scope)
        )
    ''')
    c.execute("INSERT INTO knowledge_tabs (name, type, scope) VALUES (?, ?, ?)",
              ("Recipes", "user", "default"))
    # A chat's attached document: core's per-chat RAG, scoped by CHAT NAME.
    # Never memory — the importer must leave it in place.
    c.execute("INSERT INTO knowledge_tabs (name, type, scope) VALUES (?, ?, ?)",
              ("Documents", "user", "__rag__:secret chat"))

    c.execute('''
        CREATE TABLE knowledge_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tab_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            chunk_index INTEGER DEFAULT 0,
            source_filename TEXT,
            embedding BLOB,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            embedding_provider TEXT,
            embedding_dim INTEGER
        )
    ''')
    # 3 chunked entries sharing one source_filename (sub-chunk group)
    entries = [
        (1, "chunk zero", 0, "cookbook.pdf", None, "2026-01-03 10:00:00",
         "2026-01-03 10:00:00", None, None),
        (1, "chunk one", 1, "cookbook.pdf", None, "2026-01-03 10:00:00",
         "2026-01-03 10:00:00", None, None),
        (1, "chunk two", 2, "cookbook.pdf", None, "2026-01-03 10:00:00",
         "2026-01-03 10:00:00", None, None),
        (2, "attached pdf text", 0, "a.pdf", None, "2026-01-04 10:00:00",
         "2026-01-04 10:00:00", None, None),
    ]
    c.executemany(
        'INSERT INTO knowledge_entries (tab_id, content, chunk_index, '
        'source_filename, embedding, created_at, updated_at, embedding_provider, '
        'embedding_dim) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', entries)
    conn.commit()
    conn.close()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ─── Fixture: isolated palace + redirected source paths ──────────────────────

def _fake_config_for(tmp_path):
    # The importer computes Path(config.__file__).parent / "user" / X.
    return types.SimpleNamespace(__file__=str(tmp_path / "config.py"))


@pytest.fixture
def palace_env(tmp_path, monkeypatch):
    """Redirect palace mind.db to tmp_path, build synthetic source DBs there,
    reset palace module globals, and hand back a fake `config` whose __file__
    anchors the importer's source paths at tmp_path/user/."""
    from plugins.mindpalace.tools import palace_tools
    from plugins.mindpalace.tools import import_tools

    user_dir = tmp_path / "user"
    (user_dir / "memory").mkdir(parents=True)

    # Redirect palace destination DB.
    mind_db = user_dir / "memory" / "mind.db"
    monkeypatch.setattr(palace_tools, "_db_path", mind_db, raising=False)
    monkeypatch.setattr(palace_tools, "_db_initialized", False, raising=False)
    monkeypatch.setattr(palace_tools, "_backfill_done", False, raising=False)

    # Import now kicks a backfill thread — keep it off the REAL embedder.
    class _NoEmb:
        provider_id = "fake"
        available = False

        def embed(self, texts, prefix=""):
            return None
    monkeypatch.setattr(palace_tools, "_get_embedder", lambda: _NoEmb(),
                        raising=False)
    # The import also kicks a doc-edge walk over the Library in a daemon
    # thread; left live it races the NEXT test's fresh library path ("no
    # such table: documents", order-dependent). No Library here — no walk.
    from plugins.mindpalace.tools import library as _lib
    monkeypatch.setattr(_lib, "backfill_doc_edges", lambda: None)
    # The run posts a progress row and the nudge reads/dismisses rows in the
    # notification center — never the real user/notifications.json from a
    # test. Tests that care re-patch these to capture.
    import core.notify as _notify
    from contextlib import contextmanager

    class _H:
        def update(self, *a, **k): pass
        def set(self, *a, **k): pass
        def text(self, *a, **k): pass

    @contextmanager
    def _noprog(*a, **k):
        yield _H()
    monkeypatch.setattr(_notify, "progress", _noprog)
    monkeypatch.setattr(_notify, "notify", lambda *a, **k: None)
    monkeypatch.setattr(_notify, "rows", lambda **k: [])
    monkeypatch.setattr(_notify, "dismiss", lambda rid, **k: True)
    import_tools._run_state.update(running=False, result=None, finished=None,
                                   error=None, stage=None, done=0, total=0)
    # The routes resolve `config` themselves — pin them to THIS fixture's
    # fake. A route test that reached the real config scrubbed 28 rows from
    # the dev box's classic database (2026-10-30). Never again.
    from plugins.mindpalace.routes import import_routes
    monkeypatch.setattr(import_routes, "_config", lambda: fake_config)

    # Build synthetic source DBs.
    _build_memory_db(user_dir / "memory.db")
    _build_knowledge_db(user_dir / "knowledge.db")

    fake_config = _fake_config_for(tmp_path)

    # Activate the shared memory scope for the executor gate.
    fm.register_plugin_scope("memory", plugin_name="pytest-mindpalace")
    fm.scope_memory.set("default")

    return types.SimpleNamespace(
        tmp_path=tmp_path, user_dir=user_dir, mind_db=mind_db,
        config=fake_config, palace_tools=palace_tools, import_tools=import_tools,
    )


def _mind_conn(env):
    return sqlite3.connect(env.mind_db)


# ─── Tests ────────────────────────────────────────────────────────────────────

def test_import_all_counts(palace_env):
    env = palace_env
    out, ok = env.import_tools.execute("import_v2", {"what": "all"}, env.config)
    assert ok, out

    conn = _mind_conn(env)
    c = conn.cursor()
    # 6 memories (all valid) → events
    assert c.execute("SELECT COUNT(*) FROM chunks WHERE layer='events'").fetchone()[0] == 6
    # 2 people rows → both produce a tier-1 entities chunk; entity deduped
    assert c.execute("SELECT COUNT(*) FROM chunks WHERE layer='entities'").fetchone()[0] == 2
    # 3 knowledge entries
    assert c.execute("SELECT COUNT(*) FROM chunks WHERE layer='knowledge'").fetchone()[0] == 3
    conn.close()


def test_provenance_triple_byte_identical(palace_env):
    env = palace_env
    env.import_tools.execute("import_v2", {"what": "memories"}, env.config)

    conn = _mind_conn(env)
    c = conn.cursor()
    row = c.execute(
        "SELECT embedding, embedding_provider, embedding_dim FROM chunks "
        "WHERE content='embedded memory'").fetchone()
    conn.close()
    assert row[0] == _EMB_BLOB          # byte-for-byte
    assert row[1] == "provX"
    assert row[2] == 4


def test_all_or_nothing_triple(palace_env):
    env = palace_env
    env.import_tools.execute("import_v2", {"what": "memories"}, env.config)

    conn = _mind_conn(env)
    c = conn.cursor()
    # embedding present but provider NULL in source → all three land NULL.
    row = c.execute(
        "SELECT embedding, embedding_provider, embedding_dim FROM chunks "
        "WHERE content='half-embedded memory'").fetchone()
    conn.close()
    assert row == (None, None, None)


def test_timestamps_iso_utc_tzaware(palace_env):
    env = palace_env
    env.import_tools.execute("import_v2", {"what": "memories"}, env.config)

    conn = _mind_conn(env)
    c = conn.cursor()
    rows = c.execute("SELECT created FROM chunks WHERE layer='events'").fetchall()
    conn.close()
    assert rows
    for (created,) in rows:
        dt = datetime.fromisoformat(created)
        assert dt.tzinfo is not None, f"{created} is not tz-aware"
        # normalized to UTC → zero offset
        assert dt.utcoffset() == timezone.utc.utcoffset(None)

    # The space-format row was already UTC; verify exact conversion.
    conn = _mind_conn(env)
    c = conn.cursor()
    space_created = c.execute(
        "SELECT created FROM chunks WHERE content='space-format memory'").fetchone()[0]
    conn.close()
    assert space_created == "2026-01-15T08:30:00+00:00"


def test_people_entity_and_tier_mapping(palace_env):
    env = palace_env
    env.import_tools.execute("import_v2", {"what": "people"}, env.config)

    conn = _mind_conn(env)
    c = conn.cursor()
    # duplicate-case name → exactly ONE entity in the scope
    ents = c.execute(
        "SELECT COUNT(*) FROM entities WHERE name='Krem' COLLATE NOCASE "
        "AND scope='default'").fetchone()[0]
    assert ents == 1
    ekind = c.execute("SELECT kind FROM entities WHERE scope='default'").fetchone()[0]
    assert ekind == "person"

    # both people rows → tier-1 entities chunks, entity_id points at the entity
    chunks = c.execute(
        "SELECT tier, entity_id, layer FROM chunks WHERE layer='entities'").fetchall()
    assert len(chunks) == 2
    ent_id = c.execute("SELECT id FROM entities WHERE scope='default'").fetchone()[0]
    for tier, entity_id, layer in chunks:
        assert tier == 1
        assert entity_id == ent_id
        assert layer == "entities"

    # whitelist flags landed in meta, content built from descriptive columns
    krem_chunk = c.execute(
        "SELECT content, meta FROM chunks WHERE layer='entities' "
        "AND content LIKE '%krem@example.com%'").fetchone()
    conn.close()
    assert krem_chunk is not None
    meta = json.loads(krem_chunk[1])
    assert meta.get("email_whitelisted") == 1
    assert "Relationship: creator" in krem_chunk[0]


def test_knowledge_group_identity_preserved(palace_env):
    env = palace_env
    env.import_tools.execute("import_v2", {"what": "knowledge"}, env.config)

    conn = _mind_conn(env)
    c = conn.cursor()
    rows = c.execute(
        "SELECT chunk_index, source, label FROM chunks WHERE layer='knowledge' "
        "ORDER BY chunk_index").fetchall()
    conn.close()
    assert [r[0] for r in rows] == [0, 1, 2]           # chunk_index carried
    assert all(r[1] == "cookbook.pdf" for r in rows)   # shared source
    assert all(r[2] == "recipes" for r in rows)        # tab name lowercased → label


def test_idempotent_rerun_copies_zero(palace_env):
    env = palace_env
    env.import_tools.execute("import_v2", {"what": "all"}, env.config)

    conn = _mind_conn(env)
    total_first = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    ent_first = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    conn.close()

    out2, ok2 = env.import_tools.execute("import_v2", {"what": "all"}, env.config)
    assert ok2

    conn = _mind_conn(env)
    total_second = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    ent_second = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    conn.close()

    assert total_second == total_first, "re-run must not add chunks"
    assert ent_second == ent_first, "re-run must not add entities"
    assert "already imported" in out2


def test_source_dbs_unchanged(palace_env):
    env = palace_env
    mem = env.user_dir / "memory.db"
    know = env.user_dir / "knowledge.db"
    mem_before, know_before = _sha256(mem), _sha256(know)

    env.import_tools.execute("import_v2", {"what": "all"}, env.config)
    # run twice to be sure the read-only guarantee holds on re-run too
    env.import_tools.execute("import_v2", {"what": "all"}, env.config)

    assert _sha256(mem) == mem_before, "memory.db bytes changed — read-only violated"
    assert _sha256(know) == know_before, "knowledge.db bytes changed — read-only violated"


def test_scopes_registered(palace_env):
    env = palace_env
    env.import_tools.execute("import_v2", {"what": "all"}, env.config)

    conn = _mind_conn(env)
    c = conn.cursor()
    names = {r[0] for r in c.execute("SELECT name FROM mind_scopes").fetchall()}
    conn.close()
    # default (seeded) + work + global encountered in the memory fixture
    assert {"default", "work", "global"}.issubset(names)
    assert not any(n.startswith("__rag__") for n in names)


def test_preview_counts_source_against_palace(palace_env):
    """The Import tab's numbers: per store × scope, what the classic DBs
    hold, what the palace already carries, the difference — read-only on
    both sides, attachment tabs counted aside, absent stores flagged."""
    env = palace_env
    p = env.import_tools.preview(env.config)
    assert p['stores']['memories']['total'] == 6
    assert p['stores']['memories']['pending'] == 6
    assert p['stores']['people']['total'] == 2
    assert p['stores']['knowledge']['total'] == 3 and p['rag_rows'] == 1
    assert p['stores']['goals']['absent'] is True
    assert p['pending'] == 11 and p['running'] is False
    assert p['files']['memory.db']['present'] and not p['files']['goals.db']['present']
    assert set(p['stores']['memories']['scopes']) >= {'default', 'work', 'global'}
    env.import_tools.execute("import_v2", {"what": "all"}, env.config)
    p = env.import_tools.preview(env.config)
    assert p['pending'] == 0
    assert p['stores']['memories']['imported'] == 6
    assert all(v['pending'] == 0 for v in p['stores']['knowledge']['scopes'].values())


def test_start_runs_and_reports(palace_env):
    env = palace_env
    from plugins.mindpalace.routes import import_routes
    msg, ok = env.import_tools.start(env.config, sync=True)
    assert ok, msg
    st = env.import_tools.status()
    assert st['running'] is False and st['finished'] and st['error'] is None
    assert '6 copied' in st['result']
    assert env.import_tools.preview(env.config)['pending'] == 0
    assert import_routes.status()['result'] == st['result']
    env.import_tools._run_state['running'] = True
    try:
        out, code = import_routes.run(body={'what': 'all'})
        assert code == 409 and 'already' in out['error']
    finally:
        env.import_tools._run_state['running'] = False
    assert import_routes.run(body={'what': 'bogus'})[1] == 400


def test_nudge_fires_while_pending_and_dismisses_after(palace_env, monkeypatch):
    """Boot nudge (Krem 2026-10-30, B): a row every boot while classic rows
    are pending, keyed so it re-fires rather than piles up, pointing at
    the Import tab; the import itself takes it down."""
    env = palace_env
    import core.notify as notify
    calls, dismissed = [], []
    monkeypatch.setattr(notify, 'notify', lambda *a, **k: calls.append((a, k)) or 'rid')
    monkeypatch.setattr(notify, 'rows',
                        lambda **k: [{'id': 'old', 'key': env.import_tools.NUDGE_KEY}])
    monkeypatch.setattr(notify, 'dismiss', lambda rid, **k: dismissed.append(rid))
    assert env.import_tools.nudge_if_pending(env.config) == 'rid'
    (title, level, body), kw = calls[-1]
    assert level == 'warning' and kw['key'] == env.import_tools.NUDGE_KEY
    assert kw['go'] == {'view': 'import'} and kw['source'] == 'mindpalace'
    assert '6 memories' in body and '3 knowledge' in body
    env.import_tools.start(env.config, sync=True)
    assert 'old' in dismissed                       # the import clears it
    assert env.import_tools.nudge_if_pending(env.config) is None
    assert len(calls) == 1


def _classes(env):
    rows, _rag = env.import_tools._classify(env.config)
    return {r['key']: r['cls'] for r in rows}


def test_removed_before_is_told_apart_and_stays_out(palace_env):
    """Krem's terror (2026-10-30): 444 'pending' on Prime were the rows he
    trimmed after the first import — the idempotency set was the palace's
    own keys. A row not in the palace and saved BEFORE its scope's first
    import came through once and was removed since: listed apart, never
    imported by default, back only on an explicit ask."""
    env = palace_env
    assert set(_classes(env).values()) == {'new'}
    env.import_tools.start(env.config, sync=True)
    assert set(_classes(env).values()) == {'imported'}
    conn = _mind_conn(env)
    cid = conn.execute("SELECT id FROM chunks WHERE content = 'space-format memory'"
                       ).fetchone()[0]
    conn.execute('DELETE FROM chunks WHERE id = ?', (cid,))       # trimmed by hand, no tombstone
    conn.commit()
    conn.close()
    assert _classes(env)['v2:memories:1'] == 'removed'
    p = env.import_tools.preview(env.config)
    assert p['pending'] == 0 and p['removed'] == 1
    assert p['stores']['memories']['scopes']['default']['removed'] == 1
    lst = env.import_tools.removed_list(env.config)
    assert [(r['key'], r['preview']) for r in lst] == [('v2:memories:1', 'space-format memory')]
    assert env.import_tools.removed_list(env.config, scope='work') == []
    env.import_tools.start(env.config, sync=True)
    assert _classes(env)['v2:memories:1'] == 'removed'               # stays out
    assert 'left out: 1 removed' in env.import_tools.status()['result']
    env.import_tools.start(env.config, sync=True, include_removed=True)
    assert _classes(env)['v2:memories:1'] == 'imported'              # explicit ask


def test_removed_before_is_per_store_not_per_scope(palace_env):
    """A run that copied only memories says nothing about that scope's
    people: they are new, not removed (the restored dev box showed 23
    never-imported rows as 'removed before' under a per-scope rule)."""
    env = palace_env
    env.import_tools.start(env.config, sync=True, what='memories')
    cls = _classes(env)
    assert cls['v2:memories:1'] == 'imported'
    assert cls['v2:people:1'] == 'new' and cls['v2:knowledge:1'] == 'new'
    p = env.import_tools.preview(env.config)
    assert p['removed'] == 0 and p['pending'] == 5


def test_deletes_tombstone_and_the_importer_skips_them(palace_env):
    """A deleted palace row that came from classic never comes back: the UI
    delete, her delete_memory, and an entity delete all keep the key."""
    env = palace_env
    from plugins.mindpalace.routes import browse
    env.import_tools.start(env.config, sync=True)
    conn = _mind_conn(env)
    a = conn.execute("SELECT id FROM chunks WHERE content = 't-format memory'").fetchone()[0]
    b = conn.execute("SELECT id FROM chunks WHERE content = 'global memory'").fetchone()[0]
    eid = conn.execute("SELECT id FROM entities WHERE name = 'Krem'").fetchone()[0]
    conn.close()
    assert browse.delete_chunk(cid=a) == {'success': True}
    assert env.palace_tools._delete_memory(b, 'global')[1]
    assert browse.delete_entity(eid=eid)['success']
    cls = _classes(env)
    assert cls['v2:memories:2'] == 'tombstoned' and cls['v2:memories:6'] == 'tombstoned'
    assert cls['v2:people:1'] == 'tombstoned' and cls['v2:people:2'] == 'tombstoned'
    p = env.import_tools.preview(env.config)
    assert p['pending'] == 0 and p['removed'] == 0 and p['tombstoned'] == 4
    env.import_tools.start(env.config, sync=True, include_removed=True)
    assert _classes(env)['v2:memories:2'] == 'tombstoned'             # never again
    conn = _mind_conn(env)
    assert conn.execute("SELECT COUNT(*) FROM chunks WHERE content = 't-format memory'"
                        ).fetchone()[0] == 0
    conn.close()


def test_scope_import_takes_one_scope(palace_env):
    env = palace_env
    msg, ok = env.import_tools.start(env.config, sync=True, scope='work')
    assert ok and 'work' in msg
    conn = _mind_conn(env)
    scopes = {r[0] for r in conn.execute("SELECT DISTINCT scope FROM chunks WHERE layer = 'events'")}
    conn.close()
    assert scopes == {'work'}
    p = env.import_tools.preview(env.config)
    assert p['stores']['memories']['scopes']['work']['pending'] == 0
    assert p['stores']['memories']['scopes']['default']['pending'] == 4
    assert 'other scopes' in env.import_tools.status()['result']


def test_scrub_deletes_removed_rows_from_the_old_database_only(palace_env):
    """THE one classic write: rows the user already removed, on an explicit
    typed ask. Everything else in the old database is byte-identical
    afterwards in spirit — the other rows survive untouched; the keys are
    tombstoned; the ledger carries counts, never content."""
    env = palace_env
    from plugins.mindpalace.routes import import_routes
    env.import_tools.start(env.config, sync=True)
    conn = _mind_conn(env)
    cid = conn.execute("SELECT id FROM chunks WHERE content = 'space-format memory'"
                       ).fetchone()[0]
    conn.execute('DELETE FROM chunks WHERE id = ?', (cid,))
    conn.commit()
    conn.close()
    assert import_routes.scrub(body={'scope': 'default', 'confirm': 'wrong'})[1] == 400
    out = import_routes.scrub(body={'scope': 'default', 'confirm': 'default'})
    assert out['success'] and out['scrubbed'] == 1 and out['memories'] == 1
    src = sqlite3.connect(env.user_dir / 'memory.db')
    rows = {r[0] for r in src.execute('SELECT content FROM memories')}
    src.close()
    assert 'space-format memory' not in rows and len(rows) == 5
    cls = _classes(env)
    assert 'v2:memories:1' not in cls                                 # gone from the source
    conn = _mind_conn(env)
    assert conn.execute("SELECT COUNT(*) FROM import_tombstones WHERE key = 'v2:memories:1'"
                        ).fetchone()[0] == 1
    led = conn.execute("SELECT summary FROM ledger WHERE action = 'scrubbed'").fetchone()[0]
    conn.close()
    assert 'scrubbed 1' in led and 'space-format' not in led
    assert env.import_tools.preview(env.config)['removed'] == 0


def test_rag_attachment_tabs_stay_with_the_chat(palace_env):
    """Scout find 2026-10-30: per-chat attachments (knowledge_tabs scoped
    '__rag__:<chat>') imported as knowledge AND minted a palace scope per
    chat — a private chat's name in the scope list. Core keeps attachments
    under either engine; the importer leaves them alone and never counts
    them."""
    env = palace_env
    out, ok = env.import_tools.execute("import_v2", {"what": "knowledge"}, env.config)
    assert ok, out
    assert "3 copied" in out
    conn = _mind_conn(env)
    c = conn.cursor()
    assert c.execute("SELECT COUNT(*) FROM chunks WHERE scope LIKE '__rag__:%'"
                     ).fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM chunks WHERE content = 'attached pdf text'"
                     ).fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM mind_scopes WHERE name LIKE '__rag__:%'"
                     ).fetchone()[0] == 0
    conn.close()


def test_missing_source_is_skipped_not_error(palace_env):
    env = palace_env
    # remove memory.db → its store should skip with a friendly note, others run
    (env.user_dir / "memory.db").unlink()

    out, ok = env.import_tools.execute("import_v2", {"what": "all"}, env.config)
    assert ok, out
    assert "not found" in out

    conn = _mind_conn(env)
    c = conn.cursor()
    assert c.execute("SELECT COUNT(*) FROM chunks WHERE layer='events'").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM chunks WHERE layer='knowledge'").fetchone()[0] == 3
    conn.close()


def test_scope_disabled_returns_false(palace_env):
    env = palace_env
    # simulate unresolved scope → executor must refuse cleanly
    fm.scope_memory.set(None)
    out, ok = env.import_tools.execute("import_v2", {"what": "all"}, env.config)
    assert ok is False
    assert "disabled" in out.lower()


def test_knowledge_defers_to_library_when_migrated(palace_env, monkeypatch):
    """Fresh-cycle order: the Library's boot migration ingests knowledge.db
    BEFORE import_v1 runs — imported palace copies must flag library_migrated
    so the same knowledge isn't served from two systems at once."""
    env = palace_env
    from plugins.mindpalace.tools import library
    monkeypatch.setattr(library, 'migration_report',
                        lambda: {'ran_at': 'x', 'v1': {'absent': False}})
    out, ok = env.import_tools.execute("import_v2", {"what": "knowledge"},
                                       env.config)
    assert ok, out
    conn = _mind_conn(env)
    flags = conn.execute(
        "SELECT json_extract(meta,'$.library_migrated') FROM chunks "
        "WHERE layer='knowledge'").fetchall()
    conn.close()
    assert flags and all(f[0] == 'v1-boot' for f in flags)


def test_knowledge_serves_normally_without_library_migration(palace_env, monkeypatch):
    """No library v1 migration on record → no flag: doubled surface beats
    dark content when unsure."""
    env = palace_env
    from plugins.mindpalace.tools import library
    monkeypatch.setattr(library, 'migration_report', lambda: {})
    out, ok = env.import_tools.execute("import_v2", {"what": "knowledge"},
                                       env.config)
    assert ok, out
    conn = _mind_conn(env)
    flags = conn.execute(
        "SELECT json_extract(meta,'$.library_migrated') FROM chunks "
        "WHERE layer='knowledge'").fetchall()
    conn.close()
    assert flags and all(f[0] is None for f in flags)
