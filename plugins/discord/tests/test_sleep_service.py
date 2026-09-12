from datetime import datetime

from plugins.discord.models.intentions import GoodnightIntention, ReplyMessageIntention
from plugins.discord.models.settings import SettingsStore
from plugins.discord.proactive.sleep_service import SleepService
from plugins.discord.storage.repositories.proactive import ProactiveRepository
from plugins.discord.storage.sqlite import SQLiteService


def _service(tmp_path):
    sqlite = SQLiteService(tmp_path / 'sleep.sqlite3')
    sqlite.start()
    return SleepService(proactive_repository=ProactiveRepository(sqlite))


def _settings(**kwargs):
    store = SettingsStore()
    store.global_overlay.proactive.update(kwargs)
    return store.resolve()


def test_goodnight_marks_channel_asleep(tmp_path):
    service = _service(tmp_path)
    settings = _settings(
        sleep_schedule_enabled=True,
        sleep_utc_hour=22,
        greeting_utc_hour=9,
        greeting_targets=['alpha:c1'],
        goodnight_fallback='Night!',
    )
    now = datetime(2026, 6, 30, 22, 0)

    intentions = service.evaluate_goodnight('alpha', settings, now=now)

    assert len(intentions) == 1
    assert isinstance(intentions[0], GoodnightIntention)
    service.mark_goodnight_sent(intentions[0])
    assert service.is_asleep('alpha', 'c1')


def test_goodnight_catches_up_later_in_sleep_window(tmp_path):
    service = _service(tmp_path)
    settings = _settings(
        sleep_schedule_enabled=True,
        sleep_utc_hour=22,
        greeting_utc_hour=9,
        greeting_targets=['alpha:c1'],
    )
    now = datetime(2026, 7, 1, 0, 30)

    intentions = service.evaluate_goodnight('alpha', settings, now=now)

    assert len(intentions) == 1
    assert intentions[0].channel_id == 'c1'


def test_goodnight_skips_after_sent(tmp_path):
    service = _service(tmp_path)
    settings = _settings(
        sleep_schedule_enabled=True,
        sleep_utc_hour=22,
        greeting_utc_hour=9,
        greeting_targets=['alpha:c1'],
    )
    service.proactive_repository.set_sleep_state('alpha', 'c1', goodnight_sent=1)
    now = datetime(2026, 7, 1, 0, 30)

    assert service.evaluate_goodnight('alpha', settings, now=now) == []


def test_buffers_mentions_while_asleep(tmp_path):
    service = _service(tmp_path)
    service.set_asleep('alpha', 'c1')

    service.buffer_mention('alpha', 'c1', message_id='m1', author_id='u1', content='wake up', mentioned=True)

    buffered = service.list_buffered('alpha', 'c1')
    assert len(buffered) == 1
    assert buffered[0]['content'] == 'wake up'


def test_wake_replays_buffered_mentions(tmp_path):
    service = _service(tmp_path)
    service.set_asleep('alpha', 'c1')
    service.buffer_mention('alpha', 'c1', message_id='m1', author_id='u1', content='hey', mentioned=True)

    intentions = service.drain_wake_buffer('alpha', 'c1', max_replies=3)

    assert len(intentions) == 1
    assert isinstance(intentions[0], ReplyMessageIntention)
    assert service.list_buffered('alpha', 'c1') == []


def test_account_sleep_state_tracks_greeting_targets(tmp_path):
    service = _service(tmp_path)
    settings = _settings(greeting_targets=['alpha:c1', 'alpha:c2'])
    service.set_asleep('alpha', 'c1')

    asleep, forced_wake = service.account_sleep_state('alpha', settings)

    assert asleep is True
    assert forced_wake is False


def test_account_sleep_state_uses_schedule_hours(tmp_path):
    service = _service(tmp_path)
    settings = _settings(
        sleep_schedule_enabled=True,
        sleep_utc_hour=22,
        greeting_utc_hour=9,
        greeting_targets=['alpha:c1'],
    )
    from datetime import datetime
    from plugins.discord.lib.server_time import now_local
    import plugins.discord.proactive.sleep_service as sleep_module

    night = datetime(2026, 6, 30, 23, 0)
    original = sleep_module.now_local
    sleep_module.now_local = lambda: night
    try:
        assert service.in_sleep_hours(settings) is True
        asleep, forced_wake = service.account_sleep_state('alpha', settings)
        assert asleep is True
        assert forced_wake is False
    finally:
        sleep_module.now_local = original


def test_evaluate_reply_gate_blocks_first_mention(tmp_path):
    service = _service(tmp_path)
    settings = _settings(
        sleep_schedule_enabled=True,
        sleep_utc_hour=22,
        greeting_utc_hour=9,
        forced_wake_mention_threshold=2,
        greeting_targets=['alpha:c1'],
    )
    service.set_asleep('alpha', 'c1')
    obs = type('Obs', (), {
        'account_name': 'alpha',
        'channel_id': 'c1',
        'message_id': 'm1',
        'author_id': 'u1',
        'clean_content': 'wake up',
    })()

    first = service.evaluate_reply_gate(
        obs, settings, respond_trigger=True, mentioned=True, now_ts=1_700_000_000.0,
    )
    second = service.evaluate_reply_gate(
        obs, settings, respond_trigger=True, mentioned=True, now_ts=1_700_000_100.0,
    )

    assert first == {'allow': False, 'reason': 'sleep_buffered_mention'}
    assert second['allow'] is True
    assert second['reason'] == 'forced_wake_triggered'
    assert service.is_forced_awake('alpha', 'c1', now_ts=1_700_000_100.0)


def test_evaluate_reply_gate_ignores_name_match_while_asleep(tmp_path):
    service = _service(tmp_path)
    settings = _settings(
        sleep_schedule_enabled=True,
        greeting_targets=['alpha:c1'],
    )
    service.set_asleep('alpha', 'c1')
    obs = type('Obs', (), {
        'account_name': 'alpha',
        'channel_id': 'c1',
        'message_id': 'm1',
        'author_id': 'u1',
        'clean_content': 'hey bot',
    })()

    decision = service.evaluate_reply_gate(
        obs, settings, respond_trigger=True, mentioned=False, now_ts=1_700_000_000.0,
    )

    assert decision == {'allow': False, 'reason': 'sleep_mentions_only'}
