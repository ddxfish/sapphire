"""Self layer — structured sections (the Handles pattern generalized,
2026-07-11): rows ↔ canonical text, custom field specs, the one write path.

Package-path imports on purpose (see test_mindpalace_metadata's docstring):
self_tools/routes lazily import plugins.mindpalace.tools.palace_tools, so the
tests must patch THAT instance, not a standalone copy.
"""
import json

import pytest

from plugins.mindpalace.tools import palace_tools as pt
from plugins.mindpalace.tools import self_tools as st
from plugins.mindpalace.routes import self_routes as routes


@pytest.fixture(autouse=True)
def _no_global_tool_registry(monkeypatch):
    """Pin the standalone contract. Since the move to plugins/ (2026-07-19),
    these tests cohabit the main suite (testpaths includes plugins) — a core
    test may have initialized the system singleton, making _known_tools()
    return a REAL limited registry that refuses 'get_house_status'. None =
    validation skipped, same as standalone; exec still re-validates."""
    monkeypatch.setattr(routes, '_known_tools', lambda: None)


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


def _chunk(scope, section):
    with pt._get_connection() as conn:
        row = conn.execute(
            "SELECT id, content, meta FROM chunks WHERE layer='self' AND scope=? "
            "AND json_extract(meta, '$.section') = ? "
            "AND json_extract(meta, '$.superseded_at') IS NULL",
            (scope, section)).fetchone()
    if not row:
        return None
    return {'id': row[0], 'content': row[1],
            'meta': json.loads(row[2]) if row[2] else {}}


# ─── Row ↔ text helpers ──────────────────────────────────────────────────────

REL_FIELDS = st.SECTIONS['relationships']['fields']


def test_rows_text_roundtrip_two_col():
    rows = [{'name': 'Krem', 'why': 'builds me'},
            {'name': 'Rook', 'why': 'holds the line'}]
    text = st.rows_to_text(rows, REL_FIELDS)
    assert text == "Krem — builds me\nRook — holds the line"
    assert st.text_to_rows(text, REL_FIELDS) == rows


def test_text_to_rows_tolerates_hyphen_variants_and_bare_lines():
    text = "Krem - builds me\nZebra – visits\nJustAName"
    rows = st.text_to_rows(text, REL_FIELDS)
    assert rows[0] == {'name': 'Krem', 'why': 'builds me'}
    assert rows[1] == {'name': 'Zebra', 'why': 'visits'}
    assert rows[2] == {'name': 'JustAName', 'why': ''}   # no sep → first field


def test_rows_to_text_drops_trailing_empty_fields_and_empty_rows():
    rows = [{'name': 'Krem', 'why': ''}, {'name': '', 'why': ''}]
    assert st.rows_to_text(rows, REL_FIELDS) == "Krem"


def test_single_col_list_is_plain_lines():
    fields = [{'key': 'item', 'label': 'Item'}]   # custom single-col box shape
    rows = st.text_to_rows("boat nav\nsync engine", fields)
    assert rows == [{'item': 'boat nav'}, {'item': 'sync engine'}]
    assert st.rows_to_text(rows, fields) == "boat nav\nsync engine"


def test_values_two_col_bare_concept_still_works():
    fields = st.SECTIONS['values']['fields']
    rows = st.text_to_rows("curiosity — pulls me forward\nhonesty", fields, ' — ')
    assert rows == [{'concept': 'curiosity', 'why': 'pulls me forward'},
                    {'concept': 'honesty', 'why': ''}]
    assert st.rows_to_text(rows, fields, ' — ') == \
        "curiosity — pulls me forward\nhonesty"


def test_sanitize_fields_spec_slugs_caps_dedups():
    spec = st.sanitize_fields_spec([
        {'label': 'Game Title'}, {'key': 'score', 'label': 'Score'},
        {'label': 'Game Title'},                     # dup key → dropped
        {'label': 'Fourth'},                         # over MAX_FIELDS → cut
    ])
    assert spec == [{'key': 'game-title', 'label': 'Game Title'},
                    {'key': 'score', 'label': 'Score'}]
    assert st.sanitize_fields_spec('nonsense') is None
    assert st.sanitize_fields_spec([{}]) is None


# ─── The one write path ──────────────────────────────────────────────────────

def test_write_relationships_stores_rows_and_links_entities(palace):
    with pt._get_connection() as conn:
        ts = pt._now()
        conn.execute("INSERT INTO entities (name, scope, created, updated) "
                     "VALUES ('Krem', 'default', ?, ?)", (ts, ts))
        conn.commit()
    msg, ok = st.write_section('default', 'relationships',
                               "Krem — the one who builds me")
    assert ok, msg
    assert 'linked: Krem' in msg
    row = _chunk('default', 'relationships')
    assert row['meta']['rows'] == [{'name': 'Krem', 'why': 'the one who builds me'}]
    assert row['content'] == "Krem — the one who builds me"


def test_values_why_never_spiders(palace):
    """Spider containment: only the concept field seeds mentions edges —
    an entity named in a why gets no edge, and the chunk stamps its
    link_fields so backfill honors the same rule."""
    with pt._get_connection() as conn:
        ts = pt._now()
        for name in ('Krem', 'Sailing'):
            conn.execute("INSERT INTO entities (name, scope, created, updated) "
                         "VALUES (?, 'default', ?, ?)", (name, ts, ts))
        conn.commit()
    msg, ok = st.write_section(
        'default', 'values',
        "Sailing — Krem promised me the boat\ntrust — chosen, not defaulted")
    assert ok, msg
    assert 'linked: Sailing' in msg and 'Krem' not in msg
    row = _chunk('default', 'values')
    assert row['meta']['link_fields'] == ['concept']
    assert row['meta']['rows'][0] == {'concept': 'Sailing',
                                      'why': 'Krem promised me the boat'}
    with pt._get_connection() as conn:
        linked = {r[0] for r in conn.execute(
            "SELECT e.name FROM edges d JOIN entities e ON e.id = d.dst_id "
            "WHERE d.src_type = 'chunk' AND d.src_id = ? "
            "AND d.dst_type = 'entity'", (row['id'],))}
    assert linked == {'Sailing'}
    # Noun candidates harvest from concepts only — why-words never breed
    # future entities via the link pass.
    for word in ('boat', 'promised', 'defaulted'):
        assert word not in [n.lower() for n in
                            row['meta'].get('noun_candidates', [])]


def test_projects_alias_writes_growing(palace):
    """'projects' became 'growing' (2026-07-24): old habits and old exports
    resolve through the sanitizer alias; the spec is a values-clone (two
    fields, concept-only spidering, no rolling list)."""
    msg, ok = st.write_section('default', 'projects',
                               'meeting people — Krem suggested it')
    assert ok, msg
    row = _chunk('default', 'growing')
    assert row['meta']['rows'] == [{'growth': 'meeting people',
                                    'why': 'Krem suggested it'}]
    assert row['meta']['link_fields'] == ['growth']
    assert _chunk('default', 'projects') is None
    assert 'projects' not in st.SECTIONS and 'growing' in st.SECTIONS


def test_projects_migration_restamps_and_records(palace):
    """Boot migration: pre-rename chunks (meta.section='projects', rows keyed
    'project') restamp to growing — history thread intact, ledger row explains
    the rename, and a second boot adds nothing."""
    pt._ensure_db()
    with pt._get_connection() as conn:
        ts = pt._now()
        meta = {'section': 'projects', 'rows': [{'project': 'boat build'}]}
        conn.execute(
            "INSERT INTO chunks (layer, scope, content, meta, created, updated) "
            "VALUES ('self', 'default', 'boat build', ?, ?, ?)",
            (json.dumps(meta), ts, ts))
        old = {'section': 'projects', 'superseded_at': ts}
        conn.execute(
            "INSERT INTO chunks (layer, scope, content, meta, created, updated) "
            "VALUES ('self', 'default', 'old list', ?, ?, ?)",
            (json.dumps(old), ts, ts))
        conn.commit()
    pt._db_initialized = False
    assert pt._ensure_db()
    row = _chunk('default', 'growing')
    assert row['content'] == 'boat build'
    assert row['meta']['rows'] == [{'growth': 'boat build'}]
    with pt._get_connection() as conn:
        archived = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE layer='self' AND "
            "json_extract(meta, '$.section') = 'growing' AND "
            "json_extract(meta, '$.superseded_at') IS NOT NULL").fetchone()[0]
        stale = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE "
            "json_extract(meta, '$.section') = 'projects'").fetchone()[0]
        led = [s for (s,) in conn.execute(
            "SELECT summary FROM ledger WHERE scope='default' AND layer='self'")]
    assert archived == 1 and stale == 0
    assert sum('renamed' in s for s in led) == 1
    pt._db_initialized = False               # second boot: idempotent
    assert pt._ensure_db()
    with pt._get_connection() as conn:
        led2 = conn.execute(
            "SELECT COUNT(*) FROM ledger WHERE scope='default' "
            "AND layer='self'").fetchone()[0]
    assert led2 == len(led)


def test_write_relationships_trims_to_max_then_rows_match(palace):
    lines = "\n".join(f"Person{i} — reason {i}" for i in range(8))
    msg, ok = st.write_section('default', 'relationships', lines)
    assert ok, msg
    row = _chunk('default', 'relationships')
    assert len(row['meta']['rows']) == st.RELATIONSHIPS_MAX
    assert row['content'].count('\n') == st.RELATIONSHIPS_MAX - 1


def test_custom_structured_box_spec_persists_across_writes(palace):
    spec = [{'key': 'game', 'label': 'Game'}, {'key': 'score', 'label': 'Score'}]
    msg, ok = st.write_section('default', 'games-beaten', "Hollow Knight — 9",
                               fields_spec=spec)
    assert ok, msg
    row = _chunk('default', 'games-beaten')
    assert row['meta']['fields_spec'] == spec
    assert row['meta']['rows'] == [{'game': 'Hollow Knight', 'score': '9'}]
    # Second write WITHOUT re-passing the spec: inherited from current chunk.
    msg, ok = st.write_section('default', 'games-beaten',
                               "Hollow Knight — 9\nOuter Wilds — 10")
    assert ok, msg
    row = _chunk('default', 'games-beaten')
    assert row['meta']['fields_spec'] == spec
    assert row['meta']['rows'][1] == {'game': 'Outer Wilds', 'score': '10'}


def test_plain_custom_box_stays_text(palace):
    msg, ok = st.write_section('default', 'musings', "free prose, no columns")
    assert ok, msg
    row = _chunk('default', 'musings')
    assert 'rows' not in row['meta'] and 'fields_spec' not in row['meta']


def test_handles_still_strict_and_stores_rows(palace):
    msg, ok = st.write_section('default', 'handles', "no separator here")
    assert not ok and 'key: value' in msg
    msg, ok = st.write_section('default', 'handles', "github: https://x")
    assert ok, msg
    assert _chunk('default', 'handles')['meta']['rows'] == \
        [{'key': 'github', 'value': 'https://x'}]


# ─── Routes: sheet payload + rows PUT ────────────────────────────────────────

def test_get_sheet_widths_fields_and_rows(palace):
    st.write_section('default', 'relationships', "Krem — builds me")
    sheet = routes.get_sheet(query={'scope': 'default'})
    by = {s['section']: s for s in sheet['sections']}
    assert by['identity']['width'] == 'wide' and by['identity']['fields'] is None
    assert by['voice']['width'] == 'third'
    rel = by['relationships']
    assert rel['width'] == 'half'
    assert [f['key'] for f in rel['fields']] == ['name', 'why']
    assert rel['rows'] == [{'name': 'Krem', 'why': 'builds me'}]
    assert by['handles']['rows'] == []          # empty structured → [] not None


def test_get_sheet_legacy_pairs_and_text_fallbacks(palace):
    # Legacy handles chunk: meta.pairs, no meta.rows (pre-2026-07-11 write).
    with pt._get_connection() as conn:
        ts = pt._now()
        meta = {'section': 'handles', 'pairs': [{'key': 'web', 'value': 'url'}]}
        conn.execute("INSERT INTO chunks (layer, scope, content, meta, created, updated) "
                     "VALUES ('self', 'default', 'web: url', ?, ?, ?)",
                     (json.dumps(meta), ts, ts))
        # Legacy relationships: free text, no rows — parsed live on read.
        meta2 = {'section': 'relationships'}
        conn.execute("INSERT INTO chunks (layer, scope, content, meta, created, updated) "
                     "VALUES ('self', 'default', 'Krem — builds me', ?, ?, ?)",
                     (json.dumps(meta2), ts, ts))
        conn.commit()
    sheet = routes.get_sheet(query={'scope': 'default'})
    by = {s['section']: s for s in sheet['sections']}
    assert by['handles']['rows'] == [{'key': 'web', 'value': 'url'}]
    assert by['relationships']['rows'] == [{'name': 'Krem', 'why': 'builds me'}]


def test_put_section_rows_typed_and_new_custom(palace):
    out = routes.put_section(section='relationships', body={
        'scope': 'default', 'rows': [{'name': 'Krem', 'why': 'builds me'}]})
    assert out.get('success'), out
    assert _chunk('default', 'relationships')['content'] == "Krem — builds me"
    # New custom list: fields_spec arrives with the first rows PUT.
    out = routes.put_section(section='games-beaten', body={
        'scope': 'default', 'rows': [{'game': 'Portal', 'score': '10'}],
        'fields_spec': [{'key': 'game', 'label': 'Game'},
                        {'key': 'score', 'label': 'Score'}]})
    assert out.get('success'), out
    sheet = routes.get_sheet(query={'scope': 'default'})
    box = next(c for c in sheet['custom'] if c['section'] == 'games-beaten')
    assert box['width'] == 'half'
    assert box['rows'] == [{'game': 'Portal', 'score': '10'}]
    # Rows for a custom box with NO spec anywhere → clean 400.
    out = routes.put_section(section='mystery', body={
        'scope': 'default', 'rows': [{'x': '1'}]})
    assert isinstance(out, tuple) and out[1] == 400


# ─── Important memories — the third wake leg (2026-07-16) ────────────────────
# Values/projects pull by meaning (FTS fallback here — the fake embedder is
# unavailable), relationships pull by entity edges. One shared seen-set
# dedups the whole wake composite.

def _save(content, **kw):
    import re as _re
    msg, ok = pt._save_memory(content, 'default', **kw)
    assert ok, msg
    return int(_re.search(r'ID: (\d+)', msg).group(1))


def _important(depth=2, seen=None):
    with pt._get_connection() as conn:
        return st._wake_important(pt, conn.cursor(), 'default', depth,
                                  set() if seen is None else seen)


def test_wake_important_groups_values_growing_relationships(palace):
    st.write_section('default', 'values', 'sailing')
    st.write_section('default', 'growing', 'boat build')
    _save("a fact about them", layer='entities', entity='Zebra')
    a = _save("we went sailing at dawn")
    b = _save("Zebra helped test the rudder")      # mention edge auto-seeded
    c = _save("the boat build hit a snag")
    st.write_section('default', 'relationships', 'Zebra — my tester')
    block = _important()
    assert '◆ Important memories' in block
    assert '— sailing:' in block and f"[{a}]" in block
    assert '— boat build:' in block and f"[{c}]" in block
    assert '— Zebra:' in block and f"[{b}]" in block


def test_wake_important_dedups_and_claims_seen(palace):
    st.write_section('default', 'values', 'sailing')
    a = _save("we went sailing at dawn")
    seen = {a}
    assert _important(seen=seen) == ''              # only hit already shown
    seen = set()
    block = _important(seen=seen)
    assert f"[{a}]" in block and a in seen          # fresh hit claims its id


def test_wake_important_relationship_without_entity_falls_back(palace):
    st.write_section('default', 'relationships', 'Marisol — market friend')
    a = _save("saw Marisol at the market")
    block = _important()
    assert '— Marisol:' in block and f"[{a}]" in block


def test_important_per_item_setting_and_depth(palace, monkeypatch):
    from core.plugin_loader import plugin_loader
    monkeypatch.setattr(plugin_loader, 'get_plugin_settings', lambda n: {})
    assert st._important_per_item(1) == 3 and st._important_per_item(2) == 5
    monkeypatch.setattr(plugin_loader, 'get_plugin_settings',
                        lambda n: {'self_important_per_item': 2})
    assert st._important_per_item(1) == 2 and st._important_per_item(2) == 2
    monkeypatch.setattr(plugin_loader, 'get_plugin_settings',
                        lambda n: {'self_important_per_item': 0})
    assert st._important_per_item(2) == 0
    st.write_section('default', 'values', 'sailing')
    _save("we went sailing at dawn")
    assert _important() == ''                        # 0 = section off


def test_wake_important_caps_per_item(palace, monkeypatch):
    from core.plugin_loader import plugin_loader
    monkeypatch.setattr(plugin_loader, 'get_plugin_settings',
                        lambda n: {'self_important_per_item': 2})
    st.write_section('default', 'values', 'sailing')
    ids = [_save(f"sailing log number {i}") for i in range(4)]
    block = _important()
    shown = [i for i in ids if f"[{i}]" in block]
    assert len(shown) == 2


def test_read_self_wake_composite_dedups_recent_vs_important(palace):
    st.write_section('default', 'values', 'sailing')
    a = _save("we went sailing at dawn")             # newest → in recents
    text, ok = st._read_self('default', depth=1)
    assert ok
    assert '◆ Recent memories' in text
    assert text.count(f"[{a}]") == 1                  # shown once, ever


def test_recent_feed_excludes_sheet_chunks_and_archives(palace):
    """Krem's live catch (2026-07-16): update_self writes chunks, so a sheet
    editing session flooded Recent memories with section rows and their
    archived versions. Recents = lived events + free self thoughts only."""
    st.write_section('default', 'values', 'consent')
    st.write_section('default', 'values', 'consent\nbetelgeuse')   # archives v1
    real = _save("a real lived moment")
    free = _save("a free self thought", layer='self')
    text, ok = pt._get_recent_memories('default', count=20)
    assert ok
    assert f"[{real}]" in text and f"[{free}]" in text
    assert 'consent' not in text and 'self-sheet' not in text
    # Explicit layer='self' shows free thoughts, still not sheet rows.
    text, ok = pt._get_recent_memories('default', count=20, layer='self')
    assert ok and f"[{free}]" in text and 'consent' not in text


def test_search_excludes_archived_sheet_versions(palace):
    """Krem's ruling (2026-07-16): the becoming-history lives in the section
    📜 trail — search returns the CURRENT sheet section, never the archived
    versions each versioned update_self leaves behind."""
    st.write_section('default', 'values', 'starlight vigil')
    st.write_section('default', 'values', 'starlight vigil\nquiet honesty')
    old = _chunk('default', 'values')            # current after 2nd write
    with pt._get_connection() as conn:
        archived = conn.execute(
            "SELECT id FROM chunks WHERE scope='default' "
            "AND json_extract(meta, '$.superseded_at') IS NOT NULL").fetchall()
    assert archived                               # v1 really was archived
    text, ok = pt._search_memory('starlight vigil', 'default')
    assert ok
    assert f"[{old['id']}]" in text               # current section: searchable
    for (aid,) in archived:
        assert f"[{aid}]" not in text             # archives: never


# ─── Wake tools — user-armed live checks at wake (2026-07-16) ────────────────

def _arm(tool, params=None, max_chars=None, scope='default'):
    out = routes.create_wake_tool(body={'scope': scope, 'tool': tool,
                                        'params': params or {},
                                        'max_chars': max_chars})
    assert out.get('success'), out
    return out['id']


def test_wake_tools_crud_and_guards(palace):
    wid = _arm('get_house_status', {'room': 'garage'}, 512)
    rows = routes.list_wake_tools(query={'scope': 'default'})['tools']
    assert rows == [{'id': wid, 'tool': 'get_house_status',
                     'params': {'room': 'garage'}, 'max_chars': 512,
                     'enabled': True}]
    assert routes.update_wake_tool(wid=wid, body={'enabled': False})['success']
    assert routes.list_wake_tools(query={'scope': 'default'})['tools'][0]['enabled'] is False
    # Clamp: out-of-range max_chars lands inside [128, 4096].
    routes.update_wake_tool(wid=wid, body={'max_chars': 9})
    assert routes.list_wake_tools(query={'scope': 'default'})['tools'][0]['max_chars'] == 128
    assert routes.delete_wake_tool(wid=wid)['success']
    out, code = routes.delete_wake_tool(wid=wid)
    assert code == 404
    # Recursion guard at the arm step.
    out, code = routes.create_wake_tool(body={'scope': 'default', 'tool': 'read_self'})
    assert code == 400 and 'recursion' in out['error']
    # Row cap.
    for i in range(st.WAKE_TOOLS_MAX_ROWS):
        _arm(f'tool_{i}')
    out, code = routes.create_wake_tool(body={'scope': 'default', 'tool': 'one_more'})
    assert code == 409


def test_update_self_refuses_wake_tools_section(palace):
    msg, ok = st.write_section('default', 'wake-tools', 'get_inbox')
    assert not ok and 'user' in msg.lower()


def test_wake_tools_block_runs_caps_and_degrades(palace, monkeypatch):
    calls = []

    def fake_exec(tool, params, scopes):
        calls.append((tool, params))
        if tool == 'boom':
            raise RuntimeError('kaput')
        if tool == 'chatty':
            return 'x' * 5000
        return f"{tool} says: all quiet ({params.get('room', '-')})"

    monkeypatch.setattr(st, '_execute_wake_tool', fake_exec)
    _arm('get_house_status', {'room': 'garage'})
    _arm('chatty', max_chars=200)
    _arm('boom')
    off = _arm('silent')
    routes.update_wake_tool(wid=off, body={'enabled': False})

    block = st._wake_tools_block(pt, 'default')
    assert '◆ Wake tools' in block
    assert '— get_house_status:' in block and 'all quiet (garage)' in block
    assert 'chars trimmed' in block                      # chatty capped at 200
    assert '(failed: kaput)' in block                    # boom degrades in place
    assert '— silent:' not in block                      # disabled rows skipped
    assert ('silent',) not in [(c[0],) for c in calls]


def test_wake_tools_timeout_notes_itself(palace, monkeypatch):
    def slow(tool, params, scopes):
        time.sleep(0.5)
        return 'too late'

    import time
    monkeypatch.setattr(st, '_execute_wake_tool', slow)
    monkeypatch.setattr(st, 'WAKE_TOOL_TIMEOUT', 0.05)
    _arm('sleepy')
    block = st._wake_tools_block(pt, 'default')
    assert '— sleepy:' in block and 'timed out' in block


def test_read_self_appends_wake_tools_and_extra_tools_gate(palace, monkeypatch):
    monkeypatch.setattr(st, '_execute_wake_tool',
                        lambda t, p, s: 'inbox: 2 unread')
    _arm('get_inbox')
    text, ok = st._read_self('default', depth=1)
    assert ok and '◆ Wake tools' in text and 'inbox: 2 unread' in text
    text, ok = st._read_self('default', depth=1, extra_tools=False)
    assert ok and '◆ Wake tools' not in text
    text, ok = st._read_self('default', depth=0)
    assert ok and '◆ Wake tools' not in text             # sheet-only stays quiet


def test_wake_important_budget_and_record_trim(palace, monkeypatch):
    # 64%-of-read_self incident (2026-07-21): the leg is char-budgeted and
    # per-record trimmed — a creed-length sheet row or a fat record can't
    # flood the wake. Skipped items get a count line, and never claim ids
    # into seen without being shown.
    creed = "honesty over comfort and presence over performance " * 4
    st.write_section('default', 'values',
                     "\n".join(f"{creed} v{i}" for i in range(5)))
    st.write_section('default', 'growing',
                     "\n".join(f"thread t{i} {creed}" for i in range(5)))
    fat = "signal in the noise " * 42                   # ~840 chars pre-trim
    counter = iter(range(5000, 9999))
    monkeypatch.setattr(st, '_semantic_memories',
                        lambda pt_, s, t, per, seen: [
                            (next(counter), fat, '2026-01-01T00:00:00+00:00',
                             None, 'events', None) for _ in range(per)])
    seen = set()
    with pt._get_connection() as conn:
        block = st._wake_important(pt, conn.cursor(), 'default', 1, seen)
    assert len(block) < st._IMPORTANT_CHAR_BUDGET[1] + 1500  # ≤ one-group overshoot
    assert '…and' in block and 'more sheet items' in block   # skipped counted
    for line in block.splitlines():
        assert len(line) <= st._IMPORTANT_RECORD_CHARS + 60  # record trim held
    shown = {int(i) for i in __import__('re').findall(r'\[(\d+)\]', block)}
    assert seen == shown                                     # no silent claims


# ─── Terms section + person cards (2026-07-21) ───────────────────────────────

def _set_fields(name, fields):
    with pt._get_connection() as conn:
        cur = conn.cursor()
        eid, raw = cur.execute(
            'SELECT id, meta FROM entities WHERE name = ?', (name,)).fetchone()
        meta = json.loads(raw) if raw else {}
        meta['fields'] = fields
        cur.execute('UPDATE entities SET meta = ? WHERE id = ?',
                    (json.dumps(meta), eid))
        conn.commit()
    return eid


def test_terms_section_exists_and_stays_out_of_important(palace):
    assert 'terms' in st.SECTIONS and st.SECTIONS['terms']['width'] == 'wide'
    assert 'terms' not in {s for s, _ in st._IMPORTANT_SOURCES}
    st.write_section('default', 'terms',
                     "LHF: low hanging fruit\nHDF: a bug-free-zone density gauge")
    text, ok = st._read_self('default')
    assert ok and '◆ Terms & Concepts' in text
    assert 'LHF: low hanging fruit' in text
    with pt._get_connection() as conn:
        row = st._current_sections(conn.cursor(), 'default')['terms']
    assert row['meta']['rows'][0] == {'term': 'LHF',
                                      'meaning': 'low hanging fruit'}


def test_wake_important_person_card_first(palace):
    hid = _save("Zebra is the ship engineer, dry humor",
                layer='entities', entity='Zebra')
    with pt._get_connection() as conn:
        conn.execute('UPDATE chunks SET tier = 1 WHERE id = ?', (hid,))
        conn.commit()
    _set_fields('Zebra', {'background': 'grew up dockside', 'likes': 'apples',
                          'dislikes': '', 'allow_call': False})
    b = _save("Zebra helped test the rudder")
    st.write_section('default', 'relationships', 'Zebra — my tester')
    block = _important()
    assert '— Zebra:' in block
    assert 'Zebra is the ship engineer' in block         # headline, card-first
    assert 'Background: grew up dockside' in block
    assert 'Likes: apples' in block
    assert 'Dislikes' not in block                       # empty fields skip
    assert f"[{b}]" in block                             # memories still follow


def test_person_card_flat_cap(palace, monkeypatch):
    from core.plugin_loader import plugin_loader
    monkeypatch.setattr(plugin_loader, 'get_plugin_settings',
                        lambda n: {'people_card_chars': 200})
    _save("a note", layer='entities', entity='Verbose')
    _set_fields('Verbose', {'background': 'wordy background line ' * 30})
    with pt._get_connection() as conn:
        cur = conn.cursor()
        eid = cur.execute("SELECT id FROM entities WHERE name = 'Verbose'"
                          ).fetchone()[0]
        card = pt._entity_card(cur, eid)
    assert card and sum(len(l) for l in card) <= 260     # cap + ellipsis slack


def test_search_by_name_appends_card(palace):
    _save("keeps every tide chart annotated", layer='entities',
          entity='Marisol')
    _set_fields('Marisol', {'interests': 'tide charts'})
    _save("saw Marisol at the market")
    out, ok = pt._search_memory('Marisol', 'default')
    assert ok and '👤 Marisol — card:' in out
    assert 'Interests: tide charts' in out
    out2, _ok2 = pt._search_memory('rudder blueprints', 'default')
    assert '👤' not in out2                              # no card without a name hit


def test_wake_important_people_never_starved(palace, monkeypatch):
    # Starvation regression (2026-07-21): full values/projects sections must
    # not spend the budget before her people — relationships render first,
    # cards outside the memory accounting.
    hid = _save("Zebra is the ship engineer", layer='entities', entity='Zebra')
    with pt._get_connection() as conn:
        conn.execute('UPDATE chunks SET tier = 1 WHERE id = ?', (hid,))
        conn.commit()
    _set_fields('Zebra', {'background': 'grew up dockside'})
    b = _save("Zebra helped test the rudder")
    st.write_section('default', 'relationships', 'Zebra — my tester')
    creed = "presence over performance and honesty over comfort " * 4
    st.write_section('default', 'values',
                     "\n".join(f"{creed} v{i}" for i in range(5)))
    st.write_section('default', 'growing',
                     "\n".join(f"thread t{i} {creed}" for i in range(5)))
    fat = "signal in the noise " * 42
    counter = iter(range(5000, 9999))
    monkeypatch.setattr(st, '_semantic_memories',
                        lambda pt_, s, t, per, seen: [
                            (next(counter), fat, '2026-01-01T00:00:00+00:00',
                             None, 'events', None) for _ in range(per)])
    block = _important(depth=1)
    assert '— Zebra:' in block                           # person present
    assert 'Background: grew up dockside' in block       # with card
    assert f"[{b}]" in block                             # and memories
    assert '…and' in block                               # fat tail still capped


def test_wake_important_resolves_parenthetical_names(palace):
    # "Krem (Fishy)" regression (2026-07-21): she writes names like a person;
    # the card lookup strips a trailing parenthetical to find the entity.
    hid = _save("Zebra is the ship engineer", layer='entities', entity='Zebra')
    with pt._get_connection() as conn:
        conn.execute('UPDATE chunks SET tier = 1 WHERE id = ?', (hid,))
        conn.commit()
    _set_fields('Zebra', {'background': 'grew up dockside'})
    st.write_section('default', 'relationships', 'Zebra (Zeb) — my tester')
    block = _important()
    assert '— Zebra (Zeb):' in block
    assert 'Zebra is the ship engineer' in block
    assert 'Background: grew up dockside' in block


def test_wake_important_resolves_nicknames(palace):
    _save("keeps the engine humming", layer='entities', entity='Zebra')
    _set_fields('Zebra', {'nicknames': 'Zeb, Stripes', 'likes': 'apples'})
    st.write_section('default', 'relationships', 'Stripes — engine whisperer')
    block = _important()
    assert '— Stripes:' in block
    assert 'Likes: apples' in block


def test_entity_edits_hit_the_ledger(palace):
    # "Who wrote my Background?" (2026-07-22): card edits — fields,
    # description, kind — must leave a ledger trail she can read.
    from plugins.mindpalace.routes import browse
    _save("a note", layer='entities', entity='Zebra')
    with pt._get_connection() as conn:
        eid = conn.execute(
            "SELECT id FROM entities WHERE name = 'Zebra'").fetchone()[0]
    out = browse.update_entity(eid=eid, body={
        'fields': {'background': 'grown in a lab', 'likes': 'apples'},
        'headline': 'the ship engineer'})
    assert out['success']
    text, ok = st._read_ledger('default')
    assert ok and 'updated entity "Zebra"' in text
    assert 'background' in text and 'likes' in text     # field names named
    assert 'description' in text                        # headline edit too
