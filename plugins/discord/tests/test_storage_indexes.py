"""Migration 12 indexes + the traces count cap (hunt 2026-09-12, H5 / C3)."""

from plugins.discord.storage.repositories import traces as traces_mod
from plugins.discord.storage.repositories.traces import TraceRepository
from plugins.discord.storage.sqlite import SQLiteService


def _sqlite(tmp_path):
    service = SQLiteService(tmp_path / 'idx.sqlite3')
    service.start()
    return service


def test_migration_12_creates_hot_path_indexes(tmp_path):
    service = _sqlite(tmp_path)
    conn = service.connection()
    assert conn.execute('SELECT version FROM schema_version').fetchone()[0] >= 12
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()}
    for expected in (
        'idx_messages_channel_created', 'idx_messages_author',
        'idx_traces_created', 'idx_traces_type_created',
        'idx_profile_buffers_pending', 'idx_tasks_due',
        'idx_media_artifacts_message', 'idx_media_artifacts_channel_created',
    ):
        assert expected in names
    # the recent-messages query must now use the index, not a scan
    plan = ' '.join(
        str(row[-1]) for row in conn.execute(
            'EXPLAIN QUERY PLAN SELECT * FROM messages WHERE channel_id = ? ORDER BY created_at DESC LIMIT 20',
            ('c1',),
        ).fetchall()
    )
    assert 'idx_messages_channel_created' in plan


def test_traces_are_capped_by_count(tmp_path, monkeypatch):
    monkeypatch.setattr(traces_mod, 'TRACE_ROW_CAP', 100)
    monkeypatch.setattr(traces_mod, '_TRIM_EVERY', 1)
    service = _sqlite(tmp_path)
    repo = TraceRepository(service)
    for i in range(150):
        repo.record_trace('t', f'row {i}', {'i': i})
    conn = service.connection()
    assert conn.execute('SELECT COUNT(*) FROM traces').fetchone()[0] == 100
    # the newest survive
    assert conn.execute('SELECT MAX(id) FROM traces').fetchone()[0] == 150
    assert repo.list_traces(limit=1)[0]['summary'] == 'row 149'
