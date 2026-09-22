"""Schema v2 (S7): a fresh file, the v1 adoption (rename aside + accounts copied, fail-loud),
the shared locked connection, and the legacy-path move (M31)."""
import sqlite3
import threading

from plugins.discord.storage import sqlite as sq
from plugins.discord.storage.repositories import AccountRepository, ChannelRepository, MessageRepository
from plugins.discord.storage.schema import SCHEMA_VERSION
from plugins.discord.storage.sqlite import SQLiteService, resolve_default_db_path, stored_version


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}


def _service(path):
    service = SQLiteService(path)
    service.start()
    return service


def _v1_file(path, accounts=(('sapphire', 'tok-1'), ('leona', 'tok-2')), version=13, with_accounts=True):
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE schema_version (version INTEGER NOT NULL)')
    conn.execute('INSERT INTO schema_version VALUES (?)', (version,))
    if with_accounts:
        conn.execute('CREATE TABLE accounts (name TEXT PRIMARY KEY, token TEXT NOT NULL, bot_name TEXT DEFAULT "", '
                     'bot_id TEXT DEFAULT "", state TEXT DEFAULT "disconnected", last_error TEXT DEFAULT "", '
                     'created_at REAL NOT NULL, updated_at REAL NOT NULL)')
        for name, token in accounts:
            conn.execute('INSERT INTO accounts VALUES (?, ?, ?, ?, ?, ?, 1.0, 2.0)', (name, token, 'Bot ' + name, '9', 'connected', ''))
    # every v1 file carries observations (migration 1) — the v2 detector's marker
    conn.execute('CREATE TABLE observations (id INTEGER PRIMARY KEY, observation_type TEXT, payload_json TEXT)')
    conn.execute('CREATE TABLE profile_facts (account_name TEXT, user_id TEXT, fact TEXT)')
    conn.execute("INSERT INTO profile_facts VALUES ('sapphire', 'u1', 'likes apples')")
    conn.execute('CREATE TABLE traces (id INTEGER PRIMARY KEY, trace_type TEXT)')
    conn.commit()
    conn.close()


def test_fresh_file_gets_schema_v2(tmp_path):
    service = _service(tmp_path / 'fresh.sqlite3')
    conn = service.connection()
    assert _tables(conn) == {'schema_version', 'accounts', 'guilds', 'channels', 'users', 'messages'}
    assert conn.execute('SELECT version FROM schema_version').fetchone()[0] == SCHEMA_VERSION
    service.stop()
    assert stored_version(tmp_path / 'fresh.sqlite3') == SCHEMA_VERSION
    _service(tmp_path / 'fresh.sqlite3').stop()                           # idempotent reopen


def test_v1_file_is_moved_aside_and_only_the_accounts_come_over(tmp_path):
    path = tmp_path / 'discord.sqlite3'
    _v1_file(path)
    service = _service(path)
    conn = service.connection()
    assert 'profile_facts' not in _tables(conn) and 'traces' not in _tables(conn)
    rows = AccountRepository(service).list_accounts()
    assert [r['name'] for r in rows] == ['leona', 'sapphire']
    assert AccountRepository(service).get_token('sapphire') == 'tok-1'
    assert rows[1]['bot_name'] == 'Bot sapphire' and rows[1]['state'] == 'connected'
    service.stop()
    aside = tmp_path / 'discord.sqlite3.pre-2.0'
    assert aside.exists() and stored_version(aside) == 13
    old = sqlite3.connect(aside)
    assert old.execute('SELECT COUNT(*) FROM profile_facts').fetchone()[0] == 1   # the old file is untouched
    old.close()
    _service(path).stop()                                                  # second boot: nothing to adopt
    assert not (tmp_path / 'discord.sqlite3.pre-2.0.1').exists()


def test_a_failed_adoption_restores_the_v1_file_and_refuses_to_boot(tmp_path):
    path = tmp_path / 'discord.sqlite3'
    _v1_file(path, with_accounts=False)                                    # no accounts table: the copy fails
    service = SQLiteService(path)
    try:
        service.start()
    except RuntimeError as exc:
        assert 'restored' in str(exc)
    else:
        raise AssertionError('must refuse to boot')
    assert stored_version(path) == 13 and not (tmp_path / 'discord.sqlite3.pre-2.0').exists()


def test_repositories_round_trip_and_forget(tmp_path):
    from types import SimpleNamespace
    service = _service(tmp_path / 'r.sqlite3')
    accounts, channels, messages = AccountRepository(service), ChannelRepository(service), MessageRepository(service)
    accounts.upsert_account('sapphire', token='t')
    accounts.update_connection_state('sapphire', 'connected', bot_name='Sapph', bot_id='1')
    assert accounts.get_account('sapphire')['bot_name'] == 'Sapph'
    channels.upsert_guild('g1', 'Guild')
    channels.upsert_channel('c1', 'g1', 'general')
    channels.upsert_user('u1', 'alice', 'Alice')
    assert channels.get_guild_name('g1') == 'Guild' and channels.get_channel('c1')['name'] == 'general'
    for i, (author, text) in enumerate([('u1', 'hi'), ('u2', 'yo'), ('u1', 'bye')]):
        messages.save_message(SimpleNamespace(message_id=f'm{i}', channel_id='c1', author_id=author,
                                              clean_content=text, created_at=float(i)))
    rows = messages.get_recent_messages('sapphire', 'c1')
    assert [r['content'] for r in rows] == ['hi', 'yo', 'bye'] and rows[0]['author_name'] == 'Alice'
    assert rows[1]['author_name'] == 'Unknown'
    assert messages.forget_author('u1') == {'messages': 2, 'users': 1}
    assert [r['content'] for r in messages.get_recent_messages('sapphire', 'c1')] == ['yo']
    assert messages.purge_before(10.0) == 1
    assert accounts.delete_account('sapphire') == 1 and accounts.list_accounts() == []
    service.stop()


def test_start_sets_wal_and_busy_timeout(tmp_path):
    conn = _service(tmp_path / 'engine.sqlite3').connection()
    assert conn.execute('PRAGMA journal_mode').fetchone()[0].lower() == 'wal'
    assert conn.execute('PRAGMA busy_timeout').fetchone()[0] == 5000
    assert conn.execute('PRAGMA synchronous').fetchone()[0] == 1   # NORMAL


def test_shared_connection_survives_a_thread_storm(tmp_path):
    service = _service(tmp_path / 'engine.sqlite3')
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
    service.checkpoint()
    service.stop()


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
