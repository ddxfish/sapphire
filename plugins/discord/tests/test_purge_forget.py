"""D2 (hunt 2026-09-12): chunked purge coverage, one forget door, account cascade,
check-in dedupe, payload TTL, bounded reaction memory, typed overlays, scratch test
users, the opt-in debug ring."""

import json
import time
from types import SimpleNamespace

from plugins.discord.runtime import retention_service as rs
from plugins.discord.runtime.forget_service import ForgetService
from plugins.discord.runtime.retention_service import RetentionService
from plugins.discord.storage.repositories.accounts import AccountRepository
from plugins.discord.storage.sqlite import SQLiteService


def _service(tmp_path):
    service = SQLiteService(tmp_path / 'd2.sqlite3')
    service.start()
    return service


def _seed(conn, table, **values):
    """Insert one row: every NOT NULL column without a default gets a typed blank."""
    cols = conn.execute(f'PRAGMA table_info({table})').fetchall()
    row = {}
    for c in cols:
        name, ctype, notnull, default, pk = c[1], (c[2] or '').upper(), c[3], c[4], c[5]
        if pk or name in values or default is not None or not notnull:
            continue
        row[name] = 0 if ('INT' in ctype or 'REAL' in ctype) else ''
    row.update(values)
    keys = list(row)
    conn.execute(f'INSERT INTO {table} ({", ".join(keys)}) VALUES ({", ".join("?" for _ in keys)})',
                 [row[k] for k in keys])
    conn.commit()


OLD = time.time() - 400 * 86400
FRESH = time.time()
RET = SimpleNamespace(retention=SimpleNamespace(enabled=True, message_days=90, trace_days=14, transcript_days=30,
                                                profile_buffer_days=7))


def _count(conn, table):
    return conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]


def test_purge_covers_every_accumulating_table_and_keeps_fresh_rows(tmp_path):
    service = _service(tmp_path)
    conn = service.connection()
    for stamp in (OLD, FRESH):
        _seed(conn, 'messages', message_id=f'm{stamp}', channel_id='c', author_id='u', created_at=stamp)
        _seed(conn, 'media_artifacts', message_id=f'm{stamp}', created_at=stamp)
        _seed(conn, 'sleep_buffer', account_name='bot', channel_id='c', message_id='x', created_at=stamp, processed=1)
        _seed(conn, 'traces', trace_type='t', summary='s', detail_json='{}', created_at=stamp)
        _seed(conn, 'tasks', account_name='bot', task_type='reminder', status='completed', created_at=stamp)
        _seed(conn, 'tasks', account_name='bot', task_type='reminder', status='pending', created_at=stamp)
        _seed(conn, 'voice_transcripts', session_id='s', account_name='bot', channel_id='c', text='t', created_at=stamp)
        _seed(conn, 'voice_summaries', session_id='s', account_name='bot', channel_id='c', summary='s', created_at=stamp)
        _seed(conn, 'voice_sessions', account_name='bot', channel_id='c', state='closed', ended_at=stamp)
        _seed(conn, 'profile_buffers', account_name='bot', user_id='u', content='c', created_at=stamp)
    _seed(conn, 'sleep_buffer', account_name='bot', channel_id='c', message_id='y', created_at=OLD, processed=0)

    result = RetentionService(sqlite_service=service).purge(RET)

    assert result['status'] == 'purged'
    r = result['results']
    assert r['messages'] == 1 and r['media_artifacts'] == 1 and r['sleep_buffer'] == 1
    assert r['traces'] == 1 and r['tasks'] == 1                # the pending one survives, old or not
    assert r['voice_transcripts'] == 1 and r['voice_summaries'] == 1 and r['voice_sessions'] == 1
    assert r['profile_buffers'] == 1
    assert _count(conn, 'messages') == 1 and _count(conn, 'tasks') == 3
    assert _count(conn, 'sleep_buffer') == 2                    # fresh processed + old unprocessed stay


def test_purge_deletes_in_chunks_and_yields_between_them(tmp_path, monkeypatch):
    service = _service(tmp_path)
    conn = service.connection()
    monkeypatch.setattr(rs, 'PURGE_CHUNK', 7)
    pauses = []
    monkeypatch.setattr(rs.time, 'sleep', lambda s: pauses.append(s))
    for i in range(20):
        _seed(conn, 'traces', trace_type='t', summary='s', detail_json='{}', created_at=OLD)
    assert rs._purge_where(conn, 'traces', 'created_at < ?', (FRESH,)) == 20
    assert len(pauses) == 2                                     # 7 + 7 + 6: two full chunks yielded
    assert _count(conn, 'traces') == 0


def test_forget_reaches_every_table_and_only_that_person(tmp_path):
    service = _service(tmp_path)
    conn = service.connection()
    for uid in ('u1', 'u2'):
        _seed(conn, 'user_profiles', account_name='bot', user_id=uid)
        _seed(conn, 'profile_facts', account_name='bot', user_id=uid, content='f', created_at=FRESH)
        _seed(conn, 'profile_buffers', account_name='bot', user_id=uid, content='b', created_at=FRESH)
        _seed(conn, 'interest_topics', account_name='bot', user_id=uid, topic='t' + uid, last_seen_at=FRESH, created_at=FRESH)
        _seed(conn, 'relationship_milestones', account_name='bot', user_id=uid, created_at=FRESH)
        _seed(conn, 'pinned_memories', account_name='bot', author_id=uid, content='p', created_at=FRESH)
        _seed(conn, 'voice_transcripts', session_id='s', account_name='bot', channel_id='c', speaker_id=uid, text='t', created_at=FRESH)
        _seed(conn, 'sleep_buffer', account_name='bot', channel_id='c', message_id='m' + uid, author_id=uid, created_at=FRESH)
        _seed(conn, 'messages', message_id='m' + uid, channel_id='c', author_id=uid, created_at=FRESH)
        _seed(conn, 'media_artifacts', message_id='m' + uid, created_at=FRESH)
        _seed(conn, 'tasks', account_name='bot', task_type='social_check_in', status='pending', created_at=FRESH,
              payload_json=json.dumps({'author_id': uid}))

    result = ForgetService(sqlite_service=service).forget('bot', 'u1')

    assert result['status'] == 'forgotten'
    for table, col in (('user_profiles', 'user_id'), ('profile_facts', 'user_id'), ('profile_buffers', 'user_id'),
                       ('interest_topics', 'user_id'), ('relationship_milestones', 'user_id'),
                       ('pinned_memories', 'author_id'), ('voice_transcripts', 'speaker_id'),
                       ('sleep_buffer', 'author_id'), ('messages', 'author_id')):
        assert conn.execute(f'SELECT COUNT(*) FROM {table} WHERE {col} = ?', ('u1',)).fetchone()[0] == 0, table
        assert conn.execute(f'SELECT COUNT(*) FROM {table} WHERE {col} = ?', ('u2',)).fetchone()[0] == 1, table
    assert _count(conn, 'media_artifacts') == 1
    assert conn.execute("SELECT COUNT(*) FROM tasks WHERE payload_json LIKE '%u1%'").fetchone()[0] == 0
    assert result['tasks'] == 1 and result['media_artifacts'] == 1


def test_retention_forget_door_uses_the_one_service(tmp_path):
    service = _service(tmp_path)
    conn = service.connection()
    _seed(conn, 'voice_transcripts', session_id='s', account_name='bot', channel_id='c', speaker_id='u1', text='t', created_at=FRESH)
    RetentionService(sqlite_service=service).forget_user('bot', 'u1', profile_repository=object())   # old kwargs tolerated
    assert _count(conn, 'voice_transcripts') == 0


def test_account_delete_cascades(tmp_path):
    service = _service(tmp_path)
    conn = service.connection()
    repo = AccountRepository(service)
    repo.upsert_account('a1', token='t', bot_name='A', bot_id='1')
    repo.upsert_account('a2', token='t', bot_name='B', bot_id='2')
    for acct in ('a1', 'a2'):
        _seed(conn, 'user_profiles', account_name=acct, user_id='u')
        _seed(conn, 'tasks', account_name=acct, task_type='reminder', created_at=FRESH)
        _seed(conn, 'voice_sessions', account_name=acct, channel_id='c', state='closed')
        _seed(conn, 'presence_state', account_name=acct)

    removed = repo.delete_account('a1')

    assert removed['user_profiles'] == 1 and removed['tasks'] == 1
    assert repo.get_account('a1') is None and repo.get_account('a2') is not None
    for table in ('user_profiles', 'tasks', 'voice_sessions', 'presence_state'):
        assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE account_name = 'a1'").fetchone()[0] == 0
        assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE account_name = 'a2'").fetchone()[0] == 1


def test_pending_payloads_expire_and_clear_returns_the_payload(monkeypatch):
    from plugins.discord.sapphire import event_bridge as eb

    class Loader:
        def emit_daemon_event(self, name, payload):
            return True

    bridge = eb.SapphireEventBridge(Loader())
    now = [1000.0]
    monkeypatch.setattr(eb.time, 'time', lambda: now[0])
    bridge.emit_discord_message({'message_id': 'old', 'content': 'x'})
    now[0] += eb.PENDING_PAYLOAD_TTL_SECONDS + 1
    bridge.emit_discord_message({'message_id': 'new', 'content': 'y'})
    assert 'old' not in bridge._pending_payloads and 'new' in bridge._pending_payloads
    assert bridge.clear_pending_payload('new') == {'message_id': 'new', 'content': 'y'}
    assert bridge.clear_pending_payload('new') is None


def test_reacted_messages_memory_is_bounded():
    from plugins.discord.conversation import reaction_service as rsvc
    from plugins.discord.models.intentions import AddReactionIntention

    service = rsvc.ReactionService()
    for i in range(rsvc.REACTED_MESSAGES_CAP + 50):
        intention = AddReactionIntention(intention_type='add_reaction', account_name='bot', channel_id='c',
                                         message_id=str(i), reason='r', emoji='x')
        service._record_silent_reaction(intention, result={}, delay=0.0)
    assert len(service._reacted_messages) == rsvc.REACTED_MESSAGES_CAP
    assert ('bot', 'c', '0') not in service._reacted_messages
    assert ('bot', 'c', str(rsvc.REACTED_MESSAGES_CAP + 49)) in service._reacted_messages


def test_overlay_values_are_coerced_to_field_types():
    from plugins.discord.models.settings import SettingsStore

    store = SettingsStore.from_dict({'global': {
        'safety': {'rate_limit_seconds': '45', 'allow_direct_messages': 'false'},
        'retention': {'trace_days': '3'},
        'voice': {'addressing_aliases': 'sapph, saphire', 'follow_up_seconds': 'nope'},
        'channel': {'ignored_channels': '["a", "b"]'},
    }})
    s = store.resolve()
    assert s.safety.rate_limit_seconds == 45 and s.safety.allow_direct_messages is False
    assert s.retention.trace_days == 3
    assert s.voice.addressing_aliases == ['sapph', 'saphire']
    assert s.voice.follow_up_seconds == 20.0                    # unparseable → default stands
    assert s.channel.ignored_channels == ['a', 'b']


def test_debug_ring_is_opt_in_and_clearable():
    from plugins.discord.observability.llm_debug_service import LlmDebugService

    class Loader:
        def __init__(self, on):
            self.on = on

        def get_plugin_settings(self, name):
            return {'cognitive.llm_debug_enabled': self.on}

    off = LlmDebugService(limit=5, plugin_loader=Loader(False))
    off.record_prompt({'message_id': 'm1', 'account': 'bot', 'content': 'hello'})
    assert off.list_entries() == []
    on = LlmDebugService(limit=5, plugin_loader=Loader('true'))
    on.record_prompt({'message_id': 'm1', 'account': 'bot', 'content': 'hello'})
    assert len(on.list_entries()) == 1
    assert on.clear() == 1 and on.list_entries() == []
    assert LlmDebugService(limit=5).enabled is True            # no loader = unit test / standalone
