"""Retention purge and forget-user over the v2 schema, plus the event bridge's pending TTL."""
import time
from types import SimpleNamespace

from plugins.discord.models.settings import SettingsStore
from plugins.discord.storage import retention
from plugins.discord.storage.repositories import MessageRepository
from plugins.discord.storage.sqlite import SQLiteService


def _seeded(tmp_path):
    service = SQLiteService(tmp_path / 'r.sqlite3')
    service.start()
    repo = MessageRepository(service)
    now = time.time()
    for mid, author, age_days in (('old', 'u1', 200), ('fresh', 'u1', 1), ('other', 'u2', 200)):
        repo.save_message(SimpleNamespace(message_id=mid, channel_id='c1', author_id=author, clean_content=mid,
                                          created_at=now - age_days * 86400))
    return service, repo


def test_purge_is_off_by_default_and_prunes_messages_when_on(tmp_path):
    service, repo = _seeded(tmp_path)
    assert retention.purge(service, SettingsStore().resolve()) == {'status': 'skipped', 'reason': 'retention_disabled'}
    out = retention.purge(service, SettingsStore({'retention': {'enabled': True, 'message_days': 90}}).resolve())
    assert out == {'status': 'purged', 'results': {'messages': 2}}
    assert [r['message_id'] for r in repo.get_recent_messages('bot', 'c1')] == ['fresh']
    assert retention.purge(service, SettingsStore({'retention': {'enabled': True, 'message_days': 0}}).resolve()) == \
        {'status': 'purged', 'results': {}}                              # 0 = keep forever
    service.stop()


def test_forget_user_removes_one_person_only(tmp_path):
    service, repo = _seeded(tmp_path)
    assert retention.forget_user(service, '')['status'] == 'error'
    out = retention.forget_user(service, 'u1')
    assert out['status'] == 'forgotten' and out['messages'] == 2
    assert [r['message_id'] for r in repo.get_recent_messages('bot', 'c1')] == ['other']
    service.stop()


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
