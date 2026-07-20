"""Mind Palace — the memory spider (depth= traversal) test suite.

Covers plugins/mindpalace/tools/spider.py driven through palace_tools._search_memory
and, where the cost math needs surgical control, through spider.spider_block /
spider.traverse directly.

FIXTURE NOTE — why this suite uses the *package* palace_tools, not a standalone
load like test_mindpalace.py does (copied from test_mindpalace_metadata.py):
  palace_tools._spider_block hands the spider `sys.modules[__name__]` — the REAL
  package module object plugins.mindpalace.tools.palace_tools. The spider then
  calls that module's _get_connection / _scope_condition / _private_key_clause /
  _get_embedder / SELECT_CHUNK / _format_chunk. A standalone-loaded copy would be
  a DIFFERENT module object than the spider reaches, so a monkeypatched DB path
  wouldn't line up. This suite therefore patches the real
  `plugins.mindpalace.tools.palace_tools` globals: _db_path → tmp_path,
  _db_initialized False, _backfill_done True (skip embed sweep), and
  _get_embedder → unavailable. monkeypatch auto-restores after each test.

Embedder forced UNAVAILABLE everywhere — rung-2 semantic epicenter resolution is
off, so the graphs are deterministic (structure + mention edges only). Group B(2)
explicitly asserts the embedder-down path is silently skipped. tool_context is
reset between tests so save-path provenance doesn't bleed.

Cost model under test (spider.py constants):
  STRUCT_COST=1.0  entity↔own chunk = tier×1.0 (untiered priced as 2), chunk→entity flat 1.0
  META_COST=2.0    mention edges (both directions)
  BUDGET_PER_DEPTH=2.0   depth 1 → 2.0, depth 2 → 4.0; depth clamped to [0,2] (2026-07-11)
  caps: per-depth via spider._limits() — settings-driven char budgets
"""
import sys
import sqlite3
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import core.chat.function_manager as fm
from plugins.mindpalace.tools import palace_tools as pt
from plugins.mindpalace.tools import spider


class _FakeEmbedder:
    """Unavailable embedder — keeps the spider off the semantic rung."""
    provider_id = "fake"

    @property
    def available(self):
        return False

    def embed(self, texts, prefix="search_document"):
        return None


@pytest.fixture(autouse=True)
def _clean_tool_context():
    fm.tool_context.set(None)
    yield
    fm.tool_context.set(None)


@pytest.fixture
def palace(tmp_path, monkeypatch):
    """The REAL package palace_tools, bound to a tmp_path DB with clean latches.
    Must be the package module so spider's calls into it hit this same,
    DB-redirected object (see module docstring)."""
    db_path = tmp_path / "mind.db"
    monkeypatch.setattr(pt, "_db_path", db_path, raising=False)
    monkeypatch.setattr(pt, "_db_initialized", False, raising=False)
    monkeypatch.setattr(pt, "_backfill_done", True, raising=False)
    monkeypatch.setattr(pt, "_get_embedder", lambda: _FakeEmbedder(), raising=False)
    # Pricing/tiebreak tests exercise importance — force the alpha master ON
    # (the manifest default is off → flat pricing).
    monkeypatch.setattr(spider, "_importance_on", lambda: True)
    return pt


def _connect(palace):
    """Raw connection to the palace DB (ensures schema first)."""
    palace._ensure_db()
    return sqlite3.connect(palace._get_db_path())


def _entity_id(conn, name, scope="default"):
    return conn.execute(
        "SELECT id FROM entities WHERE name = ? AND scope = ?", (name, scope)
    ).fetchone()[0]


def _chunk_id(conn, content):
    return conn.execute(
        "SELECT id FROM chunks WHERE content = ?", (content,)).fetchone()[0]


def _now(palace):
    return palace._now()


# ─── A. Budget math on a hand-built graph ─────────────────────────────────────
#
# Graph shared across A2–A4, built with precise tiers via direct SQL:
#   E  (event, no entity)  --mentions-->  X (entity)
#   X's own chunks: XF (tier 2 fact), XH1 (tier 1 headline), XT (tier 3 trivia)
#   E2 (event, no entity)  --mentions-->  X   (sibling)
#   Y  (entity)            <--mentions--  E2  (E2 also mentions Y)
#
# Seed for the spider is always chunk E. Costs from E:
#   E→X  (mention)           = 2.0
#   X→XF (struct, tier 2)    = 2.0 + 2.0 = 4.0
#   X→XH1(struct, tier 1)    = 2.0 + 1.0 = 3.0
#   X→XT (struct, tier 3)    = 2.0 + 3.0 = 5.0
#   X→E2 (mention)           = 2.0 + 2.0 = 4.0
#   E2→Y (mention)           = 4.0 + 2.0 = 6.0


def _build_budget_graph(palace):
    conn = _connect(palace)
    ts = _now(palace)
    try:
        cur = conn.cursor()
        # entities
        cur.execute("INSERT INTO entities (name, scope, kind, created, updated) "
                    "VALUES ('Xenon', 'default', 'person', ?, ?)", (ts, ts))
        xid = cur.lastrowid
        cur.execute("INSERT INTO entities (name, scope, kind, created, updated) "
                    "VALUES ('Yara', 'default', 'person', ?, ?)", (ts, ts))
        yid = cur.lastrowid
        # seed event E (no entity), and sibling E2
        cur.execute("INSERT INTO chunks (layer, scope, content, created, updated) "
                    "VALUES ('events', 'default', 'the origin event alpha', ?, ?)", (ts, ts))
        eid = cur.lastrowid
        cur.execute("INSERT INTO chunks (layer, scope, content, created, updated) "
                    "VALUES ('events', 'default', 'the sibling event bravo', ?, ?)", (ts, ts))
        e2id = cur.lastrowid
        # X's own chunks at precise tiers
        cur.execute("INSERT INTO chunks (layer, scope, content, entity_id, tier, created, updated) "
                    "VALUES ('entities', 'default', 'X fact tier two', ?, 2, ?, ?)", (xid, ts, ts))
        xf = cur.lastrowid
        cur.execute("INSERT INTO chunks (layer, scope, content, entity_id, tier, created, updated) "
                    "VALUES ('entities', 'default', 'X headline tier one', ?, 1, ?, ?)", (xid, ts, ts))
        xh1 = cur.lastrowid
        cur.execute("INSERT INTO chunks (layer, scope, content, entity_id, tier, created, updated) "
                    "VALUES ('entities', 'default', 'X trivia tier three', ?, 3, ?, ?)", (xid, ts, ts))
        xt = cur.lastrowid
        # mention edges (weight irrelevant to cost — cost comes from kind)
        for src in (eid, e2id):
            cur.execute("INSERT INTO edges (src_type, src_id, dst_type, dst_id, kind, weight, created) "
                        "VALUES ('chunk', ?, 'entity', ?, 'mentions', 0.3, ?)", (src, xid, ts))
        cur.execute("INSERT INTO edges (src_type, src_id, dst_type, dst_id, kind, weight, created) "
                    "VALUES ('chunk', ?, 'entity', ?, 'mentions', 0.3, ?)", (e2id, yid, ts))
        conn.commit()
        return dict(xid=xid, yid=yid, eid=eid, e2id=e2id, xf=xf, xh1=xh1, xt=xt)
    finally:
        conn.close()


def test_A1_depth0_no_block_identical_shape(palace):
    """depth=0 → no 'Connected memories' block; output is the plain hit list."""
    palace._save_memory("the origin event alpha", scope="default")
    out0, ok0 = palace._search_memory("origin event alpha", scope="default", depth=0)
    assert ok0
    assert "Connected memories" not in out0
    # Same query at depth=0 must equal not passing depth at all (default 0).
    out_default, _ = palace._search_memory("origin event alpha", scope="default")
    assert out0 == out_default
    assert out0.startswith("Found 1 memories:")


def test_A2_depth1_entity_name_but_not_its_facts(palace):
    """depth=1 (budget 2.0): E→X mention (2.0) fits; X→tier-2 fact (4.0) does not.

    NOTE: the tier-1 headline TEXT decorates the entity line whenever X is
    reached (see _format_block's unconditional 'newest tier-1' headline lookup),
    independent of whether that chunk was traversed. So we assert on the chunk's
    [id] marker in the connected-chunk list — the true 'traversed as a chunk'
    signal — not on the headline text."""
    ids = _build_budget_graph(palace)
    out, ok = palace._search_memory("origin event alpha", scope="default", depth=1)
    assert ok, out
    block = out.split("Connected memories", 1)[1]
    assert "Xenon" in out                   # X reached at exactly 2.0
    assert "X fact tier two" not in out     # tier-2 fact: 4.0 > 2.0 budget
    assert f"[{ids['xf']}]" not in block    # tier-2 fact chunk not traversed
    assert f"[{ids['xh1']}]" not in block   # tier-1 headline chunk not traversed (3.0 > 2.0)
    assert f"[{ids['eid']}]" not in block   # seed excluded


def test_A3_depth2_facts_and_sibling_appear_distant_entity_does_not(palace):
    """depth=2 (budget 4.0): X's tier-2 fact (4.0) and sibling E2 (4.0) appear;
    Y reachable only at 6.0 does not."""
    ids = _build_budget_graph(palace)
    out, ok = palace._search_memory("origin event alpha", scope="default", depth=2)
    assert ok, out
    block = out.split("Connected memories", 1)[1]
    assert "Xenon" in out
    assert "X fact tier two" in block       # 4.0 == budget
    assert "the sibling event bravo" in block  # E2 at 4.0
    assert "Yara" not in out                # Y at 6.0 > 4.0
    assert "X trivia tier three" not in block  # tier 3 = 5.0 > 4.0


def test_A4_tier1_reachable_tier3_not_at_depth2(palace):
    """depth=2 (budget 4.0): tier-1 headline chunk via X (2.0+1.0=3.0) is
    traversed and listed; tier-3 trivia (2.0+3.0=5.0) is not. Asserted on the
    chunk [id] markers so the entity-line headline decoration doesn't confound
    the tier-1 signal (that text shows at any depth X is reached)."""
    ids = _build_budget_graph(palace)
    out, ok = palace._search_memory("origin event alpha", scope="default", depth=2)
    assert ok, out
    block = out.split("Connected memories", 1)[1]
    assert f"[{ids['xh1']}]" in block          # tier-1 chunk: 3.0 <= 4.0
    assert f"[{ids['xt']}]" not in block       # tier-3 chunk: 5.0 > 4.0
    assert "X trivia tier three" not in block


# ─── B. Epicenter ladder (G4 rungs) ───────────────────────────────────────────

def test_B1_query_names_entity_zero_hits_still_spiders(palace):
    """Query exactly names an entity (lowercase, word-boundary NOCASE) with no
    FTS overlap on any content → 'No direct matches' + a block built from that
    entity's structural neighborhood (rung 1 fires without base hits)."""
    conn = _connect(palace)
    ts = _now(palace)
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO entities (name, scope, kind, created, updated) "
                    "VALUES ('Zephyrina', 'default', 'person', ?, ?)", (ts, ts))
        zid = cur.lastrowid
        # tier-1 headline so the entity line carries a headline, tier-2 fact for depth 2
        cur.execute("INSERT INTO chunks (layer, scope, content, entity_id, tier, created, updated) "
                    "VALUES ('entities', 'default', 'she pilots the deep survey ship', ?, 1, ?, ?)",
                    (zid, ts, ts))
        conn.commit()
    finally:
        conn.close()
    # Query names the entity in lowercase; no content shares its words.
    out, ok = palace._search_memory("zephyrina", scope="default", depth=1)
    assert ok, out
    assert out.startswith("No direct matches for 'zephyrina'")
    assert "Connected memories" in out
    assert "Zephyrina" in out
    # depth-1 budget (2.0) reaches the entity's own tier-1 chunk (struct 1.0).
    assert "she pilots the deep survey ship" in out


def test_B2_embedder_unavailable_semantic_rung_skipped_no_exception(palace):
    """Embedder down → rung 2 (semantic) silently skipped, no exception raised.
    resolve_entity_epicenters must return cleanly with only rung-1 seeds."""
    conn = _connect(palace)
    ts = _now(palace)
    try:
        conn.execute("INSERT INTO entities (name, scope, created, updated) "
                     "VALUES ('Quill', 'default', ?, ?)", (ts, ts))
        conn.commit()
    finally:
        conn.close()
    with palace._get_connection() as conn:
        cur = conn.cursor()
        # Must not raise even though embedder.available is False.
        seeds = spider.resolve_entity_epicenters(pt, cur, "quill", "default", None)
    assert isinstance(seeds, set)
    # rung 1 still resolves the exact name; rung 2 just contributes nothing.
    assert len(seeds) == 1


def test_B3_query_names_nothing_no_hits_plain_no_memories(palace):
    """Query names no entity and no content matches → plain 'No memories found',
    no block appended."""
    conn = _connect(palace)
    ts = _now(palace)
    try:
        conn.execute("INSERT INTO entities (name, scope, created, updated) "
                     "VALUES ('Realname', 'default', ?, ?)", (ts, ts))
        conn.commit()
    finally:
        conn.close()
    out, ok = palace._search_memory("floobjwqx nonexistent", scope="default", depth=2)
    assert ok, out
    assert out.startswith("No memories found for 'floobjwqx nonexistent'")
    assert "Connected memories" not in out


# ─── C. Gates: scope overlay + private_key ────────────────────────────────────

def test_C1_cross_scope_chunk_never_reached(palace):
    """A mention edge that points at a chunk in ANOTHER scope must not surface
    that chunk in the block. The scope overlay gates every spider node."""
    conn = _connect(palace)
    ts = _now(palace)
    try:
        cur = conn.cursor()
        # entity in default
        cur.execute("INSERT INTO entities (name, scope, created, updated) "
                    "VALUES ('Bridge', 'default', ?, ?)", (ts, ts))
        bid = cur.lastrowid
        # seed event in default mentions Bridge
        cur.execute("INSERT INTO chunks (layer, scope, content, created, updated) "
                    "VALUES ('events', 'default', 'crossing event here', ?, ?)", (ts, ts))
        eid = cur.lastrowid
        cur.execute("INSERT INTO edges (src_type, src_id, dst_type, dst_id, kind, weight, created) "
                    "VALUES ('chunk', ?, 'entity', ?, 'mentions', 0.3, ?)", (eid, bid, ts))
        # A chunk in scope 'other' that ALSO mentions Bridge via a cross-scope edge.
        cur.execute("INSERT INTO chunks (layer, scope, content, created, updated) "
                    "VALUES ('events', 'other', 'SECRET other-scope leak', ?, ?)", (ts, ts))
        leak = cur.lastrowid
        cur.execute("INSERT INTO edges (src_type, src_id, dst_type, dst_id, kind, weight, created) "
                    "VALUES ('chunk', ?, 'entity', ?, 'mentions', 0.3, ?)", (leak, bid, ts))
        conn.commit()
    finally:
        conn.close()
    out, ok = palace._search_memory("crossing event here", scope="default", depth=3)
    assert ok, out
    assert "SECRET other-scope leak" not in out
    assert "Bridge" in out  # the default-scope entity IS reachable


def test_C2_private_rows_gated_by_key(palace):
    """A private_key row in the spidered neighborhood is excluded when no key is
    passed, included when the matching key is passed."""
    conn = _connect(palace)
    ts = _now(palace)
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO entities (name, scope, created, updated) "
                    "VALUES ('Vault', 'default', ?, ?)", (ts, ts))
        vid = cur.lastrowid
        cur.execute("INSERT INTO chunks (layer, scope, content, created, updated) "
                    "VALUES ('events', 'default', 'public gate event', ?, ?)", (ts, ts))
        pub = cur.lastrowid
        cur.execute("INSERT INTO edges (src_type, src_id, dst_type, dst_id, kind, weight, created) "
                    "VALUES ('chunk', ?, 'entity', ?, 'mentions', 0.3, ?)", (pub, vid, ts))
        # a private tier-2 fact on Vault, gated by 'sesame'
        cur.execute("INSERT INTO chunks (layer, scope, content, entity_id, tier, private_key, created, updated) "
                    "VALUES ('entities', 'default', 'the private vault fact', ?, 2, 'sesame', ?, ?)",
                    (vid, ts, ts))
        conn.commit()
    finally:
        conn.close()
    # No key → private fact excluded (depth 3 has ample budget).
    out_nokey, ok1 = palace._search_memory("public gate event", scope="default", depth=3)
    assert ok1, out_nokey
    assert "the private vault fact" not in out_nokey
    assert "Vault" in out_nokey
    # Right key → private fact included.
    out_key, ok2 = palace._search_memory("public gate event", scope="default",
                                         depth=3, private_key="sesame")
    assert ok2, out_key
    assert "the private vault fact" in out_key


def test_C3_global_scope_rows_reachable(palace):
    """Rows in scope 'global' are part of the read-only overlay (scope IN
    (scope,'global')) and ARE reachable by the spider from a default-scope seed."""
    conn = _connect(palace)
    ts = _now(palace)
    try:
        cur = conn.cursor()
        # entity lives in global
        cur.execute("INSERT INTO entities (name, scope, created, updated) "
                    "VALUES ('Overlay', 'global', ?, ?)", (ts, ts))
        oid = cur.lastrowid
        # seed event in default mentions the global entity
        cur.execute("INSERT INTO chunks (layer, scope, content, created, updated) "
                    "VALUES ('events', 'default', 'reaches the overlay', ?, ?)", (ts, ts))
        eid = cur.lastrowid
        cur.execute("INSERT INTO edges (src_type, src_id, dst_type, dst_id, kind, weight, created) "
                    "VALUES ('chunk', ?, 'entity', ?, 'mentions', 0.3, ?)", (eid, oid, ts))
        # a global fact on the entity
        cur.execute("INSERT INTO chunks (layer, scope, content, entity_id, tier, created, updated) "
                    "VALUES ('entities', 'global', 'shared overlay fact', ?, 2, ?, ?)", (oid, ts, ts))
        conn.commit()
    finally:
        conn.close()
    out, ok = palace._search_memory("reaches the overlay", scope="default", depth=2)
    assert ok, out
    assert "Overlay" in out
    assert "shared overlay fact" in out


# ─── D. Robustness + caps ─────────────────────────────────────────────────────

def test_D1_traverse_explosion_degrades_search_still_returns_hits(palace, monkeypatch):
    """If spider.traverse raises, the search still returns its direct hits and
    appends no block. Failure degrades, never breaks a working search."""
    ids = _build_budget_graph(palace)
    # Give the query a real direct hit first.
    monkeypatch.setattr(spider, "traverse",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("kaboom")))
    out, ok = palace._search_memory("origin event alpha", scope="default", depth=2)
    assert ok, out
    assert out.startswith("Found 1 memories:")
    assert "the origin event alpha" in out
    assert "Connected memories" not in out  # block degraded to ''


def test_D2_chunk_cap_closest_first(palace):
    """More than MAX_CHUNKS_SHOWN reachable chunks → block shows at most the cap,
    and the ones shown are the closest (all these are equidistant at 2.0, so we
    only assert the count cap holds)."""
    conn = _connect(palace)
    ts = _now(palace)
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO entities (name, scope, created, updated) "
                    "VALUES ('Hub', 'default', ?, ?)", (ts, ts))
        hid = cur.lastrowid
        cur.execute("INSERT INTO chunks (layer, scope, content, created, updated) "
                    "VALUES ('events', 'default', 'the hub seed event', ?, ?)", (ts, ts))
        seed = cur.lastrowid
        cur.execute("INSERT INTO edges (src_type, src_id, dst_type, dst_id, kind, weight, created) "
                    "VALUES ('chunk', ?, 'entity', ?, 'mentions', 0.3, ?)", (seed, hid, ts))
        # 15 sibling events, each mentioning Hub → each reachable at 2.0+2.0=4.0.
        for i in range(15):
            cur.execute("INSERT INTO chunks (layer, scope, content, created, updated) "
                        "VALUES ('events', 'default', ?, ?, ?)",
                        (f"sibling number {i:02d}", ts, ts))
            sib = cur.lastrowid
            cur.execute("INSERT INTO edges (src_type, src_id, dst_type, dst_id, kind, weight, created) "
                        "VALUES ('chunk', ?, 'entity', ?, 'mentions', 0.3, ?)", (sib, hid, ts))
        conn.commit()
    finally:
        conn.close()
    out, ok = palace._search_memory("the hub seed event", scope="default", depth=2)
    assert ok, out
    block = out.split("Connected memories", 1)[1]
    shown = sum(1 for i in range(15) if f"sibling number {i:02d}" in block)
    cap = spider._limits(2)['chunks']
    assert shown <= cap
    assert shown == min(15, cap)  # per-depth cap (2026-07-11: depth-2 cap is generous; chars bind)


def test_D3_block_never_exceeds_max_chars_and_not_mid_line(palace):
    """Force a huge neighborhood; block stays <= MAX_BLOCK_CHARS and ends on a
    clean line boundary (the rsplit('\\n', 1)[0] trim), never mid-line."""
    conn = _connect(palace)
    ts = _now(palace)
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO entities (name, scope, created, updated) "
                    "VALUES ('Giant', 'default', ?, ?)", (ts, ts))
        gid = cur.lastrowid
        cur.execute("INSERT INTO chunks (layer, scope, content, created, updated) "
                    "VALUES ('events', 'default', 'the giant seed', ?, ?)", (ts, ts))
        seed = cur.lastrowid
        cur.execute("INSERT INTO edges (src_type, src_id, dst_type, dst_id, kind, weight, created) "
                    "VALUES ('chunk', ?, 'entity', ?, 'mentions', 0.3, ?)", (seed, gid, ts))
        long_body = "L" * 300  # each chunk line is long; 10 shown * long → >2000
        for i in range(12):
            cur.execute("INSERT INTO chunks (layer, scope, content, created, updated) "
                        "VALUES ('events', 'default', ?, ?, ?)",
                        (f"{i:02d} {long_body}", ts, ts))
            sib = cur.lastrowid
            cur.execute("INSERT INTO edges (src_type, src_id, dst_type, dst_id, kind, weight, created) "
                        "VALUES ('chunk', ?, 'entity', ?, 'mentions', 0.3, ?)", (sib, gid, ts))
        conn.commit()
    finally:
        conn.close()
    out, ok = palace._search_memory("the giant seed", scope="default", depth=2)
    assert ok, out
    block = out.split("── Connected memories", 1)[1]
    block = "── Connected memories" + block
    assert len(block) <= spider._limits(2)['chars']
    # Clean boundary: the trim uses rsplit('\n', 1)[0], so no trailing partial
    # line and no dangling newline.
    assert not block.endswith("\n")


def test_D4_depth_clamping_and_types(palace):
    """Negative depth → 0 (no block); 99 → clamped to 3 (no explosion);
    depth as a numeric string '2' via spider_block survives int() coercion."""
    _build_budget_graph(palace)
    # negative → treated as 0 → no block, plain hit list
    out_neg, ok = palace._search_memory("origin event alpha", scope="default", depth=-5)
    assert ok, out_neg
    assert "Connected memories" not in out_neg
    # 99 → clamped to MAX_DEPTH (2 since 2026-07-11), no explosion
    out_big, ok2 = palace._search_memory("origin event alpha", scope="default", depth=99)
    assert ok2, out_big
    assert "Connected memories (depth 2)" in out_big
    # numeric string through spider_block directly — int() must coerce cleanly.
    block = pt._spider_block("origin event alpha", "default", None,
                             [_connect_hit(palace, "the origin event alpha")], "2")
    assert "Connected memories (depth 2)" in block
    # garbage string → int() raises → returns '' (no crash)
    assert spider.spider_block(pt, "q", "default", None, [], "notanumber") == ""


def _connect_hit(palace, content):
    conn = _connect(palace)
    try:
        return _chunk_id(conn, content)
    finally:
        conn.close()


def test_D5_seed_chunks_and_entities_excluded_from_block(palace):
    """The direct-hit seeds themselves are never echoed in the Connected block."""
    ids = _build_budget_graph(palace)
    out, ok = palace._search_memory("origin event alpha", scope="default", depth=2)
    assert ok, out
    block = out.split("Connected memories", 1)[1]
    # seed chunk E is the direct hit — must not reappear as a connected chunk.
    assert f"[{ids['eid']}]" not in block
    assert "the origin event alpha" not in block


# ─── E. Format ────────────────────────────────────────────────────────────────

def test_E1_entity_line_with_headline_and_bare(palace):
    """Entity WITH a tier-1 chunk shows '• Name (kind): headline'; entity WITHOUT
    one shows bare '• Name'. Uses spider_block directly for a clean, isolated
    two-entity neighborhood at depth 1."""
    conn = _connect(palace)
    ts = _now(palace)
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO entities (name, scope, kind, created, updated) "
                    "VALUES ('Headliner', 'default', 'person', ?, ?)", (ts, ts))
        hid = cur.lastrowid
        cur.execute("INSERT INTO entities (name, scope, created, updated) "
                    "VALUES ('Bareword', 'default', ?, ?)", (ts, ts))  # no kind
        bid = cur.lastrowid
        # headline for Headliner (tier 1); nothing tier-1 for Bareword
        cur.execute("INSERT INTO chunks (layer, scope, content, entity_id, tier, created, updated) "
                    "VALUES ('entities', 'default', 'runs the whole show', ?, 1, ?, ?)", (hid, ts, ts))
        # seed event mentions BOTH entities
        cur.execute("INSERT INTO chunks (layer, scope, content, created, updated) "
                    "VALUES ('events', 'default', 'meets them both', ?, ?)", (ts, ts))
        eid = cur.lastrowid
        for target in (hid, bid):
            cur.execute("INSERT INTO edges (src_type, src_id, dst_type, dst_id, kind, weight, created) "
                        "VALUES ('chunk', ?, 'entity', ?, 'mentions', 0.3, ?)", (eid, target, ts))
        conn.commit()
    finally:
        conn.close()
    block = pt._spider_block("meets them both", "default", None,
                             [_connect_hit(palace, "meets them both")], 1)
    assert "• Headliner (person): runs the whole show" in block
    assert "• Bareword" in block
    # Bareword line is bare — no kind, no headline appended.
    for line in block.splitlines():
        if line.startswith("• Bareword"):
            assert line.strip() == "• Bareword"


def test_E2_chunk_line_standard_format_and_preview_cap(palace):
    """Connected chunks use the standard [id] (time) [layer] format and cap the
    content preview at 160 chars (with an ellipsis)."""
    conn = _connect(palace)
    ts = _now(palace)
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO entities (name, scope, created, updated) "
                    "VALUES ('Fmt', 'default', ?, ?)", (ts, ts))
        fid = cur.lastrowid
        cur.execute("INSERT INTO chunks (layer, scope, content, created, updated) "
                    "VALUES ('events', 'default', 'the format seed', ?, ?)", (ts, ts))
        seed = cur.lastrowid
        cur.execute("INSERT INTO edges (src_type, src_id, dst_type, dst_id, kind, weight, created) "
                    "VALUES ('chunk', ?, 'entity', ?, 'mentions', 0.3, ?)", (seed, fid, ts))
        long_content = "Z" * 250  # > 160 preview cap
        cur.execute("INSERT INTO chunks (layer, scope, content, created, updated) "
                    "VALUES ('events', 'default', ?, ?, ?)", (long_content, ts, ts))
        sib = cur.lastrowid
        conn.commit()
        cur.execute("INSERT INTO edges (src_type, src_id, dst_type, dst_id, kind, weight, created) "
                    "VALUES ('chunk', ?, 'entity', ?, 'mentions', 0.3, ?)", (sib, fid, ts))
        conn.commit()
    finally:
        conn.close()
    out, ok = palace._search_memory("the format seed", scope="default", depth=2)
    assert ok, out
    block = out.split("Connected memories", 1)[1]
    # standard chunk line marker present
    assert f"[{sib}]" in block
    assert "[events]" in block
    # preview capped: 160 Z's + ellipsis, never the full 250
    assert "Z" * 160 + "…" in block
    assert "Z" * 161 not in block


# ─── F. Edge pricing v2 (2026-07-11) ─────────────────────────────────────────
#
# cost = base × f(importance) × g(hub degree). Constants under test:
#   f = clamp(1.2 − imp, 0.3, 1.5), NULL → 1.0
#   g = min(2.0, 1 + log10(degree / 32)), degree ≤ 32 → 1.0
# Calibration contract: a mega-hub's unrated fan (2.0 × 2.0 = 4.0) is
# affordable only from the hub itself as seed; one mention-hop in it exceeds
# the depth-2 budget — UNLESS the memory is rated (core band penetrates).


def test_F1_pricing_factor_math():
    """The two factor functions, exact values and clamps."""
    # f(importance)
    assert spider._imp_factor(None) == 1.0
    assert spider._imp_factor(0.9) == pytest.approx(0.3)
    assert spider._imp_factor(1.0) == pytest.approx(0.3)   # floor clamp
    assert spider._imp_factor(0.5) == pytest.approx(0.7)
    assert spider._imp_factor(0.0) == pytest.approx(1.2)   # junk costs extra
    assert spider._imp_factor("garbage") == 1.0            # bad data → neutral
    # g(degree)
    assert spider._degree_factor(0) == 1.0
    assert spider._degree_factor(32) == 1.0                # knee: undamped at the free limit
    assert spider._degree_factor(320) == pytest.approx(2.0)  # 1 + log10(10)
    assert spider._degree_factor(100000) == 2.0            # cap holds
    g50, g100 = spider._degree_factor(50), spider._degree_factor(100)
    assert 1.0 < g50 < g100 < 2.0                          # monotonic between


def _build_hub(palace, fan=200):
    """A mega-hub: entity with `fan` NULL-importance mention chunks, plus one
    outside seed chunk that mentions the hub. Returns (hub_id, seed_id, fan_ids)."""
    conn = _connect(palace)
    ts = _now(palace)
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO entities (name, scope, kind, created, updated) "
                    "VALUES ('Megahub', 'default', 'person', ?, ?)", (ts, ts))
        hub = cur.lastrowid
        cur.execute("INSERT INTO chunks (layer, scope, content, created, updated) "
                    "VALUES ('events', 'default', 'the approach seed', ?, ?)", (ts, ts))
        seed = cur.lastrowid
        cur.execute("INSERT INTO edges (src_type, src_id, dst_type, dst_id, kind, weight, created) "
                    "VALUES ('chunk', ?, 'entity', ?, 'mentions', 0.3, ?)", (seed, hub, ts))
        fan_ids = []
        for i in range(fan):
            cur.execute("INSERT INTO chunks (layer, scope, content, created, updated) "
                        "VALUES ('events', 'default', ?, ?, ?)",
                        (f"hub fan memory {i:03d}", ts, ts))
            fan_ids.append(cur.lastrowid)
            cur.execute("INSERT INTO edges (src_type, src_id, dst_type, dst_id, kind, weight, created) "
                        "VALUES ('chunk', ?, 'entity', ?, 'mentions', 0.3, ?)",
                        (fan_ids[-1], hub, ts))
        conn.commit()
        return hub, seed, fan_ids
    finally:
        conn.close()


def test_F2_hub_fan_dies_one_hop_in_unless_rated(palace):
    """The wake-explosion fix. Seed chunk → hub (2.0) → unrated fan
    (2.0 × g(201) ≈ 3.6) totals ~5.6 > 4.0 → NONE of the fan is reached.
    Rate one fan memory 0.9 → its edge drops to ~1.08 → reached."""
    hub, seed, fan_ids = _build_hub(palace)
    with palace._get_connection() as conn:
        cur = conn.cursor()
        chunks, entities, _docs = spider.traverse(pt, cur, {seed}, set(), 4.0, "default", None)
    assert hub in entities                       # the hub itself IS reached
    assert not any(cid in chunks for cid in fan_ids)  # the 200-fan is not
    # Rate one memory into the core band → it penetrates the hub.
    conn2 = _connect(palace)
    try:
        conn2.execute("UPDATE chunks SET importance = 0.9 WHERE id = ?", (fan_ids[7],))
        conn2.commit()
    finally:
        conn2.close()
    with palace._get_connection() as conn:
        cur = conn.cursor()
        chunks2, _, _d2 = spider.traverse(pt, cur, {seed}, set(), 4.0, "default", None)
    assert fan_ids[7] in chunks2
    assert not any(cid in chunks2 for cid in fan_ids if cid != fan_ids[7])


def test_F2b_importance_off_prices_flat(palace, monkeypatch):
    """Alpha master toggle off (the shipping default): a core-rated (0.9)
    fan memory gets NO discount — it stays as unreachable as its unrated
    siblings. Same graph as F2, flag flipped."""
    hub, seed, fan_ids = _build_hub(palace)
    conn = _connect(palace)
    try:
        conn.execute("UPDATE chunks SET importance = 0.9 WHERE id = ?", (fan_ids[7],))
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(spider, "_importance_on", lambda: False)
    with palace._get_connection() as c:
        chunks, entities, _docs = spider.traverse(pt, c.cursor(), {seed}, set(), 4.0,
                                                  "default", None)
    assert hub in entities                            # structure unaffected
    assert not any(cid in chunks for cid in fan_ids)  # rated or not: flat


def test_F3_hub_as_seed_still_fans(palace):
    """Searching the hub by name keeps its neighborhood: from the hub as
    epicenter the unrated fan edge is 2.0 × g ≈ 3.6 ≤ 4.0 → reached."""
    hub, seed, fan_ids = _build_hub(palace)
    with palace._get_connection() as conn:
        cur = conn.cursor()
        chunks, _, _d = spider.traverse(pt, cur, set(), {hub}, 4.0, "default", None)
    reached = sum(1 for cid in fan_ids if cid in chunks)
    assert reached == len(fan_ids)


def test_F4_tiebreak_importance_then_recency(palace):
    """Equidistant connected chunks order by importance desc, then created
    desc — never id order (the 'every memory, sequential' symptom)."""
    conn = _connect(palace)
    try:
        cur = conn.cursor()
        ts = _now(palace)
        cur.execute("INSERT INTO entities (name, scope, created, updated) "
                    "VALUES ('Tie', 'default', ?, ?)", (ts, ts))
        tid = cur.lastrowid
        cur.execute("INSERT INTO chunks (layer, scope, content, created, updated) "
                    "VALUES ('events', 'default', 'the tie seed', ?, ?)", (ts, ts))
        seed = cur.lastrowid
        cur.execute("INSERT INTO edges (src_type, src_id, dst_type, dst_id, kind, weight, created) "
                    "VALUES ('chunk', ?, 'entity', ?, 'mentions', 0.3, ?)", (seed, tid, ts))
        # Three siblings, inserted oldest-content-first so id order ≠ desired order.
        rows = [("oldest ordinary alpha", "2025-01-01T00:00:00", None),
                ("newest ordinary bravo", "2025-06-01T00:00:00", None),
                ("old but core charlie", "2025-02-01T00:00:00", 0.9)]
        for content, created, imp in rows:
            cur.execute("INSERT INTO chunks (layer, scope, content, importance, created, updated) "
                        "VALUES ('events', 'default', ?, ?, ?, ?)",
                        (content, imp, created, created))
            cur.execute("INSERT INTO edges (src_type, src_id, dst_type, dst_id, kind, weight, created) "
                        "VALUES ('chunk', ?, 'entity', ?, 'mentions', 0.3, ?)",
                        (cur.lastrowid, tid, ts))
        conn.commit()
    finally:
        conn.close()
    out, ok = palace._search_memory("the tie seed", scope="default", depth=2)
    assert ok, out
    block = out.split("Connected memories", 1)[1]
    # NOTE: 'charlie' (imp 0.9) travels CHEAPER than the unrated pair, so it
    # leads by distance, not just tiebreak; the unrated pair tie exactly and
    # must order newest-first — the id-order symptom would show alpha first.
    pos = {name: block.find(name) for name in
           ("old but core charlie", "newest ordinary bravo", "oldest ordinary alpha")}
    assert all(p >= 0 for p in pos.values()), block
    assert pos["old but core charlie"] < pos["newest ordinary bravo"] < pos["oldest ordinary alpha"]
