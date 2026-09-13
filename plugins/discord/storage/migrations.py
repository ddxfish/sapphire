"""SQLite schema migrations for the Discord cognitive plugin."""

from __future__ import annotations

MIGRATIONS: list[tuple[int, str]] = [
    (1, """
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

    CREATE TABLE IF NOT EXISTS guilds (
        guild_id TEXT PRIMARY KEY,
        name TEXT DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS channels (
        channel_id TEXT PRIMARY KEY,
        guild_id TEXT DEFAULT '',
        name TEXT DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS users (
        user_id TEXT PRIMARY KEY,
        username TEXT DEFAULT '',
        display_name TEXT DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS messages (
        message_id TEXT PRIMARY KEY,
        channel_id TEXT NOT NULL,
        author_id TEXT NOT NULL,
        content TEXT DEFAULT '',
        created_at REAL NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS observations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        observation_type TEXT NOT NULL,
        channel_id TEXT DEFAULT '',
        payload_json TEXT NOT NULL,
        created_at REAL NOT NULL
    );

    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_type TEXT NOT NULL,
        target_id TEXT DEFAULT '',
        reason TEXT DEFAULT '',
        created_at REAL NOT NULL
    );

    CREATE TABLE IF NOT EXISTS traces (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trace_type TEXT NOT NULL,
        summary TEXT NOT NULL,
        detail_json TEXT NOT NULL,
        created_at REAL NOT NULL
    );

    CREATE TABLE IF NOT EXISTS presence_state (
        account_name TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        activity TEXT DEFAULT '',
        updated_at REAL NOT NULL
    );

    CREATE TABLE IF NOT EXISTS settings_overrides (
        scope_type TEXT NOT NULL,
        scope_id TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        updated_at REAL NOT NULL,
        PRIMARY KEY (scope_type, scope_id)
    );

    CREATE TABLE IF NOT EXISTS plugin_metadata (
        metadata_key TEXT PRIMARY KEY,
        metadata_value TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS memories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scope_id TEXT DEFAULT '',
        content TEXT DEFAULT '',
        created_at REAL NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS profiles (
        user_id TEXT PRIMARY KEY,
        summary TEXT DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS media_artifacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message_id TEXT DEFAULT '',
        media_kind TEXT DEFAULT '',
        source_url TEXT DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS voice_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT DEFAULT '',
        channel_id TEXT DEFAULT '',
        account_name TEXT DEFAULT '',
        state TEXT DEFAULT ''
    );
    """),
    (2, """
    CREATE TABLE IF NOT EXISTS pinned_memories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_name TEXT NOT NULL,
        guild_id TEXT DEFAULT '',
        channel_id TEXT DEFAULT '',
        author_id TEXT DEFAULT '',
        username TEXT DEFAULT '',
        content TEXT NOT NULL,
        created_at REAL NOT NULL
    );

    CREATE TABLE IF NOT EXISTS user_profiles (
        account_name TEXT NOT NULL,
        user_id TEXT NOT NULL,
        summary TEXT DEFAULT '',
        fondness REAL DEFAULT 0.5,
        trust REAL DEFAULT 0.5,
        patience REAL DEFAULT 0.5,
        respect REAL DEFAULT 0.5,
        interest REAL DEFAULT 0.5,
        familiarity REAL DEFAULT 0.0,
        message_count INTEGER DEFAULT 0,
        updated_at REAL NOT NULL,
        PRIMARY KEY (account_name, user_id)
    );

    CREATE TABLE IF NOT EXISTS profile_facts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_name TEXT NOT NULL,
        user_id TEXT NOT NULL,
        content TEXT NOT NULL,
        confidence REAL DEFAULT 1.0,
        source TEXT DEFAULT 'explicit',
        created_at REAL NOT NULL
    );

    CREATE TABLE IF NOT EXISTS profile_buffers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_name TEXT NOT NULL,
        user_id TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at REAL NOT NULL,
        processed INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS agent_affect (
        account_name TEXT PRIMARY KEY,
        energy REAL DEFAULT 0.7,
        sociability REAL DEFAULT 0.6,
        irritability REAL DEFAULT 0.2,
        playfulness REAL DEFAULT 0.5,
        stress REAL DEFAULT 0.2,
        updated_at REAL NOT NULL
    );

    CREATE TABLE IF NOT EXISTS activation_scores (
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        account_name TEXT NOT NULL DEFAULT '',
        score REAL NOT NULL DEFAULT 0,
        updated_at REAL NOT NULL,
        PRIMARY KEY (entity_type, entity_id, account_name)
    );

    ALTER TABLE tasks ADD COLUMN account_name TEXT DEFAULT '';
    ALTER TABLE tasks ADD COLUMN status TEXT DEFAULT 'pending';
    ALTER TABLE tasks ADD COLUMN urgency REAL DEFAULT 0.5;
    ALTER TABLE tasks ADD COLUMN confidence REAL DEFAULT 0.5;
    ALTER TABLE tasks ADD COLUMN expires_at REAL DEFAULT 0;
    """),
    (3, """
    CREATE TABLE IF NOT EXISTS sleep_state (
        account_name TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        is_asleep INTEGER DEFAULT 0,
        goodnight_sent INTEGER DEFAULT 0,
        forced_wake_until REAL DEFAULT 0,
        mention_count INTEGER DEFAULT 0,
        updated_at REAL NOT NULL,
        PRIMARY KEY (account_name, channel_id)
    );

    CREATE TABLE IF NOT EXISTS sleep_buffer (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_name TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        message_id TEXT NOT NULL,
        author_id TEXT DEFAULT '',
        content TEXT DEFAULT '',
        mentioned INTEGER DEFAULT 0,
        created_at REAL NOT NULL,
        processed INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS proactive_cooldowns (
        account_name TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        action_type TEXT NOT NULL,
        last_sent_at REAL NOT NULL,
        PRIMARY KEY (account_name, channel_id, action_type)
    );

    ALTER TABLE media_artifacts ADD COLUMN account_name TEXT DEFAULT '';
    ALTER TABLE media_artifacts ADD COLUMN channel_id TEXT DEFAULT '';
    ALTER TABLE media_artifacts ADD COLUMN filename TEXT DEFAULT '';
    ALTER TABLE media_artifacts ADD COLUMN content_type TEXT DEFAULT '';
    ALTER TABLE media_artifacts ADD COLUMN raw_metadata_json TEXT DEFAULT '';
    ALTER TABLE media_artifacts ADD COLUMN interpreted_json TEXT DEFAULT '';
    ALTER TABLE media_artifacts ADD COLUMN created_at REAL DEFAULT 0;
    """),
    (4, """
    ALTER TABLE voice_sessions ADD COLUMN session_id TEXT DEFAULT '';
    ALTER TABLE voice_sessions ADD COLUMN mode TEXT DEFAULT 'listen_only';
    ALTER TABLE voice_sessions ADD COLUMN started_at REAL DEFAULT 0;
    ALTER TABLE voice_sessions ADD COLUMN ended_at REAL DEFAULT 0;
    ALTER TABLE voice_sessions ADD COLUMN participants_json TEXT DEFAULT '[]';
    ALTER TABLE voice_sessions ADD COLUMN health TEXT DEFAULT 'connecting';

    CREATE TABLE IF NOT EXISTS voice_transcripts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        account_name TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        speaker_id TEXT DEFAULT '',
        speaker_name TEXT DEFAULT '',
        text TEXT NOT NULL,
        confidence REAL DEFAULT 0.5,
        started_at REAL NOT NULL,
        ended_at REAL DEFAULT 0,
        created_at REAL NOT NULL
    );

    CREATE TABLE IF NOT EXISTS voice_summaries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        account_name TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        summary TEXT NOT NULL,
        created_at REAL NOT NULL
    );
    """),
    (5, """
    CREATE TABLE IF NOT EXISTS import_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,
        import_key TEXT NOT NULL,
        status TEXT NOT NULL,
        detail_json TEXT DEFAULT '{}',
        created_at REAL NOT NULL,
        UNIQUE(source, import_key)
    );
    """),
    (6, """
    ALTER TABLE tasks ADD COLUMN run_at REAL DEFAULT 0;
    ALTER TABLE tasks ADD COLUMN payload_json TEXT DEFAULT '';
    """),
    (7, """
    ALTER TABLE user_profiles ADD COLUMN birthday_month INTEGER DEFAULT 0;
    ALTER TABLE user_profiles ADD COLUMN birthday_day INTEGER DEFAULT 0;
    ALTER TABLE user_profiles ADD COLUMN birthday_channel_id TEXT DEFAULT '';
    ALTER TABLE user_profiles ADD COLUMN birthday_username TEXT DEFAULT '';
    ALTER TABLE user_profiles ADD COLUMN birthday_display_name TEXT DEFAULT '';
    ALTER TABLE user_profiles ADD COLUMN last_birthday_wish_year INTEGER DEFAULT 0;
    UPDATE tasks SET status = 'cancelled'
    WHERE task_type = 'birthday_follow_up' AND status = 'pending';
    """),
    (8, """
    ALTER TABLE user_profiles ADD COLUMN birthday_wish_run_at REAL DEFAULT 0;
    """),
    (9, """
    ALTER TABLE user_profiles ADD COLUMN username TEXT DEFAULT '';
    ALTER TABLE user_profiles ADD COLUMN display_name TEXT DEFAULT '';
    """),
    # Backfill names for profiles created before v9 from the users cache the
    # event adapter already maintains — no Discord lookups needed. Ongoing
    # freshness comes from record_interaction stamping names per message.
    (10, """
    UPDATE user_profiles SET
        username = COALESCE(
            (SELECT u.username FROM users u WHERE u.user_id = user_profiles.user_id), ''),
        display_name = COALESCE(
            (SELECT u.display_name FROM users u WHERE u.user_id = user_profiles.user_id), '')
    WHERE username = '' OR username IS NULL;
    """),
    # Relationship milestones, shared server lore, interest graphs, and
    # soft-forget/pin support on profile facts — all plugin-local SQLite.
    (11, """
    ALTER TABLE user_profiles ADD COLUMN first_seen_at REAL DEFAULT 0;
    ALTER TABLE user_profiles ADD COLUMN last_interaction_at REAL DEFAULT 0;

    ALTER TABLE profile_facts ADD COLUMN pinned INTEGER DEFAULT 0;
    ALTER TABLE profile_facts ADD COLUMN forgotten INTEGER DEFAULT 0;
    ALTER TABLE profile_facts ADD COLUMN updated_at REAL DEFAULT 0;

    CREATE TABLE IF NOT EXISTS relationship_milestones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_name TEXT NOT NULL,
        user_id TEXT NOT NULL,
        milestone_type TEXT NOT NULL,
        milestone_key TEXT NOT NULL,
        detail TEXT DEFAULT '',
        acknowledged INTEGER DEFAULT 0,
        created_at REAL NOT NULL,
        UNIQUE(account_name, user_id, milestone_key)
    );

    CREATE TABLE IF NOT EXISTS server_lore (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_name TEXT NOT NULL,
        guild_id TEXT NOT NULL DEFAULT '',
        channel_id TEXT NOT NULL DEFAULT '',
        content TEXT NOT NULL,
        source TEXT DEFAULT 'explicit',
        confidence REAL DEFAULT 1.0,
        pinned INTEGER DEFAULT 0,
        forgotten INTEGER DEFAULT 0,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );

    CREATE TABLE IF NOT EXISTS interest_topics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_name TEXT NOT NULL,
        user_id TEXT NOT NULL,
        topic TEXT NOT NULL,
        weight REAL DEFAULT 1.0,
        mention_count INTEGER DEFAULT 1,
        last_seen_at REAL NOT NULL,
        created_at REAL NOT NULL,
        UNIQUE(account_name, user_id, topic)
    );

    CREATE INDEX IF NOT EXISTS idx_server_lore_scope
        ON server_lore(account_name, guild_id, channel_id);
    CREATE INDEX IF NOT EXISTS idx_interest_topics_user
        ON interest_topics(account_name, user_id, weight DESC);
    CREATE INDEX IF NOT EXISTS idx_milestones_user
        ON relationship_milestones(account_name, user_id, created_at DESC);
    """),
    # 12 — indexes for the hot paths (hunt 2026-09-12, H5/C3). Every reply did
    # 4-6 full scans of `messages` on the daemon loop, the Traces tab sorted the
    # whole `traces` table twice per 15 s, and purge / forget / pending-buffer /
    # due-task / media-follow-up scans were unindexed. All IF NOT EXISTS, no
    # ALTERs — idempotent under the runner's self-heal.
    (12, """
    CREATE INDEX IF NOT EXISTS idx_messages_channel_created ON messages(channel_id, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_messages_author ON messages(author_id);
    CREATE INDEX IF NOT EXISTS idx_traces_created ON traces(created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_traces_type_created ON traces(trace_type, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_profile_buffers_pending ON profile_buffers(account_name, processed, user_id);
    CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(account_name, status, run_at);
    CREATE INDEX IF NOT EXISTS idx_media_artifacts_message ON media_artifacts(message_id);
    CREATE INDEX IF NOT EXISTS idx_media_artifacts_channel_created ON media_artifacts(channel_id, created_at);
    """),
]


def apply_migrations(conn) -> int:
    current = conn.execute('SELECT version FROM schema_version').fetchone()[0]
    applied = current
    for version, sql in MIGRATIONS:
        if version <= current:
            continue
        try:
            conn.executescript(sql)
        except Exception as exc:
            # Self-heal a half-applied ALTER script: executescript autocommits
            # per statement, so a crash between two ALTERs leaves column 1
            # present with the version un-bumped — the retry then dies on
            # "duplicate column name" forever (daemon bricked at boot).
            # Treat that specific error as already-applied and move on.
            if 'duplicate column name' not in str(exc).lower():
                raise
        conn.execute('UPDATE schema_version SET version = ?', (version,))
        applied = version
    conn.commit()
    return applied
