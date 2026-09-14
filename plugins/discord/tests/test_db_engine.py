"""D1 (hunt 2026-09-12): migration runner, WAL + lock, legacy DB adoption, DM-origin facts."""

import sqlite3
import threading
from pathlib import Path

import pytest

from plugins.discord.storage import migrations as mig
from plugins.discord.storage import sqlite as sq
from plugins.discord.storage.repositories.interests import InterestRepository
from plugins.discord.storage.repositories.profile_buffers import ProfileBufferRepository
from plugins.discord.storage.repositories.profiles import ProfileRepository
from plugins.discord.storage.sqlite import SQLiteService, resolve_default_db_path


def _service(tmp_path):
    service = SQLiteService(tmp_path / 'engine.sqlite3')
    service.start()
    return service


# ── H4: per-statement runner ─────────────────────────────────────────────

def test_duplicate_column_is_skipped_per_statement_and_the_rest_still_applies(tmp_path, monkeypatch):
    conn = sqlite3.connect(tmp_path / 'm.sqlite3')
    conn.execute('CREATE TABLE schema_version (version INTEGER NOT NULL)')
    conn.execute('INSERT INTO schema_version VALUES (0)')
    conn.execute('CREATE TABLE t (a INTEGER, b INTEGER)')   # 'b' already there = half-applied earlier run
    conn.commit()
    monkeypatch.setattr(mig, 'MIGRATIONS', [
        (1, 'ALTER TABLE t ADD COLUMN b INTEGER; ALTER TABLE t ADD COLUMN c INTEGER; CREATE INDEX i_c ON t(c);'),
    ])
    assert mig.apply_migrations(conn) == 1
    cols = {r[1] for r in conn.execute('PRAGMA table_info(t)')}
    assert cols == {'a', 'b', 'c'}                       # the old runner died on the first ALTER
    assert conn.execute('SELECT version FROM schema_version').fetchone()[0] == 1


def test_failed_statement_rolls_the_version_back(tmp_path, monkeypatch):
    conn = sqlite3.connect(tmp_path / 'm.sqlite3')
    conn.execute('CREATE TABLE schema_version (version INTEGER NOT NULL)')
    conn.execute('INSERT INTO schema_version VALUES (0)')
    conn.commit()
    monkeypatch.setattr(mig, 'MIGRATIONS', [
        (1, 'CREATE TABLE ok (x INTEGER); THIS IS NOT SQL;'),
    ])
    with pytest.raises(sqlite3.OperationalError):
        mig.apply_migrations(conn)
    assert conn.execute('SELECT version FROM schema_version').fetchone()[0] == 0
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert 'ok' not in tables                            # one transaction: nothing half-applied


def test_migration_13_adds_origin_columns(tmp_path):
    conn = _service(tmp_path).connection()
    assert conn.execute('SELECT version FROM schema_version').fetchone()[0] >= 13
    for table in ('profile_facts', 'profile_buffers', 'interest_topics'):
        assert 'origin' in {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}


# ── M1: WAL, busy timeout, one lock ──────────────────────────────────────

def test_start_sets_wal_and_busy_timeout(tmp_path):
    conn = _service(tmp_path).connection()
    assert conn.execute('PRAGMA journal_mode').fetchone()[0].lower() == 'wal'
    assert conn.execute('PRAGMA busy_timeout').fetchone()[0] == 5000
    assert conn.execute('PRAGMA synchronous').fetchone()[0] == 1   # NORMAL


def test_shared_connection_survives_a_thread_storm(tmp_path):
    service = _service(tmp_path)
    conn = service.connection()
    conn.execute('CREATE TABLE storm (n INTEGER)')
    conn.commit()
    errors = []

    def hammer(k):
        try:
            for i in range(150):
                c = service.connection()
                c.execute('INSERT INTO storm (n) VALUES (?)', (k * 1000 + i,))
                c.commit()
                c.execute('SELECT COUNT(*) FROM storm').fetchone()
        except Exception as exc:  # pragma: no cover - the assertion below reports it
            errors.append(exc)

    threads = [threading.Thread(target=hammer, args=(k,)) for k in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert conn.execute('SELECT COUNT(*) FROM storm').fetchone()[0] == 8 * 150
    service.checkpoint()                                  # WAL folds back without error
    service.stop()


# ── M31: legacy database adopted once ────────────────────────────────────

def test_legacy_db_moves_into_place_once(tmp_path, monkeypatch):
    root = tmp_path / 'sapphire'
    (root / 'core').mkdir(parents=True)
    plugin_file = root / 'plugins' / 'discord' / 'storage' / 'sqlite.py'
    plugin_file.parent.mkdir(parents=True)
    monkeypatch.setattr(sq, '__file__', str(plugin_file))
    legacy = root / 'user' / 'plugin_state' / 'discord_cognitive' / 'discord.sqlite3'
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b'legacy-bytes')
    legacy.with_name('discord.sqlite3-wal').write_bytes(b'wal')
    # the old trap: a stray file in the NEW dir used to flip the plugin to an empty DB
    (root / 'user' / 'plugin_state' / 'discord').mkdir(parents=True)
    (root / 'user' / 'plugin_state' / 'discord' / 'discord_mcp_key.json').write_text('{}')

    path = resolve_default_db_path('discord')

    assert path == root / 'user' / 'plugin_state' / 'discord' / 'discord.sqlite3'
    assert path.read_bytes() == b'legacy-bytes'
    assert path.with_name('discord.sqlite3-wal').read_bytes() == b'wal'
    assert not legacy.exists()
    assert resolve_default_db_path('discord') == path      # idempotent


# ── H16b: DM-origin facts never reach a server prompt ────────────────────

def test_dm_facts_stay_out_of_servers_but_show_in_dms(tmp_path):
    service = _service(tmp_path)
    facts = ProfileRepository(service)
    facts.add_fact('bot', 'u1', 'told her in a DM: going through a divorce', origin='dm')
    facts.add_fact('bot', 'u1', 'likes chess', origin='guild-9')
    facts.add_fact('bot', 'u1', 'operator note', origin='')
    in_server = {f['content'] for f in facts.list_facts('bot', 'u1', for_guild='guild-9')}
    in_dm = {f['content'] for f in facts.list_facts('bot', 'u1', for_guild=None)}
    assert 'told her in a DM: going through a divorce' not in in_server
    assert in_server == {'likes chess', 'operator note'}
    assert 'told her in a DM: going through a divorce' in in_dm


def test_interest_origin_upgrades_from_dm_to_public_never_back(tmp_path):
    service = _service(tmp_path)
    repo = InterestRepository(service)
    repo.bump('bot', 'u1', 'guitar', origin='dm')
    assert repo.list_for_user('bot', 'u1', exclude_dm=True) == []
    repo.bump('bot', 'u1', 'guitar', origin='guild-9')      # said it in a server too
    assert [r['topic'] for r in repo.list_for_user('bot', 'u1', exclude_dm=True)] == ['guitar']
    repo.bump('bot', 'u1', 'guitar', origin='dm')
    assert repo.list_for_user('bot', 'u1', exclude_dm=True)[0]['origin'] == 'guild-9'
    assert repo.top_for_users('bot', ['u1']) and repo.top_for_users('bot', ['u1'])[0]['topic'] == 'guitar'
    repo.bump('bot', 'u2', 'therapy', origin='dm')
    assert [r['topic'] for r in repo.top_for_users('bot', ['u2'])] == []   # outreach never surfaces DM topics


def test_buffers_carry_origin_for_the_distiller(tmp_path):
    service = _service(tmp_path)
    buffers = ProfileBufferRepository(service)
    buffers.add('bot', 'u1', 'a private thing', origin='dm')
    rows = buffers.list_unprocessed_for_user('bot', 'u1')
    assert rows[0]['origin'] == 'dm'


def test_build_context_respects_is_dm(tmp_path):
    from plugins.discord.memory.interest_service import InterestService
    from plugins.discord.memory.profile_service import ProfileService

    service = _service(tmp_path)
    profiles = ProfileRepository(service)
    interests = InterestService(interest_repository=InterestRepository(service))
    svc = ProfileService(profile_repository=profiles, interest_service=interests)
    svc.remember_fact('bot', 'u1', 'secret', origin='dm')
    svc.record_interaction('bot', 'u1', message_text='I love gardening and gardening tools', origin='dm')
    server = svc.build_context('bot', 'u1', guild_id='guild-9', channel_id='c', is_dm=False)
    dm = svc.build_context('bot', 'u1', guild_id='', channel_id='dm1', is_dm=True)
    assert [f['content'] for f in server['facts']] == []
    assert server['interests'] == []
    assert [f['content'] for f in dm['facts']] == ['secret']


def test_runner_closes_a_callers_implicit_transaction_first(tmp_path, monkeypatch):
    conn = sqlite3.connect(tmp_path / 'm.sqlite3')
    conn.execute('CREATE TABLE schema_version (version INTEGER NOT NULL)')
    conn.execute('INSERT INTO schema_version VALUES (0)')     # implicit txn left OPEN
    assert conn.in_transaction
    monkeypatch.setattr(mig, 'MIGRATIONS', [(1, 'CREATE TABLE t (a INTEGER);')])
    assert mig.apply_migrations(conn) == 1                    # no "cannot start a transaction"
