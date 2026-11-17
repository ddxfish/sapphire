"""Private-model memory (2026-11-17). A row records who wrote it (wr_model,
wr_provider, wr_private, origin_name); a reader whose provider is not ticked
private is not given wr_private rows, and is told how many it was not given.
A metadata filter, not a vault. Plan: tmp/private-memory-plan.md.

Package-path imports on purpose (never re-import a plugin module in a test).
"""
import json
import sqlite3
from pathlib import Path

import pytest

import core.chat.function_manager as fm
from core.chat.llm_providers import resolve
from plugins.mindpalace.tools import palace_tools as pt
from plugins.mindpalace.tools import metadata as md
from plugins.mindpalace.tools import ledger, goal_tools, librarian, librarian_tools, library
from plugins.mindpalace.tools import self_tools
from plugins.mindpalace.routes import transfer, browse, self_routes

_REAL_FILTER_ON = pt._private_filter_on      # the palace fixture replaces it

LOCAL, CLOUD = 'qwen-box', 'claude'
PLUGIN = Path(__file__).absolute().parent.parent


class _FakeEmbedder:
    provider_id = "fake"
    available = False

    def embed(self, texts, prefix="search_document"):
        return None


@pytest.fixture(autouse=True)
def _clean_tool_context():
    fm.tool_context.set(None)
    yield
    fm.tool_context.set(None)


@pytest.fixture
def palace(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "_db_path", tmp_path / "mind.db", raising=False)
    monkeypatch.setattr(pt, "_db_initialized", False, raising=False)
    monkeypatch.setattr(pt, "_backfill_done", True, raising=False)
    monkeypatch.setattr(pt, "_name_seen", None, raising=False)
    monkeypatch.setattr(pt, "_get_embedder", lambda: _FakeEmbedder(), raising=False)
    monkeypatch.setattr(pt, "_instance_name", lambda: 'Sapphire-Purple')
    monkeypatch.setattr(pt, "_private_filter_on", lambda: True)
    # THE rule lives in core; one fake provider is ticked private.
    monkeypatch.setattr(resolve, "is_local", lambda key, conf=None: key == LOCAL)
    return pt


def turn(provider, model='m'):
    """A model turn on this provider (None = no turn: the app, MCP, a thread)."""
    fm.tool_context.set({'provider': provider, 'model': model, 'chat': 'c'} if provider else None)


def _row(cid):
    with pt._get_connection() as conn:
        return conn.execute('SELECT wr_model, wr_provider, wr_private, origin_name, meta '
                            'FROM chunks WHERE id = ?', (cid,)).fetchone()


def _save(text, **kw):
    msg, ok = pt._save_memory(text, 's', **kw)
    assert ok, msg
    return int(msg.split('ID: ')[1].split(',')[0])


# ─── The stamp ────────────────────────────────────────────────────────────────

def test_save_records_the_writer(palace):
    turn(LOCAL, 'qwen3')
    a = _save('the doctor moved the appointment')
    turn(CLOUD, 'opus')
    b = _save('the weather was grey')
    turn(None)
    c = _save('typed in the app')
    assert _row(a)[:4] == ('qwen3', LOCAL, 1, 'Sapphire-Purple')
    assert _row(b)[:4] == ('opus', CLOUD, 0, 'Sapphire-Purple')
    assert _row(c)[:4] == (None, None, 0, 'Sapphire-Purple')


def test_every_chunk_insert_is_stamped():
    """The choke point the palace does not have: an INSERT INTO chunks with no
    writer stamp after it leaves a row with no origin and a public default."""
    bare = []
    for f in list((PLUGIN / 'tools').glob('*.py')) + list((PLUGIN / 'routes').glob('*.py')):
        src = f.read_text(encoding='utf-8')
        at = src.find('INSERT INTO chunks (')
        while at != -1:
            if '_stamp_writer(' not in src[at:at + 1500]:
                bare.append(f"{f.name}@{src.count(chr(10), 0, at) + 1}")
            at = src.find('INSERT INTO chunks (', at + 1)
    assert not bare, f"INSERT INTO chunks without _stamp_writer: {bare}"


def test_migration_carries_a_recorded_model_and_marks_old_rows_public(palace):
    pt._ensure_db()
    conn = sqlite3.connect(pt._get_db_path())
    conn.execute('DROP INDEX idx_chunks_wr_private')
    for col in ('wr_model', 'wr_provider', 'wr_private', 'origin_name'):
        conn.execute(f'ALTER TABLE chunks DROP COLUMN {col}')
    conn.execute('DELETE FROM palace_kv')
    for content, meta in (('had a model', {'model': 'glm-5', 'provider': 'fw'}),
                          ('had none', {}), ('bad meta', None)):
        conn.execute("INSERT INTO chunks (layer, scope, content, meta, created, updated) "
                     "VALUES ('events', 's', ?, ?, 't', 't')",
                     (content, json.dumps(meta) if meta is not None else '{not json'))
    conn.commit()
    conn.close()
    pt._db_initialized = False
    assert pt._ensure_db()
    conn = sqlite3.connect(pt._get_db_path())
    got = {r[0]: r[1:] for r in conn.execute(
        'SELECT content, wr_model, wr_provider, wr_private, origin_name FROM chunks')}
    conn.close()
    assert got['had a model'] == ('glm-5', 'fw', 0, 'Sapphire-Purple')
    assert got['had none'] == (None, None, 0, 'Sapphire-Purple')
    assert got['bad meta'] == (None, None, 0, 'Sapphire-Purple')   # one bad row aborts nothing


def test_her_rows_follow_a_rename_and_another_sapphires_do_not(palace, monkeypatch):
    a = _save('written as purple')
    with pt._get_connection() as conn:
        conn.execute("UPDATE chunks SET origin_name = 'Sapphire-Blue' WHERE id = ?", (a,))
        conn.commit()
    b = _save('also purple')
    monkeypatch.setattr(pt, "_instance_name", lambda: 'Sapphire-Violet')
    c = _save('after the rename')
    assert [_row(i)[3] for i in (a, b, c)] == ['Sapphire-Blue', 'Sapphire-Violet', 'Sapphire-Violet']


# ─── The read gate ────────────────────────────────────────────────────────────

def test_cloud_reader_is_not_given_private_rows_and_is_told_how_many(palace):
    turn(LOCAL, 'qwen3')
    _save('the quokka needs a dentist')
    turn(CLOUD)
    _save('the quokka likes breakfast')
    text, ok = pt._search_memory('quokka', 's')
    assert ok and 'breakfast' in text and 'dentist' not in text
    assert '1 more matching memory is only available on private models' in text
    assert 'written by qwen3 on Sapphire-Purple' in text
    turn(LOCAL)
    text, _ = pt._search_memory('quokka', 's')
    assert 'dentist' in text and 'breakfast' in text and 'private models' not in text


def test_only_private_matches_says_so_without_content(palace):
    turn(LOCAL, 'qwen3')
    _save('the wombat owes rent')
    turn(CLOUD)
    text, ok = pt._search_memory('wombat', 's')
    assert ok and 'rent' not in text and 'No memories found' in text
    assert '1 more matching memory is only available on private models' in text


def test_a_stranger_reads_as_cloud_and_the_person_reads_everything(palace):
    turn(LOCAL)
    _save('the ibis sleeps late')
    turn(None)                                   # MCP client, bare thread
    assert 'sleeps' not in pt._search_memory('ibis', 's')[0]
    assert 'sleeps' not in pt._get_recent_memories('s')[0]
    with pt.as_user():                           # the app's console
        assert 'sleeps' in pt._search_memory('ibis', 's')[0]
        assert 'sleeps' in pt._get_recent_memories('s')[0]


def test_recents_names_what_was_left_out(palace):
    turn(LOCAL, 'qwen3')
    _save('a private morning')
    turn(CLOUD)
    _save('a public evening')
    text, _ = pt._get_recent_memories('s')
    assert 'public evening' in text and 'private morning' not in text
    assert '1 more matching memory is only available on private models' in text


def test_filter_off_shows_everything_and_still_stamps(palace, monkeypatch):
    monkeypatch.setattr(pt, "_private_filter_on", lambda: False)
    turn(LOCAL)
    a = _save('the emu keeps a diary')
    turn(CLOUD)
    assert 'diary' in pt._search_memory('emu', 's')[0]
    assert _row(a)[2] == 1


def test_cloud_cannot_edit_or_delete_a_private_row(palace):
    turn(LOCAL)
    a = _save('the heron hides money')
    turn(CLOUD)
    msg, ok = pt._update_memory(a, 's', content='rewritten')
    assert not ok and 'private models' in msg
    msg, ok = pt._delete_memory(a, 's')
    assert not ok and 'private models' in msg
    with pt._get_connection() as conn:
        assert conn.execute('SELECT content FROM chunks WHERE id = ?', (a,)).fetchone()[0] \
            == 'the heron hides money'


# ─── Edits ────────────────────────────────────────────────────────────────────

def test_a_private_models_edit_makes_the_row_private_and_names_the_rewriter(palace):
    turn(CLOUD, 'opus')
    a = _save('the gull flew north')
    turn(LOCAL, 'qwen3')
    msg, ok = pt._update_memory(a, 's', content='the gull flew north to see the doctor')
    assert ok, msg
    model, provider, private, _origin, meta = _row(a)
    assert (model, provider, private) == ('qwen3', LOCAL, 1)   # whose words they are now
    meta = json.loads(meta)                                    # where it began stays in meta
    assert meta['model'] == 'opus' and meta['edited_by'] == 'qwen3'


def test_a_cloud_edit_leaves_a_public_row_public(palace):
    turn(CLOUD, 'opus')
    a = _save('the tern is small')
    turn(CLOUD, 'gpt')
    pt._update_memory(a, 's', content='the tern is very small')
    assert _row(a)[:3] == ('opus', CLOUD, 0)


def test_merge_derived_never_takes_provenance_from_the_restamp():
    old = {'model': 'opus', 'chat': 'a', 'persona': 'p', 'provider': 'claude', 'added_by': 'ai'}
    fresh = {'md_v': 3, 'model': 'qwen3', 'chat': 'b', 'provider': 'qwen-box', 'session_id': 'x'}
    out = md.merge_derived(old, fresh)
    assert (out['model'], out['chat'], out['provider']) == ('opus', 'a', 'claude')
    assert out['edited_by'] == 'qwen3' and out['md_v'] == 3 and 'session_id' not in out


# ─── The other read surfaces ──────────────────────────────────────────────────

def test_private_entity_and_its_card_stay_out_of_a_cloud_turn(palace):
    turn(LOCAL)
    _save('Dr Lee is the cardiologist', layer='entities', entity='Dr Lee')
    turn(CLOUD)
    _save('Pat runs the bakery', layer='entities', entity='Pat')
    text, _ = pt._list_entities('s')
    assert 'Pat' in text and 'Dr Lee' not in text and '1 more is only available' in text
    assert pt._query_card('dr lee', 's') is None
    with pt._get_connection() as conn:
        assert [n for _i, n, _a in md.entity_aliases(conn.cursor(), 's')] == ['Pat']
    turn(None)                                   # the app shares the matcher: every name
    with pt._get_connection() as conn:
        assert {n for _i, n, _a in md.entity_aliases(conn.cursor(), 's')} == {'Pat', 'Dr Lee'}
    turn(LOCAL)
    assert 'Dr Lee' in pt._list_entities('s')[0]


def test_goals_gate_in_a_cloud_turn_only(palace):
    turn(LOCAL)
    msg, ok = goal_tools._create('s', 'find a new cardiologist')
    assert ok, msg
    turn(CLOUD)
    assert 'cardiologist' not in goal_tools._list('s')[0]
    turn(None)                                   # the Goals page
    with pt._get_connection() as conn:
        assert [g['title'] for g in goal_tools._top_level(conn.cursor(), 's')] \
            == ['find a new cardiologist']
    turn(LOCAL)
    assert 'cardiologist' in goal_tools._list('s')[0]


def test_ledger_rows_about_private_memories_are_not_read_by_cloud(palace):
    turn(LOCAL)
    a = _save('the stork has a secret')
    turn(None)
    with pt.as_user():                           # the person, in the app
        msg, ok = pt._delete_memory(a, 's')
    assert ok, msg
    with pt._get_connection() as conn:
        marks = conn.execute("SELECT action, wr_private FROM ledger WHERE target = ?",
                             (str(a),)).fetchall()
    assert len(marks) == 2 and all(m[1] == 1 for m in marks), marks   # saved + deleted
    turn(CLOUD)
    with pt._get_connection() as conn:
        text, _n = ledger.read_block(conn.cursor(), 's')
    assert 'stork' not in text
    turn(LOCAL)
    with pt._get_connection() as conn:
        text, _n = ledger.read_block(conn.cursor(), 's')
    assert 'stork' in text


def test_librarian_batch_waits_for_a_private_librarian(palace, monkeypatch):
    monkeypatch.setattr(librarian, '_optin_layers', lambda: ())
    turn(LOCAL)
    _save('the crane is private')
    turn(CLOUD)
    _save('the crane is public')
    turn(None)
    with pt._get_connection() as conn:
        cur = conn.cursor()
        monkeypatch.setattr(librarian, '_resident_private', lambda scope: False)
        assert [r[2] for r in librarian.build_batch(cur, 's', 'all', 10)] == ['the crane is public']
        monkeypatch.setattr(librarian, '_resident_private', lambda scope: True)
        assert len(librarian.build_batch(cur, 's', 'all', 10)) == 2


def test_a_derived_row_is_private_exactly_when_a_source_was(palace):
    turn(LOCAL)
    priv = _save('private source words')
    turn(CLOUD)
    pub = _save('public source words')
    turn(LOCAL)                                  # a private librarian tends both
    cols = ('id, layer, scope, content, entity_id, tier, label, favorite, importance, '
            'private_key, meta, created')
    with pt._get_connection() as conn:
        cur = conn.cursor()
        new = {}
        for name, sid in (('priv', priv), ('pub', pub)):
            src = cur.execute(f'SELECT {cols} FROM chunks WHERE id = ?', (sid,)).fetchone()
            new[name] = librarian_tools._insert_derived(cur, pt, md, src, 'tidied ' + name,
                                                        'events', 's')
        new['mixed'] = librarian_tools._insert_derived(
            cur, pt, md, cur.execute(f'SELECT {cols} FROM chunks WHERE id = ?', (pub,)).fetchone(),
            'merged', 'events', 's', sources=[pub, priv])
        conn.commit()
    assert [_row(new[k])[2] for k in ('priv', 'pub', 'mixed')] == [1, 0, 1]


def test_the_writer_travels_through_an_export_file(palace):
    turn(LOCAL, 'qwen3')
    _save('a private row leaves home')
    turn(None)
    data = transfer.export_data(query={'scope': 's', 'layer': 'events'})
    assert data['chunks'][0]['writer'] == {'model': 'qwen3', 'provider': LOCAL,
                                           'origin': 'Sapphire-Purple', 'private': True}
    data['chunks'][0]['writer']['origin'] = 'Sapphire-Blue'
    out = transfer.import_data(body={'scope': 'dst', 'data': data, 'expect_layer': 'events'})
    assert isinstance(out, dict) and not out.get('error'), out
    with pt._get_connection() as conn:
        got = conn.execute("SELECT wr_model, wr_provider, wr_private, origin_name FROM chunks "
                           "WHERE scope = 'dst'").fetchone()
    assert got == ('qwen3', LOCAL, 1, 'Sapphire-Blue')
    turn(CLOUD)
    assert 'leaves home' not in pt._search_memory('private row', 'dst')[0]


def test_a_private_models_note_is_a_private_document(palace):
    turn(LOCAL, 'qwen3')
    msg, ok = pt._save_memory('the lab results came back fine', 's', layer='knowledge',
                              label='lab results')
    assert ok, msg
    doc_id = int(msg.split('[doc ')[1].split(']')[0])
    turn(CLOUD)
    assert 'lab results' not in library.catalog_text('s')[0]
    text, ok = library.read_document_text('s', doc_id)
    assert not ok and 'private models' in text
    turn(LOCAL)
    assert 'lab results' in library.catalog_text('s')[0]
    assert library.read_document_text('s', doc_id)[1]


# ─── The bug hunt of 2026-11-17: the side doors ──────────────────────────────

def _ent(name, scope='s'):
    with pt._get_connection() as conn:
        return conn.execute('SELECT id, wr_model, wr_provider, wr_private FROM entities '
                            'WHERE name = ? AND scope = ?', (name, scope)).fetchone()


def _cloud_ledger():
    turn(CLOUD)
    with pt._get_connection() as conn:
        return ledger.read_block(conn.cursor(), 's')[0]


def test_the_switch_needs_a_real_answer(palace):
    """The one release door: a request that says nothing must not open it."""
    turn(LOCAL)
    a = _save('the heron keeps a diary')
    turn(None)
    for body in (None, {}, {'private': 'false'}, {'private': 0}):
        assert browse.set_private(cid=a, body=body)[1] == 400, body
        assert browse.set_entity_private(eid=1, body=body)[1] == 400, body
    assert _row(a)[2] == 1
    assert browse.set_private(cid=a, body={'private': False}) == {'success': True, 'private': False}
    assert _row(a)[2] == 0


def test_marking_a_row_marks_the_ledger_lines_that_quote_it(palace):
    turn(CLOUD)
    a = _save('the heron keeps a diary')
    assert 'heron' in _cloud_ledger()            # its save line, written public
    turn(None)
    assert browse.set_private(cid=a, body={'private': True})['success']
    assert 'heron' not in _cloud_ledger()


def test_merging_a_private_person_into_a_public_one_keeps_them_private(palace):
    turn(LOCAL)
    _save('Dr Lee is the cardiologist', layer='entities', entity='Dr Lee')
    turn(CLOUD)
    _save('Doctor Lee has an office downtown', layer='entities', entity='Doctor Lee')
    turn(None)
    out = browse.merge_entities(body={'keeper': _ent('Doctor Lee')[0], 'loser': _ent('Dr Lee')[0]})
    assert isinstance(out, dict) and not out.get('error'), out
    assert _ent('Doctor Lee')[3] == 1
    assert 'merged duplicate entity' not in _cloud_ledger()


def test_cloud_cannot_delete_a_goal_holding_private_notes(palace):
    turn(CLOUD)
    msg, ok = goal_tools._create('s', 'plan the garden')
    assert ok, msg
    with pt._get_connection() as conn:
        gid = conn.execute("SELECT id FROM chunks WHERE layer = 'goals'").fetchone()[0]
    turn(LOCAL)
    msg, ok = goal_tools._update('s', gid, progress_note='the doctor said to rest first')
    assert ok, msg
    turn(CLOUD)
    msg, ok = goal_tools._delete('s', gid)
    assert not ok and 'private models' in msg and 'doctor' not in msg
    turn(None)                                   # the Goals page
    assert goal_tools._delete('s', gid)[1]


def test_dashboard_names_no_private_person_and_the_self_page_shows_everything(palace):
    from datetime import datetime, timedelta
    soon = (datetime.now() + timedelta(days=2)).strftime('%Y-%m-%d')
    turn(LOCAL)
    _save('Dr Lee is the cardiologist', layer='entities', entity='Dr Lee')
    _save('checkup with Dr Lee', when=soon)

    def dash():
        with pt._get_connection() as conn:
            return json.dumps(self_tools.dashboard_data(conn.cursor(), 's'), default=str)
    assert 'Dr Lee' in dash()                    # the private model's own wake read
    turn(CLOUD)
    assert 'Dr Lee' not in dash()
    turn(None)                                   # no turn: a stranger...
    assert 'Dr Lee' not in dash()
    sheet = self_routes.get_sheet(query={'scope': 's'})      # ...but the Self page is the person
    assert 'checkup with Dr Lee' in json.dumps(sheet, default=str)


def test_librarian_name_lists_wait_for_a_private_librarian(palace, monkeypatch):
    turn(LOCAL)
    _save('Dr Lee is the cardiologist', layer='entities', entity='Dr Lee')
    turn(CLOUD)
    _save('Pat runs the bakery', layer='entities', entity='Pat')
    turn(None)
    with pt._get_connection() as conn:
        conn.execute('UPDATE entities SET mentions = 3')
        conn.commit()
        monkeypatch.setattr(librarian, '_resident_private', lambda scope: False)
        assert [r[0] for r in librarian._dirty_entities(conn.cursor(), 's')] == ['Pat']
        monkeypatch.setattr(librarian, '_resident_private', lambda scope: True)
        assert {r[0] for r in librarian._dirty_entities(conn.cursor(), 's')} == {'Pat', 'Dr Lee'}
    src = (PLUGIN / 'tools' / 'librarian.py').read_text(encoding='utf-8')
    assert src.count("SELECT name FROM entities WHERE scope IN (?, 'global')\"\n") == 2 \
        and src.count('f"{_wr_gate(scope)} ORDER BY name"') == 2, "link roster lost its gate"
    assert "{ent_gate}" in (PLUGIN / 'tools' / 'librarian_tools.py').read_text(encoding='utf-8')


def test_the_librarian_snapshot_is_not_shared_between_readers(palace, monkeypatch):
    seen = []
    monkeypatch.setattr(self_tools, '_read_self',
                        lambda scope, **kw: (seen.append(pt.reader_private()) or f'snap {len(seen)}', True))
    monkeypatch.setattr(librarian, '_snapshots', {})
    monkeypatch.setattr(librarian, '_resident_private', lambda scope: True)
    a = librarian._session_snapshot('librarian', 's')
    monkeypatch.setattr(librarian, '_resident_private', lambda scope: False)
    b = librarian._session_snapshot('librarian', 's')
    assert a != b and seen == [True, False]


def test_a_wrong_id_does_not_name_a_private_document(palace):
    turn(LOCAL, 'qwen3')
    msg, ok = pt._save_memory('the lab results came back fine', 's', layer='knowledge',
                              label='lab results')
    assert ok, msg
    doc_id = int(msg.split('[doc ')[1].split(']')[0])
    assert 'lab results' in pt._doc_id_hint(doc_id, 's')
    turn(CLOUD)
    assert pt._doc_id_hint(doc_id, 's') == ''


def test_picture_search_and_the_old_migration_keep_the_gate():
    """Inline SQL sites, pinned as source (a picture search needs a vision
    model to run): the query path of memory_view_image and the knowledge
    chunk migration read wr_private."""
    assert 'or lib._withheld(r[6])' in (PLUGIN / 'tools' / 'library_tools.py').read_text(encoding='utf-8')
    src = (PLUGIN / 'tools' / 'library.py').read_text(encoding='utf-8')
    i = src.index('def _migrate_v2_chunks')
    assert 'wr_private = 0' in src[i:i + 1200]


def test_imported_people_are_not_the_importing_models(palace):
    turn(LOCAL, 'qwen3')
    with pt._get_connection() as conn:
        pt.upsert_entity(conn.cursor(), 'Ann', 's', kind='person', imported=True)
        conn.commit()
    assert _ent('Ann')[1:] == (None, None, 0)


def test_a_private_document_stays_private_through_the_library_zip(palace, tmp_path):
    turn(LOCAL, 'qwen3')
    msg, ok = pt._save_memory('the lab results came back fine', 's', layer='knowledge',
                              label='lab results')
    assert ok, msg
    turn(None)
    report, err = library.export_scope_zip('s', tmp_path / 'lib.zip')
    assert not err, err
    report, err = library.import_scope_zip('dst', tmp_path / 'lib.zip')
    assert not err and report['imported'] == 1, (report, err)
    with library.get_connection() as conn:
        got = conn.execute("SELECT wr_model, wr_provider, wr_private, origin_name "
                           "FROM documents WHERE scope = 'dst'").fetchone()
    assert got == ('qwen3', LOCAL, 1, 'Sapphire-Purple')
    turn(CLOUD)
    assert 'lab results' not in library.catalog_text('dst')[0]


def test_a_files_writer_is_untrusted(palace):
    turn(CLOUD, 'opus')
    a = _save('the plover nests on shingle')
    with pt._get_connection() as conn:
        pt._apply_writer(conn.cursor(), a, {'model': ['x'], 'provider': {'k': 1},
                                            'origin': 7, 'private': True})
        conn.commit()
    assert _row(a)[:4] == (None, None, 1, 'Sapphire-Purple')


def test_private_fields_arriving_on_a_public_person_make_them_private(palace):
    turn(LOCAL)
    _save('Dr Lee is the cardiologist', layer='entities', entity='Dr Lee')
    turn(None)
    with pt._get_connection() as conn:
        conn.execute("UPDATE entities SET meta = ? WHERE name = 'Dr Lee'",
                     (json.dumps({'fields': {'phone': '555-0100'}}),))
        conn.commit()
    data = transfer.export_data(query={'scope': 's', 'layer': 'entities'})
    turn(CLOUD)
    msg, ok = pt._save_memory('Dr Lee was on the radio', 'dst', layer='entities', entity='Dr Lee')
    assert ok, msg
    assert _ent('Dr Lee', 'dst')[3] == 0
    turn(None)
    out = transfer.import_data(body={'scope': 'dst', 'data': data, 'expect_layer': 'entities'})
    assert isinstance(out, dict) and not out.get('error'), out
    assert _ent('Dr Lee', 'dst')[3] == 1


def test_only_an_explicit_off_turns_the_filter_off(palace, monkeypatch):
    from core.plugin_loader import plugin_loader
    for stored, on in ((None, True), (0, True), ('', True), (True, True), (False, False)):
        monkeypatch.setattr(plugin_loader, 'get_plugin_settings',
                            lambda name, v=stored: {'private_model_filter': v})
        assert _REAL_FILTER_ON() is on, stored


def test_the_notice_names_a_model_not_a_path(palace):
    note = pt._hidden_note([('/home/someone/models/qwen3-32b.gguf', 'Sapphire-Purple')])
    assert 'qwen3-32b.gguf' in note and 'someone' not in note


# ─── A private person: their description and facts are private with them ─────

def test_a_private_persons_facts_are_private_with_them(palace, monkeypatch):
    turn(CLOUD, 'opus')
    a = _save('Dr Lee is my cardiologist', layer='entities', entity='Dr Lee')
    b = _save('Pat runs the bakery', layer='entities', entity='Pat')
    assert 'is my cardiologist' in pt._search_memory('cardiologist', 's')[0]
    turn(None)                                   # the person marks Dr Lee, not the fact
    assert browse.set_entity_private(eid=_ent('Dr Lee')[0], body={'private': True})['success']
    assert _row(a)[2] == 0                       # the fact keeps its own mark...

    turn(CLOUD, 'opus')                          # ...and is not read by a cloud model
    text = pt._search_memory('cardiologist', 's')[0]
    assert 'Dr Lee' not in text and 'is my cardiologist' not in text   # the query word is echoed
    assert '1 more matching memory is only available on private models (marked private' in text
    assert 'opus' not in text.split('only available')[1]     # not "written by" a cloud model
    recents = pt._get_recent_memories('s', count=10)[0]
    assert 'Pat' in recents and 'Dr Lee' not in recents
    assert 'cardiologist' not in _cloud_ledger()             # its save line went with it
    turn(CLOUD)
    for verb, call in (('updated', lambda: pt._update_memory(a, 's', content='changed')),
                       ('deleted', lambda: pt._delete_memory(a, 's'))):
        msg, ok = call()
        assert not ok and 'private models' in msg and verb in msg, msg
    assert pt._update_memory(b, 's', content='Pat runs the bakery on Elm')[1]

    monkeypatch.setattr(librarian, '_optin_layers', lambda: ('entities',))
    monkeypatch.setattr(librarian, '_resident_private', lambda scope: False)
    with pt._get_connection() as conn:           # a cloud librarian is not handed them
        got = [r[2] for r in librarian.build_batch(conn.cursor(), 's', 'all', 10)]
    assert not any('cardiologist' in g for g in got)

    turn(LOCAL)                                  # a private model reads all of it
    assert 'is my cardiologist' in pt._search_memory('cardiologist', 's')[0]

    turn(None)                                   # released: each fact is back on its own mark
    assert browse.set_entity_private(eid=_ent('Dr Lee')[0], body={'private': False})['success']
    turn(CLOUD)
    assert 'is my cardiologist' in pt._search_memory('cardiologist', 's')[0]


# ─── Library documents and pictures: the same mark, the same switch ──────────

def test_the_house_switch_on_a_library_document(palace):
    from plugins.mindpalace.routes import library_routes
    turn(CLOUD, 'opus')
    msg, ok = pt._save_memory('the lab results came back fine', 's', layer='knowledge',
                              label='lab results')
    assert ok, msg
    doc_id = int(msg.split('[doc ')[1].split(']')[0])
    assert 'lab results' in library.catalog_text('s')[0]
    turn(None)
    for body in (None, {}, {'private': 'true'}):
        assert library_routes.set_document_private(did=doc_id, body=body)[1] == 400
    assert library_routes.set_document_private(did=99999, body={'private': True})[1] == 404
    assert library_routes.set_document_private(did=doc_id, body={'private': True}) \
        == {'success': True, 'private': True}
    assert 'lab results' in json.dumps(library.catalog_data('s'))        # the app shows every document
    assert '"private": true' in json.dumps(library.catalog_data('s'))    # and says which are marked
    turn(CLOUD)
    assert 'lab results' not in library.catalog_text('s')[0]
    assert not library.read_document_text('s', doc_id)[1]
    turn(None)
    assert library_routes.set_document_private(did=doc_id, body={'private': False})['success']
    turn(CLOUD)
    assert 'lab results' in library.catalog_text('s')[0]


# ─── The instance id: `origin` beside `origin_name` ──────────────────────────

MINE, THEIRS = 'a' * 32, 'b' * 32


def _origins(table='chunks', scope=None):
    with pt._get_connection() as conn:
        return conn.execute(f'SELECT origin, origin_name FROM {table}'
                            + (' WHERE scope = ?' if scope else '') + ' ORDER BY id',
                            (scope,) if scope else ()).fetchall()


def _restart(monkeypatch):
    monkeypatch.setattr(pt, '_db_initialized', False, raising=False)
    monkeypatch.setattr(pt, '_name_seen', None, raising=False)
    assert pt._ensure_db()


def test_rows_carry_her_id_and_older_rows_take_it(palace, monkeypatch):
    turn(CLOUD)
    _save('written before she had an id')        # under pytest core mints nothing: no id yet
    assert _origins() == [(None, 'Sapphire-Purple')]
    monkeypatch.setattr(pt, '_instance_id', lambda: MINE)
    _restart(monkeypatch)                        # the next start claims her older rows
    _save('written with an id', layer='entities', entity='Pat')
    assert _origins() == [(MINE, 'Sapphire-Purple')] * 2
    assert _origins('entities') == [(MINE, 'Sapphire-Purple')]


def test_a_rename_follows_her_id_and_leaves_another_sapphires_rows(palace, monkeypatch):
    monkeypatch.setattr(pt, '_instance_id', lambda: MINE)
    turn(CLOUD)
    a = _save('hers')
    b = _save('arrived from Blue')
    with pt._get_connection() as conn:
        pt._apply_writer(conn.cursor(), b, {'origin': 'Sapphire-Blue', 'origin_id': THEIRS})
        conn.commit()
    monkeypatch.setattr(pt, '_instance_name', lambda: 'Sapphire-Violet')
    _restart(monkeypatch)
    assert _origins() == [(MINE, 'Sapphire-Violet'), (THEIRS, 'Sapphire-Blue')]


def test_a_memory_restored_from_another_sapphire_keeps_its_origin(palace, monkeypatch):
    """Blue's backup restored on Purple's machine: the database remembers
    Blue's id, this machine has Purple's. Nothing is claimed or renamed;
    what Purple writes from now on is Purple's."""
    monkeypatch.setattr(pt, '_instance_id', lambda: THEIRS)
    monkeypatch.setattr(pt, '_instance_name', lambda: 'Sapphire-Blue')
    turn(CLOUD)
    _save('Blue wrote this')
    with pt._get_connection() as conn:           # and one from before Blue had an id
        conn.execute("INSERT INTO chunks (layer, scope, content, created, updated, origin_name) "
                     "VALUES ('events', 's', 'old Blue row', '2026-01-01', '2026-01-01', 'Sapphire-Blue')")
        conn.commit()
    monkeypatch.setattr(pt, '_instance_id', lambda: MINE)
    monkeypatch.setattr(pt, '_instance_name', lambda: 'Sapphire-Purple')
    _restart(monkeypatch)
    assert _origins() == [(THEIRS, 'Sapphire-Blue'), (None, 'Sapphire-Blue')]
    _save('Purple wrote this')
    assert _origins()[-1] == (MINE, 'Sapphire-Purple')
    _restart(monkeypatch)                        # and a later start changes nothing
    assert _origins()[:2] == [(THEIRS, 'Sapphire-Blue'), (None, 'Sapphire-Blue')]


def test_the_id_travels_in_an_export_and_is_never_swapped_for_ours(palace, monkeypatch):
    monkeypatch.setattr(pt, '_instance_id', lambda: THEIRS)
    monkeypatch.setattr(pt, '_instance_name', lambda: 'Sapphire-Blue')
    turn(CLOUD, 'opus')
    _save('Blue wrote this')
    turn(None)
    data = transfer.export_data(query={'scope': 's', 'layer': 'events'})
    assert data['chunks'][0]['writer']['origin_id'] == THEIRS
    monkeypatch.setattr(pt, '_instance_id', lambda: MINE)
    monkeypatch.setattr(pt, '_instance_name', lambda: 'Sapphire-Purple')
    _restart(monkeypatch)
    out = transfer.import_data(body={'scope': 'dst', 'data': data, 'expect_layer': 'events'})
    assert isinstance(out, dict) and not out.get('error'), out
    assert _origins(scope='dst') == [(THEIRS, 'Sapphire-Blue')]
    del data['chunks'][0]['writer']['origin_id']         # a file from before ids
    data['chunks'][0]['content'] = 'Blue wrote this too'
    transfer.import_data(body={'scope': 'dst', 'data': data, 'expect_layer': 'events'})
    assert _origins(scope='dst')[-1] == (None, 'Sapphire-Blue')
    data['chunks'][0].pop('writer')                      # a file that names nobody: written here
    data['chunks'][0]['content'] = 'nobody is named'
    transfer.import_data(body={'scope': 'dst', 'data': data, 'expect_layer': 'events'})
    assert _origins(scope='dst')[-1] == (MINE, 'Sapphire-Purple')


def test_a_document_carries_her_id(palace, monkeypatch):
    monkeypatch.setattr(pt, '_instance_id', lambda: MINE)
    turn(CLOUD)
    msg, ok = pt._save_memory('a note', 's', layer='knowledge', label='note one')
    assert ok, msg
    assert _docs_origin() == [(MINE, 'Sapphire-Purple')]


def _docs_origin():
    with library.get_connection() as conn:
        return conn.execute('SELECT origin, origin_name FROM documents ORDER BY id').fetchall()
