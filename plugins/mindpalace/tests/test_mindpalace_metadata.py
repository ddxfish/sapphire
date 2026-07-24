"""Mind Palace — Tier-A write-time metadata test suite.

Covers the new metadata layer added on top of palace_tools:
  A. Core rail   — core.chat.function_manager.tool_context ContextVar +
                   set_tool_context helper, and its snapshot/reset/restore
                   behaviour through SCOPE_REGISTRY (setting=None → invisible
                   to sidebar / persona inheritance / task fields).
  B. Pure fns    — metadata.temporal_refs / content_stats / noun_candidates /
                   match_entities (zero-model string heuristics).
  C. Save path   — _save_memory now stamps meta + seeds mention edges; the
                   step is fail-safe (a metadata error never blocks the save).
  D. backfill()  — one-shot idempotent stamp+seed over pre-existing rows.

FIXTURE NOTE — why groups C/D use the *package* palace_tools, not a standalone
load like test_mindpalace.py does:
  _save_memory does `from plugins.mindpalace.tools import metadata as md`, and
  metadata.backfill() does `from plugins.mindpalace.tools import palace_tools
  as pt`. Both are absolute-path imports that resolve to the real package
  modules regardless of how palace_tools was loaded. A standalone-loaded copy
  would talk to a DIFFERENT module object than backfill()/save_meta reach, so
  monkeypatched DB paths wouldn't line up. Groups C/D therefore patch the real
  `plugins.mindpalace.tools.palace_tools` globals (monkeypatch auto-restores).

Embedder is forced UNAVAILABLE everywhere (same as the base suite) — none of
these tests need real vectors, and it keeps the save path off the embed branch.
The tool_context ContextVar is reset between tests so provenance doesn't bleed.
"""
import sys
import json
import sqlite3
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import core.chat.function_manager as fm
from plugins.mindpalace.tools import metadata as md
from plugins.mindpalace.tools import palace_tools as pt


class _FakeEmbedder:
    """Unavailable embedder — keeps _save_memory off the embed branch."""
    provider_id = "fake"

    @property
    def available(self):
        return False

    def embed(self, texts, prefix="search_document"):
        return None


@pytest.fixture(autouse=True)
def _clean_tool_context():
    """Reset the tool_context ContextVar around every test so provenance from
    one test never leaks into the next (they share one process ContextVar)."""
    fm.tool_context.set(None)
    yield
    fm.tool_context.set(None)


@pytest.fixture
def palace(tmp_path, monkeypatch):
    """The REAL package palace_tools, bound to a tmp_path DB with clean latches.

    Must be the package module (not a standalone load) so that _save_memory's
    internal metadata import and metadata.backfill()'s internal palace_tools
    import both resolve to this same, DB-redirected module. monkeypatch restores
    the globals after each test.
    """
    db_path = tmp_path / "mind.db"
    monkeypatch.setattr(pt, "_db_path", db_path, raising=False)
    monkeypatch.setattr(pt, "_db_initialized", False, raising=False)
    # True → skip the embedding backfill sweep on first _ensure_db.
    monkeypatch.setattr(pt, "_backfill_done", True, raising=False)
    monkeypatch.setattr(pt, "_get_embedder", lambda: _FakeEmbedder(), raising=False)
    return pt


def _connect(palace):
    """Raw connection to the palace DB (ensures schema first)."""
    palace._ensure_db()
    return sqlite3.connect(palace._get_db_path())


# ─── A. Core rail: tool_context ContextVar + set_tool_context ─────────────────

def test_set_tool_context_sets_var_drops_falsy_patches_scopes_returns():
    scopes = {"memory": "default"}
    ctx = fm.set_tool_context(scopes=scopes, chat="trinity", persona="sapphire",
                              model="opus", channel="")
    # falsy 'channel' dropped
    assert ctx == {"chat": "trinity", "persona": "sapphire", "model": "opus"}
    # ContextVar set to the same object
    assert fm.tool_context.get() == ctx
    # passed-in scopes dict patched in place (untouched keys preserved)
    assert scopes["tool_context"] == ctx
    assert scopes["memory"] == "default"
    # returns the ctx
    assert fm.set_tool_context(chat="x") == {"chat": "x"}

    # all-falsy → None (not an empty dict)
    got = fm.set_tool_context(chat="", persona=None, model=0)
    assert got is None
    assert fm.tool_context.get() is None


def test_tool_context_survives_snapshot_reset_restore_round_trip():
    fm.set_tool_context(chat="c1", persona="p1", model="m1")
    snap = fm.snapshot_all_scopes()
    assert snap["tool_context"] == {"chat": "c1", "persona": "p1", "model": "m1"}

    fm.reset_scopes()
    assert fm.tool_context.get() is None  # cleared mid-flight

    fm.restore_scopes(snap)
    assert fm.tool_context.get() == {"chat": "c1", "persona": "p1", "model": "m1"}


def test_reset_scopes_clears_tool_context_to_none():
    fm.set_tool_context(chat="c1")
    assert fm.tool_context.get() is not None
    fm.reset_scopes()
    assert fm.tool_context.get() is None


def test_tool_context_invisible_to_setting_keys_and_defaults():
    # setting=None → excluded from sidebar/persona/task-field surfaces.
    assert "tool_context" not in fm.scope_setting_keys()
    assert "tool_context" not in fm.scope_defaults_dict()
    # and it IS a registered scope (that's what buys it snapshot/restore).
    assert "tool_context" in fm.SCOPE_REGISTRY
    assert fm.SCOPE_REGISTRY["tool_context"]["setting"] is None


# ─── B. Pure functions (metadata module) ─────────────────────────────────────

def test_temporal_refs_finds_dedupes_caps_and_skips_bare_months():
    refs = md.temporal_refs(
        "yesterday we met, last summer too, 3 weeks ago it started, "
        "and July 4th, 2026 was the day")
    assert "yesterday" in refs
    assert "last summer" in refs
    assert "3 weeks ago" in refs
    assert "july 4th, 2026" in refs  # lowercased, month+day+year

    # bare month names used as verbs must NOT fire
    assert md.temporal_refs("you may march to the store") == []

    # dedup: repeated ref collapses to one
    assert md.temporal_refs(" ".join(["yesterday"] * 20)) == ["yesterday"]

    # cap at 8
    many = "yesterday today tomorrow tonight last night 2020 2021 2022 2023 2024"
    assert len(md.temporal_refs(many)) == 8


def test_content_stats_keys_and_values():
    stats = md.content_stats("Hello world? http://x.com ```code```")
    assert set(stats.keys()) == {"len", "words", "question", "url", "code"}
    assert stats["question"] is True
    assert stats["url"] is True
    assert stats["code"] is True

    plain = md.content_stats("just three words")
    assert plain["len"] == len("just three words")
    assert plain["words"] == 3
    assert plain["question"] is False
    assert plain["url"] is False
    assert plain["code"] is False

    # code also trips on >= 2 backticks (not just fenced)
    assert md.content_stats("use `x` and `y`")["code"] is True


def test_noun_candidates_groups_skips_excludes_stops_and_caps():
    # consecutive capitalized run mid-sentence → one grouped candidate
    assert md.noun_candidates("I visited New York last week") == ["New York"]

    # sentence-initial word skipped (heuristic cost)
    assert md.noun_candidates("Krem loves the boat") == []

    # exclude set is lowercased-matched
    assert md.noun_candidates("We saw Alice and Bob", exclude={"alice"}) == ["Bob"]

    # _NOUN_STOP words filtered (God is in the stop set)
    assert md.noun_candidates("oh my God help me") == []

    # cap at 10: 12 distinct mid-sentence proper nouns → only 10 returned
    names = " ".join(f"x N{i}" for i in range(12))  # each 'x' resets sentence-init
    out = md.noun_candidates(names)
    assert len(out) == 10


def test_match_entities_nocase_boundary_longest_first_empty_and_regex_safe():
    # NOCASE word-boundary, returns STORED casing
    assert md.match_entities("i love krem so much", ["Krem"]) == ["Krem"]

    # longest-name-first wins on overlapping span
    assert md.match_entities("Krem Senior is here", ["Krem", "Krem Senior"]) \
        == ["Krem Senior"]

    # empty name list → []
    assert md.match_entities("anything at all", []) == []

    # word-boundary: substring inside a larger word does NOT match
    assert md.match_entities("kremlin tour", ["Krem"]) == []

    # regex-special characters in a name must not crash (re.escape path)
    assert md.match_entities("the C++ Guild met today", ["C++ Guild"]) == ["C++ Guild"]


# ─── C. Save-path integration ────────────────────────────────────────────────

def test_save_stamps_meta_seeds_edge_bumps_mentions_and_echoes_link(palace):
    # seed an entity to be mentioned
    _, ok = palace._save_memory("Krem loves the project", scope="default",
                                layer="entities", entity="Krem")
    assert ok

    fm.set_tool_context(chat="trinity", persona="sapphire", model="opus")
    msg, ok2 = palace._save_memory("I talked to Krem yesterday", scope="default")
    assert ok2, msg
    assert "linked: Krem" in msg

    conn = _connect(palace)
    try:
        cid, meta_raw = conn.execute(
            "SELECT id, meta FROM chunks WHERE content = ?",
            ("I talked to Krem yesterday",)).fetchone()
        meta = json.loads(meta_raw)
        assert meta["md_v"] == md.MD_VERSION
        assert "stats" in meta and isinstance(meta["stats"], dict)
        assert "session_id" in meta

        # exactly one mention edge chunk→entity, weight 0.3
        edges = conn.execute(
            "SELECT src_type, dst_type, kind, weight FROM edges "
            "WHERE src_id = ? AND kind = 'mentions'", (cid,)).fetchall()
        assert edges == [("chunk", "entity", "mentions", 0.3)]

        # entity mentions counter bumped
        mentions = conn.execute(
            "SELECT mentions FROM entities WHERE name = 'Krem'").fetchone()[0]
        assert mentions == 1
    finally:
        conn.close()


def test_entities_save_makes_no_self_edge_and_no_link_echo(palace):
    msg, ok = palace._save_memory("Krem is building a boat", scope="default",
                                  layer="entities", entity="Krem")
    assert ok, msg
    # no "linked:" echo of the entity just named
    assert "linked:" not in msg

    conn = _connect(palace)
    try:
        ent_id = conn.execute(
            "SELECT id FROM entities WHERE name = 'Krem'").fetchone()[0]
        chunk_id = conn.execute(
            "SELECT id FROM chunks WHERE content = ?",
            ("Krem is building a boat",)).fetchone()[0]
        # no self mention-edge from this chunk to its own entity
        n = conn.execute(
            "SELECT COUNT(*) FROM edges WHERE src_id = ? AND dst_id = ? "
            "AND kind = 'mentions'", (chunk_id, ent_id)).fetchone()[0]
        assert n == 0
    finally:
        conn.close()


def test_tool_context_flows_into_meta_and_absence_still_saves(palace):
    # WITH tool_context → chat/persona/model land in meta
    fm.set_tool_context(chat="lookout", persona="rook", model="sonnet")
    _, ok = palace._save_memory("with provenance", scope="default")
    assert ok
    conn = _connect(palace)
    try:
        meta = json.loads(conn.execute(
            "SELECT meta FROM chunks WHERE content = 'with provenance'").fetchone()[0])
        assert meta["chat"] == "lookout"
        assert meta["persona"] == "rook"
        assert meta["model"] == "sonnet"
    finally:
        conn.close()

    # WITHOUT tool_context → those keys absent, md_v still present, save ok
    fm.reset_scopes()
    _, ok2 = palace._save_memory("no provenance", scope="default")
    assert ok2
    conn = _connect(palace)
    try:
        meta2 = json.loads(conn.execute(
            "SELECT meta FROM chunks WHERE content = 'no provenance'").fetchone()[0])
        assert meta2["md_v"] == md.MD_VERSION
        for k in ("chat", "persona", "model", "channel"):
            assert k not in meta2
    finally:
        conn.close()


def test_save_meta_failure_does_not_block_save(palace, monkeypatch):
    # _save_memory uses `md.save_meta(...)` where md is this package module.
    monkeypatch.setattr(md, "save_meta",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    msg, ok = palace._save_memory("save meta explodes", scope="default")
    assert ok, msg  # save STILL succeeds

    conn = _connect(palace)
    try:
        row = conn.execute(
            "SELECT id, meta FROM chunks WHERE content = 'save meta explodes'"
        ).fetchone()
        assert row is not None            # chunk row exists
        # Meta degrades to the bare authorship stamp (2026-07-11: added_by
        # always lands, even when derivable-meta stamping explodes).
        m = json.loads(row[1])
        assert m == {'added_by': 'user'}
    finally:
        conn.close()


def test_seed_edges_failure_does_not_block_save(palace, monkeypatch):
    # An entity to trigger the seed_edges call path.
    palace._save_memory("anchor", scope="default", layer="entities", entity="Zeta")
    monkeypatch.setattr(md, "seed_edges",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("edgeboom")))
    msg, ok = palace._save_memory("mentions Zeta here", scope="default")
    assert ok, msg  # save STILL succeeds

    conn = _connect(palace)
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE content = 'mentions Zeta here'"
        ).fetchone()[0]
        assert n == 1
    finally:
        conn.close()


# ─── D. backfill() ───────────────────────────────────────────────────────────

def _seed_backfill_rows(palace):
    """Insert import-shaped rows directly: one meta=NULL row that mentions an
    entity, one row carrying only an import_key, plus the entity itself.
    Returns the original 'updated' timestamp used for all rows."""
    palace._ensure_db()
    conn = sqlite3.connect(palace._get_db_path())
    orig_ts = "2026-01-01T00:00:00+00:00"
    try:
        conn.execute(
            "INSERT INTO entities (name, scope, created, updated) "
            "VALUES ('Krem', 'default', ?, ?)", (orig_ts, orig_ts))
        # meta NULL, mentions Krem + carries a temporal ref
        conn.execute(
            "INSERT INTO chunks (layer, scope, content, meta, created, updated) "
            "VALUES ('events', 'default', 'met Krem last summer', NULL, ?, ?)",
            (orig_ts, orig_ts))
        # meta holds ONLY an import_key (simulating a v2 import row)
        conn.execute(
            "INSERT INTO chunks (layer, scope, content, meta, created, updated) "
            "VALUES ('events', 'default', 'a plain imported row', ?, ?, ?)",
            (json.dumps({"import_key": "v2:memories:1"}), orig_ts, orig_ts))
        conn.commit()
    finally:
        conn.close()
    return orig_ts


def test_linkable_text_restricts_to_link_fields():
    meta = {'link_fields': ['concept'],
            'rows': [{'concept': 'trust', 'why': 'Krem taught me'},
                     {'concept': 'Sailing', 'why': ''}]}
    assert md.linkable_text('trust — Krem taught me\nSailing', meta) == \
        'trust\nSailing'
    # No declaration → whole content linkable (the norm).
    assert md.linkable_text('anything at all', {}) == 'anything at all'
    assert md.linkable_text('anything at all', None) == 'anything at all'


def test_backfill_honors_link_fields(palace):
    """Spider containment survives re-stamping: a chunk declaring
    meta.link_fields is matched/harvested on those fields only — an entity
    named in a why gets no edge even on backfill."""
    palace._ensure_db()
    conn = sqlite3.connect(palace._get_db_path())
    ts = "2026-07-01T00:00:00+00:00"
    try:
        conn.execute("INSERT INTO entities (name, scope, created, updated) "
                     "VALUES ('Krem', 'default', ?, ?)", (ts, ts))
        meta = {'section': 'values', 'link_fields': ['concept'],
                'rows': [{'concept': 'trust', 'why': 'Krem taught me'}]}
        conn.execute(
            "INSERT INTO chunks (layer, scope, content, meta, created, updated) "
            "VALUES ('self', 'default', 'trust — Krem taught me', ?, ?, ?)",
            (json.dumps(meta), ts, ts))
        conn.commit()
    finally:
        conn.close()
    res = md.backfill()
    assert res.get('stamped') == 1 and res.get('edges') == 0
    conn = sqlite3.connect(palace._get_db_path())
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM edges WHERE kind = 'mentions'").fetchone()[0] == 0
        m = json.loads(conn.execute(
            "SELECT meta FROM chunks WHERE layer = 'self'").fetchone()[0])
        assert m['link_fields'] == ['concept']   # preserved through merge
        assert 'taught' not in [n.lower() for n in m.get('noun_candidates', [])]
    finally:
        conn.close()


def test_backfill_stamps_preserves_import_key_seeds_and_is_idempotent(palace):
    orig_ts = _seed_backfill_rows(palace)

    res1 = md.backfill()
    assert res1.get("stamped") == 2
    assert res1.get("edges") == 1

    conn = sqlite3.connect(palace._get_db_path())
    try:
        # (1) md_v/stats stamped; import_key PRESERVED in the merged JSON
        m_null = json.loads(conn.execute(
            "SELECT meta FROM chunks WHERE content = 'met Krem last summer'"
        ).fetchone()[0])
        assert m_null["md_v"] == md.MD_VERSION
        assert "stats" in m_null

        m_key = json.loads(conn.execute(
            "SELECT meta FROM chunks WHERE content = 'a plain imported row'"
        ).fetchone()[0])
        assert m_key["md_v"] == md.MD_VERSION
        assert m_key["import_key"] == "v2:memories:1"  # preserved through merge

        # (4) backfilled meta never carries forward-only provenance keys
        for meta in (m_null, m_key):
            for k in ("session_id", "chat", "persona", "model"):
                assert k not in meta

        # (2) mention edge seeded + counter bumped once
        assert conn.execute(
            "SELECT COUNT(*) FROM edges WHERE kind = 'mentions'").fetchone()[0] == 1
        assert conn.execute(
            "SELECT mentions FROM entities WHERE name = 'Krem'").fetchone()[0] == 1

        # (5) chunks.updated UNCHANGED — backfill only annotates
        updated = conn.execute(
            "SELECT updated FROM chunks WHERE content = 'met Krem last summer'"
        ).fetchone()[0]
        assert updated == orig_ts
    finally:
        conn.close()

    # (3) second run is a no-op: nothing re-stamped, no dup edges / double-bump
    res2 = md.backfill()
    assert res2.get("stamped") == 0
    assert res2.get("edges") == 0

    conn = sqlite3.connect(palace._get_db_path())
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM edges WHERE kind = 'mentions'").fetchone()[0] == 1
        assert conn.execute(
            "SELECT mentions FROM entities WHERE name = 'Krem'").fetchone()[0] == 1
    finally:
        conn.close()


def test_save_meta_threads_anchor(palace):
    # 2026-07-21: save_meta gained anchor= so librarian-derived chunks can
    # resolve relative dates against their inherited created.
    m = md.save_meta("we sailed tonight", anchor='2026-02-12T12:00:00+00:00')
    assert m.get('event_dates') == ['2026-02-12']
    from datetime import datetime
    m2 = md.save_meta("we sailed tonight")     # no anchor = genuinely new = today
    assert m2.get('event_dates') == [datetime.now().strftime('%Y-%m-%d')]
