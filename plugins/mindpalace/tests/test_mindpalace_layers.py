"""Plugin Layers v1.1 (2026-07-12) — plugins register memory layers.

Covers: core registry (reserved/dup refusal, writable), dynamic tool enums,
layer_api (fence, save provenance, bulk one-ledger-row + backfill re-arm,
delete_by_source reconciliation), dark-layer lifecycle (off means off —
search/recent/browse/spider all exclude; re-enable lights up), librarian
opt-in, scope-delete totality, and the UI layers route.
"""
import json

import pytest

from core import memory_layers as ml
from plugins.mindpalace.tools import palace_tools as pt
from plugins.mindpalace.tools import layer_api
from plugins.mindpalace.tools import librarian
from plugins.mindpalace.tools import spider
from plugins.mindpalace.routes import browse

PLUG = 'test-lore-plug'


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
    monkeypatch.setattr(pt, "_dark_cache", None, raising=False)
    monkeypatch.setattr(pt, "_get_embedder", lambda: _FakeEmbedder(), raising=False)
    ml.unregister_plugin(PLUG)
    yield pt
    ml.unregister_plugin(PLUG)


def _register(**over):
    spec = {'label': 'Lore', 'icon': '📜', 'description': 'demo lore',
            'librarian': False, 'writable': False}
    spec.update(over)
    assert ml.register_layer('lore', spec, PLUG)


# ─── Core registry ───────────────────────────────────────────────────────────

def test_reserved_dup_and_bad_keys_refused(palace):
    assert not ml.register_layer('self', {'label': 'X'}, PLUG)
    assert not ml.register_layer('events', {'label': 'X'}, PLUG)
    assert not ml.register_layer('not-an-identifier!', {'label': 'X'}, PLUG)
    assert not ml.register_layer('lore', {'mode': 'federated'}, PLUG)  # v1.1: mirror only
    _register()
    assert not ml.register_layer('lore', {'label': 'Steal'}, 'other-plug')
    assert ml.get_layers()['lore']['plugin_name'] == PLUG
    # same-plugin re-register updates in place
    _register(label='Lore v2')
    assert ml.get_layers()['lore']['label'] == 'Lore v2'


def test_unregister_bumps_generation(palace):
    g0 = ml.generation()
    _register()
    assert ml.generation() > g0
    g1 = ml.generation()
    ml.unregister_plugin(PLUG)
    assert ml.generation() > g1
    assert 'lore' not in ml.get_layers()


# ─── Dynamic tool schemas ────────────────────────────────────────────────────

def test_enums_read_everywhere_write_only_if_writable(palace):
    _register()   # writable False
    tools = {t['function']['name']: t['function'] for t in pt.get_tools()}
    assert 'lore' in tools['search_memory']['parameters']['properties']['layer']['enum']
    assert 'lore' in tools['get_recent_memories']['parameters']['properties']['layer']['enum']
    assert 'lore' not in tools['save_memory']['parameters']['properties']['layer']['enum']
    assert 'lore' in tools['search_memory']['description']
    _register(writable=True)
    tools = {t['function']['name']: t['function'] for t in pt.get_tools()}
    assert 'lore' in tools['save_memory']['parameters']['properties']['layer']['enum']
    ml.unregister_plugin(PLUG)
    assert pt.get_tools() is pt.TOOLS   # no plugin layers → static schema


def test_save_memory_tool_fence(palace, monkeypatch):
    # scope_memory is a plugin-registered ContextVar, absent under pytest —
    # pin the scope so execute() reaches the fence.
    monkeypatch.setattr(pt, '_get_current_scope', lambda: 'default')
    _register()   # writable False
    msg, ok = pt.execute('save_memory', {'content': 'sneaky', 'layer': 'lore'}, None)
    assert not ok and 'read-only plugin layer' in msg
    _register(writable=True)
    msg, ok = pt.execute('save_memory', {'content': 'allowed now', 'layer': 'lore'}, None)
    assert ok


# ─── layer_api ───────────────────────────────────────────────────────────────

def test_layer_api_fence_and_save_provenance(palace):
    _register()
    cid, msg = layer_api.save('nope', layer='events')
    assert cid is None and 'not a registered plugin layer' in msg
    cid, msg = layer_api.save('The old lighthouse keeps a logbook.',
                              layer='lore', source='vault/lighthouse.md')
    assert cid is not None
    with pt._get_connection() as conn:
        row = conn.execute('SELECT layer, source, meta FROM chunks WHERE id = ?',
                           (cid,)).fetchone()
    assert row[0] == 'lore' and row[1] == 'vault/lighthouse.md'
    assert json.loads(row[2])['added_by'] == f'plugin:{PLUG}'
    # her searches see it
    text, ok = pt._search_memory('lighthouse', 'default', layer='lore')
    assert ok and 'logbook' in text


def test_bulk_save_one_ledger_row_and_backfill_rearm(palace):
    _register()
    items = [{'content': f'lore fact {i:02d}', 'source': f'n{i}'} for i in range(3)]
    items.append({'content': 'x' * 600})   # over-length → skipped
    ids, msg = layer_api.bulk_save(items, layer='lore')
    assert len(ids) == 3 and '1 skipped' in msg
    assert pt._backfill_done is False      # NULL vectors swept on next search
    with pt._get_connection() as conn:
        rows = conn.execute("SELECT actor, action, summary FROM ledger "
                            "WHERE scope = 'default'").fetchall()
        nulls = conn.execute("SELECT COUNT(*) FROM chunks WHERE layer = 'lore' "
                             "AND embedding IS NULL").fetchone()[0]
    assert len(rows) == 1                  # ONE summary line, not 3
    assert rows[0][0] == 'system' and rows[0][1] == 'imported'
    assert 'synced 3 items' in rows[0][2]
    assert nulls == 3


def test_delete_by_source_reconciliation(palace):
    _register()
    layer_api.bulk_save([{'content': 'v1 of note', 'source': 'n1'},
                         {'content': 'also from n1', 'source': 'n1'},
                         {'content': 'other note', 'source': 'n2'}], layer='lore')
    count, _ = layer_api.delete_by_source('lore', 'n1')
    assert count == 2
    with pt._get_connection() as conn:
        left = conn.execute("SELECT content FROM chunks WHERE layer = 'lore'").fetchall()
        deleted = conn.execute("SELECT COUNT(*) FROM ledger WHERE action = 'deleted' "
                               "AND actor = 'system'").fetchone()[0]
    assert [r[0] for r in left] == ['other note']
    assert deleted == 1


def test_recent_feed_excludes_mirrored_rows(palace):
    # Sapphire's live catch (2026-07-12): a vault sync must not flood her
    # lived-recency feed. Mirrored rows stay OUT of unfiltered recent, but
    # remain reachable via layer= and in blended search.
    _register()
    layer_api.bulk_save([{'content': f'mirrored note {i}'} for i in range(12)],
                        layer='lore')
    pt._save_memory('a real life event', 'default')
    text, ok = pt._get_recent_memories('default', count=10)
    assert ok and 'a real life event' in text and 'mirrored note' not in text
    text, ok = pt._get_recent_memories('default', count=10, layer='lore')
    assert ok and 'mirrored note' in text          # explicit ask still works
    text, ok = pt._search_memory('mirrored', 'default')
    assert ok and 'mirrored note' in text          # search stays blended


# ─── Dark layers: off means off ──────────────────────────────────────────────

def test_dark_layer_lifecycle(palace):
    _register()
    layer_api.save('The hidden vault fact.', layer='lore', source='n1')
    pt._save_memory('An ordinary event happened.', 'default')
    # registered: visible everywhere
    text, ok = pt._search_memory('vault fact', 'default')
    assert ok and 'hidden vault' in text
    # provider disabled → dark
    ml.unregister_plugin(PLUG)
    layer, err = pt._validate_layer('lore')
    assert err and 'lore' in err
    text, ok = pt._search_memory('vault fact', 'default')
    assert 'hidden vault' not in text
    text, ok = pt._get_recent_memories('default', count=20)
    assert 'hidden vault' not in text and 'ordinary event' in text
    out = browse.list_chunks(query={'scope': 'default'})
    assert all(c['layer'] != 'lore' for c in out['chunks'])
    vis_sql, vis_params = spider._visible_chunk_clause(pt, 'default', None)
    assert 'NOT IN' in vis_sql and 'lore' in vis_params
    # rows survived, and re-enable lights them back up
    _register()
    text, ok = pt._search_memory('vault fact', 'default')
    assert ok and 'hidden vault' in text


# ─── Librarian opt-in ────────────────────────────────────────────────────────

def test_librarian_optin_gate(palace):
    _register()   # librarian False
    layer_api.save('unreviewed lore', layer='lore')
    pt._save_memory('unreviewed event', 'default')
    with pt._get_connection() as conn:
        cur = conn.cursor()
        batch = librarian.build_batch(cur, 'default', 'all', 50)
        assert {b[1] for b in batch} == {'events'}
        _register(librarian=True)
        batch = librarian.build_batch(cur, 'default', 'all', 50)
        assert {b[1] for b in batch} == {'events', 'lore'}
        # Legacy what='self' selects the same pool as 'all' — the free
        # self layer retired 2026-07-26; nothing lives there to sort.
        batch = librarian.build_batch(cur, 'default', 'self', 50)
        assert {b[1] for b in batch} == {'events', 'lore'}


# ─── Scope delete + UI route ─────────────────────────────────────────────────

def test_delete_scope_kills_plugin_layer_rows(palace):
    _register()
    pt.create_scope('s-doom')
    layer_api.save('doomed lore', layer='lore', scope='s-doom')
    pt.delete_scope('s-doom')
    with pt._get_connection() as conn:
        left = conn.execute("SELECT COUNT(*) FROM chunks WHERE scope = 's-doom'").fetchone()[0]
    assert left == 0


def test_list_layers_route(palace):
    assert browse.list_layers(query={}) == {'layers': []}
    _register(writable=True, librarian=True)
    out = browse.list_layers(query={})
    assert len(out['layers']) == 1
    l = out['layers'][0]
    assert l['key'] == 'lore' and l['plugin'] == PLUG
    assert l['writable'] is True and l['librarian'] is True
