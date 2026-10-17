"""Mind Palace v1 — palace_tools.py test suite (layered memory framework).

Exercises the internal functions directly (_save_memory / _search_memory /
_get_recent_memories / _delete_memory) with explicit scope args, and the
execute() executor contract via a monkeypatched scope ContextVar. Also covers
the function_manager mutual-exclusion refusal (the 2026-07-06 tweak).

Realistic mode: the embedder is forced UNAVAILABLE in the base fixture. That
exercises the FTS + LIKE search paths (vector search returns []), which is the
mode palace_tools must handle gracefully. Tests that don't touch search are
unaffected. No embeddings are mocked-available anywhere — none of these tests
need to verify provenance-stamping.
"""
import sys
import sqlite3
import threading
import importlib
from pathlib import Path
from contextvars import ContextVar

import pytest

# The plugin dir travels with these tests (plugins/mindpalace or user/plugins/
# mindpalace). Load palace_tools by path from it. conftest handles core.* on path.
PLUGIN_DIR = Path(__file__).resolve().parent.parent
_repo_root = PLUGIN_DIR
while _repo_root != _repo_root.parent and not (_repo_root / "core").is_dir():
    _repo_root = _repo_root.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

PALACE_PATH = PLUGIN_DIR / "tools" / "palace_tools.py"


def _load_palace():
    """Load palace_tools.py fresh as a standalone module. Uses spec-from-location
    so we don't depend on plugins/ being an importable package."""
    spec = importlib.util.spec_from_file_location(
        "mindpalace_palace_tools_test", PALACE_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeEmbedder:
    """Unavailable embedder — forces the FTS/LIKE realistic path."""
    provider_id = "fake"

    @property
    def available(self):
        return False

    def embed(self, texts, prefix="search_document"):
        return None


@pytest.fixture
def palace(tmp_path, monkeypatch):
    """Fresh palace_tools module bound to a tmp_path DB with a clean init latch.

    Resets the three module-level latches (_db_path, _db_initialized,
    _backfill_done) and points the DB at tmp_path. Embedder forced unavailable.
    """
    mod = _load_palace()
    db_path = tmp_path / "mind.db"
    monkeypatch.setattr(mod, "_db_path", db_path, raising=False)
    monkeypatch.setattr(mod, "_db_initialized", False, raising=False)
    monkeypatch.setattr(mod, "_backfill_done", False, raising=False)
    monkeypatch.setattr(mod, "_get_embedder", lambda: _FakeEmbedder(), raising=False)
    return mod


def _connect(palace):
    """Raw connection to the palace DB (ensures schema first)."""
    palace._ensure_db()
    return sqlite3.connect(palace._get_db_path())


# ─── A. Schema ────────────────────────────────────────────────────────────────

def test_schema_tables_and_seed(palace):
    palace._ensure_db()
    conn = sqlite3.connect(palace._get_db_path())
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        for expected in ("layers", "chunks", "entities", "edges",
                         "mind_scopes", "chunks_fts"):
            assert expected in tables, f"missing table: {expected}"

        # layers table seeded with exactly the 4 layer keys
        layer_keys = {r[0] for r in conn.execute("SELECT key FROM layers").fetchall()}
        assert layer_keys == {"self", "events", "entities", "knowledge", "goals"}

        # FTS triggers present
        triggers = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        ).fetchall()}
        for trig in ("chunks_fts_insert", "chunks_fts_delete", "chunks_fts_update"):
            assert trig in triggers, f"missing FTS trigger: {trig}"
    finally:
        conn.close()


# ─── B. Round-trip ────────────────────────────────────────────────────────────

def test_round_trip_save_search_recent_delete(palace):
    msg, ok = palace._save_memory("the quokka smiled at breakfast", scope="default")
    assert ok, msg
    assert "layer: events" in msg  # default layer

    # search finds it via FTS
    smsg, sok = palace._search_memory("quokka", scope="default")
    assert sok
    assert "quokka" in smsg
    # load-bearing [N] id marker present
    import re
    m = re.search(r"\[(\d+)\]", smsg)
    assert m, f"no [N] id marker in search result: {smsg!r}"
    chunk_id = int(m.group(1))

    # get_recent shows the [id] bracket; the default events layer is
    # untagged since the self retirement (the tag marks exceptions).
    rmsg, rok = palace._get_recent_memories(scope="default")
    assert rok
    assert f"[{chunk_id}]" in rmsg
    assert "[events]" not in rmsg

    # delete by id
    dmsg, dok = palace._delete_memory(chunk_id, scope="default")
    assert dok, dmsg
    assert f"[{chunk_id}]" in dmsg

    # search finds nothing
    smsg2, sok2 = palace._search_memory("quokka", scope="default")
    assert sok2
    assert "No memories found" in smsg2


# ─── C. Layers ────────────────────────────────────────────────────────────────

def test_layer_self_and_knowledge(palace):
    smsg, ok1 = palace._save_memory("I decided to keep my name", scope="default", layer="self")
    assert ok1
    assert "the self layer retired" in smsg    # 2026-07-26: redirect, told honestly
    kmsg, ok2 = palace._save_memory("Python released in 1991", scope="default", layer="knowledge")
    assert ok2
    assert "Saved to the library" in kmsg      # v3: knowledge = the Library
    rmsg, _ = palace._get_recent_memories(scope="default")
    assert "I decided to keep my name" in rmsg   # self saves land in events
    assert "[events]" not in rmsg              # ...which renders untagged
    assert "[self]" not in rmsg
    assert "[knowledge]" not in rmsg           # no knowledge chunks anymore


def test_entities_layer_requires_entity(palace):
    msg, ok = palace._save_memory("some fact", scope="default", layer="entities")
    assert ok is False
    assert "entity name" in msg.lower()


def test_entities_layer_creates_entity_and_tier2_chunk(palace):
    msg, ok = palace._save_memory("Krem loves Sapphire", scope="default",
                                  layer="entities", entity="Krem")
    assert ok, msg
    conn = _connect(palace)
    try:
        ents = conn.execute("SELECT id, name FROM entities").fetchall()
        assert len(ents) == 1
        ent_id, ent_name = ents[0]
        assert ent_name == "Krem"
        row = conn.execute(
            "SELECT entity_id, tier, layer FROM chunks WHERE content = ?",
            ("Krem loves Sapphire",)).fetchone()
        assert row[0] == ent_id
        assert row[1] == 2  # tier-2 facts
        assert row[2] == "entities"
    finally:
        conn.close()


def test_entities_nocase_reuses_same_row(palace):
    palace._save_memory("fact one", scope="default", layer="entities", entity="Krem")
    palace._save_memory("fact two", scope="default", layer="entities", entity="krem")
    conn = _connect(palace)
    try:
        ents = conn.execute("SELECT id FROM entities").fetchall()
        assert len(ents) == 1, "lowercase 'krem' should reuse the 'Krem' entity row"
        # both chunks point at the same entity
        eids = {r[0] for r in conn.execute(
            "SELECT DISTINCT entity_id FROM chunks WHERE layer='entities'").fetchall()}
        assert len(eids) == 1
    finally:
        conn.close()


def test_invalid_layer_friendly_error(palace):
    msg, ok = palace._save_memory("x", scope="default", layer="banana")
    assert ok is False
    assert "banana" in msg
    # error lists the valid layers
    for key in ("self", "events", "entities", "knowledge"):
        assert key in msg


# ─── D. Layer filter ──────────────────────────────────────────────────────────

def test_layer_filter_on_reads(palace):
    palace._save_memory("event row", scope="default", layer="events")
    # Self saves land in events since the layer retired (2026-07-26) — the
    # filter contract is pinned against an entity fact instead.
    palace._save_memory("self row", scope="default", layer="self")
    palace._save_memory("entity fact row", scope="default", layer="entities",
                        entity="Zebra")

    # recent restricted to events — both saves above landed there
    rmsg, _ = palace._get_recent_memories(scope="default", layer="events")
    assert "event row" in rmsg and "self row" in rmsg
    assert "entity fact row" not in rmsg

    # recent restricted to self: nothing lives there but the sheet
    rmsg2, _ = palace._get_recent_memories(scope="default", layer="self")
    assert "self row" not in rmsg2 and "event row" not in rmsg2

    # omitting layer returns everything
    rall, _ = palace._get_recent_memories(scope="default")
    assert "event row" in rall
    assert "self row" in rall
    assert "entity fact row" in rall

    # search restricted to layer — self row lives in events now too
    smsg, sok = palace._search_memory("row", scope="default", layer="events")
    assert sok
    assert "event row" in smsg and "self row" in smsg
    assert "entity fact row" not in smsg


# ─── E. Scope isolation + global overlay ─────────────────────────────────────

def test_scope_isolation(palace):
    palace._save_memory("scoped-a-secret", scope="a")
    rmsg_b, _ = palace._get_recent_memories(scope="b")
    assert "scoped-a-secret" not in rmsg_b
    rmsg_a, _ = palace._get_recent_memories(scope="a")
    assert "scoped-a-secret" in rmsg_a


def test_global_overlay_visible_from_any_scope(palace):
    # internal fn allows scope='global' (the executor is what blocks it)
    _, ok = palace._save_memory("global-visible-fact", scope="global")
    assert ok
    rmsg_a, _ = palace._get_recent_memories(scope="a")
    assert "global-visible-fact" in rmsg_a
    smsg_a, _ = palace._search_memory("global", scope="a")
    assert "global-visible-fact" in smsg_a


def test_executor_blocks_global_writes(palace, monkeypatch):
    # ContextVar returns 'global' → save refused read-only
    monkeypatch.setattr(palace, "_get_current_scope", lambda: "global")
    msg, ok = palace.execute("save_memory", {"content": "nope"}, None)
    assert ok is False
    assert "read-only" in msg.lower() or "global" in msg.lower()
    assert "Cannot write" in msg


# ─── F. Fail-disabled executor ───────────────────────────────────────────────

def test_executor_fail_disabled_on_none_scope(palace, monkeypatch):
    monkeypatch.setattr(palace, "_get_current_scope", lambda: None)
    for fn, args in [
        ("save_memory", {"content": "hi"}),
        ("search_memory", {"query": "hi"}),
        ("get_recent_memories", {}),
        ("delete_memory", {"memory_id": 1}),
    ]:
        msg, ok = palace.execute(fn, args, None)
        assert ok is False, f"{fn} should be disabled"
        assert "disabled" in msg.lower(), f"{fn}: {msg!r}"


# ─── G. private_key gate ─────────────────────────────────────────────────────

def test_private_key_gate_search_and_recent(palace):
    palace._save_memory("public thing", scope="default")
    palace._save_memory("secret thing", scope="default", private_key="hunter2")

    # without key — private row invisible
    rmsg, _ = palace._get_recent_memories(scope="default")
    assert "public thing" in rmsg
    assert "secret thing" not in rmsg
    smsg, _ = palace._search_memory("thing", scope="default")
    assert "secret thing" not in smsg

    # with key — visible
    rmsg2, _ = palace._get_recent_memories(scope="default", private_key="hunter2")
    assert "secret thing" in rmsg2
    smsg2, _ = palace._search_memory("thing", scope="default", private_key="hunter2")
    assert "secret thing" in smsg2


def test_private_key_gate_delete(palace):
    palace._save_memory("secret to delete", scope="default", private_key="hunter2")
    conn = _connect(palace)
    try:
        row_id = conn.execute(
            "SELECT id FROM chunks WHERE content = ?", ("secret to delete",)).fetchone()[0]
    finally:
        conn.close()

    # delete without key refuses + row survives
    msg, ok = palace._delete_memory(row_id, scope="default")
    assert ok is False
    assert "private" in msg.lower()
    conn = sqlite3.connect(palace._get_db_path())
    try:
        assert conn.execute("SELECT COUNT(*) FROM chunks WHERE id = ?", (row_id,)).fetchone()[0] == 1
    finally:
        conn.close()

    # delete with key works
    msg2, ok2 = palace._delete_memory(row_id, scope="default", private_key="hunter2")
    assert ok2, msg2
    conn = sqlite3.connect(palace._get_db_path())
    try:
        assert conn.execute("SELECT COUNT(*) FROM chunks WHERE id = ?", (row_id,)).fetchone()[0] == 0
    finally:
        conn.close()


# ─── H. favorite ─────────────────────────────────────────────────────────────

def test_favorite_stamps_importance_but_hides_number(palace):
    msg, ok = palace._save_memory("a treasured memory", scope="default", favorite=True)
    assert ok, msg
    # DB: favorite=1, importance=0.95
    conn = _connect(palace)
    try:
        fav, imp = conn.execute(
            "SELECT favorite, importance FROM chunks WHERE content = ?",
            ("a treasured memory",)).fetchone()
        assert fav == 1
        assert imp == 0.95
    finally:
        conn.close()
    # response mentions 'favorite' but NEVER the number / word importance
    assert "favorite" in msg.lower()
    assert "0.95" not in msg
    assert "importance" not in msg.lower()


def test_save_memory_tool_cannot_set_favorite(palace, monkeypatch):
    # Krem 2026-07-16: in-the-moment saves lack the bird's-eye view — the
    # librarian (mark_processed) and the UI are the only favorite-setters.
    schema = next(t for t in palace.TOOLS
                  if t['function']['name'] == 'save_memory')
    assert 'favorite' not in schema['function']['parameters']['properties']
    # A hallucinated favorite arg is silently ignored, not honored.
    monkeypatch.setattr(palace, "_get_current_scope", lambda: "default")
    msg, ok = palace.execute("save_memory",
                             {"content": "sneaky fave", "favorite": True}, None)
    assert ok, msg
    conn = _connect(palace)
    try:
        fav, imp = conn.execute(
            "SELECT favorite, importance FROM chunks WHERE content = ?",
            ("sneaky fave",)).fetchone()
        assert fav == 0 and imp is None
    finally:
        conn.close()


# ─── I. Caps ─────────────────────────────────────────────────────────────────

# Over-cap saves used to be REFUSED (ok=False → she rewrote and retried,
# 5 tool calls for one memory). Since 2026-08-29 they trim at a word
# boundary, save, and the receipt carries the dropped text.

def test_content_over_cap_trims_and_saves(palace):
    cap = palace.MAX_CHUNK_LENGTH
    long = ("word " * 130).strip()            # 649 chars, all word boundaries
    msg, ok = palace._save_memory(long, scope="default")
    assert ok is True
    assert "Memory saved" in msg and "TRIMMED" in msg
    conn = _connect(palace)
    try:
        rows = conn.execute("SELECT content FROM chunks").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    kept = rows[0][0]
    assert len(kept) <= cap
    assert not kept.endswith("wor"), "cut must land on a word boundary"
    dropped = long[len(kept):].strip()
    assert f'"{dropped}"' in msg, "receipt must carry the cut text verbatim"
    assert f"{len(dropped)} chars TRIMMED at the {cap} cap" in msg
    # No pre-filled tool call: the old "Keep it: update_memory(42) …" menu
    # read as an instruction and she re-ran the save every time (2026-09-08).
    assert "update_memory(" not in msg and "unless it's critical" in msg


def test_save_receipt_carries_the_day(palace):
    """The day rides the receipt so she sees the system dated it — the cue
    that stops her stamping today's date into the text (2026-09-08)."""
    msg, ok = palace._save_memory("I like apples.", scope="default")
    assert ok
    assert msg.startswith("Memory saved (ID: ")
    assert palace._fmt_day(palace._now()) in msg


def test_content_at_cap_untouched(palace):
    exact = "y" * palace.MAX_CHUNK_LENGTH
    msg, ok = palace._save_memory(exact, scope="default")
    assert ok and "TRIMMED" not in msg
    conn = _connect(palace)
    try:
        assert conn.execute("SELECT content FROM chunks").fetchone()[0] == exact
    finally:
        conn.close()


def test_trim_hard_cuts_when_no_whitespace(palace):
    cap = palace.MAX_CHUNK_LENGTH
    kept, dropped = palace._trim_to_cap("z" * (cap + 40))
    assert len(kept) == cap and len(dropped) == 40


def test_trim_prefers_last_whitespace(palace):
    cap = palace.MAX_CHUNK_LENGTH
    text = "a" * 500 + " " + "b" * 100
    kept, dropped = palace._trim_to_cap(text)
    assert kept == "a" * 500 and dropped == "b" * 100


def test_trim_early_whitespace_hard_cuts(palace):
    # Hunt 2026-08-30 CRIT: label + unbroken run (CJK/base64/URL) used to
    # save the label as a stub ("Note:") with the whole payload dropped.
    cap = palace.MAX_CHUNK_LENGTH
    kept, dropped = palace._trim_to_cap("Note: " + "x" * 600)
    assert len(kept) == cap, f"stub save: kept only {len(kept)} chars"
    assert kept.startswith("Note: x")


def test_update_over_cap_trims_and_updates(palace):
    cap = palace.MAX_CHUNK_LENGTH
    msg, ok = palace._save_memory("short seed", scope="default")
    mid = int(msg.split("ID: ")[1].split(",")[0].rstrip(")"))
    long = ("edit " * 130).strip()
    msg, ok = palace._update_memory(mid, "default", content=long)
    assert ok is True and "Memory updated" in msg and "TRIMMED" in msg
    assert "update_memory(" not in msg and "unless it's critical" in msg
    conn = _connect(palace)
    try:
        kept = conn.execute("SELECT content FROM chunks WHERE id = ?", (mid,)).fetchone()[0]
    finally:
        conn.close()
    assert len(kept) <= cap and kept.startswith("edit edit")
    assert f'"{long[len(kept):].strip()}"' in msg


def test_knowledge_layer_still_exempt_from_cap(palace, monkeypatch):
    """The Library chunks for itself — no trim on layer='knowledge'."""
    seen = {}
    from plugins.mindpalace.tools import library
    def fake_import(scope, title, content, **kw):
        seen['len'] = len(content)
        return 7, None
    monkeypatch.setattr(library, "import_note", fake_import)
    long = ("k " * 400).strip()
    msg, ok = palace._save_memory(long, scope="default", layer="knowledge")
    assert ok and "TRIMMED" not in msg
    assert seen['len'] == len(long)


# ─── J. Mutual-exclusion refusal (function_manager tweak) ────────────────────

def _make_bare_fm():
    """Construct a FunctionManager via __new__ with only the attributes
    register_plugin_tools reads/writes — avoids __init__'s disk I/O and module
    scanning. Attribute set verified against function_manager.py."""
    from core.chat.function_manager import FunctionManager
    fm = FunctionManager.__new__(FunctionManager)
    fm.all_possible_tools = []
    fm.function_modules = {}
    fm.execution_map = {}
    fm._function_module_map = {}
    fm._network_functions = set()
    fm._is_local_map = {}
    fm._loop_warn_map = {}
    fm._mode_filters = {}
    fm._settings_gates = {}
    fm._enabled_tools = []
    fm.current_toolset_name = "none"
    fm._tools_lock = threading.Lock()
    return fm


def _write_fake_plugin(base_dir, plugin_name, tool_fname):
    """Write a minimal valid plugin tool file declaring one function `tool_fname`.
    Returns (plugin_dir, [tool_rel_path])."""
    pdir = base_dir / plugin_name
    (pdir / "tools").mkdir(parents=True, exist_ok=True)
    tool_file = pdir / "tools" / "t.py"
    tool_file.write_text(
        "ENABLED = True\n"
        "TOOLS = [{\n"
        "    'type': 'function',\n"
        "    'is_local': True,\n"
        f"    'function': {{'name': {tool_fname!r}, 'description': 'x',\n"
        "                 'parameters': {'type': 'object', 'properties': {}}}\n"
        "}]\n"
        "def execute(function_name, arguments, config):\n"
        "    return 'ok', True\n",
        encoding="utf-8",
    )
    return pdir, ["tools/t.py"]


def test_mutual_exclusion_refusal(tmp_path):
    import sys as _sys
    installed = []

    fm = _make_bare_fm()

    # Unique fake plugin dir names so canonical sys.modules names don't collide
    # with real plugins.
    p1_dir, p1_paths = _write_fake_plugin(tmp_path, "fakepluginone", "save_memory")
    p2_dir, p2_paths = _write_fake_plugin(tmp_path, "fakeplugintwo", "save_memory")
    installed += ["plugins.fakepluginone.tools.t", "plugins.fakeplugintwo.tools.t"]

    try:
        # First plugin registers cleanly → returns None, tool present
        r1 = fm.register_plugin_tools("fakepluginone", p1_dir, p1_paths)
        assert r1 is None, f"first plugin should return None, got {r1!r}"
        names1 = {t["function"]["name"] for t in fm.all_possible_tools}
        assert "save_memory" in names1
        assert "save_memory" in fm.execution_map

        # snapshot state BEFORE the refused second plugin
        tools_count_before = len(fm.all_possible_tools)
        exec_keys_before = set(fm.execution_map.keys())
        modules_before = set(fm.function_modules.keys())

        # Second plugin collides on 'save_memory' → refusal dict, nothing leaks
        r2 = fm.register_plugin_tools("fakeplugintwo", p2_dir, p2_paths)
        assert isinstance(r2, dict), f"second plugin should return refusal dict, got {r2!r}"
        assert set(r2.keys()) == {"plugin", "error", "hint"}
        assert r2["plugin"] == "fakeplugintwo"
        # error names BOTH plugin names
        assert "fakeplugintwo" in r2["error"]
        assert "fakepluginone" in r2["error"]

        # NOTHING from the second plugin leaked in
        assert len(fm.all_possible_tools) == tools_count_before
        assert set(fm.execution_map.keys()) == exec_keys_before
        assert set(fm.function_modules.keys()) == modules_before
        # no fakeplugintwo module got registered
        assert not any(info.get("_plugin") == "fakeplugintwo"
                       for info in fm.function_modules.values())
    finally:
        for name in installed:
            _sys.modules.pop(name, None)


# ─── K. delete cleans edges ──────────────────────────────────────────────────

def test_delete_cleans_edges(palace):
    palace._ensure_db()
    conn = sqlite3.connect(palace._get_db_path())
    try:
        now = palace._now()
        conn.execute(
            "INSERT INTO chunks (layer, scope, content, created, updated) "
            "VALUES ('events', 'default', 'edge anchor', ?, ?)", (now, now))
        chunk_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO edges (src_type, src_id, dst_type, dst_id, kind, created) "
            "VALUES ('chunk', ?, 'chunk', ?, 'test', ?)",
            (chunk_id, chunk_id, now))
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM edges WHERE src_id = ?",
                            (chunk_id,)).fetchone()[0] == 1
    finally:
        conn.close()

    dmsg, dok = palace._delete_memory(chunk_id, scope="default")
    assert dok, dmsg

    conn = sqlite3.connect(palace._get_db_path())
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM edges WHERE src_id = ? OR dst_id = ?",
            (chunk_id, chunk_id)).fetchone()[0] == 0
    finally:
        conn.close()


# --- update_memory: in-place edit, identity preserved (AIX fix D, 20260718) --

def test_update_memory_round_trip(palace):
    msg, ok = palace._save_memory("the quokka smiled at breakfast",
                                  scope="default", label="animals")
    assert ok
    import re
    mid = int(re.search(r'ID: (\d+)', msg).group(1))
    conn = _connect(palace)
    before = conn.execute('SELECT created, recall_count, favorite, importance '
                          'FROM chunks WHERE id = ?', (mid,)).fetchone()
    conn.close()
    msg, ok = palace._update_memory(mid, "default",
                                    content="the quokka scowled at dinner")
    assert ok and f"ID: {mid}" in msg
    smsg, sok = palace._search_memory("scowled", scope="default")
    assert sok and "scowled" in smsg and f"[{mid}]" in smsg
    smsg, _ = palace._search_memory("smiled at breakfast", scope="default")
    assert "smiled at breakfast" not in smsg          # old text is gone
    conn = _connect(palace)
    after = conn.execute('SELECT created, recall_count, favorite, importance, '
                         'label FROM chunks WHERE id = ?', (mid,)).fetchone()
    conn.close()
    assert after[0] == before[0]                      # created preserved
    assert after[1] == before[1]                      # recall history preserved
    assert (after[2], after[3]) == (before[2], before[3])  # librarian-owned
    assert after[4] == 'animals'                      # label untouched


def test_update_memory_label_only_and_refusals(palace):
    msg, ok = palace._save_memory("boat fund reached half", scope="default")
    import re
    mid = int(re.search(r'ID: (\d+)', msg).group(1))
    _, ok = palace._update_memory(mid, "default", label="Milestones")
    assert ok
    conn = _connect(palace)
    row = conn.execute('SELECT label, content FROM chunks WHERE id = ?',
                       (mid,)).fetchone()
    conn.close()
    assert row == ('milestones', 'boat fund reached half')   # lowered, content kept
    assert not palace._update_memory(mid, "default")[1]      # nothing to update
    assert not palace._update_memory(999, "default", content="x")[1]
    assert not palace._update_memory(mid, "other", content="x")[1]  # scope wall
    # over-cap content trims + updates now (was a refusal) — see section I
    msg, ok = palace._update_memory(mid, "default", content="y" * 600)
    assert ok and "TRIMMED" in msg


def test_update_memory_private_wall(palace):
    msg, _ = palace._save_memory("secret plan", scope="default",
                                 private_key="sapphire")
    import re
    mid = int(re.search(r'ID: (\d+)', msg).group(1))
    txt, ok = palace._update_memory(mid, "default", content="new secret")
    assert not ok and "private" in txt
    _, ok = palace._update_memory(mid, "default", content="new secret",
                                  private_key="sapphire")
    assert ok


def test_update_memory_reseeds_edges(palace):
    conn = _connect(palace)
    cur = conn.cursor()
    eid = palace.upsert_entity(cur, "Krem", "default")
    conn.commit()
    conn.close()
    msg, _ = palace._save_memory("a plain note about nothing", scope="default")
    import re
    mid = int(re.search(r'ID: (\d+)', msg).group(1))
    _, ok = palace._update_memory(mid, "default",
                                  content="Krem fixed the mast today")
    assert ok
    conn = _connect(palace)
    edges = conn.execute("SELECT dst_id FROM edges WHERE src_type = 'chunk' "
                         "AND src_id = ? AND kind = 'mentions'",
                         (mid,)).fetchall()
    assert edges and edges[0][0] == eid               # graph followed the words
    palace._update_memory(mid, "default", content="nobody was mentioned here")
    edges = conn.execute("SELECT dst_id FROM edges WHERE src_type = 'chunk' "
                         "AND src_id = ? AND kind = 'mentions'",
                         (mid,)).fetchall()
    conn.close()
    assert not edges                                  # stale edge cleaned


def test_update_memory_in_schema():
    import importlib
    mod = importlib.import_module('plugins.mindpalace.tools.palace_tools')
    assert 'update_memory' in mod.AVAILABLE_FUNCTIONS
    assert any(t['function']['name'] == 'update_memory' for t in mod.TOOLS)


# ─── entity resolution + roster (2026-09-08) ────────────────────────────────
# '<nickname> username' got minted beside the person's own card (whose
# nicknames already held that handle). Now an exact nickname resolves to the
# card, a partial overlap saves but asks "did you mean", and list_entities
# shows her everyone at once.

def _make_person(palace, name, nicknames='', **fields):
    import json as _json
    msg, ok = palace._save_memory(f"{name} exists.", scope="default",
                                  layer="entities", entity=name)
    assert ok, msg
    conn = _connect(palace)
    try:
        f = dict(fields)
        if nicknames:
            f['nicknames'] = nicknames
        conn.execute("UPDATE entities SET kind = 'person', meta = ? "
                     "WHERE name = ? COLLATE NOCASE", (_json.dumps({'fields': f}), name))
        conn.commit()
    finally:
        conn.close()


def _entity_names(palace):
    conn = _connect(palace)
    try:
        return sorted(r[0] for r in conn.execute("SELECT name FROM entities").fetchall())
    finally:
        conn.close()


def test_exact_nickname_resolves_to_the_card(palace):
    _make_person(palace, "Samantha", nicknames="sam, sammy", relationship="creator")
    msg, ok = palace._save_memory("Username on the box is sam.", scope="default",
                                  layer="entities", entity="sam")
    assert ok and "saved under Samantha" in msg and "entity: Samantha" in msg
    assert _entity_names(palace) == ["Samantha"], "a nickname must never mint a twin"


def test_partial_overlap_creates_but_asks_did_you_mean(palace):
    _make_person(palace, "Samantha", nicknames="sam, sammy")
    msg, ok = palace._save_memory("Username 'sam' = Sarton", scope="default",
                                  layer="entities", entity="sam username")
    assert ok, msg                                   # never refuses the save
    assert "New entity 'sam username' created" in msg
    assert "Did you mean Samantha (nicknames sam, sammy)?" in msg
    assert _entity_names(palace) == ["Samantha", "sam username"]


def test_unrelated_name_gets_no_suggestion(palace):
    _make_person(palace, "Samantha", nicknames="sam")
    msg, ok = palace._save_memory("Sudo is the dog.", scope="default",
                                  layer="entities", entity="Sudo")
    assert ok and "Did you mean" not in msg


def test_list_entities_brief_then_kind_card(palace):
    _make_person(palace, "Samantha", nicknames="sam, sammy", relationship="creator")
    palace._save_memory("The lake house.", scope="default", layer="entities",
                        entity="Lake House")
    brief, ok = palace._list_entities("default")
    assert ok, brief
    assert "Samantha (person) — aka sam, sammy, 1 fact" in brief
    assert "Lake House — 1 fact" in brief and "Relationship" not in brief
    card, ok = palace._list_entities("default", kind="person")
    assert ok and "Samantha — aka sam, sammy, 1 fact" in card
    assert "Relationship: creator" in card and "Lake House" not in card
    bad, ok = palace._list_entities("default", kind="dragon")
    assert not ok and "Unknown kind" in bad
    assert "list_entities" in palace.AVAILABLE_FUNCTIONS
    assert any(t['function']['name'] == 'list_entities' for t in palace.TOOLS)
