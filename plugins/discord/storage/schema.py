"""Schema v2 (S7, 2026-09-22): the tables the host actually uses.

accounts  — bot tokens (the one table carried over from v1)
guilds / channels / users — name caches for the pickers, the mention map and
                            the transcript's author labels; refilled by traffic
messages  — recent channel history for the reply prompt (retention purges it)

Everything else v1 accumulated (profiles, memories, tasks, traces, voice
transcripts, overlays, …) belongs to features that left the host in S1–S6 or
was dropped with the traces flight recorder in S7.
"""

SCHEMA_VERSION = 2

DDL = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS accounts (
    name TEXT PRIMARY KEY,
    token TEXT NOT NULL,
    bot_name TEXT DEFAULT '',
    bot_id TEXT DEFAULT '',
    state TEXT DEFAULT 'disconnected',
    last_error TEXT DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS guilds (guild_id TEXT PRIMARY KEY, name TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS channels (channel_id TEXT PRIMARY KEY, guild_id TEXT DEFAULT '', name TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS users (user_id TEXT PRIMARY KEY, username TEXT DEFAULT '', display_name TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    author_id TEXT NOT NULL,
    content TEXT DEFAULT '',
    created_at REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_messages_channel_created ON messages(channel_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_author ON messages(author_id);
"""
