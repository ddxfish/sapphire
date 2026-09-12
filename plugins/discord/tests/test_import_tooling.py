import json
import sqlite3
import time

from plugins.discord.storage.repositories.memory import MemoryRepository
from plugins.discord.storage.repositories.profiles import ProfileRepository
from plugins.discord.storage.sqlite import SQLiteService
from plugins.discord.tools.import_from_leona import LeonaImportService


def _create_leona_db(path):
    conn = sqlite3.connect(path)
    conn.executescript('''
        CREATE TABLE pinned_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account TEXT NOT NULL,
            guild_id TEXT DEFAULT '',
            channel_id TEXT DEFAULT '',
            author_id TEXT DEFAULT '',
            username TEXT DEFAULT '',
            content TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE profile_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account TEXT NOT NULL,
            guild_id TEXT DEFAULT '',
            author_id TEXT NOT NULL,
            category TEXT NOT NULL,
            fact_key TEXT NOT NULL,
            fact_value TEXT NOT NULL,
            confidence REAL DEFAULT 0.7,
            source_message_ids TEXT DEFAULT '[]',
            first_seen_at REAL NOT NULL,
            last_confirmed_at REAL NOT NULL,
            expires_at REAL
        );
        CREATE TABLE user_profiles (
            account TEXT NOT NULL,
            guild_id TEXT DEFAULT '',
            author_id TEXT NOT NULL,
            summary_l1 TEXT DEFAULT '',
            warmth REAL DEFAULT 0.5,
            message_count INTEGER DEFAULT 0,
            last_seen_at REAL DEFAULT 0,
            PRIMARY KEY (account, guild_id, author_id)
        );
    ''')
    now = time.time()
    conn.execute(
        'INSERT INTO pinned_memories (account, guild_id, channel_id, author_id, username, content, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
        ('alpha', 'g1', 'c1', 'u1', 'alice', 'likes tea', now),
    )
    conn.execute(
        '''INSERT INTO profile_facts
        (account, guild_id, author_id, category, fact_key, fact_value, confidence, first_seen_at, last_confirmed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        ('alpha', '', 'u1', 'preference', 'drink', 'tea', 0.9, now, now),
    )
    conn.execute(
        'INSERT INTO user_profiles (account, guild_id, author_id, summary_l1, warmth, message_count, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
        ('alpha', '', 'u1', 'Alice is friendly', 0.7, 10, now),
    )
    conn.commit()
    conn.close()


def test_import_is_idempotent(tmp_path):
    leona_path = tmp_path / 'leona.sqlite3'
    target_path = tmp_path / 'cognitive.sqlite3'
    _create_leona_db(leona_path)
    target = SQLiteService(target_path)
    target.start()

    service = LeonaImportService(
        leona_db_path=leona_path,
        memory_repository=MemoryRepository(target),
        profile_repository=ProfileRepository(target),
        sqlite_service=target,
    )
    first = service.run(include=['pinned_memories', 'profile_facts', 'profile_summaries'])
    second = service.run(include=['pinned_memories', 'profile_facts', 'profile_summaries'])

    assert first['pinned_memories'] >= 1
    assert first['profile_facts'] >= 1
    assert second['pinned_memories'] == 0
    assert second['profile_facts'] == 0
    pinned = MemoryRepository(target).list_pinned('alpha', guild_id='g1')
    assert len(pinned) == 1
    facts = ProfileRepository(target).list_facts('alpha', 'u1')
    assert any('tea' in fact['content'] for fact in facts)
