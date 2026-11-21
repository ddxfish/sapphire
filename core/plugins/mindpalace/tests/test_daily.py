"""The Daily layer (tools/daily.py): a day's summary from /compact-daily kept
in the chat's memory scope. Package-path imports on purpose (never re-import a
plugin module in a test)."""
import json
import sqlite3
from datetime import date
from types import SimpleNamespace

import pytest

import core.chat.function_manager as fm
from core.chat.llm_providers import resolve
from plugins.mindpalace.tools import palace_tools as pt
from plugins.mindpalace.tools import daily, ledger, librarian, metadata as md

LOCAL, CLOUD = 'qwen-box', 'claude'
S = 'sapph'
TURN = {'provider': CLOUD, 'model': 'opus', 'chat': 'c'}


class _NoEmbedder:
    provider_id = 'fake'
    available = False

    def embed(self, texts, prefix='search_document'):
        return None


@pytest.fixture(autouse=True)
def palace(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, '_db_path', tmp_path / 'mind.db', raising=False)
    monkeypatch.setattr(pt, '_db_initialized', False, raising=False)
    monkeypatch.setattr(pt, '_backfill_done', True, raising=False)
    monkeypatch.setattr(pt, '_name_seen', None, raising=False)
    monkeypatch.setattr(pt, '_get_embedder', lambda: _NoEmbedder(), raising=False)
    monkeypatch.setattr(pt, '_backfill_embeddings', lambda background=False: None)
    monkeypatch.setattr(pt, '_instance_name', lambda: 'Sapphire-Purple')
    monkeypatch.setattr(pt, '_private_filter_on', lambda: True)
    monkeypatch.setattr(resolve, 'is_local', lambda key, conf=None: key == LOCAL)
    # The resolver names the model a chat really uses (its own, else the provider's default).
    resolved = []
    monkeypatch.setattr(resolve, 'resolve', lambda key, model='', **k: resolved.append(key) or SimpleNamespace(
        key=key, effective_model=model or f'{key}-default'))
    monkeypatch.setitem(fm.SCOPE_REGISTRY, 'memory',
                        {'var': None, 'default': 'default', 'setting': 'memory_scope'})
    monkeypatch.setattr(daily, 'enabled', lambda: True)
    monkeypatch.setattr(daily, '_stopping', False)
    monkeypatch.setattr(daily, '_today', lambda: date(2026, 11, 20))       # a Friday
    fm.tool_context.set(None)
    assert pt._ensure_db()
    yield SimpleNamespace(resolved=resolved)
    fm.tool_context.set(None)


def _pair(day, text, at=None, messages=12, persona='sapphire'):
    stamp = {'timestamp': f'{day}T22:00:00', 'compressed': True, 'compressed_day': day,
             'compressed_at': at or f'{day}T23:30:00.000001'}
    return [{'role': 'user', 'content': f'[Day summary: {day} ({messages} messages)]', **stamp},
            {'role': 'assistant', 'content': text, 'persona': persona, **stamp}]


class SM:
    """A session manager: one chat's settings and rows."""

    def __init__(self, rows, **settings):
        self.rows = rows
        self.settings = {'memory_scope': S, 'llm_primary': CLOUD, 'llm_model': 'opus', **settings}
        self.exports = 0

    def read_chat_settings(self, chat):
        return self.settings

    def export_chat(self, chat):
        self.exports += 1
        return {'messages': [dict(m) for m in self.rows]}

    def list_chat_files(self):
        return [{'name': 'main'}]


def _rows(where='1=1', params=()):
    with pt._get_connection() as conn:
        return conn.execute(
            'SELECT id, content, source, chunk_index, label, created, importance, meta, wr_private, '
            f"wr_model, wr_provider, scope FROM chunks WHERE layer = 'daily' AND {where} "
            'ORDER BY source, chunk_index', params).fetchall()


def _text(rows):
    return daily.join([(r[1], daily._sep(json.loads(r[7]), r[3])) for r in rows])


def _person(name='Ada', scope=S):
    with pt._get_connection() as conn:
        eid = pt.upsert_entity(conn.cursor(), name, scope)
        conn.commit()
    return eid


def _ask(name, **args):
    """Her tool call: a model turn on the scope S."""
    real, tok = pt._get_current_scope, fm.tool_context.set(args.pop('_turn', TURN))
    pt._get_current_scope = lambda: S
    try:
        return pt.execute(name, args, None)
    finally:
        pt._get_current_scope = real
        fm.tool_context.reset(tok)


LONG = ("We spent the morning on the pendant wake bug and found the timer was armed after sleep. "
        "Ada flashed the board twice before it held.\n\n"
        "In the afternoon we walked the email plugin with Bo. " + ("The reply rules were checked one by one. " * 14).strip()
        + "\n\nBy evening Ada said tomorrow we start the daily layer.")


# ─── Pieces ──────────────────────────────────────────────────────────────────

def test_split_keeps_the_cap_and_join_gives_the_text_back():
    pieces = daily.split(LONG, 512)
    assert len(pieces) > 3 and all(len(p) <= 512 for p, _ in pieces)
    assert daily.join(pieces) == LONG
    assert pieces[0][1] == '' and [sep for _, sep in pieces].count('\n\n') == 2


@pytest.mark.parametrize('text', [
    "Tabs\tinside a line.  Two spaces after a stop.   Three here.",
    "A list:\r\n  - indented item\r\n    - nested deeper\r\n\r\n\r\nAfter three blank lines.",
    "First.\n\n\n\nFour newlines above.\n\tTab-indented line.",
    "x" * 1300,                                         # no space anywhere: cut mid-word, nothing added
    "Before " + "y" * 700 + " after the long token. " + "z" * 600,
    "One sentence. " * 120,
])
def test_the_pieces_give_any_text_back_to_the_character(text):
    """mind.db holds the only copy of a day once it rolls off the chat: the
    stored pieces must be the summary, not a re-spaced version of it."""
    pieces = daily.split(text, 512)
    assert daily.join(pieces) == text.strip()
    assert all(0 < len(p) <= 512 for p, _ in pieces)
    assert all('\n' not in p for p, _ in pieces)        # a piece never crosses a line


def test_stored_pieces_give_the_day_back_and_old_rows_still_read():
    odd = "Plan:\n  - one\n  - two\n\n\nDone.  Really done."
    daily.record_chat('main', SM(_pair('2026-11-02', odd)))
    assert _text(_rows()) == odd
    assert daily._sep({'br': 2}, 3) == '\n\n' and daily._sep({}, 3) == ' ' and daily._sep({'sep': '\t'}, 0) == ''


def test_day_pairs_are_the_stamped_pairs_only():
    rows = (_pair('2026-11-01', 'first day', messages=1)
            + [{'role': 'user', 'content': 'hi', 'timestamp': '2026-11-02T10:00:00'},
               {'role': 'assistant', 'content': 'hello'}]
            + [{'role': 'user', 'content': '[Compressed history: ...]', 'compressed': True},
               {'role': 'assistant', 'content': 'a whole summary', 'compressed': True}]
            + _pair('2026-11-02', 'second day'))
    got = daily.day_pairs(rows)
    assert [(p['day'], p['text'], p['messages'], p['persona']) for p in got] == \
        [('2026-11-01', 'first day', 1, 'sapphire'), ('2026-11-02', 'second day', 12, 'sapphire')]
    assert daily.source_of(got[0]) == 'day:2026-11-01:2026-11-01T23:30:00.000001'
    assert daily.day_pairs(None) == [] and daily.day_pairs(_pair('not-a-day', 'x')) == []


# ─── Recording ───────────────────────────────────────────────────────────────

def test_a_day_is_recorded_as_ordered_pieces_dated_on_its_day():
    out = daily.record_chat('main', SM(_pair('2026-11-02', LONG)))
    assert out == {'days': ['2026-11-02'], 'scope': S, 'skipped': ''}
    rows = _rows()
    assert _text(rows) == LONG
    assert [r[3] for r in rows] == list(range(len(rows)))
    assert {r[2] for r in rows} == {'day:2026-11-02:2026-11-02T23:30:00.000001'}
    assert {r[4] for r in rows} == {'2026-11-02'} and {r[11] for r in rows} == {S}
    assert {r[5][:10] for r in rows} == {'2026-11-02'}                  # created = the day, not tonight
    assert {r[6] for r in rows} == {None}                               # unrated: the nightly decay leaves it
    m = json.loads(rows[0][7])
    assert (m['day'], m['chat'], m['persona'], m['messages'], m['part'], m['parts'], m['added_by']) == \
        ('2026-11-02', 'main', 'sapphire', 12, 0, len(rows), 'ai')
    assert m['md_v'] == md.MD_VERSION and 'when' not in m and 'noun_candidates' not in m
    assert (rows[0][9], rows[0][10], rows[0][8]) == ('opus', CLOUD, 0)


def test_tomorrow_in_a_summary_counts_from_its_own_day():
    daily.record_chat('main', SM(_pair('2026-11-02', 'Ada said tomorrow we start the daily layer.')))
    assert json.loads(_rows()[0][7]).get('event_dates') == ['2026-11-03']


def test_one_ledger_line_for_the_run_and_none_for_a_run_that_adds_nothing():
    sm = SM(_pair('2026-11-01', 'day one') + _pair('2026-11-02', LONG))
    daily.record_chat('main', sm)
    assert daily.record_chat('main', sm)['days'] == []
    with pt._get_connection() as conn:
        lines = conn.execute("SELECT actor, action, summary FROM ledger WHERE layer = 'daily'").fetchall()
    assert lines == [('system', 'recorded', '2 days kept in daily memory: 2026-11-01 to 2026-11-02')]
    assert len({r[2] for r in _rows()}) == 2


def test_a_night_does_not_take_a_line_of_her_wake_ledger():
    """One 'recorded' line a night filled the 4-line tail she wakes to and
    pushed the person's own edits off it. The full ledger still shows it."""
    daily.record_chat('main', SM(_pair('2026-11-01', 'day one')))
    pt._ledger(S, 'user', 'edited', summary='the person changed a memory')
    with pt._get_connection() as conn:
        cur = conn.cursor()
        tail, full = ledger.tail_block(cur, S, depth=1), ledger.read_block(cur, S)
    assert 'the person changed a memory' in tail and 'kept in daily memory' not in tail
    assert 'kept in daily memory' in str(full)


def test_a_day_on_a_private_model_is_a_private_model_row():
    daily.record_chat('main', SM(_pair('2026-11-02', 'told Qwen about the house'), llm_primary=LOCAL,
                                 llm_model='qwen3'))
    row = _rows()[0]
    assert (row[8], row[9], row[10]) == (1, 'qwen3', LOCAL)
    fm.tool_context.set({'provider': CLOUD, 'model': 'opus', 'chat': 'c'})      # a cloud reader
    assert 'No days' in daily.recent_text(S) and 'No days' in daily.search_text('house', S)
    fm.tool_context.set({'provider': LOCAL, 'model': 'qwen3', 'chat': 'c'})
    assert 'told Qwen about the house' in daily.search_text('house', S)


def test_a_cloud_models_day_does_not_link_to_a_private_model_person():
    eid = _person('Ada')
    with pt._get_connection() as conn:
        conn.execute('UPDATE entities SET wr_private = 1 WHERE id = ?', (eid,))
        conn.commit()
    daily.record_chat('main', SM(_pair('2026-11-02', 'Ada came by.')))                      # cloud writer
    daily.record_chat('den', SM(_pair('2026-11-03', 'Ada came by again.'), llm_primary=LOCAL))
    with pt._get_connection() as conn:
        linked = conn.execute("SELECT c.label FROM edges g JOIN chunks c ON c.id = g.src_id "
                              "WHERE g.dst_id = ? AND g.kind = 'retells'", (eid,)).fetchall()
    assert linked == [('2026-11-03',)]


@pytest.mark.parametrize('settings,why', [
    ({'private_chat': True}, 'private chat'),
    ({'private_chat': 'true'}, 'private chat'),
    ({'memory_scope': 'none'}, 'memory scope none'),
    ({'memory_scope': 'global'}, 'memory scope none'),
    ({'mode': 'room'}, 'a room chat'),
])
def test_chats_that_write_nothing_are_never_read(settings, why, palace):
    sm = SM(_pair('2026-11-02', 'secret'), **settings)
    assert daily.record_chat('main', sm) == {'days': [], 'scope': None, 'skipped': why}
    assert sm.exports == 0 and _rows() == [] and palace.resolved == []


def test_unreadable_settings_and_a_missing_scope_registry_fail_closed(monkeypatch):
    sm = SM(_pair('2026-11-02', 'x'))
    sm.read_chat_settings = lambda chat: None                   # sealed or gone
    assert daily.record_chat('main', sm)['scope'] is None

    def boom(chat):
        raise OSError('db locked')
    sm.read_chat_settings = boom
    assert daily.record_chat('main', sm)['scope'] is None and sm.exports == 0
    monkeypatch.delitem(fm.SCOPE_REGISTRY, 'memory')
    assert daily.record_chat('main', SM(_pair('2026-11-02', 'x')))['skipped'] == 'no memory scope registered'
    assert _rows() == []


def test_a_model_is_resolved_only_for_a_chat_with_new_days(palace):
    """The start-up pass walks every chat: a provider built (and logged by
    name) for each one, to learn it had nothing new, was the wrong order."""
    daily.record_chat('empty', SM([{'role': 'user', 'content': 'hi'}, {'role': 'assistant', 'content': 'yo'}]))
    assert palace.resolved == []
    sm = SM(_pair('2026-11-02', 'x'))
    daily.record_chat('main', sm)
    assert palace.resolved == [CLOUD]
    daily.record_chat('main', sm)                               # nothing new: not resolved again
    assert palace.resolved == [CLOUD]


def test_a_chat_whose_model_cannot_be_said_records_nothing(monkeypatch):
    out = daily.record_chat('main', SM(_pair('2026-11-02', 'x'), llm_primary='none'))
    assert out == {'days': [], 'scope': None, 'skipped': "the chat's model could not be resolved"}

    def refused(*a, **k):
        raise resolve.ProviderRefused('nothing answers')
    monkeypatch.setattr(resolve, 'resolve', refused)
    out = daily.record_chat('other', SM(_pair('2026-11-03', 'y'), llm_primary='auto'))
    assert out['scope'] is None and 'could not be resolved' in out['skipped'] and _rows() == []


def test_an_empty_scope_setting_is_the_default_scope_and_auto_is_resolved(monkeypatch):
    monkeypatch.setattr(resolve, 'resolve', lambda *a, **k: SimpleNamespace(key=LOCAL, effective_model='qwen3'))
    daily.record_chat('main', SM(_pair('2026-11-02', 'x'), memory_scope='', llm_primary='auto', llm_model=''))
    row = _rows()[0]
    assert (row[11], row[10], row[9], row[8]) == ('default', LOCAL, 'qwen3', 1)


def test_a_chat_on_its_providers_default_model_is_stamped_with_that_models_name():
    daily.record_chat('main', SM(_pair('2026-11-02', 'x'), llm_model=''))
    assert (_rows()[0][9], _rows()[0][10]) == (f'{CLOUD}-default', CLOUD)


def test_switched_off_records_nothing(monkeypatch):
    monkeypatch.setattr(daily, 'enabled', lambda: False)
    sm = SM(_pair('2026-11-02', 'x'))
    assert daily.record_chat('main', sm)['skipped'] == 'switched off' and sm.exports == 0


def test_two_chats_one_scope_same_date_are_two_days_and_a_copied_pair_is_one():
    a = SM(_pair('2026-11-02', 'from the main chat', at='2026-11-02T23:30:00.1'))
    b = SM(_pair('2026-11-02', 'from the desk chat', at='2026-11-02T23:45:00.2'))
    daily.record_chat('main', a)
    daily.record_chat('desk', b)
    daily.record_chat('main-copy', a)                       # the same pair in another chat
    assert sorted(r[1] for r in _rows()) == ['from the desk chat', 'from the main chat']
    assert {json.loads(r[7])['chat'] for r in _rows()} == {'main', 'desk'}


def test_a_chat_that_changes_scope_does_not_copy_its_days_into_the_new_one():
    """Scout hunt 2026-11-20: the chat keeps its pairs for days; switched to
    another persona, the next compact recorded the old persona's days again
    in the new persona's memory."""
    sm = SM(_pair('2026-11-01', 'a day as Sapphire') + _pair('2026-11-02', 'another'))
    daily.record_chat('main', sm)
    sm.settings['memory_scope'] = 'lookout'
    sm.rows = sm.rows + _pair('2026-11-03', 'the first day after the switch')
    assert daily.record_chat('main', sm) == {'days': ['2026-11-03'], 'scope': 'lookout', 'skipped': ''}
    assert sorted((r[11], r[4]) for r in _rows()) == \
        [('lookout', '2026-11-03'), (S, '2026-11-01'), (S, '2026-11-02')]


def test_a_chat_made_private_while_its_days_are_written_leaves_nothing():
    """The check that let the chat be read is made again at the moment of
    writing (race + privacy scouts): vaulted in between = not committed."""
    sm = SM(_pair('2026-11-02', 'a day that became private'))
    real = sm.export_chat

    def export_then_vault(chat):
        out = real(chat)
        sm.settings['private_chat'] = True
        return out
    sm.export_chat = export_then_vault
    out = daily.record_chat('main', sm)
    assert out['days'] == [] and 'changed while' in out['skipped']
    with pt._get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM ledger WHERE layer = 'daily'").fetchone()[0] == 0


def test_a_palace_that_is_unloading_writes_nothing(monkeypatch):
    sm = SM(_pair('2026-11-02', 'x'))
    daily.stop()
    assert daily._stopping and daily.record_chat('main', sm)['skipped'] == 'switched off' and sm.exports == 0
    assert daily.sweep(['main'], sm) == {'days': 0, 'failed': 0}
    daily.start(boot=False)
    try:
        assert not daily._stopping and daily.record_chat('main', sm)['days'] == ['2026-11-02']
    finally:
        daily.stop()


def test_one_row_per_piece_of_a_day_is_the_databases_rule():
    daily.record_chat('main', SM(_pair('2026-11-02', 'a day')))
    row = _rows()[0]
    with pt._get_connection() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO chunks (layer, scope, content, created, updated, chunk_index, source) "
                     "VALUES ('daily', ?, 'a second copy', 't', 't', 0, ?)", (S, row[2]))
    with pt._get_connection() as conn:          # ordinary memories are not under it
        for _ in range(2):
            conn.execute("INSERT INTO chunks (layer, scope, content, created, updated, chunk_index, source) "
                         "VALUES ('events', ?, 'same', 't', 't', 0, 'note.md')", (S,))


def test_a_full_layer_keeps_the_days_that_fit_and_says_so_honestly(monkeypatch):
    told = []
    from core import notify
    monkeypatch.setattr(notify, 'notify', lambda title, **k: told.append((k.get('key'), k.get('body', ''))))
    monkeypatch.setattr(daily, 'ROW_CAP', 3)
    sm = SM(_pair('2026-11-01', 'one') + _pair('2026-11-02', 'two\n\nthree\n\nfour') + _pair('2026-11-03', 'five'))
    assert daily.record_chat('main', sm)['days'] == ['2026-11-01']          # stops at the day that does not fit
    assert [k for k, _ in told] == ['daily-full'] and 'NOT being recorded' in told[0][1]
    out = daily.record_chat('main', sm)
    assert out['days'] == [] and out['skipped'] == 'the daily layer of this scope is full'
    monkeypatch.setattr(daily, 'ROW_CAP', 10)
    told.clear()
    assert daily.record_chat('main', sm)['days'] == ['2026-11-02', '2026-11-03']   # room made: they come in
    assert told == [('daily-cap-80', told[0][1])] if told else True


# ─── People, and the spider ──────────────────────────────────────────────────

def test_one_edge_per_person_per_day_of_the_days_own_kind():
    eid = _person('Ada')
    daily.record_chat('main', SM(_pair('2026-11-02', LONG) + _pair('2026-11-03', 'Ada came back early.')))
    with pt._get_connection() as conn:
        edges = conn.execute("SELECT src_id, kind FROM edges WHERE dst_type = 'entity' AND dst_id = ?",
                             (eid,)).fetchall()
        mentions = conn.execute('SELECT mentions FROM entities WHERE id = ?', (eid,)).fetchone()[0]
    assert len(edges) == 2 and {k for _, k in edges} == {'retells'}         # LONG names Ada three times
    first = {r[2]: r[0] for r in reversed(_rows())}                         # each day's first piece
    assert {e[0] for e in edges} == set(first.values())
    assert mentions == 0                                                    # a day is not a memory of her


def test_days_never_price_a_persons_memories_out_of_a_walk_or_land_in_one():
    """Three scouts, 2026-11-20: day pieces sat in the spider's frontier (newest
    every night, so they won every tie) and their edges counted toward hub
    damping, so ~30 days of being named hid a person's real memories."""
    from plugins.mindpalace.tools import spider
    eid = _person('Ada')
    msg, ok = pt._save_memory('Ada keeps bees behind the workshop', S)
    assert ok, msg
    days = []
    for d in range(1, 29):
        days += _pair(f'2026-10-{d:02d}', f'Ada and I talked about the hives on day {d}.')
    daily.record_chat('main', SM(days))
    with pt._get_connection() as conn:
        cur = conn.cursor()
        assert cur.execute("SELECT COUNT(*) FROM edges WHERE dst_id = ? AND kind = 'retells'", (eid,)).fetchone()[0] == 28
        chunks, ents, _ = spider.traverse(pt, cur, set(), {eid}, 2.0, S, None)
        layers = {cur.execute('SELECT layer FROM chunks WHERE id = ?', (c,)).fetchone()[0] for c in chunks}
        assert layers == {'events'}                             # her memory is reached; no day piece is
        # ...and a walk that STARTS from a day reaches the person it names.
        seed = _rows("label = '2026-10-05'")[0][0]
        _, ents, _ = spider.traverse(pt, cur, {seed}, set(), 2.0, S, None)
        assert eid in ents
    text, ok = _ask('search_memory', query='Ada', depth=1)
    block = text.split('From your days')[0]
    assert ok and 'keeps bees' in block and '[daily]' not in block


def test_the_metadata_backfill_leaves_days_alone():
    _person('Ada')
    daily.record_chat('main', SM(_pair('2026-11-02', LONG)))
    with pt._get_connection() as conn:
        conn.execute("UPDATE chunks SET meta = json_remove(meta, '$.md_v') WHERE layer = 'daily'")
        conn.commit()
    md.backfill()
    with pt._get_connection() as conn:
        assert conn.execute("SELECT kind, COUNT(*) FROM edges GROUP BY kind").fetchall() == [('retells', 1)]


# ─── She reads it and cannot write it ────────────────────────────────────────

def test_she_cannot_save_edit_or_delete_a_day():
    daily.record_chat('main', SM(_pair('2026-11-02', 'the pendant held')))
    cid = _rows()[0][0]
    for name, args in (('save_memory', {'content': 'my own day', 'layer': 'daily'}),
                       ('update_memory', {'memory_id': cid, 'content': 'rewritten'}),
                       ('delete_memory', {'memory_id': cid})):
        msg, ok = _ask(name, **args)
        assert not ok and 'daily layer' in msg, (name, msg)
    assert [r[1] for r in _rows()] == ['the pendant held']
    tools = {t['function']['name']: t['function']['parameters']['properties'] for t in pt.get_tools()}
    assert 'daily' not in tools['save_memory']['layer']['enum']
    assert 'daily' in tools['search_memory']['layer']['enum'] and 'day' in tools['search_memory']
    assert 'daily' in tools['get_recent_memories']['layer']['enum'] and 'day' in tools['get_recent_memories']


def test_the_apps_per_memory_buttons_never_act_on_one_piece_of_a_day():
    from plugins.mindpalace.routes import browse
    daily.record_chat('main', SM(_pair('2026-11-02', LONG)))
    cid = _rows()[1][0]
    for out in (browse.delete_chunk(cid=cid), browse.toggle_favorite(cid=cid, body={'favorite': True}),
                browse.set_private(cid=cid, body={'private': True})):
        assert isinstance(out, tuple) and out[1] == 400 and 'Mind > Daily' in out[0]['error']
    assert _text(_rows()) == LONG and {(r[6], r[8]) for r in _rows()} == {(None, 0)}


def test_the_date_maintenance_buttons_leave_days_alone():
    from plugins.mindpalace.routes import browse
    daily.record_chat('main', SM(_pair('2026-11-02', 'Ada said tomorrow we start, on a plan for next Friday.')))
    before = _rows()[0][7]
    for action in ('redate_regex', 'wipe_dates'):
        out = browse.maintenance(body={'action': action, 'scope': S, 'confirm': S})
        assert not isinstance(out, tuple), out
    assert _rows()[0][7] == before and json.loads(before)['event_dates']


def test_the_librarian_never_selects_a_day():
    daily.record_chat('main', SM(_pair('2026-11-02', LONG)))
    pt._save_memory('an ordinary memory about the pendant', S)
    with pt._get_connection() as conn:
        cur = conn.cursor()
        for batch in (librarian.build_batch(cur, S, 'all', 50), librarian.build_temporal_batch(cur, S, 50),
                      librarian.build_link_batch(cur, S, 50), librarian.build_dedup_batch(cur, S, 50)):
            layers = {conn.execute('SELECT layer FROM chunks WHERE id = ?', (r[0],)).fetchone()[0] for r in batch}
            assert 'daily' not in layers


def test_days_stay_out_of_the_recents_feed_and_come_as_their_own_block_in_a_search():
    daily.record_chat('main', SM(_pair('2026-11-02', 'We fixed the pendant wake bug before lunch.')))
    pt._save_memory('The pendant wakes on a double tap', S)
    text, ok = _ask('get_recent_memories')
    assert ok and 'double tap' in text and 'wake bug' not in text
    text, ok = _ask('search_memory', query='pendant')
    assert ok and text.index('double tap') < text.index('From your days')
    head, block = text.split('From your days')
    assert 'wake bug' in block and 'wake bug' not in head and '[daily] Mon Nov 2 2026' in block
    assert "get_recent_memories(layer='daily', day='YYYY-MM-DD')" in block
    text, ok = _ask('search_memory', query='lunch')                     # only a day holds the word
    assert ok and 'but your days have matches' in text and 'before lunch' in text


def test_the_door_line_is_shown_only_where_the_door_exists():
    """The persona MCP door searches memory with no layer or day argument: it
    gets the days, not a line naming arguments it cannot pass."""
    daily.record_chat('main', SM(_pair('2026-11-02', 'We fixed the pendant wake bug before lunch.')))
    text, ok = pt._search_memory('pendant', S)                          # no turn: the MCP door, the console
    assert ok and 'From your days' in text and 'get_recent_memories(' not in text


def test_the_days_block_needs_the_words_or_the_meaning_not_a_loose_prefix():
    """Under an all-layer search the loose pass (any word, any prefix) matched
    nearly every day ever kept, and the block rode on every search she made."""
    daily.record_chat('main', SM(_pair('2026-11-02', 'We fixed the pendant wake bug before lunch.')))
    assert daily.search('pendant bug', S)[0]                            # every word there
    assert daily.search('pendant dinner', S) == ([], [])                # one word of two: not enough here
    assert daily.search('pend', S) == ([], [])
    assert 'wake bug' in daily.search_text('pendant dinner', S)         # asked for the days: the loose pass runs


def test_one_day_matched_reads_long_and_many_days_read_one_piece_each():
    filler = ' '.join(f"Step {i} of the walk was checked and written down with care." for i in range(160))
    days = _pair('2026-11-01', f"The zebra plan began. {filler}")
    for d in range(2, 8):
        days += _pair(f'2026-11-0{d}', f"The quokka came by again on day {d}. {filler}")
    daily.record_chat('main', SM(days))
    text = daily.search_text('zebra', S)
    assert text.startswith('Found 1 day:') and '[excerpt] The zebra plan began.' in text and text.count('[...]') == 1
    assert 3000 < len(text) <= daily.LAYER_CHARS + 400                  # one day took the whole share
    lines, _ = daily.search('quokka', S)
    assert len(lines) == daily.MIXED_DAYS and all('[excerpt] The quokka' in ln for ln in lines)
    assert sum(len(ln) for ln in lines) <= daily.MIXED_CHARS + 3 * 120


def test_a_match_brings_the_piece_after_it_before_a_far_match_or_another_day():
    """Live find 2026-11-20: the paragraph that matched was shown and the
    decision in the very next piece was cut, while an unrelated day took half
    the budget. The outcome follows the setup: it comes first."""
    pad = ' '.join(f"Filler sentence number {i} about nothing in particular at all." for i in range(7))
    setup = f"You asked whether the wombat price was too low and I said it was. {pad}"
    outcome = f"You decided the new figure is forty-five from January. {pad}"
    far = f"Later the wombat price came up once more in passing. {pad}"
    other = f"Filler day. {pad}\n\nSomeone mentioned the wombat price at the window. {pad}"
    middle = '\n\n'.join(f"Middle paragraph {i}. {pad}" for i in range(4))
    daily.record_chat('main', SM(_pair('2026-11-01', f"{setup}\n\n{outcome}\n\n{middle}\n\n{far}")
                                 + _pair('2026-11-02', other)))
    lines, _ = daily.search('wombat price', S)
    busy = next(ln for ln in lines if 'Nov 1 2026' in ln)
    assert 'forty-five from January' in busy                     # the piece after the best match
    assert len(lines) == 2 and len(busy) > len(next(ln for ln in lines if 'Nov 2 2026' in ln))


def test_a_short_day_comes_back_whole_and_a_day_with_a_piece_missing_does_not_say_so():
    daily.record_chat('main', SM(_pair('2026-11-02', 'A quiet day. We read.')))
    lines, _ = daily.search('quiet', S)
    assert lines == ["[daily] Mon Nov 2 2026 (12 messages, chat 'main') [whole day] A quiet day. We read."]
    daily.record_chat('main', SM(_pair('2026-11-03', 'First line of three.\n\nSecond line.\n\nThird line.')))
    with pt._get_connection() as conn:
        conn.execute("DELETE FROM chunks WHERE layer = 'daily' AND content = 'Second line.'")
        conn.commit()
    text = daily.recent_text(S, span=daily.span_bounds(daily.parse_span('2026-11-03')[0]))
    assert 'First line of three. [...] Third line.' in text
    lines, _ = daily.search('line', S)
    assert '[excerpt]' in lines[0] and '[whole day]' not in lines[0]


def test_recents_of_the_layer_is_an_index_and_a_day_reads_whole():
    daily.record_chat('main', SM(_pair('2026-11-01', 'First day text.') + _pair('2026-11-02', LONG)))
    text, ok = _ask('get_recent_memories', layer='daily')
    assert ok and text.startswith('Newest 2 days:')
    assert text.index('Nov 2 2026') < text.index('Nov 1 2026')
    assert '[beginning] We spent the morning' in text and '[whole day] First day text.' in text
    assert 'reply rules were checked' not in text                       # the index, not the day
    text, ok = _ask('get_recent_memories', layer='daily', day='2026-11-02')
    assert ok and LONG in text and 'First day' not in text
    text, ok = _ask('get_recent_memories', layer='daily', day='2026-11-01..2026-11-02')
    assert text.index('First day text.') < text.index('We spent the morning')          # in order
    text, ok = _ask('get_recent_memories', layer='daily', day='2025-01-01')
    assert ok and text == 'No days recorded in that range.'
    text, ok = _ask('get_recent_memories', layer='daily', day='the day of the storm')
    assert not ok and 'day takes' in text


def test_days_too_long_to_show_whole_come_as_the_index(monkeypatch):
    monkeypatch.setattr(daily, 'READ_CHARS', 100)
    daily.record_chat('main', SM(_pair('2026-11-01', LONG) + _pair('2026-11-02', LONG)))
    text = daily.recent_text(S, span=daily.span_bounds(daily.parse_span('2026-11')[0]))
    assert text.startswith('2 days in that range are too long to show whole.') and '[beginning]' in text


def test_a_day_is_found_by_the_day_it_is_of_whatever_the_clock_did(monkeypatch):
    """This layer filters on label (the day a summary is OF), not created: a
    move of 12 time zones put old days under the neighbouring date."""
    daily.record_chat('main', SM(_pair('2026-11-02', 'a day')))
    with pt._get_connection() as conn:
        conn.execute("UPDATE chunks SET created = '2026-11-03T09:00:00+00:00' WHERE layer = 'daily'")
        conn.commit()
    assert 'a day' in _ask('get_recent_memories', layer='daily', day='2026-11-02')[0]
    assert _ask('get_recent_memories', layer='daily', day='2026-11-03')[0] == 'No days recorded in that range.'


def test_day_narrows_every_layer_and_points_at_the_days_when_only_they_hold_it():
    pt._save_memory('an old memory about the garage', S)
    pt._save_memory('a new memory about the garage', S)
    with pt._get_connection() as conn:
        conn.execute("UPDATE chunks SET created = '2026-10-01T16:00:00+00:00' WHERE content LIKE 'an old%'")
        conn.commit()
    text, ok = _ask('search_memory', query='garage', day='2026-10-01')
    assert ok and 'an old memory' in text and 'a new memory' not in text
    text, ok = _ask('get_recent_memories', day='2026-10')
    assert ok and 'an old memory' in text and 'a new memory' not in text
    text, ok = _ask('search_memory', query='garage', day='2020-01-01')
    assert ok and "for day '2020-01-01'" in text
    text, ok = _ask('search_memory', query='garage', day='soonish')
    assert not ok
    daily.record_chat('main', SM(_pair('2026-11-02', 'a day')))
    text, ok = _ask('get_recent_memories', day='2026-11-02')            # no memory that day, but the day is kept
    assert ok and text.startswith("No memories stored for day '2026-11-02'.") and "layer='daily'" in text
    assert "layer='daily'" not in _ask('get_recent_memories', day='2026-11-09')[0]
    text, ok = _ask('search_memory', query='garage', layer='knowledge', day='yesterday')
    assert ok and 'day was not applied' in text


@pytest.mark.parametrize('text,first,last', [
    ('2026-11-02', '2026-11-02', '2026-11-02'),
    ('2026-11-09..2026-11-02', '2026-11-02', '2026-11-09'),
    ('2026-11-02 to 2026-11-09', '2026-11-02', '2026-11-09'),
    ('2026-02', '2026-02-01', '2026-02-28'),
    ('Yesterday', '2026-11-19', '2026-11-19'),
    ('day before yesterday', '2026-11-18', '2026-11-18'),
    ('3 days ago', '2026-11-17', '2026-11-17'),
    ('last week', '2026-11-09', '2026-11-15'),
    ('this week', '2026-11-16', '2026-11-20'),
    ('last month', '2026-10-01', '2026-10-31'),
    ('last 3 days', '2026-11-17', '2026-11-20'),
    ('Monday', '2026-11-16', '2026-11-16'),             # asked on Friday the 20th: this week's
    ('last monday', '2026-11-16', '2026-11-16'),
    ('friday', '2026-11-13', '2026-11-13'),             # never today: the Friday before
    ('sat', '2026-11-14', '2026-11-14'),
    ('Nov 2', '2026-11-02', '2026-11-02'),
    ('november 2nd, 2025', '2025-11-02', '2025-11-02'),
    ('2 nov', '2026-11-02', '2026-11-02'),
    ('dec 25', '2025-12-25', '2025-12-25'),             # not come yet this year: the last one
])
def test_the_day_argument(text, first, last):
    span, err = daily.parse_span(text)
    assert err is None and span == (date.fromisoformat(first), date.fromisoformat(last))


@pytest.mark.parametrize('bad', [
    '2026-13-40', 'whenever', 'tomorrow', 'in 2 weeks', 'since monday', 'before 2026-11-10', 'nov 2-9',
    'week of nov 2', 'thanksgiving', 'the 18th', 'this weekend', 'feb 30', 'march', 'next friday',
])
def test_a_day_it_cannot_read_is_an_error_never_a_guess(bad):
    """The first version handed anything else to a plan parser and shifted a
    future answer back a year: "Monday" searched a Sunday 51 weeks ago and
    answered "nothing that day", with no error for her to correct from."""
    span, err = daily.parse_span(bad)
    assert span is None and 'day takes' in err


def test_no_day_argument_is_no_filter():
    assert daily.parse_span('') == (None, None) and daily.parse_span(None) == (None, None)


# ─── A deleted day stays deleted ─────────────────────────────────────────────

def test_a_deleted_day_is_not_brought_back_by_the_next_compact():
    sm = SM(_pair('2026-11-01', 'kept') + _pair('2026-11-02', 'Ada asked me to forget this'))
    _person('Ada')
    daily.record_chat('main', sm)
    gone = next(r[2] for r in _rows() if r[1].startswith('Ada'))
    with pt._get_connection() as conn:
        assert daily.forget(conn.cursor(), S, [gone]) == 1
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM edges WHERE src_type = 'chunk'").fetchone()[0] == 0
    assert daily.record_chat('main', sm)['days'] == []
    assert [r[1] for r in _rows()] == ['kept']
    pt.delete_scope(S)                                          # the scope gone: a new one starts clean
    assert daily.record_chat('main', sm)['days'] == ['2026-11-01', '2026-11-02']


# ─── The listeners ───────────────────────────────────────────────────────────

def test_only_a_daily_compact_wakes_the_recorder(monkeypatch):
    ran = []

    class Now:
        def __init__(self, target, args, **k):
            self.go = lambda: target(*args)

        def start(self):
            self.go()
    monkeypatch.setattr(daily.threading, 'Thread', Now)
    monkeypatch.setattr(daily, '_run', ran.append)
    daily._on_compressed({'data': {'chat_name': 'main', 'report': {'mode': 'whole'}}})
    daily._on_compressed({'data': {'chat_name': '', 'report': {'mode': 'daily'}}})
    daily._on_compressed(None)
    assert ran == []
    daily._on_compressed({'data': {'chat_name': 'main', 'report': {'mode': 'daily'}}})
    assert ran == ['main']


def test_a_compact_records_its_own_chat_and_every_start_walks_them_all(monkeypatch):
    """A compact with nothing new publishes nothing, so the event alone never
    repaired a night that failed: every start of the palace takes all chats."""
    seen = []
    monkeypatch.setattr(daily, 'sweep', lambda chats=None, sm=None: seen.append(chats) or {'days': 0, 'failed': 0})
    daily._run('main')
    assert seen == [['main']]
    import time
    monkeypatch.setattr(time, 'sleep', lambda s: None)
    monkeypatch.setattr(daily, '_session_manager', lambda: 'the session manager')
    daily._boot()
    assert seen == [['main'], None]
    monkeypatch.setattr(daily, '_stopping', True)               # unloading before the app is up: nothing
    daily._boot()
    assert len(seen) == 2


def test_one_chat_failing_does_not_stop_the_sweep(monkeypatch):
    sm = SM(_pair('2026-11-02', 'x'))
    sm.list_chat_files = lambda: [{'name': 'broken'}, {'name': 'main'}]
    exports = sm.export_chat

    def export(chat):
        if chat == 'broken':
            raise RuntimeError('unreadable rows')
        return exports(chat)
    sm.export_chat = export
    told = []
    from core import notify
    monkeypatch.setattr(notify, 'notify', lambda title, **k: told.append((title, k.get('key'), k.get('body'))))
    assert daily.sweep(sm=sm) == {'days': 1, 'failed': 1}
    assert told[0][:2] == ("A day's summary was not saved to memory", 'daily-record')
    assert 'when Sapphire next starts' in told[0][2]


def test_the_listeners_are_registered_once():
    from core.event_bus import Events, get_event_bus
    bus = get_event_bus()
    daily.start(boot=False)
    daily.start(boot=False)
    assert bus._hooks.get(Events.CHAT_COMPRESSED, ()).count(daily._on_compressed) == 1
    assert bus._hooks.get('chat_renamed', ()).count(daily._on_renamed) == 1
    daily.stop()
    assert daily._on_compressed not in bus._hooks.get(Events.CHAT_COMPRESSED, ())
    assert daily._on_renamed not in bus._hooks.get('chat_renamed', ())


# ─── A chat's name ───────────────────────────────────────────────────────────

def test_a_vaulted_chats_name_is_nowhere_in_its_days():
    from plugins.mindpalace.hooks import chat_vaulted
    daily.record_chat('secret-diary', SM(_pair('2026-11-02', 'an ordinary day')))
    chat_vaulted.chat_vaulted(SimpleNamespace(metadata={'name': 'secret-diary'}))
    with pt._get_connection() as conn:
        dump = json.dumps([list(map(str, r)) for r in conn.execute("SELECT * FROM chunks").fetchall()]
                          + [list(map(str, r)) for r in conn.execute("SELECT * FROM ledger").fetchall()])
    assert 'secret-diary' not in dump


def test_a_renamed_chats_memories_follow_the_new_name():
    """Days show the chat's name to her, and the vault scrub matches the
    CURRENT name: a chat renamed and then vaulted kept its old name for good."""
    from plugins.mindpalace.hooks import chat_vaulted
    daily.record_chat('lawyer-notes', SM(_pair('2026-11-02', 'an ordinary day')))
    fm.tool_context.set({**TURN, 'chat': 'lawyer-notes'})
    pt._save_memory('a memory saved in that chat', S)
    fm.tool_context.set(None)
    daily._on_renamed({'data': {'old': 'lawyer-notes', 'new': 'lawyer-notes'}})            # not a rename
    daily._renamed('lawyer-notes', 'notes')
    with pt._get_connection() as conn:
        names = {r[0] for r in conn.execute("SELECT json_extract(meta, '$.chat') FROM chunks")}
    assert names == {'notes'} and "chat 'notes'" in daily.recent_text(S)
    chat_vaulted.chat_vaulted(SimpleNamespace(metadata={'name': 'notes'}))
    with pt._get_connection() as conn:
        dump = json.dumps([list(map(str, r)) for r in conn.execute("SELECT * FROM chunks").fetchall()])
    assert 'lawyer-notes' not in dump and "chat '" not in daily.recent_text(S)


# ─── The app's side: whole days ──────────────────────────────────────────────

def test_the_app_lists_whole_days_with_what_is_known_about_them():
    from plugins.mindpalace.routes import daily_routes, browse
    _person('Ada')
    daily.record_chat('main', SM(_pair('2026-11-01', 'A quiet day.') + _pair('2026-11-02', LONG)))
    out = daily_routes.list_days(query={'scope': S})
    assert out['total'] == 2 and [d['day'] for d in out['days']] == ['2026-11-02', '2026-11-01']
    d = out['days'][0]
    assert d['text'] == LONG and d['parts'] > 3 and d['people'] == ['Ada'] and d['dates'] == ['2026-11-03']
    assert (d['chat'], d['persona'], d['messages'], d['private']) == ('main', 'sapphire', 12, False)
    assert d['writer'] == {'model': 'opus', 'provider': CLOUD, 'origin': 'Sapphire-Purple'}
    assert [x['day'] for x in daily_routes.list_days(query={'scope': S, 'q': 'quiet'})['days']] == ['2026-11-01']
    assert daily_routes.list_days(query={'scope': S, 'q': '2026-11-02'})['total'] == 1
    assert daily_routes.list_days(query={'scope': S, 'q': '100%'})['total'] == 0       # a typed % is a %
    assert daily_routes.list_days(query={'scope': 'other'}) == {'days': [], 'total': 0, 'limit': 20, 'offset': 0}
    layers = browse.list_layers()['layers']
    assert layers[0]['key'] == 'daily' and layers[0]['view'] == 'daily'


def test_the_app_pages_through_every_day_however_many():
    """Scouts, 2026-11-20: the tab asked for a growing limit from offset 0 and
    the route capped it at 100, so day 101 could not be reached."""
    from plugins.mindpalace.routes import daily_routes
    days = []
    for n in range(130):
        d = date(2026, 1, 1).toordinal() + n
        days += _pair(date.fromordinal(d).isoformat(), f'day number {n}')
    daily.record_chat('main', SM(days))
    seen, offset = [], 0
    while True:
        page = daily_routes.list_days(query={'scope': S, 'limit': 20, 'offset': offset})
        assert page['total'] == 130
        if not page['days']:
            break
        seen += [d['day'] for d in page['days']]
        offset += 20
    assert len(seen) == len(set(seen)) == 130 and seen == sorted(seen, reverse=True)


def test_the_app_marks_and_deletes_a_whole_day():
    from plugins.mindpalace.routes import daily_routes
    sm = SM(_pair('2026-11-02', LONG))
    daily.record_chat('main', sm)
    src = _rows()[0][2]
    assert daily_routes.set_day_private(body={'scope': S, 'source': src, 'private': True})['private'] is True
    assert {r[8] for r in _rows()} == {1}
    fm.tool_context.set(TURN)
    assert daily.search('pendant', S) == ([], [])                           # a cloud reader is not given it
    fm.tool_context.set(None)
    assert daily_routes.list_days(query={'scope': S})['days'][0]['private'] is True      # the app shows it
    daily_routes.set_day_private(body={'scope': S, 'source': src, 'private': False})
    assert {r[8] for r in _rows()} == {0}
    assert daily_routes.set_day_private(body={'scope': S, 'source': src, 'private': 'yes'})[1] == 400
    assert daily_routes.delete_day(query={'scope': S, 'source': 'day:nope'})[1] == 404
    assert daily_routes.delete_day(query={'scope': S, 'source': 'chunk-7'})[1] == 400
    assert daily_routes.delete_day(query={'scope': S, 'source': src})['deleted'] == len(daily.split(LONG))
    assert _rows() == [] and daily.record_chat('main', sm)['days'] == []      # and it stays deleted
    with pt._get_connection() as conn:
        acts = [r[0] for r in conn.execute("SELECT action FROM ledger WHERE layer = 'daily' ORDER BY id")]
    assert acts == ['recorded', 'private-model', 'released', 'deleted']


def test_clearing_the_tab_is_a_reset_days_still_in_a_chat_come_back():
    from plugins.mindpalace.routes import daily_routes, browse
    sm = SM(_pair('2026-11-02', 'a day'))
    daily.record_chat('main', sm)
    daily_routes.delete_day(query={'scope': S, 'source': _rows()[0][2]})
    assert daily.record_chat('main', sm)['days'] == []
    out = browse.maintenance(body={'action': 'clear_layer', 'scope': S, 'layer': 'daily', 'confirm': S})
    assert not isinstance(out, tuple), out
    assert daily.record_chat('main', sm)['days'] == ['2026-11-02']
    out = browse.maintenance(body={'action': 'wipe_scope', 'scope': S, 'confirm': S})
    assert not isinstance(out, tuple) and _rows() == []
    assert daily.record_chat('main', sm)['days'] == ['2026-11-02']


def test_days_travel_in_a_file_whole_or_not_at_all():
    """Scouts, 2026-11-20: row-by-row import dedupes on content+created, and a
    day's pieces share `created`: two identical lines lost one, a deleted day
    came back, and a day already here arrived a second time interleaved."""
    from plugins.mindpalace.routes import transfer, daily_routes
    _person('Ada')
    twice = "Ada called.\n\nNothing else.\n\nAda called.\n\nNothing else."        # identical pieces, one day
    daily.record_chat('main', SM(_pair('2026-11-01', twice) + _pair('2026-11-02', LONG)
                                 + _pair('2026-11-03', 'a day that gets deleted')))
    data = transfer.export_data(query={'scope': S, 'layer': 'daily'})
    assert data['counts']['chunks'] == len(_rows())
    gone = next(r[2] for r in _rows() if r[4] == '2026-11-03')
    daily_routes.delete_day(query={'scope': S, 'source': gone})

    again = transfer.import_data(body={'scope': S, 'expect_layer': 'daily', 'data': data})
    assert again['imported'] == 0                           # held days are skipped whole, the deleted one stays deleted
    assert {r[4] for r in _rows()} == {'2026-11-01', '2026-11-02'}

    _person('Ada', 'elsewhere')
    out = transfer.import_data(body={'scope': 'elsewhere', 'expect_layer': 'daily', 'data': data})
    assert out['success'] and out['imported'] == data['counts']['chunks']
    there = {d['day']: d for d in daily_routes.list_days(query={'scope': 'elsewhere'})['days']}
    assert there['2026-11-01']['text'] == twice and there['2026-11-02']['text'] == LONG
    assert there['2026-11-01']['people'] == ['Ada'] and there['2026-11-02']['people'] == ['Ada']
    with pt._get_connection() as conn:
        kinds = conn.execute("SELECT g.kind, COUNT(*) FROM edges g JOIN chunks c ON c.id = g.src_id "
                             "WHERE c.scope = 'elsewhere' GROUP BY g.kind").fetchall()
    assert kinds == [('retells', 2)]                        # one per person per day that names her, as when recorded
    assert transfer.import_data(body={'scope': 'elsewhere', 'expect_layer': 'daily', 'data': data})['imported'] == 0
    loose = {**data, 'chunks': [{'content': 'a row with no day', 'created': '2026-11-09T12:00:00+00:00'}]}
    assert transfer.import_data(body={'scope': 'elsewhere', 'expect_layer': 'daily', 'data': loose})['imported'] == 0


# ─── The whole road: a real compact, the real bus, the real listener ─────────

def _real_chat(tmp_path, rows, **settings):
    from unittest.mock import patch
    defaults = {'prompt': 'default'}
    with patch('core.chat.history.get_system_defaults', side_effect=lambda: dict(defaults)), \
            patch('core.chat.history.get_user_defaults', side_effect=lambda: dict(defaults)):
        from core.chat.history import ChatSessionManager
        mgr = ChatSessionManager(history_dir=str(tmp_path / 'history'))
        mgr.create_chat('diary', {'memory_scope': S, 'llm_primary': CLOUD, 'llm_model': 'opus', **settings})
        mgr.append_messages_to_chat('diary', rows)
    return mgr


def test_a_real_daily_compact_lands_its_days_in_memory(tmp_path, monkeypatch):
    import time
    from datetime import datetime, timedelta
    from unittest.mock import patch
    from core.chat import compress

    prompts = []

    class Mock:
        def chat_completion(self, msgs, generation_params=None):
            prompts.append(msgs[0]['content'])
            return SimpleNamespace(content='We fixed the pendant. Ada was glad.')

    rows = []
    for back in (3, 2):
        ts = (datetime.now() - timedelta(days=back)).replace(hour=14).isoformat()
        rows += [{'role': 'user', 'content': 'check the mail', 'timestamp': ts, 'origin': 'task:Morning mail'},
                 {'role': 'assistant', 'content': 'nothing new', 'timestamp': ts},
                 {'role': 'user', 'content': 'the pendant still will not wake ' * 60, 'timestamp': ts},
                 {'role': 'assistant', 'content': 'found it: the timer ' * 60, 'timestamp': ts}]
    now = datetime.now().isoformat()
    rows += [{'role': 'user', 'content': 'good morning', 'timestamp': now},
             {'role': 'assistant', 'content': 'morning', 'timestamp': now}]
    mgr = _real_chat(tmp_path, rows, persona='sapphire')
    defaults = {'prompt': 'default'}
    with patch('core.chat.history.get_system_defaults', side_effect=lambda: dict(defaults)), \
            patch('core.chat.history.get_user_defaults', side_effect=lambda: dict(defaults)):
        monkeypatch.setattr(compress, 'make_provider', lambda k, m, **kw: Mock())
        monkeypatch.setattr(daily, '_session_manager', lambda: mgr)
        daily.start(boot=False)
        try:
            ok, err = compress.start_compress_job(mgr, 'diary', mode='daily', provider_key=CLOUD, model='opus',
                                                  target_tokens=2000, keep_last_turns=1, backup=False,
                                                  persona='sapphire')
            assert ok, err
            for _ in range(100):
                if len({r[2] for r in _rows()}) == 2:
                    break
                time.sleep(0.05)
        finally:
            daily.stop()
        monkeypatch.setattr(daily, '_stopping', False)
        status = compress.get_job_status()
        assert status.get('ok'), status
        got = _rows()
        days = sorted({r[4] for r in got})
        assert days == [(datetime.now() - timedelta(days=b)).date().isoformat() for b in (3, 2)]
        assert {r[1] for r in got} == {'We fixed the pendant. Ada was glad.'}
        m = json.loads(got[0][7])
        assert (m['chat'], m['persona'], m['messages'], m['added_by']) == ('diary', 'sapphire', 4, 'ai')
        assert {(r[9], r[10], r[8]) for r in got} == {('opus', CLOUD, 0)}
        assert 'SCHEDULED TASK (Morning mail): check the mail' in prompts[0] and 'ceiling, not a goal' in prompts[0]
        text, ok = _ask('search_memory', query='pendant', layer='daily')
        assert ok and text.startswith('Found 2 days:') and '[whole day]' in text
        # The same night again: nothing new to compact, nothing doubled.
        assert daily.record_chat('diary', mgr)['days'] == [] and len(_rows()) == 2


def test_a_compact_cancelled_for_growing_the_chat_records_nothing(tmp_path, monkeypatch):
    """The grow guard aborts before the chat is written: no day pairs, no
    CHAT_COMPRESSED, so nothing reaches the layer. The next run retries."""
    import time
    from datetime import datetime, timedelta
    from unittest.mock import patch
    from core.chat import compress

    class Chatty:
        def chat_completion(self, msgs, generation_params=None):
            return SimpleNamespace(content='a summary far longer than the day it retells ' * 400)

    woke = []
    ts = (datetime.now() - timedelta(days=2)).replace(hour=14).isoformat()
    now = datetime.now().isoformat()
    rows = [{'role': 'user', 'content': 'a short day', 'timestamp': ts},
            {'role': 'assistant', 'content': 'yes', 'timestamp': ts},
            {'role': 'user', 'content': 'morning', 'timestamp': now},
            {'role': 'assistant', 'content': 'morning', 'timestamp': now}]
    mgr = _real_chat(tmp_path, rows)
    defaults = {'prompt': 'default'}
    with patch('core.chat.history.get_system_defaults', side_effect=lambda: dict(defaults)), \
            patch('core.chat.history.get_user_defaults', side_effect=lambda: dict(defaults)):
        monkeypatch.setattr(compress, 'make_provider', lambda k, m, **kw: Chatty())
        monkeypatch.setattr(daily, '_session_manager', lambda: mgr)
        monkeypatch.setattr(daily, '_run', woke.append)
        daily.start(boot=False)
        try:
            ok, err = compress.start_compress_job(mgr, 'diary', mode='daily', provider_key=CLOUD, model='opus',
                                                  target_tokens=2000, keep_last_turns=1, backup=False)
            assert ok, err
            for _ in range(100):
                if compress.get_job_status().get('done'):
                    break
                time.sleep(0.05)
            time.sleep(0.1)
        finally:
            daily.stop()
        monkeypatch.setattr(daily, '_stopping', False)
        status = compress.get_job_status()
        assert status.get('ok') is False and 'would grow the chat' in status.get('error', '')
        assert woke == [] and _rows() == []
        assert [m['content'] for m in mgr.export_chat('diary')['messages']] == [r['content'] for r in rows]
        assert daily.record_chat('diary', mgr)['days'] == []            # and nothing to pick up later
