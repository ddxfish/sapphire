from datetime import datetime

from plugins.discord.models.intentions import OutreachIntention
from plugins.discord.models.settings import ProactiveSettings, SettingsStore
from plugins.discord.proactive.outreach_service import OutreachService
from plugins.discord.storage.repositories.proactive import ProactiveRepository
from plugins.discord.storage.sqlite import SQLiteService


def _service(tmp_path, last_activity=None):
    sqlite = SQLiteService(tmp_path / 'outreach.sqlite3')
    sqlite.start()
    return OutreachService(
        proactive_repository=ProactiveRepository(sqlite),
        channel_last_activity=last_activity or {},
    )


def _settings(**kwargs):
    store = SettingsStore()
    store.global_overlay.proactive.update(kwargs)
    return store.resolve()


def test_outreach_targets_quiet_channel(tmp_path):
    service = _service(tmp_path, last_activity={'alpha:c1': 0.0})
    settings = _settings(
        outreach_enabled=True,
        outreach_stale_minutes=60,
        outreach_cooldown_hours=1,
        greeting_targets=['alpha:c1'],
        greeting_utc_hour=9,
    )
    now = datetime(2026, 6, 30, 14, 0)

    intentions = service.evaluate('alpha', settings, now=now, now_ts=now.timestamp())

    assert len(intentions) == 1
    assert isinstance(intentions[0], OutreachIntention)


def test_outreach_skips_during_greeting_window(tmp_path):
    service = _service(tmp_path, last_activity={'alpha:c1': 0.0})
    settings = _settings(
        outreach_enabled=True,
        outreach_stale_minutes=60,
        greeting_enabled=True,
        greeting_targets=['alpha:c1'],
        greeting_utc_hour=9,
        greeting_outreach_lead_hours=2,
    )
    now = datetime(2026, 6, 30, 8, 0)

    assert service.evaluate('alpha', settings, now=now, now_ts=now.timestamp()) == []
