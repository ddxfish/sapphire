from datetime import datetime

from plugins.discord.models.intentions import GreetChannelIntention
from plugins.discord.models.settings import ProactiveSettings, SettingsStore
from plugins.discord.proactive.greeting_service import GreetingService
from plugins.discord.proactive.sleep_service import SleepService
from plugins.discord.storage.repositories.proactive import ProactiveRepository
from plugins.discord.storage.sqlite import SQLiteService


def _service(tmp_path):
    sqlite = SQLiteService(tmp_path / 'greeting.sqlite3')
    sqlite.start()
    proactive_repository = ProactiveRepository(sqlite)
    sleep_service = SleepService(proactive_repository=proactive_repository)
    return GreetingService(
        proactive_repository=proactive_repository,
        sleep_service=sleep_service,
    )


def _settings(**kwargs):
    store = SettingsStore()
    store.global_overlay.proactive.update(kwargs)
    return store.resolve()


def test_greeting_generates_intention_at_target_hour(tmp_path):
    service = _service(tmp_path)
    settings = _settings(
        greeting_enabled=True,
        greeting_utc_hour=9,
        greeting_targets=['alpha:c1'],
        greeting_fallback='Good morning!',
    )
    now = datetime(2026, 6, 30, 9, 0)

    intentions = service.evaluate('alpha', settings, now=now)

    assert len(intentions) == 1
    assert isinstance(intentions[0], GreetChannelIntention)
    assert intentions[0].channel_id == 'c1'


def test_greeting_skips_when_disabled(tmp_path):
    service = _service(tmp_path)
    settings = _settings(greeting_enabled=False, greeting_utc_hour=9, greeting_targets=['alpha:c1'])
    now = datetime(2026, 6, 30, 9, 0)

    assert service.evaluate('alpha', settings, now=now) == []


def test_greeting_wakes_asleep_channel(tmp_path):
    service = _service(tmp_path)
    service.proactive_repository.set_sleep_state('alpha', 'c1', is_asleep=1, goodnight_sent=1)
    settings = _settings(
        greeting_enabled=True,
        greeting_utc_hour=9,
        greeting_targets=['alpha:c1'],
    )
    now = datetime(2026, 6, 30, 9, 0)

    intentions = service.evaluate('alpha', settings, now=now)

    assert len(intentions) == 1
    state = service.proactive_repository.get_sleep_state('alpha', 'c1')
    assert state.get('is_asleep') == 0
    assert state.get('goodnight_sent') == 0


def test_greeting_skips_wrong_hour(tmp_path):
    service = _service(tmp_path)
    settings = _settings(greeting_enabled=True, greeting_utc_hour=9, greeting_targets=['alpha:c1'])
    now = datetime(2026, 6, 30, 10, 0)

    assert service.evaluate('alpha', settings, now=now) == []
